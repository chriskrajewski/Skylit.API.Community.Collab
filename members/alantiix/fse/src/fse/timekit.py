"""Instants, America/New_York session rules and the Decision_Time grid.

Design "Time model" and D2:

- An ``Instant`` is an ``int`` of nanoseconds since the Unix epoch, UTC. No
  naive datetime crosses a module boundary.
- America/New_York appears only here. Every local session time is turned into
  an Instant with ``zoneinfo`` (pinned ``tzdata``), so 09:30 is 13:30 UTC under
  daylight time and 14:30 UTC under standard time (Req 5.8). A local time that
  is ambiguous or does not exist on its date raises instead of guessing.
- ``parse_rfc3339`` and ``from_unix_seconds`` are for the adapters (Skylit
  ``asOf`` and dark-pool ``timestamp``; Atlas ``t``). The engine never parses.
- ``SessionCalendar`` takes holiday and early-close data as input. Reading and
  validating the calendar files is ``fse.calendars`` (task 1.6).

Nothing here reads the clock.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from types import MappingProxyType
from zoneinfo import ZoneInfo

type Instant = int
"""Nanoseconds since 1970-01-01T00:00:00Z."""

NS_PER_US = 1_000
NS_PER_SECOND = 1_000_000_000
NS_PER_MINUTE = 60 * NS_PER_SECOND

NEW_YORK = ZoneInfo("America/New_York")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_SECONDS_PER_DAY = 86_400
_ONE_DAY = timedelta(days=1)

RTH_OPEN = time(9, 30)
RTH_CLOSE = time(16, 0)
TRADING_DAY_START = time(18, 0)
"""The trading day of session ``d`` starts at this time on ``d - 1 day`` (Req 15.14)."""

# ---------------------------------------------------------------- conversions


def _to_instant(dt: datetime) -> Instant:
    """Exact integer conversion of an aware datetime (no float timestamp)."""
    delta = dt - _EPOCH
    return (delta.days * _SECONDS_PER_DAY + delta.seconds) * NS_PER_SECOND + (
        delta.microseconds * NS_PER_US
    )


def ny_instant(d: date, wall: time) -> Instant:
    """The Instant of New York wall time ``wall`` on calendar date ``d``.

    Raises ``ValueError`` if ``wall`` is aware, or if it does not exist (spring
    forward) or occurs twice (fall back) on ``d``.
    """
    if wall.tzinfo is not None:
        raise ValueError(f"wall time {wall.isoformat()} must be naive New York time")
    naive = datetime.combine(d, wall.replace(fold=0))
    first = naive.replace(tzinfo=NEW_YORK)
    second = naive.replace(tzinfo=NEW_YORK, fold=1)
    if first.utcoffset() != second.utcoffset():
        exists = first.astimezone(UTC).astimezone(NEW_YORK).replace(tzinfo=None) == naive
        problem = "is ambiguous" if exists else "does not exist"
        raise ValueError(f"{naive.isoformat()} {problem} in America/New_York")
    return _to_instant(first)


def ny_datetime(t: Instant) -> datetime:
    """``t`` as an aware America/New_York datetime, truncated to the microsecond."""
    return (_EPOCH + timedelta(microseconds=t // NS_PER_US)).astimezone(NEW_YORK)


_RFC3339 = re.compile(
    r"(?P<y>\d{4})-(?P<mo>\d{2})-(?P<d>\d{2})[Tt ]"
    r"(?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})(?:\.(?P<frac>\d+))?"
    r"(?P<tz>[Zz]|(?P<sign>[+-])(?P<oh>\d{2}):(?P<om>\d{2}))",
    re.ASCII,
)


def parse_rfc3339(text: str) -> Instant:
    """Parse an RFC 3339 timestamp with a ``Z`` or ``±HH:MM`` offset, exactly.

    Up to 9 fractional digits are kept to the nanosecond. More digits, a leap
    second (``:60``), an out-of-range field or a missing offset raise
    ``ValueError``; nothing is rounded or assumed.
    """
    m = _RFC3339.fullmatch(text)
    if m is None:
        raise ValueError(f"not an RFC 3339 timestamp with an offset: {text!r}")
    frac = m["frac"] or ""
    if len(frac) > 9:
        raise ValueError(f"more than 9 fractional second digits: {text!r}")
    if m["s"] == "60":
        raise ValueError(f"leap seconds are not supported: {text!r}")
    try:
        y, mo, d, h, mi, s = (int(m[k]) for k in ("y", "mo", "d", "h", "mi", "s"))
        base = datetime(y, mo, d, h, mi, s, tzinfo=UTC)
    except ValueError as exc:
        raise ValueError(f"invalid RFC 3339 timestamp {text!r}: {exc}") from exc
    offset_s = 0
    if m["sign"] is not None:
        oh, om = int(m["oh"]), int(m["om"])
        if oh > 23 or om > 59:
            raise ValueError(f"invalid UTC offset in {text!r}")
        offset_s = (oh * 3600 + om * 60) * (1 if m["sign"] == "+" else -1)
    frac_ns = int(frac.ljust(9, "0")) if frac else 0
    return _to_instant(base) - offset_s * NS_PER_SECOND + frac_ns


def from_unix_seconds(value: int | float) -> Instant:
    """Unix seconds (Atlas UDF ``t``) to an Instant.

    A float is accepted only when it holds a whole number, so no precision is
    lost silently.
    """
    if isinstance(value, bool):
        raise TypeError("Unix seconds must be a number, not a bool")
    if isinstance(value, int):
        return value * NS_PER_SECOND
    if not value.is_integer():
        raise ValueError(f"Unix seconds must be a whole number, got {value!r}")
    return int(value) * NS_PER_SECOND


# ---------------------------------------------------------------- session rules


class OutsideCoverageError(ValueError):
    """A date falls outside the first and last dates the calendar covers."""


class NotASessionError(ValueError):
    """A covered date is a weekend day or an exchange holiday."""


def _check_whole_minutes(name: str, value: int, lo: int, hi: int) -> None:
    if isinstance(value, bool) or not lo <= value <= hi:
        raise ValueError(f"{name} must be an integer from {lo} to {hi} minutes, got {value!r}")


@dataclass(frozen=True, slots=True)
class SessionTimes:
    """Configured session times, as naive New York wall times.

    Defaults: Pull_Window 09:00 to 16:00 (Glossary), Flatten_Time 15:55 with a
    30-minute lead before an early close (Glossary, Req 12.15), Flat_Deadline
    16:10 with a 15-minute offset before an early close (Req 15.1, 15.15).
    """

    pull_start: time = time(9, 0)
    pull_end: time = time(16, 0)
    flatten_time: time = time(15, 55)
    flatten_early_close_lead_min: int = 30
    flat_deadline: time = time(16, 10)
    flat_deadline_early_close_offset_min: int = 15

    def __post_init__(self) -> None:
        for name in ("pull_start", "pull_end", "flatten_time", "flat_deadline"):
            value: time = getattr(self, name)
            if value.tzinfo is not None:
                raise ValueError(f"{name} must be a naive New York wall time")
        if not self.pull_start < self.pull_end:
            raise ValueError("pull_start must be before pull_end")
        if not RTH_OPEN <= self.flatten_time <= RTH_CLOSE:
            raise ValueError("flatten_time must be from 09:30 to 16:00")
        if not RTH_OPEN < self.flat_deadline < TRADING_DAY_START:
            raise ValueError("flat_deadline must be after 09:30 and before 18:00")
        _check_whole_minutes(
            "flatten_early_close_lead_min", self.flatten_early_close_lead_min, 15, 120
        )
        _check_whole_minutes(
            "flat_deadline_early_close_offset_min", self.flat_deadline_early_close_offset_min, 0, 60
        )


def _minus_minutes(wall: time, minutes: int) -> time:
    """Wall-clock subtraction on a fixed date (callers keep the result after 00:00)."""
    return (datetime.combine(date(2000, 1, 3), wall) - timedelta(minutes=minutes)).time()


class SessionCalendar:
    """Session rules for every date in ``[first, last]``.

    A session is a weekday in the covered range that is not a holiday. For a
    session ``d`` (all times America/New_York):

    - RTH open ``d 09:30``; close ``d 16:00``, or the early-close time.
    - Trading day: from ``(d - 1 day) 18:00`` to the Flat_Deadline, both
      inclusive (Req 15.14).
    - Pull_Window: the configured start and end, ending no later than the early
      close on early-close dates.
    - Flatten_Time: the configured time, or the lead before an early close.
    - Flat_Deadline: the configured time, or the offset before an early close.

    Every method that takes a date raises ``OutsideCoverageError`` for a date
    outside the calendar and ``NotASessionError`` for a non-session date.
    """

    __slots__ = ("_early_closes", "_first", "_holidays", "_last", "_times")

    def __init__(
        self,
        first: date,
        last: date,
        holidays: Iterable[date] = (),
        early_closes: Mapping[date, time] | None = None,
        times: SessionTimes | None = None,
    ) -> None:
        if first > last:
            raise ValueError(f"calendar first date {first} is after last date {last}")
        self._first = first
        self._last = last
        self._times = times if times is not None else SessionTimes()
        self._holidays = frozenset(holidays)
        for h in sorted(self._holidays):
            self._require_covered(h, "holiday")
        closes = dict(early_closes or {})
        for d in sorted(closes):
            close = closes[d]
            self._require_covered(d, "early close")
            if not self.is_session(d):
                raise ValueError(f"early close {d} is not a session")
            if close.tzinfo is not None or not RTH_OPEN < close < RTH_CLOSE:
                raise ValueError(f"early close {d} at {close} must be after 09:30, before 16:00")
            if not self._times.pull_start < close:
                raise ValueError(f"early close {d} at {close} is not after the Pull_Window start")
        self._early_closes: Mapping[date, time] = MappingProxyType(closes)

    # ---------------------------------------------------------------- data

    @property
    def first(self) -> date:
        return self._first

    @property
    def last(self) -> date:
        return self._last

    @property
    def holidays(self) -> frozenset[date]:
        return self._holidays

    @property
    def early_closes(self) -> Mapping[date, time]:
        return self._early_closes

    @property
    def times(self) -> SessionTimes:
        return self._times

    def covers(self, d: date) -> bool:
        return self._first <= d <= self._last

    def _require_covered(self, d: date, what: str = "date") -> None:
        if not self.covers(d):
            raise OutsideCoverageError(
                f"{what} {d} is outside the calendar's covered range {self._first} to {self._last}"
            )

    def is_session(self, d: date) -> bool:
        self._require_covered(d)
        return d.weekday() < 5 and d not in self._holidays

    def _require_session(self, d: date) -> None:
        if not self.is_session(d):
            raise NotASessionError(f"{d} is not a session (weekend or exchange holiday)")

    def sessions(self, first: date | None = None, last: date | None = None) -> tuple[date, ...]:
        """Every session in ``[first, last]``, by default the whole calendar."""
        lo = self._first if first is None else first
        hi = self._last if last is None else last
        if lo > hi:
            return ()
        self._require_covered(lo)
        self._require_covered(hi)
        out: list[date] = []
        d = lo
        while d <= hi:
            if self.is_session(d):
                out.append(d)
            d += _ONE_DAY
        return tuple(out)

    def is_early_close(self, d: date) -> bool:
        self._require_session(d)
        return d in self._early_closes

    # ---------------------------------------------------------------- instants

    def _close_wall(self, d: date) -> time:
        return self._early_closes.get(d, RTH_CLOSE)

    def rth_open(self, d: date) -> Instant:
        self._require_session(d)
        return ny_instant(d, RTH_OPEN)

    def rth_close(self, d: date) -> Instant:
        """16:00, or the early-close time on an early-close date."""
        self._require_session(d)
        return ny_instant(d, self._close_wall(d))

    def trading_day_start(self, d: date) -> Instant:
        """18:00 on the calendar day before ``d`` (Sunday for a Monday session)."""
        self._require_session(d)
        return ny_instant(d - _ONE_DAY, TRADING_DAY_START)

    def pull_window(self, d: date) -> tuple[Instant, Instant]:
        """``(start, end)`` of the Pull_Window."""
        self._require_session(d)
        end = min(self._times.pull_end, self._close_wall(d))
        return ny_instant(d, self._times.pull_start), ny_instant(d, end)

    def flatten_time(self, d: date) -> Instant:
        """The strategy's flatten instant (Req 12.15-12.17)."""
        self._require_session(d)
        close = self._early_closes.get(d)
        if close is None:
            return ny_instant(d, self._times.flatten_time)
        return ny_instant(d, _minus_minutes(close, self._times.flatten_early_close_lead_min))

    def flat_deadline(self, d: date) -> Instant:
        """The account's Flat_Deadline (Req 15.15)."""
        self._require_session(d)
        close = self._early_closes.get(d)
        if close is None:
            return ny_instant(d, self._times.flat_deadline)
        offset = self._times.flat_deadline_early_close_offset_min
        return ny_instant(d, _minus_minutes(close, offset))

    def trading_day_of(self, t: Instant) -> date | None:
        """The session whose trading day contains ``t``, else ``None``.

        ``None`` means ``t`` falls between a Flat_Deadline and the next 18:00,
        or before a session that is a weekend day or holiday. Raises
        ``OutsideCoverageError`` when that session is outside the calendar.
        """
        local = ny_datetime(t)
        d = local.date()
        if local.time() >= TRADING_DAY_START:
            d += _ONE_DAY
        if not self.is_session(d):
            return None
        return d if t <= self.flat_deadline(d) else None

    def decision_times(self, d: date, cadence_s: int) -> tuple[Instant, ...]:
        """Backtest Decision_Times: 09:30:00 + k x cadence while before 16:00:00.

        The grid ends at 16:00 on early-close dates too (design "Time model").
        390 entries at the default 60 s cadence (Req 18.1).
        """
        if isinstance(cadence_s, bool) or cadence_s <= 0:
            raise ValueError(
                f"Decision_Cadence must be a positive integer of seconds: {cadence_s!r}"
            )
        self._require_session(d)
        start = ny_instant(d, RTH_OPEN)
        stop = ny_instant(d, RTH_CLOSE)
        return tuple(range(start, stop, cadence_s * NS_PER_SECOND))
