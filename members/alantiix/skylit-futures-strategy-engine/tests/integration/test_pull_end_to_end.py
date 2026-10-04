"""Integration test: the pull end to end, with ProjectX bars after an Atlas ``no_data`` probe.

Every endpoint is a respx mock: Skylit ``/v1/account``, ``/v1/symbols``,
``/v1/historical/range``, ``/v1/historical`` and ``/v1/dark-pool/trades``;
Atlas ``/v1/config``, ``/v1/search`` and ``/v1/history``; and the ProjectX
login and ``retrieveBars``. Atlas answers ``no_data`` for every futures
contract, so the probe finds ES not served and every session's ES bars come
from ProjectX, while VIX bars still come from Atlas. Keys and ProjectX
credentials are the fake values of ``tests/conftest.py``. The Data_Cache,
calendar files and outputs live under ``tmp_path``, outside the repository.

- :func:`fse.data.puller.run_pull` in virtual time (``FakeClock``). The pull
  starts 365 days after its first session and 364 days after its second, so
  the first is sampled with ``/v1/historical`` and the second fetched with
  ``/v1/historical/range``.
- ``fse pull`` through :func:`fse.cli.run`: the real command wiring (its own
  httpx client, Fetch_Log, calendar files and ProjectX set up from the
  environment). It runs in real time; the mocked pull sends about 20
  requests, far under every pacing limit, so nothing waits.

The design's flow continues to an offline backtest (Req 1.8, 3.12); that
part belongs with the Backtester (task 19).

**Validates: Requirements 3.1, 3.3, 3.11, 3.14, 4.2, 4.3, 4.6**
"""

from __future__ import annotations

import io
import json
import random
import shutil
from dataclasses import dataclass, field
from datetime import date, time
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from fse import cli
from fse.calendars import (
    ECONOMIC_EVENTS_FILE,
    EXCHANGE_CALENDAR_FILE,
    ROLL_CALENDAR_FILE,
    ContractPeriod,
    Covers,
    RollCalendar,
)
from fse.data.bar_source import session_bar_range
from fse.data.cache import CacheWindowKey, DataCache, HeatmapView
from fse.data.planner import PullSpec
from fse.data.puller import PullContext, PullOptions, run_pull
from fse.engine.types import Metric
from fse.logio import LogWriter, Redactor
from fse.projectx.bars import RETRIEVE_BARS_PATH, ProjectXBars
from fse.projectx.models import format_instant
from fse.projectx.session import DEFAULT_BASE_URL, LOGIN_PATH, ProjectXSession
from fse.secrets.env import EnvView
from fse.skylit import endpoints as ep
from fse.skylit.client import ClientConfig, SkylitClient
from fse.skylit.endpoints import Endpoint, format_rfc3339
from fse.skylit.fetch_log import FETCH_LOG_FILE_NAME, FetchLog
from fse.timekit import (
    NS_PER_MINUTE,
    NS_PER_SECOND,
    Instant,
    SessionCalendar,
    SessionTimes,
    ny_instant,
    parse_rfc3339,
)
from tests.conftest import FakeEnv
from tests.fakes.clock import FakeClock

pytestmark = pytest.mark.integration

S = NS_PER_SECOND
M = NS_PER_MINUTE
PX_TOKEN = "fake-projectx-session-token-0000"
PX_HOST = httpx.URL(DEFAULT_BASE_URL).host
PROJECT_CALENDARS = Path(__file__).resolve().parents[2] / "calendars"

SYMBOLS = ("SPX", "SPY")
METRICS: tuple[Metric, ...] = ("gamma", "vanna")
TIMES = SessionTimes(pull_start=time(9, 30), pull_end=time(10, 0))  # two Cache_Windows
VIEW_ID = HeatmapView().view_id()
BARS_PER_SESSION = 22 * 60 + 11  # 18:00 the evening before to 16:11
REPLAYS = (ep.HISTORICAL_RANGE.path, ep.HISTORICAL.path)


def ny(d: date, hh: int, mm: int) -> Instant:
    return ny_instant(d, time(hh, mm))


def window_starts(d: date) -> tuple[Instant, Instant]:
    """The two Cache_Window starts of the shortened Pull_Window."""
    return ny(d, 9, 30), ny(d, 9, 45)


# ---------------------------------------------------------------- fakes


def _meta(metric: str) -> dict[str, Any]:
    return {"metric": metric, "resolution": "1s", "mode": "historical", "cached": False}


def _range_data(params: dict[str, str]) -> dict[str, Any]:
    """Frames at ``from``, ``from + 1 s`` and ``to`` for each symbol."""
    lo, hi = parse_rfc3339(params["from"]), parse_rfc3339(params["to"])
    axis = {"id": 0, "strikes": [100.0, 105.0], "expirations": ["2026-12-18"]}
    frames = [
        {
            "asOf": format_rfc3339(t),
            "axis": 0,
            "spot": 101.5,
            "previousClose": 100.0,
            "values": [1.5, -2.25],
        }
        for t in (lo, lo + S, hi)
    ]
    return {
        "from": params["from"],
        "to": params["to"],
        "symbols": [
            {"symbol": s, "axes": [axis], "frames": frames} for s in params["symbols"].split(",")
        ],
    }


def _historical_data(params: dict[str, str]) -> dict[str, Any]:
    """One board per symbol, 1 s before ``at``."""
    as_of = format_rfc3339(parse_rfc3339(params["at"]) - S)
    board = {
        "asOf": as_of,
        "spot": 101.5,
        "previousClose": 100.0,
        "expirations": ["2026-12-18"],
        "strikes": [
            {"strike": 100.0, "value": 1.5, "nodeType": "king"},
            {"strike": 105.0, "value": -2.25, "nodeType": "normal"},
        ],
    }
    return {"symbols": [{"symbol": s, **board} for s in params["symbols"].split(",")]}


@dataclass
class FakeSkylit:
    """Heatseeker replays and dark-pool prints. Saves stdout as it was at the first replay."""

    stdout: io.StringIO
    replays: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    output_at_first_replay: str | None = None

    def replay(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if self.output_at_first_replay is None:
            self.output_at_first_replay = self.stdout.getvalue()
        self.replays.append((request.url.path, params))
        if request.url.path == ep.HISTORICAL_RANGE.path:
            data = _range_data(params)
        else:
            data = _historical_data(params)
        return httpx.Response(200, json={"data": data, "meta": _meta(params["metric"])})

    def trades(self, request: httpx.Request) -> httpx.Response:
        """One SPY print on the span's last date; every other date has none."""
        last = dict(request.url.params)["date_end"]
        prints = [
            {
                "timestamp": f"{last}T15:00:00Z",
                "ticker": "SPY",
                "price": 560.25,
                "size": 2000,
                "notional": 1_120_500.0,
                "venue": "TRF",
            }
        ]
        meta = {"limit": 5000, "offset": 0, "count": 1, "hasMore": False}
        return httpx.Response(200, json={"data": prints, "meta": meta})

    def paths(self, path: str) -> list[dict[str, str]]:
        return [params for p, params in self.replays if p == path]


@dataclass
class FakeAtlas:
    """Atlas: futures history is ``no_data``; VIX has one bar per minute of ``[from, to)``."""

    history_calls: list[dict[str, str]] = field(default_factory=list)
    searches: list[str] = field(default_factory=list)

    def search(self, request: httpx.Request) -> httpx.Response:
        query = dict(request.url.params)["query"]
        self.searches.append(query)
        return httpx.Response(200, json=[{"symbol": f"CME:{query}", "ticker": query}])

    def history(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.history_calls.append(params)
        if params["symbol"] != "VIX":
            return httpx.Response(200, json={"s": "no_data"})
        t = list(range(int(params["from"]), int(params["to"]), 60))
        p = [20.0 + (s // 60 % 40) * 0.05 for s in t]
        body = {"s": "ok", "t": t, "o": p, "h": p, "l": p, "c": p, "v": [0] * len(t)}
        return httpx.Response(200, json=body)

    def futures_calls(self) -> list[dict[str, str]]:
        return [c for c in self.history_calls if c["symbol"] != "VIX"]


@dataclass
class FakeProjectX:
    """ProjectX login and ``retrieveBars``: one tick-grid bar per minute, newest first."""

    logins: list[dict[str, Any]] = field(default_factory=list)
    bodies: list[dict[str, Any]] = field(default_factory=list)

    def login(self, request: httpx.Request) -> httpx.Response:
        self.logins.append(json.loads(request.content))
        return httpx.Response(200, json={"token": PX_TOKEN, "success": True, "errorCode": 0})

    def bars(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {PX_TOKEN}"
        body: dict[str, Any] = json.loads(request.content)
        self.bodies.append(body)
        lo, hi = parse_rfc3339(str(body["startTime"])), parse_rfc3339(str(body["endTime"]))
        bars = []
        for t in range(lo, hi, M):
            p = 6000.0 + (t // M % 97) * 0.25
            stamp = format_instant(t).replace("Z", "+00:00")
            bars.append({"t": stamp, "o": p, "h": p + 0.5, "l": p - 0.5, "c": p + 0.25, "v": 3})
        return httpx.Response(200, json={"bars": bars[::-1], "success": True, "errorCode": 0})


@dataclass
class Fakes:
    skylit: FakeSkylit
    atlas: FakeAtlas
    projectx: FakeProjectX


def _get(router: respx.MockRouter, endpoint: Endpoint) -> respx.Route:
    return router.route(method="GET", scheme="https", host=endpoint.host.value, path=endpoint.path)


def mount(router: respx.MockRouter, stdout: io.StringIO) -> Fakes:
    """Route every endpoint the pull uses to the fakes."""
    fakes = Fakes(FakeSkylit(stdout), FakeAtlas(), FakeProjectX())
    account = {"data": {"limits": {"requestsPerMinute": 600, "historicalInFlight": 2}}}
    listed = [
        {
            "symbol": s,
            "isIndex": s == "SPX",
            "metrics": ["gamma", "vanna"],
            "history": {"from": "2023-03-28", "to": "2026-10-02"},
        }
        for s in SYMBOLS
    ]
    _get(router, ep.ACCOUNT).mock(return_value=httpx.Response(200, json=account))
    _get(router, ep.SYMBOLS).mock(
        return_value=httpx.Response(200, json={"data": {"symbols": listed}})
    )
    _get(router, ep.HISTORICAL_RANGE).mock(side_effect=fakes.skylit.replay)
    _get(router, ep.HISTORICAL).mock(side_effect=fakes.skylit.replay)
    _get(router, ep.DARK_POOL_TRADES).mock(side_effect=fakes.skylit.trades)
    _get(router, ep.ATLAS_CONFIG).mock(
        return_value=httpx.Response(200, json={"max_fetch_trading_days": {"1": 90}})
    )
    _get(router, ep.ATLAS_SEARCH).mock(side_effect=fakes.atlas.search)
    _get(router, ep.ATLAS_HISTORY).mock(side_effect=fakes.atlas.history)
    router.post(DEFAULT_BASE_URL + LOGIN_PATH).mock(side_effect=fakes.projectx.login)
    router.post(DEFAULT_BASE_URL + RETRIEVE_BARS_PATH).mock(side_effect=fakes.projectx.bars)
    return fakes


# ---------------------------------------------------------------- shared checks


def check_report(report: dict[str, Any], sessions: tuple[date, date], contract: str) -> None:
    """The coverage report of a completed two-session pull with ProjectX bars."""
    days = [d.isoformat() for d in sessions]
    assert (report["outcome"], report["error"]) == ("completed", None)
    assert report["sessions_requested"] == 2
    assert {(h["symbol"], h["metric"]) for h in report["heatmaps"]} == {
        (s, m) for s in SYMBOLS for m in METRICS
    }
    for h in report["heatmaps"]:
        assert (h["sessions_requested"], h["sessions_stored"]) == (2, 2)
        assert (h["no_data_gaps"], h["incomplete_windows"]) == (0, 0)
        assert [s["resolution"] for s in h["sessions"]] == [["1s"], ["1s"]]
    # Req 4.2-4.3, 4.6: the probe result, then the contract and source per session.
    (es,) = report["futures"]
    assert es["instrument"] == "ES"
    assert es["atlas_probe"] == {"served": False, "error_code": None}
    assert [
        (s["session"], s["status"], s["contract"], s["source"], s["bars"], s["missing_rth_minutes"])
        for s in es["sessions"]
    ] == [(d, "complete", contract, "projectx", BARS_PER_SESSION, 0) for d in days]
    assert report["vix"]["gaps"] == []
    (spy,) = report["dark_pool"]["tickers"]
    assert [(g["session"], g["cause"]) for g in spy["gaps"]] == [(days[0], "no_prints")]


def check_markdown(markdown: str, contract: str) -> None:
    assert "| ES | not served |" in markdown
    assert f"{contract} from projectx" in markdown


def check_no_secret(texts: list[str], secrets: list[str]) -> None:
    for value in [*secrets, PX_TOKEN]:
        for text in texts:
            assert value not in text


# ---------------------------------------------------------------- run_pull, virtual time

OLD_THU, OLD_FRI = date(2025, 3, 6), date(2025, 3, 7)
T0 = ny(date(2026, 3, 6), 8, 0)  # 365 days after OLD_THU and 364 after OLD_FRI
CALENDAR = SessionCalendar(date(2025, 1, 2), date(2026, 3, 31), times=TIMES)
ES_H5_ID = "CON.F.US.FAKE.ES.H25"
ROLL = RollCalendar(
    path=Path("roll_calendar.yaml"),
    covers=Covers(date(2025, 1, 2), date(2026, 3, 31)),
    periods={
        "ES": (
            # No atlas_symbol: the Bar_Source resolves it with /v1/search.
            ContractPeriod(
                "ES", date(2025, 1, 2), date(2025, 3, 14), "ESH5", projectx_contract_id=ES_H5_ID
            ),
        )
    },
)


@pytest.mark.asyncio
async def test_run_pull_uses_projectx_bars_after_an_atlas_no_data_probe(
    respx_router: respx.MockRouter, fake_secrets: FakeEnv, tmp_path: Path
) -> None:
    stdout = io.StringIO()
    fakes = mount(respx_router, stdout)
    clock = FakeClock(T0)
    env = EnvView(dict(fake_secrets.values), {})
    writer = LogWriter(Redactor(fake_secrets.secret_values()), stdout=stdout, stderr=io.StringIO())
    out_dir = tmp_path / "out"
    spec = PullSpec(start=OLD_THU, end=OLD_FRI, symbols=SYMBOLS, sample_interval_minutes=15)
    options = PullOptions(spec=spec, instruments=("ES",), dark_pool_tickers=("SPY",))

    async with httpx.AsyncClient() as http:
        with (
            FetchLog(writer, tmp_path / FETCH_LOG_FILE_NAME) as fetch_log,
            DataCache(tmp_path / "cache", calendar=CALENDAR) as cache,
        ):
            client = SkylitClient(
                env, ClientConfig(), fetch_log, clock, random.Random(7), http=http
            )
            session = ProjectXSession(env, writer.redactor, http, clock)
            ctx = PullContext(
                client=client,
                cache=cache,
                calendar=CALENDAR,
                roll=ROLL,
                writer=writer,
                out_dir=out_dir,
                pull_id="pull-e2e",
                started_ns=T0,
                projectx=ProjectXBars(session, clock, random.Random(7)),
                interactive=False,
                clock=clock.now,
            )
            summary = await clock.run(run_pull(options, ctx))

            # Every Cache_Window is stored; the bars are ProjectX's, from the roll contract.
            for d in (OLD_THU, OLD_FRI):
                for symbol in SYMBOLS:
                    for metric in METRICS:
                        for start in window_starts(d):
                            key = CacheWindowKey(symbol, metric, VIEW_ID, d, start)
                            assert cache.status(key) == "complete"
                bars = cache.bars.read_session("ES", 60, d)
                assert len(bars) == BARS_PER_SESSION
                assert {(b.contract, b.source) for b in bars} == {("ESH5", "projectx")}
                assert bars[0].open_ns == session_bar_range(CALENDAR, d, 60)[0]

    # Req 3.14, 3.1: the free calls first; the estimate is printed before the first replay.
    paths = [call.request.url.path for call in respx_router.calls]
    assert paths[:2] == [ep.ACCOUNT.path, ep.SYMBOLS.path]
    assert summary.estimate.request_count == 8
    assert fakes.skylit.output_at_first_replay is not None
    assert "Pull estimate" in fakes.skylit.output_at_first_replay
    # Req 3.3 (and 3.4): newest session first; range for the 364-day-old session,
    # /v1/historical samples every 15 minutes for the 365-day-old one.
    first_path, first_params = fakes.skylit.replays[0]
    assert (first_path, first_params["from"]) == (ep.HISTORICAL_RANGE.path, "2025-03-07T14:30:00Z")
    ranges = fakes.skylit.paths(ep.HISTORICAL_RANGE.path)
    assert sorted((r["from"], r["to"], r["metric"]) for r in ranges) == sorted(
        (format_rfc3339(start), format_rfc3339(start + 15 * M), metric)
        for start in window_starts(OLD_FRI)
        for metric in METRICS
    )
    samples = fakes.skylit.paths(ep.HISTORICAL.path)
    assert sorted((r["at"], r["metric"]) for r in samples) == sorted(
        (format_rfc3339(start), metric) for start in window_starts(OLD_THU) for metric in METRICS
    )
    assert {r["symbols"] for r in ranges + samples} == {"SPX,SPY"}

    # Req 4.2: one extended 1-minute request per probed session; Atlas has no ES bars.
    assert fakes.atlas.searches == ["ESH5"]
    probes = fakes.atlas.futures_calls()
    assert [(c["symbol"], c["resolution"], c["extended"]) for c in probes] == [
        ("CME:ESH5", "1", "true")
    ] * 2
    assert [(int(c["from"]) * S, int(c["to"]) * S) for c in probes] == [
        session_bar_range(CALENDAR, d, 60) for d in (OLD_THU, OLD_FRI)
    ]
    assert summary.probes["ES"].served is False
    # Req 4.3: ProjectX serves every session of the pull, in one request for the run.
    assert len(fakes.projectx.logins) == 1
    (body,) = fakes.projectx.bodies
    assert (body["contractId"], body["unit"], body["unitNumber"]) == (ES_H5_ID, 2, 1)
    assert body["startTime"] == format_instant(session_bar_range(CALENDAR, OLD_THU, 60)[0])
    assert body["endTime"] == format_instant(session_bar_range(CALENDAR, OLD_FRI, 60)[1])
    assert paths.index(RETRIEVE_BARS_PATH) < paths.index(ep.HISTORICAL_RANGE.path)
    out = stdout.getvalue()
    assert "Atlas probe ES: not served (0 bars on 2025-03-06, 0 on 2025-03-07)" in out
    assert "Bars ES (60 s, source projectx): 2 sessions fetched, 2 stored, 0 failed" in out
    assert (summary.heatmaps.complete, summary.heatmaps.incomplete) == (16, 0)

    # Req 3.11, 4.2, 4.6: the coverage report records all of it.
    report: dict[str, Any] = json.loads(summary.coverage.json.read_text())
    check_report(report, (OLD_THU, OLD_FRI), "ESH5")
    markdown = summary.coverage.markdown.read_text()
    check_markdown(markdown, "ESH5")
    fetch_text = (tmp_path / FETCH_LOG_FILE_NAME).read_text()
    check_no_secret(
        [out, markdown, summary.coverage.json.read_text(), fetch_text],
        fake_secrets.secret_values(),
    )


# ---------------------------------------------------------------- fse pull, real wiring

CLI_THU, CLI_FRI = date(2026, 9, 17), date(2026, 9, 18)
ES_Z6_ID = "CON.F.US.FAKE.ES.Z26"
ROLL_YAML = f"""\
# Test roll calendar: one ES contract with a fake ProjectX id.
covers:
  first: 2026-09-14
  last: 2026-12-11
instruments:
  ES:
    - {{first: 2026-09-14, last: 2026-12-11, contract: ESZ6, projectx_contract_id: {ES_Z6_ID}}}
"""


def calendar_dir(tmp_path: Path) -> Path:
    """The Project's exchange and event calendars, with a test roll calendar."""
    target = tmp_path / "calendars"
    target.mkdir()
    for name in (EXCHANGE_CALENDAR_FILE, ECONOMIC_EVENTS_FILE):
        shutil.copyfile(PROJECT_CALENDARS / name, target / name)
    (target / ROLL_CALENDAR_FILE).write_text(ROLL_YAML, encoding="utf-8")
    return target


def test_fse_pull_runs_end_to_end_through_the_cli(
    respx_router: respx.MockRouter, fake_secrets: FakeEnv, tmp_path: Path
) -> None:
    stdout, stderr = io.StringIO(), io.StringIO()
    fakes = mount(respx_router, stdout)
    out_dir = tmp_path / "out"
    argv = [
        "pull",
        *("--start", CLI_THU.isoformat(), "--end", CLI_FRI.isoformat()),
        *("--symbols", ",".join(SYMBOLS), "--pull-window", "09:30-10:00"),
        # Only used if the sessions are a year old when this runs: keeps the request count.
        *("--sample-interval-min", "15"),
        *("--instruments", "ES", "--dark-pool", "--dark-pool-tickers", "SPY"),
        *("--calendar-dir", str(calendar_dir(tmp_path))),
        *("--cache-dir", str(tmp_path / "cache"), "--out", str(out_dir)),
    ]

    code = cli.run(
        argv,
        project_dir=None,
        environ=dict(fake_secrets.values),
        stdout=stdout,
        stderr=stderr,
    )

    out, err = stdout.getvalue(), stderr.getvalue()
    assert (code, err) == (0, "")
    paths = [call.request.url.path for call in respx_router.calls]
    assert paths[:2] == [ep.ACCOUNT.path, ep.SYMBOLS.path]
    assert fakes.skylit.output_at_first_replay is not None
    assert "Pull estimate" in fakes.skylit.output_at_first_replay
    assert sum(len(fakes.skylit.paths(p)) for p in REPLAYS) == 8
    assert fakes.atlas.searches == ["ESZ6"]
    assert len(fakes.atlas.futures_calls()) == 2
    assert len(fakes.projectx.logins) == 1
    assert [b["contractId"] for b in fakes.projectx.bodies] == [ES_Z6_ID]
    assert "Atlas probe ES: not served" in out
    assert "Bars ES (60 s, source projectx)" in out

    (json_file,) = out_dir.glob("coverage_*.json")
    (md_file,) = out_dir.glob("coverage_*.md")
    report: dict[str, Any] = json.loads(json_file.read_text())
    check_report(report, (CLI_THU, CLI_FRI), "ESZ6")
    check_markdown(md_file.read_text(), "ESZ6")
    fetch_text = (out_dir / FETCH_LOG_FILE_NAME).read_text()
    skylit_calls = [c for c in respx_router.calls if c.request.url.host != PX_HOST]
    assert len(fetch_text.splitlines()) == len(skylit_calls)  # one line per Skylit attempt
    check_no_secret(
        [out, err, md_file.read_text(), json_file.read_text(), fetch_text],
        fake_secrets.secret_values(),
    )
