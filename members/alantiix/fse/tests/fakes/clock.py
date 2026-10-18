"""A virtual-time :class:`~fse.clock.Clock` for asyncio tests.

``FakeClock.run(main)`` runs ``main`` and every task it starts. Whenever all of
them are parked in ``sleep()``, time jumps to the earliest wake instant and
those sleepers (in the order they slept) resume. No test waits in real time,
and concurrent sleepers wake in the right order at the right instant.

``sleeps`` records every requested delay, so a test can assert the exact
waits a component asked for.
"""

from __future__ import annotations

import asyncio
import contextlib
import heapq
import itertools
from collections.abc import Coroutine
from typing import Any, Final

from fse.timekit import Instant

__all__ = ["FakeClock"]

# Loop iterations that let every runnable task reach its next `sleep()` or
# finish. Code under test awaits only in-memory futures, so far fewer suffice.
_SETTLE_ITERATIONS: Final = 200


class FakeClock:
    """Virtual time: ``now()`` moves only when every task is asleep."""

    def __init__(self, start: Instant = 0) -> None:
        self._now = start
        self._sleepers: list[tuple[Instant, int, asyncio.Future[None]]] = []
        self._seq = itertools.count()
        self.sleeps: list[int] = []

    def now(self) -> Instant:
        return self._now

    async def sleep(self, delay_ns: int) -> None:
        self.sleeps.append(delay_ns)
        if delay_ns <= 0:
            await asyncio.sleep(0)
            return
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        heapq.heappush(self._sleepers, (self._now + delay_ns, next(self._seq), future))
        await future

    def advance_to(self, t: Instant) -> None:
        """Move time forward to ``t`` without waking anyone (for synchronous tests)."""
        if t < self._now:
            raise ValueError(f"time cannot go back from {self._now} to {t}")
        self._now = t

    async def run[T](self, main: Coroutine[Any, Any, T], *, max_steps: int = 100_000) -> T:
        """Run ``main`` to completion in virtual time and return its result."""
        task = asyncio.get_running_loop().create_task(main)
        try:
            for _ in range(max_steps):
                for _ in range(_SETTLE_ITERATIONS):
                    if task.done():
                        return task.result()
                    await asyncio.sleep(0)
                if not self._wake_next():
                    raise RuntimeError("deadlock: tasks are blocked and nothing is asleep")
            raise RuntimeError(f"no result after {max_steps} time steps")
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    def _wake_next(self) -> bool:
        """Jump to the earliest live wake instant and resolve its sleepers."""
        while self._sleepers and self._sleepers[0][2].done():
            heapq.heappop(self._sleepers)  # cancelled sleeper
        if not self._sleepers:
            return False
        wake = self._sleepers[0][0]
        self._now = max(self._now, wake)
        while self._sleepers and self._sleepers[0][0] == wake:
            _, _, future = heapq.heappop(self._sleepers)
            if not future.done():
                future.set_result(None)
        return True
