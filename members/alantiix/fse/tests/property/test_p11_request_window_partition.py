"""Property 11: Request window partition.

*For any* inclusive date range and maximum span (Atlas ``max_days`` trading
days or the 31-day dark-pool cap), the generated windows are contiguous,
non-overlapping, each within the maximum, and their union equals the
requested range.

:func:`_assert_partition` checks the first three parts against dates it lists
itself (not :meth:`DateSpan.dates`):

- contiguous: the first span starts on the range's first date, the last span
  ends on its last date, and each span starts the day after the previous one
  ends;
- non-overlapping: no date lies in two spans;
- exact cover: the dates of all spans are exactly the dates of the range.

The cap is checked per test:

1. Dark pool (``counts=None``): every span holds at most ``max_days``
   calendar days. ``max_days`` is the 31-day cap, 1, or any value up to 120.
2. Atlas (``counts`` given): every span holds at most ``max_days`` counted
   trading days. ``counts`` is :func:`is_weekday` or a calendar-aware
   :meth:`SessionCalendar.is_session`, which also excludes exchange holidays
   (scattered, in one run, or every weekday of the range). The calendar covers
   exactly the requested range, so a probe outside it fails the test.
   ``max_days`` is the Atlas default of 90, 1, or any value up to 120.

Range ends cluster where a splitter goes wrong: on or next to a multiple of
``max_days`` calendar days (dark pool) or counted days (Atlas), and a few days
past one (trailing uncounted days).

The property does not ask for the fewest spans, so span count is not checked.

**Validates: Requirements 4.4, 4.12**
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import pairwise
from typing import Literal

from hypothesis import example, given
from hypothesis import strategies as st

from fse.data.spans import (
    ATLAS_DEFAULT_MAX_DAYS,
    DARK_POOL_MAX_SPAN_DAYS,
    DateSpan,
    is_weekday,
    split_date_range,
)
from fse.timekit import SessionCalendar

ONE_DAY = timedelta(days=1)
FIRST_MIN = date(2015, 1, 1)
FIRST_MAX = date(2035, 12, 31)
MAX_RANGE_DAYS = 500  # several 90-trading-day spans
MAX_DAYS_MAX = 120
MAX_MULTIPLE = 5

CountRule = Literal["calendar_days", "weekday", "session_calendar"]


def _days(first: date, last: date) -> list[date]:
    """Every date from ``first`` to ``last``, both inclusive."""
    return [date.fromordinal(o) for o in range(first.toordinal(), last.toordinal() + 1)]


# ---------------------------------------------------------------- scenario


@dataclass(frozen=True, slots=True)
class Case:
    """A requested range, a cap and what counts toward it."""

    first: date
    last: date
    max_days: int
    rule: CountRule
    holidays: frozenset[date] = field(default_factory=frozenset)

    def counts(self) -> Callable[[date], bool] | None:
        """The ``counts`` argument for :func:`split_date_range`."""
        if self.rule == "calendar_days":
            return None
        if self.rule == "weekday":
            return is_weekday
        return SessionCalendar(self.first, self.last, self.holidays).is_session

    def counted(self, d: date) -> bool:
        """Whether ``d`` counts toward the cap, decided here, not by the code under test."""
        if self.rule == "calendar_days":
            return True
        return d.weekday() < 5 and d not in self.holidays

    def describe(self) -> str:
        holidays = ", ".join(d.isoformat() for d in sorted(self.holidays)) or "none"
        return (
            f"{self.first} to {self.last} ({(self.last - self.first).days + 1} days), "
            f"max_days {self.max_days}, counting {self.rule}, holidays [{holidays}]"
        )


def _max_days(cap: int) -> st.SearchStrategy[int]:
    return st.sampled_from((1, cap)) | st.integers(1, MAX_DAYS_MAX)


@st.composite
def _calendar_day_cases(draw: st.DrawFn) -> Case:
    """Dark-pool ranges; the length is arbitrary or within a day of a multiple of the cap."""
    first = draw(st.dates(FIRST_MIN, FIRST_MAX))
    max_days = draw(_max_days(DARK_POOL_MAX_SPAN_DAYS))
    if draw(st.booleans()):
        length = draw(st.integers(1, MAX_RANGE_DAYS))
    else:
        k = draw(st.integers(1, MAX_MULTIPLE))
        length = max(1, k * max_days + draw(st.integers(-1, 1)))
    return Case(first, first + timedelta(days=length - 1), max_days, "calendar_days")


@st.composite
def _trading_day_cases(draw: st.DrawFn) -> Case:
    """Atlas ranges counted by weekday or by a session calendar with holidays."""
    first = draw(st.dates(FIRST_MIN, FIRST_MAX))
    max_days = draw(_max_days(ATLAS_DEFAULT_MAX_DAYS))
    rule: CountRule = draw(st.sampled_from(("weekday", "session_calendar")))
    horizon = _days(first, first + timedelta(days=MAX_RANGE_DAYS - 1))
    weekdays = [d for d in horizon if d.weekday() < 5]

    holidays: set[date] = set()
    if rule == "session_calendar":
        mode = draw(st.sampled_from(("scattered", "run", "every_weekday")))
        if mode == "scattered":
            holidays = draw(st.sets(st.sampled_from(weekdays), max_size=40))
        elif mode == "run":
            start = draw(st.integers(0, len(weekdays) - 1))
            holidays = set(weekdays[start : start + draw(st.integers(1, 30))])
        else:
            holidays = set(weekdays)

    counted = [d for d in weekdays if d not in holidays]
    multiples = len(counted) // max_days
    if multiples >= 1 and draw(st.booleans()):
        # On or next to the (k * max_days)-th counted day, then up to 4 more days.
        k = draw(st.integers(1, min(multiples, MAX_MULTIPLE)))
        index = min(len(counted) - 1, max(0, k * max_days - 1 + draw(st.integers(-1, 1))))
        last = counted[index] + timedelta(days=draw(st.integers(0, 4)))
    else:
        last = first + timedelta(days=draw(st.integers(0, MAX_RANGE_DAYS - 1)))
    return Case(first, last, max_days, rule, frozenset(h for h in holidays if h <= last))


# ---------------------------------------------------------------- partition check


def _assert_partition(spans: Sequence[DateSpan], case: Case) -> None:
    """Contiguous, non-overlapping, and exactly covering ``[case.first, case.last]``."""
    where = case.describe()
    assert spans, f"no spans: {where}"
    assert all(isinstance(s, DateSpan) and s.first <= s.last for s in spans), where

    # Contiguous: no gap at either end or between neighbours.
    assert spans[0].first == case.first, f"first span starts {spans[0].first}: {where}"
    assert spans[-1].last == case.last, f"last span ends {spans[-1].last}: {where}"
    for a, b in pairwise(spans):
        assert b.first == a.last + ONE_DAY, f"{a} then {b}: {where}"

    # Non-overlapping and exact cover.
    covered = Counter(d for s in spans for d in _days(s.first, s.last))
    twice = sorted(d for d, n in covered.items() if n > 1)
    assert not twice, f"dates in more than one span {twice[:5]}: {where}"
    assert set(covered) == set(_days(case.first, case.last)), f"union is not the range: {where}"


# ---------------------------------------------------------------- properties


# Feature: skylit-futures-strategy-engine, Property 11: Request window partition
@given(case=_calendar_day_cases())
@example(case=Case(date(2026, 1, 1), date(2026, 3, 15), DARK_POOL_MAX_SPAN_DAYS, "calendar_days"))
@example(case=Case(date(2024, 2, 1), date(2024, 3, 2), DARK_POOL_MAX_SPAN_DAYS, "calendar_days"))
def test_dark_pool_spans_partition_the_range_within_the_calendar_day_cap(case: Case) -> None:
    spans = split_date_range(case.first, case.last, case.max_days)

    _assert_partition(spans, case)
    for s in spans:
        n = (s.last - s.first).days + 1
        assert n <= case.max_days, f"{s} holds {n} calendar days: {case.describe()}"


# Feature: skylit-futures-strategy-engine, Property 11: Request window partition
@given(case=_trading_day_cases())
@example(case=Case(date(2026, 3, 7), date(2026, 3, 8), 1, "weekday"))  # a weekend only
@example(
    case=Case(
        date(2025, 1, 1),
        date(2025, 12, 31),
        ATLAS_DEFAULT_MAX_DAYS,
        "session_calendar",
        frozenset(
            date.fromisoformat(d)
            for d in (
                "2025-01-01",
                "2025-01-09",
                "2025-01-20",
                "2025-02-17",
                "2025-04-18",
                "2025-05-26",
                "2025-06-19",
                "2025-07-04",
                "2025-09-01",
                "2025-11-27",
                "2025-12-25",
            )
        ),
    )
)
def test_atlas_spans_partition_the_range_within_the_trading_day_cap(case: Case) -> None:
    spans = split_date_range(case.first, case.last, case.max_days, counts=case.counts())

    _assert_partition(spans, case)
    for s in spans:
        n = sum(case.counted(d) for d in _days(s.first, s.last))
        assert n <= case.max_days, f"{s} holds {n} counted trading days: {case.describe()}"
