"""The ProjectX market hub: quotes and trades over SignalR JSON v1 (design 4.3).

Source: ProjectX Gateway docs, "Real Time Data Overview" and "Connection URLs"
(content rephrased for compliance with licensing restrictions). The Gateway
has no bar push: bars stay on REST ``retrieveBars``; the hub's trades only
trigger the bar fetch and its quotes are recorded and used as diagnostics.
Only the market hub is connected; the user hub (orders, positions) never is.

:class:`MarketHubClient` speaks the minimal SignalR JSON protocol over one
WebSocket:

- **URL** ``wss://<rtc host>/hubs/market?access_token=<token>`` (an
  ``https://`` ``rtc_url`` is turned into ``wss://``). The token comes from
  :meth:`ProjectXSession.token`, which registers it with the Redactor. The URL
  is never logged: log lines name the hub only, and a connect failure is
  logged by exception class and HTTP status, never by its message.
- **Handshake** ``{"protocol":"json","version":1}`` + ``\\x1e``; the reply
  ``{}`` must arrive within 10 s (a reply with ``error`` is a failed connect).
- **Subscribe** ``SubscribeContractQuotes`` and ``SubscribeContractTrades``
  for each contract id (non-blocking invocations, no ``invocationId``),
  again after every reconnect.
- **Frames** are split on ``\\x1e``. Type 1 ``GatewayQuote``/``GatewayTrade``
  with ``arguments = [contractId, payload]`` (a trade payload may be a dict or
  a list of dicts); type 6 (ping) counts as traffic; type 7 (close) ends the
  connection; anything else is ignored.
- **Liveness**: a type 6 ping every 15 s; no frame for 30 s ends the
  connection. **Reconnect** backoff 1, 2, 5, 10, 30 s, then 30 s.
- **Token rotation**: every 60 s the client calls ``session.token()`` (which
  renews the token when due) and, when the value changed, opens a second
  connection with the new token, subscribes, and only then closes the old one
  (make-before-break). If that fails, the old connection stays.
- **Auth**: an HTTP 401/403 on upgrade calls ``session.refresh(stale)`` once
  and backs off; the next consecutive refusal (or a login failure) ends
  :meth:`MarketHubClient.run`: quotes are down for the day, logged at error.

**Payload validation.** ``price``/``bestBid``/``bestAsk``/``lastPrice`` must
be finite numbers on the 0.25 tick grid (within 1e-9), ``timestamp`` an ISO
8601 instant with an offset, the contract one we subscribed. Anything else is
dropped and counted (:attr:`MarketHubClient.dropped`). No payload is logged.

The WebSocket ``connect`` function is injected, so tests use an in-memory
fake and never open a socket. Production uses ``websockets`` with its logger
capped at WARNING and filtered by the Log_Writer's Redactor
(:func:`hub_logger`).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from dataclasses import dataclass
from typing import Any, Final, Protocol, cast
from urllib.parse import quote, urlsplit, urlunsplit

from fse.clock import Clock
from fse.engine.types import Ticks
from fse.live._wait import TIMED_OUT, FeedLog, no_log, within
from fse.logio import LogWriter
from fse.logio.canonical_json import JsonValue
from fse.logio.log_writer import RedactingFilter
from fse.projectx.session import ProjectXAuthError
from fse.timekit import NS_PER_SECOND, Instant, parse_rfc3339

__all__ = [
    "BACKOFF_S",
    "DEFAULT_RTC_URL",
    "HANDSHAKE_TIMEOUT_S",
    "PING_INTERVAL_S",
    "RECORD_SEPARATOR",
    "SILENCE_S",
    "TOKEN_POLL_S",
    "HubConnect",
    "HubConnection",
    "HubQuote",
    "HubTrade",
    "MarketHubClient",
    "TokenSource",
    "hub_logger",
    "hub_url",
    "websockets_connect",
]

DEFAULT_RTC_URL: Final = "https://rtc.topstepx.com/hubs/market"
RECORD_SEPARATOR: Final = "\x1e"
HANDSHAKE: Final = '{"protocol":"json","version":1}' + RECORD_SEPARATOR
PING: Final = '{"type":6}' + RECORD_SEPARATOR
HANDSHAKE_TIMEOUT_S: Final = 10
PING_INTERVAL_S: Final = 15
SILENCE_S: Final = 30
TOKEN_POLL_S: Final = 60
BACKOFF_S: Final = (1, 2, 5, 10, 30)
AUTH_REFUSALS_FOR_THE_DAY: Final = 2
TICK: Final = 0.25
HUB_NAME: Final = "market hub"
_AUTH_STATUSES: Final = frozenset({401, 403})


class TokenSource(Protocol):
    """The part of :class:`~fse.projectx.session.ProjectXSession` the hub uses."""

    async def token(self) -> str: ...

    async def refresh(self, stale: str) -> str: ...


class HubConnection(Protocol):
    """One open WebSocket (``websockets.asyncio.client.ClientConnection`` or a fake)."""

    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...


type HubConnect = Callable[[str], AbstractAsyncContextManager[HubConnection]]
"""Opens a WebSocket to the URL; entering the context performs the upgrade."""


@dataclass(frozen=True, slots=True)
class HubQuote:
    """A validated ``GatewayQuote``; a price the message did not carry is ``None``."""

    contract_id: str
    t_ns: Instant
    bid_t: Ticks | None
    ask_t: Ticks | None
    last_t: Ticks | None


@dataclass(frozen=True, slots=True)
class HubTrade:
    """One validated ``GatewayTrade`` print."""

    contract_id: str
    t_ns: Instant
    price_t: Ticks


def hub_url(rtc_url: str, token: str) -> str:
    """The WebSocket URL with the token as ``access_token``. Never log the result."""
    parts = urlsplit(rtc_url)
    if parts.scheme not in ("https", "wss") or not parts.netloc:
        raise ValueError("rtc_url must be an https:// or wss:// URL with a host")
    if parts.query or parts.fragment:
        raise ValueError("rtc_url must not hold a query or a fragment")
    return urlunsplit(
        ("wss", parts.netloc, parts.path, f"access_token={quote(token, safe='')}", "")
    )


def hub_logger(writer: LogWriter) -> logging.Logger:
    """The ``websockets`` logger, capped at WARNING and redacted by ``writer``."""
    logger = logging.getLogger("websockets")
    logger.setLevel(logging.WARNING)
    if not any(isinstance(f, RedactingFilter) for f in logger.filters):
        logger.addFilter(writer.logging_filter())
    return logger


def websockets_connect(logger: logging.Logger) -> HubConnect:
    """The production connect: ``websockets`` with the SignalR pings done by the client."""
    from websockets.asyncio.client import connect

    def open_hub(url: str) -> AbstractAsyncContextManager[HubConnection]:
        return cast(
            "AbstractAsyncContextManager[HubConnection]",
            connect(
                url,
                open_timeout=HANDSHAKE_TIMEOUT_S,
                ping_interval=None,
                logger=logger,
                max_size=2**22,
            ),
        )

    return open_hub


def _http_status(exc: BaseException) -> int | None:
    """The HTTP status of a refused upgrade (``websockets.InvalidStatus`` or a fake)."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) and not isinstance(status, bool) else None


def _ticks(value: object) -> Ticks | None:
    """Ticks of a whole-tick finite price; ``None`` when the value is not one."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    price = float(value)
    if not math.isfinite(price):
        return None
    ratio = price / TICK
    nearest = round(ratio)
    if abs(ratio - nearest) > 1e-9:
        return None
    return int(nearest)


def _instant(value: object) -> Instant | None:
    if not isinstance(value, str):
        return None
    try:
        return parse_rfc3339(value)
    except ValueError:
        return None


_MISSING: Final = object()


def parse_quote(contract_id: str, payload: object) -> HubQuote | None:
    """A validated quote, or ``None`` (drop) for a bad timestamp or price."""
    if not isinstance(payload, Mapping):
        return None
    t = _instant(payload.get("timestamp"))
    if t is None:
        return None
    prices: list[Ticks | None] = []
    for name in ("bestBid", "bestAsk", "lastPrice"):
        raw = payload.get(name, _MISSING)
        if raw is _MISSING or raw is None:
            prices.append(None)
            continue
        ticks = _ticks(raw)
        if ticks is None:
            return None
        prices.append(ticks)
    if all(p is None for p in prices):
        return None
    return HubQuote(contract_id, t, prices[0], prices[1], prices[2])


def parse_trade(contract_id: str, payload: object) -> HubTrade | None:
    """A validated trade print, or ``None`` (drop)."""
    if not isinstance(payload, Mapping):
        return None
    t = _instant(payload.get("timestamp"))
    price = _ticks(payload.get("price"))
    if t is None or price is None:
        return None
    return HubTrade(contract_id, t, price)


@dataclass(slots=True)
class _Conn:
    ws: HubConnection
    stack: AsyncExitStack
    token: str
    pending: list[str]


@dataclass(frozen=True, slots=True)
class _Failed:
    reason: str
    auth: bool


class _Closed(Exception):
    """The current connection must be replaced (close message)."""


class MarketHubClient:
    """Keeps one market hub connection subscribed (see the module notes)."""

    def __init__(
        self,
        session: TokenSource,
        clock: Clock,
        *,
        rtc_url: str,
        contract_ids: Iterable[str],
        connect: HubConnect,
        on_quote: Callable[[HubQuote, Instant], None],
        on_trade: Callable[[HubTrade, Instant], None],
        on_state: Callable[[bool, str], None],
        log: FeedLog = no_log,
    ) -> None:
        hub_url(rtc_url, "x")  # validates the URL before any connect
        self._session = session
        self._clock = clock
        self._rtc_url = rtc_url
        self._contracts: tuple[str, ...] = tuple(dict.fromkeys(contract_ids))
        if not self._contracts:
            raise ValueError("the market hub needs at least one contract id")
        self._connect = connect
        self._on_quote = on_quote
        self._on_trade = on_trade
        self._on_state = on_state
        self._log = log
        self._dropped = 0
        self._up = False
        self._current: _Conn | None = None
        self._reader: asyncio.Task[str] | None = None
        self._connects = 0

    @property
    def contract_ids(self) -> tuple[str, ...]:
        return self._contracts

    @property
    def dropped(self) -> int:
        """Messages dropped by payload validation so far (``rt_dropped``)."""
        return self._dropped

    @property
    def connected(self) -> bool:
        return self._up

    @property
    def connects(self) -> int:
        """Successful connects (handshake and subscriptions done) so far."""
        return self._connects

    # ------------------------------------------------------------ state and logs

    def _state(self, up: bool, detail: str) -> None:
        if up == self._up:
            return
        self._up = up
        self._on_state(up, detail)

    def _entry(self, event: str, level: str, **fields: JsonValue) -> None:
        entry: dict[str, JsonValue] = {"event": event, "level": level, "hub": HUB_NAME}
        entry.update(fields)
        self._log(entry)

    # ------------------------------------------------------------ the loop

    async def run(self) -> None:
        """Stay connected until cancelled; returns when quotes are down for the day (auth)."""
        failures = 0
        refusals = 0
        while True:
            try:
                token = await self._session.token()
            except ProjectXAuthError:
                self._down_for_day("ProjectX login refused")
                return
            opened = await self._open(token)
            if isinstance(opened, _Conn):
                failures = 0
                refusals = 0
                self._entry("market_hub_connected", "info")
                self._state(True, "connected")
                reason = await self._serve(opened)
                self._entry("market_hub_disconnected", "warning", reason=reason)
                self._state(False, reason)
            else:
                self._entry("market_hub_connect_failed", "warning", reason=opened.reason)
                if opened.auth:
                    refusals += 1
                    if refusals >= AUTH_REFUSALS_FOR_THE_DAY:
                        self._down_for_day(opened.reason)
                        return
                    try:
                        await self._session.refresh(token)
                    except ProjectXAuthError:
                        self._down_for_day("ProjectX login refused")
                        return
                else:
                    refusals = 0
            delay = BACKOFF_S[min(failures, len(BACKOFF_S) - 1)]
            failures += 1
            await self._clock.sleep(delay * NS_PER_SECOND)

    def _down_for_day(self, reason: str) -> None:
        """Always reported (even when already down), so the caller can stop for the day."""
        self._entry("market_hub_down_for_day", "error", reason=reason)
        self._up = False
        self._on_state(False, f"down for the day: {reason}")

    async def _open(self, token: str) -> _Conn | _Failed:
        """Connect, handshake and subscribe; never raises (but propagates cancellation)."""
        stack = AsyncExitStack()
        try:
            entered = await within(
                self._clock,
                stack.enter_async_context(self._connect(hub_url(self._rtc_url, token))),
                HANDSHAKE_TIMEOUT_S * NS_PER_SECOND,
            )
            if entered is TIMED_OUT:
                await _close(stack)
                return _Failed("connect timed out", auth=False)
            ws = cast("HubConnection", entered)
            await ws.send(HANDSHAKE)
            frame = await within(self._clock, ws.recv(), HANDSHAKE_TIMEOUT_S * NS_PER_SECOND)
            if frame is TIMED_OUT:
                await _close(stack)
                return _Failed("no handshake reply within 10 s", auth=False)
            records = _records(cast("str | bytes", frame))
            reply = _json(records[0]) if records else None
            if not isinstance(reply, dict) or "error" in reply:
                await _close(stack)
                return _Failed("handshake refused", auth=False)
            for cid in self._contracts:
                for target in ("SubscribeContractQuotes", "SubscribeContractTrades"):
                    message = json.dumps(
                        {"type": 1, "target": target, "arguments": [cid]}, separators=(",", ":")
                    )
                    await ws.send(message + RECORD_SEPARATOR)
        except asyncio.CancelledError:
            await _close(stack)
            raise
        except Exception as exc:
            await _close(stack)
            status = _http_status(exc)
            label = type(exc).__name__ if status is None else f"{type(exc).__name__} HTTP {status}"
            return _Failed(label, auth=status in _AUTH_STATUSES)
        self._connects += 1
        return _Conn(ws, stack, token, records[1:])

    async def _serve(self, conn: _Conn) -> str:
        """Read ``conn`` (and its token-rotation successors) until the current one dies."""
        self._current = conn
        self._reader = asyncio.ensure_future(self._read(conn))
        helpers = [
            asyncio.ensure_future(self._pinger()),
            asyncio.ensure_future(self._token_watch()),
        ]
        try:
            while True:
                reader = self._reader
                await asyncio.wait({reader})
                if reader is self._reader:
                    if reader.cancelled():
                        return "reader cancelled"
                    return reader.result()
        finally:
            for task in (*helpers, self._reader):
                if task is not None and not task.done():
                    task.cancel()
            for task in helpers:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if self._reader is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await self._reader
            current, self._current, self._reader = self._current, None, None
            if current is not None:
                await _close(current.stack)

    async def _read(self, conn: _Conn) -> str:
        silence = SILENCE_S * NS_PER_SECOND
        try:
            for record in conn.pending:
                self._record(record)
            conn.pending = []
            while True:
                frame = await within(self._clock, conn.ws.recv(), silence)
                if frame is TIMED_OUT:
                    return f"no frame for {SILENCE_S} s"
                for record in _records(cast("str | bytes", frame)):
                    self._record(record)
        except _Closed:
            return "close message"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return f"connection ended ({type(exc).__name__})"

    async def _pinger(self) -> None:
        while True:
            await self._clock.sleep(PING_INTERVAL_S * NS_PER_SECOND)
            current = self._current
            if current is None:
                continue
            with contextlib.suppress(Exception):
                await current.ws.send(PING)

    async def _token_watch(self) -> None:
        while True:
            await self._clock.sleep(TOKEN_POLL_S * NS_PER_SECOND)
            current = self._current
            if current is None:
                continue
            try:
                token = await self._session.token()
            except ProjectXAuthError:
                self._entry("market_hub_token_check_failed", "warning")
                continue
            if token == current.token:
                continue
            fresh = await self._open(token)
            if isinstance(fresh, _Failed):
                self._entry("market_hub_rotation_failed", "warning", reason=fresh.reason)
                continue
            # Make-before-break: the new connection is subscribed; now retire the old one.
            old_reader = self._reader
            self._current = fresh
            self._reader = asyncio.ensure_future(self._read(fresh))
            try:
                if old_reader is not None:
                    old_reader.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await old_reader
            finally:
                await _close(current.stack)
            self._entry("market_hub_token_rotated", "info")

    # ------------------------------------------------------------ messages

    def _record(self, record: str) -> None:
        received = self._clock.now()
        obj = _json(record)
        if not isinstance(obj, dict):
            self._dropped += 1
            return
        kind = obj.get("type")
        if kind == 7:
            raise _Closed
        if kind != 1:
            return
        target = obj.get("target")
        if target not in ("GatewayQuote", "GatewayTrade"):
            return
        args = obj.get("arguments")
        if not isinstance(args, list) or len(args) < 2 or args[0] not in self._contracts:
            self._dropped += 1
            return
        cid = cast("str", args[0])
        payload = args[1]
        if target == "GatewayQuote":
            quote_ = parse_quote(cid, payload)
            if quote_ is None:
                self._dropped += 1
                return
            self._on_quote(quote_, received)
            return
        prints: Sequence[object] = payload if isinstance(payload, list) else [payload]
        for item in prints:
            trade = parse_trade(cid, item)
            if trade is None:
                self._dropped += 1
                continue
            self._on_trade(trade, received)


def _records(frame: str | bytes) -> list[str]:
    text = frame.decode("utf-8", "replace") if isinstance(frame, bytes) else frame
    return [r for r in text.split(RECORD_SEPARATOR) if r.strip()]


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


async def _close(stack: AsyncExitStack) -> None:
    with contextlib.suppress(Exception):
        await stack.aclose()
