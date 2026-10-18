"""The retry policy: what to do after each attempt (design §2 outcome table, Req 2.6-2.10).

:func:`decide` maps one attempt's result and its attempt number n (1 to 5)
to a :class:`Decision`. It is pure: it reads no clock and draws jitter only
from the injected ``random.Random``.

=================================================  ===========================================
Outcome                                            Decision
=================================================  ===========================================
2xx                                                ``OK``: return the response
429, n < 5                                         ``RETRY``, ``hold_all``: wait ``Retry-After``;
                                                   else until ``X-RateLimit-Reset`` + U(0, 1) s;
                                                   else 60 s + U(0, 1) s (Req 2.6)
500/502/503/504, network error, timeout, n < 5     ``RETRY``: wait U(0, min(30 s, 1 s x 2^n))
                                                   (Req 2.7)
any of the above at n = 5                          ``FAILED``: Fetch_Log ``failed`` with the last
                                                   status or error type; next request (Req 2.8)
401/402/403                                        ``STOPPED``: send nothing more; error for the
                                                   Operator (Req 2.10)
400/404/422, any other status                      ``REJECTED``: Fetch_Log status, error code,
                                                   path, params; no retry; next request (Req 2.9)
=================================================  ===========================================

``hold_all`` means the client sends no request at all during the wait
(Req 2.6: "send no request"), so the caller passes ``now + wait_ns`` to
``RateLimiter.hold_until``. The 5xx backoff delays only the request that
failed. Waits are whole nanoseconds drawn with ``rng.randint`` over a closed
range, so both ends are possible. A 429 with ``Retry-After`` draws nothing.

Statuses outside 200-599 that the table does not name (1xx, 3xx) are
``REJECTED`` too: they are not a usable payload and are not in the retry list.
503 follows Req 2.7 even when it carries ``Retry-After``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from fse.skylit.ratelimit import RateHeaders
from fse.timekit import NS_PER_SECOND, Instant

__all__ = [
    "BACKOFF_BASE_NS",
    "BACKOFF_CAP_NS",
    "JITTER_NS",
    "MAX_ATTEMPTS",
    "RATE_LIMITED_WAIT_NS",
    "RETRYABLE_STATUSES",
    "STOP_STATUSES",
    "Action",
    "AttemptResult",
    "Decision",
    "ErrorKind",
    "HttpResult",
    "TransportFailure",
    "backoff_ns",
    "decide",
    "error_code_of",
]

MAX_ATTEMPTS: Final = 5
BACKOFF_BASE_NS: Final = NS_PER_SECOND
BACKOFF_CAP_NS: Final = 30 * NS_PER_SECOND
JITTER_NS: Final = NS_PER_SECOND
RATE_LIMITED_WAIT_NS: Final = 60 * NS_PER_SECOND
"""429 wait when the response has neither ``Retry-After`` nor ``X-RateLimit-Reset``."""

RETRYABLE_STATUSES: Final = frozenset({429, 500, 502, 503, 504})
STOP_STATUSES: Final = frozenset({401, 402, 403})


class ErrorKind(StrEnum):
    """An attempt that ended without a complete response."""

    NETWORK = "network"
    TIMEOUT = "timeout"  # no complete response within the request timeout (default 60 s)


@dataclass(frozen=True, slots=True)
class HttpResult:
    """An attempt that received a complete response."""

    status: int
    headers: RateHeaders = field(default_factory=RateHeaders)
    error_code: str | None = None
    """``error.code`` from the body, if any (see :func:`error_code_of`)."""


@dataclass(frozen=True, slots=True)
class TransportFailure:
    """An attempt that failed with a network error or timed out.

    ``error_type`` names the failure for the Fetch_Log, such as the exception
    class name (``"ConnectError"``, ``"ReadTimeout"``).
    """

    kind: ErrorKind
    error_type: str


type AttemptResult = HttpResult | TransportFailure


class Action(StrEnum):
    """What the client does after an attempt; the value is the Fetch_Log ``outcome``."""

    OK = "ok"
    RETRY = "retry"
    FAILED = "failed"
    REJECTED = "rejected"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class Decision:
    action: Action
    wait_ns: int = 0
    """For ``RETRY``: the delay from the response (or failure) to the retry."""
    hold_all: bool = False
    """For ``RETRY`` after a 429: no request of any kind is sent during the wait."""


_OK: Final = Decision(Action.OK)
_FAILED: Final = Decision(Action.FAILED)
_REJECTED: Final = Decision(Action.REJECTED)
_STOPPED: Final = Decision(Action.STOPPED)


def decide(result: AttemptResult, attempt: int, *, now: Instant, rng: random.Random) -> Decision:
    """The decision after attempt number ``attempt`` (1 to 5) ended at ``now`` with ``result``."""
    if isinstance(attempt, bool) or not isinstance(attempt, int):
        raise TypeError(f"attempt must be an int, got {type(attempt).__name__}")
    if not 1 <= attempt <= MAX_ATTEMPTS:
        raise ValueError(f"attempt must be from 1 to {MAX_ATTEMPTS}, got {attempt}")
    if isinstance(result, TransportFailure):
        if attempt == MAX_ATTEMPTS:
            return _FAILED
        return Decision(Action.RETRY, wait_ns=backoff_ns(attempt, rng))
    status = result.status
    if 200 <= status <= 299:
        return _OK
    if status in STOP_STATUSES:
        return _STOPPED
    if status not in RETRYABLE_STATUSES:
        return _REJECTED
    if attempt == MAX_ATTEMPTS:
        return _FAILED
    if status == 429:
        return Decision(
            Action.RETRY, wait_ns=_rate_limited_wait_ns(result.headers, now, rng), hold_all=True
        )
    return Decision(Action.RETRY, wait_ns=backoff_ns(attempt, rng))


def backoff_ns(attempts: int, rng: random.Random) -> int:
    """Full-jitter backoff after ``attempts`` attempts: U(0, min(30 s, 1 s x 2^attempts))."""
    cap = min(BACKOFF_CAP_NS, BACKOFF_BASE_NS * 2**attempts)
    return rng.randint(0, cap)


def _rate_limited_wait_ns(headers: RateHeaders, now: Instant, rng: random.Random) -> int:
    if headers.retry_after_ns is not None:
        return headers.retry_after_ns
    jitter = rng.randint(0, JITTER_NS)
    if headers.reset_at is not None:
        return max(0, headers.reset_at + jitter - now)
    return RATE_LIMITED_WAIT_NS + jitter


def error_code_of(body: object) -> str | None:
    """``error.code`` from a decoded Skylit error body, or ``None``.

    Most errors are ``{"error": {"code": ..., "message": ...}}``; a few edge
    errors carry ``{"error": "<text>"}``, which has no code.
    """
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    return code if isinstance(code, str) and code else None
