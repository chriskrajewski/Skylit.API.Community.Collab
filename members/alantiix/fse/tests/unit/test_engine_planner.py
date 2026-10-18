"""Unit tests for the Order_Planner and the ``orders`` Config_Schema section (design §12).

One synthetic SPX gamma Map with an offset-0 conversion, so a strike's MES
level is the strike in points (5780 -> 23120 ticks). The long setup fades the
5780 Floor: entry 23120, stop 23100 (1R = 20 ticks). Properties 38, 39 and 41
(tasks 16.3-16.5) cover the general case.

**Validates: Requirements 12.1, 12.8, 12.10, 12.11, 12.13, 12.14, 12.15, 12.16,
12.17, 12.18, 12.19, 12.20, 12.21, 12.22, 12.23, 25.9**
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import date, time
from decimal import Decimal
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from fse.config.schema.exits import ExitsConfig
from fse.config.schema.gates import GatesConfig
from fse.config.schema.nodes import NodesConfig
from fse.config.schema.orders import CANCEL_TRIGGERS, OrdersConfig
from fse.engine.chart import BosLeg
from fse.engine.gates.registry import GateEvaluation, Grade
from fse.engine.levels import Conversion, ConvertedMap, band
from fse.engine.nodes import NodeParams, classify
from fse.engine.planner import (
    Accepted,
    CancelCause,
    CancelOrder,
    EntryCancelled,
    Flattened,
    Invalidated,
    ModifyOrder,
    OrderBook,
    OrderFill,
    PlaceBracket,
    PlannerConfig,
    PlannerContext,
    SourceView,
    StopMoved,
    SubmitExit,
    breakeven_price,
    drop_rejected,
    flatten_time,
    manage,
    on_bar,
    place,
    tighten_only,
)
from fse.engine.risk import Lockout, RiskState
from fse.engine.sizing import SIZING_STEPS, SizedSetup, StepOutcome
from fse.engine.types import (
    Bar,
    CandidateSetup,
    Direction,
    Fill,
    Order,
    Regime,
    SetupInputs,
    SetupKey,
    Snapshot,
    SnapshotAsOf,
    SourceNodeRef,
)
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, SessionCalendar, ny_instant

D = date(2026, 3, 5)
T0 = ny_instant(D, time(10, 0))
T1 = T0 + NS_PER_MINUTE
CAL = SessionCalendar(date(2026, 1, 1), date(2026, 12, 31))
FLATTEN = flatten_time(OrdersConfig(), CAL, D)
SOURCE = 5780.0

# With a 3e9 King and node_fraction 0.2, every strike but 5850 is a Node.
BASE_VALUES: dict[float, float] = {
    5760.0: 1.0e9,
    5770.0: -2.2e9,
    SOURCE: 2.5e9,
    5790.0: 1.0e9,
    5800.0: -2.2e9,
    5825.0: 3.0e9,  # King
    5850.0: 1.0e8,
}
LEG = BosLeg(
    instrument="MES", timeframe_s=60, direction="bearish", swing_price=5794.0,
    swing_open_ns=T0 - 10 * NS_PER_MINUTE, break_open_ns=T0 - 2 * NS_PER_MINUTE,
    break_close_ns=T0 - NS_PER_MINUTE, origin=5794.0, terminal=5790.0,
)  # fmt: skip
"""Its -2.0 to -2.5 zone is 5780 to 5782 points: it contains the long entry."""


def tick(strike: float) -> int:
    return int(strike * 4)


def view(t: int = T0, **changes: float) -> SourceView:
    """The SPX gamma Map at ``t``; ``changes`` maps ``s5790`` style names to new values."""
    values = dict(BASE_VALUES)
    for name, value in changes.items():
        values[float(name[1:])] = value
    s = Snapshot(
        symbol="SPX", metric="gamma", view_id="view-test", as_of_ns=t, as_of_raw="raw",
        spot=5791.25, previous_close=None, strikes=tuple(values), values=tuple(values.values()),
        node_types=None, expirations=("2026-03-05",), resolution="1s", source_endpoint="range",
        extra_json="{}",
    )  # fmt: skip
    conv = Conversion(
        symbol="SPX", family="ES", instrument="MES", contract="MESH6", method="offset",
        factor=0.0, futures_close=s.spot, spot=s.spot, as_of_ns=t, paired_bar_close_ns=t,
    )  # fmt: skip
    levels = tuple(conv.level(k) for k in s.strikes)
    cm = ConvertedMap(
        symbol="SPX", metric="gamma", as_of_ns=t, conversion=conv, band_half_width_pts=5.0,
        strikes=s.strikes, levels=levels, bands=tuple(band(lv, 5.0) for lv in levels),
    )  # fmt: skip
    return SourceView(s, classify(s, NodeParams.from_config(NodesConfig())), cm)


def ctx(
    t: int = T0,
    source: SourceView | None = None,
    *,
    legs: Mapping[str, tuple[BosLeg, ...]] | None = None,
    last_close: int | None = None,
    risk: RiskState | None = None,
) -> PlannerContext:
    return PlannerContext(
        t=t,
        session=D,
        flatten_at=FLATTEN,
        sources={("SPX", "gamma"): source or view(T0)},
        legs=legs or {},
        last_close={} if last_close is None else {"MES": last_close},
        risk=risk or RiskState(),
    )


def setup(
    direction: Direction = "long",
    *,
    strike: float = SOURCE,
    risk: int = 20,
    regime: Regime = "Negative_Gamma",
    exit_mode: str = "opposition_or_fixed_r",
    tap_seq: int = 1,
) -> CandidateSetup:
    sign = 1 if direction == "long" else -1
    level = tick(strike)
    key = SetupKey("MES", "floor_ceiling_bounce", strike, direction, D, tap_seq)
    src = SourceNodeRef("SPX", "gamma", strike, BASE_VALUES[strike], level, band(level, 5.0))
    inputs = SetupInputs(
        map_as_of=(SnapshotAsOf("SPX", "gamma", T0),), source_spot=5791.25, futures_price=23124,
        conversion_method="offset", conversion_factor=0.0, band_half_width_pts=5.0,
        regime=regime, map_grade="Neutral_Map", stop_rule="one_node_beyond",
    )  # fmt: skip
    return CandidateSetup(
        key, "floor_ceiling_bounce", T0, level, level - sign * risk, (level + sign * 3 * risk,),
        exit_mode, src, inputs,
    )  # fmt: skip


def accepted(c: CandidateSetup, contracts: int = 2, grade: Grade = "A_Plus") -> Accepted:
    steps = tuple(StepOutcome(s, contracts, s == "base_contracts") for s in SIZING_STEPS)
    return Accepted(SizedSetup(c, contracts, steps), GateEvaluation(c.key, c.t, (), (), grade))


def config(**sections: dict[str, Any]) -> PlannerConfig:
    return PlannerConfig(
        orders=OrdersConfig.model_validate(sections.get("orders", {})),
        exits=ExitsConfig.model_validate(sections.get("exits", {})),
        gates=GatesConfig.model_validate(sections.get("gates", {})),
    )


def bar(open_ns: int, o: int, h: int, low: int, c: int) -> Bar:
    return Bar(
        instrument="MES", contract="MESH6", interval_s=60, open_ns=open_ns,
        close_ns=open_ns + 60 * NS_PER_SECOND, o=o / 4, h=h / 4, l=low / 4, c=c / 4, v=100.0,
        o_t=o, h_t=h, l_t=low, c_t=c, source="atlas",
    )  # fmt: skip


def fill(order: Order, b: Bar, price: int, qty: int | None = None) -> OrderFill:
    q = order.qty if qty is None else qty
    return OrderFill(order, Fill(order.client_id, b.open_ns, price, q, Decimal("0.74")))


def placed(c: CandidateSetup, cfg: PlannerConfig, contracts: int = 2, **kw: Any) -> OrderBook:
    book, intents, rejections = place(OrderBook(), [accepted(c, contracts)], ctx(**kw), cfg)
    assert rejections == []
    assert len(intents) == 1
    return book


def filled(book: OrderBook, cfg: PlannerConfig) -> tuple[OrderBook, Bar]:
    """The long entry fills on the bar opening at T0; its close is 23124."""
    plan = book.plans[0]
    b = bar(T0, 23125, 23130, 23118, 23124)
    out, _, _ = on_bar(book, b, [fill(plan.entry, b, plan.entry_limit)], cfg)
    return out, b


# ---------------------------------------------------------------- the orders section

DESIGN_ORDERS_YAML = """
flatten_time: "15:55"
early_close_flatten_lead_min: 30
max_open: 1
max_age_min: null
cancel_triggers: {king_flip: true, source_node_gone: true, sign_flip: true,
                  stdev_leg_dropped: true, opposition_in_target: true}
invalidation: {king_flip: exit_market, source_node_gone: exit_market,
               sign_flip: exit_market, stdev_leg_dropped: hold, opposition_in_target: hold}
"""


def test_orders_defaults_match_the_design_sketch() -> None:
    cfg = OrdersConfig()
    assert cfg == OrdersConfig.model_validate(yaml.safe_load(DESIGN_ORDERS_YAML))
    assert (cfg.flatten_time, cfg.max_open, cfg.max_age_min) == (time(15, 55), 1, None)
    assert all(cfg.cancel_triggers.enabled(t) for t in CANCEL_TRIGGERS)
    assert [cfg.invalidation.action(t) for t in CANCEL_TRIGGERS] == [
        "exit_market", "exit_market", "exit_market", "hold", "hold",
    ]  # fmt: skip
    assert cfg.model_dump()["flatten_time"] == "15:55"


@pytest.mark.parametrize(
    ("value", "error"),
    [
        ({"flatten_time": "09:29"}, "greater_than_equal"),
        ({"flatten_time": "16:01"}, "less_than_equal"),
        ({"flatten_time": 955}, "time_type"),
        ({"flatten_time": "9:55"}, "time_parsing"),
        ({"early_close_flatten_lead_min": 14}, "greater_than_equal"),
        ({"max_open": 11}, "less_than_equal"),
        ({"max_age_min": 0}, "greater_than_equal"),
        ({"max_age_min": 391}, "less_than_equal"),
        ({"invalidation": {"king_flip": "scratch"}}, "literal_error"),
        ({"cancel_triggers": {"vix_spike": True}}, "extra_forbidden"),
    ],
)
def test_orders_reject_out_of_range_values(value: dict[str, Any], error: str) -> None:
    with pytest.raises(ValidationError) as info:
        OrdersConfig.model_validate(value)
    assert error in [e["type"] for e in info.value.errors()]


def test_orders_accept_range_ends_and_null_actions() -> None:
    cfg = OrdersConfig.model_validate(
        {"flatten_time": "09:30", "max_age_min": 390, "invalidation": {"sign_flip": None}}
    )
    assert cfg.invalidation.action("sign_flip") is None
    assert OrdersConfig.model_validate({"flatten_time": "16:00"}).flatten_time == time(16, 0)


# ---------------------------------------------------------------- rules


def test_tighten_only_and_breakeven_price() -> None:
    assert tighten_only(100, 104, "long") == 104
    assert tighten_only(100, 96, "long") == 100
    assert tighten_only(100, 96, "short") == 96
    assert tighten_only(100, 104, "short") == 100
    assert breakeven_price(23120, "long", 1) == 23121
    assert breakeven_price(23120, "short", 2) == 23118


def test_flatten_time_full_and_early_close_sessions() -> None:
    assert ny_instant(D, time(15, 55)) == FLATTEN
    early = SessionCalendar(D, D, early_closes={D: time(13, 0)})
    assert flatten_time(OrdersConfig(), early, D) == ny_instant(D, time(12, 30))
    lead = OrdersConfig.model_validate({"early_close_flatten_lead_min": 120})
    assert flatten_time(lead, early, D) == ny_instant(D, time(11, 0))


# ---------------------------------------------------------------- placement


def test_a_plus_setup_places_a_bracket_at_its_prices() -> None:
    c = setup()
    book, intents, rejections = place(OrderBook(), [accepted(c, 2)], ctx(), config())
    assert rejections == []
    (bracket,) = intents
    assert isinstance(bracket, PlaceBracket)
    e, s, (tp,) = bracket.entry, bracket.stop, bracket.targets
    assert (e.side, e.kind, e.qty, e.price, e.placed_at, e.role) == (
        "buy", "limit", 2, 23120, T0, "entry",
    )  # fmt: skip
    assert (s.side, s.kind, s.qty, s.price, s.role) == ("sell", "stop", 2, 23100, "stop")
    # Opposition_Or_Fixed_R: 3R (60 ticks) is nearer than the 5800 opposition Node.
    assert (tp.side, tp.kind, tp.qty, tp.price, tp.role) == ("sell", "limit", 2, 23180, "tp1")
    assert len({e.client_id, s.client_id, tp.client_id}) == 3
    assert book.exposure() == 1
    assert book.micro_equivalents() == 2
    assert c.key in book.placed


@pytest.mark.parametrize("grade", ["Alert_2R", "Pass"])
def test_only_a_plus_setups_get_orders(grade: Grade) -> None:
    book, intents, rejections = place(OrderBook(), [accepted(setup(), 2, grade)], ctx(), config())
    assert (book.plans, intents) == ((), [])
    assert [r.reason for r in rejections] == ["not_a_plus"]


def test_a_setup_key_is_never_placed_twice() -> None:
    cfg = config(orders={"max_open": 3})
    c = setup()
    book, intents, rejections = place(OrderBook(), [accepted(c), accepted(c)], ctx(), cfg)
    assert len(intents) == 1
    assert [r.reason for r in rejections] == ["already_placed"]
    # Still declined after the entry is cancelled; a new Tap gives a new key.
    book, _, _ = manage(book, ctx(T1, view(T1, s5760=4.0e9)), cfg)
    assert book.plans == ()
    _, intents, rejections = place(book, [accepted(c)], ctx(), cfg)
    assert (intents, [r.reason for r in rejections]) == ([], ["already_placed"])
    _, intents, _ = place(book, [accepted(setup(tap_seq=2))], ctx(), cfg)
    assert len(intents) == 1


def test_max_open_counts_resting_and_open_and_declines_the_rest() -> None:
    first, second = setup(), setup(tap_seq=2)
    book, intents, rejections = place(
        OrderBook(), [accepted(first), accepted(second)], ctx(), config()
    )
    assert len(intents) == 1
    assert [(r.key, r.reason) for r in rejections] == [(second.key, "max_open")]
    book, _ = filled(book, config())
    _, _, rejections = place(book, [accepted(second)], ctx(), config())
    assert [r.reason for r in rejections] == ["max_open"]


def test_no_entry_at_or_after_flatten_time_or_under_a_lockout() -> None:
    late = replace(setup(), t=FLATTEN)
    flat = PlannerContext(t=FLATTEN, session=D, flatten_at=FLATTEN, sources=ctx().sources)
    _, intents, rejections = place(OrderBook(), [accepted(late)], flat, config())
    assert (intents, [r.reason for r in rejections]) == ([], ["flatten_time"])
    locked = RiskState(session=D, lockouts=(Lockout("max_trades", T0 - 1, D),))
    _, intents, rejections = place(OrderBook(), [accepted(setup())], ctx(risk=locked), config())
    assert intents == []
    assert [(r.reason, r.detail) for r in rejections] == [("kill_switch_lockout", ("max_trades",))]


def test_target_rejections_and_a_disabled_exit_mode_place_nothing() -> None:
    # Next_Node short from the lowest Node: no Node below entry.
    short = setup("short", strike=5760.0, regime="Positive_Gamma", exit_mode="next_node")
    _, intents, rejections = place(OrderBook(), [accepted(short)], ctx(), config())
    assert (intents, [r.reason for r in rejections]) == ([], ["no_target_node"])
    off = config(exits={"modes": {"next_node": {"enabled": False}}})
    long = setup(regime="Positive_Gamma", exit_mode="next_node")
    _, intents, rejections = place(OrderBook(), [accepted(long)], ctx(), off)
    assert (intents, [r.reason for r in rejections]) == ([], ["exit_mode_disabled"])
    with pytest.raises(ValueError, match="Exit_Mode 'fixed_r'"):
        place(OrderBook(), [accepted(setup(exit_mode="fixed_r"))], ctx(), config())


def test_tp1_partial_be_splits_contracts_and_one_contract_exits_at_tp1() -> None:
    cfg = config(
        exits={"global": {"mode": "tp1_partial_be"}, "modes": {"tp1_partial_be": {"enabled": True}}}
    )
    c = setup(exit_mode="tp1_partial_be")
    _, (three,), _ = place(OrderBook(), [accepted(c, 3)], ctx(), cfg)
    assert isinstance(three, PlaceBracket)
    # TP1 at 1.5R = 23150 for floor(0.5 x 3) = 1; TP2 at the 5790 Node for 2.
    assert [(o.role, o.qty, o.price) for o in three.targets] == [
        ("tp1", 1, 23150), ("tp2", 2, tick(5790.0)),
    ]  # fmt: skip
    _, (one,), _ = place(OrderBook(), [accepted(c, 1)], ctx(), cfg)
    assert isinstance(one, PlaceBracket)
    assert [(o.role, o.qty) for o in one.targets] == [("tp1", 1)]


# ---------------------------------------------------------------- stop updates


def test_breakeven_moves_the_stop_from_the_next_bar_and_never_back() -> None:
    cfg = config()
    book, _ = filled(placed(setup(), cfg), cfg)
    plan = book.plans[0]
    # The fill bar's high (23130) may have traded before the fill: only its close counts.
    assert (plan.status, plan.open_qty, plan.favorable) == ("open", 2, 23124)
    b1 = bar(T1, 23124, 23139, 23120, 23130)  # 19 ticks: below 1R
    book, intents, _ = on_bar(book, b1, [], cfg)
    assert intents == []
    b2 = bar(T1 + NS_PER_MINUTE, 23130, 23140, 23128, 23135)  # 20 ticks: 1R
    book, intents, events = on_bar(book, b2, [], cfg)
    assert intents == [ModifyOrder(plan.stop.client_id, 23121, b2.close_ns)]
    assert events == [StopMoved(plan.key, b2.close_ns, 23100, 23121, ("breakeven",))]
    b3 = bar(b2.close_ns, 23135, 23136, 23122, 23123)
    book, intents, _ = on_bar(book, b3, [], cfg)
    assert intents == []
    assert book.plans[0].stop_price == 23121


def test_tp1_fill_moves_the_rest_to_breakeven_and_a_stop_fill_closes() -> None:
    cfg = config(
        exits={
            "global": {"mode": "tp1_partial_be"},
            "modes": {"tp1_partial_be": {"enabled": True}},
            "breakeven": {"enabled": False},
        }
    )
    c = setup(exit_mode="tp1_partial_be")
    book, _ = filled(placed(c, cfg), cfg)
    plan = book.plans[0]
    tp1 = plan.targets[0]
    b1 = bar(T1, 23130, 23152, 23128, 23140)
    book, intents, events = on_bar(book, b1, [fill(tp1, b1, 23150)], cfg)
    assert book.plans[0].open_qty == 1
    assert intents == [ModifyOrder(plan.stop.client_id, 23121, b1.close_ns)]
    assert events == [StopMoved(plan.key, b1.close_ns, 23100, 23121, ("tp1_breakeven",))]
    b2 = bar(b1.close_ns, 23125, 23126, 23110, 23112)
    stop = replace(plan.stop, price=23121)
    book, intents, _ = on_bar(book, b2, [fill(stop, b2, 23120, 1)], cfg)
    assert (book.plans, intents) == ((), [])
    assert c.key in book.placed


def test_fixed_ticks_trailing_follows_the_most_favorable_price() -> None:
    cfg = config(
        exits={
            "global": {"mode": "trailing"},
            "modes": {"trailing": {"enabled": True, "mode": "fixed_ticks", "ticks": 8}},
        }
    )
    c = setup(exit_mode="trailing")
    book = placed(c, cfg)
    assert book.plans[0].targets == ()  # Trailing places no target order
    book, _ = filled(book, cfg)
    stops = []
    for i, high in enumerate((23140, 23135, 23150)):
        b = bar(T1 + i * NS_PER_MINUTE, high - 5, high, high - 6, high - 4)
        book, _, _ = on_bar(book, b, [], cfg)
        stops.append(book.plans[0].stop_price)
    assert stops == [23132, 23132, 23142]


def test_node_to_node_trailing_steps_back_one_node() -> None:
    cfg = config(
        exits={
            "global": {"mode": "trailing"},
            "modes": {"trailing": {"enabled": True}},
            "breakeven": {"enabled": False},
        }
    )
    book, _ = filled(placed(setup(exit_mode="trailing"), cfg), cfg)
    key = book.plans[0].key
    # Reaching the first Node beyond entry (5790): the stop goes to the source level.
    b1 = bar(T1, 23130, tick(5790.0), 23128, 23150)
    book, _, events = on_bar(book, b1, [], cfg)
    assert events == [StopMoved(key, b1.close_ns, 23100, tick(SOURCE), ("trail_node",))]
    # Reaching 5800: the stop goes back one Node, to 5790.
    b2 = bar(b1.close_ns, 23150, tick(5800.0) + 2, 23148, 23190)
    book, _, _ = on_bar(book, b2, [], cfg)
    assert book.plans[0].stop_price == tick(5790.0)


# ---------------------------------------------------------------- Cancel_Triggers


@pytest.mark.parametrize(
    ("changes", "fired"),
    [
        ({"s5760": 4.0e9}, ("king_flip",)),
        ({"s5780": 1.0e8}, ("source_node_gone",)),
        ({"s5780": -2.5e9}, ("sign_flip",)),
        ({"s5790": -2.2e9}, ("opposition_in_target",)),
        ({"s5760": 4.0e9, "s5780": -1.0e8}, ("king_flip", "source_node_gone", "sign_flip")),
    ],
)
def test_cancel_triggers_cancel_a_resting_entry_and_record_every_trigger(
    changes: dict[str, float], fired: tuple[CancelCause, ...]
) -> None:
    cfg = config()
    book = placed(setup(), cfg)
    plan = book.plans[0]
    out, intents, events = manage(book, ctx(T1, view(T1, **changes)), cfg)
    assert out.plans == ()
    assert intents == [CancelOrder(plan.entry.client_id, T1)]
    assert events == [EntryCancelled(plan.key, T1, fired)]


def test_a_changed_map_without_a_trigger_keeps_the_entry_at_its_prices() -> None:
    cfg = config()
    book = placed(setup(), cfg)
    out, intents, events = manage(book, ctx(T1, view(T1, s5760=1.1e9, s5850=2.0e8)), cfg)
    assert (out.plans, intents, events) == (book.plans, [], [])


def test_a_disabled_trigger_does_not_cancel() -> None:
    cfg = config(orders={"cancel_triggers": {"king_flip": False}})
    book = placed(setup(), cfg)
    out, intents, _ = manage(book, ctx(T1, view(T1, s5760=4.0e9)), cfg)
    assert (out.plans, intents) == (book.plans, [])


def test_stdev_leg_dropped_needs_the_leg_the_setup_used() -> None:
    cfg = config(gates={"stdev_fib_zone": {"enabled": True}})
    book = placed(setup(), cfg, legs={"MES": (LEG,)})
    assert book.plans[0].reference.leg == LEG
    out, _, events = manage(book, ctx(T1, view(T1), legs={"MES": (LEG,)}), cfg)
    assert (out.plans, events) == (book.plans, [])
    _, _, events = manage(book, ctx(T1, view(T1), legs={"MES": ()}), cfg)
    assert [e.causes for e in events if isinstance(e, EntryCancelled)] == [("stdev_leg_dropped",)]
    # With the Gate disabled the setup used no leg, so dropping it changes nothing.
    book = placed(setup(), config(), legs={"MES": (LEG,)})
    assert book.plans[0].reference.leg is None


def test_max_age_cancels_at_the_first_decision_time_it_is_reached() -> None:
    cfg = config(orders={"max_age_min": 5})
    book = placed(setup(), cfg)
    out, intents, _ = manage(book, ctx(T0 + 4 * NS_PER_MINUTE), cfg)
    assert (out.plans, intents) == (book.plans, [])
    at = T0 + 5 * NS_PER_MINUTE
    out, intents, events = manage(book, ctx(at), cfg)
    assert out.plans == ()
    assert events == [EntryCancelled(book.plans[0].key, at, ("max_age",))]


def test_a_lockout_cancels_resting_entries() -> None:
    cfg = config()
    book = placed(setup(), cfg)
    locked = RiskState(session=D, lockouts=(Lockout("red_day", T1, D),))
    out, intents, events = manage(book, ctx(T1, risk=locked), cfg)
    assert out.plans == ()
    assert intents == [CancelOrder(book.plans[0].entry.client_id, T1)]
    assert [e.causes for e in events if isinstance(e, EntryCancelled)] == [("red_day",)]
    assert drop_rejected(placed(setup(), cfg), setup().key).plans == ()


# ---------------------------------------------------------------- Flatten_Time


def test_flatten_time_cancels_resting_and_closes_open_positions() -> None:
    cfg = config(orders={"max_open": 2})
    second = setup(tap_seq=2)
    book = placed(setup(), cfg)
    book, _ = filled(book, cfg)
    book, _, _ = place(book, [accepted(second)], ctx(), cfg)
    open_plan, resting = book.plans
    flat = PlannerContext(t=FLATTEN, session=D, flatten_at=FLATTEN, sources=ctx().sources)
    out, intents, events = manage(book, flat, cfg)
    exit_order = Order(
        f"{open_plan.sid}-exit", open_plan.key, "MES", "sell", "market", 2, None, FLATTEN, "exit"
    )
    assert intents == [SubmitExit(exit_order), CancelOrder(resting.entry.client_id, FLATTEN)]
    assert events == [
        Flattened(open_plan.key, FLATTEN, 2),
        EntryCancelled(resting.key, FLATTEN, ("flatten_time",)),
    ]
    assert [p.exit_pending for p in out.plans] == [True]
    # Nothing more at the next Decision_Time: the exit is pending.
    later = replace(flat, t=FLATTEN + NS_PER_MINUTE)
    assert manage(out, later, cfg)[1:] == ([], [])


# ---------------------------------------------------------------- invalidation


def invalidation(**actions: str | None) -> PlannerConfig:
    return config(orders={"invalidation": actions})


@pytest.mark.parametrize(
    ("actions", "changes", "expected"),
    [
        # King flip (breakeven) and sign flip (exit at market): exit at market wins.
        (
            {"king_flip": "breakeven", "sign_flip": "exit_market"},
            {"s5760": 4.0e9, "s5780": -2.5e9},
            ("exit_market", "exit_market"),
        ),
        # Breakeven beats hold.
        (
            {"king_flip": "breakeven", "opposition_in_target": "hold"},
            {"s5760": 4.0e9, "s5790": -2.2e9},
            ("breakeven", "breakeven"),
        ),
        ({"opposition_in_target": "hold"}, {"s5790": -2.2e9}, ("hold", "hold")),
    ],
)
def test_invalidation_precedence(
    actions: dict[str, str], changes: dict[str, float], expected: tuple[str, str]
) -> None:
    cfg = invalidation(**actions)
    book, _ = filled(placed(setup(), cfg), cfg)
    plan = book.plans[0]
    out, intents, events = manage(book, ctx(T1, view(T1, **changes), last_close=23130), cfg)
    (inv,) = [e for e in events if isinstance(e, Invalidated)]
    assert (inv.action, inv.applied) == expected
    if expected[1] == "exit_market":
        assert [type(i) for i in intents] == [SubmitExit]
        assert out.plans[0].exit_pending
    elif expected[1] == "breakeven":
        assert intents == [ModifyOrder(plan.stop.client_id, 23121, T1)]
        assert out.plans[0].stop_price == 23121
    else:
        assert (intents, out.plans) == ([], book.plans)


@pytest.mark.parametrize("close", [23121, 23110])
def test_breakeven_with_the_close_past_breakeven_exits_at_market(close: int) -> None:
    cfg = invalidation(king_flip="breakeven")
    book, _ = filled(placed(setup(), cfg), cfg)
    out, intents, events = manage(book, ctx(T1, view(T1, s5760=4.0e9), last_close=close), cfg)
    assert out.plans[0].exit_pending
    (inv,) = events
    assert isinstance(inv, Invalidated)
    assert (inv.triggers, inv.action, inv.applied) == (("king_flip",), "breakeven", "exit_market")
    assert [type(i) for i in intents] == [SubmitExit]


def test_a_trigger_not_enabled_for_open_positions_is_not_recorded() -> None:
    cfg = invalidation(king_flip=None)
    book, _ = filled(placed(setup(), cfg), cfg)
    out, intents, events = manage(book, ctx(T1, view(T1, s5760=4.0e9), last_close=23130), cfg)
    assert (out.plans, intents, events) == (book.plans, [], [])
