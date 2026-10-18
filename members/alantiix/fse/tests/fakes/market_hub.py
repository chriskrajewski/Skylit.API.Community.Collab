"""An in-memory ProjectX market hub for :class:`~fse.projectx.realtime.MarketHubClient` tests.

:meth:`FakeMarketHub.connect` is the injected connect function: no socket is
ever opened. Each connect follows the next entry of :attr:`FakeMarketHub.plan`
(``"ok"`` by default): an int is an HTTP status refusing the upgrade (a real
``websockets.exceptions.InvalidStatus``), ``"error_handshake"`` answers the
handshake with an ``error``, ``"no_handshake"`` never answers it, and
``"hang"`` never completes the upgrade. Tests push frames into a connection
with :meth:`FakeHubConnection.push_record` and read what the client sent from
:attr:`FakeHubConnection.sent`.

:class:`FakeTokens` is a ``TokenSource`` whose token the test can rotate.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Final
from urllib.parse import parse_qs, urlsplit

from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response

from fse.clock import Clock
from fse.projectx.realtime import HANDSHAKE, PING, RECORD_SEPARATOR
from fse.timekit import Instant

__all__ = ["FakeHubConnection", "FakeMarketHub", "FakeTokens", "quote_payload", "trade_payload"]

TOKEN: Final = "fake-hub-token-0000"


def quote_payload(
    ts: str, *, bid: float | None = 6000.0, ask: float | None = 6000.25, last: float = 6000.0
) -> dict[str, Any]:
    return {
        "symbol": "F.US.MNQ",
        "bestBid": bid,
        "bestAsk": ask,
        "lastPrice": last,
        "timestamp": ts,
    }


def trade_payload(ts: str, price: float = 6000.0) -> dict[str, Any]:
    return {"symbolId": "F.US.MNQ", "price": price, "timestamp": ts, "type": 0, "volume": 1}


class FakeHubConnection:
    """One fake WebSocket."""

    def __init__(self, hub: FakeMarketHub, url: str, behavior: str) -> None:
        self.hub = hub
        self.url = url
        self.behavior = behavior
        self.sent: list[str] = []
        self.closed = False
        self.silent = False
        """``True``: no keep-alive answer to the client's pings (a dead server)."""
        self._inbox: asyncio.Queue[str | BaseException] = asyncio.Queue()

    @property
    def token(self) -> str:
        return parse_qs(urlsplit(self.url).query)["access_token"][0]

    @property
    def subscriptions(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for message in self.sent:
            for record in message.split(RECORD_SEPARATOR):
                if not record:
                    continue
                obj = json.loads(record)
                if obj.get("type") == 1:
                    out.append((obj["target"], obj["arguments"][0]))
        return out

    @property
    def pings(self) -> int:
        return sum(1 for m in self.sent if m == PING)

    async def send(self, message: str) -> None:
        if self.closed:
            raise ConnectionError("closed")
        self.sent.append(message)
        if message == PING and not self.silent:
            self.push(PING)  # the server's keep-alive traffic
        if message == HANDSHAKE:
            if self.behavior == "ok":
                self.push("{}" + RECORD_SEPARATOR)
            elif self.behavior == "error_handshake":
                self.push('{"error":"not allowed"}' + RECORD_SEPARATOR)

    async def recv(self) -> str:
        item = await self._inbox.get()
        if isinstance(item, BaseException):
            raise item
        return item

    def push(self, text: str) -> None:
        self._inbox.put_nowait(text)

    def push_record(self, *objs: dict[str, Any]) -> None:
        self.push("".join(json.dumps(o) + RECORD_SEPARATOR for o in objs))

    def push_quote(self, contract_id: str, payload: dict[str, Any]) -> None:
        self.push_record({"type": 1, "target": "GatewayQuote", "arguments": [contract_id, payload]})

    def push_trade(self, contract_id: str, payload: object) -> None:
        self.push_record({"type": 1, "target": "GatewayTrade", "arguments": [contract_id, payload]})

    def drop(self) -> None:
        self.push(ConnectionError("dropped"))

    def close(self) -> None:
        self.closed = True
        self.push(ConnectionError("closed"))


@dataclass
class FakeMarketHub:
    """The injected connect function and every connection it made."""

    plan: list[int | str] = field(default_factory=list)
    connections: list[FakeHubConnection] = field(default_factory=list)
    attempts: int = 0
    clock: Clock | None = None
    """When set, :attr:`attempt_times` records each connect attempt's instant."""
    attempt_times: list[Instant] = field(default_factory=list)

    def connect(self, url: str) -> AbstractAsyncContextManager[FakeHubConnection]:
        return self._open(url)

    @asynccontextmanager
    async def _open(self, url: str) -> AsyncIterator[FakeHubConnection]:
        self.attempts += 1
        if self.clock is not None:
            self.attempt_times.append(self.clock.now())
        behavior = self.plan.pop(0) if self.plan else "ok"
        if isinstance(behavior, int):
            raise InvalidStatus(Response(behavior, "Refused", Headers()))
        if behavior == "hang":
            await asyncio.get_running_loop().create_future()
        conn = FakeHubConnection(self, url, behavior)
        self.connections.append(conn)
        try:
            yield conn
        finally:
            conn.closed = True

    @property
    def last(self) -> FakeHubConnection:
        return self.connections[-1]

    def live(self) -> list[FakeHubConnection]:
        return [c for c in self.connections if not c.closed]


@dataclass
class FakeTokens:
    """A ``TokenSource``: ``token()`` returns :attr:`value`; ``refresh`` counts calls."""

    value: str = TOKEN
    refreshes: list[str] = field(default_factory=list)
    calls: int = 0
    refuse_login: bool = False

    async def token(self) -> str:
        self.calls += 1
        if self.refuse_login:
            from fse.projectx.session import ProjectXLoginError

            raise ProjectXLoginError("login refused")
        return self.value

    async def refresh(self, stale: str) -> str:
        self.refreshes.append(stale)
        return self.value
