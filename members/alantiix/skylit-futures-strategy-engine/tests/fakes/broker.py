"""An in-process Broker_Adapter stand-in for the broker-safety properties (74-78).

:class:`FakeAdapter` has the Broker_Adapter calls :class:`~fse.live.broker_safety.BrokerSafety`
makes and keeps a tiny broker book. Tests script it:

- :attr:`place_outcomes`: per send, ``accepted``; ``lost`` (the order exists,
  no answer arrives); ``dropped`` (no order, no answer); ``rejected``;
- :attr:`lookup_modes`: per client-id lookup, ``truthful``, ``fail`` or
  ``timeout`` (sleeps past any lookup timeout);
- :attr:`positions` and :attr:`stops` (working stop quantity per instrument)
  set directly; :attr:`down` makes every read fail.

Every call is recorded in :attr:`calls`; :attr:`lookups` keeps each lookup's
client id, outcome and whether the book held a live order with that tag then.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
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
            OrderStatus.FILLED if request.kind == "market" else OrderStatus.OPEN,
        )
        self.orders[order.order_id] = order
        if request.kind == "market":
            inst = next(i for i, c in self.contracts.items() if c == request.contract_id)
            sign = 1 if request.side == "buy" else -1
            self.positions[inst] = self.positions.get(inst, 0) + sign * request.qty
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
        self._ok()
        out: dict[str, BrokerState] = {}
        for inst in sorted(set(self.positions) | set(self.stops) | set(self.contracts)):
            pos = self.positions.get(inst, 0)
            contract = self.contracts.get(inst, f"CON.F.US.{inst}.Z26")
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
        return await self._send("place", o)

    async def modify(self, order_id: int, **changes: Any) -> Result:
        self.calls.append(("modify", (order_id, changes)))
        return Result("accepted", 0)

    async def cancel(self, order_id: int) -> Result:
        self.calls.append(("cancel", order_id))
        return Result("accepted", 0)
