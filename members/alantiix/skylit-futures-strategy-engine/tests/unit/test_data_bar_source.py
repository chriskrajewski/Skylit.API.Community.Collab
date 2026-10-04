"""Unit tests for the Bar_Source (``fse.data.bar_source``).

Atlas and ProjectX are small fakes behind respx: each answers a bar request
with one bar per minute of the requested window, priced from the bar's open
minute, so a one-bar timestamp shift changes every price. The Skylit key,
ProjectX credentials and contract ids are fake; time is virtual.

**Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8**
"""

from __future__ import annotations

import json
import random
from collections.abc import AsyncIterator, Callable, Coroutine, Iterator
from dataclasses import dataclass, field
from datetime import date, time
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
import respx

from fse.calendars import ContractPeriod, Covers, RollCalendar
from fse.data.bar_source import (
    ATLAS_SYMBOL_NOT_FOUND,
    NO_PROJECTX_CONTRACT_ID,
    AtlasBars,
    BarSource,
    BarTimestampError,
    compare_bar_timestamps,
    pull_bars,
    session_bar_range,
)
from fse.data.cache import DataCache, HeatmapView
from fse.data.coverage import AtlasProbe, CoverageCollector, CoverageRequest, OffGridPrice
from fse.engine.types import Bar
from fse.logio import LogWriter, Redactor
from fse.projectx.bars import MISALIGNED_BAR, RETRIEVE_BARS_PATH, ProjectXBars
from fse.projectx.models import format_instant
from fse.projectx.session import LOGIN_PATH, ProjectXSession
from fse.secrets.env import EnvView
from fse.skylit.client import ClientConfig, SkylitClient
from fse.skylit.endpoints import Host
from fse.skylit.fetch_log import FETCH_LOG_FILE_NAME, FetchLog
from fse.timekit import (
    NS_PER_MINUTE,
    NS_PER_SECOND,
    Instant,
    SessionCalendar,
    ny_instant,
    parse_rfc3339,
)
from tests.conftest import FakeEnv
from tests.fakes.clock import FakeClock

KEY = "fake-skylit-key-0000"
PX_BASE = "https://api.projectx.invalid"
PX_TOKEN = "fake-session-token-0000"
ES_ID = "CON.F.US.FAKE.ES.H26"
S = NS_PER_SECOND
M = NS_PER_MINUTE

MON, TUE, WED, THU, FRI = (date(2026, 3, d) for d in (2, 3, 4, 5, 6))
MON2 = date(2026, 3, 9)  # the first session under daylight time (DST began Sunday 03-08)
CALENDAR = SessionCalendar(date(2026, 3, 1), date(2026, 3, 31))
ROLL = RollCalendar(
    path=Path("roll_calendar.yaml"),
    covers=Covers(date(2026, 3, 1), date(2026, 3, 31)),
    periods={
        "ES": (
            ContractPeriod("ES", MON, date(2026, 3, 13), "ESH6", projectx_contract_id=ES_ID),
            ContractPeriod("ES", date(2026, 3, 16), date(2026, 3, 31), "ESM6", "ATLAS-ESM6"),
        ),
        "NQ": (ContractPeriod("NQ", THU, date(2026, 3, 31), "NQH6", "ATLAS-NQH6"),),
    },
)
T0 = parse_rfc3339("2026-03-20T12:00:00Z")  # the pull runs after every session above
SEARCH = [{"symbol": "CME:ESH6", "ticker": "ESH6", "type": "futures"}]
BARS_PER_SESSION = (22 * 60) + 11  # 18:00 the evening before to 16:11


def price(open_s: int) -> float:
    """A tick-grid price that changes every minute."""
    return 6000.0 + ((open_s // 60) % 97) * 0.25


def ny(d: date, hh: int, mm: int) -> Instant:
    return ny_instant(d, time(hh, mm))


@dataclass
class FakeAtlas:
    """Atlas ``/v1/history``: one bar per minute of ``[from, to)``."""

    missing: set[int] = field(default_factory=set)  # bar open seconds not served
    no_data: bool = False
    max_span_s: int | None = None  # a wider window is a 400
    price_of: Callable[[int], float] = price
    calls: list[dict[str, str]] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.calls.append(params)
        lo, hi = int(params["from"]), int(params["to"])
        if self.max_span_s is not None and hi - lo > self.max_span_s:
            body = {"s": "error", "errmsg": "too wide", "requested_days": 3, "max_days": 2}
            return httpx.Response(400, json=body)
        t = [s for s in range(lo, hi, 60) if s not in self.missing]
        if self.no_data or not t:
            return httpx.Response(200, json={"s": "no_data"})
        p = [self.price_of(s) for s in t]
        return httpx.Response(
            200,
            json={
                "s": "ok",
                "t": t,
                "o": p,
                "h": [x + 0.5 for x in p],
                "l": [x - 0.5 for x in p],
                "c": [x + 0.25 for x in p],
                "v": [3] * len(t),
            },
        )


@dataclass
class FakeProjectX:
    """ProjectX ``retrieveBars``; ``label_shift_s`` labels each bar that much after its open."""

    label_shift_s: int = 0
    fail_from: Instant | None = None  # windows starting at or after this get HTTP 400
    bodies: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        lo = parse_rfc3339(str(body["startTime"]))
        hi = parse_rfc3339(str(body["endTime"]))
        if self.fail_from is not None and lo >= self.fail_from:
            return httpx.Response(400)
        bars = []
        for t in range(lo, hi, M):
            p = price(t // S)
            label = t + self.label_shift_s * S
            stamp = format_instant(label).replace("Z", "+00:00")
            bars.append({"t": stamp, "o": p, "h": p + 0.5, "l": p - 0.5, "c": p + 0.25, "v": 3})
        return httpx.Response(200, json={"bars": bars[::-1], "success": True, "errorCode": 0})


@dataclass
class Harness:
    clock: FakeClock
    atlas: FakeAtlas
    projectx: FakeProjectX
    routes: dict[str, respx.Route]
    skylit: SkylitClient
    px: ProjectXBars

    def source(self, *, projectx: bool = True, served: dict[str, bool] | None = None) -> BarSource:
        return BarSource(
            calendar=CALENDAR,
            roll=ROLL,
            atlas=AtlasBars(self.skylit),
            projectx=self.px if projectx else None,
            served=served,
        )

    def run[T](self, main: Coroutine[Any, Any, T]) -> Coroutine[Any, Any, T]:
        return self.clock.run(main)


def atlas_route(router: respx.MockRouter, path: str) -> respx.Route:
    return router.route(method="GET", scheme="https", host=Host.ATLAS.value, path=path)


@pytest.fixture
def fetch_log(tmp_path: Path) -> Iterator[FetchLog]:
    with FetchLog(LogWriter(Redactor([KEY])), tmp_path / FETCH_LOG_FILE_NAME) as log:
        yield log


@pytest_asyncio.fixture
async def harness(
    respx_router: respx.MockRouter, fetch_log: FetchLog, fake_secrets: FakeEnv
) -> AsyncIterator[Harness]:
    clock = FakeClock(T0)
    atlas, projectx = FakeAtlas(), FakeProjectX()
    respx_router.route(method="GET", scheme="https", host=Host.API.value, path="/v1/account").mock(
        return_value=httpx.Response(200, json={"data": {"limits": {}}})
    )
    routes = {
        "config": atlas_route(respx_router, "/v1/config").mock(
            return_value=httpx.Response(200, json={"max_fetch_trading_days": {"1": 90}})
        ),
        "search": atlas_route(respx_router, "/v1/search").mock(
            return_value=httpx.Response(200, json=SEARCH)
        ),
        "history": atlas_route(respx_router, "/v1/history").mock(side_effect=atlas),
    }
    respx_router.post(PX_BASE + LOGIN_PATH).mock(
        return_value=httpx.Response(200, json={"token": PX_TOKEN, "success": True, "errorCode": 0})
    )
    routes["bars"] = respx_router.post(PX_BASE + RETRIEVE_BARS_PATH).mock(side_effect=projectx)
    env = EnvView(dict(fake_secrets.values), {})
    async with httpx.AsyncClient() as http:
        skylit = SkylitClient(
            EnvView({"SKYLIT_API_KEY": KEY}, {}),
            ClientConfig(),
            fetch_log,
            clock,
            random.Random(7),
            http=http,
        )
        session = ProjectXSession(env, Redactor(), http, clock, base_url=PX_BASE)
        px = ProjectXBars(session, clock, random.Random(7))
        yield Harness(clock, atlas, projectx, routes, skylit, px)


def coverage(sessions: tuple[date, ...]) -> CoverageCollector:
    return CoverageCollector(
        CoverageRequest(
            pull_id="pull-1",
            started_at=T0,
            first=sessions[0],
            last=sessions[-1],
            sessions=sessions,
            symbols=("SPX",),
            view=HeatmapView(),
            instruments=("ES", "NQ"),
        )
    )


def opens(bars: tuple[Bar, ...]) -> list[Instant]:
    return [b.open_ns for b in bars]


# ---------------------------------------------------------------- Atlas fetch


@pytest.mark.asyncio
async def test_atlas_bars_cover_each_trading_day_across_a_weekend_and_dst(
    harness: Harness,
) -> None:
    source = harness.source(served={"ES": True})
    result = await harness.run(source.fetch_sessions("ES", [MON2, FRI]))

    # One request covers both sessions; the symbol came from search, max_days from config.
    assert harness.routes["config"].call_count == 1
    assert [dict(c.request.url.params) for c in harness.routes["search"].calls] == [
        {"query": "ESH6", "limit": "25"}
    ]
    assert harness.atlas.calls == [
        {
            "symbol": "CME:ESH6",
            "resolution": "1",
            "from": str(ny(THU, 18, 0) // S),
            "to": str(ny(MON2, 16, 11) // S),
            "extended": "true",
        }
    ]
    fri, mon = result
    assert (fri.session, mon.session) == (FRI, MON2)
    for s, d, prior in ((fri, FRI, THU), (mon, MON2, date(2026, 3, 8))):
        assert (s.contract, s.source, s.error_code, s.off_grid) == ("ESH6", "atlas", None, ())
        assert opens(s.bars) == list(range(ny(prior, 18, 0), ny(d, 16, 11), M))
        assert len(s.bars) == BARS_PER_SESSION
    # Monday's trading day starts Sunday 18:00 EDT = 22:00 UTC; Thursday's 18:00 EST = 23:00 UTC.
    assert mon.bars[0].open_ns == parse_rfc3339("2026-03-08T22:00:00Z")
    assert fri.bars[0].open_ns == parse_rfc3339("2026-03-05T23:00:00Z")
    bar = mon.bars[0]
    p = price(bar.open_ns // S)
    assert (bar.close_ns - bar.open_ns, bar.o, bar.o_t, bar.h_t, bar.l_t, bar.c_t) == (
        M,
        p,
        int(p * 4),
        int(p * 4) + 2,
        int(p * 4) - 2,
        int(p * 4) + 1,
    )


@pytest.mark.asyncio
async def test_atlas_spans_respect_max_days_and_a_400_halves_the_span(harness: Harness) -> None:
    harness.routes["config"].mock(
        return_value=httpx.Response(200, json={"max_fetch_trading_days": {"1": 2}})
    )
    harness.atlas.max_span_s = 30 * 3600  # Atlas rejects every 2-session window
    source = harness.source(served={"ES": True})
    result = await harness.run(source.fetch_sessions("ES", [MON, TUE, WED, THU]))

    windows = [(int(c["from"]), int(c["to"])) for c in harness.atlas.calls]
    mon_tue = (ny(date(2026, 3, 1), 18, 0) // S, ny(TUE, 16, 11) // S)
    wed_thu = (ny(TUE, 18, 0) // S, ny(THU, 16, 11) // S)
    assert windows == [
        mon_tue,
        (mon_tue[0], ny(MON, 16, 11) // S),
        (ny(MON, 18, 0) // S, mon_tue[1]),
        wed_thu,
        (wed_thu[0], ny(WED, 16, 11) // S),
        (ny(WED, 18, 0) // S, wed_thu[1]),
    ]
    assert [len(s.bars) for s in result] == [BARS_PER_SESSION] * 4
    assert all(s.error_code is None for s in result)


@pytest.mark.asyncio
async def test_missing_minutes_are_not_synthesized_and_off_grid_prices_are_listed(
    harness: Harness,
) -> None:
    gone = ny(TUE, 10, 0) // S
    odd = ny(TUE, 10, 5) // S
    harness.atlas.missing = {gone, gone + 60}
    harness.atlas.price_of = lambda s: 6000.1 if s == odd else price(s)
    source = harness.source(served={"ES": True})
    (tue,) = await harness.run(source.fetch_sessions("ES", [TUE]))

    assert len(tue.bars) == BARS_PER_SESSION - 2
    assert gone * S not in opens(tue.bars)
    bar = next(b for b in tue.bars if b.open_ns == odd * S)
    assert (bar.o, bar.o_t) == (6000.1, 24000)
    assert tue.off_grid == (
        OffGridPrice(odd * S, "o", 6000.1),
        OffGridPrice(odd * S, "h", 6000.1 + 0.5),
        OffGridPrice(odd * S, "l", 6000.1 - 0.5),
        OffGridPrice(odd * S, "c", 6000.1 + 0.25),
    )


@pytest.mark.asyncio
async def test_unusable_answers_and_unresolved_symbols_leave_an_error_code(
    harness: Harness,
) -> None:
    source = harness.source(served={"ES": True})
    harness.routes["history"].mock(
        return_value=httpx.Response(
            200,
            json={"s": "ok", "t": [ny(TUE, 10, 0) // S + 30], **{k: [1.0] for k in "ohlcv"}},
        )
    )
    (tue,) = await harness.run(source.fetch_sessions("ES", [TUE]))
    assert (tue.bars, tue.error_code, tue.usable) == ((), MISALIGNED_BAR, False)

    harness.routes["search"].mock(return_value=httpx.Response(200, json=[]))
    source = harness.source(served={"ES": True})
    (tue,) = await harness.run(source.fetch_sessions("ES", [TUE]))
    assert tue.error_code == ATLAS_SYMBOL_NOT_FOUND


# ---------------------------------------------------------------- contracts and ProjectX


@pytest.mark.asyncio
async def test_sessions_without_a_contract_get_no_request(harness: Harness) -> None:
    source = harness.source(served={"NQ": True})
    tue, thu = await harness.run(source.fetch_sessions("NQ", [TUE, THU]))

    assert (tue.contract, tue.source, tue.bars, tue.error_code) == (None, None, (), None)
    assert thu.contract == "NQH6"
    assert [c["from"] for c in harness.atlas.calls] == [str(ny(WED, 18, 0) // S)]


@pytest.mark.asyncio
async def test_projectx_serves_runs_of_consecutive_sessions(harness: Harness) -> None:
    source = harness.source()  # nothing probed: not served by Atlas
    result = await harness.run(source.fetch_sessions("ES", [MON, TUE, THU, date(2026, 3, 16)]))

    assert source.source_for("ES", 60) == "projectx"
    assert [(b["startTime"], b["endTime"], b["contractId"]) for b in harness.projectx.bodies] == [
        ("2026-03-01T23:00:00Z", "2026-03-03T21:11:00Z", ES_ID),
        ("2026-03-04T23:00:00Z", "2026-03-05T21:11:00Z", ES_ID),
    ]
    assert harness.atlas.calls == []
    assert [(s.source, s.contract, len(s.bars)) for s in result[:3]] == [
        ("projectx", "ESH6", BARS_PER_SESSION)
    ] * 3
    # ESM6 has no ProjectX contract id in this roll calendar.
    assert (result[3].contract, result[3].error_code) == ("ESM6", NO_PROJECTX_CONTRACT_ID)


@pytest.mark.asyncio
async def test_second_bars_always_come_from_projectx(harness: Harness) -> None:
    source = harness.source(served={"ES": True})
    assert source.source_for("ES", 60) == "atlas"
    assert source.source_for("ES", 5) == "projectx"


# ---------------------------------------------------------------- probe and OQ10


@pytest.mark.asyncio
async def test_probe_not_served_falls_back_to_projectx_for_every_session(
    harness: Harness,
) -> None:
    harness.atlas.missing = set(range(ny(WED, 18, 0) // S, ny(THU, 16, 11) // S, 60))
    collector = coverage((TUE, WED, THU))
    source = harness.source()
    result = await harness.run(source.probe(["ES"], TUE, THU, coverage=collector))

    probe = result["ES"]
    assert (probe.served, probe.error_code, probe.bars_last) == (False, None, 0)
    assert probe.bars_first == BARS_PER_SESSION
    assert probe.timestamp_check is None
    assert collector.atlas_probe("ES") == AtlasProbe("ES", served=False, error_code=None)
    assert [c["extended"] for c in harness.atlas.calls] == ["true", "true"]
    sessions = await harness.run(source.fetch_sessions("ES", [TUE, WED, THU]))
    assert {s.source for s in sessions} == {"projectx"}
    assert len(harness.atlas.calls) == 2  # the probe only


@pytest.mark.asyncio
async def test_probe_records_the_last_error_code(harness: Harness) -> None:
    harness.routes["history"].mock(
        return_value=httpx.Response(404, json={"error": {"code": "symbol_not_found"}})
    )
    collector = coverage((TUE, THU))
    result = await harness.run(harness.source().probe(["ES", "NQ"], TUE, THU, coverage=collector))

    assert collector.atlas_probe("ES") == AtlasProbe("ES", False, "symbol_not_found")
    # NQ has no contract on the first session; its last session's request failed.
    assert (result["NQ"].served, result["NQ"].error_code) == (False, "symbol_not_found")
    assert result["NQ"].bars_first == 0


@pytest.mark.asyncio
async def test_probe_served_with_aligned_projectx_bars(harness: Harness) -> None:
    collector = coverage((TUE, THU))
    source = harness.source()
    result = await harness.run(source.probe(["ES"], TUE, THU, coverage=collector))

    check = result["ES"].timestamp_check
    assert result["ES"].served
    assert source.source_for("ES", 60) == "atlas"
    assert check is not None
    assert (check.status, check.compared, check.offset_bars) == ("aligned", 60, 0)
    assert harness.projectx.bodies[0]["startTime"] == "2026-03-05T14:30:00Z"  # 09:30 EST
    assert collector.atlas_probe("ES") == AtlasProbe("ES", served=True)


@pytest.mark.asyncio
async def test_probe_stops_the_pull_when_sources_disagree_by_a_whole_bar(
    harness: Harness,
) -> None:
    harness.projectx.label_shift_s = 60  # ProjectX labels by close: one bar later than Atlas
    collector = coverage((TUE, THU))
    with pytest.raises(BarTimestampError) as exc:
        await harness.run(harness.source().probe(["ES"], TUE, THU, coverage=collector))

    assert exc.value.exit_code == 4
    assert exc.value.check.status == "shifted"
    assert exc.value.check.offset_bars == 1
    assert "OQ10" in str(exc.value)
    assert collector.atlas_probe("ES") == AtlasProbe("ES", served=True)


def test_compare_bar_timestamps_needs_enough_common_bars() -> None:
    def bar(t: Instant, p: float) -> Bar:
        ticks = int(p * 4)
        return Bar("ES", "ESH6", 60, t, t + M, p, p, p, p, 1.0, ticks, ticks, ticks, ticks, "atlas")

    few = [bar(i * M, price(i * 60)) for i in range(5)]
    assert compare_bar_timestamps(few, few, 60)[0] == "inconclusive"
    many = [bar(i * M, price(i * 60)) for i in range(30)]
    other = [bar(i * M, 1000.0 + i) for i in range(30)]
    assert compare_bar_timestamps(many, many, 60)[:2] == ("aligned", 30)
    assert compare_bar_timestamps(many, other, 60)[0] == "inconclusive"


def test_session_range_reaches_the_bar_after_the_flat_deadline() -> None:
    early = SessionCalendar(date(2026, 3, 1), date(2026, 3, 31), early_closes={FRI: time(13, 15)})
    assert session_bar_range(early, TUE, 60) == (ny(MON, 18, 0), ny(TUE, 16, 11))
    # Early close: the Flat_Deadline (13:00) is before the close, so the range covers RTH.
    assert session_bar_range(early, FRI, 60) == (ny(THU, 18, 0), ny(FRI, 13, 16))
    assert session_bar_range(early, TUE, 5)[1] == ny(TUE, 16, 10) + 5 * S


# ---------------------------------------------------------------- storing


@pytest.mark.asyncio
async def test_pull_bars_stores_usable_sessions_and_leaves_failures_incomplete(
    harness: Harness, tmp_path: Path
) -> None:
    harness.projectx.fail_from = ny(WED, 18, 0)
    collector = coverage((TUE, THU))
    with DataCache(tmp_path / "cache") as cache:
        results = await harness.run(
            pull_bars(harness.source(), cache.bars, "ES", [THU, TUE], coverage=collector)
        )
        assert [r.session for r in results] == [TUE, THU]
        assert cache.bars.status("ES", 60, TUE) == "complete"
        assert cache.bars.status("ES", 60, THU) == "incomplete"
        stored = cache.bars.read_session("ES", 60, TUE)
        assert stored == list(results[0].bars)
        record = cache.bars.record("ES", 60, TUE)
        assert record is not None
        assert (record.contract, record.source, record.rows) == ("ESH6", "projectx", 1331)
    fetch = collector.bar_fetch("ES", THU)
    assert fetch is not None
    assert (fetch.contract, fetch.source, fetch.error_code) == ("ESH6", "projectx", "HTTP 400")
    tue = collector.bar_fetch("ES", TUE)
    assert tue is not None
    assert tue.error_code is None
