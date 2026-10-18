"""Unit tests for Order_Planner edge cases (design §11-12, task 16.6).

Three edges, each tested at the layer that owns it:

- **Next_Node with no Node beyond entry** (Req 12.12): ``plan_targets``
  skips the source Node and a Node at entry; :func:`place` records
  ``no_target_node`` (or ``tp2_not_beyond_tp1`` for TP1_Partial_BE) and
  leaves the book as it was.
- **Trailing with no target** (Req 11.7): by design the detectors skip a
  Trailing setup with ``DetectionSkip(no_target)`` and min_reward_risk fails
  it with the measured value ``no_target``, which never grades Alert_2R.
  The planner still supports Trailing when handed an A_Plus setup: no target
  order, and with no Node beyond entry the stop stays put until Flatten_Time.
- **Breakeven past the close** (Req 12.22): the move-to-breakeven action
  becomes a market exit when the latest close is at or past the
  Breakeven_Price against the trade, including a close that is in profit
  against entry but short of a Breakeven_Price with an offset.

The planner fixtures are those of ``test_engine_planner`` (SPX gamma, offset-0
conversion: a strike's MES level is the strike in points); the Gate and
detector fixtures are those of ``test_engine_gates``.

**Validates: Requirements 11.7, 12.12, 12.22**
"""

from __future__ import annotations

from typing import Any

import pytest

from fse.config.schema.exits import ExitsConfig
from fse.config.schema.orders import InvalidationAction
from fse.engine.gates.catalog import GateParams, NoPlannedTarget
from fse.engine.gates.registry import evaluate
from fse.engine.planner import (
    CancelOrder,
    Flattened,
    Invalidated,
    ModifyOrder,
    OrderBook,
    PlaceBracket,
    PlacementRejection,
    PlannerConfig,
    PlannerContext,
    StopMoved,
    SubmitExit,
    manage,
    on_bar,
    place,
)
from fse.engine.setups.base import DetectorParams
from fse.engine.setups.registry import detect_setups
from fse.engine.targets import (
    NodeLevel,
    TargetBasis,
    TargetContext,
    TargetRejection,
    Targets,
    exit_mode_for,
    plan_targets,
)
from fse.engine.types import CandidateSetup, DetectionSkip, Direction, Order, Side
from tests.unit.test_engine_gates import BASE as GATE_CTX
from tests.unit.test_engine_gates import FLOOR, all_disabled
from tests.unit.test_engine_gates import setup as gate_setup
from tests.unit.test_engine_planner import (
    FLATTEN,
    T0,
    T1,
    D,
    accepted,
    bar,
    config,
    ctx,
    fill,
    placed,
    setup,
    tick,
    view,
)


def trailing_exits(stop_rule: str) -> ExitsConfig:
    """Trailing for the Positive_Gamma Regime of the Gate fixtures."""
    return ExitsConfig.model_validate(
        {"per_regime": {"Positive_Gamma": {"mode": "trailing", "stop_rule": stop_rule}}}
    )


def opened(c: CandidateSetup, cfg: PlannerConfig, qty: int | None = None) -> OrderBook:
    """``c`` placed for 2 contracts and filled (``qty`` of them) at entry on the T0 bar.

    The fill bar closes at entry, so no stop rule moves the stop on it.
    """
    book = placed(c, cfg)
    plan = book.plans[0]
    e = plan.entry_limit
    b = bar(T0, e, e + 2, e - 2, e)
    out, _, _ = on_bar(book, b, [fill(plan.entry, b, e, qty)], cfg)
    return out


# ---------------------------------------------------------------- no Node beyond entry


def test_next_node_skips_the_source_node_and_a_node_at_entry() -> None:
    mode = exit_mode_for(ExitsConfig(), "Positive_Gamma")
    assert mode.mode == "next_node"
    # Long 4 ticks below the 5780 source level: the source Node lies beyond
    # entry but is not a target, and a Node at entry is not beyond it.
    long = TargetBasis("long", 23116, 23096, 5780.0, 2.5e9)
    nodes = (
        NodeLevel(5770.0, -2.2e9, 23080),
        NodeLevel(5779.0, 1.0e9, 23116),
        NodeLevel(5780.0, 2.5e9, 23120),
    )
    assert plan_targets(long, mode, TargetContext(nodes)) == TargetRejection("no_target_node")
    one_tick = TargetContext((*nodes, NodeLevel(5779.25, 1.0e9, 23117)))
    assert plan_targets(long, mode, one_tick) == Targets("next_node", 23117)
    # The short mirror: the source Node below entry, a Node at entry, one above.
    short = TargetBasis("short", 23124, 23144, 5780.0, 2.5e9)
    mirror = TargetContext(
        (
            NodeLevel(5790.0, 1.0e9, 23160),
            NodeLevel(5781.0, 1.0e9, 23124),
            NodeLevel(5780.0, 2.5e9, 23120),
        )
    )
    assert plan_targets(short, mode, mirror) == TargetRejection("no_target_node")


def test_next_node_from_the_top_node_places_nothing_and_holds_no_slot() -> None:
    # 5850 lies above the 5825 King but is not a Node: no Node beyond a long entry.
    top = setup(strike=5825.0, regime="Positive_Gamma", exit_mode="next_node")
    ok = setup(regime="Positive_Gamma", exit_mode="next_node")
    rejected = PlacementRejection(top.key, T0, "no_target_node")
    assert place(OrderBook(), [accepted(top)], ctx(), config()) == (OrderBook(), [], [rejected])
    # The rejection takes no max_open slot: the next setup is placed.
    book, intents, rejections = place(OrderBook(), [accepted(top), accepted(ok)], ctx(), config())
    assert rejections == [rejected]
    (bracket,) = intents
    assert isinstance(bracket, PlaceBracket)
    assert [(o.role, o.price) for o in bracket.targets] == [("tp1", tick(5790.0))]
    assert [p.key for p in book.plans] == [ok.key]
    # With the book full, no_target_node is still the reason: it is checked first.
    _, _, rejections = place(book, [accepted(top)], ctx(), config())
    assert rejections == [rejected]


@pytest.mark.parametrize(
    ("tp_cfg", "direction", "strike", "reason"),
    [
        # Node TP1, short from the lowest Node: no Node below entry.
        ({"tp1": {"rule": "node"}}, "short", 5760.0, "no_target_node"),
        # TP1 at 1.5R (23330) from the King; no Node beyond it for the Node TP2.
        ({}, "long", 5825.0, "no_target_node"),
        # TP1 at the 5790 Node (2R), TP2 at 1.5R: TP2 is nearer entry than TP1.
        (
            {"tp1": {"rule": "node"}, "tp2": {"rule": "r", "r_multiple": 1.5}},
            "long",
            5780.0,
            "tp2_not_beyond_tp1",
        ),
    ],
)
def test_tp1_partial_be_without_its_node_targets_places_nothing(
    tp_cfg: dict[str, Any], direction: Direction, strike: float, reason: str
) -> None:
    cfg = config(
        exits={
            "global": {"mode": "tp1_partial_be"},
            "modes": {"tp1_partial_be": {"enabled": True, **tp_cfg}},
        }
    )
    c = setup(direction, strike=strike, exit_mode="tp1_partial_be")
    book, intents, rejections = place(OrderBook(), [accepted(c, 3)], ctx(), cfg)
    assert (book, intents) == (OrderBook(), [])
    assert [(r.key, r.reason) for r in rejections] == [(c.key, reason)]


# ---------------------------------------------------------------- Trailing: no target


@pytest.mark.parametrize(
    ("exits", "strike", "reason"),
    [
        (trailing_exits("fixed_ticks"), FLOOR, "trailing"),
        # Next_Node (the Positive_Gamma default) long from the top strike.
        (ExitsConfig(), 5850.0, "no_target_node"),
    ],
)
def test_min_reward_risk_fails_with_no_target_and_never_grades_alert_2r(
    exits: ExitsConfig, strike: float, reason: str
) -> None:
    c = gate_setup(GATE_CTX, strike, exits=exits)
    gates = all_disabled(min_reward_risk={"enabled": True})
    ev = evaluate(c, GATE_CTX, GateParams(gates=gates, exits=exits))
    assert ev.failing == ("min_reward_risk",)
    assert [(r.reason, r.measured, r.threshold) for r in ev.rejections] == [
        ("min_reward_risk", NoPlannedTarget(reason), 3.0)
    ]
    assert NoPlannedTarget(reason).kind == "no_target"
    # The only failing Gate is min_reward_risk, but no_target is not >= 2.0.
    assert ev.grade == "Pass"


def test_detectors_skip_every_setup_under_a_per_regime_trailing_mode() -> None:
    planned = detect_setups(GATE_CTX.detect, DetectorParams())
    keys = [x.key for x in planned if isinstance(x, CandidateSetup)]
    assert keys
    trailing = detect_setups(
        GATE_CTX.detect, DetectorParams(exits=trailing_exits("one_node_beyond"))
    )
    assert not any(isinstance(x, CandidateSetup) for x in trailing)
    # Each setup the Next_Node default emits becomes a no_target skip.
    for key in keys:
        assert DetectionSkip(key, GATE_CTX.detect.t, "no_target") in trailing


def test_node_to_node_trailing_with_no_node_beyond_entry_holds_the_stop() -> None:
    cfg = config(
        exits={
            "global": {"mode": "trailing"},
            "modes": {"trailing": {"enabled": True}},
            "breakeven": {"enabled": False},
        }
    )
    # Short from the lowest Node: no Node below entry 23040, so no Node target
    # and no trailing step; Req 12.12 does not apply to Trailing.
    c = setup("short", strike=5760.0, exit_mode="trailing")
    book, intents, rejections = place(OrderBook(), [accepted(c)], ctx(), cfg)
    assert rejections == []
    (bracket,) = intents
    assert isinstance(bracket, PlaceBracket)
    assert bracket.targets == ()
    assert (bracket.stop.side, bracket.stop.qty, bracket.stop.price) == ("buy", 2, 23060)
    plan = book.plans[0]
    b0 = bar(T0, 23042, 23044, 23036, 23038)
    book, _, _ = on_bar(book, b0, [fill(plan.entry, b0, 23040)], cfg)
    b1 = bar(T1, 23038, 23039, 22900, 22910)  # 140 ticks in favor
    book, intents, events = on_bar(book, b1, [], cfg)
    assert (intents, events) == ([], [])
    assert (book.plans[0].stop_price, book.plans[0].favorable) == (23060, 22900)
    # Only the stop or Flatten_Time closes it.
    flat = PlannerContext(t=FLATTEN, session=D, flatten_at=FLATTEN, sources=ctx().sources)
    _, intents, events = manage(book, flat, cfg)
    exit_order = Order(f"{plan.sid}-exit", c.key, "MES", "buy", "market", 2, None, FLATTEN, "exit")
    assert intents == [SubmitExit(exit_order)]
    assert events == [Flattened(c.key, FLATTEN, 2)]


# ---------------------------------------------------------------- breakeven past the close


@pytest.mark.parametrize(
    ("direction", "strike", "offset", "close", "applied"),
    [
        # Long, entry 23120: Breakeven_Price 23121, a close 1 tick beyond it moves the stop.
        ("long", 5780.0, 1, 23122, "breakeven"),
        # Breakeven_Price 23124: the close is 2 ticks in profit but short of it.
        ("long", 5780.0, 4, 23122, "exit_market"),
        # Short, entry 23200: Breakeven_Price 23199.
        ("short", 5800.0, 1, 23199, "exit_market"),
        ("short", 5800.0, 1, 23210, "exit_market"),
        ("short", 5800.0, 1, 23198, "breakeven"),
    ],
)
def test_breakeven_action_exits_when_the_close_is_at_or_past_breakeven(
    direction: Direction, strike: float, offset: int, close: int, applied: InvalidationAction
) -> None:
    cfg = config(
        orders={"invalidation": {"king_flip": "breakeven"}},
        exits={"breakeven": {"offset_ticks": offset}},
    )
    book = opened(setup(direction, strike=strike), cfg)
    plan = book.plans[0]
    assert plan.entry_price is not None
    sign = 1 if direction == "long" else -1
    be = plan.entry_price + sign * offset
    out, intents, events = manage(book, ctx(T1, view(T1, s5760=4.0e9), last_close=close), cfg)
    inv = Invalidated(plan.key, T1, ("king_flip",), "breakeven", applied)
    if applied == "breakeven":
        assert intents == [ModifyOrder(plan.stop.client_id, be, T1)]
        assert events == [
            StopMoved(plan.key, T1, plan.stop_price, be, ("invalidation_breakeven",)),
            inv,
        ]
        assert (out.plans[0].stop_price, out.plans[0].exit_pending) == (be, False)
    else:
        side: Side = "sell" if direction == "long" else "buy"
        exit_order = Order(f"{plan.sid}-exit", plan.key, "MES", side, "market", 2, None, T1, "exit")
        assert intents == [SubmitExit(exit_order)]
        assert events == [inv]
        assert (out.plans[0].stop_price, out.plans[0].exit_pending) == (plan.stop_price, True)


def test_exit_in_place_of_breakeven_closes_open_contracts_and_cancels_the_rest() -> None:
    cfg = config(orders={"invalidation": {"king_flip": "breakeven"}})
    book = opened(setup(), cfg, qty=1)  # 1 of 2 entry contracts filled
    plan = book.plans[0]
    _, intents, _ = manage(book, ctx(T1, view(T1, s5760=4.0e9), last_close=23110), cfg)
    exit_order = Order(f"{plan.sid}-exit", plan.key, "MES", "sell", "market", 1, None, T1, "exit")
    assert intents == [SubmitExit(exit_order), CancelOrder(plan.entry.client_id, T1)]
