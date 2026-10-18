"""Unit tests for ``Engine.step`` and the bar-phase hooks (design "Decision loop").

One synthetic SPX gamma Map (spot 5800, MES paired at 5800, so a strike's MES
level is the strike in points) through the real pipeline: Node labels, Level
conversion, the eight detectors, all 27 Gates (disabled, so every priced setup
is A_Plus), the Position_Sizer and the Order_Planner.

Nodes 5700 (-1e9), 5750 (+3e9, Floor and King), 5760 and 5790 (+0.9e9,
Gatekeepers), 5850 (-2e9, Ceiling) and 5900 (+1e9). With MES at 5752 two setups
arm, in registry order: ``gatekeeper_fade`` long at 5760 (stop 5750, TP1 5790)
and ``floor_ceiling_bounce`` long at 5750 (stop 5700). With MES at 5788 only
``gatekeeper_fade`` long at 5790 arms.

**Validates: Requirements 5.3, 10.15, 16.7, 16.8, 23.5**
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, time
from decimal import Decimal
from typing import Any

import pytest

from fse.config.schema.chart import ChartConfig
from fse.config.schema.data import DataConfig
from fse.config.schema.gates import GATE_IDS, GatesConfig
from fse.config.schema.kill_switches import KillSwitchesConfig
from fse.config.schema.orders import CANCEL_TRIGGERS, OrdersConfig
from fse.config.schema.regime import RegimeConfig
from fse.config.schema.sizing import SizingConfig
from fse.engine.planner import (
    CancelOrder,
    EntryCancelled,
    ModifyOrder,
    OrderFill,
    PlaceBracket,
    PlacementRejection,
    StopMoved,
    SubmitExit,
)
from fse.engine.risk import RiskFill
from fse.engine.state import EngineState
from fse.engine.step import (
    BlockCancelled,
    BlockedEntry,
    DecisionPayload,
    Engine,
    EngineParams,
    EntryRejected,
    ExternalBlock,
    LossStopFired,
    StepResult,
)
from fse.engine.types import Bar, Fill, Order, SetupKey, Snapshot
from fse.logio import canonical_json
from fse.pit.market_view import HistoricalInputs
from fse.timekit import NS_PER_SECOND, SessionCalendar, ny_instant

SESSION = date(2026, 3, 5)
DT1 = ny_instant(SESSION, time(10, 0))
DT2 = DT1 + 60 * NS_PER_SECOND
MINUTE = 60 * NS_PER_SECOND
CALENDAR = SessionCalendar(date(2026, 1, 1), date(2026, 12, 31))

PAIRS = (
    (5700.0, -1.0e9),
    (5750.0, 3.0e9),
    (5760.0, 0.9e9),
    (5790.0, 0.9e9),
    (5850.0, -2.0e9),
    (5900.0, 1.0e9),
)
GK_KEY = SetupKey("MES", "gatekeeper_fade", 5760.0, "long", SESSION, 1)
FCB_KEY = SetupKey("MES", "floor_ceiling_bounce", 5750.0, "long", SESSION, 1)
GK_HIGH_KEY = SetupKey("MES", "gatekeeper_fade", 5790.0, "long", SESSION, 1)


def ticks(points: float) -> int:
    return round(points * 4)


def mes_bar(open_ns: int, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(
        "MES", "MESH6", 60, open_ns, open_ns + MINUTE, o, h, low, c, 100.0,
        ticks(o), ticks(h), ticks(low), ticks(c), "atlas",
    )  # fmt: skip


SNAPSHOT = Snapshot(
    symbol="SPX",
    metric="gamma",
    view_id="v",
    as_of_ns=DT1 - 30 * NS_PER_SECOND,
    as_of_raw="2026-03-05T14:59:30Z",
    spot=5800.0,
    previous_close=None,
    strikes=tuple(k for k, _ in PAIRS),
    values=tuple(v for _, v in PAIRS),
    node_types=None,
    expirations=("2026-03-05",),
    resolution="1s",
    source_endpoint="range",
    extra_json="{}",
)
PAIRING_BAR = mes_bar(DT1 - 2 * MINUTE, 5800.0, 5801.0, 5799.0, 5800.0)
BAR_DT1 = mes_bar(DT1 - MINUTE, 5760.0, 5761.0, 5751.0, 5752.0)
BAR_FLAT = mes_bar(DT1, 5752.0, 5753.0, 5751.0, 5752.0)  # MES stays at 5752
BAR_UP = mes_bar(DT1, 5752.0, 5790.0, 5751.0, 5788.0)  # fills 5760, closes at 5788


def inputs(second_bar: Bar = BAR_FLAT) -> HistoricalInputs:
    return HistoricalInputs(
        symbols=("SPX", "QQQ"),
        view_id="v",
        snapshots=(SNAPSHOT,),
        bars=(PAIRING_BAR, BAR_DT1, second_bar),
    )


def engine(
    *,
    orders: dict[str, Any] | None = None,
    kill_switches: dict[str, Any] | None = None,
) -> Engine:
    params = EngineParams(
        regime=RegimeConfig.model_validate({"min_abs_value": 1.0}),
        data=DataConfig.model_validate({"symbols": ["SPX", "QQQ"], "nq_sources": ["QQQ"]}),
        gates=GatesConfig.model_validate({g: {"enabled": False} for g in GATE_IDS}),
        sizing=SizingConfig.model_validate(
            {"trinity_size_down": {"enabled": False}, "vix_gap": {"enabled": False}}
        ),
        orders=OrdersConfig.model_validate(
            {"cancel_triggers": dict.fromkeys(CANCEL_TRIGGERS, False), **(orders or {})}
        ),
        kill_switches=KillSwitchesConfig.model_validate(kill_switches or {}),
    )
    return Engine(params, CALENDAR)


def entry_fill(order: Order, at: int = DT1) -> OrderFill:
    assert order.price is not None
    return OrderFill(order, Fill(order.client_id, at, order.price, order.qty, Decimal("0.00")))


def brackets(result: StepResult) -> list[PlaceBracket]:
    return [i for i in result.intents if isinstance(i, PlaceBracket)]


def placement(payload: DecisionPayload, key: SetupKey) -> object:
    [decision] = [d for d in payload.setups if d.setup.key == key]
    return decision.placement


@dataclass
class Run:
    """Two Decision_Times with the bar phase between them, as a runner drives them."""

    dt1: StepResult
    dt2: StepResult


def run(eng: Engine, second_bar: Bar = BAR_FLAT, *, fill_gk: bool = False) -> Run:
    data = inputs(second_bar)
    first = eng.step(eng.initial_state(), data.view(DT1), DT1)
    state = first.state
    events: list[Any] = []
    fills: list[OrderFill] = []
    if fill_gk:
        [gk] = [b for b in brackets(first) if b.entry.setup_key == GK_KEY]
        fills.append(entry_fill(gk.entry))
        state = eng.count_fill(state, RiskFill(fills[0].fill, "entry"))
    stops = eng.update_stops(state, second_bar, fills)
    events.extend(stops.events)
    state = eng.consume_bar(stops.state, second_bar, data.view(DT2))
    second = eng.step(state, data.view(DT2), DT2, events)
    return Run(first, second)


# ---------------------------------------------------------------- the pipeline


def test_the_first_decision_time_places_the_first_a_plus_setup() -> None:
    result = engine().step(engine().initial_state(), inputs().view(DT1), DT1)
    payload = result.payload
    assert [d.setup.key for d in payload.setups] == [GK_KEY, FCB_KEY]
    assert [d.evaluation.grade for d in payload.setups] == ["A_Plus", "A_Plus"]
    [bracket] = brackets(result)
    assert (bracket.entry.client_id, bracket.entry.price, bracket.entry.qty) == (
        "fse-000001-entry",
        ticks(5760),
        5,
    )
    assert (bracket.stop.price, [o.price for o in bracket.targets]) == (ticks(5750), [ticks(5790)])
    assert placement(payload, GK_KEY) == bracket
    rejection = placement(payload, FCB_KEY)
    assert isinstance(rejection, PlacementRejection)
    assert rejection.reason == "max_open"
    assert result.state.book.placed == frozenset({GK_KEY})
    assert (result.state.t, result.state.session) == (DT1, SESSION)


def test_the_payload_holds_the_decision_log_fields() -> None:
    payload = engine().step(engine().initial_state(), inputs().view(DT1), DT1).payload
    assert (payload.session, payload.t) == (SESSION, DT1)
    spx, *missing = payload.map_entries
    assert (spx.symbol, spx.metric, spx.missing, spx.as_of_ns) == (
        "SPX",
        "gamma",
        False,
        SNAPSHOT.as_of_ns,
    )
    assert (spx.king, spx.floor, spx.ceiling) == (5750.0, 5750.0, 5850.0)
    assert [(e.symbol, e.metric, e.missing) for e in missing] == [
        ("SPX", "vanna", True),
        ("QQQ", "gamma", True),
        ("QQQ", "vanna", True),
    ]
    mes, _ = payload.futures
    assert (mes.instrument, mes.price) == ("MES", ticks(5752))
    assert payload.snapshot_age_ns == 30 * NS_PER_SECOND
    assert all(len(d.evaluation.results) == len(GATE_IDS) for d in payload.setups)
    assert payload.cards.order_event
    assert [(c.key, c.before, c.after) for c in payload.cards.grade_changes] == [
        (GK_KEY, "Pass", "A_Plus"),
        (FCB_KEY, "Pass", "A_Plus"),
    ]
    # Every field encodes as canonical JSON for the decision log (Req 18.7).
    assert canonical_json.dumps(payload).startswith("{")


# ---------------------------------------------------------------- phase order


def test_management_runs_before_the_setup_phase() -> None:
    """At DT2 max age cancels the 5760 entry, which frees the slot for the 5750 setup."""
    result = run(engine(orders={"max_age_min": 1})).dt2
    cancel, bracket = result.intents
    assert cancel == CancelOrder("fse-000001-entry", DT2)
    assert isinstance(bracket, PlaceBracket)
    assert (bracket.entry.setup_key, bracket.entry.client_id) == (FCB_KEY, "fse-000002-entry")
    [cancelled] = result.payload.management
    assert cancelled == EntryCancelled(GK_KEY, DT2, ("max_age",))
    already = placement(result.payload, GK_KEY)
    assert isinstance(already, PlacementRejection)
    assert already.reason == "already_placed"
    assert result.payload.intents == result.intents


def test_a_rejected_entry_event_frees_its_slot_before_management() -> None:
    eng = engine()
    data = inputs()
    first = eng.step(eng.initial_state(), data.view(DT1), DT1)
    refused = EntryRejected(GK_KEY, DT1, "account position cap")
    second = eng.step(first.state, data.view(DT2), DT2, [refused])
    assert second.state.book.plan(GK_KEY) is None
    assert [b.entry.setup_key for b in brackets(second)] == [FCB_KEY]
    assert second.payload.bar_events == (refused,)
    assert GK_KEY in second.state.book.placed


# ---------------------------------------------------------------- determinism and restart


def test_identical_inputs_give_identical_states_intents_and_payloads() -> None:
    a = run(engine(orders={"max_age_min": 1}))
    b = run(engine(orders={"max_age_min": 1}))
    assert a == b
    for x, y in ((a.dt1, b.dt1), (a.dt2, b.dt2)):
        assert canonical_json.dumps(x.payload) == canonical_json.dumps(y.payload)
        assert x.state.to_canonical_json() == y.state.to_canonical_json()


def test_a_restored_state_continues_like_an_uninterrupted_run() -> None:
    eng = engine(orders={"max_age_min": 1})
    data = inputs()
    whole = run(eng)
    restored = EngineState.from_json(whole.dt1.state.to_canonical_json())
    assert restored == whole.dt1.state
    stops = eng.update_stops(restored, BAR_FLAT)
    state = eng.consume_bar(stops.state, BAR_FLAT, data.view(DT2))
    again = eng.step(state, data.view(DT2), DT2, stops.events)
    assert again == whole.dt2


# ---------------------------------------------------------------- blocks and lockouts


def test_external_blocks_withhold_entries() -> None:
    eng = engine()
    view = inputs().view(DT1)
    blocked = eng.step(eng.initial_state(), view, DT1, (), [ExternalBlock("stale_map")])
    assert blocked.intents == ()
    assert [placement(blocked.payload, k) for k in (GK_KEY, FCB_KEY)] == [
        BlockedEntry(GK_KEY, DT1, ("stale_map",)),
        BlockedEntry(FCB_KEY, DT1, ("stale_map",)),
    ]
    assert blocked.payload.external_blocks == (ExternalBlock("stale_map"),)
    assert blocked.state.book.placed == frozenset()
    other = eng.step(eng.initial_state(), view, DT1, (), [ExternalBlock("broker", "MNQ")])
    assert [b.entry.setup_key for b in brackets(other)] == [GK_KEY]


def test_an_external_block_cancels_resting_entries_of_its_instrument() -> None:
    eng = engine()
    data = inputs()
    first = eng.step(eng.initial_state(), data.view(DT1), DT1)
    halt = ExternalBlock("halt", "MES", "halt file present")
    second = eng.step(first.state, data.view(DT2), DT2, (), [halt])
    assert second.intents == (CancelOrder("fse-000001-entry", DT2),)
    assert second.payload.management == (BlockCancelled(GK_KEY, DT2, ("halt",)),)
    assert second.state.book.plans == ()
    assert placement(second.payload, FCB_KEY) == BlockedEntry(FCB_KEY, DT2, ("halt",))
    already = placement(second.payload, GK_KEY)
    assert isinstance(already, PlacementRejection)
    assert already.reason == "already_placed"


def test_a_lockout_blocks_entries_but_not_exits() -> None:
    """max_trades 1: the 5760 entry fill locks out; its stop still moves, the 5750 entry goes."""
    eng = engine(orders={"max_open": 2}, kill_switches={"max_trades": {"max": 1}})
    result = run(eng, BAR_UP, fill_gk=True)
    assert len(brackets(result.dt1)) == 2
    gk = result.dt2.state.book.plan(GK_KEY)
    assert gk is not None
    assert (gk.status, gk.open_qty, gk.stop_price) == ("open", 5, ticks(5760) + 1)
    # The bar phase moved the stop to breakeven under the Lockout (exits keep working).
    [moved] = result.dt2.payload.bar_events
    assert isinstance(moved, StopMoved)
    assert (moved.key, moved.new, moved.rules) == (GK_KEY, ticks(5760) + 1, ("breakeven",))
    # The resting 5750 entry is cancelled; the open position's orders are untouched.
    assert result.dt2.intents == (CancelOrder("fse-000002-entry", DT2),)
    assert not any(isinstance(i, SubmitExit) for i in result.dt2.intents)
    # The new 5790 setup is withheld by the Lockout.
    withheld = placement(result.dt2.payload, GK_HIGH_KEY)
    assert isinstance(withheld, PlacementRejection)
    assert (withheld.reason, withheld.detail) == ("kill_switch_lockout", ("max_trades",))
    assert [lo.rule for lo in result.dt2.payload.lockouts] == ["max_trades"]
    assert [f.fill.client_id for f in result.dt2.payload.fills] == ["fse-000001-entry"]


def test_the_loss_stop_closes_open_plans_at_market_and_locks_out() -> None:
    eng = engine(
        orders={"max_open": 2},
        kill_switches={"internal_daily_loss_stop": {"enabled": True}},
    )
    data = inputs(BAR_UP)
    first = eng.step(eng.initial_state(), data.view(DT1), DT1)
    [gk, _] = brackets(first)
    fill = entry_fill(gk.entry)
    state = eng.count_fill(first.state, RiskFill(fill.fill, "entry"))
    stops = eng.update_stops(state, BAR_UP, [fill])
    hit = eng.check_loss_stop(stops.state, BAR_UP.close_ns, {"MES": 5}, Decimal("-750.00"))
    [exit_intent] = hit.intents
    assert isinstance(exit_intent, SubmitExit)
    assert (exit_intent.order.client_id, exit_intent.order.kind, exit_intent.order.qty) == (
        "fse-000001-exit",
        "market",
        5,
    )
    [event] = hit.events
    assert isinstance(event, LossStopFired)
    assert event.hit.limit == Decimal("-750.0")
    plan = hit.state.book.plan(GK_KEY)
    assert plan is not None
    assert plan.exit_pending
    # A second check in the session changes nothing (the stop fires once).
    again = eng.check_loss_stop(hit.state, BAR_UP.close_ns, {"MES": 5}, Decimal("-900"))
    assert (again.intents, again.events) == ((), ())
    state = eng.consume_bar(hit.state, BAR_UP, data.view(DT2))
    second = eng.step(state, data.view(DT2), DT2, (*stops.events, *hit.events))
    # The resting entry is cancelled by the Lockout; the pending exit is not resent.
    assert second.intents == (CancelOrder("fse-000002-entry", DT2),)
    assert [lo.rule for lo in second.payload.lockouts] == ["internal_daily_loss_stop"]


# ---------------------------------------------------------------- bar-phase hooks


def test_consume_bar_feeds_the_taps_and_the_chart() -> None:
    eng = engine()
    data = inputs(BAR_UP)
    state = eng.consume_bar(eng.initial_state(), BAR_UP, data.view(DT2))
    view = state.taps.view(DT2)
    assert [view.session_count(("SPX", "gamma", k)) for k in (5750.0, 5760.0, 5790.0, 5850.0)] == [
        1,
        1,
        1,
        0,
    ]
    assert state.chart.instrument_names() == ("MES",)
    with pytest.raises(ValueError, match="MarketView"):
        eng.consume_bar(eng.initial_state(), BAR_UP, data.view(DT1))


def test_update_stops_echoes_only_fills_of_the_book() -> None:
    eng = engine()
    first = eng.step(eng.initial_state(), inputs(BAR_UP).view(DT1), DT1)
    [gk] = brackets(first)
    own = entry_fill(gk.entry)
    shadow_order = replace(gk.entry, client_id="shadow-entry", setup_key=FCB_KEY)
    shadow = entry_fill(shadow_order)
    result = eng.update_stops(first.state, BAR_UP, [own, shadow])
    assert result.events[0] == own
    assert all(e != shadow for e in result.events)
    assert any(isinstance(i, ModifyOrder) for i in result.intents)


def test_count_fill_keeps_the_risk_counters() -> None:
    eng = engine()
    first = eng.step(eng.initial_state(), inputs().view(DT1), DT1)
    [gk] = brackets(first)
    state = eng.count_fill(first.state, RiskFill(entry_fill(gk.entry).fill, "entry"))
    assert state.risk.session_entries == 1
    assert state.risk.session == SESSION


# ---------------------------------------------------------------- refusals


def test_step_refuses_inputs_out_of_order() -> None:
    eng = engine()
    data = inputs()
    first = eng.step(eng.initial_state(), data.view(DT1), DT1)
    with pytest.raises(ValueError, match="MarketView is at"):
        eng.step(first.state, data.view(DT2), DT2 + 1)
    with pytest.raises(ValueError, match="not after"):
        eng.step(first.state, data.view(DT1), DT1)
    other_chart = EngineState.initial(ChartConfig.model_validate({"sweep_ticks": 5}))
    with pytest.raises(ValueError, match="chart config"):
        eng.step(other_chart, data.view(DT1), DT1)


def test_params_build_every_module_parameter() -> None:
    params = engine().params
    assert params.instruments == ("MES", "MNQ")
    assert params.planner.orders is params.orders
    assert params.gate_params.gates is params.gates
    assert params.regime_params.symbol == "SPX"
    assert EngineParams.from_sections(params) == params
