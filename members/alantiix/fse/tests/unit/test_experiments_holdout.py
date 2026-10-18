"""Unit tests for the Holdout_Period computation.

**Validates: Requirements 22.1**
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction

import pytest

from fse.experiments.holdout import (
    HOLDOUT_FRACTION_DEFAULT,
    HOLDOUT_FRACTION_MAX,
    HOLDOUT_FRACTION_MIN,
    HoldoutError,
    HoldoutPeriod,
    compute_holdout_period,
    holdout_session_count,
)
from fse.timekit import OutsideCoverageError, SessionCalendar

CALENDAR = SessionCalendar(date(2026, 1, 1), date(2026, 12, 31), holidays=[date(2026, 1, 19)])


def sessions(n: int) -> list[date]:
    """The first ``n`` sessions of ``CALENDAR``."""
    return list(CALENDAR.sessions()[:n])


# ---------------------------------------------------------------- the count


def test_default_fraction_takes_the_newest_fifth() -> None:
    days = sessions(10)
    period = compute_holdout_period(days)
    assert HOLDOUT_FRACTION_DEFAULT == 0.20
    assert period.sessions == tuple(days[-2:])
    assert period.n_sessions_with_data == 10
    assert period.fraction == 0.20


def test_count_rounds_up_to_a_whole_session() -> None:
    days = sessions(11)
    period = compute_holdout_period(days, 0.20)  # 2.2 sessions
    assert period.sessions == tuple(days[-3:])


def test_float_fraction_is_read_as_its_decimal_value() -> None:
    assert math.ceil(0.07 * 100) == 8  # binary rounding would add a session
    assert holdout_session_count(100, 0.07) == 7
    assert holdout_session_count(100, Decimal("0.07")) == 7
    assert holdout_session_count(100, Fraction(7, 100)) == 7


def test_count_is_exact_ceiling_for_every_two_decimal_fraction() -> None:
    for hundredths in range(5, 51):
        fraction = hundredths / 100
        for n in range(201):
            assert holdout_session_count(n, fraction) == -(-hundredths * n // 100), (fraction, n)


@pytest.mark.parametrize("fraction", [HOLDOUT_FRACTION_MIN, HOLDOUT_FRACTION_MAX])
def test_fraction_bounds_are_inclusive(fraction: float) -> None:
    days = sessions(40)
    period = compute_holdout_period(days, fraction)
    assert len(period) == math.ceil(Fraction(repr(fraction)) * 40)
    assert period.last == days[-1]


@pytest.mark.parametrize(
    "fraction",
    [0.0, 0.0499, 0.501, 1.0, -0.2, math.nan, math.inf, Decimal("NaN"), True, "0.2", None],
)
def test_fraction_outside_range_or_not_a_number_is_rejected(fraction: object) -> None:
    with pytest.raises(HoldoutError, match="holdout fraction"):
        compute_holdout_period(sessions(10), fraction)  # type: ignore[arg-type]


def test_negative_or_non_integer_session_count_is_rejected() -> None:
    with pytest.raises(HoldoutError, match="negative"):
        holdout_session_count(-1, 0.2)
    with pytest.raises(HoldoutError, match="integer"):
        holdout_session_count(True, 0.2)


# ---------------------------------------------------------------- the sessions


def test_holdout_uses_sessions_with_data_not_calendar_sessions() -> None:
    days = sessions(12)
    with_data = [d for i, d in enumerate(days) if i not in (9, 10)]  # two sessions missing
    period = compute_holdout_period(with_data, 0.20)  # ceil(0.2 x 10) = 2
    assert period.sessions == (days[8], days[11])
    assert period.n_sessions_with_data == 10


def test_order_and_repeats_do_not_matter() -> None:
    days = sessions(10)
    shuffled = [*reversed(days), days[3], days[9]]
    assert compute_holdout_period(shuffled) == compute_holdout_period(days)


def test_single_session_is_its_own_holdout() -> None:
    day = sessions(1)[0]
    period = compute_holdout_period([day], 0.05)
    assert period.sessions == (day,)
    assert period.first == period.last == day


def test_no_sessions_with_data_is_rejected() -> None:
    with pytest.raises(HoldoutError, match="no sessions with data"):
        compute_holdout_period([])


@pytest.mark.parametrize("value", [datetime(2026, 1, 2, 9, 30), "2026-01-02", None])
def test_values_that_are_not_dates_are_rejected(value: object) -> None:
    with pytest.raises(HoldoutError, match="must be dates"):
        compute_holdout_period([date(2026, 1, 5), value])  # type: ignore[list-item]


def test_calendar_rejects_a_non_session_date() -> None:
    with pytest.raises(HoldoutError, match="not a session"):
        compute_holdout_period([date(2026, 1, 16), date(2026, 1, 17)], calendar=CALENDAR)
    with pytest.raises(HoldoutError, match="not a session"):
        compute_holdout_period([date(2026, 1, 19)], calendar=CALENDAR)  # holiday


def test_calendar_rejects_a_date_outside_its_coverage() -> None:
    with pytest.raises(OutsideCoverageError):
        compute_holdout_period([date(2025, 12, 31)], calendar=CALENDAR)


def test_calendar_accepts_its_sessions() -> None:
    days = sessions(20)
    assert compute_holdout_period(days, calendar=CALENDAR) == compute_holdout_period(days)


# ---------------------------------------------------------------- HoldoutPeriod


def test_period_accessors_and_manifest_json() -> None:
    days = sessions(10)
    period = compute_holdout_period(days, 0.30)
    assert len(period) == 3
    assert (period.first, period.last) == (days[7], days[9])
    assert days[8] in period
    assert days[6] not in period
    assert datetime(days[8].year, days[8].month, days[8].day) not in period
    assert "2026-01-14" not in period
    assert period.to_json() == {"first": days[7].isoformat(), "last": days[9].isoformat()}


def test_period_is_immutable() -> None:
    period = compute_holdout_period(sessions(10))
    with pytest.raises(AttributeError):
        period.sessions = ()  # type: ignore[misc]


def test_period_rejects_inconsistent_construction() -> None:
    days = tuple(sessions(3))
    with pytest.raises(HoldoutError, match="at least one session"):
        HoldoutPeriod(sessions=(), fraction=0.2, n_sessions_with_data=0)
    with pytest.raises(HoldoutError, match="oldest first"):
        HoldoutPeriod(sessions=(days[1], days[0]), fraction=0.5, n_sessions_with_data=4)
    with pytest.raises(HoldoutError, match="oldest first"):
        HoldoutPeriod(sessions=(days[0], days[0]), fraction=0.5, n_sessions_with_data=4)
    with pytest.raises(HoldoutError, match="holds 1 sessions, not 2"):
        HoldoutPeriod(sessions=days[:2], fraction=0.2, n_sessions_with_data=5)
    with pytest.raises(HoldoutError, match="holdout fraction"):
        HoldoutPeriod(sessions=days[:1], fraction=0.9, n_sessions_with_data=1)


def test_sessions_span_gaps_on_the_calendar() -> None:
    # A holdout across a weekend and a holiday is still consecutive sessions with data.
    days = [date(2026, 1, 15) + timedelta(days=i) for i in range(6)]
    with_data = [d for d in days if CALENDAR.is_session(d)]  # 15, 16, 20
    period = compute_holdout_period(with_data, 0.50, calendar=CALENDAR)
    assert period.sessions == (date(2026, 1, 16), date(2026, 1, 20))
