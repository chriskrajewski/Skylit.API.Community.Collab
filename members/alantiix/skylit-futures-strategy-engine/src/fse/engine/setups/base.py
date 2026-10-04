"""Shared Setup_Detector machinery (design §10, Req 8.11 and 10.5-10.14).

:class:`DetectContext` is the frozen, read-only view of one Decision_Time that
every detector reads; :class:`DetectorParams` holds the config sections the
detectors read. A detector (:class:`PatternDetector`) selects source Nodes and
directions; :func:`construct` turns each selection into a Candidate_Setup, a
Detection_Skip or nothing:

1. **Sources** (Req 10.5). ES levels come only from ``data.es_source_symbol``
   (default SPX) and NQ levels only from ``data.nq_source_symbol`` (default
   QQQ), each in the detector's ``metric``, ES first. The instrument is
   ``data.instruments`` (default MES and MNQ).
2. **Unpriced** (Req 8.11). When the Level_Converter returned ``MissingPrice``
   for the source (symbol, metric), every selected Node is emitted as an
   unpriced Candidate_Setup (no level, entry, stop or targets), so the
   Gate_Evaluator rejects it with ``no_conversion_price`` and the funnel
   counts it. Arming needs a level, so it is not checked.
3. **Arm** (Req 10.6) when ``|Futures_Price - L| <= arming distance``: ES
   ``arming.es_pts``; NQ ``arming.nq_qqq_usd x`` the QQQ ratio (NQ ÷ QQQ spot)
   of the Map_State's QQQ Snapshot in the same metric. No Futures_Price, or
   no QQQ ratio for an NQ source, means no setup.
4. **Entry** (Req 10.7): ``L + sign x entry_offset_ticks``, capped at the
   Deflection_Band edge and rounded to a tick toward ``L``.
5. **Stop** (Req 10.8-10.10). The rule is the detector's ``stop_rule`` when
   set, else the ``exits`` setting's for the setup's Regime (Req 12.2).
   One-Node-beyond: the converted level of the nearest other Node of the
   source Snapshot strictly beyond the invalidation level on the stop side,
   at most ``stop_lookout_pct`` percent of the paired futures price away;
   with none, fixed ticks. Fixed ticks: ``fixed_stop_ticks`` beyond the band
   edge on the stop side, rounded to a tick away from entry.
6. **Targets** (Req 10.13) from :func:`fse.engine.targets.plan_targets`
   under :func:`~fse.engine.targets.exit_mode_for`, the same rule the
   min_reward_risk Gate and the Order_Planner use.
7. **Validate** (Req 10.14): ``stop < entry < targets[0]`` for a long,
   mirrored for a short. A stop not on the risk side of entry, or a first
   target not beyond entry, gives ``DetectionSkip(..., "price_order")``. No
   target price (``NoTarget`` for Trailing, or ``no_target_node``) gives
   ``"no_target"``; ``tp2_not_beyond_tp1`` gives ``"price_order"`` (the
   targets break plan order).
8. **Setup_Key** (Req 10.11): ``tap_seq`` is 1 plus the source Node's Taps
   this session that ended before ``t`` (:meth:`TapView.tap_seq`).
9. **Inputs** (Req 10.12): :class:`~fse.engine.types.SetupInputs` with the
   context values. No detector parameter references a chart level, so
   ``chart_levels`` is empty.

Every price comparison is on integer ticks, on exact float multiples of a
tick (``x * 4`` is exact in binary), or on exact rationals: the arming
distance and the one-Node-beyond lookout read a config number as the decimal
it was written as (``0.35`` is 7/20, as in :mod:`fse.engine.targets`) and a
context number (the QQQ ratio, the paired futures close, a band edge) as its
exact binary value, so a distance exactly on the limit counts as within it.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from fractions import Fraction
from types import MappingProxyType
from typing import Final, Protocol

from fse.config.schema.data import DataConfig
from fse.config.schema.exits import ExitsConfig
from fse.config.schema.levels import LevelsConfig
from fse.config.schema.patterns import DetectorConfig, PatternsConfig
from fse.engine.chart import ChartFeatures
from fse.engine.levels import (
    NQ_BAND_SYMBOL,
    TICKS_PER_POINT,
    ConvertedMap,
    LevelFamily,
    LevelParams,
    conversion_method,
    level_family,
)
from fse.engine.nodes import NodeLabels
from fse.engine.regime import RegimeResult
from fse.engine.taps import TapView
from fse.engine.targets import (
    TargetBasis,
    TargetContext,
    TargetRejection,
    exit_mode_for,
    plan_targets,
)
from fse.engine.types import (
    CandidateSetup,
    DetectionSkip,
    Direction,
    MapGrade,
    Metric,
    MissingInput,
    MissingPrice,
    NoTarget,
    Regime,
    SetupInputs,
    SetupKey,
    SkipCondition,
    Snapshot,
    SnapshotAsOf,
    SourceNodeRef,
    StopRule,
    Ticks,
    direction_sign,
)
from fse.pit.protocols import MapState, SymMetric
from fse.timekit import Instant

__all__ = [
    "DetectContext",
    "Detection",
    "Detector",
    "DetectorParams",
    "PatternDetector",
    "Pick",
    "Source",
    "construct",
    "sources",
]

type Detection = CandidateSetup | DetectionSkip
"""One detector output."""

type Pick = tuple[float, Direction]
"""A selected source Node strike and the trade direction."""

_PCT: Final = 100


def _exact(x: float) -> Fraction:
    """The decimal value config number ``x`` was written as: ``0.35`` is 7/20."""
    return Fraction(repr(x))


# ---------------------------------------------------------------- context and parameters


@dataclass(frozen=True, slots=True)
class DetectContext:
    """Everything the detectors read at Decision_Time ``t``; immutable and shared.

    ``labels`` and ``converted`` hold an entry for every Snapshot in
    ``map_state`` (each converted map of that very Snapshot, or
    ``MissingPrice``). ``futures_price`` maps an instrument to its
    Futures_Price in ticks; an instrument without one is absent.
    ``map_state``, ``chart`` and ``taps`` are all at ``t``. Construction
    raises ``ValueError`` otherwise.
    """

    t: Instant
    map_state: MapState
    labels: Mapping[SymMetric, NodeLabels]
    converted: Mapping[SymMetric, ConvertedMap | MissingPrice]
    regime: RegimeResult | MissingInput
    grade: MapGrade | MissingInput
    chart: ChartFeatures
    taps: TapView
    futures_price: Mapping[str, Ticks]
    map_as_of: tuple[SnapshotAsOf, ...] = field(init=False, compare=False)

    def __post_init__(self) -> None:
        for name, at in (("map_state", self.map_state.t), ("chart", self.chart.t)):
            if at != self.t:
                raise ValueError(f"DetectContext.{name} is at {at}, not at t {self.t}")
        if self.taps.t != self.t:
            raise ValueError(f"DetectContext.taps is at {self.taps.t}, not at t {self.t}")
        labels = MappingProxyType(dict(self.labels))
        converted = MappingProxyType(dict(self.converted))
        prices = MappingProxyType(dict(self.futures_price))
        for s in self.map_state.snapshots():
            key = (s.symbol, s.metric)
            if key not in labels:
                raise ValueError(f"DetectContext.labels has no entry for {s.symbol} {s.metric}")
            entry = converted.get(key)
            if entry is None:
                raise ValueError(f"DetectContext.converted has no entry for {s.symbol} {s.metric}")
            if isinstance(entry, ConvertedMap) and (
                entry.symbol,
                entry.metric,
                entry.as_of_ns,
            ) != (s.symbol, s.metric, s.as_of_ns):
                raise ValueError(
                    f"DetectContext.converted for {s.symbol} {s.metric} is not of its Snapshot"
                )
        for instrument, price in prices.items():
            if isinstance(price, bool) or not isinstance(price, int):
                raise ValueError(f"Futures_Price of {instrument} must be integer ticks")
        as_of = sorted(
            (SnapshotAsOf(s.symbol, s.metric, s.as_of_ns) for s in self.map_state.snapshots()),
            key=lambda a: (a.symbol, a.metric),
        )
        object.__setattr__(self, "labels", labels)
        object.__setattr__(self, "converted", converted)
        object.__setattr__(self, "futures_price", prices)
        object.__setattr__(self, "map_as_of", tuple(as_of))

    @property
    def session(self) -> date:
        """The session of ``t`` (the Setup_Key session)."""
        return self.taps.session

    @property
    def regime_value(self) -> Regime | MissingInput:
        """The Regime, or the ``MissingInput`` that stands in for it."""
        return self.regime.regime if isinstance(self.regime, RegimeResult) else self.regime


def _default_patterns() -> PatternsConfig:
    return PatternsConfig()


def _default_data() -> DataConfig:
    return DataConfig()


def _default_levels() -> LevelsConfig:
    return LevelsConfig()


def _default_exits() -> ExitsConfig:
    return ExitsConfig()


@dataclass(frozen=True, slots=True)
class DetectorParams:
    """The ``patterns``, ``data``, ``levels`` and ``exits`` sections the detectors read.

    Raises ``ValueError`` when ``data.es_source_symbol`` has no ES levels or
    ``data.nq_source_symbol`` no NQ levels.
    """

    patterns: PatternsConfig = field(default_factory=_default_patterns)
    data: DataConfig = field(default_factory=_default_data)
    levels: LevelsConfig = field(default_factory=_default_levels)
    exits: ExitsConfig = field(default_factory=_default_exits)

    def __post_init__(self) -> None:
        for name, symbol, family in (
            ("es_source_symbol", self.data.es_source_symbol, "ES"),
            ("nq_source_symbol", self.data.nq_source_symbol, "NQ"),
        ):
            if level_family(symbol) != family:
                raise ValueError(f"data.{name} {symbol} has no {family} levels")

    @property
    def level_params(self) -> LevelParams:
        return LevelParams.from_config(self.levels, self.data)


# ---------------------------------------------------------------- sources


@dataclass(frozen=True, slots=True)
class Source:
    """One source Snapshot with its labels, converted map and traded instrument."""

    family: LevelFamily
    instrument: str
    snapshot: Snapshot
    labels: NodeLabels
    converted: ConvertedMap | MissingPrice
    values: Mapping[float, float] = field(init=False, compare=False)

    def __post_init__(self) -> None:
        values: dict[float, float] = {}
        for strike, value in zip(self.snapshot.strikes, self.snapshot.values, strict=True):
            values.setdefault(strike, value)
        object.__setattr__(self, "values", MappingProxyType(values))

    @property
    def symbol(self) -> str:
        return self.snapshot.symbol

    @property
    def metric(self) -> Metric:
        return self.snapshot.metric

    @property
    def spot(self) -> float:
        return self.snapshot.spot

    def value(self, strike: float) -> float:
        """The signed value of one of the Snapshot's strikes."""
        return self.values[strike]


def sources(ctx: DetectContext, p: DetectorParams, metric: Metric) -> tuple[Source, ...]:
    """The ES then NQ source Snapshots of ``metric`` present in Map_State (Req 10.5)."""
    data = p.data
    specs: tuple[tuple[LevelFamily, str, str], ...] = (
        ("ES", data.es_source_symbol, data.instruments.es_levels),
        ("NQ", data.nq_source_symbol, data.instruments.nq_levels),
    )
    out: list[Source] = []
    for family, symbol, instrument in specs:
        snapshot = ctx.map_state.get(symbol, metric)
        if not isinstance(snapshot, Snapshot):
            continue
        key = (symbol, metric)
        out.append(Source(family, instrument, snapshot, ctx.labels[key], ctx.converted[key]))
    return tuple(out)


# ---------------------------------------------------------------- construction


def _entry(level: Ticks, band: tuple[float, float], sign: int, offset: int) -> Ticks:
    """``L`` moved by the offset, capped at the band edge, rounded toward ``L`` (Req 10.7)."""
    raw = level + sign * offset
    lo, hi = band
    if raw > level:
        return min(raw, math.floor(hi * TICKS_PER_POINT))
    if raw < level:
        return max(raw, math.ceil(lo * TICKS_PER_POINT))
    return level


def _fixed_stop(band: tuple[float, float], sign: int, ticks: int) -> Ticks:
    """``ticks`` beyond the band edge on the stop side, rounded away from entry (Req 10.9)."""
    lo, hi = band
    if sign > 0:
        return math.floor(lo * TICKS_PER_POINT) - ticks
    return math.ceil(hi * TICKS_PER_POINT) + ticks


def _one_node_beyond(
    source: Source,
    cm: ConvertedMap,
    strike: float,
    level: Ticks,
    band: tuple[float, float],
    sign: int,
    cfg: DetectorConfig,
) -> Ticks | None:
    """The nearest other Node level beyond the invalidation level, within the lookout.

    Distances are exact rationals in ticks (Req 10.10 "at most").
    """
    if cfg.invalidation == "level":
        inv = Fraction(level)
    else:
        inv = Fraction(band[0] if sign > 0 else band[1]) * TICKS_PER_POINT
    close = Fraction(cm.conversion.futures_close)
    lookout = _exact(cfg.stop_lookout_pct) / _PCT * close * TICKS_PER_POINT
    best: Ticks | None = None
    for node in source.labels.nodes:
        if node == strike:
            continue
        node_level = cm.level_of(node)
        beyond = sign * (inv - node_level)
        if beyond <= 0 or beyond > lookout:
            continue
        if best is None or sign * node_level > sign * best:
            best = node_level
    return best


def _nq_ratio(ctx: DetectContext, metric: Metric) -> float | None:
    """NQ ÷ QQQ spot from the Map_State's QQQ Snapshot of ``metric``, when priced."""
    qqq = ctx.converted.get((NQ_BAND_SYMBOL, metric))
    return qqq.conversion.factor if isinstance(qqq, ConvertedMap) else None


def _arming_ticks(cfg: DetectorConfig, ctx: DetectContext, source: Source) -> Fraction | None:
    """The exact arming distance in ticks (Req 10.6); ``None`` for NQ with no QQQ ratio."""
    if source.family == "ES":
        return _exact(cfg.arming.es_pts) * TICKS_PER_POINT
    ratio = _nq_ratio(ctx, source.metric)
    if ratio is None:
        return None
    return _exact(cfg.arming.nq_qqq_usd) * Fraction(ratio) * TICKS_PER_POINT


def construct(
    detector_id: str,
    pattern: str,
    cfg: DetectorConfig,
    ctx: DetectContext,
    p: DetectorParams,
    source: Source,
    pick: Pick,
) -> Detection | None:
    """The Candidate_Setup or Detection_Skip for one selected Node; ``None`` when not armed."""
    strike, direction = pick
    sign = direction_sign(direction)
    tap_seq = ctx.taps.tap_seq((source.symbol, source.metric, strike))
    key = SetupKey(source.instrument, pattern, strike, direction, ctx.session, tap_seq)
    exit_cfg = exit_mode_for(p.exits, ctx.regime_value)
    value = source.value(strike)
    price = ctx.futures_price.get(source.instrument)
    futures: Ticks | MissingPrice = MissingPrice(source.instrument) if price is None else price
    cm = source.converted

    if isinstance(cm, MissingPrice):
        inputs = SetupInputs(
            map_as_of=ctx.map_as_of,
            source_spot=source.spot,
            futures_price=futures,
            conversion_method=conversion_method(source.symbol, p.level_params),
            conversion_factor=cm,
            band_half_width_pts=cm,
            regime=ctx.regime_value,
            map_grade=ctx.grade,
            stop_rule=None,
        )
        ref = SourceNodeRef(source.symbol, source.metric, strike, value, None, None)
        return CandidateSetup(key, detector_id, ctx.t, None, None, (), exit_cfg.mode, ref, inputs)

    if price is None:
        return None
    arming = _arming_ticks(cfg, ctx, source)
    if arming is None:
        return None
    level = cm.level_of(strike)
    if abs(price - level) > arming:
        return None

    band = cm.band_of(strike)
    entry = _entry(level, band, sign, cfg.entry_offset_ticks)
    rule: StopRule = exit_cfg.stop_rule if cfg.stop_rule is None else cfg.stop_rule
    stop: Ticks | None = None
    if rule == "one_node_beyond":
        stop = _one_node_beyond(source, cm, strike, level, band, sign, cfg)
        if stop is None:
            rule = "fixed_ticks"
    if stop is None:
        stop = _fixed_stop(band, sign, cfg.fixed_stop_ticks)

    def skip(condition: SkipCondition) -> DetectionSkip:
        return DetectionSkip(key, ctx.t, condition)

    if not sign * stop < sign * entry:
        return skip("price_order")
    basis = TargetBasis(direction, entry, stop, strike, value)
    planned = plan_targets(
        basis, exit_cfg, TargetContext.from_snapshot(source.snapshot, source.labels, cm)
    )
    if isinstance(planned, NoTarget):
        return skip("no_target")
    if isinstance(planned, TargetRejection):
        return skip("no_target" if planned.reason == "no_target_node" else "price_order")
    targets = planned.prices
    if not sign * entry < sign * targets[0]:
        return skip("price_order")

    inputs = SetupInputs(
        map_as_of=ctx.map_as_of,
        source_spot=source.spot,
        futures_price=price,
        conversion_method=cm.conversion.method,
        conversion_factor=cm.conversion.factor,
        band_half_width_pts=cm.band_half_width_pts,
        regime=ctx.regime_value,
        map_grade=ctx.grade,
        stop_rule=rule,
    )
    ref = SourceNodeRef(source.symbol, source.metric, strike, value, level, band)
    return CandidateSetup(key, detector_id, ctx.t, entry, stop, targets, exit_cfg.mode, ref, inputs)


# ---------------------------------------------------------------- detectors


class Detector(Protocol):
    """One Setup_Detector detector: an id and a pure ``detect`` (design §10)."""

    @property
    def id(self) -> str: ...

    @property
    def pattern(self) -> str: ...

    def detect(self, ctx: DetectContext, p: DetectorParams) -> tuple[Detection, ...]:
        """This detector's output at ``ctx.t``, whatever its enabled flag."""
        ...


@dataclass(frozen=True, slots=True)
class PatternDetector[C: DetectorConfig]:
    """A detector from its source Node selection rule.

    ``id`` is the detector id (``patterns`` key and ``CandidateSetup.detector_id``);
    ``pattern`` the Pattern written to ``SetupKey.pattern``. ``config_of``
    reads this detector's entry of the ``patterns`` section and ``select``
    returns its picks for one source Snapshot, in output order.
    """

    id: str
    pattern: str
    config_of: Callable[[PatternsConfig], C]
    select: Callable[[DetectContext, Source, C], tuple[Pick, ...]]

    def detect(self, ctx: DetectContext, p: DetectorParams) -> tuple[Detection, ...]:
        """Per source (ES then NQ), per pick: :func:`construct`, dropping unarmed picks."""
        cfg = self.config_of(p.patterns)
        out: list[Detection] = []
        for source in sources(ctx, p, cfg.metric):
            for pick in self.select(ctx, source, cfg):
                made = construct(self.id, self.pattern, cfg, ctx, p, source, pick)
                if made is not None:
                    out.append(made)
        return tuple(out)
