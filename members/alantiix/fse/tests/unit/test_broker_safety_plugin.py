"""BrokerSafety's generic plugin-host options (design D1, T6-T9, T7b, round-4 A1-A3).

Every option defaults off, so ``fse live`` keeps its request sequence
(``tests/integration/test_broker_flows.py`` and properties 74-78 pin that).
These tests drive :class:`~fse.live.broker_safety.BrokerSafety` against
:class:`tests.fakes.broker.FakeAdapter` with no network.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, cast

import pytest

from fse.config.schema.live import LiveConfig
from fse.engine.types import Order, SetupKey
from fse.live.broker_safety import (
    BrokerSafety,
    ClientIds,
    Expectation,
    InstrumentView,
    OrderJournal,
    OrderView,
    reconcile,
)
from fse.live.order_router import (
    BrokerAction,
    CancelAction,
    ExitAction,
    ModifyAction,
    PlaceAction,
)
from fse.live.state_store import BlockStore, BlocksUnreadable
from fse.logio import LogWriter, Redactor
from fse.logio.canonical_json import JsonValue
from fse.projectx.broker import BrokerAdapter, BrokerState, EntryIntent, OrderRequest
from fse.sim.fills import OpenTrade
from fse.timekit import NS_PER_SECOND, parse_rfc3339
from tests.fakes.broker import CONTRACTS, FakeAdapter
from tests.fakes.clock import FakeClock

S = NS_PER_SECOND
T0 = parse_rfc3339("2026-03-02T14:35:00Z")
KEY = SetupKey("MNQ", "alpha", 0.0, "long", date(2026, 3, 2), 1)
KEY2 = SetupKey("MNQ", "alpha", 0.0, "long", date(2026, 3, 2), 2)
H27 = "CON.F.US.MNQ.H27"


def order(cid: str, side: str, kind: str, qty: int, role: str, key: SetupKey = KEY) -> Order:
    return Order(cid, key, "MNQ", side, kind, qty, None, T0, role)  # type: ignore[arg-type]


ENTRY = order("p:t1:entry", "buy", "market", 2, "entry")
# A tick-bracket leg has no engine price: it is a market-kind placeholder (price None).
STOP = order("p:t1:stop", "sell", "market", 2, "stop")
TP1 = order("p:t1:tp1", "sell", "market", 2, "tp1")
EXIT_ORDER = order("p:t1:exit", "sell", "market", 2, "exit")


def place(**kw: Any) -> PlaceAction:
    args: dict[str, Any] = {"stop_ticks": 40, "target_ticks": 80} | kw
    return PlaceAction(ENTRY, STOP, (TP1,), **args)


def exit_action(qty: int = 2, **kw: Any) -> ExitAction:
    return ExitAction(EXIT_ORDER, "sell", qty, **kw)


@dataclass
class _Trade:
    instrument: str
    sign: int
    open_qty: int


class Book:
    """A minimal ExpectedBook."""

    def __init__(self, *orders: Order) -> None:
        self.orders = {o.client_id: o for o in orders}
        self.open: dict[SetupKey, OpenTrade] = {}

    def working_orders(self) -> tuple[Order, ...]:
        return tuple(self.orders.values())

    @property
    def trades(self) -> Mapping[SetupKey, OpenTrade]:
        return self.open

    def known_order(self, client_id: str) -> Order | None:
        return self.orders.get(client_id)


@dataclass
class H:
    clock: FakeClock
    fake: FakeAdapter
    safety: BrokerSafety
    book: Book
    logs: list[dict[str, JsonValue]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def events(self, name: str) -> list[dict[str, JsonValue]]:
        return [e for e in self.logs if e.get("event") == name]

    def places(self) -> list[OrderRequest]:
        return [r for n, r in self.fake.calls if n == "place"]

    def brackets(self) -> list[EntryIntent]:
        return [r for n, r in self.fake.calls if n == "place_bracketed"]

    async def run(self, coro: Any) -> Any:
        return await self.clock.run(coro)

    def submit(self, *actions: BrokerAction) -> None:
        for a in actions:
            if isinstance(a, PlaceAction):
                self.book.orders.update((o.client_id, o) for o in (a.entry, a.stop, *a.targets))
        self.safety.submit(self.clock.now(), list(actions), Expectation.of(self.book))

    async def send(self, *actions: BrokerAction) -> None:
        self.submit(*actions)
        await self.run(self.safety.step())


def harness(
    tmp: Path,
    *,
    exit_read: bool = True,
    entry_gate: Callable[[str], str | None] | None = None,
    adopt_since_ns: int | None = None,
    session_start_ns: int = T0,
    journal: OrderJournal | None = None,
    ids: ClientIds | None = None,
    clock: FakeClock | None = None,
    fake: FakeAdapter | None = None,
    book: Book | None = None,
) -> H:
    clock = clock or FakeClock(T0)
    fake = fake or FakeAdapter(clock, oco_legs=True)
    writer = LogWriter(Redactor(), stdout=io.StringIO(), stderr=io.StringIO())
    journal = journal or OrderJournal(writer, tmp)
    ids = ids or ClientIds(writer, tmp, journal, "0a1b2c3d")
    safety = BrokerSafety(
        adapter=cast(BrokerAdapter, fake),
        live=LiveConfig(),
        instruments=sorted(CONTRACTS),
        expected_contracts=CONTRACTS,
        blocks=BlockStore(writer, tmp),
        journal=journal,
        ids=ids,
        clock=clock,
        session_start_ns=session_start_ns,
        entry_gate=entry_gate,
        exit_read=exit_read,
        adopt_since_ns=adopt_since_ns,
    )
    h = H(clock, fake, safety, book or Book())
    safety.bind(h.book, h.notes.append, h.logs.append)
    return h


async def started(tmp: Path, **kw: Any) -> H:
    h = harness(tmp, **kw)
    await h.run(h.safety.start())
    return h


# ---------------------------------------------------------------- T6 market entries, contracts


@pytest.mark.asyncio
async def test_a_market_place_action_sends_one_market_bracket(tmp_path: Path) -> None:
    h = await started(tmp_path)
    await h.send(place())
    (intent,) = h.brackets()
    assert intent.kind == "market"
    assert intent.bracket_ticks == (40, 80)
    assert intent.qty == 2
    assert intent.contract_id == CONTRACTS["MNQ"]


@pytest.mark.asyncio
async def test_a_missing_tick_field_is_skipped_with_the_engine_id(tmp_path: Path) -> None:
    h = await started(tmp_path)
    await h.send(place(target_ticks=None))
    assert not h.brackets()
    (skip,) = h.events("broker_skip")
    assert skip["engine_id"] == ENTRY.client_id
    assert str(skip["reason"]).startswith("invalid market bracket")


@pytest.mark.asyncio
async def test_no_checked_contract_skips_every_action_type_with_its_engine_id(
    tmp_path: Path,
) -> None:
    clock = FakeClock(T0)
    fake = FakeAdapter(clock, contracts={"MES": CONTRACTS["MES"]})
    h = await started(tmp_path, clock=clock, fake=fake)
    await h.send(
        place(),
        CancelAction("p:t1:entry", "MNQ", "entry", KEY),
        ModifyAction("p:t1:stop", "MNQ", "stop", 100),
        exit_action(),
    )
    skips = [e for e in h.events("broker_skip") if e["reason"] == "no checked contract"]
    assert [e["engine_id"] for e in skips] == [
        "p:t1:entry",
        "p:t1:entry",
        "p:t1:stop",
        "p:t1:exit",
    ]
    assert not h.fake.sends()


@pytest.mark.asyncio
async def test_an_exit_with_no_checked_contract_goes_out_on_the_held_contract(
    tmp_path: Path,
) -> None:
    clock = FakeClock(T0)
    fake = FakeAdapter(clock, contracts={"MES": CONTRACTS["MES"]}, held_contracts={"MNQ": H27})
    fake.positions["MNQ"] = 2
    h = await started(tmp_path, clock=clock, fake=fake)
    await h.send(exit_action())
    (sent,) = h.places()
    assert sent.contract_id == H27
    assert sent.qty == 2
    assert h.fake.positions["MNQ"] == 0


@pytest.mark.parametrize("exit_read", [True, False])
@pytest.mark.asyncio
async def test_an_exit_with_no_contract_anywhere_is_skipped(
    tmp_path: Path, exit_read: bool
) -> None:
    clock = FakeClock(T0)
    fake = FakeAdapter(clock, contracts={"MES": CONTRACTS["MES"]})
    h = await started(tmp_path, clock=clock, fake=fake, exit_read=exit_read)
    await h.send(exit_action())
    assert not h.places()
    (skip,) = h.events("broker_skip")
    assert skip["engine_id"] == "p:t1:exit"


# ---------------------------------------------------------------- T7 exit sizing


@pytest.mark.asyncio
async def test_an_exit_on_a_flat_broker_sends_nothing_and_cancels_nothing(tmp_path: Path) -> None:
    h = await started(tmp_path)
    await h.send(exit_action(legs=("p:t1:stop", "p:t1:tp1")))
    assert not h.places()
    assert not [c for c in h.fake.calls if c[0] == "cancel"]
    (skip,) = h.events("broker_skip")
    assert skip == {
        "t": T0,
        "event": "broker_skip",
        "instrument": "MNQ",
        "reason": "exit: the broker holds nothing to close",
        "engine_id": "p:t1:exit",
    }


@pytest.mark.asyncio
async def test_an_exit_is_cut_to_the_held_quantity(tmp_path: Path) -> None:
    h = harness(tmp_path)
    h.fake.positions["MNQ"] = 1
    await h.run(h.safety.start())
    await h.send(exit_action(qty=2))
    assert [r.qty for r in h.places()] == [1]


@pytest.mark.asyncio
async def test_without_exit_read_the_request_sequence_is_unchanged(tmp_path: Path) -> None:
    h = await started(tmp_path, exit_read=False)
    before = len(h.safety.requests)
    h.safety.submit(T0, [exit_action()], h.safety._expect)
    t, actions = h.safety._queue[0]
    await h.run(h.safety.dispatch(t, actions[0]))
    assert h.safety.requests[before:] == ["place"]  # no fresh read, qty as routed
    assert [r.qty for r in h.places()] == [2]


@pytest.mark.asyncio
async def test_drain_empties_the_queue_and_entries_false_sends_no_entry(tmp_path: Path) -> None:
    h = harness(tmp_path)
    h.fake.positions["MNQ"] = 2
    await h.run(h.safety.start())
    h.submit(place())
    h.safety.submit(T0, [exit_action()], h.safety._expect)
    await h.run(h.safety.drain(entries=False))
    assert not h.safety._queue
    assert not h.brackets()
    assert [r.qty for r in h.places()] == [2]
    (skip,) = h.events("broker_skip")
    assert skip["reason"] == "runner stopping"
    assert skip["engine_id"] == ENTRY.client_id
    flat = await started(tmp_path / "flat")
    flat.submit(place())
    await flat.run(flat.safety.drain())
    assert not flat.safety._queue
    assert len(flat.brackets()) == 1


@pytest.mark.asyncio
async def test_filled_reports_the_last_sync_fill_count(tmp_path: Path) -> None:
    h = await started(tmp_path)
    assert h.safety.filled(ENTRY.client_id) == 0
    await h.send(place())
    assert h.safety.filled(ENTRY.client_id) == 2
    assert h.safety.filled("unknown") == 0


@pytest.mark.asyncio
async def test_the_exit_uses_the_contract_the_broker_holds(tmp_path: Path) -> None:
    clock = FakeClock(T0)
    fake = FakeAdapter(clock, held_contracts={"MNQ": H27})  # checked: Z26, held: H27
    fake.positions["MNQ"] = 2
    h = await started(tmp_path, clock=clock, fake=fake)
    assert h.safety.contracts["MNQ"] == CONTRACTS["MNQ"]
    await h.send(exit_action())
    assert [r.contract_id for r in h.places()] == [H27]


@pytest.mark.asyncio
async def test_an_exit_whose_entry_may_still_fill_is_deferred(tmp_path: Path) -> None:
    h = await started(tmp_path)
    h.fake.show_fill_after(5)
    await h.send(place())
    await h.send(exit_action(entry_id=ENTRY.client_id))
    assert not h.places()
    (deferred,) = h.events("broker_exit_deferred")
    assert deferred["engine_id"] == "p:t1:exit"
    (p,) = h.safety.pending.values()
    assert isinstance(p.request, OrderRequest)
    assert p.request.qty == 2
    assert p.defer_for == ENTRY.client_id


@pytest.mark.parametrize("entry_id", [None, "filled"])
@pytest.mark.asyncio
async def test_an_exit_with_no_entry_or_a_filled_one_is_skipped(
    tmp_path: Path, entry_id: str | None
) -> None:
    h = await started(tmp_path)
    if entry_id is not None:
        await h.send(place())  # fills at once
        await h.send(exit_action())  # closes it
        entry_id = ENTRY.client_id
    await h.send(exit_action(entry_id=entry_id))
    assert not h.events("broker_exit_deferred")
    assert h.events("broker_skip")[-1]["reason"] == "exit: the broker holds nothing to close"


@pytest.mark.asyncio
async def test_resubmit_dropped_carries_the_engine_id(tmp_path: Path) -> None:
    h = harness(tmp_path)
    h.fake.positions["MNQ"] = 2
    await h.run(h.safety.start())
    h.fake.place_outcomes.append("dropped")
    h.fake.positions["MNQ"] = 2
    h.safety.submit(T0, [exit_action()], h.safety._expect)
    t, actions = h.safety._queue.pop(0)
    await h.run(h.safety.dispatch(t, actions[0]))
    assert h.safety.pending
    h.fake.positions["MNQ"] = 0
    await h.run(h.safety.sync())
    (dropped,) = h.events("resubmit_dropped")
    assert dropped["engine_id"] == "p:t1:exit"


@pytest.mark.asyncio
async def test_an_accepted_exit_cancels_each_leg_once(tmp_path: Path) -> None:
    h = await started(tmp_path)
    await h.send(place())  # fills; the OCO legs are adopted
    legs = [h.safety.journal.order_for_engine(c) or 0 for c in ("p:t1:stop", "p:t1:tp1")]
    assert 0 not in legs
    await h.send(exit_action(legs=("p:t1:stop", "p:t1:tp1")))
    cancels = [oid for n, oid in h.fake.calls if n == "cancel"]
    assert sorted(cancels) == sorted(legs)


@pytest.mark.asyncio
async def test_a_rejected_exit_cancels_no_leg(tmp_path: Path) -> None:
    h = await started(tmp_path)
    await h.send(place())
    h.fake.place_outcomes.append("rejected")
    await h.send(exit_action(legs=("p:t1:stop", "p:t1:tp1")))
    assert not [c for c in h.fake.calls if c[0] == "cancel"]


# ---------------------------------------------------------------- T7b adoption start, hand-over


@pytest.mark.asyncio
async def test_entries_sent_before_adopt_since_are_not_adopted_again(tmp_path: Path) -> None:
    early = T0 - 3600 * S
    clock = FakeClock(early)
    fake = FakeAdapter(clock, oco_legs=True)
    first = await started(tmp_path, clock=clock, fake=fake, session_start_ns=early)
    await first.send(place())
    await first.send(exit_action())
    clock.advance_to(T0)
    second = harness(
        tmp_path,
        clock=clock,
        fake=fake,
        journal=first.safety.journal,
        ids=first.safety._ids,
        session_start_ns=early,
        adopt_since_ns=T0,
    )
    await second.run(second.safety.start())
    assert not second.events("entry_filled")
    assert not second.safety._settling
    assert ENTRY.client_id in second.safety._filled_entries
    assert second.safety.filled("p:t1:exit") == 2


@pytest.mark.asyncio
async def test_take_queued_returns_the_queue_in_order(tmp_path: Path) -> None:
    h = harness(tmp_path)
    a, b, c = place(), exit_action(), CancelAction("x", "MNQ", "entry", KEY)
    h.safety.submit(T0, [a, b], h.safety._expect)
    h.safety.submit(T0, [c], h.safety._expect)
    assert h.safety.take_queued() == [a, b, c]
    assert not h.safety._queue


# ---------------------------------------------------------------- T7c ProjectX -SL/-TP tags


@pytest.mark.asyncio
async def test_auto_oco_legs_tagged_sl_tp_are_adopted_as_engine_stop_and_tp1(
    tmp_path: Path,
) -> None:
    """Live: ProjectX tags children fse-<id>-SL/-TP; book expects alpha:…:stop/tp1."""
    h = await started(tmp_path)
    await h.send(place())
    (intent,) = h.brackets()
    assert intent.client_id.startswith("fse-")
    tagged = {
        o.tag: o
        for o in h.fake.orders.values()
        if o.tag in (f"{intent.client_id}-SL", f"{intent.client_id}-TP")
    }
    sl, tp = f"{intent.client_id}-SL", f"{intent.client_id}-TP"
    assert set(tagged) == {sl, tp}
    assert h.safety.journal.order_for_engine("p:t1:stop") == tagged[sl].order_id
    assert h.safety.journal.order_for_engine("p:t1:tp1") == tagged[tp].order_id
    assert h.safety.journal.engine_for_order(tagged[sl]) == "p:t1:stop"
    assert h.safety.journal.engine_for_order(tagged[tp]) == "p:t1:tp1"
    # After settle window, reconcile must key both sides by engine ids (not fse-*-SL/TP).
    h.clock.advance_to(h.clock.now() + h.safety._live.stop_confirm_s * S + 1)
    await h.run(h.safety.sync())
    assert not h.events("reconciliation")[-1]["differences"]
    blocks = h.safety._blocks.read()
    assert not isinstance(blocks, BlocksUnreadable)
    assert "reconciliation" not in {b.kind for b in blocks}


@pytest.mark.asyncio
async def test_untagged_auto_oco_legs_are_still_adopted(tmp_path: Path) -> None:
    """Older gateway shape (no -SL/-TP tags) keeps working."""
    clock = FakeClock(T0)
    fake = FakeAdapter(clock, oco_legs=True, oco_tag=False)
    h = await started(tmp_path, clock=clock, fake=fake)
    await h.send(place())
    assert h.safety.journal.order_for_engine("p:t1:stop") is not None
    assert h.safety.journal.order_for_engine("p:t1:tp1") is not None
    assert all(
        o.tag is None or not o.tag.endswith(("-SL", "-TP")) for o in h.fake.orders.values()
    )


@pytest.mark.asyncio
async def test_long_market_bracket_sends_negative_stop_and_positive_target_ticks(
    tmp_path: Path,
) -> None:
    """23:00 long: SL below fill (-stop), TP above (+target)."""
    h = await started(tmp_path)
    await h.send(place(stop_ticks=214, target_ticks=54))
    (intent,) = h.brackets()
    assert intent.side == "buy"
    body = intent.payload(101)
    assert body["stopLossBracket"] == {"ticks": -214, "type": 4}
    assert body["takeProfitBracket"] == {"ticks": 54, "type": 1}


# ---------------------------------------------------------------- T8 reconcile


def test_reconcile_skips_the_price_only_when_the_expected_price_is_none() -> None:
    def run(want: OrderView, have: OrderView, unpriced: bool = True) -> tuple[str, ...]:
        return reconcile(
            {"MNQ": InstrumentView(2, (want,))},
            {"MNQ": InstrumentView(2, (have,))},
            instruments=["MNQ"],
            ignored=[],
            unpriced_legs=unpriced,
        ).differences

    # fse live (the default) still compares every price
    assert run(OrderView("s", "sell", 2, None), OrderView("s", "sell", 2, 123), False)
    assert run(OrderView("s", "sell", 2, None), OrderView("s", "sell", 2, 123)) == ()
    assert run(OrderView("s", "sell", 2, 100), OrderView("s", "sell", 2, 123))
    assert run(OrderView("s", "buy", 2, None), OrderView("s", "sell", 2, 123))
    assert run(OrderView("s", "sell", 1, None), OrderView("s", "sell", 2, 123))


# ---------------------------------------------------------------- T9 entry gate


@pytest.mark.asyncio
async def test_the_entry_gate_blocks_a_first_send_and_a_resend(tmp_path: Path) -> None:
    gate: dict[str, str | None] = {"reason": "global HALT"}
    h = await started(tmp_path, entry_gate=lambda _i: gate["reason"])
    await h.send(place())
    assert not h.brackets()
    assert h.events("broker_skip")[-1]["reason"] == "global HALT"
    gate["reason"] = None
    h.fake.place_outcomes.append("dropped")
    h.submit(place())
    t, actions = h.safety._queue.pop(0)
    await h.run(h.safety.dispatch(t, actions[0]))  # no answer: pending
    assert len(h.brackets()) == 1
    assert h.safety.pending
    gate["reason"] = "plugin faulted"
    await h.run(h.safety.sync())
    assert len(h.brackets()) == 1
    skip = h.events("broker_skip")[-1]
    assert skip["reason"] == "plugin faulted"
    assert skip["engine_id"] == ENTRY.client_id


# ---------------------------------------------------------------- A1 deferred exits survive


@pytest.mark.asyncio
async def test_a_deferred_exit_survives_its_step_and_is_sent_once_the_fill_shows(
    tmp_path: Path,
) -> None:
    h = await started(tmp_path)
    h.fake.show_fill_after(3)  # hidden from this sync, the exit read and the next sync
    await h.send(place())
    lookups = len([c for c in h.fake.calls if c[0] == "lookup"])
    await h.send(exit_action(entry_id=ENTRY.client_id))  # deferred; this sync shows no fill
    assert h.events("broker_exit_deferred")
    assert not h.events("resubmit_dropped")
    assert len([c for c in h.fake.calls if c[0] == "lookup"]) == lookups
    assert h.safety.pending
    await h.run(h.safety.step())  # the fill shows: sent with the full qty
    assert [r.qty for r in h.places()] == [2]
    assert len([c for c in h.fake.calls if c[0] == "lookup"]) == lookups
    assert not h.safety.pending
    assert h.fake.positions["MNQ"] == 0


@pytest.mark.asyncio
async def test_a_deferred_exit_with_no_fill_by_its_deadline_is_dropped(tmp_path: Path) -> None:
    h = await started(tmp_path)
    h.fake.show_fill_after(100)
    await h.send(place())
    await h.send(exit_action(entry_id=ENTRY.client_id))
    (p,) = h.safety.pending.values()
    assert p.defer_until is not None
    assert p.defer_until in h.safety._deadlines()
    h.clock.advance_to(p.defer_until)
    await h.run(h.safety.step())
    assert not h.places()
    (dropped,) = h.events("resubmit_dropped")
    assert dropped["engine_id"] == "p:t1:exit"
    assert not [c for c in h.fake.calls if c[0] == "lookup"]


# ---------------------------------------------------------------- A2 duplicate exits


def _stale_reads(h: H) -> None:
    """Every later ``broker_state`` returns the current snapshot (a lagging read)."""
    snapshot: dict[str, BrokerState] = {}

    async def frozen() -> dict[str, BrokerState]:
        h.fake.calls.append(("broker_state", None))
        return snapshot

    async def take() -> None:
        snapshot.update(await FakeAdapter.broker_state(h.fake))  # type: ignore[arg-type]

    h.fake.broker_state = frozen  # type: ignore[method-assign]
    h._take = take  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_two_exits_for_one_position_send_one_market_order(tmp_path: Path) -> None:
    h = harness(tmp_path)
    h.fake.positions["MNQ"] = 2
    await h.run(h.safety.start())
    _stale_reads(h)
    await h.run(h._take())  # type: ignore[attr-defined]
    await h.send(exit_action(), exit_action())

    def exits() -> list[JsonValue]:
        return [e["engine_id"] for e in h.events("order_sent") if e["engine_id"] == "p:t1:exit"]

    assert [r.qty for r in h.places()] == [2]
    assert len(exits()) == 1
    assert h.events("broker_skip")[-1]["reason"] == "exit: the broker holds nothing to close"
    # an accepted exit older than stop_confirm_s and not working no longer counts
    h.clock.advance_to(T0 + (LiveConfig().stop_confirm_s + 1) * S)
    await h.send(exit_action())
    assert len(exits()) == 2


@pytest.mark.asyncio
async def test_exits_for_two_setups_on_one_instrument_both_send(tmp_path: Path) -> None:
    h = harness(tmp_path)
    h.fake.positions["MNQ"] = 2
    await h.run(h.safety.start())
    other = ExitAction(order("p:t2:exit", "sell", "market", 1, "exit", KEY2), "sell", 1)
    await h.send(exit_action(qty=1), other)
    assert [r.qty for r in h.places()] == [1, 1]
    assert h.fake.positions["MNQ"] == 0


# ---------------------------------------------------------------- A3 no lost batch


@pytest.mark.asyncio
async def test_a_failed_dispatch_leaves_its_batch_for_take_queued(tmp_path: Path) -> None:
    h = harness(tmp_path)
    h.fake.positions["MNQ"] = 2
    await h.run(h.safety.start())
    first = exit_action(qty=1)
    second = ExitAction(order("p:t2:exit", "sell", "market", 1, "exit", KEY2), "sell", 1)
    h.fake.raise_on_place = 1
    h.safety.submit(T0, [first, second], h.safety._expect)
    with pytest.raises(OSError, match="fake adapter failure"):
        await h.run(h.safety.step())
    # the exit whose place had started is pending (looked up, not dispatched again)
    assert h.safety.take_queued() == [second]
    (p,) = h.safety.pending.values()
    assert p.engine_id == "p:t1:exit"
    assert p.defer_for is None
    await h.run(h.safety.sync())  # the lookup finds nothing: one capped resend
    assert [r.qty for r in h.places()] == [1]
    assert not h.safety.pending


# ---------------------------------------------------------------- stop: a cancelled send


@pytest.mark.asyncio
async def test_an_exit_cancelled_mid_send_is_looked_up_not_sent_twice(tmp_path: Path) -> None:
    h = harness(tmp_path)
    h.fake.positions["MNQ"] = 2
    await h.run(h.safety.start())
    _stale_reads(h)  # later reads lag: a fresh read would still show long 2
    await h.run(h._take())  # type: ignore[attr-defined]
    h.fake.cancel_after_place = 1
    h.safety.submit(T0, [exit_action()], h.safety._expect)
    with pytest.raises(asyncio.CancelledError):
        await h.run(h.safety.step())
    assert h.fake.positions["MNQ"] == 0  # the cancelled request reached the broker
    assert h.safety.take_queued() == []
    (p,) = h.safety.pending.values()
    assert p.engine_id == "p:t1:exit"
    await h.run(h.safety.drain(entries=False))
    assert len(h.places()) == 1
    lookups = [c for c in h.fake.calls if c[0] == "lookup"]
    assert len(lookups) == 1
    assert not h.safety.pending


@pytest.mark.asyncio
async def test_a_stop_drain_never_resends_a_pending_entry(tmp_path: Path) -> None:
    h = await started(tmp_path)
    h.fake.place_outcomes.append("dropped")
    h.submit(place())
    t, actions = h.safety._queue.pop(0)
    await h.run(h.safety.dispatch(t, actions[0]))  # no answer: pending
    assert h.safety.pending
    await h.run(h.safety.drain(entries=False))
    assert len(h.brackets()) == 1
    assert not h.safety.pending
    skip = h.events("broker_skip")[-1]
    assert (skip["reason"], skip["engine_id"]) == ("runner stopping", ENTRY.client_id)
