"""The plugin host's day clock: calendar-free weekly Globex days (design 5.1).

A Globex day ``d`` (Monday to Friday) is ``[d-1 18:00 NY, d 18:00 NY)``: Sunday
18:00 opens Monday, and Friday 18:00 to Sunday 18:00 belongs to no day (the
weekend gap). Holidays are ordinary Globex days on which the data plane simply
receives no bars; exchange sessions are left to plugins.

Every instant is computed with ``America/New_York`` per date
(:func:`fse.timekit.ny_instant`), never by adding 24 hours, so 18:00 NY is
right on both sides of a DST change.

``GlobexDay.exchange_session`` and ``early_close`` come from an FSE exchange
calendar when one is passed and covers the day; without one (or outside its
coverage) a weekday counts as a session with no early close.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time, timedelta
from typing import Final

from fse.timekit import TRADING_DAY_START, Instant, SessionCalendar, ny_datetime, ny_instant

__all__ = [
    "DATA_PAUSE",
    "GlobexDay",
    "globex_day",
    "globex_day_at",
    "next_globex_day",
    "next_open",
    "next_open_ns",
    "previous_globex_day",
]

DATA_PAUSE: Final = time(17, 2)
"""The data plane polls bars from a day's open to this NY time (the 17:00 close plus slack)."""

_ONE_DAY: Final = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class GlobexDay:
    """One Globex day: ``[day-1 18:00 NY, day 18:00 NY)``."""

    day: date
    """The Globex day (Monday to Friday)."""
    open_ns: Instant
    """``day - 1`` 18:00 NY (Sunday 18:00 for a Monday)."""
    rotate_ns: Instant
    """``day`` 18:00 NY: the end of the day and of its recording."""
    exchange_session: bool
    """The FSE exchange calendar says ``day`` is a session."""
    early_close: time | None
    """The exchange calendar's early close (RTH) of ``day``, if any."""


def _session_flags(d: date, calendar: SessionCalendar | None) -> tuple[bool, time | None]:
    if calendar is None or not calendar.covers(d):
        return d.weekday() < 5, None
    if not calendar.is_session(d):
        return False, None
    return True, calendar.early_closes.get(d)


def globex_day(d: date, calendar: SessionCalendar | None = None) -> GlobexDay:
    """The Globex day of weekday ``d``; ``ValueError`` for a Saturday or Sunday."""
    if d.weekday() >= 5:
        raise ValueError(f"{d} is a {d:%A}: Globex days are Monday to Friday")
    session, early = _session_flags(d, calendar)
    return GlobexDay(
        day=d,
        open_ns=ny_instant(d - _ONE_DAY, TRADING_DAY_START),
        rotate_ns=ny_instant(d, TRADING_DAY_START),
        exchange_session=session,
        early_close=early,
    )


def globex_day_at(now: Instant, calendar: SessionCalendar | None = None) -> GlobexDay | None:
    """The Globex day holding ``now``; ``None`` in the weekend gap (Friday 18:00 to Sunday 18:00).

    Pass the host's exchange calendar (``services.calendars.exchange.sessions``)
    for exact ``exchange_session``/``early_close`` flags.
    """
    local = ny_datetime(now)
    d = local.date()
    if local.time() >= TRADING_DAY_START:
        d += _ONE_DAY
    if d.weekday() >= 5:
        return None
    return globex_day(d, calendar)


def next_globex_day(day: GlobexDay | date, calendar: SessionCalendar | None = None) -> GlobexDay:
    """The Globex day after ``day`` (Friday is followed by Monday)."""
    d = day.day if isinstance(day, GlobexDay) else day
    d += _ONE_DAY
    while d.weekday() >= 5:
        d += _ONE_DAY
    return globex_day(d, calendar)


def previous_globex_day(
    day: GlobexDay | date, calendar: SessionCalendar | None = None
) -> GlobexDay:
    """The Globex day before ``day`` (Monday is preceded by Friday)."""
    d = day.day if isinstance(day, GlobexDay) else day
    d -= _ONE_DAY
    while d.weekday() >= 5:
        d -= _ONE_DAY
    return globex_day(d, calendar)


def next_open(now: Instant, calendar: SessionCalendar | None = None) -> GlobexDay:
    """The first Globex day whose ``open_ns`` is after ``now``."""
    local = ny_datetime(now)
    d = local.date()
    if local.time() >= TRADING_DAY_START:
        d += _ONE_DAY
    # ``d`` is the Globex date holding ``now`` (maybe a weekend date): the next
    # day opens at d 18:00 at the earliest.
    candidate = d + _ONE_DAY
    while candidate.weekday() >= 5:
        candidate += _ONE_DAY
    out = globex_day(candidate, calendar)
    assert out.open_ns > now
    return out


def next_open_ns(now: Instant, calendar: SessionCalendar | None = None) -> Instant:
    """``open_ns`` of :func:`next_open`."""
    return next_open(now, calendar).open_ns
