"""Property 30: Mirror symmetry of chart features.

*For any* bar stream, negating every price (and swapping each bar's high and
low) turns bullish breaks into bearish breaks, swing highs into swing lows, red
candles into green candles (doji unchanged), and sweeps above into sweeps
below, at the same bars, and each bar gets exactly one candle label.

The mirror of a 1-minute bar is ``o, h, l, c -> -o, -l, -h, -c`` (tick prices
alike); times, volume and contract are unchanged. The original stream and its
mirror are replayed through two :class:`ChartState` instances with the same
config. Each is advanced to every bar's open before the bar is fed, so a step
processes at most one bar per timeframe and every ``BarEvents`` is seen
through ``last_bar``.

For every timeframe the two replays must close bars at the same periods, and
each mirrored bar's events must be the mirror image of the original's:

- the bar itself is the mirrored bar;
- swing highs at ``p`` become swing lows at ``-p`` on the same swing bar and
  confirmation bar, and the reverse (Req 9.7);
- bullish breaks become bearish breaks (Req 9.9-9.10): the BOS_Leg has the same
  swing bar and break bar and negated swing price, origin and terminal; the
  same mirroring holds for legs dropped by a sweep or by the 2-leg cap;
- red becomes green, green becomes red, doji stays doji (Req 9.15);
- a liquidity sweep above a Chart_Level becomes a sweep below the mirrored
  level (window high <-> low, swing high <-> low, negated price), and the
  reverse (Req 9.16-9.17).

After every step the exposed swings, BOS_Legs and Chart_Levels are also mirror
images. On the configured candle timeframe every bar, and only those, carries
exactly one label in ``{red, green, doji}`` matching its open and close, and
those bars are exactly the periods the minutes fall in (Req 9.15).

Generators: as in Property 29, one MES stream of up to 240 one-minute bars on
the 0.25-point tick grid, as a random walk with gap opens, starting anywhere in
a weekday trading day or a 23-hour or 25-hour DST trading day of 2026, with
time gaps of 0, 1-10 or 11-240 minutes. Pivot lengths, swing retention and
sweep ticks are small (1-3) half the time, else anywhere in range; the candle
and sweep timeframes are 1 minute half the time, else any chart timeframe.

**Validates: Requirements 9.10, 9.15, 9.16, 9.17**
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from datetime import date, time, timedelta
from typing import Final

from hypothesis import event, given
from hypothesis import strategies as st

from fse.config.schema.chart import ChartConfig
from fse.engine.chart import (
    BarEvents,
    BosLeg,
    BreakDirection,
    CandleLabel,
    ChartLevel,
    ChartState,
    InstrumentFeatures,
    LiquiditySweep,
    SweepSide,
    Swing,
    SwingKind,
)
from fse.engine.types import Bar, Unavailable
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, Instant, ny_datetime, ny_instant

INSTRUMENT = "MES"
TICK = 0.25
TIMEFRAMES = (60, 300, 3600, 14400)
CANDLES: Final[frozenset[CandleLabel]] = frozenset({"red", "green", "doji"})
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

OPPOSITE_KIND: Final[dict[SwingKind, SwingKind]] = {"high": "low", "low": "high"}
OPPOSITE_DIRECTION: Final[dict[BreakDirection, BreakDirection]] = {
    "bullish": "bearish",
    "bearish": "bullish",
}
OPPOSITE_SWEEP: Final[dict[SweepSide, SweepSide]] = {"above": "below", "below": "above"}
MIRROR_CANDLE: Final[dict[CandleLabel, CandleLabel]] = {
    "red": "green",
    "green": "red",
    "doji": "doji",
}


# ---------------------------------------------------------------- the mirror


def neg_ticks(x: int | None) -> int | None:
    return None if x is None else -x


def mirror_bar(b: Bar) -> Bar:
    """Negate every price and swap the high and the low."""
    return replace(
        b,
        o=-b.o,
        h=-b.l,
        l=-b.h,
        c=-b.c,
        o_t=neg_ticks(b.o_t),
        h_t=neg_ticks(b.l_t),
        l_t=neg_ticks(b.h_t),
        c_t=neg_ticks(b.c_t),
    )


def mirror_swing(s: Swing) -> Swing:
    return replace(s, kind=OPPOSITE_KIND[s.kind], price=-s.price)


def mirror_leg(leg: BosLeg) -> BosLeg:
    return replace(
        leg,
        direction=OPPOSITE_DIRECTION[leg.direction],
        swing_price=-leg.swing_price,
        origin=-leg.origin,
        terminal=-leg.terminal,
    )


def mirror_level(level: ChartLevel) -> ChartLevel:
    return replace(level, side=OPPOSITE_KIND[level.side], price=-level.price)


def mirror_sweep(sweep: LiquiditySweep) -> LiquiditySweep:
    return LiquiditySweep(OPPOSITE_SWEEP[sweep.side], mirror_level(sweep.level))


def mirror_candle(label: CandleLabel | None) -> CandleLabel | None:
    return None if label is None else MIRROR_CANDLE[label]


def assert_mirrored_events(orig: BarEvents, mirr: BarEvents) -> None:
    where = f"{orig.timeframe_s} s bar opening at {orig.bar.open_ns}"
    assert mirr.timeframe_s == orig.timeframe_s, where
    assert mirr.bar == mirror_bar(orig.bar), f"{where}: bar"
    assert mirr.candle == mirror_candle(orig.candle), f"{where}: candle"
    assert Counter(mirr.swings) == Counter(map(mirror_swing, orig.swings)), f"{where}: swings"
    assert Counter(mirr.breaks) == Counter(map(mirror_leg, orig.breaks)), f"{where}: breaks"
    assert Counter(mirr.swept_legs) == Counter(map(mirror_leg, orig.swept_legs)), (
        f"{where}: swept legs"
    )
    assert Counter(mirr.capped_legs) == Counter(map(mirror_leg, orig.capped_legs)), (
        f"{where}: capped legs"
    )
    assert Counter(mirr.sweeps) == Counter(map(mirror_sweep, orig.sweeps)), f"{where}: sweeps"


def assert_mirrored_features(orig: InstrumentFeatures, mirr: InstrumentFeatures) -> None:
    assert mirr.session == orig.session
    assert Counter(mirr.swings) == Counter(map(mirror_swing, orig.swings)), "exposed swings"
    assert Counter(mirr.bos_legs) == Counter(map(mirror_leg, orig.bos_legs)), "exposed BOS_Legs"
    assert Counter(mirr.chart_levels()) == Counter(map(mirror_level, orig.chart_levels())), (
        "exposed Chart_Levels"
    )


# ---------------------------------------------------------------- replay


type Observed = tuple[Instant, InstrumentFeatures | Unavailable]


def replay(
    minutes: Sequence[Bar], config: ChartConfig
) -> tuple[dict[int, list[BarEvents]], list[Observed]]:
    """Every closed bar's events per timeframe, and the features after every step."""
    events: dict[int, list[BarEvents]] = {tf: [] for tf in TIMEFRAMES}
    observed: list[Observed] = []

    def observe(state: ChartState, t: Instant) -> None:
        inst = state.features(t).get(INSTRUMENT)
        observed.append((t, inst))
        if isinstance(inst, Unavailable):
            return
        for tf in TIMEFRAMES:
            last = inst.last_bar(tf)
            seen = events[tf]
            if last is not None and (not seen or last.bar.open_ns != seen[-1].bar.open_ns):
                seen.append(last)

    state = ChartState.initial(config)
    for bar in minutes:
        state = state.advance(bar.open_ns)
        observe(state, bar.open_ns)
        state = state.on_bar(bar)
        observe(state, bar.close_ns)
    end = minutes[-1].close_ns + 5 * 60 * NS_PER_MINUTE  # after every open period has ended
    state = state.advance(end)
    observe(state, end)
    return events, observed


def trading_day(t: Instant) -> tuple[Instant, Instant]:
    """``[(d - 1) 18:00, d 18:00)`` New York time: the trading day containing ``t``."""
    local = ny_datetime(t)
    d = local.date() + ONE_DAY if local.time() >= DAY_START else local.date()
    return ny_instant(d - ONE_DAY, DAY_START), ny_instant(d, DAY_START)


def periods(minutes: Sequence[Bar], timeframe_s: int) -> list[tuple[Instant, Instant]]:
    """The ``timeframe_s`` periods the minutes fall in, counted from each trading day's start."""
    width = timeframe_s * NS_PER_SECOND
    out: set[tuple[Instant, Instant]] = set()
    for b in minutes:
        start, end = trading_day(b.open_ns)
        p_open = start + (b.open_ns - start) // width * width
        out.add((p_open, min(p_open + width, end)))
    return sorted(out)


def expected_candle(bar: Bar) -> CandleLabel:
    """Req 9.15: red when close < open, green when close > open, doji when equal."""
    if bar.c < bar.o:
        return "red"
    if bar.c > bar.o:
        return "green"
    return "doji"


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


TIMEFRAME_CHOICE = st.one_of(st.just(60), st.sampled_from(TIMEFRAMES))


@st.composite
def chart_configs(draw: st.DrawFn) -> ChartConfig:
    return ChartConfig.model_validate(
        {
            "pivot_len": {"60": draw(small_or_any(20)), "240": draw(small_or_any(20))},
            "swings_kept": draw(small_or_any(50)),
            "bos_pivot_len": {"1": draw(small_or_any(20)), "5": draw(small_or_any(20))},
            "sweep_ticks": draw(small_or_any(20)),
            "candle_timeframe_s": draw(TIMEFRAME_CHOICE),
            "sweep_timeframe_s": draw(TIMEFRAME_CHOICE),
        }
    )


# ---------------------------------------------------------------- the property


@given(minutes=bar_streams(), config=chart_configs())
def test_mirrored_stream_mirrors_every_chart_feature(
    minutes: list[Bar], config: ChartConfig
) -> None:
    mirrored = [mirror_bar(b) for b in minutes]
    events, observed = replay(minutes, config)
    m_events, m_observed = replay(mirrored, config)

    # The same bars close on every timeframe, and each one's events are mirror images.
    for tf in TIMEFRAMES:
        orig, mirr = events[tf], m_events[tf]
        assert [(e.bar.open_ns, e.bar.close_ns) for e in mirr] == [
            (e.bar.open_ns, e.bar.close_ns) for e in orig
        ], f"{tf} s bars closed"
        for o_ev, m_ev in zip(orig, mirr, strict=True):
            assert_mirrored_events(o_ev, m_ev)

    # Exposed features after every step are mirror images.
    assert [t for t, _ in m_observed] == [t for t, _ in observed]
    for (_, o_inst), (_, m_inst) in zip(observed, m_observed, strict=True):
        assert isinstance(m_inst, Unavailable) == isinstance(o_inst, Unavailable)
        if isinstance(o_inst, InstrumentFeatures) and isinstance(m_inst, InstrumentFeatures):
            assert_mirrored_features(o_inst, m_inst)

    # Req 9.15: each bar of the candle timeframe gets exactly one label; others get none.
    for stream_events, stream in ((events, minutes), (m_events, mirrored)):
        for tf in TIMEFRAMES:
            if tf == config.candle_timeframe_s:
                assert [(e.bar.open_ns, e.bar.close_ns) for e in stream_events[tf]] == periods(
                    stream, tf
                ), f"every {tf} s bar is labeled once"
                for e in stream_events[tf]:
                    assert e.candle in CANDLES
                    assert e.candle == expected_candle(e.bar)
            else:
                assert all(e.candle is None for e in stream_events[tf])

    all_events = [e for tf in TIMEFRAMES for e in events[tf]]
    breaks = [leg.direction for e in all_events for leg in e.breaks]
    sweeps = [s.side for e in all_events for s in e.sweeps]
    candles = {e.candle for e in all_events if e.candle is not None}
    event(f"bullish break: {'bullish' in breaks}")
    event(f"bearish break: {'bearish' in breaks}")
    event(f"sweep above: {'above' in sweeps}")
    event(f"sweep below: {'below' in sweeps}")
    event(f"red and green candles: {'red' in candles and 'green' in candles}")
    event(f"doji candle: {'doji' in candles}")
