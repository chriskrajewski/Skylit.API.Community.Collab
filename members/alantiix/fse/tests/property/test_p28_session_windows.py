"""Property 28: Session windows.

*For any* bar stream across sessions, the prior RTH, overnight, Asia, London
and IB30 highs and lows equal the extremes of exactly the bars inside each
window, are exposed only at Decision_Times at or after the window end (IB30
from 10:00), and are unavailable, never substituted, when the window has no
bars.

Streams cover 1 to 5 consecutive weekday sessions (often across a weekend and
a daylight-saving transition) for two instruments, MES and MNQ. Each session
gets 0 to 12 one-minute bars per instrument, biased to the minutes around every
window boundary, and some bars are shifted off the minute grid so they straddle
a boundary (a bar opening 09:29:30 belongs to neither overnight nor IB30).
Prices are random on the 0.25-point tick grid, so a value taken from another
window, session or instrument shows up as a mismatch.

Bars are fed to ``ChartState`` in close order, interleaved with queries: before
each query instant ``t``, every bar with ``close_ns <= t`` is consumed. Queries
fall on every window boundary of every session (at the boundary and 1 ns
before it) plus random instants of each trading day.

The oracle works in minutes after the trading-day start ``(d - 1) 18:00``. A
weekday trading day never contains a daylight-saving switch (02:00 Sunday), so
each window bound is that start plus a fixed number of minutes: it does not
reuse the per-bound wall-clock conversion of ``fse.engine.chart``. The 18:00
anchor itself comes from ``fse.timekit.ny_instant``, which Property 16 covers.
For each instrument and query the oracle scans the consumed bars and applies
Req 9.2-9.6 as written:

- overnight, Asia, London: the extremes of the bars opening at or after the
  window start and closing at or before its end, exposed from the end through
  the rest of the session;
- IB30: the same for 09:30 to 10:00, exposed from 10:00 through 16:00;
- prior RTH: the RTH (09:30 to 16:00) extremes of the latest earlier session
  with at least one RTH bar;
- anything else, or a window with no bars: unavailable (both high and low).

The window Chart_Levels must match too. An instrument with no consumed bar is
unavailable as a whole.

**Validates: Requirements 9.2, 9.3, 9.4, 9.5, 9.6**
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time, timedelta
from typing import Final

from hypothesis import given
from hypothesis import strategies as st

from fse.engine.chart import ChartState, InstrumentFeatures, LevelSource
from fse.engine.types import Bar, Unavailable
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, Instant, ny_instant

ONE_DAY: Final = timedelta(days=1)
DAY_MIN: Final = 1440
DAY_NS: Final = DAY_MIN * NS_PER_MINUTE
INSTRUMENTS: Final[tuple[str, ...]] = ("MES", "MNQ")

# Window bounds in minutes after (d - 1) 18:00 (Req 9.2, 9.3, 9.5).
OVERNIGHT: Final = (0, 930)  # (d - 1) 18:00 to d 09:30
ASIA: Final = (60, 480)  # (d - 1) 19:00 to d 02:00
LONDON: Final = (480, 840)  # d 02:00 to d 08:00
IB30: Final = (930, 960)  # d 09:30 to d 10:00
RTH: Final = (930, 1320)  # d 09:30 to d 16:00
SESSION_WINDOWS: Final[tuple[tuple[LevelSource, tuple[int, int]], ...]] = (
    ("overnight", OVERNIGHT),
    ("asia", ASIA),
    ("london", LONDON),
    ("ib30", IB30),
)
BOUNDARIES_MIN: Final[tuple[int, ...]] = (0, 60, 480, 840, 930, 960, 1320, 1440)
NEAR_BOUNDARY_MIN: Final[tuple[int, ...]] = tuple(
    sorted({b + k for b in BOUNDARIES_MIN for k in (-2, -1, 0, 1) if 0 <= b + k < DAY_MIN})
)

type HighLow = tuple[float, float]

# ---------------------------------------------------------------- sessions


def _sunday_on_or_after(d: date) -> date:
    return d + timedelta(days=(6 - d.weekday()) % 7)


TRANSITION_SUNDAYS: Final[tuple[date, ...]] = tuple(
    sorted(
        sunday
        for year in range(2023, 2036)
        for sunday in (
            _sunday_on_or_after(date(year, 3, 8)),  # second Sunday of March
            _sunday_on_or_after(date(year, 11, 1)),  # first Sunday of November
        )
    )
)


def _to_weekday(d: date) -> date:
    """Saturday to the Friday before, Sunday to the Monday after."""
    return d + timedelta(days={5: -1, 6: 1}.get(d.weekday(), 0))


def _weekdays_from(first: date, count: int) -> tuple[date, ...]:
    out: list[date] = []
    d = first
    while len(out) < count:
        if d.weekday() < 5:
            out.append(d)
        d += ONE_DAY
    return tuple(out)


def day_start(d: date) -> Instant:
    """The trading-day start of session ``d``: 18:00 New York on ``d - 1``."""
    return ny_instant(d - ONE_DAY, time(18, 0))


def _shift(d: date, days: int) -> date:
    return d + timedelta(days=days)


FIRST_SESSIONS = st.one_of(
    # Wednesday, Thursday or Friday before, or Monday after, a daylight-saving switch.
    st.builds(_shift, st.sampled_from(TRANSITION_SUNDAYS), st.sampled_from((-4, -3, -2, 1))),
    st.dates(date(2023, 1, 2), date(2035, 12, 28)).map(_to_weekday),
)

# ---------------------------------------------------------------- bars

MINUTES = st.one_of(st.integers(0, DAY_MIN - 1), st.sampled_from(NEAR_BOUNDARY_MIN))
SHIFTS_S = st.sampled_from((0, 0, 0, 1, 30, 59))


@st.composite
def ohlc_ticks(draw: st.DrawFn) -> tuple[int, int, int, int]:
    low = draw(st.integers(16_000, 16_400))
    high = low + draw(st.integers(0, 40))
    return draw(st.integers(low, high)), high, low, draw(st.integers(low, high))


def make_bar(instrument: str, open_ns: Instant, ticks: tuple[int, int, int, int]) -> Bar:
    o_t, h_t, l_t, c_t = ticks
    return Bar(
        instrument=instrument,
        contract=f"{instrument}H6",
        interval_s=60,
        open_ns=open_ns,
        close_ns=open_ns + NS_PER_MINUTE,
        o=o_t / 4,
        h=h_t / 4,
        l=l_t / 4,
        c=c_t / 4,
        v=1.0,
        o_t=o_t,
        h_t=h_t,
        l_t=l_t,
        c_t=c_t,
        source="atlas",
    )


@st.composite
def session_bars(draw: st.DrawFn, instrument: str, start: Instant) -> list[Bar]:
    """Non-overlapping 1-minute bars inside one trading day, sorted by open."""
    minutes = sorted(set(draw(st.lists(MINUTES, max_size=12))))
    bars: list[Bar] = []
    for k, m in enumerate(minutes):
        shift_s = draw(SHIFTS_S)
        # An off-grid bar spills into the next minute: only when that minute is free
        # and the bar still opens and closes inside this trading day.
        next_free = k + 1 == len(minutes) or minutes[k + 1] >= m + 2
        if m >= DAY_MIN - 1 or not next_free:
            shift_s = 0
        open_ns = start + m * NS_PER_MINUTE + shift_s * NS_PER_SECOND
        bars.append(make_bar(instrument, open_ns, draw(ohlc_ticks())))
    return bars


# ---------------------------------------------------------------- case


@dataclass(frozen=True, slots=True)
class Case:
    """Sessions, every instrument's bars (sorted by close) and the query instants."""

    sessions: tuple[date, ...]
    bars: tuple[Bar, ...]
    queries: tuple[tuple[date, Instant], ...]  # (session, instant), sorted by instant


@st.composite
def cases(draw: st.DrawFn) -> Case:
    sessions = _weekdays_from(draw(FIRST_SESSIONS), draw(st.integers(1, 5)))
    bars: list[Bar] = []
    queries: set[tuple[date, Instant]] = set()
    for d in sessions:
        start = day_start(d)
        # No daylight-saving switch inside a weekday trading day: window bounds are
        # the start plus fixed minutes.
        assert day_start(d + ONE_DAY) - start == DAY_NS
        for name in INSTRUMENTS:
            bars.extend(draw(session_bars(name, start)))
        for b in BOUNDARIES_MIN:
            for nudge in (-1, 0):
                q = start + b * NS_PER_MINUTE + nudge
                if start <= q < start + DAY_NS:
                    queries.add((d, q))
    extra = draw(
        st.lists(
            st.tuples(st.sampled_from(sessions), st.integers(0, DAY_NS - 1)),
            max_size=10,
        )
    )
    queries.update((d, day_start(d) + offset) for d, offset in extra)
    return Case(
        sessions=sessions,
        bars=tuple(sorted(bars, key=lambda b: (b.close_ns, b.instrument))),
        queries=tuple(sorted(queries, key=lambda q: q[1])),
    )


# ---------------------------------------------------------------- oracle


def extremes(bars: list[Bar], start: Instant, window: tuple[int, int]) -> HighLow | None:
    """High and low of the bars opening at or after the window start and closing by its end."""
    lo_ns = start + window[0] * NS_PER_MINUTE
    hi_ns = start + window[1] * NS_PER_MINUTE
    inside = [b for b in bars if b.open_ns >= lo_ns and b.close_ns <= hi_ns]
    if not inside:
        return None
    return max(b.h for b in inside), min(b.l for b in inside)


def expected_windows(
    sessions: tuple[date, ...], consumed: list[Bar], d: date, t: Instant
) -> dict[LevelSource, HighLow | None]:
    """Each window of session ``d`` at ``t``: ``(high, low)`` if exposed, else ``None``."""
    start = day_start(d)
    prior: HighLow | None = None
    for earlier in sorted((s for s in sessions if s < d), reverse=True):
        prior = extremes(consumed, day_start(earlier), RTH)
        if prior is not None:
            break
    out: dict[LevelSource, HighLow | None] = {"prior_rth": prior}
    for name, window in SESSION_WINDOWS:
        exposed = t >= start + window[1] * NS_PER_MINUTE
        if name == "ib30":
            exposed = exposed and t <= start + RTH[1] * NS_PER_MINUTE
        out[name] = extremes(consumed, start, window) if exposed else None
    return out


# ---------------------------------------------------------------- property


def check_instrument(
    case: Case, got: InstrumentFeatures, consumed: list[Bar], d: date, t: Instant
) -> None:
    want = expected_windows(case.sessions, consumed, d, t)
    where = f"{got.instrument} session {d} at {t} ns"
    assert got.session == d, where
    for source, rng in got.windows():
        expected = want[source]
        if expected is None:
            assert isinstance(rng.high, Unavailable), f"{source} high: {where}"
            assert isinstance(rng.low, Unavailable), f"{source} low: {where}"
            assert not rng.available
        else:
            assert (rng.high, rng.low) == expected, f"{source}: {where}"
    levels = [(lv.source, lv.side, lv.price) for lv in got.chart_levels() if lv.source != "swing"]
    want_levels: list[tuple[LevelSource, str, float]] = []
    for source, _ in got.windows():
        hl = want[source]
        if hl is not None:
            want_levels += [(source, "high", hl[0]), (source, "low", hl[1])]
    assert levels == want_levels, where


# Feature: skylit-futures-strategy-engine, Property 28: Session windows
@given(case=cases())
def test_session_windows(case: Case) -> None:
    state = ChartState.initial()
    consumed: dict[str, list[Bar]] = {name: [] for name in INSTRUMENTS}
    k = 0
    for d, t in case.queries:
        while k < len(case.bars) and case.bars[k].close_ns <= t:
            bar = case.bars[k]
            state = state.on_bar(bar)
            consumed[bar.instrument].append(bar)
            k += 1
        features = state.features(t)
        for name in INSTRUMENTS:
            got = features.get(name)
            if not consumed[name]:
                assert isinstance(got, Unavailable), f"{name} at {t} ns"
                continue
            assert isinstance(got, InstrumentFeatures)
            check_instrument(case, got, consumed[name], d, t)
