# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 v01
from __future__ import annotations

import asyncio
import json

from bigtrades.projectx.signalr import RS, HubConnection


class FakeSocket:
    def __init__(self, frames: list[str]) -> None:
        self.sent: list[str] = []
        self._frames = frames

    async def __aenter__(self) -> FakeSocket:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def send(self, text: str) -> None:
        self.sent.append(text)

    async def recv(self) -> str:
        return "{}" + RS  # handshake ok

    def __aiter__(self) -> FakeSocket:
        return self

    async def __anext__(self) -> str:
        if not self._frames:
            raise StopAsyncIteration
        return self._frames.pop(0)


def test_hub_handshake_subscribe_dispatch_and_reconnect_stop():
    frame = json.dumps({"type": 1, "target": "Hello", "arguments": ["a", {"b": 1}]}) + RS + json.dumps({"type": 6}) + RS
    socket = FakeSocket([frame])
    urls: list[str] = []
    got: list[tuple[str, list]] = []
    stop = asyncio.Event()

    def connect(url: str, **kwargs: object) -> FakeSocket:
        urls.append(url)
        return socket

    async def token() -> str:
        return "jwt"

    async def on_message(target: str, args: list) -> None:
        got.append((target, args))
        stop.set()

    async def on_connected(hub: HubConnection) -> None:
        await hub.send("Subscribe", 42)

    hub = HubConnection("https://rtc.example.test/hubs/x", token, on_message, on_connected, connect=connect)
    asyncio.run(hub.run_forever(stop))
    assert urls == ["wss://rtc.example.test/hubs/x?access_token=jwt"]
    assert socket.sent[0] == '{"protocol": "json", "version": 1}' + RS
    assert json.loads(socket.sent[1].rstrip(RS)) == {"type": 1, "target": "Subscribe", "arguments": [42]}
    assert got == [("Hello", ["a", {"b": 1}])]
