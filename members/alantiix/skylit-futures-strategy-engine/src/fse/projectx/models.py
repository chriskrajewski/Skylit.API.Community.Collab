"""ProjectX Gateway wire formats and bar-history results (design §4 and §24, OQ9).

Sources (official ProjectX Gateway docs; content rephrased for compliance with
licensing restrictions):

- Login with an API key:
  https://gateway.docs.projectx.com/docs/getting-started/authenticate/authenticate-api-key/
  ``POST /api/Auth/loginKey`` with ``{"userName", "apiKey"}`` returns
  ``{"token", "success", "errorCode", "errorMessage"}``. A failed login still
  answers HTTP 200 with ``success: false`` and a non-zero ``errorCode`` (3
  InvalidCredentials, 7 AgreementsNotSigned, 9 ApiSubscriptionNotFound, 10
  ApiKeyAuthenticationDisabled); only a body missing a field gets HTTP 400.
  The token is sent as a bearer token, is valid for 24 hours, and logging in
  again does not revoke tokens already issued.
- Retrieve bars:
  https://gateway.docs.projectx.com/docs/api-reference/market-data/retrieve-bars/
  ``POST /api/History/retrieveBars`` with ``contractId``, ``live`` (sim or live
  data subscription), ``startTime``, ``endTime``, ``unit`` (1 second, 2 minute,
  3 hour, 4 day, 5 week, 6 month), ``unitNumber``, ``limit`` (at most 20,000
  bars per request) and ``includePartialBar``. The answer is
  ``{"bars": [{"t", "o", "h", "l", "c", "v"}], "success", "errorCode",
  "errorMessage"}`` with ``t`` in ISO 8601 with an offset; the documented
  example lists the newest bar first.
- Rate limits: https://gateway.docs.projectx.com/docs/getting-started/rate-limits/
  ``retrieveBars`` allows 50 requests per 30 seconds, every other endpoint 200
  per 60 seconds; over the limit the API answers HTTP 429.
- Orders, positions, accounts and contracts (design §24):
  https://gateway.docs.projectx.com/docs/api-reference/order/order-place/ and
  the order modify, cancel, search and search-open pages, the open-position
  search, the account search and the contract search pages. ``Order/place``
  takes ``accountId``, ``contractId``, ``type`` (1 limit, 2 market, 4 stop),
  ``side`` (0 bid = buy, 1 ask = sell), ``size``, ``limitPrice``,
  ``stopPrice``, ``customTag`` (unique across the account) and the
  ``stopLossBracket`` / ``takeProfitBracket`` objects ``{ticks, type}``, and
  answers ``{orderId, success, errorCode, errorMessage}``. A bracket sent to
  an account in Position Brackets mode is rejected with ``errorCode 2``; the
  rejected order still gets an id. Order searches list ``{id, contractId,
  status, type, side, size, limitPrice, stopPrice, fillVolume, filledPrice}``
  and, on the order search page, ``customTag`` (OQ8: whether every search
  returns it is not confirmed). Open positions list ``{contractId, type (1
  long, 2 short), size, averagePrice}``.

Parsing is hand-written rather than pydantic: a validation error must never
echo an input value, and a login body holds the session token. Every error
message here names a field or an index, never a value.

ProjectX ``t`` is taken as the bar open time, as Atlas ``t`` is (OQ10); the
Bar_Source's cross-source check (task 5.5) tests that assumption. Second bars
(``unit=1``) for ``--bar-interval 5`` are an assumption too (OQ9): if ProjectX
does not serve them, the cadence comparison reports it (Req 18.9).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import IntEnum
from types import MappingProxyType
from typing import Final

from fse.engine.types import Bar, Ticks
from fse.timekit import NS_PER_SECOND, NS_PER_US, Instant, parse_rfc3339

__all__ = [
    "LOGIN_ERROR_NAMES",
    "MAX_BARS_PER_REQUEST",
    "TICKS_PER_POINT",
    "WORKING_STATUSES",
    "AccountRef",
    "ApiResponse",
    "BarFetchFailure",
    "BarHistory",
    "BarUnit",
    "ContractRef",
    "LoginResponse",
    "MalformedResponseError",
    "OrderSide",
    "OrderStatus",
    "OrderType",
    "PlaceResponse",
    "PositionType",
    "RetrieveBarsRequest",
    "RetrieveBarsResponse",
    "WireBar",
    "WireOrder",
    "WirePosition",
    "bar_unit_for",
    "format_instant",
    "parse_accounts",
    "parse_contracts",
    "parse_orders",
    "parse_positions",
    "price_to_ticks",
    "ticks_to_price",
    "to_bar",
]

MAX_BARS_PER_REQUEST: Final = 20_000
"""The documented maximum number of bars one ``retrieveBars`` request returns."""

TICKS_PER_POINT: Final = 4
"""ES, NQ, MES and MNQ trade in 0.25-point ticks."""

LOGIN_ERROR_NAMES: Final[Mapping[int, str]] = MappingProxyType(
    {
        3: "InvalidCredentials",
        7: "AgreementsNotSigned",
        9: "ApiSubscriptionNotFound",
        10: "ApiKeyAuthenticationDisabled",
    }
)
"""Documented ``loginKey`` error codes and their names."""

_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)


class MalformedResponseError(ValueError):
    """A ProjectX response body does not have the documented shape.

    The message names the field or bar index only, never a value.
    """


class BarUnit(IntEnum):
    """``retrieveBars`` ``unit`` values."""

    SECOND = 1
    MINUTE = 2
    HOUR = 3
    DAY = 4
    WEEK = 5
    MONTH = 6


def bar_unit_for(interval_s: int) -> tuple[BarUnit, int]:
    """``(unit, unitNumber)`` for bars of ``interval_s`` seconds.

    Whole minutes use minute bars (60 s is ``(MINUTE, 1)``); anything else
    uses second bars, so ``--bar-interval 5`` asks for ``(SECOND, 5)`` (OQ9).
    """
    if isinstance(interval_s, bool) or not isinstance(interval_s, int):
        raise TypeError(f"interval_s must be an int, got {type(interval_s).__name__}")
    if interval_s <= 0:
        raise ValueError(f"interval_s must be a positive number of seconds, got {interval_s}")
    if interval_s % 60 == 0:
        return BarUnit.MINUTE, interval_s // 60
    return BarUnit.SECOND, interval_s


def format_instant(t: Instant) -> str:
    """``t`` as ISO 8601 UTC (``2026-03-02T23:00:00Z``), microseconds only when non-zero."""
    dt = _EPOCH + timedelta(microseconds=t // NS_PER_US)
    if dt.microsecond:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def price_to_ticks(price: float) -> Ticks:
    """``round_half_up(price x 4)``: the nearest 0.25-point tick, halves rounded up.

    Multiplying by 4 is exact in binary floating point, so only the rounding
    step can move a price, and only for a price off the tick grid.
    """
    if not math.isfinite(price):
        raise ValueError("a price must be a finite number")
    return math.floor(price * TICKS_PER_POINT + 0.5)


def ticks_to_price(ticks: Ticks) -> float:
    """The price of ``ticks`` 0.25-point ticks (exact in binary floating point)."""
    return ticks / TICKS_PER_POINT


# ---------------------------------------------------------------- orders and accounts


class OrderType(IntEnum):
    """``Order/place`` ``type`` values (also the bracket ``type``)."""

    LIMIT = 1
    MARKET = 2
    STOP = 4
    TRAILING_STOP = 5
    JOIN_BID = 6
    JOIN_ASK = 7


class OrderSide(IntEnum):
    """``Order/place`` ``side`` values."""

    BID = 0  # buy
    ASK = 1  # sell


class OrderStatus(IntEnum):
    """Order ``status`` values in the order searches."""

    NONE = 0
    OPEN = 1
    FILLED = 2
    CANCELLED = 3
    EXPIRED = 4
    REJECTED = 5
    PENDING = 6


WORKING_STATUSES: Final[frozenset[int]] = frozenset({OrderStatus.OPEN, OrderStatus.PENDING})
"""Statuses of an order that can still fill."""


class PositionType(IntEnum):
    """Open-position ``type`` values."""

    LONG = 1
    SHORT = 2


@dataclass(frozen=True, slots=True)
class ApiResponse:
    """The ``success`` and ``errorCode`` of any ProjectX answer."""

    success: bool
    error_code: int

    @property
    def ok(self) -> bool:
        return self.success and self.error_code == 0

    @classmethod
    def parse(cls, body: object, where: str) -> ApiResponse:
        if not isinstance(body, dict):
            raise MalformedResponseError(f"the {where} response is not a JSON object")
        return cls(
            _bool_field(body, "success", f"the {where} response"),
            _int_field(body, "errorCode", f"the {where} response"),
        )


@dataclass(frozen=True, slots=True)
class PlaceResponse:
    """A decoded ``Order/place`` answer; ``order_id`` is ``None`` when absent."""

    order_id: int | None
    success: bool
    error_code: int

    @classmethod
    def parse(cls, body: object) -> PlaceResponse:
        api = ApiResponse.parse(body, "Order/place")
        assert isinstance(body, dict)
        raw = body.get("orderId")
        if raw is not None and (isinstance(raw, bool) or not isinstance(raw, int)):
            raise MalformedResponseError("the Order/place response field 'orderId' is not an int")
        return cls(raw, api.success, api.error_code)


@dataclass(frozen=True, slots=True)
class AccountRef:
    """One account from ``Account/search``. ``name`` is never put in a Finding_Card."""

    id: int
    name: str
    can_trade: bool


@dataclass(frozen=True, slots=True)
class ContractRef:
    """One contract from ``Contract/search``."""

    id: str
    name: str
    tick_size: float
    active: bool
    symbol_id: str | None


@dataclass(frozen=True, slots=True)
class WireOrder:
    """One order from an order search.

    ``custom_tag`` is the ``customTag`` value (``None`` when null or absent);
    ``tag_field`` says whether the answer had the field at all (OQ8).
    """

    id: int
    contract_id: str
    status: int
    type: int
    side: int
    size: int
    limit_price: float | None
    stop_price: float | None
    fill_volume: int | None
    filled_price: float | None
    custom_tag: str | None
    tag_field: bool


@dataclass(frozen=True, slots=True)
class WirePosition:
    """One open position from ``Position/searchOpen``."""

    contract_id: str
    type: int
    size: int
    average_price: float | None


def parse_accounts(body: object) -> tuple[AccountRef, ...]:
    """The accounts of an ``Account/search`` answer (``success`` checked by the caller)."""
    items = _list_field(body, "accounts", "Account/search")
    out: list[AccountRef] = []
    for i, item in enumerate(items):
        where = f"Account/search account {i}"
        if not isinstance(item, dict):
            raise MalformedResponseError(f"{where} is not a JSON object")
        name = item.get("name")
        out.append(
            AccountRef(
                _int_field(item, "id", where),
                name if isinstance(name, str) else "",
                _bool_field(item, "canTrade", where),
            )
        )
    return tuple(out)


def parse_contracts(body: object) -> tuple[ContractRef, ...]:
    """The contracts of a ``Contract/search`` answer."""
    items = _list_field(body, "contracts", "Contract/search")
    out: list[ContractRef] = []
    for i, item in enumerate(items):
        where = f"Contract/search contract {i}"
        if not isinstance(item, dict):
            raise MalformedResponseError(f"{where} is not a JSON object")
        cid, name, symbol = item.get("id"), item.get("name"), item.get("symbolId")
        if not isinstance(cid, str) or not cid.strip():
            raise MalformedResponseError(f"{where} field 'id' is not a string")
        out.append(
            ContractRef(
                cid,
                name if isinstance(name, str) else "",
                _number_field(item, "tickSize", where),
                _bool_field(item, "activeContract", where),
                symbol if isinstance(symbol, str) else None,
            )
        )
    return tuple(out)


def parse_orders(body: object, where: str) -> tuple[WireOrder, ...]:
    """The orders of an order-search answer."""
    items = _list_field(body, "orders", where)
    out: list[WireOrder] = []
    for i, item in enumerate(items):
        at = f"{where} order {i}"
        if not isinstance(item, dict):
            raise MalformedResponseError(f"{at} is not a JSON object")
        contract = item.get("contractId")
        if not isinstance(contract, str):
            raise MalformedResponseError(f"{at} field 'contractId' is not a string")
        tag = item.get("customTag")
        if tag is not None and not isinstance(tag, str):
            raise MalformedResponseError(f"{at} field 'customTag' is not a string")
        out.append(
            WireOrder(
                id=_int_field(item, "id", at),
                contract_id=contract,
                status=_int_field(item, "status", at),
                type=_int_field(item, "type", at),
                side=_int_field(item, "side", at),
                size=_int_field(item, "size", at),
                limit_price=_optional_number(item, "limitPrice", at),
                stop_price=_optional_number(item, "stopPrice", at),
                fill_volume=_optional_int(item, "fillVolume", at),
                filled_price=_optional_number(item, "filledPrice", at),
                custom_tag=tag if tag else None,
                tag_field="customTag" in item,
            )
        )
    return tuple(out)


def parse_positions(body: object) -> tuple[WirePosition, ...]:
    """The open positions of a ``Position/searchOpen`` answer."""
    items = _list_field(body, "positions", "Position/searchOpen")
    out: list[WirePosition] = []
    for i, item in enumerate(items):
        at = f"Position/searchOpen position {i}"
        if not isinstance(item, dict):
            raise MalformedResponseError(f"{at} is not a JSON object")
        contract = item.get("contractId")
        if not isinstance(contract, str):
            raise MalformedResponseError(f"{at} field 'contractId' is not a string")
        out.append(
            WirePosition(
                contract,
                _int_field(item, "type", at),
                _int_field(item, "size", at),
                _optional_number(item, "averagePrice", at),
            )
        )
    return tuple(out)


# ---------------------------------------------------------------- login


@dataclass(frozen=True, slots=True)
class LoginResponse:
    """A decoded ``loginKey`` body. ``token`` is ``None`` when blank or absent."""

    token: str | None
    success: bool
    error_code: int

    @classmethod
    def parse(cls, body: object) -> LoginResponse:
        if not isinstance(body, dict):
            raise MalformedResponseError("the login response is not a JSON object")
        token = body.get("token")
        if token is not None and not isinstance(token, str):
            raise MalformedResponseError("the login response field 'token' is not a string")
        return cls(
            token=token if token is not None and token.strip() else None,
            success=_bool_field(body, "success", "the login response"),
            error_code=_int_field(body, "errorCode", "the login response"),
        )

    def __repr__(self) -> str:
        # Never show the token: a repr can reach a log line or a traceback.
        token = "None" if self.token is None else "[REDACTED]"
        return f"LoginResponse(token={token}, success={self.success}, error_code={self.error_code})"


# ---------------------------------------------------------------- bars


@dataclass(frozen=True, slots=True)
class RetrieveBarsRequest:
    """One ``retrieveBars`` request body."""

    contract_id: str
    live: bool
    start_ns: Instant
    end_ns: Instant
    unit: BarUnit
    unit_number: int
    limit: int
    include_partial_bar: bool = False

    def to_json(self) -> dict[str, object]:
        return {
            "contractId": self.contract_id,
            "live": self.live,
            "startTime": format_instant(self.start_ns),
            "endTime": format_instant(self.end_ns),
            "unit": int(self.unit),
            "unitNumber": self.unit_number,
            "limit": self.limit,
            "includePartialBar": self.include_partial_bar,
        }


@dataclass(frozen=True, slots=True)
class WireBar:
    """One bar as ProjectX sent it, with ``t`` parsed to an Instant."""

    t_ns: Instant
    o: float
    h: float
    l: float  # noqa: E741 - wire field name
    c: float
    v: float


@dataclass(frozen=True, slots=True)
class RetrieveBarsResponse:
    """A decoded ``retrieveBars`` body; ``bars`` keeps the order received."""

    bars: tuple[WireBar, ...]
    success: bool
    error_code: int

    @classmethod
    def parse(cls, body: object) -> RetrieveBarsResponse:
        if not isinstance(body, dict):
            raise MalformedResponseError("the retrieveBars response is not a JSON object")
        success = _bool_field(body, "success", "the retrieveBars response")
        error_code = _int_field(body, "errorCode", "the retrieveBars response")
        raw = body.get("bars")
        if raw is None and not success:
            return cls((), success, error_code)
        if not isinstance(raw, list):
            raise MalformedResponseError("the retrieveBars response field 'bars' is not a list")
        return cls(tuple(_wire_bar(item, i) for i, item in enumerate(raw)), success, error_code)


def to_bar(wire: WireBar, *, instrument: str, contract: str, interval_s: int) -> Bar:
    """Normalize a ProjectX bar: open and close Instants, prices as received and as ticks."""
    return Bar(
        instrument=instrument,
        contract=contract,
        interval_s=interval_s,
        open_ns=wire.t_ns,
        close_ns=wire.t_ns + interval_s * NS_PER_SECOND,
        o=wire.o,
        h=wire.h,
        l=wire.l,
        c=wire.c,
        v=wire.v,
        o_t=price_to_ticks(wire.o),
        h_t=price_to_ticks(wire.h),
        l_t=price_to_ticks(wire.l),
        c_t=price_to_ticks(wire.c),
        source="projectx",
    )


@dataclass(frozen=True, slots=True)
class BarFetchFailure:
    """One request window whose bars could not be fetched.

    Exactly one of ``status`` (an HTTP status), ``error_code`` (a ProjectX
    ``errorCode`` in a ``success: false`` body) or ``error_type`` (a network
    error or timeout class name, or ``malformed_response``) describes it.
    """

    window_start_ns: Instant
    window_end_ns: Instant
    status: int | None = None
    error_code: int | None = None
    error_type: str | None = None

    @property
    def cause(self) -> str:
        """A short cause for the coverage report: ``HTTP 503``, ``errorCode 1``, ``ReadTimeout``."""
        if self.status is not None:
            return f"HTTP {self.status}"
        if self.error_code is not None:
            return f"errorCode {self.error_code}"
        return self.error_type or "unknown"


@dataclass(frozen=True, slots=True)
class BarHistory:
    """The bars of one contract over ``[start_ns, end_ns]``, oldest first.

    Every bar opens at or after ``start_ns`` and closes at or before
    ``end_ns``. Nothing is synthesized: a window that failed is listed in
    ``failures`` and contributes no bars (Req 4.8). ``requests`` counts every
    HTTP attempt, retries included.
    """

    instrument: str
    contract: str
    contract_id: str
    interval_s: int
    start_ns: Instant
    end_ns: Instant
    bars: tuple[Bar, ...]
    failures: tuple[BarFetchFailure, ...]
    requests: int

    @property
    def complete(self) -> bool:
        """True when every request window returned a usable answer."""
        return not self.failures

    @property
    def last_failure(self) -> BarFetchFailure | None:
        return self.failures[-1] if self.failures else None


# ---------------------------------------------------------------- field helpers


def _wire_bar(item: object, index: int) -> WireBar:
    where = f"retrieveBars bar {index}"
    if not isinstance(item, dict):
        raise MalformedResponseError(f"{where} is not a JSON object")
    t = item.get("t")
    if not isinstance(t, str):
        raise MalformedResponseError(f"{where} field 't' is not a string")
    try:
        t_ns = parse_rfc3339(t)
    except ValueError:
        raise MalformedResponseError(f"{where} field 't' is not ISO 8601 with an offset") from None
    o, h, low, c, v = (_number_field(item, name, where) for name in ("o", "h", "l", "c", "v"))
    return WireBar(t_ns=t_ns, o=o, h=h, l=low, c=c, v=v)


def _bool_field(body: dict[str, object], name: str, where: str) -> bool:
    value = body.get(name)
    if not isinstance(value, bool):
        raise MalformedResponseError(f"{where} field {name!r} is not a boolean")
    return value


def _int_field(body: dict[str, object], name: str, where: str) -> int:
    value = body.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise MalformedResponseError(f"{where} field {name!r} is not an integer")
    return value


def _list_field(body: object, name: str, where: str) -> list[object]:
    if not isinstance(body, dict):
        raise MalformedResponseError(f"the {where} response is not a JSON object")
    value = body.get(name)
    if not isinstance(value, list):
        raise MalformedResponseError(f"the {where} response field {name!r} is not a list")
    return value


def _optional_number(body: dict[str, object], name: str, where: str) -> float | None:
    return None if body.get(name) is None else _number_field(body, name, where)


def _optional_int(body: dict[str, object], name: str, where: str) -> int | None:
    return None if body.get(name) is None else _int_field(body, name, where)


def _number_field(body: dict[str, object], name: str, where: str) -> float:
    value = body.get(name)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise MalformedResponseError(f"{where} field {name!r} is not a number")
    try:
        number = float(value)
    except OverflowError:  # an int too large for a float
        number = math.inf
    if not math.isfinite(number):
        raise MalformedResponseError(f"{where} field {name!r} is not a finite number")
    return number
