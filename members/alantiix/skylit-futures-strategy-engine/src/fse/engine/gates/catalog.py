"""The 27 Gates: their context, measured values and measurements (design §11 table).

Each Gate measures one Candidate_Setup at its Decision_Time ``t`` against its
``gates`` settings and returns a :class:`Measurement`: the measured value, the
threshold, and whether the measurement fails. A Gate never reads its enabled
flag: :func:`fse.engine.gates.registry.evaluate` measures every Gate and turns
a disabled Gate's measurement into a ``disabled`` result (Req 11.2-11.4).

**Missing inputs are values** (Req 11.11, design "Error Handling" 2). A Gate
whose input is absent at ``t`` (a Snapshot, bar, VIX value, lifecycle label,
Node_Velocity, or dark-pool prints not fetched) measures
:class:`DataUnavailable` and fails. Inputs arrive as typed ``Unavailable``,
``MissingInput`` or absent mapping keys; no Gate catches an exception to
produce it. A Gate that needs the source Node's converted level, entry, stop
or targets measures :class:`NoConversionPrice` and fails when the
Candidate_Setup is unpriced (Req 8.11). An exception from a Gate is a bug.

**Other measured values**: :class:`NoPlannedTarget` (``no_target``, Req 11.7),
:class:`NoneFound` (nothing to measure against: no Chart_Level, BOS_Leg,
qualifying print, release or opposition Node), :class:`PatternExempt` (the
Gate does not apply to the detector; it passes), :class:`LateChase`, and the
active Lockouts.

**Exact comparisons.** Prices are integer ticks (``x / 4`` points is exact).
Ratios of tick distances are exact fractions, compared with a threshold taken
as the decimal written in the Strategy_Config (:func:`exact_decimal`), and
recorded as the nearest float. Times compare as New York wall times or as
integer nanoseconds.

The Gates, in the Req 11.1 order (``L`` the source Node's converted level,
``h`` the Deflection_Band half-width in points, "beyond" in the trade
direction):

1. ``stale_map``: Snapshot_Age in seconds; fails above ``max_snapshot_age_s``.
2. ``map_grade``: the Map_Grade; fails in ``fail_grades`` or missing.
3. ``midpoint``: ``x = (entry - Floor) / (Ceiling - Floor)`` on converted
   levels; fails when ``lo < x < hi`` and the source Node is neither the King
   nor a Gatekeeper. No Floor or no Ceiling: :class:`NoneFound`, passes.
4. ``deflection_band``: how far Futures_Price is past ``L`` on the far side
   (below for a long, above for a short), in points, at least 0; fails above ``h``.
5. ``chart_confluence``: the distance from ``L`` to the nearest exposed
   Chart_Level of the instrument; fails above ``h``.
6. ``stdev_fib_zone``: the distance from entry to the nearest zone of any
   active BOS_Leg (0 inside a zone); fails above ``h``.
7. ``dark_pool_confluence``: the distance from ``L`` to the nearest print
   with notional at least ``min_notional_usd``, its price scaled by the
   ticker's futures-to-spot ratio; fails above ``h``. ``dark_pool`` holds the
   prints of the trailing ``lookback_sessions`` sessions.
8. ``trinity_agreement``: how many of the Trinity (ES) or NQ_Sources (NQ)
   symbols have a Node of the source Node's sign and metric whose level, in
   the setup's instrument, lies in the source Node's band, edges included.
   Same-family levels come from the Level_Converter; a Trinity symbol of the
   other family (QQQ for ES, SPY for NQ) is scaled by the ratio method with
   the source's paired futures close. A symbol without a conversion price
   does not agree; one without a Snapshot makes the count unavailable. Fails
   below ``min_agree``, except that the Empty_Basement exception passes a long
   ``floor_ceiling_bounce`` Pattern whose QQQ and SPY Floors are both
   Empty_Basement Floors in the band (Req 11.9-11.10).
9. ``candle_color``: the label of the latest closed candle-timeframe bar; a
   long needs ``red``, a short ``green``.
10. ``tap_count``: the Setup_Key ``tap_seq``; fails above ``max_tap_seq``.
11. ``third_gatekeeper_test``: the weekly test number, 1 plus the source
    Node's Taps this week that ended before ``t``; ``gatekeeper_fade`` only,
    fails at ``fail_at_test`` or more.
12. ``weekly_node_tests``: the same test number; fails above ``max_tests``.
13. ``sloppy_seconds``: whether the source Node is labeled Sloppy_Seconds.
14. ``air_pocket_fade``: for a fade (``fade_detectors``), how far an entry
    outside the source band sits inside an Air_Pocket, in points; fails above
    ``max_depth_pts``.
15. ``min_reward_risk``: the position-weighted mean entry-to-target distance
    over the entry-to-stop distance, from :func:`fse.engine.targets.plan_targets`
    under the Candidate_Setup's Exit_Mode (TP1_Partial_BE weights TP1 by
    ``tp1_fraction``); fails below ``min``, or ``no_target`` (Req 11.5-11.7).
16. ``opposition_inside_target``: the nearest level of another Node with
    ``|value| >= fraction x |source value|`` beyond entry by at most
    ``window_r x |entry - stop|``; fails when one exists (Req 11.8).
17. ``fomo_travel``: the largest favorable move from entry during the source
    Node's Tap in progress (``seq == tap_seq``, not ended), over the
    entry-to-TP1 distance; 0 with no Tap in progress; fails above ``max_fraction``.
18. ``open_shuffle``: minutes since 09:30 New York time; fails below ``min_minutes``.
19. ``entry_cutoff``: the New York wall time; fails at or after ``cutoff``.
20. ``late_session_chase``: fails in ``[start, end)`` for ``trend_follow`` or
    a setup toward the King (long with the King above spot, short below).
21. ``news_window``: minutes to the nearest release of ``event_types``,
    before or after ``t``; fails at ``minutes`` or fewer.
22. ``vix_gap``: the session's VIX gap in percent (as the Position_Sizer
    computes it); fails at ``pct`` or more before ``until``.
23. ``regime_match``: the Regime; fails outside the detector's allowed set or missing.
24. ``dormant_node``: whether the source Node is Dormant.
25. ``gatekeepers_on_path``: Gatekeepers of the source Snapshot strictly
    between entry and TP1; fails at ``fail_at`` or more.
26. ``node_growth_divergence``: for a fade, the source Node_Velocity in
    percent; fails at ``fail_at_pct`` or more, or unavailable.
27. ``kill_switch_lockout``: the Lockouts active in the session; fails when
    any is (Req 16.9).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time
from fractions import Fraction
from types import MappingProxyType
from typing import Final, Literal, Protocol

from fse.config.schema.data import DataConfig
from fse.config.schema.exits import ExitsConfig
from fse.config.schema.gates import GATE_IDS, GateId, GatesConfig
from fse.engine.levels import ConvertedMap, LevelFamily, level_family, round_to_tick
from fse.engine.lifecycle import NodeState
from fse.engine.nodes import NodeLabels, Velocities
from fse.engine.regime import TRINITY
from fse.engine.risk import Lockout, RiskState, active_lockouts
from fse.engine.setups.base import DetectContext
from fse.engine.sizing import vix_gap_pct
from fse.engine.taps import BASE_INTERVAL_S, NodeId
from fse.engine.targets import TargetContext, TargetRejection, exit_mode_for, plan_targets
from fse.engine.types import (
    Bar,
    CandidateSetup,
    DarkPoolPrint,
    EconomicEvent,
    MissingInput,
    MissingPrice,
    NoTarget,
    Snapshot,
    Ticks,
    Unavailable,
    VixState,
    direction_sign,
)
from fse.pit.protocols import SymMetric
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, RTH_OPEN, Instant, ny_datetime, ny_instant

__all__ = [
    "CATALOG",
    "FLOOR_CEILING_PATTERN",
    "CatalogGate",
    "DataUnavailable",
    "Gate",
    "GateContext",
    "GateParams",
    "LateChase",
    "Measured",
    "Measurement",
    "NoConversionPrice",
    "NoPlannedTarget",
    "NoneFound",
    "PatternExempt",
    "Threshold",
    "exact_decimal",
    "weekly_test",
]

FLOOR_CEILING_PATTERN: Final = "floor_ceiling_bounce"
"""The Pattern the Empty_Basement exception covers (both of its detectors)."""

_TICKS_PER_POINT: Final = 4
_NO_VIX: Final = VixState(
    Unavailable("no VIX daily open given"),
    Unavailable("no VIX prior close given"),
    Unavailable("no VIX 1-minute close given"),
)


def exact_decimal(x: float) -> Fraction:
    """The decimal ``x`` was written as: ``0.6`` is exactly 3/5, not its binary float."""
    return Fraction(repr(x))


def _pts(ticks: int) -> float:
    return ticks / _TICKS_PER_POINT


def _hhmm(wall: time) -> str:
    return f"{wall.hour:02d}:{wall.minute:02d}"


def _hhmmss(local: datetime) -> str:
    return f"{local.hour:02d}:{local.minute:02d}:{local.second:02d}"


# ---------------------------------------------------------------- measured values


@dataclass(frozen=True, slots=True)
class DataUnavailable:
    """The measured value ``data_unavailable``: an input is absent at ``t`` (Req 11.11)."""

    reason: str
    kind: Literal["data_unavailable"] = "data_unavailable"


@dataclass(frozen=True, slots=True)
class NoConversionPrice:
    """The measured value ``no_conversion_price``: the source has no conversion price (Req 8.11)."""

    symbol_or_contract: str
    kind: Literal["no_conversion_price"] = "no_conversion_price"


@dataclass(frozen=True, slots=True)
class NoPlannedTarget:
    """The min_reward_risk measured value ``no_target`` (Req 11.7).

    ``reason`` is ``trailing`` (``NoTarget``) or the ``TargetRejection`` reason.
    """

    reason: str
    kind: Literal["no_target"] = "no_target"


@dataclass(frozen=True, slots=True)
class NoneFound:
    """Nothing to measure against, named by ``what``."""

    what: str
    kind: Literal["none"] = "none"


@dataclass(frozen=True, slots=True)
class PatternExempt:
    """The Gate does not apply to ``detector_id``'s Pattern; it passes."""

    detector_id: str
    kind: Literal["not_applicable"] = "not_applicable"


@dataclass(frozen=True, slots=True)
class LateChase:
    """The late_session_chase measured value: the New York time and the chase conditions."""

    local_time: str
    trend_follow: bool
    toward_king: bool


type Measured = (
    float
    | int
    | str
    | bool
    | tuple[Lockout, ...]
    | LateChase
    | DataUnavailable
    | NoConversionPrice
    | NoPlannedTarget
    | NoneFound
    | PatternExempt
)
"""A Gate's measured value (Req 11.3)."""

type Threshold = (
    float | int | str | bool | tuple[float, float] | tuple[str, ...] | NoConversionPrice
)
"""A Gate's threshold; ``NoConversionPrice`` when it is the band half-width of an unpriced setup."""


@dataclass(frozen=True, slots=True)
class Measurement:
    """One Gate's measured value and threshold, and whether the measurement fails."""

    measured: Measured
    threshold: Threshold
    failed: bool


# ---------------------------------------------------------------- context and parameters


def _default_gates() -> GatesConfig:
    return GatesConfig()


def _default_exits() -> ExitsConfig:
    return ExitsConfig()


def _default_data() -> DataConfig:
    return DataConfig()


@dataclass(frozen=True, slots=True)
class GateParams:
    """The ``gates``, ``exits`` and ``data`` sections the Gates read.

    ``exits`` must be the section the detectors used, so min_reward_risk plans
    the same targets (Req 11.5).
    """

    gates: GatesConfig = field(default_factory=_default_gates)
    exits: ExitsConfig = field(default_factory=_default_exits)
    data: DataConfig = field(default_factory=_default_data)


@dataclass(frozen=True, slots=True)
class GateContext:
    """Everything the Gates read at Decision_Time ``detect.t``; immutable.

    - ``detect``: the Setup_Detector's context (Map_State, labels, converted
      maps, Regime, Map_Grade, chart features, Taps, Futures_Price).
    - ``node_states``: the lifecycle and Sloppy_Seconds label of every
      Map_State Node (``LifecycleState.evaluate``).
    - ``dormant``: the Dormant Nodes (``fse.engine.lifecycle.dormant_nodes``).
    - ``velocities``: Node_Velocity per Map_State (symbol, metric)
      (``fse.engine.nodes.velocity``).
    - ``vix``: the VIX values at ``t``.
    - ``events``: the economic-calendar events.
    - ``dark_pool``: per ticker, the prints of the trailing
      ``dark_pool_confluence.lookback_sessions`` sessions, or ``Unavailable``
      when they were not fetched; an absent ticker was not fetched.
    - ``minute_bars``: per instrument, the session's 1-minute bars that closed
      at or before ``t``, in time order.
    - ``risk``: the Risk_Manager state.

    Construction raises ``ValueError`` for a print or bar after ``t``, a bar of
    another interval or instrument, a bar without tick prices, or bars out of
    order.
    """

    detect: DetectContext
    node_states: Mapping[NodeId, NodeState] = field(default_factory=dict)
    dormant: Collection[NodeId] = frozenset()
    velocities: Mapping[SymMetric, Velocities] = field(default_factory=dict)
    vix: VixState = _NO_VIX
    events: tuple[EconomicEvent, ...] = ()
    dark_pool: Mapping[str, tuple[DarkPoolPrint, ...] | Unavailable] = field(default_factory=dict)
    minute_bars: Mapping[str, tuple[Bar, ...]] = field(default_factory=dict)
    risk: RiskState = field(default_factory=RiskState)

    def __post_init__(self) -> None:
        t = self.t
        prints: dict[str, tuple[DarkPoolPrint, ...] | Unavailable] = {}
        for ticker, entry in self.dark_pool.items():
            if isinstance(entry, Unavailable):
                prints[ticker] = entry
                continue
            kept = tuple(entry)
            for pp in kept:
                if pp.ts_ns > t:
                    raise ValueError(f"{ticker} dark-pool print at {pp.ts_ns} is after t {t}")
            prints[ticker] = kept
        bars: dict[str, tuple[Bar, ...]] = {}
        for instrument, seq in self.minute_bars.items():
            kept_bars = tuple(seq)
            last_close: Instant | None = None
            for bar in kept_bars:
                if bar.instrument != instrument or bar.interval_s != BASE_INTERVAL_S:
                    raise ValueError(
                        f"minute_bars[{instrument!r}] holds a {bar.instrument} "
                        f"{bar.interval_s} s bar"
                    )
                if bar.h_t is None or bar.l_t is None:
                    raise ValueError(f"{instrument} bar opening at {bar.open_ns} has no ticks")
                if bar.close_ns > t:
                    raise ValueError(f"{instrument} bar closing at {bar.close_ns} is after t {t}")
                if last_close is not None and bar.open_ns < last_close:
                    raise ValueError(f"minute_bars[{instrument!r}] is out of order")
                last_close = bar.close_ns
            bars[instrument] = kept_bars
        object.__setattr__(self, "node_states", MappingProxyType(dict(self.node_states)))
        object.__setattr__(self, "dormant", frozenset(self.dormant))
        object.__setattr__(self, "velocities", MappingProxyType(dict(self.velocities)))
        object.__setattr__(self, "events", tuple(self.events))
        object.__setattr__(self, "dark_pool", MappingProxyType(prints))
        object.__setattr__(self, "minute_bars", MappingProxyType(bars))

    @property
    def t(self) -> Instant:
        return self.detect.t

    @property
    def session(self) -> date:
        """The session of ``t``."""
        return self.detect.session


# ---------------------------------------------------------------- source helpers


def _node(c: CandidateSetup) -> NodeId:
    return (c.source.symbol, c.source.metric, c.source.strike)


def _key(c: CandidateSetup) -> SymMetric:
    return (c.source.symbol, c.source.metric)


def _snapshot(c: CandidateSetup, ctx: GateContext) -> Snapshot:
    snap = ctx.detect.map_state.get(c.source.symbol, c.source.metric)
    if not isinstance(snap, Snapshot):
        raise ValueError(f"the context has no {c.source.symbol} {c.source.metric} Snapshot")
    return snap


def _labels(c: CandidateSetup, ctx: GateContext) -> NodeLabels:
    return ctx.detect.labels[_key(c)]


def _values(snap: Snapshot) -> dict[float, float]:
    """Each strike's signed value, first listing kept."""
    out: dict[float, float] = {}
    for strike, value in zip(snap.strikes, snap.values, strict=True):
        out.setdefault(strike, value)
    return out


@dataclass(frozen=True, slots=True)
class _Priced:
    """A priced Candidate_Setup's prices and its source Node's converted map."""

    sign: int
    entry: Ticks
    stop: Ticks
    tp1: Ticks
    level: Ticks
    band: tuple[float, float]
    half_width: float
    converted: ConvertedMap


def _priced(c: CandidateSetup, ctx: GateContext) -> _Priced | NoConversionPrice:
    """The prices of ``c``, or :class:`NoConversionPrice` for an unpriced setup (Req 8.11)."""
    cm = ctx.detect.converted[_key(c)]
    factor = c.inputs.conversion_factor
    if c.entry is None or c.stop is None or c.source.level is None or c.source.band is None:
        if isinstance(factor, MissingPrice):
            return NoConversionPrice(factor.symbol_or_contract)
        if isinstance(cm, MissingPrice):
            return NoConversionPrice(cm.symbol_or_contract)
        return NoConversionPrice(c.source.symbol)
    half_width = c.inputs.band_half_width_pts
    if isinstance(cm, MissingPrice) or isinstance(half_width, MissingPrice):
        raise ValueError(f"priced Candidate_Setup {c.key} has no converted map in the context")
    return _Priced(
        sign=direction_sign(c.key.direction),
        entry=c.entry,
        stop=c.stop,
        tp1=c.targets[0],
        level=c.source.level,
        band=c.source.band,
        half_width=half_width,
        converted=cm,
    )


def _band_threshold(pr: _Priced | NoConversionPrice) -> Threshold:
    return pr if isinstance(pr, NoConversionPrice) else pr.half_width


def _unpriced(pr: NoConversionPrice, threshold: Threshold) -> Measurement:
    return Measurement(pr, threshold, True)


def _within_band(distance_pts: float, pr: _Priced) -> bool:
    return not distance_pts > pr.half_width


def weekly_test(ctx: GateContext, node: NodeId) -> int:
    """1 plus the Node's Taps this week that ended before ``t``: the test the setup makes."""
    taps = ctx.detect.taps
    return taps.prior_week_counts.get(node, 0) + taps.ended_count(node) + 1


def _missing(names: MissingInput) -> DataUnavailable:
    return DataUnavailable("missing " + ", ".join(names.names))


# ---------------------------------------------------------------- the Gates


def _stale_map(c: CandidateSetup, ctx: GateContext, p: GateParams) -> Measurement:
    limit = p.gates.stale_map.max_snapshot_age_s
    age = ctx.detect.map_state.snapshot_age_ns()
    if isinstance(age, Unavailable):
        return Measurement(DataUnavailable(age.reason), limit, True)
    return Measurement(age / NS_PER_SECOND, limit, age > limit * NS_PER_SECOND)


def _map_grade(c: CandidateSetup, ctx: GateContext, p: GateParams) -> Measurement:
    fail: tuple[str, ...] = p.gates.map_grade.fail_grades
    grade = ctx.detect.grade
    if isinstance(grade, MissingInput):
        return Measurement(_missing(grade), fail, True)
    return Measurement(grade, fail, grade in fail)


def _midpoint(c: CandidateSetup, ctx: GateContext, p: GateParams) -> Measurement:
    cfg = p.gates.midpoint
    bounds = (cfg.lo, cfg.hi)
    pr = _priced(c, ctx)
    if isinstance(pr, NoConversionPrice):
        return _unpriced(pr, bounds)
    labels = _labels(c, ctx)
    if labels.floor is None or labels.ceiling is None:
        return Measurement(NoneFound("Floor and Ceiling"), bounds, False)
    lo_level = pr.converted.level_of(labels.floor)
    hi_level = pr.converted.level_of(labels.ceiling)
    if lo_level == hi_level:
        return Measurement(NoneFound("a Floor-to-Ceiling range"), bounds, False)
    x = Fraction(pr.entry - lo_level, hi_level - lo_level)
    strike = c.source.strike
    exempt = strike == labels.king or strike in labels.gatekeepers
    inside = exact_decimal(cfg.lo) < x < exact_decimal(cfg.hi)
    return Measurement(float(x), bounds, inside and not exempt)


def _deflection_band(c: CandidateSetup, ctx: GateContext, p: GateParams) -> Measurement:
    pr = _priced(c, ctx)
    if isinstance(pr, NoConversionPrice):
        return _unpriced(pr, pr)
    price = ctx.detect.futures_price.get(c.key.instrument)
    if price is None:
        reason = f"no {c.key.instrument} Futures_Price at t"
        return Measurement(DataUnavailable(reason), pr.half_width, True)
    penetration = _pts(max(0, pr.sign * (pr.level - price)))
    return Measurement(penetration, pr.half_width, not _within_band(penetration, pr))


def _chart_levels(c: CandidateSetup, ctx: GateContext) -> tuple[float, ...] | DataUnavailable:
    feats = ctx.detect.chart.get(c.key.instrument)
    if isinstance(feats, Unavailable):
        return DataUnavailable(feats.reason)
    return tuple(level.price for level in feats.chart_levels())


def _chart_confluence(c: CandidateSetup, ctx: GateContext, p: GateParams) -> Measurement:
    pr = _priced(c, ctx)
    if isinstance(pr, NoConversionPrice):
        return _unpriced(pr, pr)
    levels = _chart_levels(c, ctx)
    if isinstance(levels, DataUnavailable):
        return Measurement(levels, pr.half_width, True)
    if not levels:
        return Measurement(NoneFound(f"{c.key.instrument} Chart_Level"), pr.half_width, True)
    target = _pts(pr.level)
    distance = min(abs(level - target) for level in levels)
    return Measurement(distance, pr.half_width, not _within_band(distance, pr))


def _stdev_fib_zone(c: CandidateSetup, ctx: GateContext, p: GateParams) -> Measurement:
    pr = _priced(c, ctx)
    if isinstance(pr, NoConversionPrice):
        return _unpriced(pr, pr)
    feats = ctx.detect.chart.get(c.key.instrument)
    if isinstance(feats, Unavailable):
        return Measurement(DataUnavailable(feats.reason), pr.half_width, True)
    if not feats.bos_legs:
        return Measurement(NoneFound(f"active {c.key.instrument} BOS_Leg"), pr.half_width, True)
    entry = _pts(pr.entry)
    best = math.inf  # finite after the loop: there is a leg and a zone
    for leg in feats.bos_legs:
        span = leg.origin - leg.terminal
        for zone in p.gates.stdev_fib_zone.zones:
            a = leg.terminal + zone.near * span
            b = leg.terminal + zone.far * span
            lo, hi = min(a, b), max(a, b)
            distance = lo - entry if entry < lo else entry - hi if entry > hi else 0.0
            best = min(best, distance)
    return Measurement(best, pr.half_width, not _within_band(best, pr))


def _dark_pool_confluence(c: CandidateSetup, ctx: GateContext, p: GateParams) -> Measurement:
    cfg = p.gates.dark_pool_confluence
    pr = _priced(c, ctx)
    if isinstance(pr, NoConversionPrice):
        return _unpriced(pr, pr)
    family = level_family(c.source.symbol)
    ticker: str = cfg.es_ticker if family == "ES" else cfg.nq_ticker
    prints = ctx.dark_pool.get(ticker)
    if prints is None:
        reason = f"{ticker} dark-pool prints were not fetched"
        return Measurement(DataUnavailable(reason), pr.half_width, True)
    if isinstance(prints, Unavailable):
        return Measurement(DataUnavailable(prints.reason), pr.half_width, True)
    scale = ctx.detect.converted.get((ticker, c.source.metric))
    if not isinstance(scale, ConvertedMap):
        reason = f"no {ticker} {c.source.metric} conversion price to scale prints"
        return Measurement(DataUnavailable(reason), pr.half_width, True)
    ratio = scale.conversion.futures_close / scale.conversion.spot
    large = [pp.price for pp in prints if pp.notional >= cfg.min_notional_usd]
    if not large:
        what = f"{ticker} print with notional of at least {cfg.min_notional_usd}"
        return Measurement(NoneFound(what), pr.half_width, True)
    target = _pts(pr.level)
    distance = min(abs(price * ratio - target) for price in large)
    return Measurement(distance, pr.half_width, not _within_band(distance, pr))


def _level_fn(
    symbol: str, c: CandidateSetup, ctx: GateContext, family: LevelFamily, pr: _Priced
) -> Callable[[float], Ticks] | None:
    """Strike to a level of the setup's family; ``None`` without a conversion price."""
    if level_family(symbol) == family:
        cm = ctx.detect.converted.get((symbol, c.source.metric))
        return cm.level_of if isinstance(cm, ConvertedMap) else None
    snap = ctx.detect.map_state.get(symbol, c.source.metric)
    if not isinstance(snap, Snapshot) or not (math.isfinite(snap.spot) and snap.spot > 0):
        return None
    ratio = pr.converted.conversion.futures_close / snap.spot

    def scaled(strike: float) -> Ticks:
        return round_to_tick(strike * ratio)

    return scaled


def _in_band(level: Ticks, pr: _Priced) -> bool:
    lo, hi = pr.band
    return lo <= _pts(level) <= hi


def _agrees(
    symbol: str, c: CandidateSetup, ctx: GateContext, family: LevelFamily, pr: _Priced
) -> bool:
    snap = ctx.detect.map_state.get(symbol, c.source.metric)
    to_level = _level_fn(symbol, c, ctx, family, pr)
    if not isinstance(snap, Snapshot) or to_level is None:
        return False
    positive = c.source.value > 0
    values = _values(snap)
    return any(
        (values[n] > 0) == positive and _in_band(to_level(n), pr)
        for n in ctx.detect.labels[(symbol, c.source.metric)].nodes
    )


def _empty_basement_floor(
    symbol: str, c: CandidateSetup, ctx: GateContext, family: LevelFamily, pr: _Priced
) -> bool:
    snap = ctx.detect.map_state.get(symbol, c.source.metric)
    if not isinstance(snap, Snapshot):
        return False
    labels = ctx.detect.labels[(symbol, c.source.metric)]
    to_level = _level_fn(symbol, c, ctx, family, pr)
    if labels.floor is None or not labels.empty_basement or to_level is None:
        return False
    return _in_band(to_level(labels.floor), pr)


def _trinity_agreement(c: CandidateSetup, ctx: GateContext, p: GateParams) -> Measurement:
    cfg = p.gates.trinity_agreement
    pr = _priced(c, ctx)
    if isinstance(pr, NoConversionPrice):
        return _unpriced(pr, cfg.min_agree)
    family = level_family(c.source.symbol)
    symbols: tuple[str, ...] = TRINITY if family == "ES" else p.data.nq_sources
    metric = c.source.metric
    absent = [s for s in symbols if not isinstance(ctx.detect.map_state.get(s, metric), Snapshot)]
    exception = (
        cfg.empty_basement_exception
        and c.key.pattern == FLOOR_CEILING_PATTERN
        and c.key.direction == "long"
        and all(_empty_basement_floor(s, c, ctx, family, pr) for s in ("QQQ", "SPY"))
    )
    if absent:
        reason = "no " + ", ".join(f"{s} {metric}" for s in absent) + " Snapshot"
        return Measurement(DataUnavailable(reason), cfg.min_agree, not exception)
    count = sum(1 for s in symbols if _agrees(s, c, ctx, family, pr))
    return Measurement(count, cfg.min_agree, count < cfg.min_agree and not exception)


def _candle_color(c: CandidateSetup, ctx: GateContext, p: GateParams) -> Measurement:
    need = "red" if c.key.direction == "long" else "green"
    feats = ctx.detect.chart.get(c.key.instrument)
    if isinstance(feats, Unavailable):
        return Measurement(DataUnavailable(feats.reason), need, True)
    labeled = next((e for e in feats.last_bars if e.candle is not None), None)
    if labeled is None or labeled.candle is None:
        reason = f"no closed {c.key.instrument} candle-timeframe bar"
        return Measurement(DataUnavailable(reason), need, True)
    return Measurement(labeled.candle, need, labeled.candle != need)


def _tap_count(c: CandidateSetup, ctx: GateContext, p: GateParams) -> Measurement:
    limit = p.gates.tap_count.max_tap_seq
    return Measurement(c.key.tap_seq, limit, c.key.tap_seq > limit)


def _third_gatekeeper_test(c: CandidateSetup, ctx: GateContext, p: GateParams) -> Measurement:
    fail_at = p.gates.third_gatekeeper_test.fail_at_test
    if c.detector_id != "gatekeeper_fade":
        return Measurement(PatternExempt(c.detector_id), fail_at, False)
    test = weekly_test(ctx, _node(c))
    return Measurement(test, fail_at, test >= fail_at)


def _weekly_node_tests(c: CandidateSetup, ctx: GateContext, p: GateParams) -> Measurement:
    limit = p.gates.weekly_node_tests.max_tests
    test = weekly_test(ctx, _node(c))
    return Measurement(test, limit, test > limit)


def _sloppy_seconds(c: CandidateSetup, ctx: GateContext, p: GateParams) -> Measurement:
    state = ctx.node_states.get(_node(c))
    if state is None:
        return Measurement(DataUnavailable("no lifecycle label for the source Node"), False, True)
    return Measurement(state.sloppy, False, state.sloppy)


def _is_fade(c: CandidateSetup, p: GateParams) -> bool:
    return c.detector_id in p.gates.fade_detectors


def _air_pocket_fade(c: CandidateSetup, ctx: GateContext, p: GateParams) -> Measurement:
    limit = p.gates.air_pocket_fade.max_depth_pts
    if not _is_fade(c, p):
        return Measurement(PatternExempt(c.detector_id), limit, False)
    pr = _priced(c, ctx)
    if isinstance(pr, NoConversionPrice):
        return _unpriced(pr, limit)
    entry = _pts(pr.entry)
    lo, hi = pr.band
    outside = lo - entry if entry < lo else entry - hi if entry > hi else 0.0
    depth = 0.0
    if outside > 0:
        for a_strike, b_strike in _labels(c, ctx).air_pockets:
            a = pr.converted.level_of(a_strike)
            b = pr.converted.level_of(b_strike)
            if min(a, b) < pr.entry < max(a, b):
                depth = outside
                break
    return Measurement(depth, limit, depth > limit)


def _min_reward_risk(c: CandidateSetup, ctx: GateContext, p: GateParams) -> Measurement:
    cfg = p.gates.min_reward_risk
    pr = _priced(c, ctx)
    if isinstance(pr, NoConversionPrice):
        return _unpriced(pr, cfg.min)
    mode = exit_mode_for(p.exits, ctx.detect.regime_value)
    if mode.mode != c.exit_mode:
        raise ValueError(
            f"Candidate_Setup {c.key} has Exit_Mode {c.exit_mode!r} but the exits "
            f"setting for its Regime is {mode.mode!r}"
        )
    snap = _snapshot(c, ctx)
    planned = plan_targets(
        c, mode, TargetContext.from_snapshot(snap, _labels(c, ctx), pr.converted)
    )
    if isinstance(planned, NoTarget):
        return Measurement(NoPlannedTarget(mode.mode), cfg.min, True)
    if isinstance(planned, TargetRejection):
        return Measurement(NoPlannedTarget(planned.reason), cfg.min, True)
    reward: Fraction = Fraction(abs(planned.tp1 - pr.entry))
    if planned.tp2 is not None and planned.tp1_fraction is not None:
        share = exact_decimal(planned.tp1_fraction)
        reward = share * abs(planned.tp1 - pr.entry) + (1 - share) * abs(planned.tp2 - pr.entry)
    ratio = reward / abs(pr.entry - pr.stop)
    measured = float(ratio)
    return Measurement(measured, cfg.min, exact_decimal(measured) < exact_decimal(cfg.min))


def _opposition_inside_target(c: CandidateSetup, ctx: GateContext, p: GateParams) -> Measurement:
    cfg = p.gates.opposition_inside_target
    pr = _priced(c, ctx)
    if isinstance(pr, NoConversionPrice):
        return _unpriced(pr, cfg.window_r)
    window = exact_decimal(cfg.window_r) * abs(pr.entry - pr.stop)
    min_abs = exact_decimal(cfg.fraction) * exact_decimal(abs(c.source.value))
    values = _values(_snapshot(c, ctx))
    best: Ticks | None = None
    for n in _labels(c, ctx).nodes:
        if n == c.source.strike or not exact_decimal(abs(values[n])) >= min_abs:
            continue
        level = pr.converted.level_of(n)
        beyond = pr.sign * (level - pr.entry)
        if 0 < beyond <= window and (best is None or beyond < pr.sign * (best - pr.entry)):
            best = level
    if best is None:
        return Measurement(
            NoneFound(f"opposition Node within {cfg.window_r}R"), cfg.window_r, False
        )
    return Measurement(_pts(best), cfg.window_r, True)


def _fomo_travel(c: CandidateSetup, ctx: GateContext, p: GateParams) -> Measurement:
    limit = p.gates.fomo_travel.max_fraction
    pr = _priced(c, ctx)
    if isinstance(pr, NoConversionPrice):
        return _unpriced(pr, limit)
    taps = ctx.detect.taps.session_taps(_node(c))
    current = next((tap for tap in taps if tap.seq == c.key.tap_seq and not tap.ended), None)
    if current is None:
        return Measurement(0.0, limit, False)
    bars = ctx.minute_bars.get(c.key.instrument)
    if bars is None:
        reason = f"no {c.key.instrument} 1-minute bars given"
        return Measurement(DataUnavailable(reason), limit, True)
    since = [b for b in bars if b.open_ns >= current.first_open_ns]
    if not since:
        reason = f"no {c.key.instrument} 1-minute bar since the Tap began"
        return Measurement(DataUnavailable(reason), limit, True)
    if pr.sign > 0:
        travel = max(0, max(b.h_t for b in since if b.h_t is not None) - pr.entry)
    else:
        travel = max(0, pr.entry - min(b.l_t for b in since if b.l_t is not None))
    ratio = Fraction(travel, abs(pr.tp1 - pr.entry))
    return Measurement(float(ratio), limit, ratio > exact_decimal(limit))


def _open_shuffle(c: CandidateSetup, ctx: GateContext, p: GateParams) -> Measurement:
    limit = p.gates.open_shuffle.min_minutes
    t = ctx.t
    since = t - ny_instant(ny_datetime(t).date(), RTH_OPEN)
    return Measurement(since / NS_PER_MINUTE, limit, since < limit * NS_PER_MINUTE)


def _entry_cutoff(c: CandidateSetup, ctx: GateContext, p: GateParams) -> Measurement:
    cutoff = p.gates.entry_cutoff.cutoff
    local = ny_datetime(ctx.t)
    return Measurement(_hhmmss(local), _hhmm(cutoff), local.time() >= cutoff)


def _late_session_chase(c: CandidateSetup, ctx: GateContext, p: GateParams) -> Measurement:
    cfg = p.gates.late_session_chase
    window = (_hhmm(cfg.start), _hhmm(cfg.end))
    local = ny_datetime(ctx.t)
    king = _labels(c, ctx).king
    spot = _snapshot(c, ctx).spot
    toward = king is not None and (
        (c.key.direction == "long" and king > spot) or (c.key.direction == "short" and king < spot)
    )
    trend = c.detector_id == "trend_follow"
    inside = cfg.start <= local.time() < cfg.end
    return Measurement(
        LateChase(_hhmmss(local), trend, toward), window, inside and (trend or toward)
    )


def _news_window(c: CandidateSetup, ctx: GateContext, p: GateParams) -> Measurement:
    cfg = p.gates.news_window
    kinds = frozenset(cfg.event_types)
    gaps = [abs(e.release_ns - ctx.t) for e in ctx.events if e.event_type in kinds]
    if not gaps:
        return Measurement(NoneFound(" or ".join(cfg.event_types) + " release"), cfg.minutes, False)
    nearest = min(gaps)
    return Measurement(nearest / NS_PER_MINUTE, cfg.minutes, nearest <= cfg.minutes * NS_PER_MINUTE)


def _vix_gap(c: CandidateSetup, ctx: GateContext, p: GateParams) -> Measurement:
    cfg = p.gates.vix_gap
    gap = vix_gap_pct(ctx.vix)
    if isinstance(gap, MissingInput):
        return Measurement(_missing(gap), cfg.pct, True)
    early = ny_datetime(ctx.t).time() < cfg.until
    return Measurement(float(gap), cfg.pct, early and gap >= exact_decimal(cfg.pct))


def _regime_match(c: CandidateSetup, ctx: GateContext, p: GateParams) -> Measurement:
    allowed: tuple[str, ...] = p.gates.regime_match.allowed.for_detector(c.detector_id)
    regime = ctx.detect.regime_value
    if isinstance(regime, MissingInput):
        return Measurement(_missing(regime), allowed, True)
    return Measurement(regime, allowed, regime not in allowed)


def _dormant_node(c: CandidateSetup, ctx: GateContext, p: GateParams) -> Measurement:
    dormant = _node(c) in ctx.dormant
    return Measurement(dormant, False, dormant)


def _gatekeepers_on_path(c: CandidateSetup, ctx: GateContext, p: GateParams) -> Measurement:
    fail_at = p.gates.gatekeepers_on_path.fail_at
    pr = _priced(c, ctx)
    if isinstance(pr, NoConversionPrice):
        return _unpriced(pr, fail_at)
    lo, hi = min(pr.entry, pr.tp1), max(pr.entry, pr.tp1)
    count = sum(1 for g in _labels(c, ctx).gatekeepers if lo < pr.converted.level_of(g) < hi)
    return Measurement(count, fail_at, count >= fail_at)


def _node_growth_divergence(c: CandidateSetup, ctx: GateContext, p: GateParams) -> Measurement:
    fail_at = p.gates.node_growth_divergence.fail_at_pct
    if not _is_fade(c, p):
        return Measurement(PatternExempt(c.detector_id), fail_at, False)
    by_strike = ctx.velocities.get(_key(c))
    if by_strike is None:
        reason = f"no {c.source.symbol} {c.source.metric} Node_Velocity"
        return Measurement(DataUnavailable(reason), fail_at, True)
    value = by_strike.get(c.source.strike)
    if value is None:
        return Measurement(DataUnavailable("no Node_Velocity for the source Node"), fail_at, True)
    if isinstance(value, Unavailable):
        return Measurement(DataUnavailable(value.reason), fail_at, True)
    return Measurement(value, fail_at, value >= fail_at)


def _kill_switch_lockout(c: CandidateSetup, ctx: GateContext, p: GateParams) -> Measurement:
    active = active_lockouts(ctx.risk, ctx.detect.session)
    return Measurement(active, 0, bool(active))


# ---------------------------------------------------------------- the catalog


class Gate(Protocol):
    """One Gate: an id and a pure ``measure`` (design §11)."""

    @property
    def id(self) -> GateId: ...

    def measure(self, c: CandidateSetup, ctx: GateContext, p: GateParams) -> Measurement:
        """The measurement of ``c`` at ``ctx.t``, whatever the Gate's enabled flag."""
        ...


type _MeasureFn = Callable[[CandidateSetup, GateContext, GateParams], Measurement]


@dataclass(frozen=True, slots=True)
class CatalogGate:
    """A Gate from its id and measurement function."""

    id: GateId
    fn: _MeasureFn

    def measure(self, c: CandidateSetup, ctx: GateContext, p: GateParams) -> Measurement:
        return self.fn(c, ctx, p)


_FUNCTIONS: Final[Mapping[GateId, _MeasureFn]] = MappingProxyType(
    {
        "stale_map": _stale_map,
        "map_grade": _map_grade,
        "midpoint": _midpoint,
        "deflection_band": _deflection_band,
        "chart_confluence": _chart_confluence,
        "stdev_fib_zone": _stdev_fib_zone,
        "dark_pool_confluence": _dark_pool_confluence,
        "trinity_agreement": _trinity_agreement,
        "candle_color": _candle_color,
        "tap_count": _tap_count,
        "third_gatekeeper_test": _third_gatekeeper_test,
        "weekly_node_tests": _weekly_node_tests,
        "sloppy_seconds": _sloppy_seconds,
        "air_pocket_fade": _air_pocket_fade,
        "min_reward_risk": _min_reward_risk,
        "opposition_inside_target": _opposition_inside_target,
        "fomo_travel": _fomo_travel,
        "open_shuffle": _open_shuffle,
        "entry_cutoff": _entry_cutoff,
        "late_session_chase": _late_session_chase,
        "news_window": _news_window,
        "vix_gap": _vix_gap,
        "regime_match": _regime_match,
        "dormant_node": _dormant_node,
        "gatekeepers_on_path": _gatekeepers_on_path,
        "node_growth_divergence": _node_growth_divergence,
        "kill_switch_lockout": _kill_switch_lockout,
    }
)

CATALOG: Final[tuple[CatalogGate, ...]] = tuple(CatalogGate(g, _FUNCTIONS[g]) for g in GATE_IDS)
"""The 27 Gates, in the Req 11.1 order."""
