"""The Broker_Adapter: ProjectX accounts, contracts, orders and positions (design §24, Req 24).

Sources: the ProjectX Gateway order, position, account and contract pages
listed in :mod:`fse.projectx.models` (content rephrased for compliance with
licensing restrictions).

- **Credentials and account ids come only from the EnvView (Req 24.7).** The
  session reads the two ProjectX credential variables
  (:mod:`fse.projectx.session`);
  :meth:`BrokerAdapter.for_mode` reads ``PRACTICE_ACCOUNT_ID`` or
  ``COMBINE_ACCOUNT_ID``. Nothing here takes a credential or an account id
  from the Strategy_Config or the command line.
- **One account per adapter (Req 24.4-24.5).** An adapter made by
  :meth:`BrokerAdapter.for_mode` is bound to that mode's account id and every
  order request carries it. An unbound adapter (the one that resolves the
  Order_Mode) has no account and refuses every order and account call
  (:class:`BrokerModeError`).
- **Pacing.** Every request passes one rolling 60 s
  :class:`~fse.skylit.ratelimit.RateLimiter` (default 180 sends, under the
  documented 200 per 60 s for every endpoint except ``retrieveBars``). An
  HTTP 429 holds the limiter for 60 s.
- **No automatic resend.** An order request with no answer is reported as
  ``no_response``; only the caller resends, after a client-id lookup
  (:meth:`BrokerAdapter.find_by_client_id`, Req 24.14-24.15). A 401 renews the
  session token once and resends: ProjectX refused the request, so nothing
  was placed.
- **Health (Req 24.21).** :attr:`BrokerAdapter.health` keeps the instant of
  the last HTTP 2xx answer with a JSON object body, shared by every adapter
  made from one root adapter.
- **Bracket entry (Req 24.8).** :class:`EntryIntent` builds the design §24
  payload: a limit (or stop) entry with ``stopLossBracket {ticks: |entry -
  stop|, type: 4}``, ``takeProfitBracket {ticks: |tp1 - entry|, type: 1}``,
  ``size`` = the entry quantity and ``customTag`` = the client id.
- **Client-id lookup (OQ8).** If the order search returns ``customTag``, an
  order matches by tag. If not, the local order journal's ``orderId`` for the
  client id is used. If neither confirms, the lookup reports ``failed`` and
  the caller skips the resubmission (Req 24.15).

Messages name endpoints, HTTP statuses, error codes and exception classes
only, never a credential, token or ``errorMessage`` text.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Literal, Protocol

import httpx

from fse.clock import Clock
from fse.engine.types import OrderKind, Side, Ticks
from fse.projectx.models import (
    WORKING_STATUSES,
    AccountRef,
    ApiResponse,
    ContractRef,
    MalformedResponseError,
    OrderSide,
    OrderStatus,
    OrderType,
    PlaceResponse,
    PositionType,
    WireOrder,
    format_instant,
    parse_accounts,
    parse_contracts,
    parse_orders,
    parse_positions,
    price_to_ticks,
    ticks_to_price,
)
from fse.projectx.session import ProjectXAuthError, ProjectXSession
from fse.secrets.env import EnvView
from fse.skylit.ratelimit import RateLimiter
from fse.timekit import NS_PER_SECOND, Instant

__all__ = [
    "ACCOUNT_SEARCH_PATH",
    "ACCOUNT_VARIABLES",
    "CLIENT_ID_PREFIX",
    "CONTRACT_SEARCH_PATH",
    "DEFAULT_REQUEST_TIMEOUT_S",
    "DEFAULT_SENDS_PER_WINDOW",
    "GATEWAY_RATE_LIMIT",
    "GATEWAY_RATE_WINDOW_NS",
    "ORDER_CANCEL_PATH",
    "ORDER_MODIFY_PATH",
    "ORDER_PATHS",
    "ORDER_PLACE_PATH",
    "ORDER_SEARCH_OPEN_PATH",
    "ORDER_SEARCH_PATH",
    "POSITION_SEARCH_OPEN_PATH",
    "PROJECTX_SYMBOLS",
    "BrokerAdapter",
    "BrokerFailure",
    "BrokerHealth",
    "BrokerModeError",
    "BrokerOrder",
    "BrokerState",
    "EntryIntent",
    "Lookup",
    "LookupOutcome",
    "OrderIndex",
    "OrderRequest",
    "PlaceResult",
    "ResolveError",
    "Result",
    "gateway_rate_limiter",
    "instrument_of_contract",
]

ACCOUNT_SEARCH_PATH: Final = "/api/Account/search"
CONTRACT_SEARCH_PATH: Final = "/api/Contract/search"
ORDER_PLACE_PATH: Final = "/api/Order/place"
ORDER_MODIFY_PATH: Final = "/api/Order/modify"
ORDER_CANCEL_PATH: Final = "/api/Order/cancel"
ORDER_SEARCH_PATH: Final = "/api/Order/search"
ORDER_SEARCH_OPEN_PATH: Final = "/api/Order/searchOpen"
POSITION_SEARCH_OPEN_PATH: Final = "/api/Position/searchOpen"
ORDER_PATHS: Final[frozenset[str]] = frozenset(
    {ORDER_PLACE_PATH, ORDER_MODIFY_PATH, ORDER_CANCEL_PATH}
)
"""The endpoints that submit, modify or cancel an order."""

GATEWAY_RATE_LIMIT: Final = 200
"""Documented limit for every endpoint but ``retrieveBars``: requests per 60 s."""
GATEWAY_RATE_WINDOW_NS: Final = 60 * NS_PER_SECOND
DEFAULT_SENDS_PER_WINDOW: Final = 180
DEFAULT_REQUEST_TIMEOUT_S: Final = 10.0
_HOLD_429_NS: Final = 60 * NS_PER_SECOND

ACCOUNT_VARIABLES: Final[Mapping[str, str]] = {
    "practice": "PRACTICE_ACCOUNT_ID",
    "combine": "COMBINE_ACCOUNT_ID",
}
"""The environment variable that names each broker Order_Mode's account."""

PROJECTX_SYMBOLS: Final[Mapping[str, str]] = {"MES": "MES", "MNQ": "MNQ", "ES": "EP", "NQ": "ENQ"}
"""The ProjectX symbol (``CON.F.US.<symbol>.<month>``) of each traded instrument."""
_INSTRUMENT_OF_SYMBOL: Final[Mapping[str, str]] = {v: k for k, v in PROJECTX_SYMBOLS.items()}

CLIENT_ID_PREFIX: Final = "fse-"
"""Every client id the Project sends starts with this (``fse-{run_uuid8}-{seq}``)."""


def gateway_rate_limiter(
    clock: Clock, sends_per_window: int = DEFAULT_SENDS_PER_WINDOW
) -> RateLimiter:
    """A limiter of ``sends_per_window`` (1 to 199) sends per rolling 60 s window."""
    if isinstance(sends_per_window, bool) or not 1 <= sends_per_window < GATEWAY_RATE_LIMIT:
        raise ValueError(
            f"sends_per_window must be from 1 to {GATEWAY_RATE_LIMIT - 1}, got {sends_per_window!r}"
        )
    return RateLimiter(clock, sends_per_window, low_water=0, window_ns=GATEWAY_RATE_WINDOW_NS)


def instrument_of_contract(contract_id: str) -> str | None:
    """The instrument root of a ``CON.F.US.<symbol>.<month>`` id (``EP`` is ES); else ``None``."""
    parts = contract_id.split(".")
    if len(parts) != 5 or parts[0] != "CON" or not parts[3]:
        return None
    return _INSTRUMENT_OF_SYMBOL.get(parts[3], parts[3])


class BrokerModeError(Exception):
    """An order or account call on an adapter with no account, or a bad account id variable."""


# ---------------------------------------------------------------- requests


def _side(side: Side) -> int:
    return int(OrderSide.BID if side == "buy" else OrderSide.ASK)


@dataclass(frozen=True, slots=True)
class EntryIntent:
    """One bracketed entry: the entry, its stop-loss and its first target (Req 24.8).

    Prices are in 0.25-point ticks; ``kind`` is the entry's order kind
    (``limit`` or ``stop``).
    """

    client_id: str
    contract_id: str
    side: Side
    qty: int
    kind: OrderKind
    entry: Ticks
    stop: Ticks
    target: Ticks

    def __post_init__(self) -> None:
        if not self.client_id.startswith(CLIENT_ID_PREFIX):
            raise ValueError(f"a client id starts with {CLIENT_ID_PREFIX!r}")
        if isinstance(self.qty, bool) or self.qty < 1:
            raise ValueError("an entry quantity is at least 1")
        if self.kind not in ("limit", "stop"):
            raise ValueError("a bracketed entry is a limit or stop order")
        sign = 1 if self.side == "buy" else -1
        if not sign * self.stop < sign * self.entry < sign * self.target:
            raise ValueError("the stop and the target must lie on either side of the entry")

    @property
    def stop_ticks(self) -> int:
        return abs(self.entry - self.stop)

    @property
    def target_ticks(self) -> int:
        return abs(self.target - self.entry)

    def payload(self, account_id: int) -> dict[str, object]:
        """The ``Order/place`` body (design §24)."""
        price = ticks_to_price(self.entry)
        return {
            "accountId": account_id,
            "contractId": self.contract_id,
            "type": int(OrderType.LIMIT if self.kind == "limit" else OrderType.STOP),
            "side": _side(self.side),
            "size": self.qty,
            "limitPrice": price if self.kind == "limit" else None,
            "stopPrice": price if self.kind == "stop" else None,
            "customTag": self.client_id,
            "stopLossBracket": {"ticks": self.stop_ticks, "type": int(OrderType.STOP)},
            "takeProfitBracket": {"ticks": self.target_ticks, "type": int(OrderType.LIMIT)},
        }


@dataclass(frozen=True, slots=True)
class OrderRequest:
    """One order without brackets: a market close or exit, or a limit target (TP2)."""

    client_id: str
    contract_id: str
    side: Side
    qty: int
    kind: OrderKind
    price: Ticks | None = None

    def __post_init__(self) -> None:
        if not self.client_id.startswith(CLIENT_ID_PREFIX):
            raise ValueError(f"a client id starts with {CLIENT_ID_PREFIX!r}")
        if isinstance(self.qty, bool) or self.qty < 1:
            raise ValueError("an order quantity is at least 1")
        if (self.kind == "market") != (self.price is None):
            raise ValueError("a market order has no price; a limit or stop order has one")

    def payload(self, account_id: int) -> dict[str, object]:
        types = {"market": OrderType.MARKET, "limit": OrderType.LIMIT, "stop": OrderType.STOP}
        price = None if self.price is None else ticks_to_price(self.price)
        return {
            "accountId": account_id,
            "contractId": self.contract_id,
            "type": int(types[self.kind]),
            "side": _side(self.side),
            "size": self.qty,
            "limitPrice": price if self.kind == "limit" else None,
            "stopPrice": price if self.kind == "stop" else None,
            "customTag": self.client_id,
        }


# ---------------------------------------------------------------- results


type Outcome = Literal["accepted", "rejected", "no_response"]


@dataclass(frozen=True, slots=True)
class BrokerFailure:
    """A request without a usable answer: the endpoint and the cause (no secret)."""

    endpoint: str
    detail: str

    def __str__(self) -> str:
        return f"{self.endpoint}: {self.detail}"


@dataclass(frozen=True, slots=True)
class PlaceResult:
    """The answer to an order submission.

    ``rejected`` is an answer with ``success: false`` or a non-zero
    ``errorCode`` (``order_id`` is set when ProjectX created the rejected
    order); ``no_response`` is no usable answer, so the order may or may not
    exist.
    """

    client_id: str
    outcome: Outcome
    order_id: int | None = None
    error_code: int | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Result:
    """The answer to a modify or cancel request."""

    outcome: Outcome
    error_code: int | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome == "accepted"


@dataclass(frozen=True, slots=True)
class ResolveError:
    """A contract that could not be resolved."""

    instrument: str
    reason: str


type BrokerKind = Literal["limit", "market", "stop", "other"]

_KINDS: Final[Mapping[int, BrokerKind]] = {
    OrderType.LIMIT: "limit",
    OrderType.MARKET: "market",
    OrderType.STOP: "stop",
}


@dataclass(frozen=True, slots=True)
class BrokerOrder:
    """One order as ProjectX reports it, prices in ticks."""

    order_id: int
    contract_id: str
    tag: str | None
    side: Side
    qty: int
    kind: BrokerKind
    price: Ticks | None
    status: int
    filled_qty: int = 0
    tag_field: bool = True

    @property
    def working(self) -> bool:
        return self.status in WORKING_STATUSES

    @property
    def filled(self) -> bool:
        return self.status == OrderStatus.FILLED

    @classmethod
    def from_wire(cls, w: WireOrder) -> BrokerOrder:
        kind = _KINDS.get(w.type, "other")
        raw = w.stop_price if kind == "stop" else w.limit_price
        return cls(
            order_id=w.id,
            contract_id=w.contract_id,
            tag=w.custom_tag,
            side="buy" if w.side == OrderSide.BID else "sell",
            qty=w.size,
            kind=kind,
            price=None if raw is None else price_to_ticks(raw),
            status=w.status,
            filled_qty=w.fill_volume or 0,
            tag_field=w.tag_field,
        )


@dataclass(frozen=True, slots=True)
class BrokerState:
    """One contract at the broker: the signed position (long above 0) and the working orders."""

    instrument: str
    contract_id: str
    position: int = 0
    orders: tuple[BrokerOrder, ...] = ()


type LookupOutcome = Literal["found", "absent", "failed"]


@dataclass(frozen=True, slots=True)
class Lookup:
    """A client-id lookup: ``found`` (a working or filled order), ``absent`` or ``failed``."""

    outcome: LookupOutcome
    order_ids: tuple[int, ...] = ()
    source: Literal["custom_tag", "journal", "search"] | None = None
    detail: str = ""


class OrderIndex(Protocol):
    """The local order journal as the lookup reads it (OQ8)."""

    def order_id_for(self, client_id: str) -> int | None: ...


@dataclass(slots=True)
class BrokerHealth:
    """When ProjectX last answered (Req 24.21), shared by the adapters of one root."""

    started_ns: Instant
    last_success_ns: Instant | None = None
    last_failure: BrokerFailure | None = None

    def silent_since(self) -> Instant:
        """The instant since which no successful answer has arrived."""
        return self.started_ns if self.last_success_ns is None else self.last_success_ns


# ---------------------------------------------------------------- the adapter


@dataclass(slots=True)
class _Shared:
    session: ProjectXSession
    env: EnvView
    clock: Clock
    limiter: RateLimiter
    timeout_s: float
    live: bool
    health: BrokerHealth = field(init=False)

    def __post_init__(self) -> None:
        self.health = BrokerHealth(self.clock.now())


class BrokerAdapter:
    """ProjectX order, position, account and contract calls (see the module notes)."""

    __slots__ = ("_account_id", "_s")

    def __init__(
        self,
        session: ProjectXSession,
        env: EnvView,
        clock: Clock,
        *,
        limiter: RateLimiter | None = None,
        timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S,
        live: bool = False,
    ) -> None:
        """An unbound adapter. ``live`` picks the live contract list in ``Contract/search``."""
        if timeout_s <= 0:
            raise ValueError(f"timeout_s must be positive, got {timeout_s}")
        self._s = _Shared(
            session,
            env,
            clock,
            gateway_rate_limiter(clock) if limiter is None else limiter,
            timeout_s,
            live,
        )
        self._account_id: int | None = None

    @classmethod
    def _bound(cls, shared: _Shared, account_id: int) -> BrokerAdapter:
        adapter = cls.__new__(cls)
        adapter._s = shared
        adapter._account_id = account_id
        return adapter

    def for_mode(self, mode: str) -> BrokerAdapter:
        """An adapter bound to the account named by ``mode``'s environment variable."""
        name = ACCOUNT_VARIABLES.get(mode)
        if name is None:
            raise BrokerModeError(f"Order_Mode {mode!r} sends no broker orders")
        raw = self._s.env.get(name)
        if raw is None:
            raise BrokerModeError(f"{name} is blank")
        try:
            account_id = int(raw)
        except ValueError:
            raise BrokerModeError(f"{name} is not a ProjectX account id (an integer)") from None
        if str(account_id) != raw:
            raise BrokerModeError(f"{name} is not a ProjectX account id (an integer)")
        return self._bound(self._s, account_id)

    @property
    def account_id(self) -> int | None:
        return self._account_id

    @property
    def health(self) -> BrokerHealth:
        return self._s.health

    @property
    def limiter(self) -> RateLimiter:
        return self._s.limiter

    def _require_account(self) -> int:
        if self._account_id is None:
            raise BrokerModeError("this Broker_Adapter has no account; it sends no orders")
        return self._account_id

    # ------------------------------------------------------------ reads

    async def resolve_accounts(self) -> list[AccountRef] | BrokerFailure:
        """The active accounts the credentials can see."""
        body = await self._post(ACCOUNT_SEARCH_PATH, {"onlyActiveAccounts": True})
        if isinstance(body, BrokerFailure):
            return body
        try:
            return list(parse_accounts(body))
        except MalformedResponseError as exc:
            return BrokerFailure(ACCOUNT_SEARCH_PATH, str(exc))

    async def resolve_contract(self, instrument: str) -> ContractRef | ResolveError:
        """The one active contract of ``instrument`` (Req 24.11)."""
        symbol = PROJECTX_SYMBOLS.get(instrument)
        if symbol is None:
            return ResolveError(instrument, "no ProjectX symbol is known for the instrument")
        body = await self._post(
            CONTRACT_SEARCH_PATH, {"searchText": instrument, "live": self._s.live}
        )
        if isinstance(body, BrokerFailure):
            return ResolveError(instrument, str(body))
        try:
            contracts = parse_contracts(body)
        except MalformedResponseError as exc:
            return ResolveError(instrument, str(exc))
        prefix = f"CON.F.US.{symbol}."
        found = [
            c for c in contracts if c.active and c.id.startswith(prefix) and c.id.count(".") == 4
        ]
        if len(found) != 1:
            return ResolveError(
                instrument, f"Contract/search returned {len(found)} active {symbol} contracts"
            )
        return found[0]

    async def broker_state(self) -> dict[str, BrokerState] | BrokerFailure:
        """Every contract with an open position or a working order, by instrument.

        A contract whose id is not ``CON.F.US.<symbol>.<month>`` is keyed by its id.
        """
        account = self._require_account()
        positions_body = await self._post(POSITION_SEARCH_OPEN_PATH, {"accountId": account})
        if isinstance(positions_body, BrokerFailure):
            return positions_body
        orders_body = await self._post(ORDER_SEARCH_OPEN_PATH, {"accountId": account})
        if isinstance(orders_body, BrokerFailure):
            return orders_body
        try:
            positions = parse_positions(positions_body)
            orders = parse_orders(orders_body, "Order/searchOpen")
        except MalformedResponseError as exc:
            return BrokerFailure(POSITION_SEARCH_OPEN_PATH, str(exc))
        net: dict[str, int] = {}
        for p in positions:
            sign = 1 if p.type == PositionType.LONG else -1
            net[p.contract_id] = net.get(p.contract_id, 0) + sign * p.size
        working: dict[str, list[BrokerOrder]] = {}
        for w in orders:
            order = BrokerOrder.from_wire(w)
            if order.working:
                working.setdefault(w.contract_id, []).append(order)
        out: dict[str, BrokerState] = {}
        for contract in sorted(set(net) | set(working)):
            key = instrument_of_contract(contract) or contract
            prev = out.get(key)
            orders_here = tuple(working.get(contract, ()))
            if prev is not None:  # two contracts of one instrument: keep both, net the position
                out[key] = BrokerState(
                    key,
                    prev.contract_id,
                    prev.position + net.get(contract, 0),
                    prev.orders + orders_here,
                )
            else:
                out[key] = BrokerState(key, contract, net.get(contract, 0), orders_here)
        return out

    async def search_orders(
        self, start_ns: Instant, *, timeout_s: float | None = None
    ) -> tuple[BrokerOrder, ...] | BrokerFailure:
        """Every order of the account created at or after ``start_ns``, any status."""
        account = self._require_account()
        body = await self._post(
            ORDER_SEARCH_PATH,
            {"accountId": account, "startTimestamp": format_instant(start_ns)},
            timeout_s=timeout_s,
        )
        if isinstance(body, BrokerFailure):
            return body
        try:
            return tuple(BrokerOrder.from_wire(w) for w in parse_orders(body, "Order/search"))
        except MalformedResponseError as exc:
            return BrokerFailure(ORDER_SEARCH_PATH, str(exc))

    async def find_by_client_id(
        self, client_id: str, index: OrderIndex, *, start_ns: Instant, timeout_s: float
    ) -> Lookup:
        """Working or filled orders that carry ``client_id`` (Req 24.14, OQ8)."""
        orders = await self.search_orders(start_ns, timeout_s=timeout_s)
        if isinstance(orders, BrokerFailure):
            return Lookup("failed", detail=str(orders))
        alive = [o for o in orders if o.working or o.filled]
        tagged = [o.order_id for o in alive if o.tag == client_id]
        if tagged:
            return Lookup("found", tuple(tagged), "custom_tag")
        known = index.order_id_for(client_id)
        if known is not None:
            listed = [o for o in orders if o.order_id == known]
            if any(o.working or o.filled for o in listed):
                return Lookup("found", (known,), "journal")
            if listed:
                return Lookup("absent", (), "journal", "the journaled order is not working")
        if any(o.tag_field for o in orders):
            return Lookup("absent", (), "custom_tag")
        if not orders:
            return Lookup("absent", (), "search", "the account has no order since the send")
        return Lookup("failed", detail="neither customTag nor the journal confirms the order")

    # ------------------------------------------------------------ orders

    async def place_bracketed(self, o: EntryIntent) -> PlaceResult:
        """Submit an entry with its stop-loss and target brackets (Req 24.8)."""
        return await self._place(o.client_id, o.payload(self._require_account()))

    async def place(self, o: OrderRequest) -> PlaceResult:
        """Submit one order without brackets (a market close, an exit, a TP2 limit)."""
        return await self._place(o.client_id, o.payload(self._require_account()))

    async def modify(
        self,
        order_id: int,
        *,
        size: int | None = None,
        limit_price: Ticks | None = None,
        stop_price: Ticks | None = None,
    ) -> Result:
        """Change an open order's size, limit price or stop price."""
        payload: dict[str, object] = {
            "accountId": self._require_account(),
            "orderId": order_id,
            "size": size,
            "limitPrice": None if limit_price is None else ticks_to_price(limit_price),
            "stopPrice": None if stop_price is None else ticks_to_price(stop_price),
        }
        return await self._change(ORDER_MODIFY_PATH, payload)

    async def cancel(self, order_id: int) -> Result:
        """Cancel an open order."""
        return await self._change(
            ORDER_CANCEL_PATH, {"accountId": self._require_account(), "orderId": order_id}
        )

    async def _place(self, client_id: str, payload: dict[str, object]) -> PlaceResult:
        body = await self._post(ORDER_PLACE_PATH, payload)
        if isinstance(body, BrokerFailure):
            return PlaceResult(client_id, "no_response", detail=body.detail)
        try:
            parsed = PlaceResponse.parse(body)
        except MalformedResponseError as exc:
            return PlaceResult(client_id, "no_response", detail=str(exc))
        if parsed.success and parsed.error_code == 0 and parsed.order_id is not None:
            return PlaceResult(client_id, "accepted", parsed.order_id, 0)
        return PlaceResult(
            client_id,
            "rejected",
            parsed.order_id,
            parsed.error_code,
            f"errorCode {parsed.error_code}",
        )

    async def _change(self, path: str, payload: dict[str, object]) -> Result:
        body = await self._post(path, payload)
        if isinstance(body, BrokerFailure):
            return Result("no_response", detail=body.detail)
        try:
            api = ApiResponse.parse(body, path.removeprefix("/api/"))
        except MalformedResponseError as exc:
            return Result("no_response", detail=str(exc))
        if api.ok:
            return Result("accepted", 0)
        return Result("rejected", api.error_code, f"errorCode {api.error_code}")

    # ------------------------------------------------------------ transport

    async def _post(
        self, path: str, payload: dict[str, object], *, timeout_s: float | None = None
    ) -> dict[str, object] | BrokerFailure:
        """POST ``payload``; the JSON object answered, or a failure. Never resends on its own."""
        s = self._s
        timeout = s.timeout_s if timeout_s is None else timeout_s
        renewed = False
        while True:
            try:
                token = await s.session.token()
            except ProjectXAuthError:
                return self._failed(path, "ProjectX login failed")
            await s.limiter.acquire()
            try:
                response = await s.session.http.post(
                    s.session.url(path),
                    json=payload,
                    headers=ProjectXSession.auth_headers(token),
                    timeout=timeout,
                )
            except httpx.TimeoutException as exc:
                return self._failed(
                    path, f"no response within {timeout:g} s ({type(exc).__name__})"
                )
            except (httpx.HTTPError, httpx.StreamError) as exc:
                return self._failed(path, type(exc).__name__)
            status = response.status_code
            if status == 401 and not renewed:
                renewed = True
                try:
                    await s.session.refresh(token)
                except ProjectXAuthError:
                    return self._failed(path, "ProjectX login failed")
                continue
            if status == 429:
                s.limiter.hold_until(s.clock.now() + _HOLD_429_NS)
            if not 200 <= status <= 299:
                return self._failed(path, f"HTTP {status}")
            try:
                body = json.loads(response.content)
            except ValueError:
                return self._failed(path, "the response is not JSON")
            if not isinstance(body, dict):
                return self._failed(path, "the response is not a JSON object")
            s.health.last_success_ns = s.clock.now()
            return body

    def _failed(self, path: str, detail: str) -> BrokerFailure:
        failure = BrokerFailure(path, detail)
        self._s.health.last_failure = failure
        return failure

    def __repr__(self) -> str:
        bound = "bound" if self._account_id is not None else "unbound"
        return f"BrokerAdapter({bound}, base_url={self._s.session.base_url!r})"
