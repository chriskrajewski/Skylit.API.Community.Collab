"""Property 36: Gate measurements.

*For any* Candidate_Setup and context, ``stale_map`` measures Snapshot_Age
against its maximum; ``min_reward_risk`` equals the position-weighted mean
target distance / stop distance (``no_target`` when no target exists);
``opposition_inside_target`` fails exactly when some other Node with at least
the configured fraction of the source value lies on the target side within
the configured R window; ``trinity_agreement`` counts agreeing symbols per
Requirement 11.9 and passes under the Empty_Basement exception; and every
enabled Gate whose input is missing fails with ``data_unavailable``.

The reference model reads the criteria directly, on whole ticks and exact
rationals built from the decimal text a Strategy_Config or Snapshot carries
("0.85" is 17/20, not its binary float). It is checked against
:func:`fse.engine.gates.registry.evaluate` with a random set of enabled
Gates: an enabled Gate records ``pass`` or ``fail`` and a disabled one
``disabled``, with the same measured value and threshold (Req 11.4), and a
failing one has a Rejection_Reason with both.

- stale_map (5.7): Snapshot_Age is t minus the oldest ``asOf`` of the
  Map_State Snapshots, in seconds; fails above the configured maximum.
- min_reward_risk (11.5-11.7): over the targets the Order_Planner plans
  (``plan_targets``) under the setup's Exit_Mode, reward is |TP1 - entry|, or
  f x |TP1 - entry| + (1 - f) x |TP2 - entry| under TP1_Partial_BE with TP1
  fraction f; the measured value is reward / |entry - stop| and fails below
  the minimum. No planned target measures ``no_target`` and fails.
- opposition_inside_target (11.8): a Node of the source Snapshot, other than
  the source, with |value| >= fraction x |source value| and a level d ticks
  beyond entry, 0 < d <= window_r x |entry - stop|; fails when one exists and
  measures the nearest one's level in points.
- trinity_agreement (11.9-11.10): of the Trinity (an ES-family setup) or the
  NQ_Sources (an NQ-family setup), the symbols whose Snapshot in the source
  metric has a Node of the source's sign with a level, in the setup's
  instrument, inside the source band (edges included); a symbol without a
  conversion price does not agree; a missing Snapshot makes the count
  ``data_unavailable``; fails below ``min_agree``. The Empty_Basement
  exception (a long floor_ceiling_bounce whose QQQ and SPY Floors in the
  source metric are both Empty_Basement Floors with levels in the band)
  passes it.
- data_unavailable (11.11): :func:`missing_inputs` lists, per Gate, the
  inputs at t it needs; a Gate measures ``data_unavailable`` exactly when one
  of them is missing, and then fails when enabled.

Interpretations the model makes explicit:

- Node_Classifier labels (Nodes, Floor, Empty_Basement) and Level_Converter
  maps are inputs; scenarios write them directly. Same-family maps use the
  offset method, so a strike's level is 4 x (strike + offset) ticks.
- A Trinity symbol of the other family (QQQ for ES, SPY for NQ) has no
  same-family conversion: its level in the setup's instrument is its strike
  times the source map's futures close over its spot, rounded half up to a
  tick (the Gate catalog's reading; Req 8 defines no cross-family
  conversion). Generated ratios are n/16, so float and exact levels agree.
- When a Trinity Snapshot is missing and the Empty_Basement exception holds,
  trinity_agreement measures ``data_unavailable`` and passes: the exception
  applies "regardless of how many symbols agree" (Req 11.10 over 11.11).
- dark_pool_confluence with prints but no conversion price for the scaling
  ticker lacks a Level_Converter price, which Req 11.11 excludes: no claim.
- Setups are priced; ``no_conversion_price`` is Property 35's.

Generators: an ES-family (SPX or SPY source, MES) or NQ-family (QQQ, NDX or
NDXP source, MNQ) setup in either metric and direction, from any detector,
with a 1 to 100 tick band half-width, an entry inside the band and 1 to 200
ticks of risk. Each (symbol, metric) Map_State entry is a Snapshot, marked
unavailable, or absent (the source is always a Snapshot), with an age from 0
to 400 s or a nanosecond either side of the maximum. Node levels land
anywhere, or on (or a tick either side of) entry, each R target, the window
edge and the band edges; Node values are short decimals, or exactly (or just
below) the opposition threshold. One scenario in four aims at the
Empty_Basement exception. Exit_Mode, R multiples, the TP1 fraction, the four
Gates' parameters, the NQ_Sources and each optional input (Map_Grade,
Regime, Futures_Price, chart features, candle label, dark-pool prints,
lifecycle label, Node_Velocity, VIX values, the Tap in progress and its
1-minute bars) vary independently. Three explicit examples pin the
boundaries: an ES long with the exception, reward:risk exactly 3, opposition
exactly at 85% and 3R, and Snapshot_Age exactly at the maximum; the same
exception with the SPX Snapshot unavailable; and an NQ short with every
optional input missing.

**Validates: Requirements 5.7, 11.5, 11.6, 11.7, 11.8, 11.9, 11.10, 11.11**
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, time
from decimal import Decimal
from fractions import Fraction
from typing import Any, Final, Literal

from hypothesis import event, example, given
from hypothesis import strategies as st

from fse.config.schema.data import DEFAULT_NQ_SOURCES, DataConfig
from fse.config.schema.exits import ExitModeName, ExitsConfig
from fse.config.schema.gates import GATE_IDS, GatesConfig
from fse.config.schema.patterns import DETECTOR_IDS
from fse.engine.chart import BarEvents, CandleLabel, ChartFeatures, InstrumentFeatures, WindowRange
from fse.engine.gates.catalog import (
    DataUnavailable,
    GateContext,
    GateParams,
    NoneFound,
    NoPlannedTarget,
)
from fse.engine.gates.registry import GateEvaluation, GateResult, RejectionReason, evaluate
from fse.engine.levels import Conversion, ConvertedMap
from fse.engine.lifecycle import NodeState
from fse.engine.nodes import NodeLabels
from fse.engine.regime import RegimeResult
from fse.engine.setups.base import DetectContext
from fse.engine.taps import Tap, TapView
from fse.engine.targets import (
    ExitModeCfg,
    NodeLevel,
    TargetBasis,
    TargetContext,
    Targets,
    plan_targets,
)
from fse.engine.types import (
    Bar,
    CandidateSetup,
    DarkPoolPrint,
    Direction,
    Metric,
    MissingInput,
    MissingPrice,
    Regime,
    SetupInputs,
    SetupKey,
    Snapshot,
    SourceNodeRef,
    Unavailable,
    VixState,
)
from fse.pit.protocols import MapState, SymMetric
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, ny_instant

type Family = Literal["ES", "NQ"]
type MapCase = Literal["present", "unavailable", "absent"]
type DarkPoolCase = Literal["absent", "unavailable", "prints"]
type VelocityCase = Literal["no_map", "no_strike", "unavailable", "value"]
type TapCase = Literal["none", "in_progress"]
type BarsCase = Literal["absent", "before_tap", "since_tap"]
type TargetRule = Literal["r", "node"]
type RawNode = tuple[Fraction, Decimal]  # strike, signed value as written

SESSION: Final = date(2026, 3, 5)
T: Final = ny_instant(SESSION, time(10, 0))
NS: Final = NS_PER_SECOND
TICKS_PER_POINT: Final = 4

FAMILIES: Final[tuple[Family, ...]] = ("ES", "NQ")
SAME: Final[dict[Family, tuple[str, ...]]] = {"ES": ("SPX", "SPY"), "NQ": ("QQQ", "NDX", "NDXP")}
CROSS: Final[dict[Family, str]] = {"ES": "QQQ", "NQ": "SPY"}
INSTRUMENT: Final[dict[Family, str]] = {"ES": "MES", "NQ": "MNQ"}
TRINITY: Final[tuple[str, ...]] = ("SPX", "SPY", "QQQ")  # Glossary
FLOOR_CEILING: Final = "floor_ceiling_bounce"
FCB_DETECTORS: Final[tuple[str, ...]] = (FLOOR_CEILING, "floor_ceiling_bounce_empty_basement")

METRICS: Final[tuple[Metric, ...]] = ("gamma", "vanna")
DIRECTIONS: Final[tuple[Direction, ...]] = ("long", "short")
REGIMES: Final[tuple[Regime, ...]] = (
    "Positive_Gamma",
    "Negative_Gamma",
    "Vanna_Dominant",
    "Whipsaw",
    "Structureless",
)
MODES: Final[tuple[ExitModeName, ...]] = (
    "fixed_r",
    "next_node",
    "tp1_partial_be",
    "opposition_or_fixed_r",
    "trailing",
)
TARGET_RULES: Final[tuple[TargetRule, ...]] = ("r", "node")
MAP_CASES: Final[tuple[MapCase, ...]] = ("present", "present", "present", "unavailable", "absent")
DARK_POOL_CASES: Final[tuple[DarkPoolCase, ...]] = ("absent", "unavailable", "prints", "prints")
VELOCITY_CASES: Final[tuple[VelocityCase, ...]] = (
    "no_map",
    "no_strike",
    "unavailable",
    "value",
    "value",
)
TAP_CASES: Final[tuple[TapCase, ...]] = ("none", "in_progress")
BARS_CASES: Final[tuple[BarsCase, ...]] = ("absent", "before_tap", "since_tap")
CANDLES: Final[tuple[CandleLabel | None, ...]] = ("red", "green", "doji", None)


def family_of(symbol: str) -> Family:
    return "ES" if symbol in SAME["ES"] else "NQ"


def tick_round(points: Fraction) -> int:
    """Points to the nearest tick, a half rounded up (Req 8.5)."""
    return math.floor(points * TICKS_PER_POINT + Fraction(1, 2))


# ---------------------------------------------------------------- scenario


@dataclass(frozen=True, slots=True)
class RawMap:
    """One Map_State Snapshot as written, with its labels and its offset.

    ``offset`` is the offset-method factor in points of the map in the
    symbol's own family; ``None`` makes the Level_Converter return
    ``MissingPrice``. ``nodes`` are the Node_Classifier's Nodes and
    ``extras`` the other strikes.
    """

    age_ns: int
    spot: Fraction
    offset: Fraction | None
    nodes: tuple[RawNode, ...]
    extras: tuple[RawNode, ...] = ()
    floor: Fraction | None = None
    empty_basement: bool = False

    def level(self, strike: Fraction) -> int | None:
        """The level of ``strike`` in the symbol's own family; ``None`` without a price."""
        return None if self.offset is None else tick_round(strike + self.offset)


@dataclass(frozen=True, slots=True)
class Raw:
    """One scenario as written: prices in ticks and every other number as a decimal.

    ``maps`` holds a ``RawMap`` per Snapshot, ``None`` per entry marked
    unavailable; an absent key is not in the Map_State. Parameters default to
    the Config_Schema defaults and optional inputs to present.
    """

    family: Family
    source_symbol: str
    metric: Metric
    source_strike: Fraction
    direction: Direction
    detector: str
    level: int  # the source Node's converted level L
    entry: int
    risk: int  # |entry - stop|
    half_width: Fraction  # the Deflection_Band half-width, points
    maps: Mapping[SymMetric, RawMap | None]
    enabled: frozenset[str] = frozenset(GATE_IDS)
    tap_seq: int = 1
    # exits
    mode: ExitModeName = "next_node"
    fixed_r: Decimal = Decimal("3.0")
    tp1_rule: TargetRule = "r"
    tp1_r: Decimal = Decimal("1.5")
    tp2_rule: TargetRule = "node"
    tp2_r: Decimal = Decimal("3.0")
    tp1_fraction: Decimal = Decimal("0.5")
    # Gates and data
    max_age_s: int = 90
    rr_min: Decimal = Decimal("3.0")
    opp_fraction: Decimal = Decimal("0.85")
    window_r: Decimal = Decimal("3.0")
    min_agree: int = 2
    eb_exception: bool = True
    nq_sources: tuple[str, ...] = DEFAULT_NQ_SOURCES
    # optional inputs
    grade_missing: bool = False
    regime_missing: bool = False
    futures_missing: bool = False
    chart: bool = True
    candle: CandleLabel | None = "red"
    dark_pool: DarkPoolCase = "prints"
    node_state: bool = True
    velocity: VelocityCase = "value"
    vix_open_missing: bool = False
    vix_prior_missing: bool = False
    tap: TapCase = "none"
    bars: BarsCase = "since_tap"

    @property
    def sign(self) -> int:
        return 1 if self.direction == "long" else -1

    @property
    def stop(self) -> int:
        return self.entry - self.sign * self.risk

    @property
    def instrument(self) -> str:
        return INSTRUMENT[self.family]

    @property
    def pattern(self) -> str:
        return FLOOR_CEILING if self.detector.startswith(FLOOR_CEILING) else self.detector

    @property
    def source_map(self) -> RawMap:
        m = self.maps[(self.source_symbol, self.metric)]
        assert m is not None
        assert m.offset is not None
        return m

    @property
    def source_value(self) -> Decimal:
        return dict(self.source_map.nodes)[self.source_strike]


@dataclass(frozen=True, slots=True)
class Scenario:
    setup: CandidateSetup
    ctx: GateContext
    params: GateParams


def params_of(raw: Raw) -> GateParams:
    gates: dict[str, Any] = {g: {"enabled": g in raw.enabled} for g in GATE_IDS}
    gates["stale_map"]["max_snapshot_age_s"] = raw.max_age_s
    gates["min_reward_risk"] |= {
        "min": float(raw.rr_min),
        "alert_min": float(min(raw.rr_min, Decimal(2))),
    }
    gates["opposition_inside_target"] |= {
        "fraction": float(raw.opp_fraction),
        "window_r": float(raw.window_r),
    }
    gates["trinity_agreement"] |= {
        "min_agree": raw.min_agree,
        "empty_basement_exception": raw.eb_exception,
    }
    exits = ExitsConfig.model_validate(
        {
            "global": {"mode": raw.mode, "stop_rule": "fixed_ticks"},
            "per_regime": dict.fromkeys(REGIMES),  # every Regime uses the global setting
            "modes": {
                "fixed_r": {"r_multiple": float(raw.fixed_r)},
                "tp1_partial_be": {
                    "tp1": {"rule": raw.tp1_rule, "r_multiple": float(raw.tp1_r)},
                    "tp2": {"rule": raw.tp2_rule, "r_multiple": float(raw.tp2_r)},
                    "tp1_fraction": float(raw.tp1_fraction),
                },
            },
        }
    )
    data = DataConfig.model_validate({"nq_sources": list(raw.nq_sources)})
    return GateParams(GatesConfig.model_validate(gates), exits, data)


def snapshot(symbol: str, metric: Metric, m: RawMap) -> Snapshot:
    pairs = sorted((*m.nodes, *m.extras), key=lambda p: p[0])
    return Snapshot(
        symbol=symbol,
        metric=metric,
        view_id="v",
        as_of_ns=T - m.age_ns,
        as_of_raw="raw",
        spot=float(m.spot),
        previous_close=None,
        strikes=tuple(float(k) for k, _ in pairs),
        values=tuple(float(v) for _, v in pairs),
        node_types=None,
        expirations=("2026-03-05",),
        resolution="1s",
        source_endpoint="range",
        extra_json="{}",
    )


def labels(m: RawMap) -> NodeLabels:
    king = max(m.nodes, key=lambda p: abs(p[1]))[0] if m.nodes else None
    return NodeLabels(
        king=None if king is None else float(king),
        nodes=tuple(sorted(float(k) for k, _ in m.nodes)),
        floor=None if m.floor is None else float(m.floor),
        ceiling=None,
        gatekeepers=(),
        air_pockets=(),
        clear_skies=False,
        empty_basement=m.empty_basement,
    )


def converted(
    symbol: str, metric: Metric, m: RawMap, half_width: Fraction
) -> ConvertedMap | MissingPrice:
    """The Level_Converter map in the symbol's own family (offset method)."""
    family = family_of(symbol)
    instrument = INSTRUMENT[family]
    if m.offset is None:
        return MissingPrice(f"{instrument}H6")
    strikes = sorted(k for k, _ in (*m.nodes, *m.extras))
    levels = tuple(tick_round(k + m.offset) for k in strikes)
    as_of = T - m.age_ns
    conversion = Conversion(
        symbol=symbol,
        family=family,
        instrument=instrument,
        contract=f"{instrument}H6",
        method="offset",
        factor=float(m.offset),
        futures_close=float(m.spot + m.offset),
        spot=float(m.spot),
        as_of_ns=as_of,
        paired_bar_close_ns=as_of,
    )
    hw = float(half_width)
    return ConvertedMap(
        symbol=symbol,
        metric=metric,
        as_of_ns=as_of,
        conversion=conversion,
        band_half_width_pts=hw,
        strikes=tuple(float(k) for k in strikes),
        levels=levels,
        bands=tuple((x / TICKS_PER_POINT - hw, x / TICKS_PER_POINT + hw) for x in levels),
    )


def bar(instrument: str, open_ns: int, price: int) -> Bar:
    p = price / TICKS_PER_POINT
    return Bar(
        instrument, f"{instrument}H6", 60, open_ns, open_ns + 60 * NS, p, p + 1, p - 1, p,
        100.0, price, price + 4, price - 4, price, "atlas",
    )  # fmt: skip


def chart_of(raw: Raw) -> ChartFeatures:
    if not raw.chart:
        return ChartFeatures(T, ())
    events = BarEvents(
        60, bar(raw.instrument, T - 60 * NS, raw.entry), raw.candle, (), (), (), (), ()
    )
    none = WindowRange(Unavailable("empty"), Unavailable("empty"))
    p = raw.level / TICKS_PER_POINT
    feats = InstrumentFeatures(
        raw.instrument, SESSION, WindowRange(p + 3, p - 30), none, none, none, none,
        (), (), (events,),
    )  # fmt: skip
    return ChartFeatures(T, (feats,))


def context_of(raw: Raw, p: GateParams) -> GateContext:
    entries: dict[SymMetric, Snapshot | Unavailable] = {}
    present: dict[SymMetric, RawMap] = {}
    for key, m in raw.maps.items():
        if m is None:
            entries[key] = Unavailable(f"no {key[0]} {key[1]} Snapshot at or before t")
        else:
            entries[key] = snapshot(*key, m)
            present[key] = m
    node = (raw.source_symbol, raw.metric, float(raw.source_strike))
    taps: tuple[Tap, ...] = ()
    if raw.tap == "in_progress":
        start = T - 3 * NS_PER_MINUTE
        taps = (Tap(*node, raw.instrument, SESSION, raw.tap_seq, start, start + NS_PER_MINUTE, T),)
    no_snapshot = MissingInput((f"{raw.source_symbol} {raw.metric} Snapshot",))
    detect = DetectContext(
        t=T,
        map_state=MapState(T, entries),
        labels={k: labels(m) for k, m in present.items()},
        converted={k: converted(*k, m, raw.half_width) for k, m in present.items()},
        regime=no_snapshot
        if raw.regime_missing
        else RegimeResult("Positive_Gamma", 1.0, 0.5, 1.0, 0.5, 1.0, False),
        grade=no_snapshot if raw.grade_missing else "Neutral_Map",
        chart=chart_of(raw),
        taps=TapView(T, SESSION, taps, {}, {}),
        futures_price={} if raw.futures_missing else {raw.instrument: raw.entry},
    )

    cfg = p.gates.dark_pool_confluence
    ticker: str = cfg.es_ticker if raw.family == "ES" else cfg.nq_ticker
    dark_pool: dict[str, tuple[DarkPoolPrint, ...] | Unavailable] = {}
    if raw.dark_pool == "unavailable":
        dark_pool[ticker] = Unavailable("the coverage report lists the session as missing")
    elif raw.dark_pool == "prints":
        dark_pool[ticker] = (DarkPoolPrint(ticker, T - NS, 500.0, 4000, 2.0e6, "D"),)

    minute_bars: dict[str, tuple[Bar, ...]] = {}
    if raw.bars != "absent":
        opens = [T - 5 * NS_PER_MINUTE]
        if raw.bars == "since_tap":
            opens += [T - 3 * NS_PER_MINUTE, T - 2 * NS_PER_MINUTE]
        minute_bars[raw.instrument] = tuple(
            bar(raw.instrument, o, raw.entry + raw.sign * 4 * i) for i, o in enumerate(opens)
        )

    velocities: dict[SymMetric, dict[float, float | Unavailable]] = {}
    key = (raw.source_symbol, raw.metric)
    if raw.velocity == "no_strike":
        velocities[key] = {}
    elif raw.velocity == "unavailable":
        velocities[key] = {node[2]: Unavailable("no Snapshot a velocity window earlier")}
    elif raw.velocity == "value":
        velocities[key] = {node[2]: -5.0}

    return GateContext(
        detect,
        node_states={node: NodeState("Fresh")} if raw.node_state else {},
        velocities=velocities,
        vix=VixState(
            Unavailable("no VIX daily open") if raw.vix_open_missing else 16.5,
            Unavailable("no VIX prior close") if raw.vix_prior_missing else 16.0,
            16.2,
        ),
        dark_pool=dark_pool,
        minute_bars=minute_bars,
    )


def candidate(raw: Raw, ctx: GateContext, p: GateParams) -> CandidateSetup:
    """The priced Candidate_Setup, with the planned targets as its target prices."""
    src = raw.source_map
    assert src.offset is not None
    planned = plan_targets(basis(raw), mode_cfg(raw, p), target_context(raw))
    targets = (
        planned.prices if isinstance(planned, Targets) else (raw.entry + raw.sign * 3 * raw.risk,)
    )
    centre = Fraction(raw.level, TICKS_PER_POINT)
    band = (float(centre - raw.half_width), float(centre + raw.half_width))
    strike = float(raw.source_strike)
    key = SetupKey(raw.instrument, raw.pattern, strike, raw.direction, SESSION, raw.tap_seq)
    ref = SourceNodeRef(
        raw.source_symbol, raw.metric, strike, float(raw.source_value), raw.level, band
    )
    inputs = SetupInputs(
        map_as_of=ctx.detect.map_as_of,
        source_spot=float(src.spot),
        futures_price=ctx.detect.futures_price.get(raw.instrument, MissingPrice(raw.instrument)),
        conversion_method="offset",
        conversion_factor=float(src.offset),
        band_half_width_pts=float(raw.half_width),
        regime=ctx.detect.regime_value,
        map_grade=ctx.detect.grade,
        stop_rule="fixed_ticks",
    )
    return CandidateSetup(key, raw.detector, T, raw.entry, raw.stop, targets, raw.mode, ref, inputs)


def build(raw: Raw) -> Scenario:
    p = params_of(raw)
    ctx = context_of(raw, p)
    return Scenario(candidate(raw, ctx, p), ctx, p)


# ---------------------------------------------------------------- reference model


def basis(raw: Raw) -> TargetBasis:
    return TargetBasis(
        raw.direction, raw.entry, raw.stop, float(raw.source_strike), float(raw.source_value)
    )


def target_context(raw: Raw) -> TargetContext:
    """The source Snapshot's Nodes with their levels: what the Order_Planner plans from."""
    m = raw.source_map
    nodes: list[NodeLevel] = []
    for strike, value in m.nodes:
        level = m.level(strike)
        assert level is not None
        nodes.append(NodeLevel(float(strike), float(value), level))
    return TargetContext(tuple(nodes))


def mode_cfg(raw: Raw, p: GateParams) -> ExitModeCfg:
    """The setup's Exit_Mode: the global one, as no Regime has its own setting."""
    return ExitModeCfg(raw.mode, "fixed_ticks", p.exits.modes)


def expected_age(raw: Raw) -> tuple[int, bool]:
    """Snapshot_Age in ns and whether it exceeds the maximum (Req 5.7)."""
    age = max(m.age_ns for m in raw.maps.values() if m is not None)
    return age, age > raw.max_age_s * NS


def expected_reward_risk(raw: Raw, p: GateParams) -> Fraction | None:
    """Weighted reward / risk over the planned targets; ``None``: no target (Req 11.5, 11.7)."""
    planned = plan_targets(basis(raw), mode_cfg(raw, p), target_context(raw))
    if not isinstance(planned, Targets):
        return None
    to_tp1 = abs(planned.tp1 - raw.entry)
    if raw.mode == "tp1_partial_be":
        assert planned.tp2 is not None
        f = Fraction(raw.tp1_fraction)
        reward = f * to_tp1 + (1 - f) * abs(planned.tp2 - raw.entry)
    else:
        reward = Fraction(to_tp1)
    return reward / raw.risk


def expected_opposition(raw: Raw) -> int | None:
    """The nearest opposition level within the window, in ticks (Req 11.8)."""
    window = Fraction(raw.window_r) * raw.risk
    least = Fraction(raw.opp_fraction) * abs(Fraction(raw.source_value))
    m = raw.source_map
    best: int | None = None
    for strike, value in m.nodes:
        level = m.level(strike)
        assert level is not None
        beyond = raw.sign * (level - raw.entry)
        if strike == raw.source_strike or abs(Fraction(value)) < least:
            continue
        if 0 < beyond <= window and (best is None or beyond < raw.sign * (best - raw.entry)):
            best = level
    return best


def setup_level(raw: Raw, symbol: str, m: RawMap, strike: Fraction) -> int | None:
    """``strike`` of ``symbol`` as a level of the setup's instrument; ``None`` without a price."""
    if family_of(symbol) == raw.family:
        return m.level(strike)
    src = raw.source_map
    assert src.offset is not None
    if m.spot <= 0:
        return None
    return tick_round(strike * (src.spot + src.offset) / m.spot)


def in_band(raw: Raw, level: int | None) -> bool:
    """Whether ``level`` lies in the source Node's Deflection_Band, edges included."""
    if level is None:
        return False
    centre = Fraction(raw.level, TICKS_PER_POINT)
    return centre - raw.half_width <= Fraction(level, TICKS_PER_POINT) <= centre + raw.half_width


def trinity_symbols(raw: Raw) -> tuple[str, ...]:
    return TRINITY if raw.family == "ES" else raw.nq_sources


def agrees(raw: Raw, symbol: str) -> bool:
    m = raw.maps.get((symbol, raw.metric))
    if m is None:
        return False
    positive = raw.source_value > 0
    return any(
        (value > 0) == positive and in_band(raw, setup_level(raw, symbol, m, strike))
        for strike, value in m.nodes
    )


def empty_basement_floor(raw: Raw, symbol: str) -> bool:
    m = raw.maps.get((symbol, raw.metric))
    if m is None or m.floor is None or not m.empty_basement:
        return False
    return in_band(raw, setup_level(raw, symbol, m, m.floor))


def exception_holds(raw: Raw) -> bool:
    """The Empty_Basement exception (Req 11.10)."""
    return (
        raw.eb_exception
        and raw.pattern == FLOOR_CEILING
        and raw.direction == "long"
        and all(empty_basement_floor(raw, s) for s in ("QQQ", "SPY"))
    )


def expected_trinity(raw: Raw) -> tuple[int | None, bool]:
    """The agreeing count (``None``: a Snapshot is missing) and whether the Gate fails."""
    exception = exception_holds(raw)
    symbols = trinity_symbols(raw)
    if any(raw.maps.get((s, raw.metric)) is None for s in symbols):
        return None, not exception
    count = sum(agrees(raw, s) for s in symbols)
    return count, count < raw.min_agree and not exception


def missing_inputs(raw: Raw, p: GateParams) -> dict[str, bool | None]:
    """Per Gate, whether an input it needs at t is missing (Req 11.11); ``None``: no claim.

    Every Gate not listed reads only inputs every scenario gives (the source
    Snapshot, its labels and converted map, the Taps, the time, the calendar
    and the Risk_Manager state).
    """
    cfg = p.gates.dark_pool_confluence
    ticker = cfg.es_ticker if raw.family == "ES" else cfg.nq_ticker
    scale = raw.maps.get((ticker, raw.metric))
    dark_pool: bool | None = raw.dark_pool != "prints" or scale is None
    if not dark_pool and scale is not None and scale.offset is None:
        dark_pool = None  # a Level_Converter price, outside Req 11.11
    fade = raw.detector in p.gates.fade_detectors
    return {
        "map_grade": raw.grade_missing,
        "deflection_band": raw.futures_missing,
        "chart_confluence": not raw.chart,
        "stdev_fib_zone": not raw.chart,
        "dark_pool_confluence": dark_pool,
        "trinity_agreement": any(
            raw.maps.get((s, raw.metric)) is None for s in trinity_symbols(raw)
        ),
        "candle_color": not raw.chart or raw.candle is None,
        "sloppy_seconds": not raw.node_state,
        "fomo_travel": raw.tap == "in_progress" and raw.bars != "since_tap",
        "vix_gap": raw.vix_open_missing or raw.vix_prior_missing,
        "regime_match": raw.regime_missing,
        "node_growth_divergence": fade and raw.velocity != "value",
    }


def outcome(raw: Raw, gate_id: str, failed: bool) -> str:
    if gate_id not in raw.enabled:
        return "disabled"
    return "fail" if failed else "pass"


def assert_rejection(ev: GateEvaluation, r: GateResult) -> None:
    """Only a failing Gate has a Rejection_Reason, with its measured value and threshold."""
    found = [x for x in ev.rejections if x.reason == r.gate_id]
    wanted = [RejectionReason(r.gate_id, r.measured, r.threshold)] if r.result == "fail" else []
    assert found == wanted, r


# ---------------------------------------------------------------- generators


def decimals(lo: int, hi: int, places: int) -> st.SearchStrategy[Decimal]:
    """Decimals from ``lo`` to ``hi`` units of ``10 ** -places``."""
    return st.integers(lo, hi).map(lambda n: Decimal(n).scaleb(-places))


def scaled(n: int, e: int) -> Decimal:
    return Decimal(n).scaleb(e)


def signed(sign: int, magnitude: Decimal) -> Decimal:
    return sign * magnitude


def rarely() -> st.SearchStrategy[bool]:
    """True one time in four."""
    return st.integers(0, 3).map(lambda i: i == 0)


R_MULTIPLES: Final = st.one_of(decimals(5, 100, 1), decimals(50, 1000, 2))  # 0.5 to 10.0
OPP_FRACTIONS: Final = st.one_of(st.just(Decimal("0.85")), decimals(1, 100, 2))  # 0.01 to 1
MAGNITUDES: Final = st.builds(scaled, st.integers(1, 9999), st.integers(-2, 9))
SIGNS: Final = st.sampled_from((1, -1))


def split(
    draw: st.DrawFn,
    strikes: list[Fraction],
    values: st.SearchStrategy[Decimal],
    forced: Fraction | None,
) -> tuple[tuple[RawNode, ...], tuple[RawNode, ...]]:
    """Strikes with values: Nodes (``forced`` always one) and the strikes below the threshold."""
    nodes: list[RawNode] = []
    extras: list[RawNode] = []
    for k in strikes:
        (nodes if k == forced or draw(st.integers(0, 4)) > 0 else extras).append((k, draw(values)))
    return tuple(nodes), tuple(extras)


def floor_of(
    draw: st.DrawFn, nodes: tuple[RawNode, ...], forced: Fraction | None
) -> tuple[Fraction | None, bool]:
    """The Floor and Empty_Basement flag; ``forced`` is an Empty_Basement Floor."""
    if forced is not None:
        return forced, True
    floor = draw(st.none() | st.sampled_from([k for k, _ in nodes])) if nodes else None
    return floor, draw(st.booleans())


@st.composite
def raws(draw: st.DrawFn) -> Raw:
    # The setup.
    family = draw(st.sampled_from(FAMILIES))
    same, cross = SAME[family], CROSS[family]
    source_symbol = draw(st.sampled_from(same))
    metric = draw(st.sampled_from(METRICS))
    aim_eb = draw(rarely())  # aim at the Empty_Basement exception
    direction: Direction = "long" if aim_eb else draw(st.sampled_from(DIRECTIONS))
    detector = draw(st.sampled_from(FCB_DETECTORS if aim_eb else DETECTOR_IDS))
    sign = 1 if direction == "long" else -1
    hw_ticks = draw(st.integers(1, 100))
    level = draw(st.integers(16_000, 100_000))
    entry = level + draw(st.integers(-hw_ticks, hw_ticks))
    risk = draw(st.one_of(st.integers(1, 8), st.integers(9, 200)))

    # The exits section and the four Gates' parameters.
    mode = draw(st.sampled_from(MODES))
    fixed_r, tp1_r, tp2_r, window_r = (draw(R_MULTIPLES) for _ in range(4))
    tp1_rule, tp2_rule = (draw(st.sampled_from(TARGET_RULES)) for _ in range(2))
    rr_min = draw(st.one_of(decimals(5, 100, 1), st.sampled_from((fixed_r, tp1_r, tp2_r))))
    opp_fraction = draw(OPP_FRACTIONS)
    max_age_s = draw(st.integers(1, 300))
    limit = max_age_s * NS
    ages = st.one_of(st.integers(0, 400 * NS), st.sampled_from((limit - 1, limit, limit + 1)))

    # Node levels near entry, each R target, the window edge and the band edges.
    def r_ticks(r: Decimal) -> int:
        return max(1, math.floor(Fraction(r) * risk))

    reach = [0, *(r_ticks(r) for r in (fixed_r, tp1_r, tp2_r, Decimal(3)))]
    reach.append(math.floor(Fraction(window_r) * risk))
    marks = sorted({*(entry + sign * d for d in reach), level - hw_ticks, level, level + hw_ticks})
    levels = st.one_of(
        st.sampled_from(marks).flatmap(lambda x: st.integers(x - 1, x + 1)),
        st.integers(level - 800, level + 800),
    )

    # The cross-family ratio n/16 fixes the source map's futures close.
    ratio_n = draw(st.integers(16, 400))
    cross_spot = Fraction(draw(st.integers(400, 40_000)), TICKS_PER_POINT)
    futures_close = cross_spot * Fraction(ratio_n, 16)
    source_offset = Fraction(draw(st.integers(-200, 200)), TICKS_PER_POINT)
    source_strike = Fraction(level, TICKS_PER_POINT) - source_offset

    # Node values: short decimals, or exactly (or just below) the opposition threshold.
    source_abs = draw(MAGNITUDES)
    source_value = draw(SIGNS) * source_abs
    threshold = opp_fraction * source_abs
    just_below = threshold - Decimal(1).scaleb(threshold.adjusted() - 6)
    values = st.builds(
        signed, SIGNS, st.one_of(MAGNITUDES, st.just(threshold), st.just(just_below))
    )

    required = {(source_symbol, metric)}
    if aim_eb:
        required |= {("SPY", metric), ("QQQ", metric)}
    maps: dict[SymMetric, RawMap | None] = {}
    for symbol in (*same, cross):
        for m in METRICS:
            key: SymMetric = (symbol, m)
            case = "present" if key in required else draw(st.sampled_from(MAP_CASES))
            if case == "unavailable":
                maps[key] = None
            if case != "present":
                continue
            age = draw(ages)
            aim = aim_eb and m == metric and symbol in ("SPY", "QQQ")
            forced: Fraction | None = None
            if key == (source_symbol, metric):
                others = [x for x in draw(st.lists(levels, max_size=7, unique=True)) if x != level]
                strikes = [Fraction(x, TICKS_PER_POINT) - source_offset for x in others]
                nodes, extras = split(draw, strikes, values, None)
                nodes = ((source_strike, source_value), *nodes)
                floor, eb = floor_of(draw, nodes, source_strike if aim else None)
                spot = futures_close - source_offset
                maps[key] = RawMap(age, spot, source_offset, nodes, extras, floor, eb)
            elif family_of(symbol) == family:
                offset = Fraction(draw(st.integers(-400, 400)), TICKS_PER_POINT)
                lvls = draw(st.lists(levels, max_size=6, unique=True))
                if aim:
                    target = level + draw(st.integers(-hw_ticks, hw_ticks))
                    lvls = [target, *(x for x in lvls if x != target)]
                    forced = Fraction(target, TICKS_PER_POINT) - offset
                strikes = [Fraction(x, TICKS_PER_POINT) - offset for x in lvls]
                nodes, extras = split(draw, strikes, values, forced)
                floor, eb = floor_of(draw, nodes, forced)
                priced = aim or not draw(rarely())
                spot = Fraction(draw(st.integers(400, 400_000)), TICKS_PER_POINT)
                maps[key] = RawMap(age, spot, offset if priced else None, nodes, extras, floor, eb)
            else:
                # Strikes m/64 whose scaled levels land near the drawn levels.
                ms: list[int] = []
                targets = [level] if aim else []
                for i, x in enumerate([*targets, *draw(st.lists(levels, max_size=6))]):
                    jitter = 0 if aim and i == 0 else draw(st.integers(-3, 3))
                    mm = math.floor(Fraction(256 * x, ratio_n) + Fraction(1, 2)) + jitter
                    if mm not in ms:
                        ms.append(mm)
                strikes = [Fraction(mm, 64) for mm in ms]
                forced = strikes[0] if aim else None
                nodes, extras = split(draw, strikes, values, forced)
                floor, eb = floor_of(draw, nodes, forced)
                own = None if draw(rarely()) else Fraction(draw(st.integers(-400, 400)), 4)
                maps[key] = RawMap(age, cross_spot, own, nodes, extras, floor, eb)

    nq_sources: tuple[str, ...] = DEFAULT_NQ_SOURCES
    if family == "NQ":
        more = draw(st.lists(st.sampled_from(("NDX", "NDXP")), unique=True))
        nq_sources = tuple(draw(st.permutations(("QQQ", *more))))

    return Raw(
        family=family,
        source_symbol=source_symbol,
        metric=metric,
        source_strike=source_strike,
        direction=direction,
        detector=detector,
        level=level,
        entry=entry,
        risk=risk,
        half_width=Fraction(hw_ticks, TICKS_PER_POINT),
        maps=maps,
        enabled=frozenset(g for g in GATE_IDS if not draw(rarely())),
        tap_seq=draw(st.integers(1, 3)),
        mode=mode,
        fixed_r=fixed_r,
        tp1_rule=tp1_rule,
        tp1_r=tp1_r,
        tp2_rule=tp2_rule,
        tp2_r=tp2_r,
        tp1_fraction=draw(decimals(10, 90, 2)),
        max_age_s=max_age_s,
        rr_min=rr_min,
        opp_fraction=opp_fraction,
        window_r=window_r,
        min_agree=draw(st.integers(1, 3)),
        eb_exception=not draw(rarely()),
        nq_sources=nq_sources,
        grade_missing=draw(rarely()),
        regime_missing=draw(rarely()),
        futures_missing=draw(rarely()),
        chart=not draw(rarely()),
        candle=draw(st.sampled_from(CANDLES)),
        dark_pool=draw(st.sampled_from(DARK_POOL_CASES)),
        node_state=not draw(rarely()),
        velocity=draw(st.sampled_from(VELOCITY_CASES)),
        vix_open_missing=draw(rarely()),
        vix_prior_missing=draw(rarely()),
        tap=draw(st.sampled_from(TAP_CASES)),
        bars=draw(st.sampled_from(BARS_CASES)),
    )


# ---------------------------------------------------------------- explicit examples

S90: Final = 90 * NS
S30: Final = 30 * NS

# ES long at the SPX 5750 Floor (offset 0, futures close 5800, band 5745-5755,
# risk 28 ticks). 5771 (1.7e9, exactly 85% of 2.0e9) is 84 ticks out: exactly
# 3R, so it is opposition and the Next_Node target (reward:risk exactly 3.0).
# SPY 575 (+5175) and QQQ 718.75 (x 5800/725 = 8) are Barney Empty_Basement
# Floors at 5750: one symbol agrees, but the exception passes trinity_agreement.
# The SPX Snapshot is exactly 90 s old.
ES_EXCEPTION_AT_EDGES: Final = Raw(
    family="ES",
    source_symbol="SPX",
    metric="gamma",
    source_strike=Fraction(5750),
    direction="long",
    detector="floor_ceiling_bounce",
    level=23000,
    entry=23000,
    risk=28,
    half_width=Fraction(5),
    maps={
        ("SPX", "gamma"): RawMap(
            S90,
            Fraction(5800),
            Fraction(0),
            (
                (Fraction(5750), Decimal("2.0e9")),
                (Fraction(5771), Decimal("1.7e9")),
                (Fraction(5800), Decimal("-3.0e9")),
            ),
            floor=Fraction(5750),
        ),
        ("SPY", "gamma"): RawMap(
            S30,
            Fraction(580),
            Fraction(5175),
            ((Fraction(575), Decimal("-2.0e9")), (Fraction(590), Decimal("3.0e9"))),
            floor=Fraction(575),
            empty_basement=True,
        ),
        ("QQQ", "gamma"): RawMap(
            S30,
            Fraction(725),
            Fraction(20500),
            ((Fraction(2875, 4), Decimal("-2.0e9")), (Fraction(740), Decimal("3.0e9"))),
            floor=Fraction(2875, 4),
            empty_basement=True,
        ),
    },
)

# NQ short whipsaw_fade at the QQQ vanna 500 Barney (offset 20500, band 20980-21020,
# risk 40): 495 (-1.4e9) is 20 ticks beyond entry. Trailing plans no target. The
# Snapshot is 200 s old, NDX vanna is absent and NDXP vanna is marked
# unavailable, and every optional input is missing; vix_gap and sloppy_seconds
# are disabled.
NQ_EVERY_INPUT_MISSING: Final = Raw(
    family="NQ",
    source_symbol="QQQ",
    metric="vanna",
    source_strike=Fraction(500),
    direction="short",
    detector="whipsaw_fade",
    level=84000,
    entry=84000,
    risk=40,
    half_width=Fraction(20),
    maps={
        ("QQQ", "vanna"): RawMap(
            200 * NS,
            Fraction(505),
            Fraction(20500),
            ((Fraction(495), Decimal("-1.4e9")), (Fraction(500), Decimal("-1.5e9"))),
        ),
        ("NDXP", "vanna"): None,
    },
    enabled=frozenset(GATE_IDS) - {"vix_gap", "sloppy_seconds"},
    mode="trailing",
    grade_missing=True,
    regime_missing=True,
    futures_missing=True,
    chart=False,
    dark_pool="unavailable",
    node_state=False,
    velocity="unavailable",
    vix_open_missing=True,
    vix_prior_missing=True,
    tap="in_progress",
    bars="absent",
)

# The first example from the SPY 575 Floor (+5175, spot 625 so the futures close
# stays 5800) with the SPX Snapshot marked unavailable: the count is
# data_unavailable, and the exception still passes trinity_agreement.
ES_EXCEPTION_WITHOUT_SPX: Final = replace(
    ES_EXCEPTION_AT_EDGES,
    source_symbol="SPY",
    source_strike=Fraction(575),
    maps={
        ("SPX", "gamma"): None,
        ("SPY", "gamma"): RawMap(
            S30,
            Fraction(625),
            Fraction(5175),
            ((Fraction(575), Decimal("-2.0e9")), (Fraction(590), Decimal("3.0e9"))),
            floor=Fraction(575),
            empty_basement=True,
        ),
        ("QQQ", "gamma"): ES_EXCEPTION_AT_EDGES.maps[("QQQ", "gamma")],
    },
)


# ---------------------------------------------------------------- property


# Feature: skylit-futures-strategy-engine, Property 36: Gate measurements
@example(raw=ES_EXCEPTION_AT_EDGES)
@example(raw=NQ_EVERY_INPUT_MISSING)
@example(raw=ES_EXCEPTION_WITHOUT_SPX)
@given(raw=raws())
def test_gate_measurements_follow_the_requirements(raw: Raw) -> None:
    s = build(raw)
    ev = evaluate(s.setup, s.ctx, s.params)

    # stale_map (5.7): Snapshot_Age against the maximum.
    age, stale = expected_age(raw)
    r = ev.result("stale_map")
    assert (r.measured, r.threshold) == (age / NS, raw.max_age_s)
    assert r.result == outcome(raw, "stale_map", stale)
    assert_rejection(ev, r)
    event(f"stale_map {'over' if stale else 'within'} the maximum")

    # min_reward_risk (11.5-11.7): weighted reward / risk, or no_target.
    ratio = expected_reward_risk(raw, s.params)
    r = ev.result("min_reward_risk")
    if ratio is None:
        assert isinstance(r.measured, NoPlannedTarget)
        assert r.measured.kind == "no_target"
    else:
        assert r.measured == float(ratio)
    assert r.threshold == float(raw.rr_min)
    low = ratio is None or ratio < Fraction(raw.rr_min)
    assert r.result == outcome(raw, "min_reward_risk", low)
    assert_rejection(ev, r)
    event(f"min_reward_risk {'no_target' if ratio is None else 'below' if low else 'at or above'}")

    # opposition_inside_target (11.8): the nearest opposition level in the window.
    nearest = expected_opposition(raw)
    r = ev.result("opposition_inside_target")
    if nearest is None:
        assert isinstance(r.measured, NoneFound)
    else:
        assert r.measured == nearest / TICKS_PER_POINT
    assert r.threshold == float(raw.window_r)
    assert r.result == outcome(raw, "opposition_inside_target", nearest is not None)
    assert_rejection(ev, r)
    event(f"opposition {'found' if nearest is not None else 'none'}")

    # trinity_agreement (11.9-11.10): the agreeing count and the exception.
    count, disagree = expected_trinity(raw)
    r = ev.result("trinity_agreement")
    if count is None:
        assert isinstance(r.measured, DataUnavailable)
    else:
        assert type(r.measured) is int
        assert r.measured == count
    assert r.threshold == raw.min_agree
    assert r.result == outcome(raw, "trinity_agreement", disagree)
    assert_rejection(ev, r)
    exception = exception_holds(raw)
    event(
        f"trinity {'unavailable' if count is None else count}{' + exception' if exception else ''}"
    )

    # data_unavailable (11.11): exactly the Gates missing an input, failing when enabled.
    claims = missing_inputs(raw, s.params)
    for r in ev.results:
        claim = claims.get(r.gate_id, False)
        if claim is None:
            continue
        assert isinstance(r.measured, DataUnavailable) == claim, r
        if claim and r.gate_id != "trinity_agreement":  # trinity: the exception, above
            assert r.result == outcome(raw, r.gate_id, True), r
            assert_rejection(ev, r)
    event(f"{sum(1 for v in claims.values() if v)} Gates missing an input")
