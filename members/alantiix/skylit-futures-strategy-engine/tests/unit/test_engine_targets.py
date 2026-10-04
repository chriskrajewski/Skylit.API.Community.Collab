"""Unit tests for target planning (design §12 "Targets").

Long and short examples for each Exit_Mode on one synthetic SPX gamma Map:
an offset-0 conversion, so a strike's MES level is the strike in points.
Property 37 (task 13.4) covers the general case.

**Validates: Requirements 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7, 12.9, 12.12**
"""

from __future__ import annotations

from datetime import date, time
from typing import Any

import pytest

from fse.config.schema.exits import ExitsConfig
from fse.config.schema.nodes import NodesConfig
from fse.engine.levels import Conversion, ConvertedMap, band, round_to_tick
from fse.engine.nodes import NodeParams, classify
from fse.engine.targets import (
    ExitModeCfg,
    NodeLevel,
    TargetBasis,
    TargetContext,
    TargetRejection,
    Targets,
    exit_mode_for,
    plan_targets,
    r_target,
    tp_quantities,
)
from fse.engine.types import (
    CandidateSetup,
    MissingInput,
    NoTarget,
    SetupInputs,
    SetupKey,
    Snapshot,
    SourceNodeRef,
)
from fse.timekit import ny_instant

T0 = ny_instant(date(2026, 3, 5), time(10, 0))
SOURCE = 5780.0
SOURCE_VALUE = 2.5e9

# (strike, value): the source Floor at 5780 and Nodes on both sides. With a 3e9
# King and node_fraction 0.2, every strike here is a Node.
STRIKES_VALUES: tuple[tuple[float, float], ...] = (
    (5760.0, 1.0e9),
    (5770.0, -2.2e9),  # 88% of the source: opposition for a short
    (SOURCE, SOURCE_VALUE),
    (5790.0, 1.0e9),  # 40%: not opposition
    (5800.0, -2.2e9),  # 88%: opposition for a long
    (5825.0, 3.0e9),  # King
)
NODES = tuple(NodeLevel(k, v, round_to_tick(k)) for k, v in STRIKES_VALUES)
CTX = TargetContext(NODES)
L_SOURCE = round_to_tick(SOURCE)  # 23120

LONG = TargetBasis("long", L_SOURCE, L_SOURCE - 20, SOURCE, SOURCE_VALUE)  # risk 20 ticks
SHORT = TargetBasis("short", L_SOURCE, L_SOURCE + 20, SOURCE, SOURCE_VALUE)


def tick(strike: float) -> int:
    return round_to_tick(strike)


def mode(name: str, **modes: dict[str, Any]) -> ExitModeCfg:
    cfg = ExitsConfig.model_validate({"global": {"mode": name}, "modes": modes})
    return exit_mode_for(cfg, MissingInput(("regime",)))


# ---------------------------------------------------------------- Exit_Mode selection


def test_exit_mode_for_uses_the_regime_setting_else_global() -> None:
    cfg = ExitsConfig.model_validate(
        {"per_regime": {"Whipsaw": {"mode": "tp1_partial_be", "stop_rule": "fixed_ticks"}}}
    )
    pg = exit_mode_for(cfg, "Positive_Gamma")
    assert (pg.mode, pg.stop_rule, pg.modes) == ("next_node", "one_node_beyond", cfg.modes)
    ws = exit_mode_for(cfg, "Whipsaw")
    assert (ws.mode, ws.stop_rule) == ("tp1_partial_be", "fixed_ticks")
    for regime in ("Negative_Gamma", "Vanna_Dominant", "Structureless"):
        assert exit_mode_for(cfg, regime).mode == "opposition_or_fixed_r"
    missing = exit_mode_for(cfg, MissingInput(("regime",)))
    assert (missing.mode, missing.stop_rule) == ("opposition_or_fixed_r", "one_node_beyond")


# ---------------------------------------------------------------- Fixed_R and rounding


def test_fixed_r_is_r_times_risk_beyond_entry() -> None:
    assert plan_targets(LONG, mode("fixed_r"), CTX) == Targets("fixed_r", L_SOURCE + 60)
    assert plan_targets(SHORT, mode("fixed_r"), CTX) == Targets("fixed_r", L_SOURCE - 60)
    two = mode("fixed_r", fixed_r={"r_multiple": 2.0})
    assert plan_targets(LONG, two, CTX) == Targets("fixed_r", L_SOURCE + 40)


@pytest.mark.parametrize(
    ("risk", "r", "offset"),
    [
        (5, 1.5, 7),  # 7.5 ticks rounds toward entry
        (1, 0.5, 1),  # 0.5 tick: kept 1 tick beyond entry
        (3, 0.5, 1),  # 1.5 ticks
        (30, 4.1, 123),  # exact decimal: a float product floors to 122
        (45, 1.4, 63),  # exact decimal: a float product floors to 62
    ],
)
def test_r_target_rounds_toward_entry_and_stays_a_tick_beyond(
    risk: int, r: float, offset: int
) -> None:
    long = TargetBasis("long", 1000, 1000 - risk, SOURCE, SOURCE_VALUE)
    short = TargetBasis("short", 1000, 1000 + risk, SOURCE, SOURCE_VALUE)
    assert r_target(long, r) == 1000 + offset
    assert r_target(short, r) == 1000 - offset


# ---------------------------------------------------------------- Next_Node


def test_next_node_is_the_nearest_other_node_beyond_entry() -> None:
    assert plan_targets(LONG, mode("next_node"), CTX) == Targets("next_node", tick(5790.0))
    assert plan_targets(SHORT, mode("next_node"), CTX) == Targets("next_node", tick(5770.0))


def test_next_node_skips_the_source_and_nodes_at_entry() -> None:
    # Entry 1 tick above the source level: the source Node lies behind entry for a
    # short and would be "beyond" it, but it is skipped. A Node at entry is not beyond.
    at_entry = (*NODES, NodeLevel(5780.25, 1.0e9, L_SOURCE + 1))
    short = TargetBasis("short", L_SOURCE + 1, L_SOURCE + 21, SOURCE, SOURCE_VALUE)
    assert plan_targets(short, mode("next_node"), TargetContext(at_entry)) == Targets(
        "next_node", tick(5770.0)
    )


def test_next_node_without_a_node_beyond_entry_is_no_target_node() -> None:
    below_only = TargetContext(tuple(n for n in NODES if n.strike <= SOURCE))
    assert plan_targets(LONG, mode("next_node"), below_only) == TargetRejection("no_target_node")
    assert plan_targets(SHORT, mode("next_node"), TargetContext(())) == TargetRejection(
        "no_target_node"
    )


# ---------------------------------------------------------------- TP1_Partial_BE


def test_tp1_partial_be_default_rules_r_then_node_beyond_tp1() -> None:
    # TP1 at 1.5R = 30 ticks; TP2 at the nearest Node beyond TP1.
    assert plan_targets(LONG, mode("tp1_partial_be"), CTX) == Targets(
        "tp1_partial_be", L_SOURCE + 30, tick(5790.0), 0.5
    )
    assert plan_targets(SHORT, mode("tp1_partial_be"), CTX) == Targets(
        "tp1_partial_be", L_SOURCE - 30, tick(5770.0), 0.5
    )


def test_tp1_partial_be_node_rules_and_r_rules() -> None:
    nodes = mode("tp1_partial_be", tp1_partial_be={"tp1": {"rule": "node"}})
    assert plan_targets(LONG, nodes, CTX) == Targets(
        "tp1_partial_be", tick(5790.0), tick(5800.0), 0.5
    )
    r_r = mode(
        "tp1_partial_be",
        tp1_partial_be={"tp2": {"rule": "r", "r_multiple": 4.0}, "tp1_fraction": 0.3},
    )
    assert plan_targets(SHORT, r_r, CTX) == Targets(
        "tp1_partial_be", L_SOURCE - 30, L_SOURCE - 80, 0.3
    )


@pytest.mark.parametrize(
    "tp_cfg",
    [
        {"tp1": {"rule": "node"}, "tp2": {"rule": "r", "r_multiple": 1.5}},  # 30 < 40 ticks
        {"tp1": {"rule": "r", "r_multiple": 2.0}, "tp2": {"rule": "r", "r_multiple": 2.0}},
    ],
)
def test_tp2_not_farther_than_tp1_is_rejected(tp_cfg: dict[str, Any]) -> None:
    cfg = mode("tp1_partial_be", tp1_partial_be=tp_cfg)
    for basis in (LONG, SHORT):
        assert plan_targets(basis, cfg, CTX) == TargetRejection("tp2_not_beyond_tp1")


def test_tp2_equal_after_rounding_is_rejected() -> None:
    # 1 tick of risk: 1.5R and 1.6R both round to 1 tick.
    basis = TargetBasis("long", 1000, 999, SOURCE, SOURCE_VALUE)
    cfg = mode(
        "tp1_partial_be",
        tp1_partial_be={"tp2": {"rule": "r", "r_multiple": 1.6}},
    )
    assert plan_targets(basis, cfg, CTX) == TargetRejection("tp2_not_beyond_tp1")


def test_tp1_partial_be_missing_nodes_are_no_target_node() -> None:
    below_only = TargetContext(tuple(n for n in NODES if n.strike <= SOURCE))
    node_tp1 = mode("tp1_partial_be", tp1_partial_be={"tp1": {"rule": "node"}})
    assert plan_targets(LONG, node_tp1, below_only) == TargetRejection("no_target_node")
    # TP1 at 1.5R exists, but no Node lies beyond it.
    near = TargetContext(tuple(n for n in NODES if n.strike <= 5790.0))
    far_tp1 = mode("tp1_partial_be", tp1_partial_be={"tp1": {"r_multiple": 2.5}})
    assert plan_targets(LONG, far_tp1, near) == TargetRejection("no_target_node")


@pytest.mark.parametrize(
    ("qty", "fraction", "split"),
    [
        (1, 0.5, (1, 0)),  # a 1-contract position exits in full at TP1
        (2, 0.5, (1, 1)),
        (5, 0.5, (2, 3)),
        (3, 0.1, (1, 2)),  # floor(0.3) = 0, raised to 1
        (10, 0.9, (9, 1)),
        (100, 0.29, (29, 71)),  # exact decimal: a float product floors to 28
    ],
)
def test_tp_quantities(qty: int, fraction: float, split: tuple[int, int]) -> None:
    assert tp_quantities(qty, fraction) == split
    assert Targets("tp1_partial_be", 10, 20, fraction).quantities(qty) == split


def test_single_target_takes_every_contract_at_tp1() -> None:
    assert Targets("fixed_r", 10).quantities(5) == (5, 0)
    assert Targets("fixed_r", 10).prices == (10,)
    assert Targets("tp1_partial_be", 10, 20, 0.5).prices == (10, 20)
    with pytest.raises(ValueError, match="qty must be at least 1"):
        Targets("fixed_r", 10).quantities(0)


# ---------------------------------------------------------------- Opposition_Or_Fixed_R


def test_opposition_or_fixed_r_takes_the_nearer() -> None:
    # Opposition 5800 is 80 ticks out; 3R is 60 ticks: the R target is nearer.
    default = mode("opposition_or_fixed_r")
    assert plan_targets(LONG, default, CTX) == Targets("opposition_or_fixed_r", L_SOURCE + 60)
    # At 5R (100 ticks) the opposition Node is nearer; 5790 (40%) is not opposition.
    five = mode("opposition_or_fixed_r", opposition_or_fixed_r={"r_multiple": 5.0})
    assert plan_targets(LONG, five, CTX) == Targets("opposition_or_fixed_r", tick(5800.0))
    assert plan_targets(SHORT, five, CTX) == Targets("opposition_or_fixed_r", tick(5770.0))


def test_opposition_threshold_is_inclusive_and_r_target_is_the_fallback() -> None:
    exact = TargetContext((NodeLevel(SOURCE, 100.0, L_SOURCE), NodeLevel(5790.0, -85.0, 23160)))
    basis = TargetBasis("long", L_SOURCE, L_SOURCE - 20, SOURCE, 100.0)
    five = mode("opposition_or_fixed_r", opposition_or_fixed_r={"r_multiple": 5.0})
    assert plan_targets(basis, five, exact) == Targets("opposition_or_fixed_r", 23160)
    stricter = mode(
        "opposition_or_fixed_r", opposition_or_fixed_r={"r_multiple": 5.0, "fraction": 0.9}
    )
    assert plan_targets(basis, stricter, exact) == Targets("opposition_or_fixed_r", L_SOURCE + 100)


def test_opposition_threshold_compares_exact_decimals() -> None:
    # 0.07 x 2.5e9 is 175000000.00000003 as floats; the decimals give exactly 1.75e8,
    # so a Node of 1.75e8 (40 ticks out, nearer than 3R = 60) is opposition.
    assert 0.07 * SOURCE_VALUE > 1.75e8
    seven = mode("opposition_or_fixed_r", opposition_or_fixed_r={"fraction": 0.07})
    long_ctx = TargetContext(
        (NodeLevel(SOURCE, SOURCE_VALUE, L_SOURCE), NodeLevel(5790.0, -1.75e8, L_SOURCE + 40))
    )
    assert plan_targets(LONG, seven, long_ctx) == Targets("opposition_or_fixed_r", L_SOURCE + 40)
    short_ctx = TargetContext(
        (NodeLevel(SOURCE, SOURCE_VALUE, L_SOURCE), NodeLevel(5770.0, 1.75e8, L_SOURCE - 40))
    )
    assert plan_targets(SHORT, seven, short_ctx) == Targets("opposition_or_fixed_r", L_SOURCE - 40)
    # Just below the threshold: the R target.
    below = TargetContext(
        (NodeLevel(SOURCE, SOURCE_VALUE, L_SOURCE), NodeLevel(5790.0, 1.7499999e8, L_SOURCE + 40))
    )
    assert plan_targets(LONG, seven, below) == Targets("opposition_or_fixed_r", L_SOURCE + 60)


# ---------------------------------------------------------------- Trailing


def test_trailing_plans_no_target() -> None:
    assert plan_targets(LONG, mode("trailing"), CTX) == NoTarget()
    assert plan_targets(SHORT, mode("trailing"), TargetContext(())) == NoTarget()


# ---------------------------------------------------------------- inputs


def snapshot() -> Snapshot:
    strikes = (*(k for k, _ in STRIKES_VALUES), 5850.0)
    values = (*(v for _, v in STRIKES_VALUES), 1.0e8)  # 5850 is not a Node
    return Snapshot(
        symbol="SPX",
        metric="gamma",
        view_id="view-test",
        as_of_ns=T0,
        as_of_raw="raw",
        spot=5791.25,
        previous_close=None,
        strikes=strikes,
        values=values,
        node_types=None,
        expirations=("2026-03-05",),
        resolution="1s",
        source_endpoint="range",
        extra_json="{}",
    )


def converted(s: Snapshot, as_of_ns: int = T0) -> ConvertedMap:
    conv = Conversion(
        symbol="SPX", family="ES", instrument="MES", contract="MESH6", method="offset",
        factor=0.0, futures_close=s.spot, spot=s.spot, as_of_ns=as_of_ns,
        paired_bar_close_ns=as_of_ns,
    )  # fmt: skip
    levels = tuple(conv.level(k) for k in s.strikes)
    return ConvertedMap(
        symbol=s.symbol,
        metric=s.metric,
        as_of_ns=as_of_ns,
        conversion=conv,
        band_half_width_pts=5.0,
        strikes=s.strikes,
        levels=levels,
        bands=tuple(band(level, 5.0) for level in levels),
    )


def test_context_from_snapshot_holds_the_labeled_nodes() -> None:
    s = snapshot()
    labels = classify(s, NodeParams.from_config(NodesConfig()))
    ctx = TargetContext.from_snapshot(s, labels, converted(s))
    assert ctx == CTX
    with pytest.raises(ValueError, match="is not of the SPX gamma Snapshot"):
        TargetContext.from_snapshot(s, labels, converted(s, T0 - 1))


def test_basis_of_a_priced_setup_and_validation() -> None:
    key = SetupKey("MES", "floor_ceiling_bounce", SOURCE, "long", date(2026, 3, 5), 1)
    src = SourceNodeRef("SPX", "gamma", SOURCE, SOURCE_VALUE, L_SOURCE, (5775.0, 5785.0))
    inputs = SetupInputs(
        map_as_of=(),
        source_spot=5791.25,
        futures_price=23165,
        conversion_method="offset",
        conversion_factor=0.0,
        band_half_width_pts=5.0,
        regime="Positive_Gamma",
        map_grade="Neutral_Map",
        stop_rule="fixed_ticks",
    )
    setup = CandidateSetup(
        key, "floor_ceiling_bounce", T0, L_SOURCE, L_SOURCE - 20, (L_SOURCE + 60,), "fixed_r",
        src, inputs,
    )  # fmt: skip
    assert TargetBasis.of(setup) == LONG
    assert plan_targets(setup, mode("next_node"), CTX) == plan_targets(LONG, mode("next_node"), CTX)
    with pytest.raises(ValueError, match="stop below entry"):
        TargetBasis("long", 100, 100, SOURCE, SOURCE_VALUE)
    with pytest.raises(ValueError, match="stop above entry"):
        TargetBasis("short", 100, 99, SOURCE, SOURCE_VALUE)
    with pytest.raises(ValueError, match="distinct strikes"):
        TargetContext((NODES[0], NODES[0]))


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"mode": "trailing", "tp1": 10}, "Targets.mode"),
        ({"mode": "fixed_r", "tp1": 10, "tp2": 20, "tp1_fraction": 0.5}, "exactly"),
        ({"mode": "tp1_partial_be", "tp1": 10}, "exactly"),
        ({"mode": "tp1_partial_be", "tp1": 10, "tp2": 10, "tp1_fraction": 0.5}, "differ"),
        ({"mode": "tp1_partial_be", "tp1": 10, "tp2": 20, "tp1_fraction": 1.0}, "below 1"),
    ],
)
def test_targets_reject_malformed_plans(kwargs: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Targets(**kwargs)


def test_rejection_reason_is_checked() -> None:
    with pytest.raises(ValueError, match="no_target_node"):
        TargetRejection("max_open")  # type: ignore[arg-type]
