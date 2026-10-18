"""An in-process Broker_Adapter stand-in for the broker-safety properties (74-78).

:class:`FakeAdapter` has the Broker_Adapter calls :class:`~fse.live.broker_safety.BrokerSafety`
makes and keeps a tiny broker book. Tests script it:

- :attr:`place_outcomes`: per send, ``accepted``; ``lost`` (the order exists,
  no answer arrives); ``dropped`` (no order, no answer); ``rejected``;
- :attr:`lookup_modes`: per client-id lookup, ``truthful``, ``fail`` or
  ``timeout`` (sleeps past any lookup timeout);
- :attr:`positions` and :attr:`stops` (working stop quantity per instrument)
  set directly; :attr:`down` makes every read fail;
- :attr:`oco_legs`: a bracketed market entry gets a stop and a limit leg on
  the closing side when it fills (Auto OCO), tagged ``{entryTag}-SL`` /
  ``{entryTag}-TP`` like ProjectX (untagged when the entry has no tag), and
  a cancel takes an order off the book;
- :meth:`show_fill_after`: the next bracketed market entry stays working
  through that many ``broker_state`` reads after it is placed, then fills
  (status and position change together, as one broker update);
- :attr:`held_contracts`: the contract a position is held on, when it is not
  the listed one (a roll day);
- :attr:`raise_on_place`: the next ``place`` calls raise ``OSError``;
- :attr:`cancel_after_place`: the next ``place`` calls reach the book, then
  raise ``CancelledError`` before the answer (a task cancelled mid-send).

Every call is recorded in :attr:`calls`; :attr:`lookups` keeps each lookup's
client id, outcome and whether the book held a live order with that tag then.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Any

from fse.engine.types import Side
from fse.projectx.broker import (
    BrokerFailure,
    BrokerHealth,
    BrokerOrder,
    BrokerState,
    EntryIntent,
    Lookup,
    LookupOutcome,
    OrderIndex,
    OrderRequest,
    PlaceResult,
    ResolveError,
    Result,
)
from fse.projectx.models import ContractRef, OrderStatus
from fse.timekit import NS_PER_SECOND, Instant
from tests.fakes.clock import FakeClock

CONTRACTS = {"MES": "CON.F.US.MES.Z26", "MNQ": "CON.F.US.MNQ.Z26"}


@dataclass
class FakeAdapter:
    clock: FakeClock
    account_id: int | None = 101
    contracts: dict[str, str] = field(default_factory=lambda: dict(CONTRACTS))
    place_outcomes: deque[str] = field(default_factory=deque)
    lookup_modes: deque[str] = field(default_factory=deque)
    positions: dict[str, int] = field(default_factory=dict)
    stops: dict[str, int] = field(default_factory=dict)
    down: bool = False
    orders: dict[int, BrokerOrder] = field(default_factory=dict)
    calls: list[tuple[str, Any]] = field(default_factory=list)
    lookups: list[tuple[str, str, bool]] = field(default_factory=list)
    next_id: int = 500
    duplicate_attempts: int = 0
    oco_legs: bool = False
    oco_tag: bool = True
    """When ``oco_legs`` is on, tag children ``{entry}-SL``/``-TP`` (ProjectX)."""
    held_contracts: dict[str, str] = field(default_factory=dict)
    raise_on_place: int = 0
    cancel_after_place: int = 0
    lagged: dict[int, int] = field(default_factory=dict)
    fill_lag: int = 0
    bracketed: set[int] = field(default_factory=set)
    health: BrokerHealth = field(init=False)

    def __post_init__(self) -> None:
        self.health = BrokerHealth(self.clock.now())

    # ------------------------------------------------------------ helpers

    def live_tags(self) -> list[str]:
        return [o.tag for o in self.orders.values() if o.tag and (o.working or o.filled)]

    def sends(self) -> list[tuple[str, Any]]:
        return [c for c in self.calls if c[0] in ("place", "place_bracketed")]

    def _ok(self) -> None:
        self.health.last_success_ns = self.clock.now()

    def show_fill_after(self, n_reads: int) -> None:
        """The next bracketed market entry fills only after ``n_reads`` broker reads.

        A sync makes one read, and an ``exit_read`` exit makes one more, so
        ``show_fill_after(2)`` hides the fill from the first post-flush sync and
        from an exit sent right after it, and shows it at the next sync.
        """
        self.fill_lag = n_reads

    def _release_lagged(self) -> None:
        for oid, left in list(self.lagged.items()):
            if left <= 0:
                del self.lagged[oid]
                self._fill_market(self.orders[oid])
            else:
                self.lagged[oid] = left - 1

    def _instrument(self, contract_id: str) -> str:
        for table in (self.held_contracts, self.contracts):
            for inst, cid in table.items():
                if cid == contract_id:
                    return inst
        raise KeyError(contract_id)

    def _fill_market(self, order: BrokerOrder) -> None:
        order = replace(order, status=OrderStatus.FILLED, filled_qty=order.qty)
        self.orders[order.order_id] = order
        inst = self._instrument(order.contract_id)
        sign = 1 if order.side == "buy" else -1
        self.positions[inst] = self.positions.get(inst, 0) + sign * order.qty
        if self.oco_legs and order.order_id in self.bracketed:
            closing: Side = "sell" if order.side == "buy" else "buy"
            for kind, suffix in (("stop", "SL"), ("limit", "TP")):
                self.next_id += 1
                tag = (
                    None
                    if order.tag is None or not self.oco_tag
                    else f"{order.tag}-{suffix}"
                )
                self.orders[self.next_id] = BrokerOrder(
                    self.next_id,
                    order.contract_id,
                    tag,
                    closing,
                    order.qty,
                    kind,
                    None,
                    OrderStatus.OPEN,
                )

    def _new(self, request: EntryIntent | OrderRequest) -> BrokerOrder:
        self.next_id += 1
        price = request.entry if isinstance(request, EntryIntent) else request.price
        order = BrokerOrder(
            self.next_id,
            request.contract_id,
            request.client_id,
            request.side,
            request.qty,
            request.kind,
            price,
            OrderStatus.OPEN,
        )
        self.orders[order.order_id] = order
        if isinstance(request, EntryIntent):
            self.bracketed.add(order.order_id)
        if request.kind == "market":
            if isinstance(request, EntryIntent) and self.fill_lag:
                self.lagged[order.order_id] = self.fill_lag
                self.fill_lag = 0
            else:
                self._fill_market(order)
                order = self.orders[order.order_id]
        return order

    # ------------------------------------------------------------ the adapter calls

    async def resolve_contract(self, instrument: str) -> ContractRef | ResolveError:
        self.calls.append(("resolve_contract", instrument))
        cid = self.contracts.get(instrument)
        if cid is None:
            return ResolveError(instrument, "not listed")
        return ContractRef(cid, instrument, 0.25, True, None)

    async def broker_state(self) -> dict[str, BrokerState] | BrokerFailure:
        self.calls.append(("broker_state", None))
        if self.down:
            return BrokerFailure("/api/Position/searchOpen", "HTTP 503")
        self._release_lagged()
        self._ok()
        out: dict[str, BrokerState] = {}
        for inst in sorted(set(self.positions) | set(self.stops) | set(self.contracts)):
            pos = self.positions.get(inst, 0)
            contract = self.held_contracts.get(inst) or self.contracts.get(
                inst, f"CON.F.US.{inst}.Z26"
            )
            orders = tuple(
                o for o in self.orders.values() if o.contract_id == contract and o.working
            )
            stop = self.stops.get(inst, 0)
            if stop:
                side: Side = "sell" if pos > 0 else "buy"
                orders = (
                    *orders,
                    BrokerOrder(1, contract, None, side, stop, "stop", 1, OrderStatus.OPEN),
                )
            if pos or orders:
                out[inst] = BrokerState(inst, contract, pos, orders)
        return out

    async def search_orders(
        self, start_ns: Instant, *, timeout_s: float | None = None
    ) -> tuple[BrokerOrder, ...] | BrokerFailure:
        self.calls.append(("search_orders", start_ns))
        if self.down:
            return BrokerFailure("/api/Order/search", "HTTP 503")
        self._ok()
        return tuple(self.orders.values())

    async def find_by_client_id(
        self, client_id: str, index: OrderIndex, *, start_ns: Instant, timeout_s: float
    ) -> Lookup:
        mode = self.lookup_modes.popleft() if self.lookup_modes else "truthful"
        held = client_id in self.live_tags()
        self.calls.append(("lookup", (client_id, mode, held)))
        if mode == "timeout":
            self.lookups.append((client_id, "timeout", held))
            await self.clock.sleep(int((timeout_s + 1) * NS_PER_SECOND))
            return Lookup("failed", detail="late")
        if mode == "fail":
            self.lookups.append((client_id, "failed", held))
            return Lookup("failed", detail="HTTP 503")
        ids = tuple(
            o.order_id
            for o in self.orders.values()
            if o.tag == client_id and (o.working or o.filled)
        )
        outcome: LookupOutcome = "found" if ids else "absent"
        self.lookups.append((client_id, outcome, held))
        return Lookup(outcome, ids, "custom_tag")

    async def _send(self, name: str, request: EntryIntent | OrderRequest) -> PlaceResult:
        self.calls.append((name, request))
        outcome = self.place_outcomes.popleft() if self.place_outcomes else "accepted"
        if outcome == "dropped":
            return PlaceResult(request.client_id, "no_response", detail="ReadTimeout")
        if outcome == "rejected":
            return PlaceResult(request.client_id, "rejected", None, 2, "errorCode 2")
        if request.client_id in self.live_tags():  # customTag must be unique
            self.duplicate_attempts += 1
            return PlaceResult(request.client_id, "rejected", None, 2, "errorCode 2")
        order = self._new(request)
        if outcome == "lost":
            return PlaceResult(request.client_id, "no_response", detail="ReadTimeout")
        return PlaceResult(request.client_id, "accepted", order.order_id, 0)

    async def place_bracketed(self, o: EntryIntent) -> PlaceResult:
        return await self._send("place_bracketed", o)

    async def place(self, o: OrderRequest) -> PlaceResult:
        if self.raise_on_place:
            self.raise_on_place -= 1
            self.calls.append(("place_raised", o))
            raise OSError("fake adapter failure")
        result = await self._send("place", o)
        if self.cancel_after_place:
            # the request reached the broker; the caller is cancelled before the answer
            self.cancel_after_place -= 1
            raise asyncio.CancelledError
        return result

    async def modify(self, order_id: int, **changes: Any) -> Result:
        self.calls.append(("modify", (order_id, changes)))
        return Result("accepted", 0)

    async def cancel(self, order_id: int) -> Result:
        self.calls.append(("cancel", order_id))
        order = self.orders.get(order_id)
        if self.oco_legs and order is not None and order.working:
            self.orders[order_id] = replace(order, status=OrderStatus.CANCELLED)
        return Result("accepted", 0)
