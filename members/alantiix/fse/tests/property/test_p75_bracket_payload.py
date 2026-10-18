"""Property 75: Bracket payload.

*For any* entry intent in Practice or Combine mode, the place request carries
the entry quantity and signed ProjectX bracket ticks (stop = stop - entry,
take-profit = first target - entry), so the bracket prices implied from the
entry price equal the planned stop and first target. Shorts therefore send a
negative take-profit tick count.

Each example draws a long or short bracket (limit or stop entry, one or two
targets) as the Order_Planner would route it, sends it through
:class:`~fse.live.broker_safety.BrokerSafety` to a stand-in adapter that keeps
the :class:`~fse.projectx.broker.EntryIntent`, and checks the ``Order/place``
body that intent builds for the bound account.

**Validates: Requirements 24.8**
"""

from __future__ import annotations

import asyncio
import io
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from hypothesis import given
from hypothesis import strategies as st

from fse.config.schema.live import LiveConfig
from fse.engine.types import Order, OrderKind, SetupKey, Side
from fse.live.broker_safety import BrokerSafety, ClientIds, OrderJournal
from fse.live.order_router import MirrorBroker, PlaceAction
from fse.live.state_store import BlockStore
from fse.logio import LogWriter, Redactor
from fse.projectx.broker import BrokerAdapter, EntryIntent
from fse.projectx.models import ticks_to_price
from tests.fakes.broker import CONTRACTS, FakeAdapter
from tests.fakes.clock import FakeClock

T0 = 1_772_462_000 * 1_000_000_000


class _Mirror:
    def __init__(self, orders: list[Order]) -> None:
        self._orders = {o.client_id: o for o in orders}

    def add(self, orders: list[Order]) -> None:
        self._orders.update((o.client_id, o) for o in orders)

    def known_order(self, client_id: str) -> Order | None:
        return self._orders.get(client_id)

    def working_orders(self) -> tuple[Order, ...]:
        return tuple(self._orders.values())

    @property
    def trades(self) -> dict[object, object]:
        return {}


@st.composite
def brackets(draw: st.DrawFn) -> tuple[Order, Order, tuple[Order, ...]]:
    long = draw(st.booleans())
    sign = 1 if long else -1
    entry = draw(st.integers(4_000, 100_000))
    risk = draw(st.integers(1, 400))
    reward = draw(st.integers(1, 800))
    qty = draw(st.integers(1, 20))
    kind: OrderKind = draw(st.sampled_from(["limit", "stop"]))
    instrument = draw(st.sampled_from(sorted(CONTRACTS)))
    key = SetupKey(instrument, "fade", 6000.0, "long" if long else "short", date(2026, 3, 2), 1)
    side: Side = "buy" if long else "sell"
    close: Side = "sell" if long else "buy"
    e = Order("fse-000001-entry", key, instrument, side, kind, qty, entry, T0, "entry")
    s = Order(
        "fse-000001-stop", key, instrument, close, "stop", qty, entry - sign * risk, T0, "stop"
    )
    tp1_qty = qty if qty == 1 or not draw(st.booleans()) else draw(st.integers(1, qty - 1))
    targets = [
        Order(
            "fse-000001-tp1",
            key,
            instrument,
            close,
            "limit",
            tp1_qty,
            entry + sign * reward,
            T0,
            "tp1",
        )
    ]
    if tp1_qty < qty:
        tp2 = entry + sign * (reward + draw(st.integers(1, 400)))
        targets.append(
            Order("fse-000001-tp2", key, instrument, close, "limit", qty - tp1_qty, tp2, T0, "tp2")
        )
    return e, s, tuple(targets)


@given(legs=brackets(), account=st.integers(1, 10**9))
def test_bracket_payload(legs: tuple[Order, Order, tuple[Order, ...]], account: int) -> None:
    entry, stop, targets = legs

    async def main(state: Path) -> FakeAdapter:
        clock = FakeClock(T0)
        fake = FakeAdapter(clock, account_id=account)
        writer = LogWriter(Redactor(), stdout=io.StringIO(), stderr=io.StringIO())
        journal = OrderJournal(writer, state)
        safety = BrokerSafety(
            adapter=cast(BrokerAdapter, fake),
            live=LiveConfig(),
            instruments=sorted(CONTRACTS),
            expected_contracts=CONTRACTS,
            blocks=BlockStore(writer, state),
            journal=journal,
            ids=ClientIds(writer, state, journal, "0a1b2c3d"),
            clock=clock,
            session_start_ns=T0,
        )
        mirror = _Mirror([])
        safety.bind(cast(MirrorBroker, mirror), lambda _n: None)
        await clock.run(safety.start())  # the first comparison runs before any order
        mirror.add([entry, stop, *targets])
        await clock.run(safety.dispatch(T0, PlaceAction(entry, stop, targets)))
        return fake

    with TemporaryDirectory() as tmp:
        fake = asyncio.run(main(Path(tmp)))
    sent = [r for name, r in fake.calls if name == "place_bracketed"]
    assert len(sent) == 1
    intent = sent[0]
    assert isinstance(intent, EntryIntent)
    body = intent.payload(account)
    assert body["accountId"] == account
    assert body["contractId"] == CONTRACTS[entry.instrument]
    assert body["size"] == entry.qty
    assert body["side"] == (0 if entry.side == "buy" else 1)
    assert body["type"] == (1 if entry.kind == "limit" else 4)
    assert body["customTag"] == intent.client_id
    assert intent.client_id.startswith("fse-0a1b2c3d-")
    assert entry.price is not None
    assert stop.price is not None
    assert targets[0].price is not None
    price = body["limitPrice"] if entry.kind == "limit" else body["stopPrice"]
    assert price == ticks_to_price(entry.price)
    sl, tp = body["stopLossBracket"], body["takeProfitBracket"]
    assert isinstance(sl, dict)
    assert isinstance(tp, dict)
    assert sl == {"ticks": stop.price - entry.price, "type": 4}
    assert tp == {"ticks": targets[0].price - entry.price, "type": 1}
    assert entry.price + sl["ticks"] == stop.price  # implied stop = planned stop
    assert entry.price + tp["ticks"] == targets[0].price  # implied TP1 = planned TP1
    if entry.side == "sell":
        assert tp["ticks"] < 0
        assert sl["ticks"] > 0
    else:
        assert tp["ticks"] > 0
        assert sl["ticks"] < 0


@given(
    long=st.booleans(),
    qty=st.integers(1, 20),
    stop_ticks=st.integers(1, 4000),
    target_ticks=st.integers(1, 4000),
    account=st.integers(1, 10**9),
)
def test_market_bracket_payload(
    long: bool, qty: int, stop_ticks: int, target_ticks: int, account: int
) -> None:
    """A market entry (D5) is ``type 2`` with no prices and signed bracket ticks."""
    side: Side = "buy" if long else "sell"
    intent = EntryIntent.market(
        "fse-0a1b2c3d-000001", CONTRACTS["MNQ"], side, qty,
        stop_ticks=stop_ticks, target_ticks=target_ticks,
    )
    body = intent.payload(account)
    assert body["type"] == 2
    assert body["limitPrice"] is None
    assert body["stopPrice"] is None
    assert body["size"] == qty
    assert body["side"] == (0 if long else 1)
    wire_sl = -stop_ticks if long else stop_ticks
    wire_tp = target_ticks if long else -target_ticks
    assert body["stopLossBracket"] == {"ticks": wire_sl, "type": 4}
    assert body["takeProfitBracket"] == {"ticks": wire_tp, "type": 1}
    if not long:
        assert wire_tp < 0
