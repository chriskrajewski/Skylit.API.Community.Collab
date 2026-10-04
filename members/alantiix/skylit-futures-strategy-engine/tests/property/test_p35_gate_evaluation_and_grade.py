"""Property 35: Gate evaluation completeness and Grade.

*For any* Candidate_Setup, context and Gate configuration (any enabled subset,
any order), the Gate_Evaluator records exactly one result for each of the 27
Gates in the configured order, including Gates after a failure; disabled Gates
are recorded ``disabled`` with their measured value and do not affect the
Grade; and the Grade is A_Plus when no enabled Gate fails, Alert_2R when
min_reward_risk is the only failing enabled Gate with a measured value of at
least 2.0, and Pass otherwise.

What :func:`evaluate` is checked against:

- the record (Req 11.2-11.4, 19.2): one result per Gate, in ``gates.order``,
  each with the Setup_Key, the Decision_Time, the Gate id, and the measured
  value and threshold the Gate measures with every Gate enabled in the default
  order (so neither the enabled flag nor the order changes a measurement). The
  result is ``disabled`` for a disabled Gate, else ``fail`` exactly when that
  measurement fails, else ``pass``;
- the Rejection_Reasons (Req 19.2): one per failing enabled Gate, in Gate
  order, with its measured value and threshold. An unpriced Candidate_Setup
  has ``no_conversion_price`` first, naming the missing price (Req 8.11);
- the Grade (Req 11.12), restated in :func:`req_11_12_grade` from the failing
  enabled Gates alone. An unpriced Candidate_Setup always grades Pass: it has
  no entry to place (Req 8.11, Operator decision), so "every Gate disabled
  gives A_Plus" applies to priced setups.

Generators: the scene of ``tests/unit/test_engine_gates.py`` (SPX, SPY and QQQ
gamma Maps, SPX offset 0, band half-width 5), where a gatekeeper_fade long at
the 5750 Floor at 10:00 in Positive_Gamma passes all 27 Gates. A scenario
moves up to 8 (half the time at most 2) of 27 knobs off that base: the source
strike, direction, detector, entry offset, Exit_Mode, tap_seq and pricing; the
time of day, Snapshot age, Map_Grade, Regime, Futures_Price, SPY and QQQ Maps,
chart (candle, BOS_Legs, no features), dark-pool prints, weekly Taps,
lifecycle label, Dormant, Node_Velocity, VIX, releases, Lockouts and a Tap in
progress; and three Gate thresholds. Each Gate passes on the base and fails
under at least one knob. The order is any permutation; the enabled set is all, none or
any subset. Explicit examples: every Gate disabled over failing measurements
(A_Plus); min_reward_risk alone failing at exactly 2.0R (Alert_2R) and at 1.5R
(Pass); an unpriced setup with every Gate disabled (Pass); the reversed order
with two failures.

**Validates: Requirements 11.2, 11.3, 11.4, 11.12, 19.2**
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, time
from typing import Any, Final, Literal

from hypothesis import event, example, given
from hypothesis import strategies as st

from fse.config.schema.exits import ExitModeName, ExitsConfig
from fse.config.schema.gates import GATE_IDS, GateId, GatesConfig
from fse.config.schema.nodes import NodesConfig
from fse.engine.chart import (
    BarEvents,
    BosLeg,
    CandleLabel,
    ChartFeatures,
    InstrumentFeatures,
    WindowRange,
)
from fse.engine.gates.catalog import (
    GateContext,
    GateParams,
    Measured,
    Measurement,
    NoConversionPrice,
)
from fse.engine.gates.registry import GRADES, NO_CONVERSION_PRICE, Grade, evaluate, gate
from fse.engine.levels import Conversion, ConvertedMap, band, level_family
from fse.engine.lifecycle import NodeState
from fse.engine.nodes import NodeParams, Velocities, classify
from fse.engine.regime import RegimeResult
from fse.engine.risk import Lockout, RiskState
from fse.engine.setups.base import DetectContext
from fse.engine.taps import NodeId, Tap, TapView
from fse.engine.targets import TargetBasis, TargetContext, Targets, exit_mode_for, plan_targets
from fse.engine.types import (
    Bar,
    CandidateSetup,
    ConversionMethod,
    DarkPoolPrint,
    Direction,
    EconomicEvent,
    MapGrade,
    MissingInput,
    MissingPrice,
    Regime,
    SetupInputs,
    SetupKey,
    Snapshot,
    SourceNodeRef,
    StopRule,
    Unavailable,
    VixState,
    direction_sign,
)
from fse.pit.protocols import MapState, SymMetric
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, ny_instant

ALERT_2R_MIN: Final = 2.0
"""Req 11.12: Alert_2R needs a min_reward_risk measured value of at least 2.0."""

SESSION: Final = date(2026, 3, 5)
CONTRACT: Final = "MESH6"
NODE_PARAMS: Final = NodeParams.from_config(NodesConfig())
SPX: Final[SymMetric] = ("SPX", "gamma")
FLOOR: Final = 5750.0

type Pairs = tuple[tuple[float, float], ...]
type PairsCase = Literal["agree", "empty_basement", "no_qqq"]
type LegsCase = Literal["near", "far", "none"]
type DarkPoolCase = Literal["near", "small", "absent", "unavailable"]
type NodeStateCase = Literal["fresh", "sloppy", "unlabeled"]
type VelocityCase = Literal["shrinking", "growing", "unavailable", "absent"]
type VixCase = Literal["flat", "gap", "missing"]
type LockoutCase = Literal["none", "active", "expired"]
type FomoCase = Literal["none", "travel", "no_bars"]

# Spot 5800, every strike a Node: Floor 5750, Gatekeepers 5780 and 5820, King and
# Ceiling 5850; Air_Pockets (5700, 5750), (5750, 5780) and (5820, 5850).
SPX_PAIRS: Final[Pairs] = (
    (5700.0, -1.0e9),
    (5750.0, 2.0e9),
    (5780.0, 1.0e9),
    (5800.0, 0.9e9),
    (5820.0, 1.5e9),
    (5850.0, -3.0e9),
)
# "agree": SPY's 575 Node (MES 5750) agrees with the 5750 Floor, QQQ's do not:
# a count of 2. "empty_basement": SPY 575 and QQQ 496 are Empty_Basement Floors
# in the band, a count of 1. "no_qqq": no QQQ Snapshot.
PAIRS: Final[Mapping[PairsCase, Mapping[str, Pairs]]] = {
    "agree": {
        "SPX": SPX_PAIRS,
        "SPY": ((575.0, 2.0e9), (590.0, -3.0e9)),
        "QQQ": ((490.0, 2.0e9), (510.0, -3.0e9)),
    },
    "empty_basement": {
        "SPX": SPX_PAIRS,
        "SPY": ((575.0, -2.0e9), (590.0, 3.0e9)),
        "QQQ": ((496.0, -2.0e9), (510.0, 3.0e9)),
    },
    "no_qqq": {"SPX": SPX_PAIRS, "SPY": ((575.0, 2.0e9), (590.0, -3.0e9))},
}
SPECS: Final[Mapping[str, tuple[float, ConversionMethod, float, float]]] = {
    "SPX": (5800.0, "offset", 0.0, 5.0),
    "SPY": (580.0, "ratio", 10.0, 5.0),
    "QQQ": (500.0, "ratio", 40.0, 20.0),
}
DETECTORS: Final = (
    "floor_ceiling_bounce",
    "floor_ceiling_bounce_empty_basement",
    "beach_ball",
    "rug",
    "reverse_rug",
    "whipsaw_fade",
    "trend_follow",
)
VIX: Final[Mapping[VixCase, VixState]] = {
    "flat": VixState(16.0, 16.0, 16.0),
    "gap": VixState(18.4, 16.0, 18.0),  # a 15% gap
    "missing": VixState(Unavailable("no VIX daily open"), 16.0, 18.0),
}
NO_WINDOW: Final = WindowRange(Unavailable("empty"), Unavailable("empty"))


# ---------------------------------------------------------------- scenario


@dataclass(frozen=True, slots=True)
class Scenario:
    """One Candidate_Setup, its context and its Gate configuration; the defaults are the base.

    ``entry_offset`` is in ticks in the trade direction from the source level;
    ``exit_mode`` is the global Exit_Mode with its Fixed_R multiple. ``None``
    means missing for ``grade``, ``regime`` and ``futures``, no chart features
    for ``candle`` and no release for ``news`` (event type, minutes from ``t``).
    """

    order: tuple[GateId, ...] = GATE_IDS
    enabled: frozenset[GateId] = frozenset(GATE_IDS)
    rr_min: float = 3.0
    min_agree: int = 2
    gk_fail_at: int = 2
    unpriced: bool = False
    strike: float = FLOOR
    direction: Direction = "long"
    detector: str = "gatekeeper_fade"
    entry_offset: int = 0
    exit_mode: tuple[ExitModeName, float] = ("next_node", 3.0)
    tap_seq: int = 1
    wall: time = time(10, 0)
    age_s: int = 30
    grade: MapGrade | None = "Neutral_Map"
    regime: Regime | None = "Positive_Gamma"
    futures: float | None = 5752.0
    pairs: PairsCase = "agree"
    candle: CandleLabel | None = "red"
    legs: LegsCase = "near"
    dark_pool: DarkPoolCase = "near"
    prior_week: int = 0
    node_state: NodeStateCase = "fresh"
    dormant: bool = False
    velocity: VelocityCase = "shrinking"
    vix: VixCase = "flat"
    news: tuple[str, int] | None = None
    lockout: LockoutCase = "none"
    fomo: FomoCase = "none"


BASE: Final = Scenario()

# The values each knob moves to; the Gates each one can fail are in brackets.
ALTERNATIVES: Final[Mapping[str, tuple[Any, ...]]] = {
    "rr_min": (2.0, 4.5, 20.0),  # [min_reward_risk]
    "min_agree": (1, 3),  # [trinity_agreement]
    "gk_fail_at": (1,),  # [gatekeepers_on_path]
    "unpriced": (True,),  # [every Gate that needs a price]
    "strike": (5700.0, 5780.0, 5800.0, 5820.0, 5850.0),  # [midpoint, band Gates, ...]
    "direction": ("short",),
    "detector": DETECTORS,  # [third_gatekeeper_test, regime_match, late_session_chase]
    "entry_offset": (-12, 12, 40),  # [air_pocket_fade, midpoint]
    "exit_mode": (  # [min_reward_risk, gatekeepers_on_path, fomo_travel]
        ("fixed_r", 1.5),
        ("fixed_r", 2.0),
        ("fixed_r", 2.5),
        ("fixed_r", 5.0),
        ("tp1_partial_be", 3.0),
        ("opposition_or_fixed_r", 3.0),
        ("trailing", 3.0),
    ),
    "tap_seq": (2, 3),  # [tap_count]
    "wall": (time(9, 40), time(12, 30), time(15, 25), time(15, 45)),  # [time Gates, vix_gap]
    "age_s": (90, 91),  # [stale_map]
    "grade": ("A_Plus_Map", "F_Map", None),  # [map_grade]
    "regime": ("Negative_Gamma", "Vanna_Dominant", "Whipsaw", "Structureless", None),
    "futures": (5740.0, 5760.0, None),  # [deflection_band]
    "pairs": ("empty_basement", "no_qqq"),  # [trinity_agreement]
    "candle": ("green", "doji", None),  # [candle_color, chart Gates]
    "legs": ("far", "none"),  # [stdev_fib_zone]
    "dark_pool": ("small", "absent", "unavailable"),  # [dark_pool_confluence]
    "prior_week": (1, 2),  # [weekly_node_tests, third_gatekeeper_test]
    "node_state": ("sloppy", "unlabeled"),  # [sloppy_seconds]
    "dormant": (True,),  # [dormant_node]
    "velocity": ("growing", "unavailable", "absent"),  # [node_growth_divergence]
    "vix": ("gap", "missing"),  # [vix_gap]
    "news": (("CPI", 5), ("FOMC", -5), ("NFP", 6), ("PPI", 0)),  # [news_window]
    "lockout": ("active", "expired"),  # [kill_switch_lockout]
    "fomo": ("travel", "no_bars"),  # [fomo_travel]
}
KNOBS: Final = tuple(sorted(ALTERNATIVES))


def _subset(flags: list[bool]) -> frozenset[GateId]:
    return frozenset(g for g, on in zip(GATE_IDS, flags, strict=True) if on)


ENABLED: Final = st.one_of(
    st.just(frozenset(GATE_IDS)),
    st.just(frozenset[GateId]()),
    st.lists(st.booleans(), min_size=len(GATE_IDS), max_size=len(GATE_IDS)).map(_subset),
)


@st.composite
def scenarios(draw: st.DrawFn) -> Scenario:
    """The base with up to 8 knobs moved (half the time at most 2), any order, any enabled set."""
    most = draw(st.sampled_from((2, 8)))
    moved = draw(st.lists(st.sampled_from(KNOBS), max_size=most, unique=True))
    changes: dict[str, Any] = {k: draw(st.sampled_from(ALTERNATIVES[k])) for k in moved}
    order = tuple(draw(st.permutations(GATE_IDS)))
    return replace(BASE, order=order, enabled=draw(ENABLED), **changes)


# ---------------------------------------------------------------- the scene


def pts(points: float) -> int:
    return round(points * 4)


def snapshot(symbol: str, pairs: Pairs, as_of: int) -> Snapshot:
    return Snapshot(
        symbol=symbol,
        metric="gamma",
        view_id="v",
        as_of_ns=as_of,
        as_of_raw="raw",
        spot=SPECS[symbol][0],
        previous_close=None,
        strikes=tuple(k for k, _ in pairs),
        values=tuple(v for _, v in pairs),
        node_types=None,
        expirations=("2026-03-05",),
        resolution="1s",
        source_endpoint="range",
        extra_json="{}",
    )


def converted(s: Snapshot) -> ConvertedMap:
    _, method, factor, half_width = SPECS[s.symbol]
    family = level_family(s.symbol)
    instrument = "MES" if family == "ES" else "MNQ"
    conv = Conversion(
        symbol=s.symbol,
        family=family,
        instrument=instrument,
        contract=f"{instrument}H6",
        method=method,
        factor=factor,
        futures_close=s.spot + factor if method == "offset" else s.spot * factor,
        spot=s.spot,
        as_of_ns=s.as_of_ns,
        paired_bar_close_ns=s.as_of_ns,
    )
    levels = tuple(conv.level(k) for k in s.strikes)
    return ConvertedMap(
        symbol=s.symbol,
        metric=s.metric,
        as_of_ns=s.as_of_ns,
        conversion=conv,
        band_half_width_pts=half_width,
        strikes=s.strikes,
        levels=levels,
        bands=tuple(band(level, half_width) for level in levels),
    )


def minute_bar(open_ns: int, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(
        "MES", CONTRACT, 60, open_ns, open_ns + 60 * NS_PER_SECOND, o, h, low, c, 100.0,
        pts(o), pts(h), pts(low), pts(c), "atlas",
    )  # fmt: skip


def chart(s: Scenario, t: int) -> ChartFeatures:
    """A 5753/5700 prior-RTH range and the latest candle; ``near`` legs put 5750 in a zone."""
    if s.candle is None:
        return ChartFeatures(t, ())
    origin, terminal = (5720.0, 5730.0) if s.legs == "near" else (5700.0, 5730.0)
    leg = BosLeg("MES", 60, "bullish", origin, t - 600, t - 300, t - 240, origin, terminal)
    legs = () if s.legs == "none" else (leg,)
    bar = minute_bar(t - 60 * NS_PER_SECOND, 5753.0, 5754.0, 5751.0, 5752.0)
    events = BarEvents(60, bar, s.candle, (), (), (), (), ())
    feats = InstrumentFeatures(
        "MES", SESSION, WindowRange(5753.0, 5700.0), NO_WINDOW, NO_WINDOW, NO_WINDOW, NO_WINDOW,
        (), legs, (events,),
    )  # fmt: skip
    return ChartFeatures(t, (feats,))


def dark_pool(case: DarkPoolCase, t: int) -> dict[str, tuple[DarkPoolPrint, ...] | Unavailable]:
    if case == "absent":
        return {}
    if case == "unavailable":
        return {"SPY": Unavailable("not in coverage")}
    notional = 2.0e6 if case == "near" else 5.0e5
    return {"SPY": (DarkPoolPrint("SPY", t - 1, 575.1, 1, notional, "D"),)}


def risk(case: LockoutCase, t: int) -> RiskState:
    if case == "active":
        return RiskState(
            session=SESSION, lockouts=(Lockout("max_losers", t - NS_PER_MINUTE, SESSION),)
        )
    if case == "expired":
        old = Lockout("red_day", t - 86_400 * NS_PER_SECOND, date(2026, 3, 4))
        return RiskState(session=SESSION, lockouts=(old,))
    return RiskState()


def context(s: Scenario) -> GateContext:
    t = ny_instant(SESSION, s.wall)
    as_of = t - s.age_s * NS_PER_SECOND
    snaps: dict[SymMetric, Snapshot] = {}
    for symbol, pairs in PAIRS[s.pairs].items():
        snaps[(symbol, "gamma")] = snapshot(symbol, pairs, as_of)
    maps: dict[SymMetric, ConvertedMap | MissingPrice] = {k: converted(v) for k, v in snaps.items()}
    if s.unpriced:
        maps[SPX] = MissingPrice(CONTRACT)
    node: NodeId = ("SPX", "gamma", s.strike)
    start = t - 3 * NS_PER_MINUTE
    taps: tuple[Tap, ...] = ()
    if s.fomo != "none":
        tap = Tap(
            "SPX", "gamma", s.strike, "MES", SESSION, s.tap_seq, start, start + NS_PER_MINUTE, t
        )
        taps = (tap,)
    bars: dict[str, tuple[Bar, ...]] = {}
    if s.fomo == "travel":
        highs = (5755.0, 5770.0, 5760.0)
        bars["MES"] = tuple(
            minute_bar(start + i * NS_PER_MINUTE, 5751.0, h, 5749.0, 5751.0)
            for i, h in enumerate(highs)
        )
    regime = (
        MissingInput(("SPX vanna Snapshot",))
        if s.regime is None
        else RegimeResult(s.regime, 1.0, 0.5, 1.0, 0.5, 1.0, False)
    )
    detect = DetectContext(
        t=t,
        map_state=MapState(t, snaps),
        labels={k: classify(v, NODE_PARAMS) for k, v in snaps.items()},
        converted=maps,
        regime=regime,
        grade=MissingInput(("SPX gamma Snapshot",)) if s.grade is None else s.grade,
        chart=chart(s, t),
        taps=TapView(t, SESSION, taps, {node: s.prior_week} if s.prior_week else {}, {}),
        futures_price={} if s.futures is None else {"MES": pts(s.futures)},
    )
    states: dict[NodeId, NodeState] = {}
    if s.node_state == "fresh":
        states[node] = NodeState("Fresh")
    elif s.node_state == "sloppy":
        states[node] = NodeState("Tested", t - 1)
    by_strike: dict[float, float | Unavailable] = {
        s.strike: -5.0
        if s.velocity == "shrinking"
        else 20.0
        if s.velocity == "growing"
        else Unavailable("no prior Snapshot")
    }
    velocities: dict[SymMetric, Velocities] = {} if s.velocity == "absent" else {SPX: by_strike}
    events = (
        ()
        if s.news is None
        else (EconomicEvent(s.news[0], SESSION, t + s.news[1] * NS_PER_MINUTE),)
    )
    return GateContext(
        detect,
        node_states=states,
        dormant=frozenset({node}) if s.dormant else frozenset(),
        velocities=velocities,
        vix=VIX[s.vix],
        events=events,
        dark_pool=dark_pool(s.dark_pool, t),
        minute_bars=bars,
        risk=risk(s.lockout, t),
    )


def exits_config(s: Scenario) -> ExitsConfig:
    """The scenario's Exit_Mode for every Regime (no per-Regime setting), fixed-ticks stops."""
    mode, r_multiple = s.exit_mode
    return ExitsConfig.model_validate(
        {
            "global": {"mode": mode, "stop_rule": "fixed_ticks"},
            "per_regime": {"Positive_Gamma": None},
            "modes": {"fixed_r": {"r_multiple": r_multiple}},
        }
    )


def gates_config(s: Scenario, order: Sequence[GateId], enabled: frozenset[GateId]) -> GatesConfig:
    raw: dict[str, Any] = {g: {"enabled": g in enabled} for g in GATE_IDS}
    raw["order"] = list(order)
    raw["min_reward_risk"]["min"] = s.rr_min
    raw["trinity_agreement"]["min_agree"] = s.min_agree
    raw["gatekeepers_on_path"]["fail_at"] = s.gk_fail_at
    return GatesConfig.model_validate(raw)


def setup_inputs(
    ctx: GateContext,
    factor: float | MissingPrice,
    half_width: float | MissingPrice,
    stop_rule: StopRule | None,
) -> SetupInputs:
    return SetupInputs(
        map_as_of=ctx.detect.map_as_of,
        source_spot=SPECS["SPX"][0],
        futures_price=ctx.detect.futures_price.get("MES", MissingPrice("MES")),
        conversion_method="offset",
        conversion_factor=factor,
        band_half_width_pts=half_width,
        regime=ctx.detect.regime_value,
        map_grade=ctx.detect.grade,
        stop_rule=stop_rule,
    )


def candidate(s: Scenario, ctx: GateContext, exits: ExitsConfig) -> CandidateSetup:
    """An SPX setup at ``s.strike``: an 8-tick fixed stop beyond the band and the planned targets.

    A plan with no target (Trailing, or no Node) gets a placeholder 40 ticks
    beyond entry, so the setup is valid; min_reward_risk replans and fails.
    """
    snap = ctx.detect.map_state.get("SPX", "gamma")
    assert isinstance(snap, Snapshot)
    value = snap.values[snap.strikes.index(s.strike)]
    mode = exit_mode_for(exits, ctx.detect.regime_value)
    pattern = "floor_ceiling_bounce" if s.detector.startswith("floor_ceiling") else s.detector
    key = SetupKey("MES", pattern, s.strike, s.direction, SESSION, s.tap_seq)
    cm = ctx.detect.converted[SPX]
    if isinstance(cm, MissingPrice):
        ref = SourceNodeRef("SPX", "gamma", s.strike, value, None, None)
        inputs = setup_inputs(ctx, cm, cm, None)
        return CandidateSetup(key, s.detector, ctx.t, None, None, (), mode.mode, ref, inputs)
    sign = direction_sign(s.direction)
    level, (lo, hi) = cm.level_of(s.strike), cm.band_of(s.strike)
    stop = math.floor(lo * 4) - 8 if s.direction == "long" else math.ceil(hi * 4) + 8
    entry = level + sign * s.entry_offset
    basis = TargetBasis(s.direction, entry, stop, s.strike, value)
    planned = plan_targets(
        basis, mode, TargetContext.from_snapshot(snap, ctx.detect.labels[SPX], cm)
    )
    targets = planned.prices if isinstance(planned, Targets) else (entry + sign * 40,)
    ref = SourceNodeRef("SPX", "gamma", s.strike, value, level, (lo, hi))
    inputs = setup_inputs(ctx, cm.conversion.factor, cm.band_half_width_pts, "fixed_ticks")
    return CandidateSetup(key, s.detector, ctx.t, entry, stop, targets, mode.mode, ref, inputs)


# ---------------------------------------------------------------- the oracle


def req_11_12_grade(priced: bool, failing: Sequence[GateId], reward_risk: Measured) -> Grade:
    """Req 11.12 from the failing enabled Gates and the min_reward_risk measured value.

    A_Plus with no failing enabled Gate (every Gate disabled included); Alert_2R
    when min_reward_risk is the only one, measured at 2.0 or more; else Pass.
    An unpriced Candidate_Setup is Pass: it has no entry to place (Req 8.11).
    """
    if not priced:
        return "Pass"
    if not failing:
        return "A_Plus"
    alone = list(failing) == ["min_reward_risk"]
    if alone and isinstance(reward_risk, float) and reward_risk >= ALERT_2R_MIN:
        return "Alert_2R"
    return "Pass"


# ---------------------------------------------------------------- the property


@example(replace(BASE, enabled=frozenset(), strike=5800.0, tap_seq=3, lockout="active"))
@example(replace(BASE, exit_mode=("fixed_r", 2.0)))
@example(replace(BASE, exit_mode=("fixed_r", 1.5)))
@example(replace(BASE, unpriced=True, enabled=frozenset()))
@example(replace(BASE, order=tuple(reversed(GATE_IDS)), regime="Negative_Gamma", tap_seq=3))
@given(scenarios())
def test_every_gate_is_recorded_in_order_and_the_grade_follows_req_11_12(s: Scenario) -> None:
    ctx = context(s)
    exits = exits_config(s)
    c = candidate(s, ctx, exits)
    params = GateParams(gates=gates_config(s, s.order, s.enabled), exits=exits)
    every_gate = GateParams(gates=gates_config(s, GATE_IDS, frozenset(GATE_IDS)), exits=exits)
    measured: dict[GateId, Measurement] = {g: gate(g).measure(c, ctx, every_gate) for g in GATE_IDS}

    ev = evaluate(c, ctx, params)

    # Req 11.2: all 27 Gates, each once, in the configured order, after failures too.
    assert (ev.setup_key, ev.t) == (c.key, ctx.t)
    assert [r.gate_id for r in ev.results] == list(s.order)
    assert sorted(r.gate_id for r in ev.results) == sorted(GATE_IDS)
    for r in ev.results:
        m = measured[r.gate_id]
        # Req 11.3: Setup_Key, Decision_Time, Gate id, result, measured value, threshold.
        assert (r.setup_key, r.t) == (c.key, ctx.t)
        assert (r.measured, r.threshold) == (m.measured, m.threshold), r.gate_id
        # Req 11.4: a disabled Gate is recorded disabled with its measured value.
        want = "disabled" if r.gate_id not in s.enabled else "fail" if m.failed else "pass"
        assert r.result == want, r.gate_id

    failing = [g for g in s.order if g in s.enabled and measured[g].failed]
    assert list(ev.failing) == failing

    # Req 19.2: one Rejection_Reason per failing enabled Gate, in Gate order.
    reasons = [(x.reason, x.measured, x.threshold) for x in ev.rejections]
    per_gate = [(g, measured[g].measured, measured[g].threshold) for g in failing]
    if c.priced:
        assert reasons == per_gate
    else:
        assert reasons[0][:2] == (NO_CONVERSION_PRICE, NoConversionPrice(CONTRACT))
        assert reasons[1:] == per_gate

    # Req 11.12: exactly one Grade, from the failing enabled Gates alone.
    rr = measured["min_reward_risk"].measured
    assert ev.grade in GRADES
    assert ev.grade == req_11_12_grade(c.priced, failing, rr)

    event(f"grade {ev.grade}")
    event("priced" if c.priced else "unpriced")
    event(f"failing enabled Gates: {min(len(failing), 3)}{'+' if len(failing) >= 3 else ''}")
