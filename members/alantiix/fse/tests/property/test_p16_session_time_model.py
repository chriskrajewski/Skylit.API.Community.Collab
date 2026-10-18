"""Property 16: Session-time model.

*For any* session date between 2023 and 2035 (daylight-saving transition dates
and early-close dates included) and any cadence, RTH open converts to 13:30 UTC
under daylight time and 14:30 UTC under standard time, the trading day starts
at 18:00 New York on the prior calendar day, every fill is assigned to the
trading day containing it, the Flat_Deadline is the configured time or the
early-close offset before close, and the Decision_Time grid starts at
09:30:00, steps by the cadence and stops before 16:00:00 (390 entries at 60 s).

The oracle does not use ``zoneinfo``. It applies the US daylight-saving rule in
force since 2007 (daylight time from the second Sunday of March to the first
Sunday of November, switching at 02:00 local) with integer arithmetic, so a
wrong offset in ``timekit`` cannot be mirrored by the oracle. Every wall time
the oracle converts lies between 07:31 and 18:00, after the 02:00 switch, so
its offset is the offset of its date.

Calendars are synthetic: a 15-day window around the session with random
holidays, random early closes (on the session itself about half the time) and
random ``SessionTimes``. Fills are drawn across the neighbouring trading days,
biased to every boundary (18:00, Flat_Deadline) by 0, 1 ns and 1 us, and
compared with a model that scans each session's [18:00 eve, Flat_Deadline]
interval. Explicit examples cover the Friday before and the Monday after every
DST transition from 2023 to 2035, with fills every 30 minutes across the
transition Sunday.

**Validates: Requirements 5.8, 15.14, 15.15, 18.1**
"""

from __future__ import annotations

import operator
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise

from hypothesis import example, given
from hypothesis import strategies as st

from fse.timekit import SessionCalendar, SessionTimes, ny_datetime

NS = 1_000_000_000
DAY_S = 86_400
ONE_DAY = timedelta(days=1)
_EPOCH_ORDINAL = date(1970, 1, 1).toordinal()
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

FIRST_SESSION = date(2023, 1, 1)
LAST_SESSION = date(2035, 12, 31)
WINDOW_DAYS = 7  # calendar covers session - 7 days to session + 7 days
FILL_REACH_NS = 2 * DAY_S * NS  # fills range 2 days either side of the session's trading day

RTH_OPEN_S = 9 * 3600 + 30 * 60
RTH_CLOSE_S = 16 * 3600
DAY_START_S = 18 * 3600
SUNDAY = 6
NUDGES_NS = (-1_000, -1, 0, 1, 1_000)

# ---------------------------------------------------------------- oracle (no zoneinfo)


def _sunday_on_or_after(d: date) -> date:
    return d + timedelta(days=(SUNDAY - d.weekday()) % 7)


def dst_start(year: int) -> date:
    """Second Sunday of March."""
    return _sunday_on_or_after(date(year, 3, 8))


def dst_end(year: int) -> date:
    """First Sunday of November."""
    return _sunday_on_or_after(date(year, 11, 1))


def is_daylight(d: date) -> bool:
    """Whether daylight time is in effect from 03:00 to 24:00 local on ``d``."""
    return dst_start(d.year) <= d < dst_end(d.year)


def utc_offset_s(d: date) -> int:
    return -4 * 3600 if is_daylight(d) else -5 * 3600


def epoch_days(d: date) -> int:
    return d.toordinal() - _EPOCH_ORDINAL


def oracle_instant(d: date, wall_s: int) -> int:
    """New York wall time ``wall_s`` (seconds after midnight, 03:00 or later) on ``d``."""
    return (epoch_days(d) * DAY_S + wall_s - utc_offset_s(d)) * NS


def seconds_of(t: time) -> int:
    return t.hour * 3600 + t.minute * 60 + t.second


def wall(seconds: int) -> time:
    return time(seconds // 3600, seconds % 3600 // 60, seconds % 60)


def _days(first: date, last: date) -> Iterator[date]:
    d = first
    while d <= last:
        yield d
        d += ONE_DAY


def _utc_text(t: int) -> str:
    return (_EPOCH + timedelta(microseconds=t // 1_000)).isoformat()


TRANSITION_SUNDAYS: tuple[date, ...] = tuple(
    sorted(
        rule(y)
        for y in range(FIRST_SESSION.year, LAST_SESSION.year + 1)
        for rule in (dst_start, dst_end)
    )
)

# ---------------------------------------------------------------- case


@dataclass(frozen=True, slots=True)
class Case:
    """One session plus the synthetic calendar, cadence and fill instants around it."""

    session: date
    first: date
    last: date
    holidays: frozenset[date]
    early_closes: tuple[tuple[date, time], ...]
    times: SessionTimes
    cadence_s: int
    fills: tuple[int, ...]

    def calendar(self) -> SessionCalendar:
        return SessionCalendar(
            self.first, self.last, self.holidays, dict(self.early_closes), self.times
        )

    def sessions(self) -> list[date]:
        return [
            d for d in _days(self.first, self.last) if d.weekday() < 5 and d not in self.holidays
        ]

    def close_s(self, d: date) -> int:
        close = dict(self.early_closes).get(d)
        return RTH_CLOSE_S if close is None else seconds_of(close)

    def day_start(self, d: date) -> int:
        return oracle_instant(d - ONE_DAY, DAY_START_S)

    def flat_deadline(self, d: date) -> int:
        if d in dict(self.early_closes):
            offset_s = self.times.flat_deadline_early_close_offset_min * 60
            return oracle_instant(d, self.close_s(d) - offset_s)
        return oracle_instant(d, seconds_of(self.times.flat_deadline))

    def flatten_time(self, d: date) -> int:
        if d in dict(self.early_closes):
            lead_s = self.times.flatten_early_close_lead_min * 60
            return oracle_instant(d, self.close_s(d) - lead_s)
        return oracle_instant(d, seconds_of(self.times.flatten_time))

    def fill_window(self) -> tuple[int, int]:
        d = self.session
        return self.day_start(d) - FILL_REACH_NS, self.flat_deadline(d) + FILL_REACH_NS

    def anchors(self) -> list[int]:
        """Every 18:00 and Flat_Deadline inside the fill window."""
        lo, hi = self.fill_window()
        points = {oracle_instant(d, DAY_START_S) for d in _days(self.first, self.last)}
        points |= {self.flat_deadline(s) for s in self.sessions()}
        return sorted(p for p in points if lo <= p <= hi)

    def expected_trading_day(self, t: int) -> date | None:
        for s in self.sessions():
            if self.day_start(s) <= t <= self.flat_deadline(s):
                return s
        return None


# ---------------------------------------------------------------- generators


def _to_weekday(d: date) -> date:
    """Saturday to the Friday before, Sunday to the Monday after."""
    return d + timedelta(days={5: -1, 6: 1}.get(d.weekday(), 0))


def _shift(d: date, days: int) -> date:
    return d + timedelta(days=days)


def _minutes_to_time(minutes: int) -> time:
    return time(minutes // 60, minutes % 60)


SESSION_DATES = st.one_of(
    # Weekdays from Tuesday before to Friday after each transition Sunday.
    st.builds(
        _shift,
        st.sampled_from(TRANSITION_SUNDAYS),
        st.sampled_from((-5, -4, -3, -2, 1, 2, 3, 4, 5)),
    ),
    st.dates(FIRST_SESSION, LAST_SESSION).map(_to_weekday),
)
EARLY_CLOSE_TIMES = st.one_of(
    st.just(time(13, 0)), st.integers(9 * 60 + 31, 15 * 60 + 59).map(_minutes_to_time)
)
SESSION_TIMES = st.one_of(
    st.just(SessionTimes()),
    st.builds(
        SessionTimes,
        flatten_time=st.integers(RTH_OPEN_S, RTH_CLOSE_S).map(wall),
        flatten_early_close_lead_min=st.integers(15, 120),
        flat_deadline=st.integers(RTH_OPEN_S + 1, DAY_START_S - 1).map(wall),
        flat_deadline_early_close_offset_min=st.integers(0, 60),
    ),
)
CADENCES = st.one_of(st.sampled_from((1, 5, 60, 300, 23_400, 23_401)), st.integers(1, 30_000))


@st.composite
def cases(draw: st.DrawFn) -> Case:
    session = draw(SESSION_DATES)
    first, last = session - WINDOW_DAYS * ONE_DAY, session + WINDOW_DAYS * ONE_DAY
    weekdays = [d for d in _days(first, last) if d.weekday() < 5 and d != session]
    holidays = draw(st.frozensets(st.sampled_from(weekdays), max_size=4))
    others = [d for d in weekdays if d not in holidays]
    early = draw(st.dictionaries(st.sampled_from(others), EARLY_CLOSE_TIMES, max_size=3))
    if draw(st.booleans()):
        early[session] = draw(EARLY_CLOSE_TIMES)
    case = Case(
        session=session,
        first=first,
        last=last,
        holidays=holidays,
        early_closes=tuple(sorted(early.items())),
        times=draw(SESSION_TIMES),
        cadence_s=draw(CADENCES),
        fills=(),
    )
    lo, hi = case.fill_window()
    near_boundary = st.builds(
        operator.add, st.sampled_from(case.anchors()), st.sampled_from(NUDGES_NS)
    )
    fills = draw(st.lists(st.one_of(st.integers(lo, hi), near_boundary), min_size=1, max_size=24))
    return replace(case, fills=tuple(fills))


def _dst_case(session: date, sunday: date, early_close: time | None) -> Case:
    """Default times, no holidays, 60 s cadence; fills at every boundary and across ``sunday``."""
    case = Case(
        session=session,
        first=session - WINDOW_DAYS * ONE_DAY,
        last=session + WINDOW_DAYS * ONE_DAY,
        holidays=frozenset(),
        early_closes=() if early_close is None else ((session, early_close),),
        times=SessionTimes(),
        cadence_s=60,
        fills=(),
    )
    sunday_utc = epoch_days(sunday) * DAY_S * NS
    across_sunday = range(sunday_utc, sunday_utc + DAY_S * NS + 1, 1_800 * NS)
    near = {a + n for a in case.anchors() for n in NUDGES_NS}
    return replace(case, fills=tuple(sorted(near | set(across_sunday))))


def _with_dst_examples[F: Callable[..., None]](test: F) -> F:
    """The Friday before (an early close) and the Monday after each transition Sunday."""
    for sunday in TRANSITION_SUNDAYS:
        test = example(case=_dst_case(sunday - 2 * ONE_DAY, sunday, time(13, 0)))(test)
        test = example(case=_dst_case(sunday + ONE_DAY, sunday, None))(test)
    return test


# ---------------------------------------------------------------- property


# Feature: skylit-futures-strategy-engine, Property 16: Session-time model
@_with_dst_examples
@given(case=cases())
def test_session_time_model(case: Case) -> None:
    cal = case.calendar()
    d = case.session
    eve = d - ONE_DAY

    # RTH open: 13:30 UTC under daylight time, 14:30 UTC under standard time (Req 5.8).
    rth_open = cal.rth_open(d)
    utc_hm = (13, 30) if is_daylight(d) else (14, 30)
    assert rth_open == (epoch_days(d) * DAY_S + utc_hm[0] * 3600 + utc_hm[1] * 60) * NS
    assert rth_open == oracle_instant(d, RTH_OPEN_S)
    local = ny_datetime(rth_open)
    assert (local.date(), local.time(), local.utcoffset()) == (
        d,
        time(9, 30),
        timedelta(seconds=utc_offset_s(d)),
    )
    # The other session-time rules use the same date's offset (Req 5.8).
    assert cal.rth_close(d) == oracle_instant(d, case.close_s(d))
    assert cal.flatten_time(d) == case.flatten_time(d)

    # Trading day starts at 18:00 New York on the prior calendar day (Req 5.8, 15.14):
    # 22:00 UTC under daylight time on that day, 23:00 UTC under standard time.
    start = cal.trading_day_start(d)
    utc_hour = 22 if is_daylight(eve) else 23
    assert start == (epoch_days(eve) * DAY_S + utc_hour * 3600) * NS
    local = ny_datetime(start)
    assert (local.date(), local.time()) == (eve, time(18, 0))

    # Flat_Deadline: the configured time, or the early-close offset before close (Req 15.15).
    deadline = cal.flat_deadline(d)
    assert deadline == case.flat_deadline(d)
    if cal.is_early_close(d):
        offset_ns = case.times.flat_deadline_early_close_offset_min * 60 * NS
        assert cal.rth_close(d) - deadline == offset_ns
    else:
        assert ny_datetime(deadline).time() == case.times.flat_deadline

    # Every fill belongs to the trading day containing it, both ends inclusive (Req 15.14).
    assert cal.trading_day_of(start) == d
    assert cal.trading_day_of(start - 1) != d
    assert cal.trading_day_of(deadline) == d
    assert cal.trading_day_of(deadline + 1) is None
    for t in case.fills:
        expected = case.expected_trading_day(t)
        assert cal.trading_day_of(t) == expected, f"fill at {t} ns ({_utc_text(t)})"

    # Decision_Time grid: 09:30:00 + k x cadence while before 16:00:00 (Req 18.1).
    grid = cal.decision_times(d, case.cadence_s)
    step = case.cadence_s * NS
    stop = oracle_instant(d, RTH_CLOSE_S)
    assert grid[0] == rth_open
    assert all(b - a == step for a, b in pairwise(grid))
    assert grid[-1] < stop <= grid[-1] + step
    assert len(grid) == -(-(RTH_CLOSE_S - RTH_OPEN_S) // case.cadence_s)
    if case.cadence_s == 60:
        assert len(grid) == 390
