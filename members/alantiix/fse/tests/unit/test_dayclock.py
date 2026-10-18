"""The host day clock (design 5.1): Globex days, the weekend gap and DST on both sides."""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest

from fse.calendars import load_calendars
from fse.live.host.dayclock import (
    globex_day,
    globex_day_at,
    next_globex_day,
    next_open,
    next_open_ns,
    previous_globex_day,
)
from fse.timekit import NS_PER_SECOND
from tests.fakes.dataplane import CALENDAR_DIR, ny


def utc(y: int, mo: int, d: int, h: int, mi: int = 0) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=UTC).timestamp()) * NS_PER_SECOND


def test_a_globex_day_runs_from_1800_the_day_before() -> None:
    wed = globex_day(date(2026, 10, 14))
    assert wed.open_ns == ny(date(2026, 10, 13), 18)
    assert wed.rotate_ns == ny(date(2026, 10, 14), 18)
    mon = globex_day(date(2026, 10, 19))
    assert mon.open_ns == ny(date(2026, 10, 18), 18)  # Sunday 18:00
    with pytest.raises(ValueError, match="Saturday"):
        globex_day(date(2026, 10, 17))


def test_spring_forward_both_sides() -> None:
    # DST starts Sunday 2026-03-08 02:00 NY
    fri = globex_day(date(2026, 3, 6))
    assert fri.rotate_ns == utc(2026, 3, 6, 23)  # 18:00 EST
    mon = globex_day(date(2026, 3, 9))
    assert mon.open_ns == utc(2026, 3, 8, 22)  # 18:00 EDT on the change day
    assert mon.rotate_ns == utc(2026, 3, 9, 22)
    assert mon.rotate_ns - mon.open_ns == 24 * 3600 * NS_PER_SECOND


def test_fall_back_both_sides() -> None:
    # DST ends Sunday 2026-11-01 02:00 NY
    fri = globex_day(date(2026, 10, 30))
    assert fri.rotate_ns == utc(2026, 10, 30, 22)  # 18:00 EDT
    mon = globex_day(date(2026, 11, 2))
    assert mon.open_ns == utc(2026, 11, 1, 23)  # 18:00 EST
    assert globex_day_at(utc(2026, 11, 1, 22, 59)) is None  # 17:59 EST Sunday
    assert globex_day_at(utc(2026, 11, 1, 23)) == mon


@pytest.mark.parametrize(
    ("wall", "expected"),
    [
        ((date(2026, 10, 16), time(17, 59)), date(2026, 10, 16)),
        ((date(2026, 10, 16), time(18, 0)), None),  # Friday 18:00: weekend gap
        ((date(2026, 10, 17), time(12, 0)), None),
        ((date(2026, 10, 18), time(17, 59)), None),
        ((date(2026, 10, 18), time(18, 0)), date(2026, 10, 19)),
        ((date(2026, 10, 14), time(18, 0)), date(2026, 10, 15)),
        ((date(2026, 10, 14), time(0, 30)), date(2026, 10, 14)),
    ],
)
def test_globex_day_at(wall: tuple[date, time], expected: date | None) -> None:
    d, t = wall
    found = globex_day_at(ny(d, t.hour, t.minute))
    assert (None if found is None else found.day) == expected


def test_next_open_and_neighbours() -> None:
    assert next_open(ny(date(2026, 10, 16), 19)).day == date(2026, 10, 19)
    assert next_open(ny(date(2026, 10, 14), 10)).day == date(2026, 10, 15)
    assert next_open_ns(ny(date(2026, 10, 14), 18)) == ny(date(2026, 10, 15), 18)
    assert next_globex_day(date(2026, 10, 16)).day == date(2026, 10, 19)
    assert previous_globex_day(date(2026, 10, 19)).day == date(2026, 10, 16)


def test_exchange_flags_from_the_calendar() -> None:
    cal = load_calendars(CALENDAR_DIR, date(2026, 11, 20), date(2026, 12, 31)).exchange.sessions
    thanksgiving = globex_day(date(2026, 11, 26), cal)
    assert thanksgiving.exchange_session is False
    assert thanksgiving.early_close is None
    after = globex_day(date(2026, 11, 27), cal)
    assert after.exchange_session is True
    assert after.early_close == time(13, 15)
    plain = globex_day(date(2026, 11, 24), cal)
    assert plain.exchange_session is True
    assert plain.early_close is None
    # outside the calendar a weekday counts as a session
    assert globex_day(date(2027, 1, 4), cal).exchange_session is True
    assert globex_day(date(2026, 11, 26)).exchange_session is True  # no calendar
