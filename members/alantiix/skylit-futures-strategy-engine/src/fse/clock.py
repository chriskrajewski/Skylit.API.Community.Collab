"""The injected clock: one ``now()`` and one ``sleep()`` (design §2, "Clock and rng are injected").

Adapters and runners that wait take a :class:`Clock` instead of calling
``time`` or ``asyncio.sleep`` directly, so tests drive time with a fake clock
and never wait in real time.

``now()`` is wall time (Unix ns, UTC) because Skylit's ``X-RateLimit-Reset``
header is a Unix timestamp. :class:`SystemClock` sleeps with ``asyncio.sleep``,
which uses the loop's monotonic timer, so a caller that must not act early
re-checks ``now()`` after waking (as ``RateLimiter.acquire`` does).
"""

from __future__ import annotations

import asyncio
import time
from typing import Protocol

from fse.timekit import NS_PER_SECOND, Instant

__all__ = ["Clock", "SystemClock"]


class Clock(Protocol):
    """Current time and asynchronous waiting, in Instants and nanoseconds."""

    def now(self) -> Instant:
        """The current Instant (ns since the Unix epoch, UTC)."""
        ...

    async def sleep(self, delay_ns: int) -> None:
        """Wait ``delay_ns`` nanoseconds; zero or less yields to the loop once."""
        ...


class SystemClock:
    """The real clock: ``time.time_ns()`` and ``asyncio.sleep``."""

    __slots__ = ()

    def now(self) -> Instant:
        return time.time_ns()

    async def sleep(self, delay_ns: int) -> None:
        await asyncio.sleep(max(0, delay_ns) / NS_PER_SECOND)

    def __repr__(self) -> str:
        return "SystemClock()"
