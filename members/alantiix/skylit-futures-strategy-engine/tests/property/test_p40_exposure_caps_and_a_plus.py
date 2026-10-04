"""Property 40: Exposure caps and A_Plus-only entries.

*For any* sequence of Decision_Times, Candidate_Setups and fills, entry orders
are placed only for A_Plus Candidate_Setups (never for Alert_2R or Pass);
resting entries plus open positions never exceed ``max_open``; the
Micro_Equivalents of open positions plus resting entries never exceed the
sizing limit; and an order that would raise open plus working-entry
Micro_Equivalents above the account position cap is rejected with positions
and orders unchanged.

**The engine path.** Each example plays one session through the real
pipeline, as a runner drives it: :meth:`Engine.step` at every Decision_Time
(Setup_Detector, all 27 Gates and the Grade, the Position_Sizer against the
book's Micro_Equivalents, the Order_Planner), the bar phase between
Decision_Times (:meth:`Engine.update_stops` with the bar's fills, then
:meth:`Engine.consume_bar`), and the Account_Simulator's
:meth:`AccountSim.check_order` on every entry the engine places. An entry the
account refuses goes back to the next ``step`` as an :class:`EntryRejected`
event, which frees its slot.

The Map is the synthetic SPX gamma Map of ``tests/unit/test_engine_step.py``
(spot 5800, the ES-level instrument paired at 5800, so a strike's level is the
strike in points). With Next_Node exits the setups it arms have different
reward:risk values: ``gatekeeper_fade`` long at 5760 has 3.0 and at 5790 has
2.0, ``floor_ceiling_bounce`` long at 5750 has 0.2 and short at 5850 has 1.2.
Enabling the min_reward_risk Gate (and the tap_count Gate) therefore mixes
A_Plus, Alert_2R and Pass setups at one Decision_Time.

Generators:

- the ES-level instrument MES (1 Micro_Equivalent) or ES (10), with 1-12 MES
  or 1-4 ES fixed contracts;
- ``orders.max_open`` 1-10 (1 and 2 favoured), max age off or 1-4 minutes,
  Cancel_Triggers on or off;
- ``sizing.micro_equivalent_limit`` 1-60 and the account position cap 1-60
  (1-12 favoured), so either cap can bind first;
- the min_reward_risk Gate off (favoured) or on with (min, alert_min) giving
  every Grade mix, and the tap_count Gate off or on with ``max_tap_seq`` 1 or 2;
- 2-12 Decision_Times one minute apart, each after a 1-minute bar closing at
  a price drawn near the Map's Nodes (the bar opens at the previous close);
- in each bar phase, for every entry the account accepted and that still
  rests, a full fill at its limit or none, and for every open position a full
  stop fill or none. A market exit the planner sent fills in full at the next
  bar's open.

Checked at every Decision_Time and after every bar phase:

- every :class:`PlaceBracket` is for a Setup_Key graded A_Plus at that
  Decision_Time, logged as that setup's placement, with the sized contracts;
  every Alert_2R or Pass setup has no sizing and no placement, and every plan
  in the book was placed this way (Req 25.9);
- resting entries plus open positions, which is the number of plans, is at
  most ``max_open`` (Req 12.18);
- the Micro_Equivalents of open contracts plus unfilled entry contracts,
  counted here from the plans with 1 per MES and 10 per ES contract, equal
  :meth:`OrderBook.micro_equivalents` and are at most the limit (Req 14.4);
- each entry the account checks is refused exactly when the account's open
  Micro_Equivalents (from the test's own net position per instrument) plus
  its accepted, unfilled entries plus the order exceed the cap. A refusal
  names ``position_cap``, the total and the cap, and leaves the positions and
  the working entries unchanged; the engine's plan is gone after the next
  ``step`` (Req 15.5).

A second property drives :meth:`AccountSim.check_order` directly with entries
on all four instruments and both sides, so the cap counts MES, MNQ, ES and NQ
together and without regard to direction.

Kill_Switch counting (:meth:`Engine.count_fill`) is not fed here: Lockouts
only withhold entries and are Property 48's subject.

**Validates: Requirements 12.18, 14.4, 15.5, 25.9**
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Final

from hypothesis import event, given
from hypothesis import strategies as st

from fse.config.schema.account import AccountConfig
from fse.config.schema.data import DataConfig
from fse.config.schema.exits import ExitsConfig
from fse.config.schema.gates import GATE_IDS, GatesConfig
from fse.config.schema.orders import CANCEL_TRIGGERS, OrdersConfig
from fse.config.schema.regime import RegimeConfig
from fse.config.schema.sizing import SizingConfig
from fse.engine.planner import (
    CancelOrder,
    OrderBook,
    OrderFill,
    PlaceBracket,
    PlacementRejection,
    SubmitExit,
)
from fse.engine.sizing import SizingRejection
from fse.engine.state import EngineState
from fse.engine.step import Engine, EngineParams, EntryRejected, Sized, StepEvent, StepResult
from fse.engine.types import Bar, Fill, Order, SetupKey, Side, Snapshot
from fse.pit.market_view import HistoricalInputs
from fse.sim.account import AccountRejection, AccountSim
from fse.timekit import NS_PER_SECOND, SessionCalendar, SessionTimes, ny_instant

SESSION: Final = date(2026, 3, 5)
DT1: Final = ny_instant(SESSION, time(10, 0))
MINUTE: Final = 60 * NS_PER_SECOND
CALENDAR: Final = SessionCalendar(
    date(2026, 1, 1), date(2026, 12, 31), times=AccountConfig().session_times(SessionTimes())
)
ZERO: Final = Decimal("0.00")

ME_PER_CONTRACT: Final[Mapping[str, int]] = MappingProxyType(
    {"MES": 1, "MNQ": 1, "ES": 10, "NQ": 10}
)
"""The Glossary's Micro_Equivalents, written out here as the reference."""
FEES: Final[Mapping[str, Decimal]] = MappingProxyType(dict.fromkeys(ME_PER_CONTRACT, ZERO))

PAIRS: Final[tuple[tuple[float, float], ...]] = (
    (5700.0, -1.0e9),
    (5750.0, 3.0e9),
    (5760.0, 0.9e9),
    (5790.0, 0.9e9),
    (5850.0, -2.0e9),
    (5900.0, 1.0e9),
)
SNAPSHOT: Final = Snapshot(
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
CLOSES: Final[tuple[float, ...]] = (
    5752.0, 5755.0, 5762.0, 5788.0, 5795.0, 5800.0, 5820.0, 5848.0, 5850.0, 5702.0,
)  # fmt: skip
"""Bar closes near the Map's Nodes: each arms a different set of setups."""
REWARD_RISK: Final[tuple[tuple[float, float] | None, ...]] = (
    None,
    (2.0, 2.0),
    (2.5, 2.0),
    (2.5, 1.0),
    (3.0, 2.0),
    (3.5, 2.0),
)
"""min_reward_risk (min, alert_min); against 3.0, 2.0, 1.2 and 0.2 every Grade mix occurs."""


def ticks(points: float) -> int:
    return round(points * 4)


def make_bar(instrument: str, open_ns: int, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(
        instrument, f"{instrument}H6", 60, open_ns, open_ns + MINUTE, o, h, low, c, 100.0,
        ticks(o), ticks(h), ticks(low), ticks(c), "atlas",
    )  # fmt: skip


# ---------------------------------------------------------------- scenario


@dataclass(frozen=True, slots=True)
class Scenario:
    instrument: str
    contracts: int
    max_open: int
    max_age_min: int | None
    cancel_triggers: bool
    me_limit: int
    account_cap: int
    reward_risk: tuple[float, float] | None
    max_tap_seq: int | None
    closes: tuple[float, ...]

    def bars(self) -> tuple[Bar, ...]:
        """The pairing bar, then bar ``k`` closing at Decision_Time ``k``."""
        out = [make_bar(self.instrument, DT1 - 2 * MINUTE, 5800.0, 5801.0, 5799.0, 5800.0)]
        prev = 5800.0
        for k, c in enumerate(self.closes):
            open_ns = DT1 + (k - 1) * MINUTE
            out.append(
                make_bar(self.instrument, open_ns, prev, max(prev, c) + 1, min(prev, c) - 1, c)
            )
            prev = c
        return tuple(out)

    def engine(self) -> Engine:
        gates: dict[str, Any] = {g: {"enabled": False} for g in GATE_IDS}
        if self.reward_risk is not None:
            rr_min, alert_min = self.reward_risk
            gates["min_reward_risk"] = {"enabled": True, "min": rr_min, "alert_min": alert_min}
        if self.max_tap_seq is not None:
            gates["tap_count"] = {"enabled": True, "max_tap_seq": self.max_tap_seq}
        params = EngineParams(
            regime=RegimeConfig.model_validate({"min_abs_value": 1.0}),
            data=DataConfig.model_validate(
                {
                    "symbols": ["SPX", "QQQ"],
                    "nq_sources": ["QQQ"],
                    "instruments": {"es_levels": self.instrument},
                }
            ),
            gates=GatesConfig.model_validate(gates),
            exits=ExitsConfig.model_validate({"global": {"mode": "next_node"}}),
            sizing=SizingConfig.model_validate(
                {
                    "fixed": {self.instrument: self.contracts},
                    "micro_equivalent_limit": self.me_limit,
                    "trinity_size_down": {"enabled": False},
                    "vix_gap": {"enabled": False},
                }
            ),
            orders=OrdersConfig.model_validate(
                {
                    "max_open": self.max_open,
                    "max_age_min": self.max_age_min,
                    "cancel_triggers": dict.fromkeys(CANCEL_TRIGGERS, self.cancel_triggers),
                }
            ),
        )
        return Engine(params, CALENDAR)


@st.composite
def scenarios(draw: st.DrawFn) -> Scenario:
    instrument = draw(st.sampled_from(("MES", "ES")))
    return Scenario(
        instrument=instrument,
        contracts=draw(st.integers(1, 12 if instrument == "MES" else 4)),
        max_open=draw(st.integers(1, 2) | st.integers(1, 10)),
        max_age_min=draw(st.none() | st.integers(1, 4)),
        cancel_triggers=draw(st.booleans()),
        me_limit=draw(st.integers(1, 60)),
        account_cap=draw(st.integers(1, 12) | st.integers(1, 60)),
        reward_risk=draw(st.none() | st.sampled_from(REWARD_RISK)),
        max_tap_seq=draw(st.none() | st.sampled_from((1, 2))),
        closes=tuple(draw(st.lists(st.sampled_from(CLOSES), min_size=2, max_size=12))),
    )


# ---------------------------------------------------------------- the account side


def open_micro_equivalents(net: Mapping[str, int]) -> int:
    """Net open contracts per instrument in Micro_Equivalents, without regard to direction."""
    return sum(ME_PER_CONTRACT[i] * abs(q) for i, q in net.items())


def entry_micro_equivalents(orders: Sequence[Order]) -> int:
    return sum(ME_PER_CONTRACT[o.instrument] * o.qty for o in orders)


class Account:
    """An AccountSim with the test's own record of net positions and working entries."""

    def __init__(self, cap: int) -> None:
        cfg = AccountConfig.model_validate({"position_cap": {"micro_equivalents": cap}})
        self.cap = cap
        self.sim = AccountSim(cfg, CALENDAR, FEES)
        self.sim.start_trading_day(SESSION)
        self.working: dict[str, Order] = {}
        self.net: dict[str, int] = {}

    def check(self, order: Order) -> AccountRejection | None:
        """Submit entry ``order``; check the outcome against Req 15.5."""
        positions = dict(self.sim.positions)
        held = self.sim.open_micro_equivalents()
        working = tuple(self.working.values())
        total = (
            open_micro_equivalents(self.net)
            + entry_micro_equivalents(working)
            + ME_PER_CONTRACT[order.instrument] * order.qty
        )
        outcome = self.sim.check_order(order, working)
        # A check changes no position, accepted or not.
        assert dict(self.sim.positions) == positions
        assert self.sim.open_micro_equivalents() == held
        if total > self.cap:
            assert isinstance(outcome, AccountRejection), (total, self.cap)
            assert (outcome.rule, outcome.client_id) == ("position_cap", order.client_id)
            assert (outcome.total, outcome.cap) == (total, self.cap)
            event("account: position_cap rejection")
            return outcome
        assert outcome == order, (total, self.cap, outcome)
        self.working[order.client_id] = order
        return None

    def fill(self, order: Order, f: Fill) -> None:
        self.sim.on_fill(f, order)
        sign = 1 if order.side == "buy" else -1
        self.net[order.instrument] = self.net.get(order.instrument, 0) + sign * f.qty
        if order.role == "entry":
            del self.working[order.client_id]
        assert {i: q for i, q in self.sim.positions.items() if q} == {
            i: q for i, q in self.net.items() if q
        }
        assert self.sim.open_micro_equivalents() == open_micro_equivalents(self.net)


# ---------------------------------------------------------------- checks


def check_book(book: OrderBook, s: Scenario) -> None:
    """Req 12.18 and 14.4 on the planner's book."""
    assert len(book.resting) + len(book.open_positions) == len(book.plans) == book.exposure()
    assert book.exposure() <= s.max_open, (book.exposure(), s.max_open)
    used = sum(
        ME_PER_CONTRACT[p.instrument] * (p.open_qty + p.entry.qty - p.filled_qty)
        for p in book.plans
    )
    assert used == book.micro_equivalents()
    assert used <= s.me_limit, (used, s.me_limit)
    if book.open_positions:
        event("book: open position held")


def check_decisions(result: StepResult, bracketed: set[SetupKey]) -> list[PlaceBracket]:
    """Req 25.9: only A_Plus setups get an entry; returns the brackets placed at ``t``."""
    brackets = [i for i in result.intents if isinstance(i, PlaceBracket)]
    decisions = {d.setup.key: d for d in result.payload.setups}
    for b in brackets:
        key = b.entry.setup_key
        assert key is not None
        d = decisions[key]
        assert d.evaluation.grade == "A_Plus", (key, d.evaluation.grade)
        assert d.placement == b
        assert isinstance(d.sizing, Sized)
        assert b.entry.qty == b.stop.qty == d.sizing.contracts
        assert b.entry.role == "entry"
        bracketed.add(key)
    placed = [d for d in result.payload.setups if isinstance(d.placement, PlaceBracket)]
    assert len(placed) == len(brackets)
    for d in result.payload.setups:
        event(f"grade: {d.evaluation.grade}")
        if d.evaluation.grade != "A_Plus":
            assert (d.sizing, d.placement) == (None, None), d.setup.key
        elif isinstance(d.sizing, SizingRejection) and d.sizing.step == "micro_cap":
            event("sizer: micro_cap size_zero")
        elif isinstance(d.placement, PlacementRejection) and d.placement.reason == "max_open":
            event("planner: max_open rejection")
    assert {p.key for p in result.state.book.plans} <= bracketed
    return brackets


# ---------------------------------------------------------------- the runner


def bar_phase(
    eng: Engine,
    state: EngineState,
    bar: Bar,
    account: Account,
    exits: list[Order],
    data: st.DataObject,
) -> tuple[EngineState, list[StepEvent]]:
    """Draw this bar's fills, book them at the account, then the engine's bar hooks."""
    fills: list[OrderFill] = []
    for order in exits:  # market exits fill in full at the bar's open
        assert bar.o_t is not None
        fills.append(OrderFill(order, Fill(order.client_id, bar.open_ns, bar.o_t, order.qty, ZERO)))
    exits.clear()
    pending = {f.order.setup_key for f in fills}
    for plan in state.book.plans:
        # Only an entry the account accepted can fill; a refused one waits for its EntryRejected.
        resting = plan.status == "resting" and plan.entry.client_id in account.working
        held = plan.status == "open" and plan.key not in pending and not plan.exit_pending
        if resting and data.draw(st.booleans(), label=f"fill entry {plan.sid}"):
            entry = plan.entry
            fills.append(
                OrderFill(
                    entry, Fill(entry.client_id, bar.open_ns, plan.entry_limit, entry.qty, ZERO)
                )
            )
        elif held and data.draw(st.booleans(), label=f"stop out {plan.sid}"):
            stop = plan.stop
            fills.append(
                OrderFill(
                    stop, Fill(stop.client_id, bar.open_ns, plan.stop_price, plan.open_qty, ZERO)
                )
            )
    for f in fills:
        event(f"bar: {f.order.role} fill")
        account.fill(f.order, f.fill)
    stops = eng.update_stops(state, bar, fills)
    return stops.state, list(stops.events)


def route(
    result: StepResult, t: int, account: Account, exits: list[Order]
) -> tuple[list[StepEvent], list[SetupKey]]:
    """Send ``step``'s intents in order: cancels and exits, then entries to the account check."""
    events: list[StepEvent] = []
    refused: list[SetupKey] = []
    for intent in result.intents:
        if isinstance(intent, CancelOrder):
            account.working.pop(intent.client_id, None)
        elif isinstance(intent, SubmitExit):
            exits.append(intent.order)
        elif isinstance(intent, PlaceBracket):
            rejection = account.check(intent.entry)
            if rejection is not None:
                key = intent.entry.setup_key
                assert key is not None
                events.append(EntryRejected(key, t, rejection.message))
                refused.append(key)
    return events, refused


@given(s=scenarios(), data=st.data())
def test_entries_are_a_plus_only_and_within_every_cap(s: Scenario, data: st.DataObject) -> None:
    eng = s.engine()
    bars = s.bars()
    inputs = HistoricalInputs(symbols=("SPX", "QQQ"), view_id="v", snapshots=(SNAPSHOT,), bars=bars)
    account = Account(s.account_cap)
    state = eng.initial_state()
    events: list[StepEvent] = []
    refused: list[SetupKey] = []
    exits: list[Order] = []
    bracketed: set[SetupKey] = set()
    for k in range(len(s.closes)):
        t = DT1 + k * MINUTE
        view = inputs.view(t)
        if k > 0:
            bar = bars[k + 1]
            state, bar_events = bar_phase(eng, state, bar, account, exits, data)
            events.extend(bar_events)
            state = eng.consume_bar(state, bar, view)
            check_book(state.book, s)
        result = eng.step(state, view, t, events)
        for key in refused:  # an account refusal frees the slot at the next step
            assert result.state.book.plan(key) is None
        check_decisions(result, bracketed)
        check_book(result.state.book, s)
        events, refused = route(result, t, account, exits)
        state = result.state


# ---------------------------------------------------------------- the account on its own

INSTRUMENTS: Final[tuple[str, ...]] = tuple(ME_PER_CONTRACT)
SIDES: Final[tuple[Side, ...]] = ("buy", "sell")


@given(
    cap=st.integers(1, 60),
    entries=st.lists(
        st.tuples(
            st.sampled_from(INSTRUMENTS),
            st.sampled_from(SIDES),
            st.integers(1, 12),
            st.booleans(),
        ),
        min_size=1,
        max_size=15,
    ),
)
def test_the_account_cap_counts_every_instrument_without_direction(
    cap: int, entries: list[tuple[str, Side, int, bool]]
) -> None:
    account = Account(cap)
    for n, (instrument, side, qty, fills) in enumerate(entries):
        order = Order(f"entry-{n}", None, instrument, side, "limit", qty, 23200, DT1, "entry")
        rejected = account.check(order) is not None
        assert (order.client_id in account.working) is not rejected
        if fills and not rejected:
            account.fill(order, Fill(order.client_id, DT1, 23200, qty, ZERO))
