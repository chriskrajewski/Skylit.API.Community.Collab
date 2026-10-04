"""Property 29: Structure features match the reference model.

*For any* bar stream and pivot, retention and sweep parameters, confirmed
swings, breaks of structure, BOS_Leg origins and terminals, Fibonacci levels
(exactly the eight ratios, terminal + r x (origin - terminal)), and active-leg
sets equal a reference implementation; at most 2 BOS_Legs are active per
instrument, timeframe and direction at any time; and a leg is dropped exactly
on a qualifying sweep bar.

The reference model is a batch recomputation over the whole 1-minute bar list,
read straight from Requirement 9 criteria 7-14:

- 5m, 1h and 4h bars group the minutes by period, counted from the 18:00 start
  of each minute's trading day and cut at that trading day's end (Req 9.7);
- bar ``i`` of a timeframe is a swing when its high (low) is strictly above
  (below) those of the ``N`` bars on each side, confirmed by bar ``i + N``; on
  1h and 4h the newest ``swings_kept`` highs and lows are exposed (Req 9.7-9.8);
- on 1m and 5m a close strictly through the newest swing confirmed at an
  earlier bar and not yet broken is a break; the leg origin is the extreme of
  the swing bar through the break bar, the terminal the break bar's high or low
  (Req 9.9-9.11); a third leg in one direction drops the one with the earliest
  break bar (Req 9.13); a later bar that opens and closes strictly inside the
  leg and wicks past level 0 or 1 by the sweep distance drops it (Req 9.14).

On one bar the steps run in the order the ``fse.engine.chart`` docstring
states: sweeps, then breaks (with the 2-leg cap), then swing confirmations.
Every quantity is recomputed from full-history slices, with no running
extremes or stacks.

The production :class:`ChartState` is replayed bar by bar. It is advanced to
each bar's open first, so a step processes at most one bar per timeframe and
every ``BarEvents`` is seen through ``last_bar``. The events per timeframe, and
the swings and BOS_Legs exposed after every step, must equal the reference.

Generators: one MES stream of up to 240 one-minute bars on the 0.25-point tick
grid, as a random walk with gap opens, starting anywhere in a weekday trading
day or in one of the 23-hour and 25-hour DST trading days of 2026. Time gaps
are 0 minutes most of the time, else 1-10 or 11-240 minutes, so the stream
spans days and builds enough 1h and 4h bars for swings. Pivot lengths, swing
retention and sweep ticks are small (1-3) half the time, else anywhere in
their valid range.

**Validates: Requirements 9.7, 9.8, 9.9, 9.11, 9.12, 9.13, 9.14**
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, time, timedelta

from hypothesis import event, given
from hypothesis import strategies as st

from fse.config.schema.chart import ChartConfig
from fse.engine.chart import BarEvents, BosLeg, BreakDirection, ChartState, Swing, SwingKind
from fse.engine.types import Bar, Unavailable
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, Instant, ny_datetime, ny_instant

INSTRUMENT = "MES"
TICK = 0.25
TIMEFRAMES = (60, 300, 3600, 14400)
BOS_TIMEFRAMES = (60, 300)
SWING_TIMEFRAMES = (3600, 14400)
RATIOS = (0.0, 1.0, -2.0, -2.25, -2.5, -3.5, -4.0, -4.5)  # Req 9.12, and no others
MAX_ACTIVE_LEGS = 2  # Req 9.13
DIRECTIONS: tuple[BreakDirection, ...] = ("bullish", "bearish")
SIDES: tuple[tuple[SwingKind, BreakDirection], ...] = (("high", "bullish"), ("low", "bearish"))
DAY_START = time(18, 0)
ONE_DAY = timedelta(days=1)

# Session dates (the trading day runs from 18:00 the day before): weekdays, and
# the 2026 DST Sundays, whose trading days last 23 hours (Mar 8) and 25 hours (Nov 1).
SESSION_DAYS = (
    date(2026, 3, 4),
    date(2026, 3, 8),
    date(2026, 6, 16),
    date(2026, 11, 1),
    date(2026, 12, 10),
)


# ---------------------------------------------------------------- reference model


@dataclass(frozen=True, slots=True)
class RBar:
    """One bar of one timeframe: its period and prices."""

    open_ns: Instant
    close_ns: Instant
    o: float
    h: float
    l: float  # noqa: E741 - matches Bar
    c: float


# Per bar: (bar, swings it confirms, legs it creates, legs it sweeps, legs it caps).
type Events = tuple[
    RBar, tuple[Swing, ...], tuple[BosLeg, ...], tuple[BosLeg, ...], tuple[BosLeg, ...]
]
type Exposed = tuple[tuple[Swing, ...], tuple[BosLeg, ...]]


@dataclass(frozen=True, slots=True)
class RefTimeframe:
    """The reference result for one timeframe.

    ``legs_after[j]`` holds the legs active after bar ``j`` (bullish then
    bearish, oldest first); ``swings`` pairs each confirmed swing with the
    index of the bar that confirmed it.
    """

    timeframe_s: int
    bars: tuple[RBar, ...]
    events: tuple[Events, ...]
    legs_after: tuple[tuple[BosLeg, ...], ...]
    swings: tuple[tuple[int, Swing], ...]


@dataclass(frozen=True, slots=True)
class Reference:
    timeframes: tuple[RefTimeframe, ...]
    swings_kept: int

    def exposed_at(self, t: Instant) -> Exposed:
        """Swings and active legs at ``t``: every bar that closed by ``t`` is processed."""
        swings: list[Swing] = []
        legs: list[BosLeg] = []
        for tf in self.timeframes:
            done = bisect_right(tf.bars, t, key=lambda b: b.close_ns)
            if tf.timeframe_s in SWING_TIMEFRAMES:
                seen = [s for j, s in tf.swings if j < done]
                for kind in ("high", "low"):
                    swings += [s for s in seen if s.kind == kind][-self.swings_kept :]
            elif done:
                legs += tf.legs_after[done - 1]
        return tuple(swings), tuple(legs)


def trading_day(t: Instant) -> tuple[Instant, Instant]:
    """``[(d - 1) 18:00, d 18:00)`` New York time: the trading day containing ``t``."""
    local = ny_datetime(t)
    d = local.date() + ONE_DAY if local.time() >= DAY_START else local.date()
    return ny_instant(d - ONE_DAY, DAY_START), ny_instant(d, DAY_START)


def aggregate(minutes: Sequence[Bar], timeframe_s: int) -> list[RBar]:
    """Bars of ``timeframe_s`` over periods counted from each trading day's start."""
    width = timeframe_s * NS_PER_SECOND
    periods: dict[tuple[Instant, Instant], list[Bar]] = {}
    for b in minutes:
        start, end = trading_day(b.open_ns)
        p_open = start + (b.open_ns - start) // width * width
        periods.setdefault((p_open, min(p_open + width, end)), []).append(b)
    return [
        RBar(p_open, p_close, g[0].o, max(x.h for x in g), min(x.l for x in g), g[-1].c)
        for (p_open, p_close), g in sorted(periods.items())
    ]


def is_swept(leg: BosLeg, bar: RBar, sweep_pts: float) -> bool:
    """Req 9.14: open and close strictly between levels 0 and 1, wick past either by the sweep."""
    zero, one = leg.terminal, leg.origin
    lower, upper = min(zero, one), max(zero, one)
    inside = lower < bar.o < upper and lower < bar.c < upper
    if leg.direction == "bullish":  # level 0 is the upper end
        wick = bar.h >= zero + sweep_pts or bar.l <= one - sweep_pts
    else:  # level 0 is the lower end
        wick = bar.l <= zero - sweep_pts or bar.h >= one + sweep_pts
    return inside and wick


def structure(bars: Sequence[RBar], timeframe_s: int, n: int, sweep_pts: float) -> RefTimeframe:
    highs = [b.h for b in bars]
    lows = [b.l for b in bars]
    confirmed_by: dict[SwingKind, dict[int, int]] = {"high": {}, "low": {}}  # swing bar -> bar
    broken: dict[SwingKind, set[int]] = {"high": set(), "low": set()}
    active: dict[BreakDirection, list[BosLeg]] = {"bullish": [], "bearish": []}
    sides = SIDES if timeframe_s in BOS_TIMEFRAMES else ()
    events: list[Events] = []
    legs_after: list[tuple[BosLeg, ...]] = []
    swings: list[tuple[int, Swing]] = []
    for j, bar in enumerate(bars):
        # (1) Req 9.14: drop every active leg (all from earlier bars) this bar sweeps.
        swept = tuple(leg for d in DIRECTIONS for leg in active[d] if is_swept(leg, bar, sweep_pts))
        for d in DIRECTIONS:
            active[d] = [leg for leg in active[d] if leg not in swept]

        # (2) Req 9.9-9.11, 9.13: a close through the newest unbroken swing confirmed earlier.
        breaks: list[BosLeg] = []
        capped: list[BosLeg] = []
        for kind, direction in sides:
            unbroken = [
                i for i, cj in confirmed_by[kind].items() if cj < j and i not in broken[kind]
            ]
            if not unbroken:
                continue
            s = max(unbroken)
            bullish = kind == "high"
            if not (bar.c > highs[s] if bullish else bar.c < lows[s]):
                continue
            broken[kind].add(s)
            span = range(s, j + 1)  # the broken swing bar through the break bar
            leg = BosLeg(
                instrument=INSTRUMENT,
                timeframe_s=timeframe_s,
                direction=direction,
                swing_price=highs[s] if bullish else lows[s],
                swing_open_ns=bars[s].open_ns,
                break_open_ns=bar.open_ns,
                break_close_ns=bar.close_ns,
                origin=min(lows[k] for k in span) if bullish else max(highs[k] for k in span),
                terminal=bar.h if bullish else bar.l,
            )
            if len(active[direction]) == MAX_ACTIVE_LEGS:
                earliest = min(active[direction], key=lambda x: x.break_open_ns)
                active[direction].remove(earliest)
                capped.append(earliest)
            active[direction].append(leg)
            breaks.append(leg)

        # (3) Req 9.7-9.8: bar j confirms a swing at bar j - n.
        confirmed: list[Swing] = []
        i = j - n
        if i - n >= 0:
            others = [k for k in range(i - n, i + n + 1) if k != i]
            if all(highs[i] > highs[k] for k in others):
                confirmed_by["high"][i] = j
                confirmed.append(
                    Swing(INSTRUMENT, timeframe_s, "high", highs[i], bars[i].open_ns, bar.close_ns)
                )
            if all(lows[i] < lows[k] for k in others):
                confirmed_by["low"][i] = j
                confirmed.append(
                    Swing(INSTRUMENT, timeframe_s, "low", lows[i], bars[i].open_ns, bar.close_ns)
                )
        swings += [(j, sw) for sw in confirmed]
        events.append((bar, tuple(confirmed), tuple(breaks), swept, tuple(capped)))
        legs_after.append((*active["bullish"], *active["bearish"]))
    return RefTimeframe(timeframe_s, tuple(bars), tuple(events), tuple(legs_after), tuple(swings))


def reference(minutes: Sequence[Bar], config: ChartConfig) -> Reference:
    pivots = {
        60: config.bos_pivot_len.one_minute,
        300: config.bos_pivot_len.five_minute,
        3600: config.pivot_len.one_hour,
        14400: config.pivot_len.four_hour,
    }
    sweep_pts = config.sweep_ticks * TICK
    return Reference(
        tuple(structure(aggregate(minutes, tf), tf, pivots[tf], sweep_pts) for tf in TIMEFRAMES),
        config.swings_kept,
    )


# ---------------------------------------------------------------- production replay


def as_events(ev: BarEvents) -> Events:
    b = ev.bar
    return (
        RBar(b.open_ns, b.close_ns, b.o, b.h, b.l, b.c),
        ev.swings,
        ev.breaks,
        ev.swept_legs,
        ev.capped_legs,
    )


def replay(
    minutes: Sequence[Bar], config: ChartConfig
) -> tuple[dict[int, list[Events]], list[tuple[Instant, Exposed]]]:
    """Per-timeframe events, and the swings and legs exposed after every step."""
    events: dict[int, list[Events]] = {tf: [] for tf in TIMEFRAMES}
    exposed: list[tuple[Instant, Exposed]] = []

    def observe(state: ChartState, t: Instant) -> None:
        inst = state.features(t).get(INSTRUMENT)
        if isinstance(inst, Unavailable):
            return
        for tf in TIMEFRAMES:
            last = inst.last_bar(tf)
            if last is not None and (not events[tf] or as_events(last) != events[tf][-1]):
                events[tf].append(as_events(last))
        exposed.append((t, (inst.swings, inst.bos_legs)))

    state = ChartState.initial(config)
    for bar in minutes:
        state = state.advance(bar.open_ns)
        observe(state, bar.open_ns)
        state = state.on_bar(bar)
        observe(state, bar.close_ns)
    end = minutes[-1].close_ns + 5 * 60 * NS_PER_MINUTE  # after every open period has ended
    state = state.advance(end)
    observe(state, end)
    return events, exposed


# ---------------------------------------------------------------- generators


def minute_bar(open_ns: Instant, o: int, h: int, l: int, c: int) -> Bar:  # noqa: E741
    """A 1-minute MES bar from tick prices."""
    return Bar(
        instrument=INSTRUMENT,
        contract="MESH6",
        interval_s=60,
        open_ns=open_ns,
        close_ns=open_ns + NS_PER_MINUTE,
        o=o * TICK,
        h=h * TICK,
        l=l * TICK,
        c=c * TICK,
        v=1.0,
        o_t=o,
        h_t=h,
        l_t=l,
        c_t=c,
        source="atlas",
    )


GAPS = st.one_of(st.just(0), st.just(0), st.just(0), st.integers(1, 10), st.integers(11, 240))
STEPS = st.tuples(
    GAPS, st.integers(-8, 8), st.integers(-6, 6), st.integers(0, 3), st.integers(0, 3)
)


@st.composite
def bar_streams(draw: st.DrawFn) -> list[Bar]:
    day = draw(st.sampled_from(SESSION_DAYS))
    t = ny_instant(day - ONE_DAY, DAY_START) + draw(st.integers(0, 23 * 60)) * NS_PER_MINUTE
    price = 20_000  # ticks: 5000.00 points
    count = draw(st.integers(1, 240))  # drawn first: plain lists stay short
    bars: list[Bar] = []
    for gap, jump, body, up, down in draw(st.lists(STEPS, min_size=count, max_size=count)):
        t += gap * NS_PER_MINUTE
        o = price + (jump if gap else 0)
        c = o + body
        bars.append(minute_bar(t, o, max(o, c) + up, min(o, c) - down, c))
        t += NS_PER_MINUTE
        price = c
    return bars


def small_or_any(hi: int) -> st.SearchStrategy[int]:
    return st.one_of(st.integers(1, 3), st.integers(1, hi))


@st.composite
def chart_configs(draw: st.DrawFn) -> ChartConfig:
    return ChartConfig.model_validate(
        {
            "pivot_len": {"60": draw(small_or_any(20)), "240": draw(small_or_any(20))},
            "swings_kept": draw(small_or_any(50)),
            "bos_pivot_len": {"1": draw(small_or_any(20)), "5": draw(small_or_any(20))},
            "sweep_ticks": draw(small_or_any(20)),
        }
    )


# ---------------------------------------------------------------- the property


@given(minutes=bar_streams(), config=chart_configs())
def test_structure_features_match_the_reference_model(
    minutes: list[Bar], config: ChartConfig
) -> None:
    ref = reference(minutes, config)
    events, exposed = replay(minutes, config)

    for tf in ref.timeframes:
        assert events[tf.timeframe_s] == list(tf.events), f"{tf.timeframe_s} s bar events"

    for t, (swings, legs) in exposed:
        assert (swings, legs) == ref.exposed_at(t), f"swings and BOS_Legs exposed at {t}"
        for tf_s in BOS_TIMEFRAMES:
            for d in DIRECTIONS:
                active = [leg for leg in legs if leg.timeframe_s == tf_s and leg.direction == d]
                assert len(active) <= MAX_ACTIVE_LEGS
        for leg in legs:
            assert sorted(lv.ratio for lv in leg.levels) == sorted(RATIOS)
            for lv in leg.levels:
                assert lv.price == leg.terminal + lv.ratio * (leg.origin - leg.terminal)

    all_events = [e for tf in ref.timeframes for e in tf.events]
    event(f"break: {any(e[2] for e in all_events)}")
    event(f"sweep drop: {any(e[3] for e in all_events)}")
    event(f"cap drop: {any(e[4] for e in all_events)}")
    event(f"4h swing: {any(tf.swings for tf in ref.timeframes if tf.timeframe_s == 14400)}")
