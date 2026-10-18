"""Request pacing for one Skylit key (design §2 ``RateLimiter``, Req 2.3 and 2.5).

Skylit counts a key's requests to every host (``api``, ``flow-api``,
``atlas-api``) against one per-minute limit, so a client holds exactly one
:class:`RateLimiter` and every attempt, retries included, passes through
:meth:`RateLimiter.acquire` immediately before it is sent.

- **Rolling window (Req 2.3):** the limiter keeps the instants of recent
  sends. A send at ``t`` is granted only when fewer than ``requests_per_minute``
  sends fall in ``(t - 60 s, t]``, so every half-open 60 s window holds at most
  ``requests_per_minute`` sends.
- **Low-water pause (Req 2.5):** :meth:`RateLimiter.observe` reads each
  response's headers. ``X-RateLimit-Remaining`` at or below the low-water mark
  (default 5) holds every send until the ``X-RateLimit-Reset`` instant, or for
  60 s from receipt when that header is absent or unreadable.
- **Client-wide holds:** :meth:`RateLimiter.hold_until` is also how a 429 wait
  (Req 2.6, ``fse.skylit.retry``) stops every request, not just the one that
  got the 429. A hold only ever extends; a later, earlier reset never cuts it
  short.

:class:`RateHeaders` parses the rate and credit headers from any
``Mapping[str, str]`` (``httpx.Headers`` or a plain dict, matched without regard
to case), so nothing here imports httpx. Unreadable header values count as
absent. Waiting goes through the injected :class:`~fse.clock.Clock`.
"""

from __future__ import annotations

import asyncio
import calendar
import re
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Final

from fse.clock import Clock
from fse.timekit import NS_PER_SECOND, Instant

__all__ = [
    "DEFAULT_LOW_WATER",
    "LOW_WATER_PAUSE_NS",
    "WINDOW_NS",
    "RateHeaders",
    "RateLimiter",
]

WINDOW_NS: Final = 60 * NS_PER_SECOND
"""Length of the rolling window that ``requests_per_minute`` applies to."""

DEFAULT_LOW_WATER: Final = 5
"""Default low-water mark for ``X-RateLimit-Remaining`` (Req 2.5)."""

LOW_WATER_PAUSE_NS: Final = 60 * NS_PER_SECOND
"""Low-water pause when the response has no readable ``X-RateLimit-Reset`` (Req 2.5)."""

_UNSIGNED_INT: Final = re.compile(r"[0-9]+", re.ASCII)
_UNSIGNED_DECIMAL: Final = re.compile(r"(?P<whole>[0-9]+)(?:\.(?P<frac>[0-9]+))?", re.ASCII)


@dataclass(frozen=True, slots=True)
class RateHeaders:
    """The pacing and credit headers of one response, parsed.

    ``reset_at`` is ``X-RateLimit-Reset`` (Unix seconds) as an Instant.
    ``retry_after_ns`` is ``Retry-After`` as a delay from receipt, from either
    delta-seconds or an HTTP-date. ``credits_remaining`` keeps the
    ``X-Credits-Remaining`` text as sent (Req 2.11).
    """

    remaining: int | None = None
    reset_at: Instant | None = None
    retry_after_ns: int | None = None
    credits_remaining: str | None = None

    @classmethod
    def parse(cls, headers: Mapping[str, str], received_at: Instant) -> RateHeaders:
        """Parse ``headers`` of a response received at ``received_at``."""
        lower = {name.lower(): value for name, value in headers.items()}
        credits = lower.get("x-credits-remaining", "").strip()
        return cls(
            remaining=_parse_unsigned_int(lower.get("x-ratelimit-remaining")),
            reset_at=_parse_unix_seconds(lower.get("x-ratelimit-reset")),
            retry_after_ns=_parse_retry_after(lower.get("retry-after"), received_at),
            credits_remaining=credits or None,
        )


class RateLimiter:
    """One rolling 60 s send limit plus client-wide holds, shared by every host."""

    __slots__ = (
        "_clock",
        "_default_pause_ns",
        "_lock",
        "_low_water",
        "_paused_until",
        "_rpm",
        "_sends",
        "_window_ns",
    )

    def __init__(
        self,
        clock: Clock,
        requests_per_minute: int,
        *,
        low_water: int = DEFAULT_LOW_WATER,
        window_ns: int = WINDOW_NS,
        default_pause_ns: int = LOW_WATER_PAUSE_NS,
    ) -> None:
        _require_int("requests_per_minute", requests_per_minute, minimum=1)
        _require_int("low_water", low_water, minimum=0)
        _require_int("window_ns", window_ns, minimum=1)
        _require_int("default_pause_ns", default_pause_ns, minimum=0)
        self._clock = clock
        self._rpm = requests_per_minute
        self._low_water = low_water
        self._window_ns = window_ns
        self._default_pause_ns = default_pause_ns
        self._sends: deque[Instant] = deque()
        self._paused_until: Instant | None = None
        self._lock = asyncio.Lock()

    @property
    def requests_per_minute(self) -> int:
        return self._rpm

    def set_requests_per_minute(self, requests_per_minute: int) -> None:
        """Apply the limit read from ``GET /v1/account`` (or its fallback, Req 2.1-2.2).

        Sends already made keep counting against the new limit.
        """
        _require_int("requests_per_minute", requests_per_minute, minimum=1)
        self._rpm = requests_per_minute

    @property
    def low_water(self) -> int:
        return self._low_water

    @property
    def paused_until(self) -> Instant | None:
        """The end of the current client-wide hold, or ``None`` if there has been none."""
        return self._paused_until

    def hold_until(self, t: Instant) -> None:
        """Send nothing before ``t``. An existing later hold is kept."""
        if self._paused_until is None or t > self._paused_until:
            self._paused_until = t

    def observe(self, headers: RateHeaders) -> Instant | None:
        """Apply the low-water rule to a response's headers (Req 2.5).

        Call once per response, whatever its status. Returns the hold end the
        response asked for, or ``None`` when ``X-RateLimit-Remaining`` is absent
        or above the low-water mark.
        """
        if headers.remaining is None or headers.remaining > self._low_water:
            return None
        if headers.reset_at is not None:
            until = headers.reset_at
        else:
            until = self._clock.now() + self._default_pause_ns
        self.hold_until(until)
        return until

    def next_send_at(self) -> Instant:
        """The earliest instant at which :meth:`acquire` could grant a send, as of now."""
        now = self._clock.now()
        self._prune(now)
        return max(now, self._earliest(now))

    async def acquire(self) -> Instant:
        """Wait until a send is allowed, record it, and return its instant.

        Callers are served first come, first served. Call immediately before
        each attempt, retries included (Req 2.3); the returned instant is the
        attempt's ``sent_at``.
        """
        async with self._lock:
            while True:
                now = self._clock.now()
                self._prune(now)
                wake = self._earliest(now)
                if wake <= now:
                    self._sends.append(now)
                    return now
                # Re-check after waking: a response may have extended the hold.
                await self._clock.sleep(wake - now)

    def _earliest(self, now: Instant) -> Instant:
        """The first instant not blocked by the hold or the window (may be in the past)."""
        earliest = now
        if self._paused_until is not None and self._paused_until > earliest:
            earliest = self._paused_until
        if len(self._sends) >= self._rpm:
            # The send `rpm` places back must have left the window.
            earliest = max(earliest, self._sends[-self._rpm] + self._window_ns)
        return earliest

    def _prune(self, now: Instant) -> None:
        while self._sends and self._sends[0] + self._window_ns <= now:
            self._sends.popleft()

    def __repr__(self) -> str:
        return (
            f"RateLimiter(requests_per_minute={self._rpm}, low_water={self._low_water}, "
            f"paused_until={self._paused_until})"
        )


# ---------------------------------------------------------------- header parsing


def _parse_unsigned_int(text: str | None) -> int | None:
    if text is None:
        return None
    value = text.strip()
    return int(value) if _UNSIGNED_INT.fullmatch(value) else None


def _parse_seconds_ns(text: str) -> int | None:
    """Non-negative decimal seconds to whole ns, truncating past 9 fractional digits."""
    m = _UNSIGNED_DECIMAL.fullmatch(text)
    if m is None:
        return None
    frac = (m["frac"] or "")[:9].ljust(9, "0")
    return int(m["whole"]) * NS_PER_SECOND + int(frac)


def _parse_unix_seconds(text: str | None) -> Instant | None:
    return None if text is None else _parse_seconds_ns(text.strip())


def _parse_retry_after(text: str | None, received_at: Instant) -> int | None:
    """``Retry-After`` as delta-seconds or an HTTP-date, as a delay of at least 0 ns."""
    if text is None:
        return None
    value = text.strip()
    delay = _parse_seconds_ns(value)
    if delay is not None:
        return delay
    try:
        when = parsedate_to_datetime(value)
    except TypeError, ValueError, IndexError:
        return None
    # An HTTP-date is GMT; a naive result (from "-0000") is UTC too.
    at = calendar.timegm(when.utctimetuple()) * NS_PER_SECOND
    return max(0, at - received_at)


def _require_int(name: str, value: object, *, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}, got {value}")
