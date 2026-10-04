"""Unit tests for the puller (``fse.data.puller``).

Every Skylit and Atlas endpoint is a respx mock: range and historical replays
answer with small boards built from the request, Atlas answers one bar per
minute, and the dark-pool tape is empty. The key is fake, time is virtual
(``FakeClock``) and every cache and report directory is under ``tmp_path``.
The Pull_Window is shortened to 09:30-10:00 (two Cache_Windows per session)
so a session costs four range requests.

The end-to-end integration test with ProjectX bars is task 5.12.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.8, 3.9, 3.11, 3.15, 4.2, 4.12**
"""

from __future__ import annotations

import asyncio
import io
import json
import random
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, time
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import pytest_asyncio
import respx

from fse.calendars import ContractPeriod, Covers, RollCalendar
from fse.data.cache import CacheWindowKey, DataCache, HeatmapView
from fse.data.cache_io import CacheWriteError
from fse.data.estimate import PullSizeLimitError
from fse.data.planner import PullSpec
from fse.data.puller import PullContext, PullOptions, PullSummary, run_pull
from fse.engine.types import Metric
from fse.logio import LogWriter, Redactor
from fse.secrets.env import EnvView
from fse.skylit import endpoints as ep
from fse.skylit.client import ClientConfig, SkylitClient
from fse.skylit.endpoints import Endpoint, format_rfc3339
from fse.skylit.fetch_log import FETCH_LOG_FILE_NAME, FetchLog
from fse.timekit import (
    NS_PER_SECOND,
    Instant,
    SessionCalendar,
    SessionTimes,
    ny_instant,
    parse_rfc3339,
)
from tests.fakes.clock import FakeClock

KEY = "fake-skylit-key-0000"
S = NS_PER_SECOND
VIEW_ID = HeatmapView().view_id()

THU, FRI = date(2026, 3, 5), date(2026, 3, 6)
OLD = date(2025, 2, 4)  # 398 days before the pull: sampled with /v1/historical
TIMES = SessionTimes(pull_start=time(9, 30), pull_end=time(10, 0))
CALENDAR = SessionCalendar(date(2025, 1, 2), date(2026, 3, 31), times=TIMES)
ROLL = RollCalendar(
    path=Path("roll_calendar.yaml"),
    covers=Covers(date(2025, 1, 2), date(2026, 3, 31)),
    periods={
        "ES": (ContractPeriod("ES", date(2026, 2, 2), date(2026, 3, 13), "ESH6", "CME:ESH6"),)
    },
)
T0 = ny_instant(date(2026, 3, 9), time(8, 0))  # Monday morning: every session above has ended
SYMBOLS = ("SPX", "SPY")


def ny(d: date, hh: int, mm: int, ss: int = 0) -> Instant:
    return ny_instant(d, time(hh, mm, ss))


def options(start: date, end: date, **kw: Any) -> PullOptions:
    spec_kw = {k: kw.pop(k) for k in ("sample_interval_minutes",) if k in kw}
    spec = PullSpec(start=start, end=end, symbols=SYMBOLS, **spec_kw)
    return PullOptions(spec=spec, instruments=("ES",), **kw)


# ---------------------------------------------------------------- fakes


def _board(symbol: str, as_of_ns: Instant) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "asOf": format_rfc3339(as_of_ns),
        "spot": 101.5,
        "previousClose": 100.0,
        "expirations": ["2026-03-20"],
        "strikes": [
            {"strike": 100.0, "value": 1.5, "nodeType": "king"},
            {"strike": 105.0, "value": -2.25, "nodeType": "normal"},
        ],
    }


def _meta(metric: str) -> dict[str, Any]:
    return {"metric": metric, "resolution": "1s", "mode": "historical", "cached": False}


@dataclass
class FakeSkylit:
    """Range frames at ``from``, ``from + 1 s`` and ``to``; historical boards 1 s before ``at``.

    ``answer`` may replace the answer to any replay (it gets the endpoint path
    and the query) or raise.
    """

    answer: Callable[[str, dict[str, str]], httpx.Response | None] | None = None
    calls: list[tuple[str, dict[str, str]]] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        path = request.url.path
        self.calls.append((path, params))
        if self.answer is not None:
            got = self.answer(path, params)
            if got is not None:
                return got
        symbols = params["symbols"].split(",")
        if path == ep.HISTORICAL_RANGE.path:
            lo, hi = parse_rfc3339(params["from"]), parse_rfc3339(params["to"])
            data = {
                "from": params["from"],
                "to": params["to"],
                "symbols": [
                    {
                        "symbol": s,
                        "axes": [
                            {"id": 0, "strikes": [100.0, 105.0], "expirations": ["2026-03-20"]}
                        ],
                        "frames": [
                            {
                                "asOf": format_rfc3339(t),
                                "axis": 0,
                                "spot": 101.5,
                                "previousClose": 100.0,
                                "values": [1.5, -2.25],
                            }
                            for t in (lo, lo + S, hi)
                        ],
                    }
                    for s in symbols
                ],
            }
        else:
            at = parse_rfc3339(params["at"])
            data = {"symbols": [_board(s, at - S) for s in symbols]}
        return httpx.Response(200, json={"data": data, "meta": _meta(params["metric"])})

    def replays(self, path: str = ep.HISTORICAL_RANGE.path) -> list[dict[str, str]]:
        return [params for p, params in self.calls if p == path]


@dataclass
class FakeAtlas:
    """Atlas ``/v1/history``: one tick-grid bar per minute of ``[from, to)``."""

    calls: list[dict[str, str]] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.calls.append(params)
        t = list(range(int(params["from"]), int(params["to"]), 60))
        p = [6000.0 + (s // 60 % 97) * 0.25 for s in t]
        body = {"s": "ok", "t": t, "o": p, "h": p, "l": p, "c": p, "v": [1] * len(t)}
        return httpx.Response(200, json=body)


@dataclass
class Harness:
    skylit: FakeSkylit
    atlas: FakeAtlas
    dark_pool: respx.Route
    client: SkylitClient
    cache: DataCache
    clock: FakeClock
    out_dir: Path
    stdout: io.StringIO
    writer: LogWriter

    async def pull(
        self, opts: PullOptions, *, started_ns: Instant = T0, pull_id: str = "pull-1"
    ) -> PullSummary:
        ctx = PullContext(
            client=self.client,
            cache=self.cache,
            calendar=CALENDAR,
            roll=ROLL,
            writer=self.writer,
            out_dir=self.out_dir,
            pull_id=pull_id,
            started_ns=started_ns,
            interactive=False,
            clock=self.clock.now,
        )
        return await self.clock.run(run_pull(opts, ctx))

    def report(self, pull_id: str = "pull-1") -> dict[str, Any]:
        data: dict[str, Any] = json.loads((self.out_dir / f"coverage_{pull_id}.json").read_text())
        return data

    def status(self, symbol: str, metric: str, session: date, hh: int, mm: int) -> str:
        key = CacheWindowKey(symbol, cast(Metric, metric), VIEW_ID, session, ny(session, hh, mm))
        return self.cache.status(key)


def _route(router: respx.MockRouter, endpoint: Endpoint) -> respx.Route:
    return router.route(method="GET", scheme="https", host=endpoint.host.value, path=endpoint.path)


@pytest.fixture
def fetch_log(tmp_path: Path) -> Iterator[FetchLog]:
    with FetchLog(LogWriter(Redactor([KEY])), tmp_path / FETCH_LOG_FILE_NAME) as log:
        yield log


@pytest_asyncio.fixture
async def harness(
    respx_router: respx.MockRouter, fetch_log: FetchLog, tmp_path: Path
) -> AsyncIterator[Harness]:
    skylit, atlas = FakeSkylit(), FakeAtlas()
    account = {"data": {"limits": {"requestsPerMinute": 600, "historicalInFlight": 2}}}
    _route(respx_router, ep.ACCOUNT).mock(return_value=httpx.Response(200, json=account))
    symbols = {
        "data": {
            "symbols": [
                {
                    "symbol": s,
                    "isIndex": s == "SPX",
                    "metrics": ["gamma", "vanna"],
                    "history": {"from": "2023-03-28", "to": "2026-03-06"},
                }
                for s in SYMBOLS
            ]
        }
    }
    _route(respx_router, ep.SYMBOLS).mock(return_value=httpx.Response(200, json=symbols))
    _route(respx_router, ep.HISTORICAL_RANGE).mock(side_effect=skylit)
    _route(respx_router, ep.HISTORICAL).mock(side_effect=skylit)
    _route(respx_router, ep.ATLAS_CONFIG).mock(
        return_value=httpx.Response(200, json={"max_fetch_trading_days": {"1": 90}})
    )
    _route(respx_router, ep.ATLAS_HISTORY).mock(side_effect=atlas)
    empty = {"data": [], "meta": {"limit": 5000, "offset": 0, "count": 0, "hasMore": False}}
    dark_pool = _route(respx_router, ep.DARK_POOL_TRADES).mock(
        return_value=httpx.Response(200, json=empty)
    )
    clock = FakeClock(T0)
    stdout = io.StringIO()
    writer = LogWriter(Redactor([KEY]), stdout=stdout, stderr=io.StringIO())
    async with httpx.AsyncClient() as http:
        client = SkylitClient(
            EnvView({"SKYLIT_API_KEY": KEY}, {}),
            ClientConfig(),
            fetch_log,
            clock,
            random.Random(7),
            http=http,
        )
        with DataCache(tmp_path / "cache", calendar=CALENDAR) as cache:
            yield Harness(
                skylit, atlas, dark_pool, client, cache, clock, tmp_path / "out", stdout, writer
            )


# ---------------------------------------------------------------- tests


@pytest.mark.asyncio
async def test_pull_stores_every_window_and_writes_the_coverage_report(harness: Harness) -> None:
    summary = await harness.pull(options(THU, FRI, dark_pool_tickers=("SPY",)))

    # Newest session first, one range request per window and metric carrying both symbols.
    ranges = harness.skylit.replays()
    assert len(ranges) == 8 == summary.estimate.request_count
    assert ranges[0]["from"] == format_rfc3339(ny(FRI, 9, 30))
    assert {r["symbols"] for r in ranges} == {"SPX,SPY"}
    for d in (THU, FRI):
        for symbol in SYMBOLS:
            for metric in ("gamma", "vanna"):
                assert harness.status(symbol, metric, d, 9, 30) == "complete"
                assert harness.status(symbol, metric, d, 9, 45) == "complete"
    # The range end is inclusive: the frame at 09:45:00 belongs to the second window only.
    first = CacheWindowKey("SPX", "gamma", VIEW_ID, THU, ny(THU, 9, 30))
    second = CacheWindowKey("SPX", "gamma", VIEW_ID, THU, ny(THU, 9, 45))
    assert [s.as_of_ns for s in harness.cache.read_window(first)] == [
        ny(THU, 9, 30),
        ny(THU, 9, 30, 1),
    ]
    assert [s.as_of_ns for s in harness.cache.read_window(second)] == [
        ny(THU, 9, 45),
        ny(THU, 9, 45, 1),
    ]
    assert (summary.heatmaps.complete, summary.heatmaps.incomplete) == (16, 0)

    # The estimate is printed before the probe, and the probe before the first session.
    out = harness.stdout.getvalue()
    assert (
        out.index("Pull estimate")
        < out.index("Atlas probe ES: served")
        < out.index(f"{FRI} (range): 4 Replay_Requests")
    )
    assert "OQ10 timestamp check: skipped: no ProjectX" in out

    # Bars, VIX and dark-pool prints are stored; the pull is in the catalog.
    for d in (THU, FRI):
        assert harness.cache.bars.status("ES", 60, d) == "complete"
    vix = harness.cache.vix.read_daily()
    assert vix[THU].open is not None
    assert vix[FRI].close is not None
    assert harness.cache.catalog.pull("pull-1") is not None
    # The dark-pool request reaches back 5 sessions for the Gate's trailing window.
    (call,) = harness.dark_pool.calls
    assert dict(call.request.url.params)["date_start"] == "2026-02-26"
    assert dict(call.request.url.params)["date_end"] == "2026-03-06"

    report = harness.report()
    assert report["outcome"] == "completed"
    spx_gamma = next(
        h for h in report["heatmaps"] if (h["symbol"], h["metric"]) == ("SPX", "gamma")
    )
    assert spx_gamma["sessions_stored"] == 2
    assert [s["resolution"] for s in spx_gamma["sessions"]] == [["1s"], ["1s"]]
    (es,) = report["futures"]
    assert es["atlas_probe"] == {"served": True, "error_code": None}
    assert [(s["contract"], s["source"]) for s in es["sessions"]] == [("ESH6", "atlas")] * 2
    (spy,) = report["dark_pool"]["tickers"]
    assert [(g["session"], g["cause"]) for g in spy["gaps"]] == [
        ("2026-03-05", "no_prints"),
        ("2026-03-06", "no_prints"),
    ]


@pytest.mark.asyncio
async def test_a_rerun_serves_stored_windows_without_a_replay_request(harness: Harness) -> None:
    await harness.pull(options(THU, FRI))
    harness.skylit.calls.clear()
    harness.atlas.calls.clear()

    summary = await harness.pull(options(THU, FRI), pull_id="pull-2")

    assert harness.skylit.calls == []
    assert summary.estimate.request_count == 0
    assert summary.estimate.served_windows == 16
    # Only the probe asks Atlas again; stored bars and VIX values are not refetched.
    assert len(harness.atlas.calls) == 2
    assert harness.report("pull-2")["outcome"] == "completed"


@pytest.mark.asyncio
async def test_a_pull_over_the_size_limit_sends_no_replay_request(harness: Harness) -> None:
    with pytest.raises(PullSizeLimitError) as raised:
        await harness.pull(options(THU, FRI, size_limit_gb=1e-9))

    assert raised.value.exit_code == 2
    assert harness.skylit.calls == []
    assert harness.atlas.calls == []
    assert "Pull estimate" in harness.stdout.getvalue()
    assert not harness.out_dir.exists()


@pytest.mark.asyncio
async def test_a_failed_request_leaves_its_windows_incomplete_and_no_data_is_stored(
    harness: Harness,
) -> None:
    def answer(path: str, params: dict[str, str]) -> httpx.Response | None:
        start = params.get("from")
        if (start, params["metric"]) == (format_rfc3339(ny(THU, 9, 30)), "gamma"):
            return httpx.Response(503)
        if (start, params["metric"]) == (format_rfc3339(ny(THU, 9, 45)), "vanna"):
            return httpx.Response(404, json={"error": {"code": "no_data", "message": "none"}})
        return None

    harness.skylit.answer = answer
    summary = await harness.pull(options(THU, THU))

    for symbol in SYMBOLS:
        assert harness.status(symbol, "gamma", THU, 9, 30) == "incomplete"
        assert harness.status(symbol, "vanna", THU, 9, 45) == "no_data"
        assert harness.status(symbol, "vanna", THU, 9, 30) == "complete"
        assert harness.status(symbol, "gamma", THU, 9, 45) == "complete"
    tally = summary.heatmaps
    assert (tally.failed_requests, tally.incomplete, tally.no_data, tally.complete) == (1, 2, 2, 4)
    assert tally.last_failure == "/v1/historical/range HTTP 503"
    assert "Rerun the same command" in harness.stdout.getvalue()
    sessions = next(h for h in harness.report()["heatmaps"] if h["metric"] == "gamma")["sessions"]
    assert [w["status"] for w in sessions[0]["incomplete_windows"]] == ["incomplete"]


@pytest.mark.asyncio
async def test_a_cache_write_failure_stops_the_pull_before_the_next_replay_request(
    harness: Harness,
) -> None:
    harness.cache.root.mkdir(parents=True)
    (harness.cache.root / "heatmaps").write_text("not a directory")

    with pytest.raises(CacheWriteError) as raised:
        await harness.pull(options(THU, THU))

    assert raised.value.exit_code == 4
    message = str(raised.value)
    assert "SPX gamma" in message
    assert "session 2026-03-05 Cache_Window start 09:30" in message
    # Only the first window's two requests (gamma and vanna) were sent.
    assert len(harness.skylit.replays()) == 2
    assert harness.status("SPX", "gamma", THU, 9, 30) == "incomplete"
    assert harness.status("SPX", "gamma", THU, 9, 45) == "absent"
    assert harness.report()["outcome"] == "failed"


@pytest.mark.asyncio
async def test_an_interrupt_leaves_windows_incomplete_and_writes_the_report(
    harness: Harness,
) -> None:
    def answer(path: str, params: dict[str, str]) -> httpx.Response | None:
        if params.get("from") == format_rfc3339(ny(THU, 9, 45)) and params["metric"] == "gamma":
            raise asyncio.CancelledError  # what Ctrl-C does to the pull task
        return None

    harness.skylit.answer = answer
    with pytest.raises(asyncio.CancelledError):
        await harness.pull(options(THU, THU))

    assert harness.status("SPX", "gamma", THU, 9, 30) == "complete"
    assert harness.status("SPX", "gamma", THU, 9, 45) == "incomplete"
    assert harness.status("SPY", "vanna", THU, 9, 45) == "incomplete"
    report = harness.report()
    assert (report["outcome"], report["error"]) == ("interrupted", "Operator interrupt")


@pytest.mark.asyncio
async def test_old_sessions_are_sampled_and_a_missing_sample_is_a_no_data_gap(
    harness: Harness,
) -> None:
    def answer(path: str, params: dict[str, str]) -> httpx.Response | None:
        if params.get("at") == format_rfc3339(ny(OLD, 9, 45)) and params["metric"] == "gamma":
            return httpx.Response(404, json={"error": {"code": "no_data", "message": "none"}})
        return None

    harness.skylit.answer = answer
    summary = await harness.pull(options(OLD, OLD, sample_interval_minutes=15))

    samples = harness.skylit.replays(ep.HISTORICAL.path)
    assert harness.skylit.replays() == []
    assert sorted((p["at"], p["metric"]) for p in samples) == sorted(
        (format_rfc3339(ny(OLD, h, m)), metric)
        for h, m in ((9, 30), (9, 45))
        for metric in ("gamma", "vanna")
    )
    # The board answered for 09:30 is the latest at or before it, so it is kept as returned.
    key = CacheWindowKey("SPX", "gamma", VIEW_ID, OLD, ny(OLD, 9, 30))
    (snap,) = harness.cache.read_window(key)
    assert (snap.as_of_ns, snap.source_endpoint, snap.node_types) == (
        ny(OLD, 9, 29, 59),
        "historical",
        ("king", "normal"),
    )
    assert harness.status("SPX", "gamma", OLD, 9, 45) == "no_data"
    assert (summary.heatmaps.complete, summary.heatmaps.no_data) == (6, 2)
    spx = next(
        h for h in harness.report()["heatmaps"] if (h["symbol"], h["metric"]) == ("SPX", "gamma")
    )
    (gap,) = spx["sessions"][0]["no_data_gaps"]
    assert (gap["start"], gap["end"]) == (ny(OLD, 9, 45), ny(OLD, 10, 0))
    # No ES contract in the roll calendar for that session: no bar request, cause recorded.
    assert "Atlas probe ES: not served" in harness.stdout.getvalue()


@pytest.mark.asyncio
async def test_windows_not_ended_at_pull_start_get_no_request(harness: Harness) -> None:
    started = ny(THU, 9, 50)  # the second window and the session's bars are still open

    summary = await harness.pull(options(THU, THU), started_ns=started)

    assert {r["from"] for r in harness.skylit.replays()} == {format_rfc3339(ny(THU, 9, 30))}
    assert summary.heatmaps.unfinished == 4
    assert harness.status("SPX", "gamma", THU, 9, 30) == "complete"
    assert harness.status("SPX", "gamma", THU, 9, 45) == "absent"
    assert harness.cache.bars.status("ES", 60, THU) == "absent"
    assert "not finished at pull start" in harness.stdout.getvalue()
