"""Examples for the time model (design "Time model").

The calendar below is synthetic test data, not the Project's exchange calendar.

**Validates: Requirements 4.7, 5.8, 12.15, 15.14, 15.15, 18.1**
"""

from __future__ import annotations

from datetime import date, time
from itertools import pairwise

import pytest

from fse.timekit import (
    NS_PER_MINUTE,
    NS_PER_SECOND,
    Instant,
    NotASessionError,
    OutsideCoverageError,
    SessionCalendar,
    SessionTimes,
    from_unix_seconds,
    ny_datetime,
    ny_instant,
    parse_rfc3339,
)

GOOD_FRIDAY = date(2026, 4, 3)
THANKSGIVING = date(2026, 11, 26)
DAY_AFTER_THANKSGIVING = date(2026, 11, 27)  # Friday, early close
MON = date(2026, 3, 9)  # Monday after the 2026-03-08 spring-forward Sunday
WINTER_THU = date(2026, 3, 5)
SUMMER_WED = date(2026, 7, 1)


def utc(text: str) -> Instant:
    return parse_rfc3339(text)


@pytest.fixture
def cal() -> SessionCalendar:
    return SessionCalendar(
        first=date(2026, 1, 1),
        last=date(2026, 12, 31),
        holidays=[date(2026, 1, 1), GOOD_FRIDAY, THANKSGIVING, date(2026, 12, 25)],
        early_closes={DAY_AFTER_THANKSGIVING: time(13, 0), date(2026, 12, 24): time(13, 0)},
    )


# ---------------------------------------------------------------- conversions


def test_ny_instant_uses_the_offset_in_effect_on_each_date() -> None:
    assert ny_instant(WINTER_THU, time(9, 30)) == utc("2026-03-05T14:30:00Z")
    assert ny_instant(SUMMER_WED, time(9, 30)) == utc("2026-07-01T13:30:00Z")
    # The decision-log example in the design.
    assert ny_instant(WINTER_THU, time(9, 30)) == 1_772_721_000_000_000_000


@pytest.mark.parametrize(
    ("d", "wall", "problem"),
    [
        (date(2026, 3, 8), time(2, 30), "does not exist"),
        (date(2026, 11, 1), time(1, 30), "is ambiguous"),
    ],
)
def test_ny_instant_rejects_dst_gap_and_overlap(d: date, wall: time, problem: str) -> None:
    with pytest.raises(ValueError, match=problem):
        ny_instant(d, wall)


def test_ny_datetime_round_trips() -> None:
    t = ny_instant(MON, time(18, 0))
    local = ny_datetime(t)
    assert (local.date(), local.time()) == (MON, time(18, 0))
    assert local.isoformat() == "2026-03-09T18:00:00-04:00"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-03-05T14:30:00Z", 1_772_721_000 * NS_PER_SECOND),
        ("2026-03-05T09:30:00-05:00", 1_772_721_000 * NS_PER_SECOND),
        ("2026-03-05t14:30:00z", 1_772_721_000 * NS_PER_SECOND),
        ("2026-03-05 15:30:00+01:00", 1_772_721_000 * NS_PER_SECOND),
        ("2026-03-05T14:30:00.123456789Z", 1_772_721_000 * NS_PER_SECOND + 123_456_789),
        ("2026-03-05T14:30:00.5+00:00", 1_772_721_000 * NS_PER_SECOND + 500_000_000),
        ("2026-03-05T14:30:00.0000000+00:00", 1_772_721_000 * NS_PER_SECOND),
    ],
)
def test_parse_rfc3339_is_exact(text: str, expected: Instant) -> None:
    assert parse_rfc3339(text) == expected


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("2026-03-05T14:30:00", "with an offset"),
        ("2026-03-05", "with an offset"),
        ("not a time", "with an offset"),
        ("2026-03-05T14:30:00.1234567891Z", "more than 9"),
        ("2026-06-30T23:59:60Z", "leap seconds"),
        ("2026-13-05T14:30:00Z", "invalid RFC 3339"),
        ("2026-02-30T14:30:00Z", "invalid RFC 3339"),
        ("2026-03-05T24:00:00Z", "invalid RFC 3339"),
        ("2026-03-05T14:30:00+24:00", "invalid UTC offset"),
    ],
)
def test_parse_rfc3339_rejects_bad_input(text: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        parse_rfc3339(text)


def test_from_unix_seconds() -> None:
    assert from_unix_seconds(1_772_721_000) == utc("2026-03-05T14:30:00Z")
    assert from_unix_seconds(1_772_721_000.0) == utc("2026-03-05T14:30:00Z")
    with pytest.raises(ValueError, match="whole number"):
        from_unix_seconds(1_772_721_000.5)
    with pytest.raises(ValueError, match="whole number"):
        from_unix_seconds(float("nan"))
    with pytest.raises(TypeError, match="bool"):
        from_unix_seconds(True)


# ---------------------------------------------------------------- sessions


def test_sessions_skip_weekends_and_holidays(cal: SessionCalendar) -> None:
    assert cal.sessions(date(2026, 3, 30), date(2026, 4, 5)) == (
        date(2026, 3, 30),
        date(2026, 3, 31),
        date(2026, 4, 1),
        date(2026, 4, 2),
    )
    assert cal.is_session(MON)
    assert not cal.is_session(date(2026, 3, 8))
    assert not cal.is_session(GOOD_FRIDAY)
    assert cal.sessions(date(2026, 3, 9), date(2026, 3, 8)) == ()


def test_dates_outside_coverage_raise(cal: SessionCalendar) -> None:
    with pytest.raises(OutsideCoverageError, match="2027-01-04"):
        cal.is_session(date(2027, 1, 4))
    with pytest.raises(OutsideCoverageError):
        cal.sessions(date(2025, 12, 29), date(2026, 1, 9))


def test_non_session_dates_raise(cal: SessionCalendar) -> None:
    for query in (cal.rth_open, cal.rth_close, cal.flatten_time, cal.flat_deadline):
        with pytest.raises(NotASessionError, match="2026-04-03"):
            query(GOOD_FRIDAY)
    with pytest.raises(NotASessionError):
        cal.decision_times(date(2026, 3, 7), 60)


def test_rth_on_a_regular_session(cal: SessionCalendar) -> None:
    assert cal.rth_open(WINTER_THU) == utc("2026-03-05T14:30:00Z")
    assert cal.rth_close(WINTER_THU) == utc("2026-03-05T21:00:00Z")
    assert cal.rth_open(SUMMER_WED) == utc("2026-07-01T13:30:00Z")
    assert not cal.is_early_close(WINTER_THU)


def test_early_close_session(cal: SessionCalendar) -> None:
    d = DAY_AFTER_THANKSGIVING
    assert cal.is_early_close(d)
    assert cal.rth_close(d) == utc("2026-11-27T18:00:00Z")  # 13:00 EST
    assert cal.pull_window(d) == (utc("2026-11-27T14:00:00Z"), utc("2026-11-27T18:00:00Z"))
    assert cal.flatten_time(d) == ny_instant(d, time(12, 30))  # 30 min lead
    assert cal.flat_deadline(d) == ny_instant(d, time(12, 45))  # 15 min offset


def test_default_session_times(cal: SessionCalendar) -> None:
    assert cal.pull_window(WINTER_THU) == (
        ny_instant(WINTER_THU, time(9, 0)),
        ny_instant(WINTER_THU, time(16, 0)),
    )
    assert cal.flatten_time(WINTER_THU) == ny_instant(WINTER_THU, time(15, 55))
    assert cal.flat_deadline(WINTER_THU) == ny_instant(WINTER_THU, time(16, 10))


def test_configured_session_times() -> None:
    times = SessionTimes(
        pull_start=time(8, 30),
        pull_end=time(15, 0),
        flatten_time=time(15, 30),
        flatten_early_close_lead_min=60,
        flat_deadline=time(16, 5),
        flat_deadline_early_close_offset_min=0,
    )
    d, early = date(2026, 11, 25), DAY_AFTER_THANKSGIVING
    cal = SessionCalendar(d, early, early_closes={early: time(13, 0)}, times=times)
    assert cal.pull_window(d) == (ny_instant(d, time(8, 30)), ny_instant(d, time(15, 0)))
    assert cal.pull_window(early)[1] == ny_instant(early, time(13, 0))
    assert cal.flatten_time(d) == ny_instant(d, time(15, 30))
    assert cal.flatten_time(early) == ny_instant(early, time(12, 0))
    assert cal.flat_deadline(d) == ny_instant(d, time(16, 5))
    assert cal.flat_deadline(early) == ny_instant(early, time(13, 0))


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"pull_start": time(16, 0)}, "pull_start"),
        ({"flatten_time": time(16, 1)}, "flatten_time"),
        ({"flatten_time": time(9, 29)}, "flatten_time"),
        ({"flatten_early_close_lead_min": 14}, "15 to 120"),
        ({"flatten_early_close_lead_min": 121}, "15 to 120"),
        ({"flat_deadline_early_close_offset_min": 61}, "0 to 60"),
        ({"flat_deadline_early_close_offset_min": -1}, "0 to 60"),
        ({"flat_deadline_early_close_offset_min": True}, "0 to 60"),
        ({"flat_deadline": time(18, 0)}, "flat_deadline"),
    ],
)
def test_session_times_reject_out_of_range(kwargs: dict[str, object], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        SessionTimes(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"first": date(2026, 2, 1), "last": date(2026, 1, 1)}, "after last date"),
        ({"holidays": [date(2027, 1, 1)]}, "outside"),
        ({"early_closes": {date(2027, 7, 2): time(13, 0)}}, "outside"),
        ({"early_closes": {date(2026, 3, 7): time(13, 0)}}, "not a session"),
        ({"holidays": [GOOD_FRIDAY], "early_closes": {GOOD_FRIDAY: time(13, 0)}}, "not a session"),
        ({"early_closes": {WINTER_THU: time(16, 0)}}, "before 16:00"),
        ({"early_closes": {WINTER_THU: time(9, 30)}}, "after 09:30"),
        (
            {"early_closes": {WINTER_THU: time(10, 0)}, "times": SessionTimes(pull_start=time(10))},
            "Pull_Window start",
        ),
    ],
)
def test_calendar_rejects_inconsistent_input(kwargs: dict[str, object], match: str) -> None:
    args: dict[str, object] = {"first": date(2026, 1, 1), "last": date(2026, 12, 31), **kwargs}
    with pytest.raises(ValueError, match=match):
        SessionCalendar(**args)  # type: ignore[arg-type]


# ---------------------------------------------------------------- trading days


def test_trading_day_starts_at_1800_on_the_prior_calendar_day(cal: SessionCalendar) -> None:
    # Monday after the spring-forward Sunday: Sunday 18:00 EDT = 22:00 UTC.
    assert cal.trading_day_start(MON) == utc("2026-03-08T22:00:00Z")
    # Monday after the fall-back Sunday: Sunday 18:00 EST = 23:00 UTC.
    assert cal.trading_day_start(date(2026, 11, 2)) == utc("2026-11-01T23:00:00Z")
    # The session after a holiday starts at 18:00 on the holiday itself.
    assert cal.trading_day_start(date(2026, 1, 2)) == ny_instant(date(2026, 1, 1), time(18))


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        ("2026-03-08T22:00:00Z", MON),  # Sunday 18:00 EDT
        ("2026-03-09T12:00:00Z", MON),
        ("2026-03-09T20:10:00Z", MON),  # Flat_Deadline 16:10 EDT, inclusive
        ("2026-03-09T20:10:00.000000001Z", None),
        ("2026-03-09T21:59:59.999999999Z", None),
        ("2026-03-09T22:00:00Z", date(2026, 3, 10)),  # Monday 18:00 starts Tuesday
        ("2026-03-08T21:59:59.999999999Z", None),  # Sunday 17:59
        ("2026-03-13T23:00:00Z", None),  # Friday 19:00 belongs to no session
        ("2026-04-02T23:00:00Z", None),  # Thursday 19:00 before Good Friday
        ("2026-11-27T17:45:00Z", DAY_AFTER_THANKSGIVING),  # early-close deadline 12:45 EST
        ("2026-11-27T17:46:00Z", None),
        ("2026-11-25T23:30:00Z", None),  # Wednesday 18:30 before Thanksgiving
    ],
)
def test_trading_day_of(cal: SessionCalendar, instant: str, expected: date | None) -> None:
    assert cal.trading_day_of(utc(instant)) == expected


def test_trading_day_of_outside_coverage_raises(cal: SessionCalendar) -> None:
    with pytest.raises(OutsideCoverageError):
        cal.trading_day_of(ny_instant(date(2026, 12, 31), time(18, 0)))


# ---------------------------------------------------------------- Decision_Time grid


def test_decision_grid_default_cadence(cal: SessionCalendar) -> None:
    grid = cal.decision_times(WINTER_THU, 60)
    assert len(grid) == 390
    assert grid[0] == utc("2026-03-05T14:30:00Z")
    assert grid[-1] == ny_instant(WINTER_THU, time(15, 59))
    assert {b - a for a, b in pairwise(grid)} == {NS_PER_MINUTE}


@pytest.mark.parametrize(("cadence_s", "count"), [(5, 4680), (300, 78), (7, 3343), (23400, 1)])
def test_decision_grid_other_cadences(cal: SessionCalendar, cadence_s: int, count: int) -> None:
    grid = cal.decision_times(SUMMER_WED, cadence_s)
    assert len(grid) == count
    assert grid[0] == ny_instant(SUMMER_WED, time(9, 30))
    assert grid[-1] < ny_instant(SUMMER_WED, time(16, 0))


def test_decision_grid_runs_to_1600_on_early_close(cal: SessionCalendar) -> None:
    grid = cal.decision_times(DAY_AFTER_THANKSGIVING, 60)
    assert len(grid) == 390
    assert grid[-1] == ny_instant(DAY_AFTER_THANKSGIVING, time(15, 59))


@pytest.mark.parametrize("cadence_s", [0, -60, True])
def test_decision_grid_rejects_bad_cadence(cal: SessionCalendar, cadence_s: int) -> None:
    with pytest.raises(ValueError, match="Decision_Cadence"):
        cal.decision_times(WINTER_THU, cadence_s)
