"""Clock-driven waits shared by the live feeds (design D9).

The feeds wait on the injected :class:`~fse.clock.Clock`, never on
``asyncio.timeout``, so a fake clock drives every timeout in tests.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final

from fse.clock import Clock
from fse.logio.canonical_json import JsonValue
from fse.timekit import Instant

__all__ = ["TIMED_OUT", "FeedLog", "TimedOut", "next_tick", "no_log", "within"]

type FeedLog = Callable[[Mapping[str, JsonValue]], None]
"""Where a feed logs failures and reconnects; the Live_Runner writes them via the Log_Writer."""


class TimedOut:
    """The marker :func:`within` returns when the wait ran out first."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "TIMED_OUT"


TIMED_OUT: Final = TimedOut()


def no_log(_entry: Mapping[str, JsonValue]) -> None:
    """The default :data:`FeedLog`: drop the entry."""


async def within[T](clock: Clock, work: Awaitable[T], timeout_ns: int) -> T | TimedOut:
    """``work``'s result, or :data:`TIMED_OUT` (``work`` cancelled) after ``timeout_ns``.

    An exception from ``work`` propagates.
    """
    task: asyncio.Future[T] = asyncio.ensure_future(work)
    timer: asyncio.Future[None] = asyncio.ensure_future(clock.sleep(timeout_ns))
    pending: set[asyncio.Future[Any]] = {task, timer}
    try:
        await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
    except BaseException:
        task.cancel()
        raise
    finally:
        timer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await timer
    if task.done():
        return task.result()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    return TIMED_OUT


def next_tick(start_ns: Instant, interval_ns: int, after: Instant, now: Instant) -> Instant:
    """The first instant ``start + k * interval`` after ``after`` and at or after ``now``."""
    if interval_ns <= 0:
        raise ValueError("interval_ns must be positive")
    k = max((after - start_ns) // interval_ns + 1, -(-(now - start_ns) // interval_ns))
    return start_ns + k * interval_ns
