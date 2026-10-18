"""Chart_Feature_Builder: the incremental chart state machine (design §9, Req 9).

:class:`ChartState` consumes closed 1-minute bars in time order, one at a time,
and answers :meth:`ChartState.features` at a Decision_Time. Feeding bars one at
a time or as a batch (:meth:`ChartState.on_bars`) gives equal states: every
state is a frozen value that depends only on the bars fed, in order, and the
:class:`~fse.config.schema.chart.ChartConfig`. The same code runs in live and
backtest. Prices are futures points (``Bar.o/h/l/c``); one tick is
:data:`TICK_SIZE_PTS` for every traded instrument (MES, MNQ, ES, NQ).

**Sessions.** An instant belongs to session ``d`` when it falls in that
session's trading day ``[(d - 1) 18:00, d 18:00)`` New York time. Session
windows (Req 9.2-9.6) hold the extremes of the 1-minute bars that open at or
after the window start and close at or before its end:

- overnight ``(d - 1) 18:00`` to ``d 09:30``; Asia ``(d - 1) 19:00`` to
  ``d 02:00``; London ``d 02:00`` to ``d 08:00``: exposed from the window end
  through the rest of session ``d``.
- IB30 ``d 09:30`` to ``d 10:00``: exposed from 10:00 through 16:00.
- prior RTH: the bars opening at or after 09:30 and closing at or before 16:00
  on the latest earlier date that has any, exposed through session ``d``.

A window with no bars is ``Unavailable``; no other session or window is used.

**Aggregation.** 5-minute, 1-hour and 4-hour bars are built from the 1-minute
bars, in periods counted from the trading-day start (18:00). The last period of
a trading day ends at the trading-day end, so on the 23-hour and 25-hour DST
trading days the last 4-hour bar spans 3 hours and 1 hour. A period's bar is
emitted when its last minute closes, or when a later bar or
:meth:`ChartState.advance` shows the period has ended.

**Swings** (Req 9.7-9.8). Bar ``i`` of a timeframe is a swing high when its
high is strictly above the high of each of the ``N`` bars before and after it
(a swing low mirrors this); it is confirmed when bar ``i + N`` closes. On 1h
and 4h the newest ``swings_kept`` confirmed highs and lows are exposed as
resistance and support Chart_Levels.

**Breaks of structure** (Req 9.9-9.11), on 1m and 5m with the BOS pivot length.
The candidate for a bar is the most recent swing high among those confirmed
at the close of an earlier bar and not yet broken; a close strictly above it
marks a bullish break, marks that swing broken and creates a :class:`BosLeg`.
Older unbroken swings stay candidates, so after the newest is broken the next
older one is the candidate for later bars. At most one bullish and one bearish
break are marked per bar. Bearish breaks mirror this with swing lows.

**Order on one bar of a timeframe:** (1) existing BOS_Legs swept by the bar are
dropped (Req 9.14); (2) a break creates a leg, first dropping the leg with the
earliest break bar when 2 are already active in that direction (Req 9.13);
(3) swings confirmed by the bar are recorded, so a bar never breaks the swing
it confirms; (4) the candle label (Req 9.15) and liquidity sweeps
(Req 9.16-9.17) are recorded on the configured timeframes.

**Liquidity sweeps** test every Chart_Level exposed at the sweep bar's open
(for an aggregated bar, its period's open), as computed from the bars closed
by then. A level the bar itself helps form is therefore never tested against
that bar.

**Point in time** (Req 9.1). ``features(t)`` uses only bars with
``close_ns <= t``: it raises when ``t`` is before a bar already consumed, so a
state can never answer for an earlier instant.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, time, timedelta
from typing import Final, Literal

from fse.config.schema.chart import (
    BOS_TIMEFRAMES_S,
    CHART_TIMEFRAMES_S,
    SWING_TIMEFRAMES_S,
    ChartConfig,
)
from fse.engine.types import Bar, BarSourceName, Unavailable
from fse.timekit import NS_PER_SECOND, TRADING_DAY_START, Instant, ny_datetime, ny_instant

__all__ = [
    "BASE_TIMEFRAME_S",
    "FIB_RATIOS",
    "MAX_ACTIVE_LEGS",
    "TICK_SIZE_PTS",
    "TIMEFRAME_LABELS",
    "BarEvents",
    "BosLeg",
    "BreakDirection",
    "CandleLabel",
    "ChartFeatures",
    "ChartLevel",
    "ChartState",
    "FibLevel",
    "InstrumentFeatures",
    "LevelSource",
    "LiquiditySweep",
    "SweepSide",
    "Swing",
    "SwingKind",
    "WindowRange",
    "candle_label",
    "fib_levels",
]

type SwingKind = Literal["high", "low"]
type BreakDirection = Literal["bullish", "bearish"]
type CandleLabel = Literal["red", "green", "doji"]
type SweepSide = Literal["above", "below"]
type LevelSource = Literal["prior_rth", "overnight", "asia", "london", "ib30", "swing"]

BASE_TIMEFRAME_S: Final = 60
"""``ChartState.on_bar`` takes 1-minute bars; the other timeframes are built from them."""

FIB_RATIOS: Final[tuple[float, ...]] = (0.0, 1.0, -2.0, -2.25, -2.5, -3.5, -4.0, -4.5)
"""The standard-deviation Fibonacci ratios of a BOS_Leg, and no others (Req 9.12)."""

MAX_ACTIVE_LEGS: Final = 2
"""Active BOS_Legs per instrument, timeframe and direction (Req 9.13)."""

TICK_SIZE_PTS: Final = 0.25
"""One tick of MES, MNQ, ES and NQ, in points."""

TIMEFRAME_LABELS: Final[dict[int, str]] = {60: "1m", 300: "5m", 3600: "1h", 14400: "4h"}

_AGG_TIMEFRAMES: Final[tuple[int, ...]] = CHART_TIMEFRAMES_S[1:]
_INF: Final = math.inf
_ONE_DAY: Final = timedelta(days=1)

# Session windows: (name, start day offset, start, end day offset, end). Index 4 is
# the session's own RTH, which becomes the next session's prior RTH.
_WINDOW_SPECS: Final[tuple[tuple[LevelSource, int, time, int, time], ...]] = (
    ("overnight", -1, time(18, 0), 0, time(9, 30)),
    ("asia", -1, time(19, 0), 0, time(2, 0)),
    ("london", 0, time(2, 0), 0, time(8, 0)),
    ("ib30", 0, time(9, 30), 0, time(10, 0)),
    ("prior_rth", 0, time(9, 30), 0, time(16, 0)),
)
_RTH: Final = 4
_IB30: Final = 3
_WINDOW_NAMES: Final[tuple[LevelSource, ...]] = ("prior_rth", "overnight", "asia", "london", "ib30")


# ---------------------------------------------------------------- public values


def candle_label(open_: float, close: float) -> CandleLabel:
    """Red when close < open, green when close > open, doji when equal (Req 9.15)."""
    if close < open_:
        return "red"
    if close > open_:
        return "green"
    return "doji"


@dataclass(frozen=True, slots=True)
class FibLevel:
    """One standard-deviation Fibonacci level of a BOS_Leg, in points."""

    ratio: float
    price: float


def fib_levels(origin: float, terminal: float) -> tuple[FibLevel, ...]:
    """``terminal + r x (origin - terminal)`` for each of :data:`FIB_RATIOS` (Req 9.12)."""
    span = origin - terminal
    return tuple(FibLevel(r, terminal + r * span) for r in FIB_RATIOS)


@dataclass(frozen=True, slots=True)
class Swing:
    """A confirmed swing; ``bar_open_ns`` is the swing bar, ``confirmed_ns`` the close
    of the ``N``-th bar after it. A high is resistance and a low support (Req 9.8)."""

    instrument: str
    timeframe_s: int
    kind: SwingKind
    price: float
    bar_open_ns: Instant
    confirmed_ns: Instant


@dataclass(frozen=True, slots=True)
class BosLeg:
    """The range of one break of structure (Req 9.11-9.12).

    ``origin`` is Fibonacci level 1: the lowest low (bullish) or highest high
    (bearish) of the bars from the broken swing bar through the break bar.
    ``terminal`` is level 0: the break bar's high (bullish) or low (bearish).
    The levels are exposed from ``break_close_ns``.
    """

    instrument: str
    timeframe_s: int
    direction: BreakDirection
    swing_price: float
    swing_open_ns: Instant
    break_open_ns: Instant
    break_close_ns: Instant
    origin: float
    terminal: float

    @property
    def levels(self) -> tuple[FibLevel, ...]:
        """The eight Fibonacci levels, in :data:`FIB_RATIOS` order."""
        return fib_levels(self.origin, self.terminal)


@dataclass(frozen=True, slots=True)
class ChartLevel:
    """One Chart_Level (Glossary): a session-window extreme or a 1h or 4h swing.

    ``side`` is ``high`` for a window high or swing resistance and ``low`` for
    a window low or swing support. ``timeframe_s`` and ``swing_open_ns`` are
    set for swings only.
    """

    source: LevelSource
    side: SwingKind
    price: float
    timeframe_s: int | None = None
    swing_open_ns: Instant | None = None

    @property
    def name(self) -> str:
        """``overnight_high``, ``prior_rth_low``, or ``1h_swing_high@<bar open ns>``."""
        if self.source != "swing":
            return f"{self.source}_{self.side}"
        label = TIMEFRAME_LABELS.get(self.timeframe_s or 0, f"{self.timeframe_s}s")
        return f"{label}_swing_{self.side}@{self.swing_open_ns}"


@dataclass(frozen=True, slots=True)
class LiquiditySweep:
    """A wick through ``level`` that closed back on its starting side (Req 9.16-9.17)."""

    side: SweepSide
    level: ChartLevel


@dataclass(frozen=True, slots=True)
class BarEvents:
    """What was marked on one closed bar of one timeframe.

    - ``candle``: set only on the configured candle timeframe.
    - ``swings``: swings this bar confirmed (every timeframe).
    - ``breaks``: BOS_Legs created on this bar (1m and 5m).
    - ``swept_legs`` / ``capped_legs``: BOS_Legs dropped on this bar by a sweep
      (Req 9.14) or by the 2-leg cap (Req 9.13).
    - ``sweeps``: set only on the configured sweep timeframe.
    """

    timeframe_s: int
    bar: Bar
    candle: CandleLabel | None
    swings: tuple[Swing, ...]
    breaks: tuple[BosLeg, ...]
    swept_legs: tuple[BosLeg, ...]
    capped_legs: tuple[BosLeg, ...]
    sweeps: tuple[LiquiditySweep, ...]


@dataclass(frozen=True, slots=True)
class WindowRange:
    """A session window's high and low, both available or both ``Unavailable``."""

    high: float | Unavailable
    low: float | Unavailable

    @property
    def available(self) -> bool:
        return not isinstance(self.high, Unavailable)


@dataclass(frozen=True, slots=True)
class InstrumentFeatures:
    """Chart features of one instrument at a Decision_Time ``t`` of ``session``.

    - ``swings``: the kept 1h then 4h swings; highs then lows; oldest first.
    - ``bos_legs``: active legs, 1m then 5m; bullish then bearish; oldest first.
    - ``last_bars``: the latest closed bar of each timeframe that has one.
    """

    instrument: str
    session: date
    prior_rth: WindowRange
    overnight: WindowRange
    asia: WindowRange
    london: WindowRange
    ib30: WindowRange
    swings: tuple[Swing, ...]
    bos_legs: tuple[BosLeg, ...]
    last_bars: tuple[BarEvents, ...]

    def windows(self) -> tuple[tuple[LevelSource, WindowRange], ...]:
        """The five session windows in Chart_Level order."""
        return (
            ("prior_rth", self.prior_rth),
            ("overnight", self.overnight),
            ("asia", self.asia),
            ("london", self.london),
            ("ib30", self.ib30),
        )

    def chart_levels(self) -> tuple[ChartLevel, ...]:
        """Every exposed Chart_Level: window highs and lows, then 1h and 4h swings."""
        out: list[ChartLevel] = []
        for source, rng in self.windows():
            if isinstance(rng.high, float) and isinstance(rng.low, float):
                out.append(ChartLevel(source, "high", rng.high))
                out.append(ChartLevel(source, "low", rng.low))
        out.extend(_swing_level(s) for s in self.swings)
        return tuple(out)

    def last_bar(self, timeframe_s: int) -> BarEvents | None:
        """The latest closed bar of ``timeframe_s``, or ``None`` before the first."""
        for events in self.last_bars:
            if events.timeframe_s == timeframe_s:
                return events
        return None


@dataclass(frozen=True, slots=True)
class ChartFeatures:
    """Chart features at Decision_Time ``t``, one entry per instrument seen, by name."""

    t: Instant
    instruments: tuple[InstrumentFeatures, ...]

    def get(self, instrument: str) -> InstrumentFeatures | Unavailable:
        for features in self.instruments:
            if features.instrument == instrument:
                return features
        return Unavailable(f"no {instrument} bar closed at or before the Decision_Time")


# ---------------------------------------------------------------- internal state

# A Chart_Level before it is built: (price, source, side, timeframe_s, swing_open_ns).
type _LevelSpec = tuple[float, LevelSource, SwingKind, int | None, Instant | None]
type _HighLow = tuple[float, float]


@dataclass(frozen=True, slots=True)
class _WBar:
    """A bar in a timeframe's pivot window; ``idx`` counts bars of that timeframe."""

    idx: int
    open_ns: Instant
    h: float
    l: float  # noqa: E741 - matches Bar


def _hi(b: _WBar, sign: int) -> float:
    """The high in signed space: the low, negated, for the bearish (mirrored) side."""
    return b.h if sign > 0 else -b.l


def _lo(b: _WBar, sign: int) -> float:
    return b.l if sign > 0 else -b.h


@dataclass(frozen=True, slots=True)
class _Unbroken:
    """A confirmed swing that has not produced a break, in signed space.

    ``seg`` is the signed-space low over the swing's bars up to the next newer
    unbroken swing's bar (exclusive). It is not used for the newest entry,
    whose range is ``_Side.tail`` plus the pivot window.
    """

    price: float
    idx: int
    open_ns: Instant
    seg: float


@dataclass(frozen=True, slots=True)
class _Side:
    """One break direction of a BOS timeframe; bearish runs on negated prices.

    ``tail`` covers at least the bars from the newest unbroken swing's bar up
    to the pivot window, and no bar after the current one, so the BOS_Leg
    origin is ``min(tail, window lows from the swing bar on)``.
    """

    stack: tuple[_Unbroken, ...]
    tail: float
    legs: tuple[BosLeg, ...]


_EMPTY_SIDE: Final = _Side((), _INF, ())


@dataclass(frozen=True, slots=True)
class _Tf:
    """The state of one timeframe of one instrument."""

    timeframe_s: int
    pivot: int
    count: int
    window: tuple[_WBar, ...]
    highs: tuple[Swing, ...]  # kept swings (1h and 4h)
    lows: tuple[Swing, ...]
    bull: _Side  # unbroken swings and legs (1m and 5m)
    bear: _Side
    last: BarEvents | None


@dataclass(frozen=True, slots=True)
class _Pending:
    """An aggregated bar still collecting minutes; ``levels`` are for sweep checks."""

    open_ns: Instant
    close_ns: Instant
    contract: str
    source: BarSourceName
    o: float
    h: float
    l: float  # noqa: E741 - matches Bar
    c: float
    v: float
    ticks: tuple[int, int, int, int] | None
    levels: tuple[_LevelSpec, ...]

    def merge(self, bar: Bar) -> _Pending:
        ticks = self.ticks
        if ticks is not None:
            if bar.h_t is None or bar.l_t is None or bar.c_t is None:
                ticks = None
            else:
                ticks = (ticks[0], max(ticks[1], bar.h_t), min(ticks[2], bar.l_t), bar.c_t)
        return _Pending(
            self.open_ns,
            self.close_ns,
            self.contract,
            self.source,
            self.o,
            max(self.h, bar.h),
            min(self.l, bar.l),
            bar.c,
            self.v + bar.v,
            ticks,
            self.levels,
        )

    def to_bar(self, instrument: str) -> Bar:
        o_t, h_t, l_t, c_t = self.ticks if self.ticks is not None else (None, None, None, None)
        return Bar(
            instrument=instrument,
            contract=self.contract,
            interval_s=(self.close_ns - self.open_ns) // NS_PER_SECOND,
            open_ns=self.open_ns,
            close_ns=self.close_ns,
            o=self.o,
            h=self.h,
            l=self.l,
            c=self.c,
            v=self.v,
            o_t=o_t,
            h_t=h_t,
            l_t=l_t,
            c_t=c_t,
            source=self.source,
        )


def _start_pending(
    bar: Bar, open_ns: Instant, close_ns: Instant, levels: tuple[_LevelSpec, ...]
) -> _Pending:
    ticks = None
    if bar.o_t is not None and bar.h_t is not None and bar.l_t is not None and bar.c_t is not None:
        ticks = (bar.o_t, bar.h_t, bar.l_t, bar.c_t)
    return _Pending(
        open_ns,
        close_ns,
        bar.contract,
        bar.source,
        bar.o,
        bar.h,
        bar.l,
        bar.c,
        bar.v,
        ticks,
        levels,
    )


@dataclass(frozen=True, slots=True)
class _Session:
    """The session of the latest bar: its trading day and running window extremes."""

    day: date
    start_ns: Instant  # (day - 1) 18:00
    end_ns: Instant  # day 18:00
    bounds: tuple[tuple[Instant, Instant], ...] | None  # per _WINDOW_SPECS
    ranges: tuple[_HighLow | None, ...]  # per _WINDOW_SPECS


@dataclass(frozen=True, slots=True)
class _Instrument:
    """The chart state of one instrument.

    ``horizon_ns``: the next 1-minute bar must open at or after it (the latest
    bar close, or the end of an aggregated period already emitted).
    ``prior_rth``: RTH extremes of the latest date before ``session`` with RTH bars.
    """

    instrument: str
    horizon_ns: Instant
    session: _Session
    prior_rth: _HighLow | None
    tfs: tuple[_Tf, ...]  # per CHART_TIMEFRAMES_S
    pending: tuple[_Pending | None, ...]  # per _AGG_TIMEFRAMES


# ---------------------------------------------------------------- sessions


def _session_day(t: Instant) -> date:
    local = ny_datetime(t)
    day = local.date()
    return day + _ONE_DAY if local.time() >= TRADING_DAY_START else day


def _new_session(t: Instant) -> _Session:
    day = _session_day(t)
    try:
        bounds: tuple[tuple[Instant, Instant], ...] | None = tuple(
            (ny_instant(day + s_off * _ONE_DAY, s_wall), ny_instant(day + e_off * _ONE_DAY, e_wall))
            for _, s_off, s_wall, e_off, e_wall in _WINDOW_SPECS
        )
    except ValueError:
        # A window bound inside a DST gap or overlap (only possible for a weekend "session"):
        # its windows stay empty, so they are reported unavailable.
        bounds = None
    return _Session(
        day,
        ny_instant(day - _ONE_DAY, TRADING_DAY_START),
        ny_instant(day, TRADING_DAY_START),
        bounds,
        (None,) * len(_WINDOW_SPECS),
    )


def _add_to_windows(session: _Session, bar: Bar) -> _Session:
    if session.bounds is None:
        return session
    ranges = list(session.ranges)
    changed = False
    for i, (start, end) in enumerate(session.bounds):
        if bar.open_ns >= start and bar.close_ns <= end:
            r = ranges[i]
            ranges[i] = (bar.h, bar.l) if r is None else (max(r[0], bar.h), min(r[1], bar.l))
            changed = True
    if not changed:
        return session
    return _Session(session.day, session.start_ns, session.end_ns, session.bounds, tuple(ranges))


def _windows_at(inst: _Instrument, t: Instant) -> tuple[date, tuple[_HighLow | str, ...]]:
    """The session of ``t`` and its five windows in ``_WINDOW_NAMES`` order.

    Each window is ``(high, low)`` when exposed at ``t``, else the reason it is not.
    """
    s = inst.session
    day = s.day if s.start_ns <= t < s.end_ns else _session_day(t)
    if day < s.day:
        raise ValueError(f"instant {t} is before the session of bars already consumed")
    if day > s.day:
        # No bar of this session yet: its own windows are empty.
        prior = s.ranges[_RTH] if s.ranges[_RTH] is not None else inst.prior_rth
        empty = tuple(f"{name} window has no bars for session {day}" for name in _WINDOW_NAMES[1:])
        return day, (prior if prior is not None else _no_prior(day), *empty)
    out: list[_HighLow | str] = [inst.prior_rth if inst.prior_rth is not None else _no_prior(day)]
    for i in range(4):  # overnight, asia, london, ib30
        name = _WINDOW_SPECS[i][0]
        r = s.ranges[i]
        if r is None or s.bounds is None:
            out.append(f"{name} window has no bars for session {day}")
            continue
        end = s.bounds[i][1]
        if t < end:
            out.append(f"{name} window of session {day} has not ended")
        elif i == _IB30 and t > s.bounds[_RTH][1]:
            out.append(f"ib30 of session {day} is exposed only through 16:00")
        else:
            out.append(r)
    return day, tuple(out)


def _no_prior(day: date) -> str:
    return f"no session before {day} has RTH bars"


def _swing_level(s: Swing) -> ChartLevel:
    return ChartLevel("swing", s.kind, s.price, s.timeframe_s, s.bar_open_ns)


def _level_specs(inst: _Instrument, t: Instant) -> tuple[_LevelSpec, ...]:
    """Every Chart_Level exposed at ``t``, unbuilt, in ``chart_levels`` order."""
    _, windows = _windows_at(inst, t)
    out: list[_LevelSpec] = []
    for name, w in zip(_WINDOW_NAMES, windows, strict=True):
        if not isinstance(w, str):
            out.append((w[0], name, "high", None, None))
            out.append((w[1], name, "low", None, None))
    for tf in inst.tfs:
        if tf.timeframe_s in SWING_TIMEFRAMES_S:
            for s in (*tf.highs, *tf.lows):
                out.append((s.price, "swing", s.kind, s.timeframe_s, s.bar_open_ns))
    return tuple(out)


# ---------------------------------------------------------------- one timeframe bar


def _drop_swept(side: _Side, bar: Bar, sweep_pts: float) -> tuple[_Side, tuple[BosLeg, ...]]:
    """Drop legs the bar sweeps: open and close strictly inside (0, 1), wick through."""
    if not side.legs:
        return side, ()
    kept: list[BosLeg] = []
    swept: list[BosLeg] = []
    for leg in side.legs:
        lo, hi = min(leg.origin, leg.terminal), max(leg.origin, leg.terminal)
        if (
            lo < bar.o < hi
            and lo < bar.c < hi
            and (bar.h >= hi + sweep_pts or bar.l <= lo - sweep_pts)
        ):
            swept.append(leg)
        else:
            kept.append(leg)
    if not swept:
        return side, ()
    return _Side(side.stack, side.tail, tuple(kept)), tuple(swept)


def _break(
    side: _Side, window: tuple[_WBar, ...], bar: Bar, sign: int, timeframe_s: int
) -> tuple[_Side, BosLeg | None, BosLeg | None]:
    """A break of the newest unbroken swing: ``(side, new leg, leg dropped by the cap)``."""
    if not side.stack:
        return side, None, None
    top = side.stack[-1]
    close = bar.c if sign > 0 else -bar.c
    if not close > top.price:
        return side, None, None
    origin = side.tail
    for w in window:
        if w.idx >= top.idx:
            origin = min(origin, _lo(w, sign))
    terminal = _hi(window[-1], sign)
    leg = BosLeg(
        instrument=bar.instrument,
        timeframe_s=timeframe_s,
        direction="bullish" if sign > 0 else "bearish",
        swing_price=sign * top.price,
        swing_open_ns=top.open_ns,
        break_open_ns=bar.open_ns,
        break_close_ns=bar.close_ns,
        origin=sign * origin,
        terminal=sign * terminal,
    )
    stack = side.stack[:-1]
    tail = min(stack[-1].seg, side.tail) if stack else _INF
    legs = side.legs
    capped = None
    if len(legs) >= MAX_ACTIVE_LEGS:
        capped, legs = legs[0], legs[1:]
    return _Side(stack, tail, (*legs, leg)), leg, capped


def _push(side: _Side, cand: _WBar, window: tuple[_WBar, ...], sign: int) -> _Side:
    """Add a newly confirmed swing as the newest unbroken one."""
    entry = _Unbroken(_hi(cand, sign), cand.idx, cand.open_ns, _INF)
    if not side.stack:
        return _Side((entry,), _INF, side.legs)
    top = side.stack[-1]
    seg = side.tail
    for w in window:
        if top.idx <= w.idx < cand.idx:
            seg = min(seg, _lo(w, sign))
    return _Side((*side.stack[:-1], replace(top, seg=seg), entry), _INF, side.legs)


def _sweeps(levels: Iterable[_LevelSpec], bar: Bar, sweep_pts: float) -> tuple[LiquiditySweep, ...]:
    out: list[LiquiditySweep] = []
    for price, source, side, timeframe_s, swing_open_ns in levels:
        if bar.o < price and bar.h >= price + sweep_pts and bar.c < price:
            level = ChartLevel(source, side, price, timeframe_s, swing_open_ns)
            out.append(LiquiditySweep("above", level))
        elif bar.o > price and bar.l <= price - sweep_pts and bar.c > price:
            level = ChartLevel(source, side, price, timeframe_s, swing_open_ns)
            out.append(LiquiditySweep("below", level))
    return tuple(out)


def _on_tf_bar(tf: _Tf, bar: Bar, levels: tuple[_LevelSpec, ...], config: ChartConfig) -> _Tf:
    n = tf.pivot
    wb = _WBar(tf.count, bar.open_ns, bar.h, bar.l)
    window = (*tf.window, wb)
    evicted = None
    if len(window) > 2 * n + 1:
        evicted, window = window[0], window[1:]
    sweep_pts = config.sweep_ticks * TICK_SIZE_PTS
    bos = tf.timeframe_s in BOS_TIMEFRAMES_S
    bull, bear = tf.bull, tf.bear
    highs, lows = tf.highs, tf.lows
    breaks: list[BosLeg] = []
    swept: tuple[BosLeg, ...] = ()
    capped: list[BosLeg] = []
    if bos:
        sides: list[_Side] = []
        for sign, side in ((1, bull), (-1, bear)):
            if evicted is not None and side.stack and evicted.idx >= side.stack[-1].idx:
                side = _Side(side.stack, min(side.tail, _lo(evicted, sign)), side.legs)
            side, dropped = _drop_swept(side, bar, sweep_pts)
            swept += dropped
            side, leg, cap = _break(side, window, bar, sign, tf.timeframe_s)
            if leg is not None:
                breaks.append(leg)
            if cap is not None:
                capped.append(cap)
            sides.append(side)
        bull, bear = sides
    confirmed: list[Swing] = []
    if len(window) == 2 * n + 1:
        cand = window[n]
        for sign in (1, -1):
            peak = _hi(cand, sign)
            if all(peak > _hi(w, sign) for w in window if w.idx != cand.idx):
                swing = Swing(
                    instrument=bar.instrument,
                    timeframe_s=tf.timeframe_s,
                    kind="high" if sign > 0 else "low",
                    price=cand.h if sign > 0 else cand.l,
                    bar_open_ns=cand.open_ns,
                    confirmed_ns=bar.close_ns,
                )
                confirmed.append(swing)
                if bos:
                    if sign > 0:
                        bull = _push(bull, cand, window, sign)
                    else:
                        bear = _push(bear, cand, window, sign)
                elif sign > 0:
                    highs = (*highs, swing)[-config.swings_kept :]
                else:
                    lows = (*lows, swing)[-config.swings_kept :]
    events = BarEvents(
        timeframe_s=tf.timeframe_s,
        bar=bar,
        candle=candle_label(bar.o, bar.c) if tf.timeframe_s == config.candle_timeframe_s else None,
        swings=tuple(confirmed),
        breaks=tuple(breaks),
        swept_legs=swept,
        capped_legs=tuple(capped),
        sweeps=_sweeps(levels, bar, sweep_pts)
        if tf.timeframe_s == config.sweep_timeframe_s
        else (),
    )
    return _Tf(tf.timeframe_s, n, tf.count + 1, window, highs, lows, bull, bear, events)


# ---------------------------------------------------------------- one instrument


def _new_instrument(bar: Bar, config: ChartConfig) -> _Instrument:
    tfs = tuple(
        _Tf(tf, config.pivot_len_for(tf), 0, (), (), (), _EMPTY_SIDE, _EMPTY_SIDE, None)
        for tf in CHART_TIMEFRAMES_S
    )
    return _Instrument(
        bar.instrument,
        bar.open_ns,
        _new_session(bar.open_ns),
        None,
        tfs,
        (None,) * len(_AGG_TIMEFRAMES),
    )


def _flush(inst: _Instrument, t: Instant, config: ChartConfig) -> _Instrument:
    """Emit every aggregated bar whose period ended at or before ``t``."""
    if not any(p is not None and p.close_ns <= t for p in inst.pending):
        return inst
    tfs = list(inst.tfs)
    pending = list(inst.pending)
    horizon = inst.horizon_ns
    for k, p in enumerate(pending):
        if p is not None and p.close_ns <= t:
            tfs[k + 1] = _on_tf_bar(tfs[k + 1], p.to_bar(inst.instrument), p.levels, config)
            pending[k] = None
            horizon = max(horizon, p.close_ns)
    return _Instrument(
        inst.instrument, horizon, inst.session, inst.prior_rth, tuple(tfs), tuple(pending)
    )


def _on_minute(inst: _Instrument, bar: Bar, config: ChartConfig) -> _Instrument:
    inst = _flush(inst, bar.open_ns, config)
    sweep_tf = config.sweep_timeframe_s
    # Sweep levels come from the state before this bar: those exposed at its open.
    minute_levels = _level_specs(inst, bar.open_ns) if sweep_tf == BASE_TIMEFRAME_S else ()

    session, prior = inst.session, inst.prior_rth
    if not session.start_ns <= bar.open_ns < session.end_ns:
        rth = session.ranges[_RTH]
        prior = rth if rth is not None else prior
        session = _new_session(bar.open_ns)

    pending = list(inst.pending)
    for k, tf_s in enumerate(_AGG_TIMEFRAMES):
        p = pending[k]
        if p is None:
            width = tf_s * NS_PER_SECOND
            start = session.start_ns + (bar.open_ns - session.start_ns) // width * width
            end = min(start + width, session.end_ns)  # cut at the trading-day end (Req 9.7)
            levels = _level_specs(inst, start) if tf_s == sweep_tf else ()
            pending[k] = _start_pending(bar, start, end, levels)
        else:
            pending[k] = p.merge(bar)

    session = _add_to_windows(session, bar)
    tfs = list(inst.tfs)
    tfs[0] = _on_tf_bar(tfs[0], bar, minute_levels, config)
    for k, p in enumerate(pending):
        if p is not None and p.close_ns <= bar.close_ns:
            tfs[k + 1] = _on_tf_bar(tfs[k + 1], p.to_bar(inst.instrument), p.levels, config)
            pending[k] = None
    return _Instrument(
        inst.instrument,
        max(inst.horizon_ns, bar.close_ns),
        session,
        prior,
        tuple(tfs),
        tuple(pending),
    )


def _features(inst: _Instrument, t: Instant) -> InstrumentFeatures:
    day, windows = _windows_at(inst, t)
    prior_rth, overnight, asia, london, ib30 = (
        WindowRange(Unavailable(w), Unavailable(w)) if isinstance(w, str) else WindowRange(*w)
        for w in windows
    )
    swings: list[Swing] = []
    legs: list[BosLeg] = []
    for tf in inst.tfs:
        swings.extend((*tf.highs, *tf.lows))
        legs.extend((*tf.bull.legs, *tf.bear.legs))
    return InstrumentFeatures(
        instrument=inst.instrument,
        session=day,
        prior_rth=prior_rth,
        overnight=overnight,
        asia=asia,
        london=london,
        ib30=ib30,
        swings=tuple(swings),
        bos_legs=tuple(legs),
        last_bars=tuple(tf.last for tf in inst.tfs if tf.last is not None),
    )


# ---------------------------------------------------------------- the state machine


@dataclass(frozen=True, slots=True)
class ChartState:
    """The Chart_Feature_Builder state of every instrument seen, by instrument name.

    Build one with :meth:`initial`, then feed closed 1-minute bars in time order
    with :meth:`on_bar`. Every method returns a new state and leaves this one
    unchanged.
    """

    config: ChartConfig
    instruments: tuple[_Instrument, ...] = ()

    @classmethod
    def initial(cls, config: ChartConfig | None = None) -> ChartState:
        """An empty state; ``config`` defaults to the ``chart`` section defaults."""
        return cls(config if config is not None else ChartConfig())

    def instrument_names(self) -> tuple[str, ...]:
        return tuple(i.instrument for i in self.instruments)

    def on_bar(self, bar: Bar) -> ChartState:
        """Consume one closed 1-minute bar.

        Raises ``ValueError`` for a bar of another interval, or one that opens
        before the instrument's previous bar closed (or before the end of an
        aggregated period already emitted).
        """
        if bar.interval_s != BASE_TIMEFRAME_S:
            raise ValueError(
                f"ChartState takes {BASE_TIMEFRAME_S} s bars, got a {bar.interval_s} s bar"
            )
        names = self.instrument_names()
        if bar.instrument in names:
            k = names.index(bar.instrument)
            current = self.instruments[k]
            if bar.open_ns < current.horizon_ns:
                raise ValueError(
                    f"{bar.instrument} bar opening at {bar.open_ns} is out of order: bars up to "
                    f"{current.horizon_ns} were already consumed"
                )
            updated = _on_minute(current, bar, self.config)
            instruments = (*self.instruments[:k], updated, *self.instruments[k + 1 :])
        else:
            added = _on_minute(_new_instrument(bar, self.config), bar, self.config)
            instruments = tuple(sorted((*self.instruments, added), key=lambda i: i.instrument))
        return ChartState(self.config, instruments)

    def on_bars(self, bars: Iterable[Bar]) -> ChartState:
        """Consume bars in order; equal to calling :meth:`on_bar` for each."""
        state = self
        for bar in bars:
            state = state.on_bar(bar)
        return state

    def advance(self, t: Instant) -> ChartState:
        """Emit every aggregated bar whose period ended at or before ``t``.

        Optional: :meth:`on_bar` and :meth:`features` do the same on demand,
        so calling it never changes a later result.
        """
        instruments = tuple(_flush(i, t, self.config) for i in self.instruments)
        if all(a is b for a, b in zip(instruments, self.instruments, strict=True)):
            return self
        return ChartState(self.config, instruments)

    def features(self, t: Instant) -> ChartFeatures:
        """Chart features at Decision_Time ``t`` from the bars consumed so far.

        Raises ``ValueError`` when ``t`` is before a bar already consumed
        (Req 9.1: a feature at ``t`` uses only bars with ``close_ns <= t``).
        """
        out: list[InstrumentFeatures] = []
        for inst in self.instruments:
            if t < inst.horizon_ns:
                raise ValueError(
                    f"features at {t} requested after consuming {inst.instrument} bars up to "
                    f"{inst.horizon_ns}"
                )
            out.append(_features(_flush(inst, t, self.config), t))
        return ChartFeatures(t, tuple(out))
