"""Unit tests for the Gate_Evaluator (design §11).

Synthetic SPX, SPY and QQQ gamma Maps with hand-built conversions: SPX with
offset 0 (a strike's MES level is the strike in points, 5750 is 23000 ticks,
band half-width 5), SPY with ratio 10 and QQQ with ratio 40. The base setup is
a floor_ceiling_bounce long at the 5750 Floor in a Positive_Gamma Regime, with
the 8-tick fixed stop (risk 28 ticks) and the Next_Node target 5780, and it
passes every Gate. Each test changes one input. Properties 35-36 (tasks
15.2-15.3) cover the general case.

**Validates: Requirements 5.7, 8.11, 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7,
11.8, 11.9, 11.10, 11.11, 11.12, 16.9, 19.2**
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import replace
from datetime import date, time
from typing import Any

import pytest

from fse.config.schema.exits import ExitsConfig
from fse.config.schema.gates import GATE_IDS, GatesConfig
from fse.config.schema.nodes import NodesConfig
from fse.config.schema.patterns import PatternsConfig
from fse.engine.chart import BarEvents, BosLeg, ChartFeatures, InstrumentFeatures, WindowRange
from fse.engine.gates.catalog import (
    DataUnavailable,
    GateContext,
    GateParams,
    LateChase,
    Measurement,
    NoConversionPrice,
    NoneFound,
    NoPlannedTarget,
    PatternExempt,
)
from fse.engine.gates.registry import (
    NO_CONVERSION_PRICE,
    REGISTRY,
    evaluate,
    gate,
    trinity_count,
)
from fse.engine.levels import Conversion, ConvertedMap, band, level_family
from fse.engine.lifecycle import NodeState
from fse.engine.nodes import NodeParams, classify
from fse.engine.regime import RegimeResult
from fse.engine.risk import Lockout, RiskState
from fse.engine.setups.registry import DetectContext, DetectorParams, detect_setups
from fse.engine.taps import Tap, TapView
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
    Unavailable,
    VixState,
)
from fse.pit.protocols import MapState, SymMetric
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, ny_instant

SESSION = date(2026, 3, 5)
T0 = ny_instant(SESSION, time(10, 0))
NODE_PARAMS = NodeParams.from_config(NodesConfig())
SPX: SymMetric = ("SPX", "gamma")
SPY: SymMetric = ("SPY", "gamma")
QQQ: SymMetric = ("QQQ", "gamma")
FLOOR = 5750.0
FLOOR_NODE = ("SPX", "gamma", FLOOR)

# Spot 5800, every strike a Node. Floor 5750 (not Empty_Basement), Gatekeepers
# 5780 and 5820, 5800 at spot, King and Ceiling 5850. Air_Pockets (5700, 5750),
# (5750, 5780) and (5820, 5850).
SPX_PAIRS: tuple[tuple[float, float], ...] = (
    (5700.0, -1.0e9),
    (5750.0, 2.0e9),
    (5780.0, 1.0e9),
    (5800.0, 0.9e9),
    (5820.0, 1.5e9),
    (5850.0, -3.0e9),
)
# SPY Floor 575 is a Pika at MES 5750: it agrees. QQQ's Nodes scale (x 5800/500)
# to 5684 and 5916, outside the band: it does not.
SPY_PAIRS = ((575.0, 2.0e9), (590.0, -3.0e9))
QQQ_PAIRS = ((490.0, 2.0e9), (510.0, -3.0e9))
SPECS: dict[str, tuple[float, ConversionMethod, float, float]] = {
    "SPX": (5800.0, "offset", 0.0, 5.0),
    "SPY": (580.0, "ratio", 10.0, 5.0),
    "QQQ": (500.0, "ratio", 40.0, 20.0),
}


def pts(points: float) -> int:
    return round(points * 4)


def snapshot(symbol: str, pairs: tuple[tuple[float, float], ...], as_of: int) -> Snapshot:
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
        "MES", "MESH6", 60, open_ns, open_ns + 60 * NS_PER_SECOND, o, h, low, c, 100.0,
        pts(o), pts(h), pts(low), pts(c), "atlas",
    )  # fmt: skip


NO_WINDOW = WindowRange(Unavailable("empty"), Unavailable("empty"))


def chart_at(t: int, *, candle: Any = "red", legs: tuple[BosLeg, ...] = ()) -> ChartFeatures:
    bar = minute_bar(t - 60 * NS_PER_SECOND, 5753.0, 5754.0, 5751.0, 5752.0)
    events = BarEvents(60, bar, candle, (), (), (), (), ())
    feats = InstrumentFeatures(
        "MES", SESSION, WindowRange(5753.0, 5700.0), NO_WINDOW, NO_WINDOW, NO_WINDOW, NO_WINDOW,
        (), legs, (events,),
    )  # fmt: skip
    return ChartFeatures(t, (feats,))


def context(
    t: int = T0,
    *,
    pairs: Mapping[str, tuple[tuple[float, float], ...]] | None = None,
    regime: Regime | MissingInput = "Positive_Gamma",
    grade: MapGrade | MissingInput = "Neutral_Map",
    futures: Mapping[str, int] | None = None,
    taps: tuple[Tap, ...] = (),
    prior_week: Mapping[Any, int] | None = None,
    chart: ChartFeatures | None = None,
    unpriced: bool = False,
    as_of_age_s: int = 30,
    **fields: Any,
) -> GateContext:
    chosen = {"SPX": SPX_PAIRS, "SPY": SPY_PAIRS, "QQQ": QQQ_PAIRS} if pairs is None else pairs
    as_of = t - as_of_age_s * NS_PER_SECOND
    snaps: dict[SymMetric, Snapshot] = {}
    for symbol, symbol_pairs in chosen.items():
        snaps[(symbol, "gamma")] = snapshot(symbol, symbol_pairs, as_of)
    maps: dict[SymMetric, ConvertedMap | MissingPrice] = {k: converted(v) for k, v in snaps.items()}
    if unpriced:
        maps[SPX] = MissingPrice("MESH6")
    detect = DetectContext(
        t=t,
        map_state=MapState(t, snaps),
        labels={k: classify(s, NODE_PARAMS) for k, s in snaps.items()},
        converted=maps,
        regime=regime if isinstance(regime, MissingInput) else regime_result(regime),
        grade=grade,
        chart=chart_at(t) if chart is None else chart,
        taps=TapView(t, SESSION, taps, dict(prior_week or {}), {}),
        futures_price={"MES": pts(5752.0)} if futures is None else futures,
    )
    defaults: dict[str, Any] = {
        "node_states": {FLOOR_NODE: NodeState("Fresh")},
        "velocities": {SPX: {FLOOR: -5.0}},
        "vix": VixState(16.0, 16.0, 16.0),
    }
    return GateContext(detect, **{**defaults, **fields})


def regime_result(regime: Regime) -> RegimeResult:
    return RegimeResult(regime, 1.0, 0.5, 1.0, 0.5, 1.0, False)


def setup(
    ctx: GateContext,
    strike: float = FLOOR,
    direction: Direction = "long",
    *,
    detector: str = "floor_ceiling_bounce",
    entry: int | None = None,
    targets: tuple[int, ...] | None = None,
    exits: ExitsConfig | None = None,
    tap_seq: int = 1,
) -> CandidateSetup:
    """A priced SPX setup with the 8-tick fixed stop and the planned targets."""
    snap = ctx.detect.map_state.get("SPX", "gamma")
    assert isinstance(snap, Snapshot)
    value = snap.values[snap.strikes.index(strike)]
    mode = exit_mode_for(exits or ExitsConfig(), ctx.detect.regime_value)
    pattern = "floor_ceiling_bounce" if detector.startswith("floor_ceiling") else detector
    key = SetupKey("MES", pattern, strike, direction, SESSION, tap_seq)
    cm = ctx.detect.converted[SPX]
    if isinstance(cm, MissingPrice):
        inputs = spx_inputs(ctx, cm, cm, None)
        ref = SourceNodeRef("SPX", "gamma", strike, value, None, None)
        return CandidateSetup(key, detector, ctx.t, None, None, (), mode.mode, ref, inputs)
    level, (lo, hi) = cm.level_of(strike), cm.band_of(strike)
    stop = math.floor(lo * 4) - 8 if direction == "long" else math.ceil(hi * 4) + 8
    entry = level if entry is None else entry
    if targets is None:
        basis = TargetBasis(direction, entry, stop, strike, value)
        tctx = TargetContext.from_snapshot(snap, ctx.detect.labels[SPX], cm)
        planned = plan_targets(basis, mode, tctx)
        targets = planned.prices if isinstance(planned, Targets) else (entry + 40,)
    inputs = spx_inputs(ctx, cm.conversion.factor, cm.band_half_width_pts, "fixed_ticks")
    ref = SourceNodeRef("SPX", "gamma", strike, value, level, (lo, hi))
    return CandidateSetup(key, detector, ctx.t, entry, stop, targets, mode.mode, ref, inputs)


def spx_inputs(ctx: GateContext, factor: Any, half_width: Any, stop_rule: Any) -> SetupInputs:
    return SetupInputs(
        map_as_of=ctx.detect.map_as_of,
        source_spot=5800.0,
        futures_price=ctx.detect.futures_price.get("MES", MissingPrice("MES")),
        conversion_method="offset",
        conversion_factor=factor,
        band_half_width_pts=half_width,
        regime=ctx.detect.regime_value,
        map_grade=ctx.detect.grade,
        stop_rule=stop_rule,
    )


def measure(gate_id: str, c: CandidateSetup, ctx: GateContext, **gates: Any) -> Measurement:
    return gate(gate_id).measure(c, ctx, GateParams(gates=GatesConfig.model_validate(gates)))


def all_disabled(**extra: Any) -> GatesConfig:
    cfg: dict[str, Any] = {g: {"enabled": False} for g in GATE_IDS}
    for g, keys in extra.items():
        cfg[g] = {**cfg[g], **keys}
    return GatesConfig.model_validate(cfg)


BASE = context()
BASE_SETUP = setup(BASE)


# ---------------------------------------------------------------- evaluation and Grade


def test_registry_order_and_unknown_id() -> None:
    assert tuple(g.id for g in REGISTRY) == GATE_IDS
    with pytest.raises(ValueError, match="vwap"):
        gate("vwap")


def test_base_setup_passes_every_gate_and_is_a_plus() -> None:
    ev = evaluate(BASE_SETUP, BASE, GateParams())
    assert [r.gate_id for r in ev.results] == list(GATE_IDS)
    assert {r.result for r in ev.results} == {"pass", "disabled"}
    assert [r.gate_id for r in ev.results if r.result == "disabled"] == [
        "stdev_fib_zone",
        "dark_pool_confluence",
    ]
    assert ev.failing == ()
    assert ev.rejections == ()
    assert ev.grade == "A_Plus"
    rr = ev.result("min_reward_risk")
    assert rr.measured == 120 / 28
    assert rr.threshold == 3.0
    assert (rr.setup_key, rr.t) == (BASE_SETUP.key, T0)
    assert trinity_count(ev) == 2


def test_disabled_gates_are_measured_and_kept_out_of_the_grade() -> None:
    ev = evaluate(BASE_SETUP, BASE, GateParams())
    assert ev.result("dark_pool_confluence").measured == DataUnavailable(
        "SPY dark-pool prints were not fetched"
    )
    assert ev.result("stdev_fib_zone").measured == NoneFound("active MES BOS_Leg")
    lock = Lockout("max_losers", T0 - NS_PER_MINUTE, SESSION)
    ctx = context(risk=RiskState(session=SESSION, lockouts=(lock,)))
    ev = evaluate(setup(ctx, tap_seq=5), ctx, GateParams(gates=all_disabled()))
    assert {r.result for r in ev.results} == {"disabled"}
    assert ev.result("kill_switch_lockout").measured == (lock,)
    assert ev.result("tap_count").measured == 5
    assert ev.grade == "A_Plus"
    assert ev.rejections == ()


def test_every_gate_runs_in_the_configured_order_after_failures() -> None:
    order = list(reversed(GATE_IDS))
    ctx = context(regime="Negative_Gamma")
    c = setup(ctx, tap_seq=3)
    ev = evaluate(c, ctx, GateParams(gates=GatesConfig(order=tuple(order))))
    assert [r.gate_id for r in ev.results] == order
    assert ev.failing == ("regime_match", "tap_count")
    assert [r.reason for r in ev.rejections] == ["regime_match", "tap_count"]
    assert ev.rejections[1].measured == 3
    assert ev.rejections[1].threshold == 2
    assert ev.grade == "Pass"


def test_alert_2r_when_min_reward_risk_alone_fails_at_2r_or_more() -> None:
    exits = ExitsConfig.model_validate(
        {
            "per_regime": {"Positive_Gamma": {"mode": "fixed_r", "stop_rule": "fixed_ticks"}},
            "modes": {"fixed_r": {"r_multiple": 2.5}},
        }
    )
    c = setup(BASE, exits=exits)
    ev = evaluate(c, BASE, GateParams(exits=exits))
    assert ev.failing == ("min_reward_risk",)
    assert ev.result("min_reward_risk").measured == 2.5
    assert ev.grade == "Alert_2R"
    strict = GatesConfig.model_validate({"min_reward_risk": {"alert_min": 2.6}})
    assert evaluate(c, BASE, GateParams(gates=strict, exits=exits)).grade == "Pass"
    ev = evaluate(setup(BASE, exits=exits, tap_seq=3), BASE, GateParams(exits=exits))
    assert ev.failing == ("tap_count", "min_reward_risk")
    assert ev.grade == "Pass"


def test_unpriced_setup_is_rejected_with_no_conversion_price() -> None:
    ctx = context(unpriced=True)
    c = setup(ctx)
    ev = evaluate(c, ctx, GateParams())
    assert ev.rejections[0].reason == NO_CONVERSION_PRICE
    assert ev.rejections[0].measured == NoConversionPrice("MESH6")
    assert ev.result("min_reward_risk").measured == NoConversionPrice("MESH6")
    assert ev.result("min_reward_risk").result == "fail"
    assert ev.result("stale_map").result == "pass"
    assert ev.grade == "Pass"
    ev = evaluate(c, ctx, GateParams(gates=all_disabled()))
    assert [r.reason for r in ev.rejections] == [NO_CONVERSION_PRICE]
    assert ev.grade == "Pass"


def test_detector_output_evaluates_in_its_context() -> None:
    found = detect_setups(BASE.detect, DetectorParams(patterns=PatternsConfig()))
    setups = [x for x in found if isinstance(x, CandidateSetup)]
    assert setups
    for c in setups:
        assert len(evaluate(c, BASE, GateParams()).results) == len(GATE_IDS)


def test_a_setup_from_another_context_raises() -> None:
    with pytest.raises(ValueError, match="evaluated in a context"):
        evaluate(BASE_SETUP, context(T0 + 60 * NS_PER_SECOND), GateParams())


# ---------------------------------------------------------------- measurements


def test_stale_map_measures_snapshot_age() -> None:
    assert measure("stale_map", BASE_SETUP, BASE) == Measurement(30.0, 90, False)
    old = context(as_of_age_s=91)
    assert measure("stale_map", setup(old), old) == Measurement(91.0, 90, True)


def test_map_grade() -> None:
    f_map = context(grade="F_Map")
    assert measure("map_grade", setup(f_map), f_map) == Measurement("F_Map", ("F_Map",), True)
    gone = context(grade=MissingInput(("SPX gamma Snapshot",)))
    m = measure("map_grade", setup(gone), gone)
    assert m.failed
    assert m.measured == DataUnavailable("missing SPX gamma Snapshot")


def test_midpoint() -> None:
    assert measure("midpoint", BASE_SETUP, BASE) == Measurement(0.0, (0.33, 0.67), False)
    mid = setup(BASE, 5800.0)  # at spot: neither King nor Gatekeeper
    assert measure("midpoint", mid, BASE) == Measurement(0.5, (0.33, 0.67), True)


def test_deflection_band_measures_penetration_past_the_level() -> None:
    through = context(futures={"MES": pts(5740.0)})
    assert measure("deflection_band", setup(through), through) == Measurement(10.0, 5.0, True)
    no_price = context(futures={})
    m = measure("deflection_band", setup(no_price), no_price)
    assert m.failed
    assert isinstance(m.measured, DataUnavailable)


def test_chart_confluence_and_stdev_fib_zone() -> None:
    assert measure("chart_confluence", BASE_SETUP, BASE) == Measurement(3.0, 5.0, False)
    bare = context(chart=ChartFeatures(T0, ()))
    m = measure("chart_confluence", setup(bare), bare)
    assert m.failed
    assert isinstance(m.measured, DataUnavailable)
    # Bullish leg 5700 -> 5730: zone -2..-2.5 is 5790..5805, zone -3.5..-4.5 5835..5865.
    leg = BosLeg("MES", 60, "bullish", 5710.0, T0 - 600, T0 - 300, T0 - 240, 5700.0, 5730.0)
    legged = context(chart=chart_at(T0, legs=(leg,)))
    m = measure("stdev_fib_zone", setup(legged), legged)
    assert m == Measurement(5790.0 - 5750.0, 5.0, True)
    m = measure("stdev_fib_zone", setup(legged, 5800.0), legged)
    assert m == Measurement(0.0, 5.0, False)


def test_dark_pool_confluence() -> None:
    def prints(*items: tuple[float, float]) -> dict[str, Any]:
        return {"SPY": tuple(DarkPoolPrint("SPY", T0 - 1, p, 1, n, "D") for p, n in items)}

    near = context(dark_pool=prints((575.1, 2.0e6), (590.0, 5.0e5)))
    m = measure("dark_pool_confluence", setup(near), near)
    assert m.failed is False
    assert m.measured == pytest.approx(1.0)
    small = context(dark_pool=prints((575.0, 5.0e5)))
    assert isinstance(measure("dark_pool_confluence", setup(small), small).measured, NoneFound)
    missing = context(dark_pool={"SPY": Unavailable("not in coverage")})
    m = measure("dark_pool_confluence", setup(missing), missing)
    assert m == Measurement(DataUnavailable("not in coverage"), 5.0, True)


def test_trinity_agreement_counts_symbols_and_the_empty_basement_exception() -> None:
    assert measure("trinity_agreement", BASE_SETUP, BASE) == Measurement(2, 2, False)
    # Barney Empty_Basement Floors: SPY 575 (MES 5750) and QQQ 496 (MES 5753.5).
    eb = {"SPX": SPX_PAIRS, "SPY": ((575.0, -2.0e9), (590.0, 3.0e9))}
    eb["QQQ"] = ((496.0, -2.0e9), (510.0, 3.0e9))
    ctx = context(pairs=eb)
    assert measure("trinity_agreement", setup(ctx), ctx) == Measurement(1, 2, False)
    off = {"empty_basement_exception": False}
    m = measure("trinity_agreement", setup(ctx), ctx, trinity_agreement=off)
    assert m == Measurement(1, 2, True)
    no_qqq = context(pairs={"SPX": SPX_PAIRS, "SPY": SPY_PAIRS})
    m = measure("trinity_agreement", setup(no_qqq), no_qqq)
    assert m.failed
    assert m.measured == DataUnavailable("no QQQ gamma Snapshot")


def test_candle_color() -> None:
    assert measure("candle_color", BASE_SETUP, BASE) == Measurement("red", "red", False)
    for label in ("green", "doji"):
        ctx = context(chart=chart_at(T0, candle=label))
        assert measure("candle_color", setup(ctx), ctx) == Measurement(label, "red", True)


def test_tap_counts() -> None:
    assert measure("tap_count", setup(BASE, tap_seq=3), BASE) == Measurement(3, 2, True)
    week = context(prior_week={FLOOR_NODE: 2, ("SPX", "gamma", 5780.0): 2})
    assert measure("weekly_node_tests", setup(week), week) == Measurement(3, 2, True)
    gk = setup(week, 5780.0, detector="gatekeeper_fade")
    assert measure("third_gatekeeper_test", gk, week) == Measurement(3, 3, True)
    m = measure("third_gatekeeper_test", setup(week), week)
    assert m == Measurement(PatternExempt("floor_ceiling_bounce"), 3, False)


def test_lifecycle_labels() -> None:
    sloppy = context(node_states={FLOOR_NODE: NodeState("Tested", T0 - 1)})
    assert measure("sloppy_seconds", setup(sloppy), sloppy) == Measurement(True, False, True)
    unlabeled = context(node_states={})
    assert isinstance(
        measure("sloppy_seconds", setup(unlabeled), unlabeled).measured, DataUnavailable
    )
    dormant = context(dormant=(FLOOR_NODE,))
    assert measure("dormant_node", setup(dormant), dormant) == Measurement(True, False, True)


def test_air_pocket_fade() -> None:
    assert measure("air_pocket_fade", BASE_SETUP, BASE) == Measurement(0.0, 0.0, False)
    # Entry 5760: 5 points past the band's 5755 edge, inside the (5750, 5780) pocket.
    deep = setup(BASE, entry=pts(5760.0), targets=(pts(5780.0),))
    assert measure("air_pocket_fade", deep, BASE) == Measurement(5.0, 0.0, True)
    rug = setup(BASE, 5820.0, "short", detector="rug")
    assert measure("air_pocket_fade", rug, BASE).measured == PatternExempt("rug")


def test_min_reward_risk_weights_partial_targets_and_fails_without_one() -> None:
    def per_regime(mode: str, **modes: Any) -> ExitsConfig:
        return ExitsConfig.model_validate(
            {
                "per_regime": {"Positive_Gamma": {"mode": mode, "stop_rule": "fixed_ticks"}},
                "modes": modes,
            }
        )

    # TP1 at 1.5R (42 ticks), TP2 at the next Node 5780 (120 ticks), half each.
    partial = per_regime("tp1_partial_be")
    c = setup(BASE, exits=partial)
    m = gate("min_reward_risk").measure(c, BASE, GateParams(exits=partial))
    assert m == Measurement((0.5 * 42 + 0.5 * 120) / 28, 3.0, True)
    trailing = per_regime("trailing")
    c = setup(BASE, exits=trailing)
    m = gate("min_reward_risk").measure(c, BASE, GateParams(exits=trailing))
    assert m == Measurement(NoPlannedTarget("trailing"), 3.0, True)


def test_opposition_inside_target() -> None:
    assert measure("opposition_inside_target", BASE_SETUP, BASE).failed is False
    # Long at 5780 (1e9): 5800 (0.9e9 >= 85%) is 80 ticks beyond entry, inside 3R (84).
    gk = setup(BASE, 5780.0, detector="gatekeeper_fade")
    assert measure("opposition_inside_target", gk, BASE) == Measurement(5800.0, 3.0, True)
    m = measure("opposition_inside_target", gk, BASE, opposition_inside_target={"window_r": 2.5})
    assert m.failed is False
    assert isinstance(m.measured, NoneFound)


def test_fomo_travel_reads_the_tap_in_progress() -> None:
    assert measure("fomo_travel", BASE_SETUP, BASE) == Measurement(0.0, 0.6, False)
    start = T0 - 3 * NS_PER_MINUTE
    tap = Tap("SPX", "gamma", FLOOR, "MES", SESSION, 1, start, start + NS_PER_MINUTE, T0)
    bars = tuple(
        minute_bar(start + i * NS_PER_MINUTE, 5751.0, h, 5749.0, 5751.0)
        for i, h in enumerate((5755.0, 5770.0, 5760.0))
    )
    ctx = context(taps=(tap,), minute_bars={"MES": bars})
    # 20 points of the 30 to TP1 at 5780.
    assert measure("fomo_travel", setup(ctx), ctx) == Measurement(20 / 30, 0.6, True)
    no_bars = context(taps=(tap,))
    assert isinstance(measure("fomo_travel", setup(no_bars), no_bars).measured, DataUnavailable)


@pytest.mark.parametrize(
    ("wall", "shuffle", "cutoff", "chase"),
    [
        (time(9, 40), True, False, False),
        (time(9, 45), False, False, False),
        (time(15, 25), False, True, False),
        (time(15, 45), False, True, True),
    ],
)
def test_time_gates(wall: time, shuffle: bool, cutoff: bool, chase: bool) -> None:
    ctx = context(ny_instant(SESSION, wall))
    c = setup(ctx)
    assert measure("open_shuffle", c, ctx).failed is shuffle
    assert measure("entry_cutoff", c, ctx).failed is cutoff
    m = measure("late_session_chase", c, ctx)
    assert m.failed is chase
    assert m.measured == LateChase(f"{wall.hour:02d}:{wall.minute:02d}:00", False, True)


def test_news_window() -> None:
    def at(minutes: int, kind: str = "CPI") -> GateContext:
        release = T0 + minutes * NS_PER_MINUTE
        return context(events=(EconomicEvent(kind, SESSION, release),))

    for minutes, failed in ((5, True), (-5, True), (6, False)):
        ctx = at(minutes)
        assert measure("news_window", setup(ctx), ctx) == Measurement(abs(minutes), 5, failed)
    other = at(0, "PPI")
    assert isinstance(measure("news_window", setup(other), other).measured, NoneFound)


def test_vix_gap() -> None:
    gap = context(vix=VixState(18.4, 16.0, 18.0))
    assert measure("vix_gap", setup(gap), gap) == Measurement(15.0, 15.0, True)
    noon = context(ny_instant(SESSION, time(12, 0)), vix=VixState(18.4, 16.0, 18.0))
    assert measure("vix_gap", setup(noon), noon).failed is False
    gone = context(vix=VixState(Unavailable("x"), 16.0, 18.0))
    m = measure("vix_gap", setup(gone), gone)
    assert m == Measurement(DataUnavailable("missing vix_daily_open"), 15.0, True)


def test_regime_match_uses_the_playbook_baseline_sets() -> None:
    allowed = ("Positive_Gamma", "Whipsaw")
    assert measure("regime_match", BASE_SETUP, BASE) == Measurement(
        "Positive_Gamma", allowed, False
    )
    neg = context(regime="Negative_Gamma")
    assert measure("regime_match", setup(neg), neg).failed is True
    trend = setup(neg, 5700.0, detector="trend_follow")
    assert measure("regime_match", trend, neg).failed is False
    gone = context(regime=MissingInput(("SPX vanna Snapshot",)))
    m = measure("regime_match", setup(gone), gone)
    assert m == Measurement(DataUnavailable("missing SPX vanna Snapshot"), allowed, True)


def test_gatekeepers_on_path() -> None:
    assert measure("gatekeepers_on_path", BASE_SETUP, BASE) == Measurement(0, 2, False)
    far = setup(BASE, targets=(pts(5845.0),))  # past both Gatekeepers, 5780 and 5820
    assert measure("gatekeepers_on_path", far, BASE) == Measurement(2, 2, True)


def test_node_growth_divergence() -> None:
    assert measure("node_growth_divergence", BASE_SETUP, BASE) == Measurement(-5.0, 20.0, False)
    growing = context(velocities={SPX: {FLOOR: 20.0}})
    assert measure("node_growth_divergence", setup(growing), growing).failed is True
    unknown = context(velocities={SPX: {FLOOR: Unavailable("no prior Snapshot")}})
    m = measure("node_growth_divergence", setup(unknown), unknown)
    assert m == Measurement(DataUnavailable("no prior Snapshot"), 20.0, True)
    rug = setup(BASE, 5820.0, "short", detector="rug")
    assert measure("node_growth_divergence", rug, unknown).failed is False


def test_kill_switch_lockout_names_each_active_lockout() -> None:
    old = Lockout("red_day", T0 - 86_400 * NS_PER_SECOND, date(2026, 3, 4))
    live = Lockout("consecutive_losers", T0 - NS_PER_MINUTE, date(2026, 3, 6))
    ctx = context(risk=RiskState(session=SESSION, lockouts=(old, live)))
    assert measure("kill_switch_lockout", setup(ctx), ctx) == Measurement((live,), 0, True)
    assert measure("kill_switch_lockout", BASE_SETUP, BASE) == Measurement((), 0, False)


def test_context_rejects_inputs_after_t() -> None:
    late = DarkPoolPrint("SPY", T0 + 1, 575.0, 1, 2.0e6, "D")
    with pytest.raises(ValueError, match="after t"):
        replace(BASE, dark_pool={"SPY": (late,)})
    with pytest.raises(ValueError, match="after t"):
        replace(BASE, minute_bars={"MES": (minute_bar(T0, 1.0, 1.0, 1.0, 1.0),)})
