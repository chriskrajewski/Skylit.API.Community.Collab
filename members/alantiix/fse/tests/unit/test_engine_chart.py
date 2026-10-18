"""Unit tests for the Chart_Feature_Builder state machine (``fse.engine.chart``, design §9).

Bars are synthetic 1-minute MES bars on the 0.25-point tick grid. A seeded
brute-force model of Req 9.7-9.14, written straight from the criteria, checks
the incremental swing, break and leg bookkeeping on random streams.

**Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8, 9.9, 9.10,
9.11, 9.12, 9.13, 9.14, 9.15, 9.16, 9.17**
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import date, time, timedelta

import pytest

from fse.config.schema.chart import ChartConfig
from fse.engine.chart import (
    FIB_RATIOS,
    BarEvents,
    BosLeg,
    ChartFeatures,
    ChartLevel,
    ChartState,
    InstrumentFeatures,
    LiquiditySweep,
    Swing,
    WindowRange,
    candle_label,
    fib_levels,
)
from fse.engine.types import Bar, Unavailable
from fse.timekit import NS_PER_MINUTE, Instant, ny_instant

THU = date(2026, 3, 5)
WED = date(2026, 3, 4)
MINUTE = NS_PER_MINUTE


def at(day: date, hh: int, mm: int) -> Instant:
    return ny_instant(day, time(hh, mm))


def mbar(
    open_ns: Instant,
    o: float,
    h: float,
    l: float,  # noqa: E741 - Bar field name
    c: float,
    instrument: str = "MES",
    v: float = 1.0,
) -> Bar:
    return Bar(
        instrument=instrument,
        contract=f"{instrument}H6",
        interval_s=60,
        open_ns=open_ns,
        close_ns=open_ns + MINUTE,
        o=o,
        h=h,
        l=l,
        c=c,
        v=v,
        o_t=round(o * 4),
        h_t=round(h * 4),
        l_t=round(l * 4),
        c_t=round(c * 4),
        source="atlas",
    )


def hl(open_ns: Instant, h: float, l: float) -> Bar:  # noqa: E741
    return mbar(open_ns, l, h, l, h)


def run(bars: Sequence[Bar], config: ChartConfig | None = None) -> ChartState:
    return ChartState.initial(config).on_bars(bars)


def only(features: ChartFeatures) -> InstrumentFeatures:
    [inst] = features.instruments
    return inst


def series(start: Instant, ohlc: Sequence[tuple[float, float, float, float]]) -> list[Bar]:
    return [mbar(start + k * MINUTE, *x) for k, x in enumerate(ohlc)]


def bos_config(n: int = 1, **extra: object) -> ChartConfig:
    return ChartConfig.model_validate({"bos_pivot_len": {"1": n, "5": n}, **extra})


def per_bar_events(
    bars: Sequence[Bar], config: ChartConfig, timeframe_s: int = 60
) -> list[BarEvents | None]:
    state = ChartState.initial(config)
    out: list[BarEvents | None] = []
    for bar in bars:
        state = state.on_bar(bar)
        out.append(only(state.features(bar.close_ns)).last_bar(timeframe_s))
    return out


# ---------------------------------------------------------------- small pieces


def test_candle_labels() -> None:
    assert [candle_label(10, 9), candle_label(10, 11), candle_label(10, 10)] == [
        "red",
        "green",
        "doji",
    ]


def test_fib_levels_are_exactly_the_eight_ratios() -> None:
    levels = fib_levels(origin=9.5, terminal=13.0)
    assert [x.ratio for x in levels] == list(FIB_RATIOS)
    assert [x.price for x in levels] == [13.0, 9.5, 20.0, 20.875, 21.75, 25.25, 27.0, 28.75]


# ---------------------------------------------------------------- session windows

WINDOW_BARS = [
    hl(at(WED, 9, 29), 100, 98),  # before RTH: not in Wednesday's RTH
    hl(at(WED, 10, 0), 105, 101),
    hl(at(WED, 15, 59), 104, 99),
    hl(at(WED, 16, 0), 200, 1),  # closes 16:01: not in RTH
    hl(at(WED, 18, 0), 110, 108),  # overnight
    hl(at(WED, 19, 0), 112, 107),  # overnight, Asia
    hl(at(THU, 1, 59), 111, 106),  # overnight, Asia (closes 02:00)
    hl(at(THU, 2, 0), 113, 109),  # overnight, London
    hl(at(THU, 7, 59), 114, 110),  # overnight, London
    hl(at(THU, 9, 0), 115, 104),  # overnight
]
IB_BARS = [hl(at(THU, 9, 30), 116, 111), hl(at(THU, 9, 59), 117, 112)]
LATE_BAR = hl(at(THU, 10, 0), 118, 113)


def test_windows_are_exposed_from_their_end() -> None:
    state = run(WINDOW_BARS)
    early = only(state.features(at(THU, 9, 29)))
    assert early.session == THU
    assert early.prior_rth == WindowRange(105.0, 99.0)
    assert early.asia == WindowRange(112.0, 106.0)
    assert early.london == WindowRange(114.0, 109.0)
    assert not early.overnight.available
    assert not early.ib30.available
    assert isinstance(early.overnight.high, Unavailable)
    assert "has not ended" in early.overnight.high.reason

    assert only(state.features(at(THU, 9, 30))).overnight == WindowRange(115.0, 104.0)

    opening = only(run([*WINDOW_BARS, IB_BARS[0]]).features(at(THU, 9, 31))).ib30
    assert isinstance(opening.high, Unavailable)
    assert "has not ended" in opening.high.reason
    ib = run([*WINDOW_BARS, *IB_BARS])
    assert only(ib.features(at(THU, 10, 0))).ib30 == WindowRange(117.0, 111.0)

    state = state.on_bars([*IB_BARS, LATE_BAR])
    with pytest.raises(ValueError, match="features at"):
        state.features(at(THU, 10, 0))  # the 10:00 bar is already consumed
    assert only(state.features(at(THU, 16, 0))).ib30 == WindowRange(117.0, 111.0)
    after_close = only(state.features(at(THU, 16, 1)))
    assert not after_close.ib30.available
    assert after_close.overnight == WindowRange(115.0, 104.0)

    # From 18:00 the next session starts: Thursday's RTH is its prior RTH.
    friday = only(state.features(at(THU, 18, 0)))
    assert friday.session == date(2026, 3, 6)
    assert friday.prior_rth == WindowRange(118.0, 111.0)
    for rng in (friday.overnight, friday.asia, friday.london, friday.ib30):
        assert isinstance(rng.high, Unavailable)
        assert "has no bars" in rng.high.reason


def test_empty_windows_and_missing_prior_session_are_unavailable() -> None:
    # Only overnight bars after 02:00: Asia is empty; nothing earlier has RTH bars.
    state = run([hl(at(THU, 3, 0), 101, 100), hl(at(THU, 9, 0), 103, 99)])
    inst = only(state.features(at(THU, 10, 0)))
    assert inst.overnight == WindowRange(103.0, 99.0)
    assert inst.london == WindowRange(101.0, 100.0)
    assert isinstance(inst.asia.low, Unavailable)
    assert isinstance(inst.prior_rth.high, Unavailable)
    assert "no session before 2026-03-05 has RTH bars" in inst.prior_rth.high.reason
    assert not inst.ib30.available  # no IB bars


def test_prior_rth_skips_a_session_without_rth_bars() -> None:
    mon, tue, wed = date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4)
    bars = [
        hl(at(mon, 12, 0), 50, 40),
        hl(at(tue, 3, 0), 90, 80),  # Tuesday: overnight bars only
        hl(at(wed, 3, 0), 70, 60),
    ]
    inst = only(run(bars).features(at(wed, 9, 30)))
    assert inst.prior_rth == WindowRange(50.0, 40.0)


# ---------------------------------------------------------------- aggregation


@pytest.mark.parametrize("day", [THU, date(2026, 3, 9)], ids=["standard", "daylight"])
def test_aggregated_bars_align_to_the_trading_day_start(day: date) -> None:
    eve = day - timedelta(days=1)  # 2026-03-08 is the first day of daylight time
    bars = [hl(at(eve, 18, 0), 10, 9), hl(at(day, 1, 59), 12, 8), hl(at(day, 2, 0), 11, 10)]
    inst = only(run(bars).features(at(day, 2, 1)))
    four = inst.last_bar(14400)
    one = inst.last_bar(3600)
    assert four is not None
    assert one is not None
    assert (four.bar.open_ns, four.bar.close_ns) == (at(eve, 22, 0), at(day, 2, 0))
    assert (four.bar.h, four.bar.l) == (12.0, 8.0)
    assert (one.bar.open_ns, one.bar.close_ns) == (at(day, 1, 0), at(day, 2, 0))


@pytest.mark.parametrize(
    ("day", "last_open_hh", "last_hours"),
    [(date(2026, 3, 8), 15, 3), (date(2026, 11, 1), 17, 1)],
    ids=["23-hour", "25-hour"],
)
def test_the_last_period_of_a_dst_trading_day_ends_at_the_trading_day_end(
    day: date, last_open_hh: int, last_hours: int
) -> None:
    bars = [hl(at(day, 17, 0), 10, 9), hl(at(day, 18, 30), 12, 11), hl(at(day, 19, 30), 14, 13)]
    last = only(run(bars[:1]).features(at(day, 18, 0))).last_bar(14400)
    assert last is not None
    assert (last.bar.open_ns, last.bar.close_ns) == (at(day, last_open_hh, 0), at(day, 18, 0))
    assert last.bar.interval_s == last_hours * 3600
    assert (last.bar.h, last.bar.l) == (10.0, 9.0)
    # The next trading day's bars form its own first 4-hour bar, from 18:00.
    first = only(run(bars).features(at(day, 22, 0))).last_bar(14400)
    assert first is not None
    assert (first.bar.open_ns, first.bar.close_ns) == (at(day, 18, 0), at(day, 22, 0))
    assert (first.bar.h, first.bar.l) == (14.0, 11.0)


def test_five_minute_bar_from_minutes() -> None:
    start = at(THU, 10, 0)
    ohlc = [(10, 11, 9.5, 10.5), (10.5, 12, 10, 11), (11, 11.5, 8, 9), (9, 10, 8.5, 9.75)]
    bars = [mbar(start + k * MINUTE, *x, v=k + 1.0) for k, x in enumerate(ohlc)]
    bars.append(mbar(start + 4 * MINUTE, 9.75, 10.25, 9.25, 10.0, v=5.0))
    state = run(bars[:4])
    assert only(state.features(start + 4 * MINUTE)).last_bar(300) is None
    events = only(run(bars).features(start + 5 * MINUTE)).last_bar(300)
    assert events is not None
    agg = events.bar
    assert (agg.interval_s, agg.open_ns, agg.close_ns) == (300, start, start + 5 * MINUTE)
    assert (agg.o, agg.h, agg.l, agg.c, agg.v) == (10.0, 12.0, 8.0, 10.0, 15.0)
    assert (agg.o_t, agg.h_t, agg.l_t, agg.c_t) == (40, 48, 32, 40)


def test_a_period_with_missing_minutes_is_emitted_once_it_ends() -> None:
    start = at(THU, 10, 0)
    state = run([mbar(start + MINUTE, 10, 11, 9, 10.5), mbar(start + 2 * MINUTE, 10.5, 12, 10, 11)])
    assert only(state.features(start + 4 * MINUTE)).last_bar(300) is None
    events = only(state.features(start + 5 * MINUTE)).last_bar(300)
    assert events is not None
    assert (events.bar.open_ns, events.bar.o, events.bar.h) == (start, 10.0, 12.0)

    advanced = state.advance(start + 5 * MINUTE)
    assert advanced.features(start + 5 * MINUTE) == state.features(start + 5 * MINUTE)
    assert state.advance(start + 4 * MINUTE) is state
    with pytest.raises(ValueError, match="out of order"):
        advanced.on_bar(mbar(start + 4 * MINUTE, 11, 11, 11, 11))  # inside the emitted period
    nxt = mbar(start + 5 * MINUTE, 11, 13, 11, 12)
    assert advanced.on_bar(nxt) == state.on_bar(nxt)


# ---------------------------------------------------------------- 1h swings


def hourly(first_hour: Instant, highs: Sequence[float]) -> list[Bar]:
    """One minute per hour, at :59, so each 1-hour bar has that minute's high and low."""
    return [hl(first_hour + k * 60 * MINUTE + 59 * MINUTE, h, h - 1) for k, h in enumerate(highs)]


HOURLY_HIGHS = [100, 101, 105, 102, 101, 103, 108, 104, 103, 110, 104, 103]


def test_hourly_swings_are_confirmed_after_n_bars_and_kept() -> None:
    cfg = ChartConfig.model_validate({"pivot_len": {"60": 2}, "swings_kept": 2})
    first = at(WED, 18, 0)
    bars = hourly(first, HOURLY_HIGHS)
    hour_close = [first + (k + 1) * 60 * MINUTE for k in range(len(bars))]

    before = only(run(bars[:4], cfg).features(hour_close[3]))
    assert [s for s in before.swings if s.timeframe_s == 3600] == []
    after = only(run(bars[:5], cfg).features(hour_close[4]))
    assert [s for s in after.swings if s.timeframe_s == 3600] == [
        Swing("MES", 3600, "high", 105.0, first + 2 * 60 * MINUTE, hour_close[4]),
    ]
    level = ChartLevel("swing", "high", 105.0, 3600, first + 2 * 60 * MINUTE)
    assert level in after.chart_levels()
    assert level.name == f"1h_swing_high@{first + 2 * 60 * MINUTE}"

    end = only(run(bars, cfg).features(hour_close[-1]))
    highs = [s.price for s in end.swings if s.timeframe_s == 3600 and s.kind == "high"]
    lows = [s.price for s in end.swings if s.timeframe_s == 3600 and s.kind == "low"]
    assert highs == [108.0, 110.0]  # 105 dropped: only the newest 2 are kept
    assert lows == [100.0, 102.0]  # lows at hours 4 and 8 (highs 101 and 103, minus 1)


def test_equal_highs_are_not_swings() -> None:
    cfg = ChartConfig.model_validate({"pivot_len": {"60": 1}})
    bars = hourly(at(WED, 18, 0), [100, 105, 105, 100, 100])
    inst = only(run(bars, cfg).features(bars[-1].close_ns))
    assert [s for s in inst.swings if s.kind == "high"] == []


# ---------------------------------------------------------------- breaks and BOS_Legs

START = at(THU, 10, 0)
BREAK = [
    (10, 10.5, 9, 10),
    (10, 12, 10, 11),  # swing high 12
    (11, 11.5, 9.5, 10),  # confirms it (N = 1)
    (10, 13, 11, 12.5),  # closes above 12: bullish break
    (12.5, 14, 12, 13.5),  # above again, but the swing is already broken
]


def test_bullish_break_creates_a_leg() -> None:
    bars = series(START, BREAK)
    events = per_bar_events(bars, bos_config())
    assert events[2] is not None
    assert [(s.kind, s.price) for s in events[2].swings] == [("high", 12.0)]
    assert events[2].breaks == ()
    third = events[3]
    assert third is not None
    leg = BosLeg(
        instrument="MES",
        timeframe_s=60,
        direction="bullish",
        swing_price=12.0,
        swing_open_ns=bars[1].open_ns,
        break_open_ns=bars[3].open_ns,
        break_close_ns=bars[3].close_ns,
        origin=9.5,
        terminal=13.0,
    )
    assert third.breaks == (leg,)
    assert [x.price for x in leg.levels] == [13.0, 9.5, 20.0, 20.875, 21.75, 25.25, 27.0, 28.75]
    assert events[4] is not None
    assert events[4].breaks == ()
    assert only(run(bars, bos_config()).features(bars[-1].close_ns)).bos_legs == (leg,)


@pytest.mark.parametrize(
    ("sweep_bar", "dropped"),
    [
        ((12, 13.5, 11.5, 12.5), True),  # wick 2 ticks above level 0, open and close inside
        ((12, 13.25, 11.5, 12.5), False),  # only 1 tick
        ((12, 13.5, 11.5, 13.0), False),  # closes at level 0, not strictly inside
        ((10, 12, 9.0, 11), True),  # wick 2 ticks below level 1
    ],
)
def test_a_sweep_bar_drops_the_leg(
    sweep_bar: tuple[float, float, float, float], dropped: bool
) -> None:
    bars = series(START, [*BREAK, sweep_bar])
    last = per_bar_events(bars, bos_config())[-1]
    assert last is not None
    active = only(run(bars, bos_config()).features(bars[-1].close_ns)).bos_legs
    if dropped:
        assert [leg.break_open_ns for leg in last.swept_legs] == [bars[3].open_ns]
        assert active == ()
    else:
        assert last.swept_legs == ()
        assert len(active) == 1


STAIRS = [
    (10, 10, 9, 10),
    (10, 12, 10, 11),
    (11, 11, 10, 10.5),
    (10.5, 13, 10.5, 12.5),  # break 1 of swing 12
    (12.5, 12.75, 11.5, 12),
    (12, 14, 12, 13.5),  # break 2 of swing 13
    (13.5, 13.75, 12.5, 13),
    (13, 15, 13, 14.5),  # break 3 of swing 14: break 1 is dropped
]


def test_at_most_two_legs_per_direction() -> None:
    bars = series(START, STAIRS)
    events = per_bar_events(bars, bos_config())
    breaks = [e.breaks[0] for e in events if e is not None and e.breaks]
    assert [leg.swing_price for leg in breaks] == [12.0, 13.0, 14.0]
    last = events[-1]
    assert last is not None
    assert last.capped_legs == (breaks[0],)
    assert only(run(bars, bos_config()).features(bars[-1].close_ns)).bos_legs == tuple(breaks[1:])


def test_bearish_break_mirrors_the_bullish_one() -> None:
    mirrored = [(-o, -lo, -hi, -c) for o, hi, lo, c in STAIRS]
    bull = only(run(series(START, STAIRS), bos_config()).features(START + 8 * MINUTE)).bos_legs
    bear = only(run(series(START, mirrored), bos_config()).features(START + 8 * MINUTE)).bos_legs
    assert [leg.direction for leg in bear] == ["bearish", "bearish"]
    assert [(-leg.origin, -leg.terminal, -leg.swing_price) for leg in bear] == [
        (leg.origin, leg.terminal, leg.swing_price) for leg in bull
    ]


def test_an_older_unbroken_swing_is_the_next_candidate() -> None:
    bars = series(
        START,
        [
            (10, 10, 9, 10),
            (10, 15, 10, 11),  # swing high 15
            (11, 12, 10, 11),
            (11, 13, 11, 12),  # swing high 13
            (12, 12.5, 11, 12),
            (12, 14, 12, 13.5),  # breaks 13, the newest
            (13.5, 16, 13.5, 15.5),  # breaks 15, now the newest unbroken
        ],
    )
    events = per_bar_events(bars, bos_config())
    assert [(e.breaks[0].swing_price if e and e.breaks else None) for e in events] == [
        None,
        None,
        None,
        None,
        None,
        13.0,
        15.0,
    ]
    last = events[-1]
    assert last is not None
    assert (last.breaks[0].origin, last.breaks[0].terminal) == (10.0, 16.0)


# ---------------------------------------------------------------- reference cross-check


type RefEvents = tuple[
    tuple[Swing, ...], tuple[BosLeg, ...], tuple[BosLeg, ...], tuple[BosLeg, ...]
]


def _is_swept(leg: BosLeg, b: Bar, sweep: float) -> bool:
    lo, hi = sorted((leg.origin, leg.terminal))
    return lo < b.o < hi and lo < b.c < hi and (b.h >= hi + sweep or b.l <= lo - sweep)


def reference(bars: Sequence[Bar], n: int, sweep: float) -> list[RefEvents]:
    """Per bar: (swings confirmed, breaks, legs swept, legs capped), from Req 9.7-9.14.

    Brute force over the whole history: no running extremes, no stacks.
    """
    hs = [b.h for b in bars]
    ls = [b.l for b in bars]
    confirmed_highs: dict[int, int] = {}  # swing bar -> confirming bar
    confirmed_lows: dict[int, int] = {}
    broken_highs: set[int] = set()
    broken_lows: set[int] = set()
    bull: list[BosLeg] = []
    bear: list[BosLeg] = []
    out: list[RefEvents] = []
    for j, b in enumerate(bars):
        swept = [leg for leg in (*bull, *bear) if _is_swept(leg, b, sweep)]
        bull = [leg for leg in bull if leg not in swept]
        bear = [leg for leg in bear if leg not in swept]
        breaks: list[BosLeg] = []
        capped: list[BosLeg] = []

        def make(s: int, bullish: bool, b: Bar = b, j: int = j) -> BosLeg:
            return BosLeg(
                instrument=b.instrument,
                timeframe_s=60,
                direction="bullish" if bullish else "bearish",
                swing_price=hs[s] if bullish else ls[s],
                swing_open_ns=bars[s].open_ns,
                break_open_ns=b.open_ns,
                break_close_ns=b.close_ns,
                origin=min(ls[s : j + 1]) if bullish else max(hs[s : j + 1]),
                terminal=b.h if bullish else b.l,
            )

        highs = [i for i, cj in confirmed_highs.items() if cj < j and i not in broken_highs]
        if highs and b.c > hs[max(highs)]:
            broken_highs.add(max(highs))
            breaks.append(make(max(highs), bullish=True))
            if len(bull) == 2:
                capped.append(bull.pop(0))
            bull.append(breaks[-1])
        lows = [i for i, cj in confirmed_lows.items() if cj < j and i not in broken_lows]
        if lows and b.c < ls[max(lows)]:
            broken_lows.add(max(lows))
            breaks.append(make(max(lows), bullish=False))
            if len(bear) == 2:
                capped.append(bear.pop(0))
            bear.append(breaks[-1])

        swings: list[Swing] = []
        i = j - n
        if i - n >= 0:
            others = [k for k in range(i - n, i + n + 1) if k != i]
            if all(hs[i] > hs[k] for k in others):
                confirmed_highs[i] = j
                swings.append(Swing(b.instrument, 60, "high", hs[i], bars[i].open_ns, b.close_ns))
            if all(ls[i] < ls[k] for k in others):
                confirmed_lows[i] = j
                swings.append(Swing(b.instrument, 60, "low", ls[i], bars[i].open_ns, b.close_ns))
        out.append((tuple(swings), tuple(breaks), tuple(swept), tuple(capped)))
    return out


def random_walk(seed: int, count: int) -> list[Bar]:
    rng = random.Random(seed)
    price = 5000.0
    bars = []
    for k in range(count):
        o = price
        c = o + 0.25 * rng.randint(-6, 6)
        h = max(o, c) + 0.25 * rng.randint(0, 3)
        l = min(o, c) - 0.25 * rng.randint(0, 3)  # noqa: E741
        bars.append(mbar(START + k * MINUTE, o, h, l, c))
        price = c
    return bars


@pytest.mark.parametrize("n", [1, 2, 3])
@pytest.mark.parametrize("seed", [7, 11, 2026])
def test_breaks_and_legs_match_the_reference_model(seed: int, n: int) -> None:
    bars = random_walk(seed, 300)
    cfg = bos_config(n)
    got = [
        (e.swings, e.breaks, e.swept_legs, e.capped_legs) if e is not None else None
        for e in per_bar_events(bars, cfg)
    ]
    want = reference(bars, n, 2 * 0.25)
    assert got == want
    assert sum(len(x[1]) for x in want) > 5  # the stream exercises breaks
    final = only(run(bars, cfg).features(bars[-1].close_ns))
    one_minute_legs = [leg for leg in final.bos_legs if leg.timeframe_s == 60]
    assert all(
        sum(1 for leg in one_minute_legs if leg.direction == d) <= 2 for d in ("bullish", "bearish")
    )


# ---------------------------------------------------------------- candles and sweeps


def test_candle_labels_only_on_the_candle_timeframe() -> None:
    bars = series(
        START,
        [(10, 11, 9, 9.5), (9.5, 10, 9, 10), (10, 10, 10, 10), (10, 11, 9, 11), (11, 11, 9, 9)],
    )
    one = per_bar_events(bars, ChartConfig())
    assert [e.candle if e else None for e in one] == ["red", "green", "doji", "green", "red"]
    cfg = ChartConfig.model_validate({"candle_timeframe_s": 300})
    inst = only(run(bars, cfg).features(bars[-1].close_ns))
    one_minute, five = inst.last_bar(60), inst.last_bar(300)
    assert one_minute is not None
    assert five is not None
    assert (one_minute.candle, five.candle) == (None, "red")  # 5m: open 10, close 9


OVERNIGHT = [hl(at(WED, 18, 0), 110, 100)]


@pytest.mark.parametrize(
    ("bar", "expected"),
    [
        ((109, 110.5, 108, 109.5), [("above", "overnight_high")]),
        ((109, 110.25, 108, 109.5), []),  # 1 tick only
        ((109, 110.5, 108, 110.0), []),  # closes at the level, not below
        ((101, 102, 99.5, 100.5), [("below", "overnight_low")]),
    ],
)
def test_liquidity_sweeps_of_exposed_levels(
    bar: tuple[float, float, float, float], expected: list[tuple[str, str]]
) -> None:
    state = run([*OVERNIGHT, mbar(at(THU, 9, 30), *bar)])
    events = only(state.features(at(THU, 9, 31))).last_bar(60)
    assert events is not None
    assert [(s.side, s.level.name) for s in events.sweeps] == expected


def test_a_level_is_not_swept_before_it_is_exposed() -> None:
    # The 09:29 bar is inside the overnight window, which is exposed only from 09:30.
    state = run([*OVERNIGHT, mbar(at(THU, 9, 29), 109, 120, 108, 109)])
    events = only(state.features(at(THU, 9, 30))).last_bar(60)
    assert events is not None
    assert events.sweeps == ()


def test_sweeps_on_an_aggregated_sweep_timeframe() -> None:
    cfg = ChartConfig.model_validate({"sweep_timeframe_s": 300})
    minutes = [(109, 109.5, 108, 109), (109, 111, 108.5, 109.5), (109.5, 109.75, 109, 109.25)]
    bars = [*OVERNIGHT, *series(at(THU, 9, 30), minutes)]
    bars += series(at(THU, 9, 33), [(109.25, 109.5, 109, 109.25), (109.25, 109.5, 109, 109.5)])
    inst = only(run(bars, cfg).features(at(THU, 9, 35)))
    one_minute, five = inst.last_bar(60), inst.last_bar(300)
    assert one_minute is not None
    assert five is not None
    assert one_minute.sweeps == ()
    assert five.sweeps == (LiquiditySweep("above", ChartLevel("overnight", "high", 110.0)),)


# ---------------------------------------------------------------- state machine rules


def gappy_stream() -> list[Bar]:
    bars = random_walk(5, 240)
    return [b for k, b in enumerate(bars) if k % 7 not in (3, 4)]  # missing minutes


def test_one_at_a_time_equals_batch_and_advance_changes_nothing() -> None:
    bars = gappy_stream()
    cfg = bos_config(2)
    batch = ChartState.initial(cfg).on_bars(bars)
    single = ChartState.initial(cfg)
    stepped = ChartState.initial(cfg)
    for k, bar in enumerate(bars):
        single = single.on_bar(bar)
        stepped = stepped.on_bar(bar)
        nxt = bars[k + 1].open_ns if k + 1 < len(bars) else bar.close_ns + 10 * MINUTE
        stepped = stepped.advance(nxt)
        assert stepped.features(nxt) == single.features(nxt)
    assert single == batch
    assert stepped.on_bar(mbar(bars[-1].close_ns + 10 * MINUTE, 1, 1, 1, 1)) == batch.on_bar(
        mbar(bars[-1].close_ns + 10 * MINUTE, 1, 1, 1, 1)
    )
    assert ChartState.initial(cfg).on_bars(bars) == batch  # deterministic


def test_features_never_answer_for_an_earlier_instant() -> None:
    bars = series(START, BREAK)
    state = run(bars)
    with pytest.raises(ValueError, match="features at"):
        state.features(bars[-1].close_ns - 1)
    assert state.features(bars[-1].close_ns).t == bars[-1].close_ns


def test_bars_must_be_one_minute_and_in_order() -> None:
    state = run(series(START, BREAK))
    with pytest.raises(ValueError, match="out of order"):
        state.on_bar(mbar(START + 2 * MINUTE, 10, 10, 10, 10))
    five = Bar("MES", "MESH6", 300, START, START + 5 * MINUTE, 1, 1, 1, 1, 1, 4, 4, 4, 4, "atlas")
    with pytest.raises(ValueError, match="takes 60 s bars"):
        ChartState.initial().on_bar(five)


def test_instruments_are_kept_apart() -> None:
    es = series(START, BREAK)
    nq = [mbar(b.open_ns, b.o * 4, b.h * 4, b.l * 4, b.c * 4, instrument="MNQ") for b in es]
    mixed = [x for pair in zip(nq, es, strict=True) for x in pair]
    features = run(mixed, bos_config()).features(es[-1].close_ns)
    assert [i.instrument for i in features.instruments] == ["MES", "MNQ"]
    mes, mnq = features.get("MES"), features.get("MNQ")
    assert isinstance(mes, InstrumentFeatures)
    assert isinstance(mnq, InstrumentFeatures)
    assert [leg.origin for leg in mes.bos_legs] == [9.5]
    assert [leg.origin for leg in mnq.bos_legs] == [38.0]
    assert isinstance(features.get("ES"), Unavailable)
    assert run(es, bos_config()).features(es[-1].close_ns).instruments == (mes,)
