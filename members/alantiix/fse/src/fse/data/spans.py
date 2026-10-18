"""Split an inclusive date range into request spans of capped length (Req 4.4, 4.12).

Two endpoints cap the dates one request may cover:

- Atlas ``GET /v1/history``: at most the tier's ``max_days`` trading days per
  request (default 90 for 1-minute bars, readable from ``GET /v1/config``).
- ``GET /v1/dark-pool/trades``: a trade-date span of at most 31 days.

:func:`split_date_range` returns spans that are contiguous (each starts the
day after the previous one ends), never overlap, together cover the range
exactly, and each hold at most ``max_days`` counted days. By default every
calendar day counts, which is the dark-pool cap. Pass ``counts`` to count only
some days, for example :func:`is_weekday` for Atlas trading days: a weekday
holiday then counts as well, so a span never holds more trading days than the
cap.

With ``counts``, each span after the first starts on a counted day and keeps
the uncounted days that follow its last counted day (a weekend after a
Friday). So no span holds only uncounted days, unless the whole range does,
in which case the range is one span.

Nothing here reads the clock or makes a request.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final

__all__ = [
    "ATLAS_DEFAULT_MAX_DAYS",
    "DARK_POOL_MAX_SPAN_DAYS",
    "DateSpan",
    "is_weekday",
    "split_date_range",
]

ATLAS_DEFAULT_MAX_DAYS: Final = 90
"""Trading days per Atlas 1-minute ``/v1/history`` request when ``/v1/config`` gives none."""

DARK_POOL_MAX_SPAN_DAYS: Final = 31
"""Calendar days per ``/v1/dark-pool/trades`` request."""

_ONE_DAY: Final = timedelta(days=1)


def _require_date(name: str, value: object) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError(f"{name} must be a date, got {value!r}")
    return value


@dataclass(frozen=True, slots=True, order=True)
class DateSpan:
    """The dates ``first`` to ``last``, both inclusive."""

    first: date
    last: date

    def __post_init__(self) -> None:
        _require_date("DateSpan.first", self.first)
        _require_date("DateSpan.last", self.last)
        if self.first > self.last:
            raise ValueError(f"DateSpan first {self.first} is after last {self.last}")

    @property
    def calendar_days(self) -> int:
        return (self.last - self.first).days + 1

    def contains(self, d: date) -> bool:
        return self.first <= d <= self.last

    def dates(self) -> Iterator[date]:
        """Every date of the span, in order."""
        for i in range(self.calendar_days):
            yield self.first + timedelta(days=i)

    def __str__(self) -> str:
        return f"{self.first} to {self.last}"


def is_weekday(d: date) -> bool:
    """Monday to Friday: a conservative count of trading days (holidays included)."""
    return d.weekday() < 5


def split_date_range(
    first: date,
    last: date,
    max_days: int,
    *,
    counts: Callable[[date], bool] | None = None,
) -> tuple[DateSpan, ...]:
    """Contiguous, non-overlapping spans covering ``[first, last]``, oldest first.

    Each span holds at most ``max_days`` days for which ``counts`` is true
    (every day when ``counts`` is ``None``). Raises ``ValueError`` when
    ``first`` is after ``last`` or ``max_days`` is not a positive integer.
    """
    _require_date("first", first)
    _require_date("last", last)
    if first > last:
        raise ValueError(f"range first date {first} is after last date {last}")
    if not _positive_int(max_days):
        raise ValueError(f"max_days must be a positive integer, got {max_days!r}")
    total = (last - first).days + 1
    if counts is None:
        return tuple(
            DateSpan(
                first + timedelta(days=i), first + timedelta(days=min(i + max_days, total) - 1)
            )
            for i in range(0, total, max_days)
        )
    spans: list[DateSpan] = []
    span_first = first
    counted = 0
    for i in range(total):
        d = first + timedelta(days=i)
        if not counts(d):
            continue
        if counted == max_days:
            spans.append(DateSpan(span_first, d - _ONE_DAY))
            span_first, counted = d, 0
        counted += 1
    spans.append(DateSpan(span_first, last))
    return tuple(spans)


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1
