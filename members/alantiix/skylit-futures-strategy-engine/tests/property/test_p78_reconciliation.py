"""Property 78: Reconciliation.

*For any* expected positions and orders and any Broker_State, the
reconciliation flags a difference (and blocks entries) if and only if some
compared field differs or a working order on a configured instrument lacks a
Project client id; ignored instruments never cause a difference and never
receive a submit, modify or cancel request.

Part one draws expected and broker views over MES and MNQ (configured) and
MGC and SIL (ignored), often equal and then perturbed, and checks
:func:`~fse.live.broker_safety.reconcile` against an independent comparison.
Part two dispatches drawn broker actions for ignored instruments through
:class:`~fse.live.broker_safety.BrokerSafety` and checks that no order request
is made.

**Validates: Requirements 24.18, 24.19, 24.20**
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import replace
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from hypothesis import event, given
from hypothesis import strategies as st

from fse.config.schema.live import LiveConfig
from fse.engine.types import Order, SetupKey
from fse.live.broker_safety import (
    BrokerSafety,
    ClientIds,
    InstrumentView,
    OrderJournal,
    OrderView,
    reconcile,
)
from fse.live.order_router import (
    BrokerAction,
    CancelAction,
    ExitAction,
    MirrorBroker,
    ModifyAction,
    PlaceAction,
)
from fse.live.state_store import BlockStore
from fse.logio import LogWriter, Redactor
from fse.projectx.broker import BrokerAdapter
from fse.timekit import NS_PER_SECOND
from tests.fakes.broker import FakeAdapter
from tests.fakes.clock import FakeClock

CONFIGURED = ("MES", "MNQ")
IGNORED = ("MGC", "SIL")
T0 = 1_772_462_000 * NS_PER_SECOND


@st.composite
def views(draw: st.DrawFn) -> InstrumentView:
    keys = draw(
        st.lists(
            st.sampled_from([f"fse-{i:06d}-stop" for i in range(1, 5)]), unique=True, max_size=3
        )
    )
    orders = tuple(
        OrderView(
            k,
            draw(st.sampled_from(["buy", "sell"])),
            draw(st.integers(1, 3)),
            draw(st.none() | st.integers(23_990, 24_010)),
        )
        for k in keys
    )
    return InstrumentView(draw(st.integers(-2, 2)), orders)


@st.composite
def perturbed(draw: st.DrawFn, base: InstrumentView) -> InstrumentView:
    """``base``, or a copy with one change: position, a field, a missing, extra or foreign order."""
    change = draw(
        st.sampled_from(["none", "none", "position", "field", "drop", "extra", "foreign"])
    )
    orders = list(base.orders)
    if change == "position":
        return replace(base, position=base.position + draw(st.sampled_from([-1, 1])))
    if change == "field" and orders:
        i = draw(st.integers(0, len(orders) - 1))
        o = orders[i]
        name = draw(st.sampled_from(["side", "qty", "price"]))
        if name == "side":
            orders[i] = replace(o, side="sell" if o.side == "buy" else "buy")
        elif name == "qty":
            orders[i] = replace(o, qty=o.qty + 1)
        else:
            orders[i] = replace(o, price=(o.price or 0) + 1)
    elif change == "drop" and orders:
        orders.pop(draw(st.integers(0, len(orders) - 1)))
    elif change == "extra":
        orders.append(OrderView("fse-000009-tp1", "sell", 1, 24_020))
    elif change == "foreign":
        orders.append(OrderView("#77", "sell", 1, 23_900, foreign=True))
    return replace(base, orders=tuple(orders))


def _differs(want: InstrumentView, have: InstrumentView) -> bool:
    if want.position != have.position or any(o.foreign for o in have.orders):
        return True
    a = {o.key: (o.side, o.qty, o.price) for o in want.orders}
    b = {o.key: (o.side, o.qty, o.price) for o in have.orders}
    return a != b


@given(data=st.data())
def test_reconciliation_flags_exactly_the_differences(data: st.DataObject) -> None:
    expected: dict[str, InstrumentView] = {}
    broker: dict[str, InstrumentView] = {}
    for inst in (*CONFIGURED, *IGNORED):
        if data.draw(st.booleans(), label=f"{inst} expected"):
            expected[inst] = data.draw(views(), label=f"{inst} expected view")
        base = expected.get(inst, InstrumentView())
        if data.draw(st.booleans(), label=f"{inst} at broker"):
            broker[inst] = data.draw(perturbed(base), label=f"{inst} broker view")
        elif inst in IGNORED or data.draw(st.booleans()):
            broker[inst] = data.draw(views(), label=f"{inst} unrelated view")
    result = reconcile(expected, broker, instruments=(*CONFIGURED, *IGNORED), ignored=IGNORED)
    want = any(
        _differs(expected.get(i, InstrumentView()), broker.get(i, InstrumentView()))
        for i in CONFIGURED
    )
    event(f"difference={want}")
    assert result.clean == (not want)
    for text in result.differences:
        assert text.split(" ", 1)[0] in CONFIGURED  # ignored instruments never differ
    listed = {p.split(" ", 1)[0] for p in result.ignored_positions}
    assert listed == {i for i in IGNORED if broker.get(i, InstrumentView()).position != 0}


class _Mirror:
    def known_order(self, client_id: str) -> Order | None:
        return None

    def working_orders(self) -> tuple[Order, ...]:
        return ()

    @property
    def trades(self) -> dict[object, object]:
        return {}


@st.composite
def ignored_actions(draw: st.DrawFn) -> BrokerAction:
    inst = draw(st.sampled_from(IGNORED))
    kind = draw(st.sampled_from(["place", "modify", "cancel", "exit"]))
    key = SetupKey(inst, "fade", 2000.0, "long", date(2026, 3, 2), 1)
    e = Order("fse-000001-entry", key, inst, "buy", "limit", 1, 8_000, T0, "entry")
    if kind == "place":
        s = Order("fse-000001-stop", key, inst, "sell", "stop", 1, 7_990, T0, "stop")
        t = Order("fse-000001-tp1", key, inst, "sell", "limit", 1, 8_020, T0, "tp1")
        return PlaceAction(e, s, (t,))
    if kind == "modify":
        return ModifyAction("fse-000001-stop", inst, "stop", draw(st.integers(7_000, 9_000)))
    if kind == "cancel":
        return CancelAction("fse-000001-entry", inst, "entry", key)
    x = Order("fse-000001-exit", key, inst, "sell", "market", 1, None, T0, "exit")
    return ExitAction(x, "sell", 1)


@given(actions=st.lists(ignored_actions(), min_size=1, max_size=6))
def test_ignored_instruments_receive_no_order_request(actions: list[BrokerAction]) -> None:
    async def main(state: Path) -> FakeAdapter:
        clock = FakeClock(T0)
        contracts = {
            "MES": "CON.F.US.MES.Z26",
            "MGC": "CON.F.US.MGC.J26",
            "SIL": "CON.F.US.SIL.K26",
        }
        fake = FakeAdapter(clock, contracts=contracts)
        fake.positions.update({"MGC": 2, "SIL": -1})
        writer = LogWriter(Redactor(), stdout=io.StringIO(), stderr=io.StringIO())
        journal = OrderJournal(writer, state)
        safety = BrokerSafety(
            adapter=cast(BrokerAdapter, fake),
            live=LiveConfig(),  # ignored_instruments: MGC and SIL
            instruments=("MES", *IGNORED),
            expected_contracts=contracts,
            blocks=BlockStore(writer, state),
            journal=journal,
            ids=ClientIds(writer, state, journal),
            clock=clock,
            session_start_ns=T0,
        )
        safety.bind(cast(MirrorBroker, _Mirror()), lambda _n: None)
        await clock.run(safety.start())
        safety.submit(T0, actions, safety._expect)
        await clock.run(safety.step())
        return fake

    with TemporaryDirectory() as tmp:
        fake = asyncio.run(main(Path(tmp)))
    names = [name for name, _ in fake.calls]
    assert not {"place", "place_bracketed", "modify", "cancel"} & set(names)
    assert [i for name, i in fake.calls if name == "resolve_contract"] == ["MES"]
