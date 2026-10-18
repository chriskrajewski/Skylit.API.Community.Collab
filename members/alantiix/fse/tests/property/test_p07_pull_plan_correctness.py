"""Property 7: Pull plan correctness.

*For any* date range, symbol set, per-symbol first history date, pull date and
cache state, the plan holds: every request covers at most one Cache_Window;
sessions under 365 days old use ``/v1/historical/range`` and older ones use
``/v1/historical`` at exactly the sample-interval instants; no request targets
a session before the symbol's first history date; no request targets a
complete or ``no_data`` window of a completed session; and the printed
request-count estimate equals the number of planned requests.

The test plans each generated pull with :func:`plan_pull` and compares it with
an oracle written here from the requirements, not from the planner:

- sessions are the weekdays of the range that are not holidays; a session's
  Pull_Window runs from the configured start to the configured end or the
  early close, whichever is first, and is tiled into 15-minute Cache_Windows
  from its start (the last may be shorter);
- a session is completed when its Pull_Window ended strictly before the pull
  started, and its age is the calendar days from the session to the New York
  date of the pull start;
- a symbol gets nothing on a session before its first history date;
- a ``complete`` or ``no_data`` window of a completed session is served;
- every other window gets, per symbol and metric, one range request (age under
  365 days) or one ``/v1/historical`` request at each instant
  ``Pull_Window start + k x sample interval`` (k >= 0, before the Pull_Window
  end) inside the window. With ``label_sample_minutes``, a range window also
  gets label-sample requests at the instants of that grid.

Each planned request is flattened into one "cell" per symbol it carries
(endpoint, label flag, symbol, metric, session, window, instant). The cells
must equal the oracle's as a multiset, so no window is missing, duplicated or
added. Each request must also name exactly the window of the WindowPlan it
sits in, carry at most 5 (range) or 10 (historical) distinct symbols, and
target no symbol before its first history date. The estimate's request counts
(total, per endpoint, per symbol and metric) and the printed table must equal
the planned counts.

Inputs cluster where a planner goes wrong: session ages around 365 days, the
pull starting at, just before or just after the newest session's Pull_Window
end, first history dates inside the range, more than 5 and more than 10
symbols, sample intervals that do not divide 15 minutes, early closes and
Pull_Window ends off the 15-minute grid. The cache is a ``status()`` lookup
over a generated state; the planner reads nothing else from the Data_Cache.

**Validates: Requirements 3.1, 3.3, 3.4, 3.5, 3.8**
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, time, timedelta
from typing import Final, NamedTuple

from hypothesis import example, given
from hypothesis import strategies as st

from fse.data.cache import CacheWindowKey, HeatmapView
from fse.data.catalog import WindowStatus
from fse.data.estimate import estimate_pull, render_estimate
from fse.data.planner import (
    DEFAULT_SYMBOLS,
    HISTORICAL_MAX_SYMBOLS,
    RANGE_MAX_AGE_DAYS,
    RANGE_MAX_SYMBOLS,
    PullPlan,
    PullSpec,
    ReplayEndpoint,
    ReplayRequest,
    plan_pull,
)
from fse.engine.types import Metric
from fse.timekit import NS_PER_MINUTE, Instant, SessionCalendar, SessionTimes, ny_instant

ONE_DAY: Final = timedelta(days=1)
WINDOW_NS: Final = 15 * NS_PER_MINUTE
CLOSE: Final = time(16, 0)
MAX_RANGE_DAYS: Final = 7  # calendar days in a pull range
TODAY_MIN: Final = date(2024, 1, 1)
TODAY_MAX: Final = date(2035, 12, 31)

SYMBOL_POOL: Final[tuple[str, ...]] = (
    *DEFAULT_SYMBOLS,
    *("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG"),
)  # 12 symbols: more than one group at either endpoint
METRIC_ORDERS: Final[tuple[tuple[Metric, ...], ...]] = (
    ("gamma",),
    ("vanna",),
    ("gamma", "vanna"),
    ("vanna", "gamma"),
)
VIEWS: Final = (HeatmapView(), HeatmapView(max_strikes=40, max_expirations=2))
STATUSES: Final[tuple[WindowStatus, ...]] = ("absent", "incomplete", "complete", "no_data")
SERVED_STATUSES: Final[frozenset[WindowStatus]] = frozenset({"complete", "no_data"})
type StatusMode = WindowStatus | None  # None: a status drawn per window


def _days(first: date, last: date) -> list[date]:
    return [date.fromordinal(o) for o in range(first.toordinal(), last.toordinal() + 1)]


def _wall(minutes: int) -> time:
    return time(minutes // 60, minutes % 60)


# ---------------------------------------------------------------- scenario


@dataclass(frozen=True, slots=True)
class FakeCache:
    """A Data_Cache status lookup over a fixed state; unlisted keys get ``default``."""

    statuses: Mapping[CacheWindowKey, WindowStatus]
    default: WindowStatus

    def status(self, key: CacheWindowKey) -> WindowStatus:
        return self.statuses.get(key, self.default)


@dataclass(frozen=True, slots=True)
class Case:
    """One pull: the spec, ``GET /v1/symbols``, the start instant, calendar and cache."""

    spec: PullSpec
    listed: Mapping[str, date]
    today: date
    started_ns: Instant
    cal_first: date
    cal_last: date
    holidays: frozenset[date]
    early_closes: Mapping[date, time]
    times: SessionTimes
    default_status: WindowStatus
    statuses: Mapping[CacheWindowKey, WindowStatus] = field(repr=False)

    def calendar(self) -> SessionCalendar:
        return SessionCalendar(
            self.cal_first, self.cal_last, self.holidays, self.early_closes, self.times
        )

    def cache(self) -> FakeCache:
        return FakeCache(self.statuses, self.default_status)

    def is_session(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def sessions(self) -> list[date]:
        return [d for d in _days(self.spec.start, self.spec.end) if self.is_session(d)]

    def pull_window(self, d: date) -> tuple[Instant, Instant]:
        end = min(self.times.pull_end, self.early_closes.get(d, CLOSE))
        return ny_instant(d, self.times.pull_start), ny_instant(d, end)

    def windows(self, d: date) -> tuple[tuple[Instant, Instant], ...]:
        """The session's Cache_Windows: 15 minutes each from the Pull_Window start."""
        start, end = self.pull_window(d)
        out: list[tuple[Instant, Instant]] = []
        t = start
        while t < end:
            out.append((t, min(t + WINDOW_NS, end)))
            t += WINDOW_NS
        return tuple(out)

    def describe(self) -> str:
        s = self.spec
        early = {d.isoformat(): c.isoformat("minutes") for d, c in self.early_closes.items()}
        first = {k: v.isoformat() for k, v in self.listed.items()}
        stored = dict(Counter(self.statuses.values()))
        return (
            f"pull {s.start} to {s.end}, today {self.today}, started_ns {self.started_ns}, "
            f"symbols {s.symbols}, metrics {s.metrics}, sample {s.sample_interval_minutes} min, "
            f"label {s.label_sample_minutes}, first history {first}, Pull_Window "
            f"{self.times.pull_start}-{self.times.pull_end}, holidays "
            f"{sorted(d.isoformat() for d in self.holidays)}, early closes {early}, "
            f"cache {stored} else {self.default_status}"
        )


@st.composite
def _session_times(draw: st.DrawFn) -> SessionTimes:
    start = draw(st.just(9 * 60) | st.integers(8 * 60, 10 * 60))
    end = draw(st.just(16 * 60) | st.integers(start + 1, 16 * 60 + 30))
    return SessionTimes(pull_start=_wall(start), pull_end=_wall(end))


def _close_times(times: SessionTimes) -> st.SearchStrategy[time]:
    lo = max(9 * 60 + 31, times.pull_start.hour * 60 + times.pull_start.minute + 1)
    return st.just(time(13, 0)) | st.integers(lo, 15 * 60 + 59).map(_wall)


@st.composite
def _started_ns(draw: st.DrawFn, case_today: date, newest_end: Instant | None) -> Instant:
    """Any instant of the pull date, or one next to the newest Pull_Window end on it."""
    if newest_end is not None and draw(st.booleans()):
        return newest_end + draw(st.sampled_from((-NS_PER_MINUTE, -1, 0, 1, NS_PER_MINUTE)))
    midnight = ny_instant(case_today, time(0, 0))
    next_midnight = ny_instant(case_today + ONE_DAY, time(0, 0))
    return draw(
        st.sampled_from((midnight, next_midnight - 1)) | st.integers(midnight, next_midnight - 1)
    )


@st.composite
def cases(draw: st.DrawFn) -> Case:
    today = draw(st.dates(TODAY_MIN, TODAY_MAX))
    length = draw(st.integers(0, MAX_RANGE_DAYS - 1))
    ages = draw(st.sampled_from(("recent", "boundary", "old", "any")))
    if ages == "recent":
        age = draw(st.integers(0, 3))
    elif ages == "boundary":  # the range's oldest date is 365+ days old, its newest at most 365
        age = draw(st.integers(RANGE_MAX_AGE_DAYS - length, RANGE_MAX_AGE_DAYS))
    elif ages == "old":
        age = draw(st.integers(RANGE_MAX_AGE_DAYS, 800))
    else:
        age = draw(st.integers(0, 800))
    end = today - timedelta(days=age)
    start = end - timedelta(days=length)
    cal_first = start - timedelta(days=draw(st.integers(0, 5)))
    cal_last = end + timedelta(days=draw(st.integers(0, 5)))

    times = draw(_session_times())
    weekdays = [d for d in _days(cal_first, cal_last) if d.weekday() < 5]
    holidays = frozenset(draw(st.sets(st.sampled_from(weekdays), max_size=3)) if weekdays else ())
    open_days = [d for d in weekdays if d not in holidays]
    early: dict[date, time] = (
        draw(st.dictionaries(st.sampled_from(open_days), _close_times(times), max_size=2))
        if open_days
        else {}
    )

    symbols = tuple(
        draw(st.lists(st.sampled_from(SYMBOL_POOL), min_size=1, max_size=12, unique=True))
    )
    spec = PullSpec(
        start,
        end,
        symbols=symbols,
        metrics=draw(st.sampled_from(METRIC_ORDERS)),
        view=draw(st.sampled_from(VIEWS)),
        sample_interval_minutes=draw(st.integers(1, 15)),
        label_sample_minutes=draw(st.none() | st.integers(1, 60)),
    )
    listed: dict[str, date] = {
        s: start + timedelta(days=draw(st.integers(-3, length + 3))) for s in symbols
    }
    listed.update(draw(st.dictionaries(st.sampled_from(("XXX", "YYY")), st.dates(), max_size=2)))

    shell = Case(spec, listed, today, 0, cal_first, cal_last, holidays, early, times, "absent", {})
    newest_end = shell.pull_window(end)[1] if end == today and shell.is_session(end) else None
    started = draw(_started_ns(today, newest_end))

    # Cache state: per session, symbol and metric one status for every window, or a
    # status drawn per window.
    rnd = draw(st.randoms(use_true_random=False))
    view_id = spec.view.view_id()
    statuses: dict[CacheWindowKey, WindowStatus] = {}
    for d in shell.sessions():
        windows = shell.windows(d)
        for symbol in symbols:
            for metric in spec.metrics:
                mode: StatusMode = draw(st.sampled_from((*STATUSES, None)))
                for window_start, _ in windows:
                    status = rnd.choice(STATUSES) if mode is None else mode
                    if status != "absent":
                        key = CacheWindowKey(symbol, metric, view_id, d, window_start)
                        statuses[key] = status
    return Case(
        spec,
        listed,
        today,
        started,
        cal_first,
        cal_last,
        holidays,
        early,
        times,
        "absent",
        statuses,
    )


def _example(
    start: date,
    end: date,
    *,
    started_ns: Instant,
    default_status: WindowStatus,
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    sample: int = 1,
    label: int | None = None,
    times: SessionTimes | None = None,
    early_closes: Mapping[date, time] | None = None,
) -> Case:
    today = date(2026, 3, 6)
    return Case(
        PullSpec(start, end, symbols, sample_interval_minutes=sample, label_sample_minutes=label),
        {s: date(2023, 3, 28) for s in symbols},
        today,
        started_ns,
        date(2024, 1, 1),
        date(2026, 12, 31),
        frozenset({date(2026, 2, 16)}),
        dict(early_closes or {}),
        times or SessionTimes(),
        default_status,
        {},
    )


# ---------------------------------------------------------------- oracle


class Cell(NamedTuple):
    """One symbol's share of one request."""

    endpoint: ReplayEndpoint
    label_sample: bool
    symbol: str
    metric: Metric
    session: date
    window_start_ns: Instant
    window_end_ns: Instant
    at_ns: Instant | None


@dataclass(frozen=True, slots=True)
class Expected:
    endpoints: dict[date, ReplayEndpoint]
    windows: dict[date, tuple[tuple[Instant, Instant], ...]]
    cells: Counter[Cell]
    served: frozenset[CacheWindowKey]
    fetch: frozenset[CacheWindowKey]
    skipped: frozenset[tuple[str, date]]


def _grid(origin: Instant, step: int, lo: Instant, hi: Instant, end: Instant) -> list[Instant]:
    """``origin + k * step`` (k >= 0) before ``end`` that fall in ``[lo, hi)``."""
    out: list[Instant] = []
    t = origin
    while t < end:
        if lo <= t < hi:
            out.append(t)
        t += step
    return out


def _expected(case: Case) -> Expected:
    spec = case.spec
    view_id = spec.view.view_id()
    sample_step = spec.sample_interval_minutes * NS_PER_MINUTE
    label = spec.label_sample_minutes
    endpoints: dict[date, ReplayEndpoint] = {}
    windows: dict[date, tuple[tuple[Instant, Instant], ...]] = {}
    cells: Counter[Cell] = Counter()
    served: set[CacheWindowKey] = set()
    fetch: set[CacheWindowKey] = set()
    skipped: set[tuple[str, date]] = set()
    cache = case.cache()

    for d in case.sessions():
        pull_start, pull_end = case.pull_window(d)
        recent = (case.today - d).days < RANGE_MAX_AGE_DAYS
        completed = pull_end < case.started_ns
        endpoints[d] = "range" if recent else "historical"
        windows[d] = case.windows(d)
        skipped |= {(s, d) for s in spec.symbols if d < case.listed[s]}
        for ws, we in windows[d]:
            for metric in spec.metrics:
                for symbol in spec.symbols:
                    if d < case.listed[symbol]:
                        continue
                    key = CacheWindowKey(symbol, metric, view_id, d, ws)
                    if completed and cache.status(key) in SERVED_STATUSES:
                        served.add(key)
                        continue
                    fetch.add(key)
                    if recent:
                        cells[Cell("range", False, symbol, metric, d, ws, we, None)] += 1
                        if label is not None:
                            step = label * NS_PER_MINUTE
                            for t in _grid(pull_start, step, ws, we, pull_end):
                                cells[Cell("historical", True, symbol, metric, d, ws, we, t)] += 1
                    else:
                        for t in _grid(pull_start, sample_step, ws, we, pull_end):
                            cells[Cell("historical", False, symbol, metric, d, ws, we, t)] += 1
    return Expected(
        endpoints, windows, cells, frozenset(served), frozenset(fetch), frozenset(skipped)
    )


def _cells(request: ReplayRequest) -> list[Cell]:
    r = request
    return [
        Cell(
            r.endpoint,
            r.label_sample,
            s,
            r.metric,
            r.session,
            r.window_start_ns,
            r.window_end_ns,
            r.at_ns,
        )
        for s in r.symbols
    ]


def _diff(actual: Counter[Cell], expected: Counter[Cell]) -> str:
    extra = list((actual - expected).elements())[:3]
    missing = list((expected - actual).elements())[:3]
    return f"unexpected {extra}; missing {missing}"


def _checked_requests(plan: PullPlan, case: Case, expected: Expected) -> list[ReplayRequest]:
    """Every request, after checking it names its WindowPlan's Cache_Window and nothing more."""
    where = case.describe()
    view_id = case.spec.view.view_id()
    out: list[ReplayRequest] = []
    for session in plan.sessions:
        tiles = tuple((w.start_ns, w.end_ns) for w in session.windows)
        assert tiles == expected.windows[session.session], f"{session.session} tiling: {where}"
        for window in session.windows:
            for r in window.requests:
                assert (r.session, r.window_start_ns, r.window_end_ns) == (
                    window.session,
                    window.start_ns,
                    window.end_ns,
                ), f"{r} is not on its window: {where}"
                assert r.view_id == view_id, f"{r}: {where}"
                cap = RANGE_MAX_SYMBOLS if r.endpoint == "range" else HISTORICAL_MAX_SYMBOLS
                assert 1 <= len(r.symbols) <= cap, f"{r}: {where}"
                assert len(set(r.symbols)) == len(r.symbols), f"{r}: {where}"
                if r.endpoint == "range":
                    assert r.at_ns is None, f"{r}: {where}"
                else:
                    assert r.at_ns is not None, f"{r}: {where}"
                    assert window.start_ns <= r.at_ns < window.end_ns, f"{r}: {where}"
                early = [s for s in r.symbols if r.session < case.listed[s]]
                assert not early, f"{r} targets {early} before first history: {where}"
                out.append(r)
    return out


def _printed_requests(lines: Sequence[str]) -> dict[str, str]:
    """The printed request count of each table row, by row label."""
    rows = [line.split() for line in lines if line.startswith("  ")][1:]  # skip the header
    out: dict[str, str] = {}
    for cells in rows:
        if cells[0] == "total":
            out["total"] = cells[1]
        else:
            out[f"{cells[0]} {cells[1]}"] = cells[2]
    return out


# ---------------------------------------------------------------- property


# Feature: skylit-futures-strategy-engine, Property 7: Pull plan correctness
@given(case=cases())
@example(  # ages 366, 365 and 364 days; 12 symbols; grids off the 15-minute tiling
    case=_example(
        date(2025, 3, 5),
        date(2025, 3, 7),
        started_ns=ny_instant(date(2026, 3, 6), time(17, 0)),
        default_status="absent",
        symbols=SYMBOL_POOL,
        sample=4,
        label=7,
        times=SessionTimes(pull_start=time(9, 5)),
        early_closes={date(2025, 3, 7): time(13, 7)},
    )
)
@example(  # the pull starts at today's Pull_Window end: today is not completed
    case=_example(
        date(2026, 3, 5),
        date(2026, 3, 6),
        started_ns=ny_instant(date(2026, 3, 6), time(16, 0)),
        default_status="complete",
    )
)
@example(  # one nanosecond later: every complete window is served, no request
    case=_example(
        date(2026, 3, 5),
        date(2026, 3, 6),
        started_ns=ny_instant(date(2026, 3, 6), time(16, 0)) + 1,
        default_status="no_data",
    )
)
def test_pull_plan_matches_the_requirements_and_the_estimate_counts_it(case: Case) -> None:
    where = case.describe()
    plan = plan_pull(
        case.spec,
        listed=case.listed,
        started_ns=case.started_ns,
        calendar=case.calendar(),
        cache=case.cache(),
    )
    expected = _expected(case)

    assert plan.today == case.today, where
    endpoints = {s.session: s.endpoint for s in plan.sessions}
    assert endpoints == expected.endpoints, f"session endpoints {endpoints}: {where}"

    # One Cache_Window per request, group caps, first history dates (Req 3.3-3.5).
    requests = _checked_requests(plan, case, expected)

    # Exactly the oracle's requests: range or sample-grid instants, nothing for a
    # served window or a skipped symbol, nothing missing or doubled (Req 3.3-3.5, 3.8).
    cells: Counter[Cell] = Counter(c for r in requests for c in _cells(r))
    assert cells == expected.cells, f"{_diff(cells, expected.cells)}: {where}"
    assert set(plan.served_keys()) == expected.served, where
    assert set(plan.fetch_keys()) == expected.fetch, where
    assert set(plan.skipped()) == expected.skipped, where

    # The estimate counts the planned requests, in total and per symbol and metric (Req 3.1).
    n = len(requests)
    assert plan.request_count == n == sum(1 for _ in plan.requests()), where
    estimate = estimate_pull(plan)
    total = estimate.total
    assert estimate.request_count == n, f"estimate {estimate.request_count}, plan {n}: {where}"
    assert total.range_requests == sum(r.endpoint == "range" for r in requests), where
    assert total.historical_requests == sum(r.endpoint == "historical" for r in requests), where
    printed = _printed_requests(render_estimate(estimate))
    assert printed["total"] == f"{n:,}", f"printed {printed['total']}, plan {n}: {where}"
    for line in estimate.lines:
        assert line.symbol is not None
        assert line.metric is not None
        carried = sum(1 for r in requests if r.metric == line.metric and line.symbol in r.symbols)
        assert line.requests == carried, f"{line.label}: {line.requests} vs {carried}: {where}"
        assert printed[line.label] == f"{carried:,}", f"{line.label} printed: {where}"
