# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 v01
"""Where alerts go. Add your own by implementing ``Sink``."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from typing import Protocol, TextIO

import httpx

from .models import BigTrade

log = logging.getLogger(__name__)


class Sink(Protocol):
    """Receives each published big trade and its formatted text."""

    async def publish(self, trade: BigTrade, text: str) -> None: ...


class ConsoleSink:
    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream

    async def publish(self, trade: BigTrade, text: str) -> None:
        stream = self._stream or sys.stdout
        stream.write(text + "\n")
        stream.flush()


class CallbackSink:
    """Wrap any ``async def fn(trade, text)``. Handy for tests and embedding."""

    def __init__(self, fn: Callable[[BigTrade, str], Awaitable[None]]) -> None:
        self._fn = fn

    async def publish(self, trade: BigTrade, text: str) -> None:
        await self._fn(trade, text)


class DiscordWebhookSink:
    """Posts ``content`` only.

    It never sets ``username`` or ``avatar_url``: the webhook keeps the name and
    picture its owner configured in Discord. ``allowed_mentions`` is empty so an
    alert can never ping anyone.
    """

    def __init__(self, url: str, http: httpx.AsyncClient, *, max_retries: int = 2) -> None:
        if not url.startswith("https://"):
            raise ValueError("Discord webhook URL must be https")
        self._url = url
        self._http = http
        self._max_retries = max_retries

    @staticmethod
    def payload(text: str) -> dict[str, object]:
        return {"content": text[:2000], "allowed_mentions": {"parse": []}}

    async def publish(self, trade: BigTrade, text: str) -> None:
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._http.post(self._url, json=self.payload(text))
            except httpx.HTTPError as exc:
                log.warning("discord post failed: %s", exc.__class__.__name__)
                return
            if response.status_code == 429 and attempt < self._max_retries:
                try:
                    wait = float(response.json().get("retry_after", 1.0))
                except (ValueError, AttributeError):
                    wait = 1.0
                await asyncio.sleep(min(wait, 10.0))
                continue
            if response.status_code >= 400:
                log.warning("discord post failed: HTTP %s", response.status_code)
            return


async def publish_all(sinks: list[Sink], trade: BigTrade, text: str) -> None:
    """Fan out. One failing sink never blocks the others."""
    results = await asyncio.gather(*(sink.publish(trade, text) for sink in sinks), return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            log.warning("sink failed: %s", result.__class__.__name__)
