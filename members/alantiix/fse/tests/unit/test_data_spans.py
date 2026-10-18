"""Unit tests for the request date-span helper (Atlas ``max_days``, dark-pool 31 days).

**Validates: Requirements 4.4, 4.12**
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from itertools import pairwise

import pytest

from fse.data.spans import (
    DARK_POOL_MAX_SPAN_DAYS,
    DateSpan,
    is_weekday,
    split_date_range,
)


def spans(*pairs: tuple[str, str]) -> tuple[DateSpan, ...]:
    return tuple(DateSpan(date.fromisoformat(a), date.fromisoformat(b)) for a, b in pairs)


def test_dark_pool_spans_are_at_most_31_calendar_days() -> None:
    got = split_date_range(date(2026, 1, 1), date(2026, 3, 15), DARK_POOL_MAX_SPAN_DAYS)
    assert got == spans(
        ("2026-01-01", "2026-01-31"),
        ("2026-02-01", "2026-03-03"),
        ("2026-03-04", "2026-03-15"),
    )


def test_short_and_exact_ranges() -> None:
    day = date(2026, 3, 5)
    assert split_date_range(day, day, 31) == (DateSpan(day, day),)
    assert split_date_range(date(2026, 1, 1), date(2026, 3, 3), 31) == spans(
        ("2026-01-01", "2026-01-31"), ("2026-02-01", "2026-03-03")
    )


def test_counted_days_keep_trailing_weekends_in_the_span() -> None:
    # Mon 2026-03-02 to Sun 2026-03-15, 5 weekdays per span.
    assert split_date_range(date(2026, 3, 2), date(2026, 3, 15), 5, counts=is_weekday) == spans(
        ("2026-03-02", "2026-03-08"), ("2026-03-09", "2026-03-15")
    )
    # A leading weekend belongs to the first span and does not count.
    assert split_date_range(date(2026, 2, 28), date(2026, 3, 6), 3, counts=is_weekday) == spans(
        ("2026-02-28", "2026-03-04"), ("2026-03-05", "2026-03-06")
    )


def test_range_without_counted_days_is_one_span() -> None:
    sat, sun = date(2026, 3, 7), date(2026, 3, 8)
    assert split_date_range(sat, sun, 1, counts=is_weekday) == (DateSpan(sat, sun),)


@pytest.mark.parametrize("counts", [None, is_weekday])
@pytest.mark.parametrize("max_days", [1, 2, 5, 31, 90])
def test_spans_partition_the_range(max_days: int, counts: Callable[[date], bool] | None) -> None:
    first = date(2025, 12, 20)
    for length in (1, 3, 7, 40, 200):
        last = first + timedelta(days=length - 1)
        got = split_date_range(first, last, max_days, counts=counts)
        assert got[0].first == first
        assert got[-1].last == last
        assert all(b.first == a.last + timedelta(days=1) for a, b in pairwise(got))
        count = counts or (lambda _: True)
        assert all(sum(map(count, s.dates())) <= max_days for s in got)


@pytest.mark.parametrize(
    ("first", "last", "max_days", "message"),
    [
        (date(2026, 3, 6), date(2026, 3, 5), 31, "is after last date"),
        (date(2026, 3, 5), date(2026, 3, 6), 0, "max_days must be a positive integer"),
        (date(2026, 3, 5), date(2026, 3, 6), True, "max_days must be a positive integer"),
        (datetime(2026, 3, 5), date(2026, 3, 6), 31, "first must be a date"),
    ],
)
def test_invalid_input_is_rejected(first: date, last: date, max_days: int, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        split_date_range(first, last, max_days)
