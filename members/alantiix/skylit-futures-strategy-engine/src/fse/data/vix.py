"""VIX index data: daily open, prior-session close, intraday bars and gaps (Req 4.9-4.11, OQ2).

Design §4 and "Time model":

- **Daily open**: the open of the 1-minute VIX bar that opens at the RTH open
  (09:30), known at 09:31, the close of that first interval (Req 4.9).
- **Close**: the close of the 1-minute bar that closes at the RTH close (16:00,
  or the early close), known at that instant. Session ``d``'s prior close is
  the close of the most recent session before ``d`` (the Friday close for a
  Monday). A missing bar leaves the value missing; nothing is taken from a
  neighbouring bar.
- **Intraday bars** (Req 4.10): the session's RTH 1-minute bars, stored in
  ``vix/bars/`` when an intraday VIX source is configured.
- **Gaps** (Req 4.11): per session, each missing item (``daily_open``,
  ``prior_close``, ``intraday_bars``), as :class:`~fse.data.coverage.VixGap`.

VIX bars come from Atlas ``GET /v1/history`` with ``extended=false`` (RTH
only), in spans of at most ``max_days`` weekdays, through
:class:`~fse.data.bar_source.AtlasBars`. The Atlas symbol defaults to
``VIX``; when Atlas does not serve it, ``fse import-vix`` loads an Operator
CSV instead (OQ2).

Stored daily values are only improved, never degraded: a fetched record
replaces a stored one only when it holds every value the stored one holds,
and an Operator import is replaced only when the fetch adds a value it lacks.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

from fse.data.aux_stores import VixDailyRecord, VixDailySource, VixStore
from fse.data.bar_source import AtlasBars
from fse.data.coverage import TimeRange, VixGap, VixItem, missing_minute_ranges
from fse.engine.types import Bar
from fse.timekit import NS_PER_SECOND, Instant, SessionCalendar

__all__ = [
    "DEFAULT_VIX_SYMBOL",
    "VIX_CONTRACT",
    "VIX_INSTRUMENT",
    "VIX_INTERVAL_S",
    "VixPull",
    "VixSession",
    "daily_record",
    "fetch_vix",
    "open_observed_at",
    "prior_session",
    "pull_vix",
    "replaces",
    "vix_gaps",
    "vix_session_range",
]

VIX_INSTRUMENT: Final = "VIX"
"""The cache's instrument name for VIX bars (``CoverageRequest.vix_instrument``)."""
VIX_CONTRACT: Final = "VIX"
"""The ``Bar.contract`` label of an index bar."""
DEFAULT_VIX_SYMBOL: Final = "VIX"
"""The Atlas symbol asked for VIX bars."""
VIX_INTERVAL_S: Final = 60

_ONE_DAY: Final = timedelta(days=1)


def vix_session_range(calendar: SessionCalendar, session: date) -> tuple[Instant, Instant]:
    """``[RTH open, RTH close)`` of ``session``: the VIX bars' range."""
    return calendar.rth_open(session), calendar.rth_close(session)


def open_observed_at(calendar: SessionCalendar, session: date) -> Instant:
    """09:31 of ``session``: the close of its first 1-minute interval (Req 4.9)."""
    return calendar.rth_open(session) + VIX_INTERVAL_S * NS_PER_SECOND


def prior_session(calendar: SessionCalendar, session: date) -> date | None:
    """The most recent session before ``session``, or ``None`` outside the calendar."""
    d = session - _ONE_DAY
    while calendar.covers(d):
        if calendar.is_session(d):
            return d
        d -= _ONE_DAY
    return None


def daily_record(
    calendar: SessionCalendar,
    session: date,
    bars: Iterable[Bar],
    *,
    source: VixDailySource = "atlas",
) -> VixDailyRecord:
    """The session's daily open and close from its 1-minute RTH bars.

    The open is the bar opening at the RTH open, the close the bar closing at
    the RTH close; either is ``None`` when that bar is missing.
    """
    rth_open, rth_close = vix_session_range(calendar, session)
    first = last = None
    for bar in bars:
        if bar.interval_s != VIX_INTERVAL_S:
            raise ValueError(f"VIX bars must be {VIX_INTERVAL_S} s bars, got {bar.interval_s} s")
        if bar.open_ns == rth_open:
            first = bar
        if bar.close_ns == rth_close:
            last = bar
    return VixDailyRecord(
        session=session,
        open=None if first is None else first.o,
        open_at_ns=None if first is None else open_observed_at(calendar, session),
        close=None if last is None else last.c,
        close_at_ns=None if last is None else rth_close,
        source=source,
    )


def replaces(new: VixDailyRecord, old: VixDailyRecord | None) -> bool:
    """True when ``new`` should replace the stored ``old`` record of its session.

    ``new`` must hold at least one value and every value ``old`` holds. An
    Operator import (``old.source == "import"``) is replaced only when
    ``new`` adds a value it lacks.
    """
    if new.open is None and new.close is None:
        return False
    if old is None:
        return True
    if (old.open is not None and new.open is None) or (old.close is not None and new.close is None):
        return False
    if old.source == "import" and new.source != "import":
        return (old.open is None and new.open is not None) or (
            old.close is None and new.close is not None
        )
    return new != old


def vix_gaps(
    calendar: SessionCalendar,
    sessions: Iterable[date],
    daily: Mapping[date, VixDailyRecord],
    *,
    intraday: Mapping[date, Sequence[Bar]] | None = None,
) -> tuple[VixGap, ...]:
    """Each session's missing VIX items (Req 4.11), oldest first.

    ``intraday`` (when an intraday source is configured) maps a session to
    its stored 1-minute bars; a session absent from it misses its whole RTH.
    """
    gaps: list[VixGap] = []
    for session in sorted(set(sessions)):
        items: list[VixItem] = []
        today = daily.get(session)
        if today is None or today.open is None:
            items.append("daily_open")
        prior = prior_session(calendar, session)
        before = None if prior is None else daily.get(prior)
        if before is None or before.close is None:
            items.append("prior_close")
        missing: tuple[TimeRange, ...] = ()
        if intraday is not None:
            start, end = vix_session_range(calendar, session)
            bars = intraday.get(session)
            missing = (
                (TimeRange(start, end),)
                if bars is None
                else missing_minute_ranges(((b.open_ns, b.close_ns) for b in bars), start, end)
            )
            if missing:
                items.append("intraday_bars")
        if items:
            gaps.append(VixGap(session, tuple(items), missing))
    return tuple(gaps)


# ---------------------------------------------------------------- fetching


@dataclass(frozen=True, slots=True)
class VixSession:
    """One session's fetched VIX bars; ``record`` is ``None`` when the request failed."""

    session: date
    bars: tuple[Bar, ...]
    error_code: str | None
    record: VixDailyRecord | None


async def fetch_vix(
    atlas: AtlasBars,
    calendar: SessionCalendar,
    sessions: Iterable[date],
    *,
    symbol: str = DEFAULT_VIX_SYMBOL,
) -> tuple[VixSession, ...]:
    """Each session's RTH 1-minute VIX bars and daily record, oldest first."""
    days = sorted(set(sessions))
    if not days:
        return ()
    fetched = await atlas.fetch_sessions(
        symbol,
        days,
        instrument=VIX_INSTRUMENT,
        contract=VIX_CONTRACT,
        session_range=lambda d: vix_session_range(calendar, d),
        interval_s=VIX_INTERVAL_S,
        extended=False,
        futures=False,
    )
    out: list[VixSession] = []
    for d in days:
        got = fetched[d]
        record = None if got.error_code is not None else daily_record(calendar, d, got.bars)
        out.append(VixSession(d, got.bars, got.error_code, record))
    return tuple(out)


@dataclass(frozen=True, slots=True)
class VixPull:
    """What :func:`pull_vix` fetched, stored and still misses."""

    fetched: tuple[VixSession, ...]
    written: tuple[date, ...]  # sessions whose daily record was written
    gaps: tuple[VixGap, ...]  # of the requested sessions, after the write


async def pull_vix(
    atlas: AtlasBars,
    store: VixStore,
    calendar: SessionCalendar,
    sessions: Iterable[date],
    *,
    intraday: bool = False,
    symbol: str = DEFAULT_VIX_SYMBOL,
) -> VixPull:
    """Fetch and store the VIX daily values (and intraday bars) of ``sessions``.

    The prior session of each requested session is fetched too when its
    close is not stored, so the prior close is available (Req 4.9). With
    ``intraday``, each requested session's bars are marked ``incomplete``
    before the first request and stored ``complete`` or ``no_data`` after a
    usable answer; a failed session stays ``incomplete``. A
    :class:`~fse.data.cache_io.CacheWriteError` propagates (exit 4).
    """
    days = sorted(set(sessions))
    stored = store.read_daily()
    needed = set(days)
    for d in days:
        prior = prior_session(calendar, d)
        if prior is not None and (prior not in stored or stored[prior].close is None):
            needed.add(prior)
    if intraday:
        for d in days:
            store.bars.mark_incomplete(
                VIX_INSTRUMENT, VIX_INTERVAL_S, d, contract=VIX_CONTRACT, source="atlas"
            )
    fetched = await fetch_vix(atlas, calendar, needed, symbol=symbol)
    requested = set(days)
    if intraday:
        for s in fetched:
            if s.session in requested and s.error_code is None:
                store.bars.write_session(
                    VIX_INSTRUMENT,
                    VIX_INTERVAL_S,
                    s.session,
                    s.bars,
                    contract=VIX_CONTRACT,
                    source="atlas",
                )
    updates = [
        s.record
        for s in fetched
        if s.record is not None and replaces(s.record, stored.get(s.session))
    ]
    if updates:
        store.write_daily(updates)
    merged = {**stored, **{r.session: r for r in updates}}
    bars = {s.session: s.bars for s in fetched if s.error_code is None} if intraday else None
    return VixPull(
        fetched=fetched,
        written=tuple(r.session for r in updates),
        gaps=vix_gaps(calendar, days, merged, intraday=bars),
    )
