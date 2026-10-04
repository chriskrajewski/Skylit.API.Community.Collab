"""The Fetch_Log: one JSONL line per Skylit attempt (design §2, Req 2.8, 2.9 and 2.11).

Each line is canonical JSON written through the Log_Writer, so every
Secret_Variable value is redacted before it reaches disk. A line holds:

``sent_at`` (Instant) and ``sent_at_ny``, ``host``, ``path``, ``params``,
``attempt`` (1 to 5), ``status`` or ``error_type`` (the other is ``null``),
``error_code``, ``x_credits_remaining`` (the ``X-Credits-Remaining`` text, Req
2.11), ``duration_ms``, ``outcome`` (``ok``, ``retry``, ``failed``,
``rejected`` or ``stopped``; see :class:`~fse.skylit.retry.Action`) and
``retry_wait_ns`` (the wait before the next attempt, or ``null``).

The key cannot reach the log by construction: an entry has no header field,
so the ``Authorization`` header is never recorded, and a query parameter whose
name is a credential name (``api_key``, ``token`` and similar) is written as
``[REDACTED]``. The Log_Writer's Redactor is the second line of defence.

The file is opened on the first record and flushed after every line, so an
interrupt or crash keeps every attempt already made. Path checks (Req 1.11)
belong to the caller that chooses the run directory.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType, TracebackType
from typing import Final, Self

from fse.logio import REDACTED, LineSink, LogWriter
from fse.logio.canonical_json import JsonValue, instant_fields
from fse.skylit.retry import Action, AttemptResult, Decision, HttpResult
from fse.timekit import Instant

__all__ = ["FETCH_LOG_FILE_NAME", "SENSITIVE_PARAM_NAMES", "FetchLog", "FetchLogEntry"]

FETCH_LOG_FILE_NAME: Final = "fetch_log.jsonl"

SENSITIVE_PARAM_NAMES: Final = frozenset(
    {"access_token", "api_key", "apikey", "authorization", "key", "token", "x-api-key"}
)
"""Query parameter names (any case) whose value is never written."""

_NS_PER_US: Final = 1_000
_US_PER_MS: Final = 1_000


@dataclass(frozen=True, slots=True)
class FetchLogEntry:
    """One attempt, as recorded."""

    sent_at: Instant
    host: str
    path: str
    params: Mapping[str, str]
    attempt: int
    outcome: Action
    duration_ns: int
    status: int | None = None
    error_type: str | None = None
    error_code: str | None = None
    x_credits_remaining: str | None = None
    retry_wait_ns: int | None = None

    def __post_init__(self) -> None:
        if (self.status is None) == (self.error_type is None):
            raise ValueError("a Fetch_Log entry has exactly one of status and error_type")
        if self.attempt < 1:
            raise ValueError(f"attempt must be at least 1, got {self.attempt}")
        if self.duration_ns < 0:
            raise ValueError(f"duration_ns must be at least 0, got {self.duration_ns}")
        # Keep a read-only copy with every credential-named value already replaced.
        safe = {name: _safe_param(name, value) for name, value in self.params.items()}
        object.__setattr__(self, "params", MappingProxyType(safe))

    @classmethod
    def from_attempt(
        cls,
        *,
        sent_at: Instant,
        host: str,
        path: str,
        params: Mapping[str, str],
        attempt: int,
        result: AttemptResult,
        decision: Decision,
        duration_ns: int,
    ) -> FetchLogEntry:
        """The entry for an attempt that ended with ``result`` and led to ``decision``."""
        retry_wait = decision.wait_ns if decision.action is Action.RETRY else None
        if isinstance(result, HttpResult):
            return cls(
                sent_at=sent_at,
                host=host,
                path=path,
                params=params,
                attempt=attempt,
                outcome=decision.action,
                duration_ns=duration_ns,
                status=result.status,
                error_code=result.error_code,
                x_credits_remaining=result.headers.credits_remaining,
                retry_wait_ns=retry_wait,
            )
        return cls(
            sent_at=sent_at,
            host=host,
            path=path,
            params=params,
            attempt=attempt,
            outcome=decision.action,
            duration_ns=duration_ns,
            error_type=result.error_type,
            retry_wait_ns=retry_wait,
        )

    def to_json(self) -> dict[str, JsonValue]:
        """The Fetch_Log line as plain JSON values."""
        return {
            **instant_fields("sent_at", self.sent_at),
            "host": self.host,
            "path": self.path,
            "params": dict(self.params),
            "attempt": self.attempt,
            "status": self.status,
            "error_type": self.error_type,
            "error_code": self.error_code,
            "x_credits_remaining": self.x_credits_remaining,
            "duration_ms": _duration_ms(self.duration_ns),
            "outcome": self.outcome.value,
            "retry_wait_ns": self.retry_wait_ns,
        }


class FetchLog:
    """Appends :class:`FetchLogEntry` lines to one file through a :class:`LogWriter`."""

    __slots__ = ("_closed", "_path", "_sink", "_writer")

    def __init__(self, writer: LogWriter, path: str | Path) -> None:
        self._writer = writer
        self._path = Path(path)
        self._sink: LineSink | None = None
        self._closed = False

    @property
    def path(self) -> Path:
        return self._path

    def record(self, entry: FetchLogEntry) -> None:
        """Write one line and flush it to the OS."""
        if self._closed:
            raise ValueError(f"the Fetch_Log {self._path} is closed")
        if self._sink is None:
            self._sink = self._writer.open_lines(self._path)
        self._sink.write_json(entry.to_json())
        self._sink.flush()

    def close(self) -> None:
        """Fsync and close the file. Safe to call more than once."""
        if self._closed:
            return
        self._closed = True
        if self._sink is not None:
            try:
                self._sink.flush(fsync=True)
            finally:
                self._sink.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"FetchLog({str(self._path)!r})"


def _safe_param(name: str, value: str) -> str:
    return REDACTED if name.strip().lower() in SENSITIVE_PARAM_NAMES else value


def _duration_ms(duration_ns: int) -> float:
    """Milliseconds with microsecond resolution (truncated), for a stable encoding."""
    return (duration_ns // _NS_PER_US) / _US_PER_MS
