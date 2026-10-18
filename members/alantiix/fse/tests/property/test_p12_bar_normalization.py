"""Property 12: Bar normalization.

*For any* bars from Atlas (Unix seconds) or ProjectX (ISO 8601 with offset),
on any date including daylight-saving transition dates, the normalized bar
has ``close_ns - open_ns`` equal to the bar duration and an ``open_ns`` equal
to the true UTC instant of the bar open; every bar of a session comes from
the contract the roll calendar assigns to that session; and for any set of
removed RTH minutes, the coverage report lists exactly the missing minute
ranges and no bar is synthesized.

Two tests:

1. Wire timestamps. Bars that open on any interval-grid instant from 2000 to
   2040, often within six hours of a New York DST change, go through the
   Atlas parser (``t`` in Unix seconds) and the ProjectX parser (``t`` in ISO
   8601 with ``Z``, ``+00:00``, the New York offset of that instant, or any
   other fixed offset, with 0, 3 or 7 fractional digits). Each bar opens at
   the true instant and closes one interval later. Prices are kept as
   received, ticks are ``round_half_up(price x 4)`` and every off-grid price
   is listed.
2. Sessions end to end. :func:`pull_bars` drives a real :class:`BarSource`
   (Atlas served, or the ProjectX fallback) against respx fakes of both APIs,
   then :func:`build_coverage_report` reads the Data_Cache. The calendar spans
   about 11 days around a DST change or any date, with holidays and an early
   close. The roll calendar has random roll dates and gaps. Each fake serves
   every contract at every instant, priced per contract and per minute, so a
   bar from the wrong contract or a shifted timestamp changes its prices.
   Random RTH and overnight bars are withheld. Each session must hold exactly
   the served bars of its window (18:00 the evening before to one bar after
   the later of the Flat_Deadline and the RTH close), all from its contract,
   and the report must list exactly the RTH minutes no bar covers.

The oracles use ``zoneinfo`` and integer arithmetic, not ``fse.timekit``. The
Skylit key, ProjectX credentials and contract ids are fake; time is virtual.

**Validates: Requirements 4.5, 4.7, 4.8**
"""

from __future__ import annotations

import asyncio
import json
import math
import random
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from fractions import Fraction
from itertools import pairwise
from pathlib import Path
from typing import Any, Final, Literal
from zoneinfo import ZoneInfo

import httpx
import respx
from hypothesis import example, given
from hypothesis import strategies as st

from fse.calendars import ContractPeriod, Covers, RollCalendar
from fse.data.bar_source import (
    AtlasBars,
    BarSource,
    SessionBars,
    normalize_atlas_bars,
    off_grid_prices,
    pull_bars,
)
from fse.data.cache import DataCache, HeatmapView
from fse.data.coverage import (
    CoverageCollector,
    CoverageReport,
    CoverageRequest,
    MissingMinutes,
    OffGridPrice,
    TimeRange,
    build_coverage_report,
    missing_minute_ranges,
)
from fse.engine.types import Bar, BarSourceName
from fse.logio import LogWriter, Redactor
from fse.projectx.bars import RETRIEVE_BARS_PATH, ProjectXBars
from fse.projectx.models import RetrieveBarsResponse, to_bar
from fse.projectx.session import LOGIN_PATH, ProjectXSession
from fse.secrets.env import EnvView
from fse.skylit.client import ClientConfig, SkylitClient
from fse.skylit.endpoints import Host
from fse.skylit.fetch_log import FETCH_LOG_FILE_NAME, FetchLog
from fse.skylit.models import HistoryBars, parse_atlas_history
from fse.timekit import SessionCalendar
from tests.fakes.clock import FakeClock

NY: Final = ZoneInfo("America/New_York")
EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
S: Final = 1_000_000_000
M: Final = 60 * S
ONE_DAY: Final = timedelta(days=1)

INSTRUMENT: Final = "ES"
KEY: Final = "fake-skylit-key-0000"
PX_BASE: Final = "https://api.projectx.invalid"
PX_ENV: Final = {
    "PROJECTX_USERNAME": "fake-projectx-user-0000",
    "PROJECTX_API_KEY": "fake-projectx-key-0000",
}
PX_TOKEN: Final = "fake-session-token-0000"
ATLAS_PREFIX: Final = "FAKE:"
PX_PREFIX: Final = "CON.F.US.FAKE."

type Column = Literal["o", "h", "l", "c"]
type ZoneName = Literal["Z", "+00:00", "ny"]
type Label = ZoneName | int
"""How a ProjectX ``t`` is written: ``Z``, ``+00:00``, New York, or a fixed offset in minutes."""

COLUMNS: Final[tuple[Column, ...]] = ("o", "h", "l", "c")
ZONE_NAMES: Final[tuple[ZoneName, ...]] = ("Z", "+00:00", "ny")
SOURCES: Final[tuple[BarSourceName, ...]] = ("atlas", "projectx")
FRACTION_DIGITS: Final = (0, 3, 7)
OFF_GRID_DELTAS: Final = (0.05, 0.1, 0.125, 0.3, 0.5)  # 0.5 is two whole ticks: on the grid
EARLY_CLOSES: Final = (time(13, 0), time(13, 15))
ATLAS_INTERVALS: Final = (60, 300, 900, 3600)
PROJECTX_INTERVALS: Final = (5, 60, 300, 900, 3600)


def _ns(dt: datetime) -> int:
    """An aware datetime as integer ns since the Unix epoch (exact)."""
    return (dt - EPOCH) // timedelta(microseconds=1) * 1000


def _ny(d: date, wall: time) -> int:
    return _ns(datetime.combine(d, wall, tzinfo=NY))


T0: Final = _ns(datetime(2040, 1, 1, tzinfo=UTC))  # the pull runs after every session


def _dst_changes(first_year: int, last_year: int) -> tuple[int, ...]:
    """Unix seconds of each whole UTC hour at which New York's UTC offset changes."""
    out: list[int] = []
    day = datetime(first_year, 1, 1, tzinfo=UTC)
    end = datetime(last_year + 1, 1, 1, tzinfo=UTC)
    while day < end:
        nxt = day + ONE_DAY
        if day.astimezone(NY).utcoffset() != nxt.astimezone(NY).utcoffset():
            hour = day
            while hour.astimezone(NY).utcoffset() == day.astimezone(NY).utcoffset():
                hour += timedelta(hours=1)
            out.append(_ns(hour) // S)
        day = nxt
    return tuple(out)


DST_CHANGES: Final = _dst_changes(2000, 2040)
DST_SUNDAYS: Final = tuple(
    d
    for d in (datetime.fromtimestamp(t, NY).date() for t in DST_CHANGES)
    if date(2016, 1, 10) <= d <= date(2035, 12, 20)
)


def _ticks(price: float) -> int:
    """Exact ``round_half_up(price x 4)``."""
    return math.floor(Fraction(price) * 4 + Fraction(1, 2))


def _on_grid(price: float) -> bool:
    return (Fraction(price) * 4).denominator == 1


def _iso(t_s: int, label: Label, frac_digits: int) -> str:
    """Unix seconds ``t_s`` as ProjectX might write it, in the zone ``label`` names."""
    if label in ("Z", "+00:00"):
        tz: timezone | ZoneInfo = UTC
    elif label == "ny":
        tz = NY
    else:
        assert isinstance(label, int)
        tz = timezone(timedelta(minutes=label))
    text = datetime.fromtimestamp(t_s, tz).isoformat(timespec="seconds")
    head, offset = text[:19], text[19:]
    if label == "Z":
        offset = "Z"
    return head + ("." + "0" * frac_digits if frac_digits else "") + offset


def _unix_s(text: str) -> int:
    """An ISO 8601 request time as Unix seconds (the fake's own parser)."""
    return (datetime.fromisoformat(text) - EPOCH) // timedelta(seconds=1)


labels: st.SearchStrategy[Label] = st.one_of(
    st.sampled_from(ZONE_NAMES),
    st.integers(-(23 * 60 + 59), 23 * 60 + 59),
)


def _expected_off_grid(bars: Sequence[Bar]) -> tuple[OffGridPrice, ...]:
    return tuple(
        OffGridPrice(b.open_ns, column, price)
        for b in bars
        for column, price in zip(COLUMNS, (b.o, b.h, b.l, b.c), strict=True)
        if not _on_grid(price)
    )


# ---------------------------------------------------------------- 1. wire timestamps


@dataclass(frozen=True, slots=True)
class WireBar:
    """One bar as the source knows it: its true open (Unix seconds) and its values."""

    open_s: int
    prices: tuple[float, float, float, float]
    volume: float


@dataclass(frozen=True, slots=True)
class WireCase:
    interval_s: int
    bars: tuple[WireBar, ...]
    label: Label
    frac_digits: int


prices: st.SearchStrategy[float] = st.builds(
    lambda ticks, delta: ticks / 4 + delta,
    st.integers(4_000, 100_000),
    st.sampled_from((0.0, 0.0, 0.0, *OFF_GRID_DELTAS)),
)


@st.composite
def wire_cases(draw: st.DrawFn) -> WireCase:
    interval_s = draw(st.sampled_from(PROJECTX_INTERVALS))
    lo, hi = (
        _ns(datetime(2000, 1, 2, tzinfo=UTC)) // S,
        _ns(datetime(2040, 12, 30, tzinfo=UTC)) // S,
    )
    center = draw(st.one_of(st.sampled_from(DST_CHANGES), st.integers(lo, hi)))
    base = center - center % interval_s
    reach = max(1, 6 * 3600 // interval_s)
    steps = draw(st.lists(st.integers(-reach, reach), min_size=1, max_size=30, unique=True))
    bars = tuple(
        WireBar(
            base + k * interval_s,
            draw(st.tuples(prices, prices, prices, prices)),
            float(draw(st.integers(0, 50_000))),
        )
        for k in steps
    )
    return WireCase(interval_s, bars, draw(labels), draw(st.sampled_from(FRACTION_DIGITS)))


def _check_normalized(bars: Sequence[Bar], case: WireCase, source: BarSourceName) -> None:
    want = {b.open_s * S: b for b in case.bars}
    got = sorted(bars, key=lambda b: b.open_ns)
    assert [b.open_ns for b in got] == sorted(want), "a bar is missing, extra or shifted"
    for bar in got:
        wire = want[bar.open_ns]
        assert bar.close_ns - bar.open_ns == case.interval_s * S
        assert (bar.instrument, bar.contract, bar.interval_s, bar.source) == (
            INSTRUMENT,
            "ESX0",
            case.interval_s,
            source,
        )
        assert (bar.o, bar.h, bar.l, bar.c, bar.v) == (*wire.prices, wire.volume)
        assert (bar.o_t, bar.h_t, bar.l_t, bar.c_t) == tuple(_ticks(p) for p in wire.prices)
    assert off_grid_prices(got) == _expected_off_grid(got)


@given(case=wire_cases())
def test_wire_timestamps_normalize_to_the_true_open_instant(case: WireCase) -> None:
    ordered = sorted(case.bars, key=lambda b: b.open_s)

    # ProjectX: ISO 8601 with an offset, newest first as documented.
    px_body = json.dumps(
        {
            "bars": [
                {
                    "t": _iso(b.open_s, case.label, case.frac_digits),
                    "o": b.prices[0],
                    "h": b.prices[1],
                    "l": b.prices[2],
                    "c": b.prices[3],
                    "v": b.volume,
                }
                for b in reversed(ordered)
            ],
            "success": True,
            "errorCode": 0,
        }
    )
    parsed = RetrieveBarsResponse.parse(json.loads(px_body))
    px_bars = [
        to_bar(w, instrument=INSTRUMENT, contract="ESX0", interval_s=case.interval_s)
        for w in parsed.bars
    ]
    _check_normalized(px_bars, case, "projectx")

    # Atlas: UDF columns with t in Unix seconds, oldest first. Atlas has no second bars.
    if case.interval_s not in ATLAS_INTERVALS:
        return
    atlas_body = json.dumps(
        {
            "s": "ok",
            "t": [b.open_s for b in ordered],
            **{c: [b.prices[i] for b in ordered] for i, c in enumerate(COLUMNS)},
            "v": [b.volume for b in ordered],
        }
    )
    history = parse_atlas_history(json.loads(atlas_body))
    assert isinstance(history, HistoryBars)
    atlas_bars = normalize_atlas_bars(
        history,
        instrument=INSTRUMENT,
        contract="ESX0",
        interval_s=case.interval_s,
        start_ns=ordered[0].open_s * S,
        end_ns=(ordered[-1].open_s + case.interval_s) * S,
    )
    assert isinstance(atlas_bars, tuple), atlas_bars
    _check_normalized(atlas_bars, case, "atlas")


# ---------------------------------------------------------------- 2. sessions end to end


@dataclass(frozen=True, slots=True)
class Segment:
    """Calendar dates ``first`` to ``last``; ``contract`` is ``None`` for a roll-calendar gap."""

    first: date
    last: date
    contract: str | None


@dataclass(frozen=True, slots=True)
class Window:
    """The oracle's instants (ns) for one session."""

    start: int  # 18:00 the evening before
    end: int  # one bar after the later of the Flat_Deadline and the RTH close
    rth_open: int
    rth_close: int


@dataclass(frozen=True, slots=True)
class SessionCase:
    source: BarSourceName
    interval_s: int
    cal_first: date
    cal_last: date
    holidays: tuple[date, ...]
    early_closes: tuple[tuple[date, time], ...]
    segments: tuple[Segment, ...]
    sessions: tuple[date, ...]
    removed: tuple[tuple[int, int], ...]  # [lo, hi) Unix seconds the feeds do not serve
    off_grid: tuple[tuple[int, Column, float], ...]  # (open s, column, delta)
    label: Label
    frac_digits: int

    def calendar(self) -> SessionCalendar:
        return SessionCalendar(
            self.cal_first, self.cal_last, self.holidays, dict(self.early_closes)
        )

    def roll(self) -> RollCalendar:
        periods = tuple(
            ContractPeriod(
                INSTRUMENT,
                seg.first,
                seg.last,
                seg.contract,
                atlas_symbol=ATLAS_PREFIX + seg.contract,
                projectx_contract_id=PX_PREFIX + seg.contract,
            )
            for seg in self.segments
            if seg.contract is not None
        )
        return RollCalendar(
            path=Path("roll_calendar.yaml"),
            covers=Covers(self.cal_first, self.cal_last),
            periods={INSTRUMENT: periods},
        )

    def contract_for(self, d: date) -> str | None:
        return next((s.contract for s in self.segments if s.first <= d <= s.last), None)

    def window(self, d: date) -> Window:
        return _window(d, self.interval_s, dict(self.early_closes))

    def request(self) -> CoverageRequest:
        return CoverageRequest(
            pull_id="p12",
            started_at=T0,
            first=self.sessions[0],
            last=self.sessions[-1],
            sessions=self.sessions,
            symbols=("SPX",),
            view=HeatmapView(),
            instruments=(INSTRUMENT,),
            bar_interval_s=self.interval_s,
        )


def _window(d: date, interval_s: int, early: Mapping[date, time]) -> Window:
    rth_close = _ny(d, early.get(d, time(16, 0)))
    flat = rth_close - 15 * M if d in early else _ny(d, time(16, 10))
    return Window(
        start=_ny(d - ONE_DAY, time(18, 0)),
        end=max(flat, rth_close) + interval_s * S,
        rth_open=_ny(d, time(9, 30)),
        rth_close=rth_close,
    )


def _days(first: date, last: date) -> list[date]:
    return [date.fromordinal(o) for o in range(first.toordinal(), last.toordinal() + 1)]


@st.composite
def session_cases(draw: st.DrawFn) -> SessionCase:
    anchor = draw(
        st.one_of(st.sampled_from(DST_SUNDAYS), st.dates(date(2016, 1, 10), date(2035, 12, 20)))
    )
    cal_first, cal_last = anchor - 5 * ONE_DAY, anchor + 5 * ONE_DAY
    all_days = _days(cal_first, cal_last)
    weekdays = [d for d in all_days if d.weekday() < 5]
    holidays = draw(st.sets(st.sampled_from(weekdays), max_size=2))
    open_days = [d for d in weekdays if d not in holidays]
    early = draw(
        st.lists(
            st.tuples(st.sampled_from(open_days), st.sampled_from(EARLY_CLOSES)),
            max_size=1,
        )
    )
    interval_s = draw(st.sampled_from((60, 300)))
    source = draw(st.sampled_from(SOURCES))

    cuts = sorted(draw(st.sets(st.integers(1, len(all_days) - 1), max_size=3)))
    segments = tuple(
        Segment(
            all_days[lo],
            all_days[hi - 1],
            f"ESX{i}" if draw(st.sampled_from((True, True, True, False))) else None,
        )
        for i, (lo, hi) in enumerate(pairwise([0, *cuts, len(all_days)]))
    )
    sessions = tuple(
        sorted(draw(st.lists(st.sampled_from(open_days), min_size=1, max_size=4, unique=True)))
    )

    step = interval_s
    removed: list[tuple[int, int]] = []
    off_grid: list[tuple[int, Column, float]] = []
    for d in sessions:
        w = _window(d, interval_s, dict(early))
        start_s, end_s = w.start // S, w.end // S
        rth_s, rth_end_s = w.rth_open // S, w.rth_close // S
        n_rth, n_win = (rth_end_s - rth_s) // step, (end_s - start_s) // step
        mode = draw(st.sampled_from(("none", "some", "some", "some", "all_rth", "all")))
        if mode == "all":
            removed.append((start_s, end_s))
        elif mode == "all_rth":
            removed.append((rth_s, rth_end_s))
        elif mode == "some":
            blocks = draw(
                st.lists(st.tuples(st.integers(0, n_rth - 1), st.integers(1, n_rth)), max_size=3)
            )
            points = draw(st.lists(st.integers(0, n_rth - 1), max_size=8))
            outside = draw(st.lists(st.integers(0, n_win - 1), max_size=4))
            removed += [(rth_s + i * step, rth_s + min(i + k, n_rth) * step) for i, k in blocks]
            removed += [(rth_s + i * step, rth_s + (i + 1) * step) for i in points]
            removed += [(start_s + i * step, start_s + (i + 1) * step) for i in outside]
        marks = draw(
            st.lists(
                st.tuples(
                    st.integers(0, n_win - 1),
                    st.sampled_from(COLUMNS),
                    st.sampled_from(OFF_GRID_DELTAS),
                ),
                max_size=3,
                unique_by=lambda m: (m[0], m[1]),
            )
        )
        off_grid += [(start_s + i * step, column, delta) for i, column, delta in marks]

    return SessionCase(
        source=source,
        interval_s=interval_s,
        cal_first=cal_first,
        cal_last=cal_last,
        holidays=tuple(sorted(holidays)),
        early_closes=tuple(early),
        segments=segments,
        sessions=sessions,
        removed=tuple(removed),
        off_grid=tuple(off_grid),
        label=draw(labels),
        frac_digits=draw(st.sampled_from(FRACTION_DIGITS)),
    )


class Market:
    """Both fakes' data: every contract trades at every instant, priced per contract and minute."""

    def __init__(self, case: SessionCase) -> None:
        self.case = case
        self.interval_s = case.interval_s
        self.index = {
            seg.contract: i for i, seg in enumerate(case.segments) if seg.contract is not None
        }
        step = case.interval_s
        self.removed = frozenset(
            t for lo, hi in case.removed for t in range(lo - lo % step, hi, step) if t >= lo
        )
        self.deltas: dict[tuple[int, Column], float] = {
            (t, column): delta for t, column, delta in case.off_grid
        }

    def bar(self, contract: str, t_s: int) -> tuple[float, float, float, float, float]:
        """``(o, h, l, c, v)`` of ``contract``'s bar opening at ``t_s``."""
        base = 1000.0 * (self.index[contract] + 1) + ((t_s // 60) % 89) * 0.25
        raw = (base, base + 1.0, base - 0.75, base + 0.25)
        pairs = zip(raw, COLUMNS, strict=True)
        o, h, low, c = (p + self.deltas.get((t_s, col), 0.0) for p, col in pairs)
        return o, h, low, c, float((t_s // 60) % 50 + 1)

    def opens(self, lo_s: int, hi_s: int) -> list[int]:
        """Served bar opens in ``[lo_s, hi_s)``, oldest first."""
        step = self.interval_s
        first = -(-lo_s // step) * step
        return [t for t in range(first, hi_s, step) if t not in self.removed]

    def atlas_history(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        contract = params["symbol"].removeprefix(ATLAS_PREFIX)
        if contract not in self.index or params["resolution"] != str(self.interval_s // 60):
            return httpx.Response(404, json={"s": "error", "errmsg": "symbol_not_found"})
        t = self.opens(int(params["from"]), int(params["to"]))
        if not t:
            return httpx.Response(200, json={"s": "no_data"})
        rows = [self.bar(contract, x) for x in t]
        names = ("o", "h", "l", "c", "v")
        return httpx.Response(
            200,
            json={"s": "ok", "t": t, **{n: [r[i] for r in rows] for i, n in enumerate(names)}},
        )

    def retrieve_bars(self, request: httpx.Request) -> httpx.Response:
        body: dict[str, Any] = json.loads(request.content)
        contract = str(body["contractId"]).removeprefix(PX_PREFIX)
        unit = (2, self.interval_s // 60)  # minute bars
        if contract not in self.index or (body["unit"], body["unitNumber"]) != unit:
            return httpx.Response(200, json={"bars": [], "success": False, "errorCode": 1})
        bars = []
        for t in self.opens(_unix_s(body["startTime"]), _unix_s(body["endTime"])):
            o, h, low, c, v = self.bar(contract, t)
            stamp = _iso(t, self.case.label, self.case.frac_digits)
            bars.append({"t": stamp, "o": o, "h": h, "l": low, "c": c, "v": v})
        return httpx.Response(200, json={"bars": bars[::-1], "success": True, "errorCode": 0})


def _mock_apis(router: respx.MockRouter, market: Market) -> None:
    def atlas(path: str) -> respx.Route:
        return router.route(method="GET", scheme="https", host=Host.ATLAS.value, path=path)

    router.route(method="GET", scheme="https", host=Host.API.value, path="/v1/account").mock(
        return_value=httpx.Response(200, json={"data": {"limits": {}}})
    )
    atlas("/v1/config").mock(
        return_value=httpx.Response(200, json={"max_fetch_trading_days": {"1": 90, "5": 90}})
    )
    atlas("/v1/history").mock(side_effect=market.atlas_history)
    router.post(PX_BASE + LOGIN_PATH).mock(
        return_value=httpx.Response(200, json={"token": PX_TOKEN, "success": True, "errorCode": 0})
    )
    router.post(PX_BASE + RETRIEVE_BARS_PATH).mock(side_effect=market.retrieve_bars)


async def _pull(
    case: SessionCase, market: Market, tmp: Path
) -> tuple[tuple[SessionBars, ...], CoverageReport]:
    calendar, roll = case.calendar(), case.roll()
    clock = FakeClock(T0)
    with (
        FetchLog(LogWriter(Redactor([KEY])), tmp / FETCH_LOG_FILE_NAME) as fetch_log,
        respx.mock(assert_all_mocked=True, assert_all_called=False) as router,
        DataCache(tmp / "cache", calendar=calendar) as cache,
    ):
        _mock_apis(router, market)
        async with httpx.AsyncClient() as http:
            skylit = SkylitClient(
                EnvView({"SKYLIT_API_KEY": KEY}, {}),
                ClientConfig(),
                fetch_log,
                clock,
                random.Random(0),
                http=http,
            )
            session = ProjectXSession(
                EnvView(PX_ENV, {}), Redactor(), http, clock, base_url=PX_BASE
            )
            source = BarSource(
                calendar=calendar,
                roll=roll,
                atlas=AtlasBars(skylit),
                projectx=ProjectXBars(session, clock, random.Random(0)),
                served={INSTRUMENT: case.source == "atlas"},
            )
            collector = CoverageCollector(case.request())
            results = await clock.run(
                pull_bars(
                    source,
                    cache.bars,
                    INSTRUMENT,
                    case.sessions,
                    interval_s=case.interval_s,
                    coverage=collector,
                )
            )
        report = build_coverage_report(
            collector, cache=cache, calendar=calendar, ended_at=T0, roll=roll
        )
    return results, report


def _missing_rth(w: Window, served_s: set[int], interval_s: int) -> tuple[TimeRange, ...]:
    """Runs of RTH minutes that no served bar covers (a bar covers each minute it overlaps)."""
    out: list[TimeRange] = []
    run: int | None = None
    for minute in range(w.rth_open, w.rth_close, M):
        m_s = minute // S
        if m_s - m_s % interval_s in served_s:
            if run is not None:
                out.append(TimeRange(run, minute))
                run = None
        elif run is None:
            run = minute
    if run is not None:
        out.append(TimeRange(run, w.rth_close))
    return tuple(out)


def _check_sessions(
    case: SessionCase, market: Market, results: Sequence[SessionBars], report: CoverageReport
) -> None:
    assert [r.session for r in results] == list(case.sessions)
    (futures,) = report.futures
    assert (futures.instrument, futures.interval_s) == (INSTRUMENT, case.interval_s)
    reported = {s.session: s for s in futures.sessions}
    step_ns = case.interval_s * S
    for r in results:
        d, w, contract = r.session, case.window(r.session), case.contract_for(r.session)
        cov = reported[d]
        if contract is None:  # no contract in the roll calendar: no request, no bars
            assert (r.contract, r.source, r.bars, r.error_code) == (None, None, (), None)
            assert (cov.contract, cov.source, cov.bars) == (None, None, 0)
            assert cov.missing_rth == (MissingMinutes(w.rth_open, w.rth_close, "no_contract"),)
            continue

        # Exactly the served bars that open and close in the window, oldest first.
        served = [
            t for t in market.opens(w.start // S, w.end // S) if (t + case.interval_s) * S <= w.end
        ]
        assert [b.open_ns for b in r.bars] == [t * S for t in served], d
        assert (r.contract, r.source, r.error_code) == (contract, case.source, None)
        for bar in r.bars:
            assert (bar.instrument, bar.contract, bar.interval_s, bar.source) == (
                INSTRUMENT,
                contract,
                case.interval_s,
                case.source,
            )
            assert bar.close_ns - bar.open_ns == step_ns
            # Prices identify the contract and the minute, so this catches a bar
            # from another contract (Req 4.5) or a shifted timestamp (Req 4.7).
            want = market.bar(contract, bar.open_ns // S)
            assert (bar.o, bar.h, bar.l, bar.c, bar.v) == want, (d, bar)
            assert (bar.o_t, bar.h_t, bar.l_t, bar.c_t) == tuple(_ticks(p) for p in want[:4])
        off_grid = _expected_off_grid(r.bars)
        assert r.off_grid == off_grid

        # Missing RTH minutes: exactly the uncovered runs (Req 4.8).
        gaps = _missing_rth(w, set(served), case.interval_s)
        spans = ((b.open_ns, b.close_ns) for b in r.bars)
        assert missing_minute_ranges(spans, w.rth_open, w.rth_close) == gaps
        assert (cov.contract, cov.source, cov.bars) == (contract, case.source, len(served))
        assert cov.missing_rth == tuple(
            MissingMinutes(g.start_ns, g.end_ns, "no_bars") for g in gaps
        )
        assert cov.off_grid_prices == off_grid


def _example(
    source: BarSourceName,
    interval_s: int,
    sunday: date,
    removed_wall: Sequence[tuple[time, time]],
    label: Label,
) -> SessionCase:
    """The Friday before and the Monday after a DST change, rolling on that Monday."""
    fri, mon = sunday - 2 * ONE_DAY, sunday + ONE_DAY
    removed = tuple((_ny(mon, a) // S, _ny(mon, b) // S) for a, b in removed_wall)
    return SessionCase(
        source=source,
        interval_s=interval_s,
        cal_first=sunday - 5 * ONE_DAY,
        cal_last=sunday + 5 * ONE_DAY,
        holidays=(),
        early_closes=(),
        segments=(
            Segment(sunday - 5 * ONE_DAY, sunday, "ESX0"),
            Segment(mon, sunday + 5 * ONE_DAY, "ESX1"),
        ),
        sessions=(fri, mon),
        removed=removed,
        off_grid=((_ny(mon, time(9, 30)) // S, "o", 0.1),),
        label=label,
        frac_digits=3,
    )


@given(case=session_cases())
@example(
    case=_example("atlas", 60, date(2026, 3, 8), [(time(10, 0), time(10, 2))], "Z"),
)
@example(
    case=_example(
        "projectx",
        300,
        date(2026, 11, 1),
        [(time(9, 30), time(9, 35)), (time(15, 55), time(16, 0))],
        "ny",
    ),
)
def test_sessions_keep_one_contract_true_instants_and_report_missing_minutes(
    case: SessionCase,
) -> None:
    market = Market(case)
    with tempfile.TemporaryDirectory(prefix="fse-p12-") as tmp:
        results, report = asyncio.run(_pull(case, market, Path(tmp)))
    _check_sessions(case, market, results, report)
