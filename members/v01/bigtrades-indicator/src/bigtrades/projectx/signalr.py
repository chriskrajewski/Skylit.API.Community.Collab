# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 v01
"""Just enough SignalR (JSON protocol over WebSocket) for the ProjectX hubs.

ProjectX hubs:
    market: https://rtc.topstepx.com/hubs/market  (GatewayQuote, GatewayTrade, GatewayDepth)
    user:   https://rtc.topstepx.com/hubs/user    (GatewayUserAccount/Order/Position/Trade)

We skip the negotiate step (the official examples use ``skipNegotiation``)
and connect straight to ``wss://.../hubs/<name>?access_token=<JWT>``.

Wire format: every message is JSON terminated by the record separator 0x1E.
    handshake  -> {"protocol":"json","version":1}
    invocation -> {"type":1,"target":"SubscribeContractTrades","arguments":["CON.F.US.EP.Z26"]}
    ping       -> {"type":6}
    close      -> {"type":7}
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

log = logging.getLogger(__name__)

RS = "\x1e"
HANDSHAKE = json.dumps({"protocol": "json", "version": 1}) + RS
TYPE_INVOCATION = 1
TYPE_PING = 6
TYPE_CLOSE = 7

MessageHandler = Callable[[str, list[Any]], Awaitable[None] | None]
ConnectedHandler = Callable[["HubConnection"], Awaitable[None]]
TokenProvider = Callable[[], Awaitable[str]]


def encode(message: dict[str, Any]) -> str:
    return json.dumps(message, separators=(",", ":")) + RS


def decode(frame: str | bytes) -> list[dict[str, Any]]:
    """Split one WebSocket frame into SignalR messages. Bad JSON is skipped."""
    text = frame.decode("utf-8") if isinstance(frame, bytes) else frame
    out: list[dict[str, Any]] = []
    for chunk in text.split(RS):
        if not chunk.strip():
            continue
        try:
            message = json.loads(chunk)
        except json.JSONDecodeError:
            log.debug("skipping non-JSON hub chunk")
            continue
        if isinstance(message, dict):
            out.append(message)
    return out


def invocation(target: str, *arguments: Any) -> dict[str, Any]:
    """A non-blocking invocation (no invocationId, so the server sends no reply)."""
    return {"type": TYPE_INVOCATION, "target": target, "arguments": list(arguments)}


def hub_ws_url(hub_url: str, token: str) -> str:
    """https://host/hubs/x -> wss://host/hubs/x?access_token=<token>."""
    parts = urlsplit(hub_url)
    scheme = {"https": "wss", "http": "ws"}.get(parts.scheme, parts.scheme)
    return urlunsplit((scheme, parts.netloc, parts.path, urlencode({"access_token": token}), ""))


class HubConnection:
    """Reconnecting SignalR client.

    ``on_connected`` runs after every (re)connect; put your Subscribe* calls there.
    ``on_message(target, arguments)`` runs for every server invocation.
    """

    def __init__(
        self,
        hub_url: str,
        token_provider: TokenProvider,
        on_message: MessageHandler,
        on_connected: ConnectedHandler,
        *,
        connect: Callable[..., Any] | None = None,
        ping_sec: float = 15.0,
        max_backoff_sec: float = 30.0,
    ) -> None:
        self.hub_url = hub_url
        self._token_provider = token_provider
        self._on_message = on_message
        self._on_connected = on_connected
        self._connect = connect
        self._ping_sec = ping_sec
        self._max_backoff = max_backoff_sec
        self._ws: Any = None

    async def send(self, target: str, *arguments: Any) -> None:
        if self._ws is None:
            raise RuntimeError("hub not connected")
        await self._ws.send(encode(invocation(target, *arguments)))

    async def run_forever(self, stop: asyncio.Event) -> None:
        backoff = 1.0
        while not stop.is_set():
            try:
                await self._run_once(stop)
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # network errors, server close, bad token
                log.warning("hub %s dropped: %s", urlsplit(self.hub_url).path, exc.__class__.__name__)
            if stop.is_set():
                break
            delay = min(self._max_backoff, backoff) * (0.8 + 0.4 * random.random())
            backoff = min(self._max_backoff, backoff * 2)
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                pass

    async def _run_once(self, stop: asyncio.Event) -> None:
        connect = self._connect
        if connect is None:
            from websockets.asyncio.client import connect as ws_connect

            connect = ws_connect
        url = hub_ws_url(self.hub_url, await self._token_provider())
        async with connect(url, ping_interval=None, max_size=None) as ws:
            await ws.send(HANDSHAKE)
            first = await asyncio.wait_for(ws.recv(), timeout=10)
            for message in decode(first):
                if message.get("error"):
                    raise ConnectionError(f"handshake refused: {message['error']}")
            self._ws = ws
            log.info("hub %s connected", urlsplit(self.hub_url).path)
            pinger = asyncio.create_task(self._ping_loop(ws))
            try:
                await self._on_connected(self)
                async for frame in ws:
                    if stop.is_set():
                        break
                    for message in decode(frame):
                        await self._dispatch(message)
            finally:
                pinger.cancel()
                self._ws = None

    async def _dispatch(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == TYPE_INVOCATION:
            result = self._on_message(str(message.get("target")), list(message.get("arguments") or []))
            if inspect.isawaitable(result):
                await result
        elif kind == TYPE_CLOSE:
            raise ConnectionError(f"server closed hub: {message.get('error') or 'no reason'}")

    async def _ping_loop(self, ws: Any) -> None:
        while True:
            await asyncio.sleep(self._ping_sec)
            await ws.send(encode({"type": TYPE_PING}))
