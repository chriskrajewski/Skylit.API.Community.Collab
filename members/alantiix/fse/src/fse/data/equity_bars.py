"""Research-safe QQQ/SPY Atlas bars with strict RTH quality checks.

This module is intentionally separate from the futures puller. It uses the
existing :class:`AtlasBars` transport and parser, but writes only to
``DataCache.equity_bars`` and never to the futures ``bars`` dataset.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Final, Literal

from fse.data.aux_stores import EquityBarStore, validate_equity_bar
from fse.data.bar_source import AtlasBars, FetchedBars
from fse.data.coverage import TimeRange, missing_minute_ranges
from fse.data.spans import ATLAS_DEFAULT_MAX_DAYS, is_weekday, split_date_range
from fse.engine.types import Bar
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, Instant, SessionCalendar, ny_datetime

__all__ = [
    "EQUITY_INTERVAL_S",
    "EQUITY_SYMBOLS",
    "EquityPull",
    "EquitySession",
    "EquitySessionQuality",
    "estimate_equity_requests",
    "fetch_equity_sessions",
    "pull_equity_bars",
]

EQUITY_INTERVAL_S: Final = 60
EQUITY_SYMBOLS: Final = ("QQQ", "SPY")

QualityStatus = Literal["complete", "incomplete", "no_data"]


@dataclass(frozen=True, slots=True)
class EquitySessionQuality:
    """Strict quality facts for one requested RTH session."""

    expected_rows: int
    missing_minutes: tuple[TimeRange, ...]
    duplicate_timestamps: tuple[Instant, ...] = ()
    off_grid_timestamps: tuple[Instant, ...] = ()
    out_of_rth_timestamps: tuple[Instant, ...] = ()
    error: str | None = None

    @property
    def valid(self) -> bool:
        return not (
            self.error
            or self.missing_minutes
            or self.duplicate_timestamps
            or self.off_grid_timestamps
            or self.out_of_rth_timestamps
        )


@dataclass(frozen=True, slots=True)
class EquitySession:
    """Fetched or resumed bars for one exact symbol and exchange session."""

    symbol: str
    session: date
    status: QualityStatus
    bars: tuple[Bar, ...]
    quality: EquitySessionQuality
    request_from_s: int
    request_to_s: int
    error: str | None = None

    @property
    def row_count(self) -> int:
        return len(self.bars)


@dataclass(frozen=True, slots=True)
class EquityPull:
    """All requested symbol/session results, oldest session first."""

    results: tuple[EquitySession, ...]
    history_requests: int

    @property
    def complete(self) -> bool:
        return all(item.status == "complete" for item in self.results)


def _equity_session_runs(
    calendar: SessionCalendar,
    sessions: Iterable[date],
    max_days: int,
) -> tuple[tuple[date, ...], ...]:
    """Partition capped spans so no request crosses an early-close boundary."""
    ordered = tuple(sorted(set(sessions)))
    if not ordered:
        return ()

    runs: list[tuple[date, ...]] = []
    for span in split_date_range(ordered[0], ordered[-1], max_days, counts=is_weekday):
        regular: list[date] = []
        for session in (day for day in ordered if span.contains(day)):
            if calendar.is_early_close(session):
                if regular:
                    runs.append(tuple(regular))
                    regular.clear()
                runs.append((session,))
            else:
                regular.append(session)
        if regular:
            runs.append(tuple(regular))
    return tuple(runs)


def estimate_equity_requests(
    sessions: Iterable[date],
    symbols: Iterable[str],
    *,
    calendar: SessionCalendar | None = None,
    max_days: int = ATLAS_DEFAULT_MAX_DAYS,
) -> dict[str, int]:
    """Return the offline account/config/history/credit estimate for a pull."""
    days = tuple(sorted(set(sessions)))
    names = tuple(symbols)
    if not days:
        spans: tuple[tuple[date, ...], ...] = ()
    elif calendar is None:
        spans = tuple(
            tuple(day for day in days if span.contains(day))
            for span in split_date_range(days[0], days[-1], max_days, counts=is_weekday)
        )
    else:
        spans = _equity_session_runs(calendar, days, max_days)
    history = len(spans) * len(names)
    return {
        "account_requests": 1,
        "config_requests": 1,
        "history_requests": history,
        "credits": history,
        "sessions": len(days),
        "symbols": len(names),
        "expected_rth_rows": sum(
            390 for _ in days
        ),  # callers replace this with calendar-aware totals
    }


def _expected_rows(calendar: SessionCalendar, session: date) -> int:
    return (calendar.rth_close(session) - calendar.rth_open(session)) // NS_PER_MINUTE


def _timestamp_text(timestamp: Instant) -> str:
    return ny_datetime(timestamp).isoformat()


def _diagnostics(
    got: FetchedBars,
    *,
    session: date,
    calendar: SessionCalendar,
    error: str | None = None,
) -> EquitySessionQuality:
    start, end = calendar.rth_open(session), calendar.rth_close(session)
    expected = _expected_rows(calendar, session)
    received = got.received_opens
    duplicate = tuple(sorted({t for t in received if received.count(t) > 1}))
    off_grid = tuple(sorted({t for t in received if t % (EQUITY_INTERVAL_S * NS_PER_SECOND)}))
    out_of_rth = tuple(sorted({t for t in received if t < start or t >= end}))
    missing = missing_minute_ranges(
        ((bar.open_ns, bar.close_ns) for bar in got.bars), start, end
    )
    return EquitySessionQuality(
        expected_rows=expected,
        missing_minutes=missing,
        duplicate_timestamps=duplicate,
        off_grid_timestamps=off_grid,
        out_of_rth_timestamps=out_of_rth,
        error=error,
    )


def _session_from_fetch(
    symbol: str,
    session: date,
    got: FetchedBars,
    calendar: SessionCalendar,
) -> EquitySession:
    start, end = calendar.rth_open(session), calendar.rth_close(session)
    quality = _diagnostics(got, session=session, calendar=calendar, error=got.error_code)
    if got.error_code is None and not got.bars:
        status: QualityStatus = "no_data"
    elif got.error_code is None and quality.valid and len(got.bars) == quality.expected_rows:
        status = "complete"
    else:
        status = "incomplete"
    return EquitySession(
        symbol=symbol,
        session=session,
        status=status,
        bars=got.bars,
        quality=quality,
        request_from_s=start // NS_PER_SECOND,
        request_to_s=end // NS_PER_SECOND,
        error=got.error_code,
    )


async def fetch_equity_sessions(
    atlas: AtlasBars,
    calendar: SessionCalendar,
    symbol: str,
    sessions: Iterable[date],
) -> tuple[EquitySession, ...]:
    """Fetch exact QQQ/SPY sessions through the existing strict Atlas path."""
    if symbol not in EQUITY_SYMBOLS:
        raise ValueError(f"symbol must be one of {EQUITY_SYMBOLS}, got {symbol!r}")
    days = tuple(sorted(set(sessions)))
    if not days:
        return ()
    cap = await atlas.max_days("1")
    fetched: dict[date, FetchedBars] = {}
    for run in _equity_session_runs(calendar, days, cap):
        fetched.update(
            await atlas.fetch_sessions(
                symbol,
                run,
                instrument=symbol,
                contract=symbol,
                session_range=lambda d: (calendar.rth_open(d), calendar.rth_close(d)),
                interval_s=EQUITY_INTERVAL_S,
                extended=False,
                futures=False,
                strict=True,
            )
        )
    return tuple(_session_from_fetch(symbol, d, fetched[d], calendar) for d in days)


def _stored_complete(
    store: EquityBarStore, symbol: str, session: date, calendar: SessionCalendar
) -> EquitySession | None:
    if store.status(symbol, EQUITY_INTERVAL_S, session) != "complete":
        return None
    try:
        bars = tuple(store.read_session(symbol, EQUITY_INTERVAL_S, session))
        for index, bar in enumerate(bars):
            validate_equity_bar(bar, label=f"stored {symbol} {session} row {index}")
    except Exception:
        return None
    expected = _expected_rows(calendar, session)
    start, end = calendar.rth_open(session), calendar.rth_close(session)
    exact = tuple(start + i * EQUITY_INTERVAL_S * NS_PER_SECOND for i in range(expected))
    if len(bars) != expected or tuple(bar.open_ns for bar in bars) != exact:
        return None
    got = FetchedBars(bars, received_opens=exact)
    quality = _diagnostics(got, session=session, calendar=calendar)
    if not quality.valid:
        return None
    return EquitySession(
        symbol,
        session,
        "complete",
        bars,
        quality,
        start // NS_PER_SECOND,
        end // NS_PER_SECOND,
    )


def _pending_session_runs(
    calendar: SessionCalendar, sessions: Iterable[date]
) -> tuple[tuple[date, ...], ...]:
    """Group pending dates so strict Atlas spans never cross cached sessions."""
    ordered = tuple(sorted(set(sessions)))
    runs: list[list[date]] = []
    for session in ordered:
        if not runs or calendar.sessions(runs[-1][-1], session) != (runs[-1][-1], session):
            runs.append([session])
        else:
            runs[-1].append(session)
    return tuple(tuple(run) for run in runs)


async def pull_equity_bars(
    atlas: AtlasBars,
    store: EquityBarStore,
    calendar: SessionCalendar,
    sessions: Iterable[date],
    symbols: Iterable[str],
    *,
    partial_results: list[EquitySession] | None = None,
) -> EquityPull:
    """Resume complete verified sessions and store only complete Atlas answers.

    ``partial_results`` is populated after each successful session lifecycle
    step. The command uses it to preserve audit entries when a later client,
    fetch, or storage operation raises before this function can return.
    """
    days = tuple(sorted(set(sessions)))
    names = tuple(symbols)
    if not names or any(name not in EQUITY_SYMBOLS for name in names):
        raise ValueError(f"symbols must be a non-empty subset of {EQUITY_SYMBOLS}")
    if len(set(names)) != len(names):
        raise ValueError("symbols must not contain duplicates")
    completed_results: list[EquitySession] = []

    def record(result: EquitySession) -> None:
        completed_results.append(result)
        if partial_results is not None:
            partial_results.append(result)

    requests = 0
    work: list[tuple[date, str, tuple[date, ...]]] = []
    resumed_by_key: dict[tuple[str, date], EquitySession] = {}
    for symbol in names:
        pending: list[date] = []
        for session in days:
            existing = _stored_complete(store, symbol, session, calendar)
            if existing is None:
                if store.status(symbol, EQUITY_INTERVAL_S, session) == "complete":
                    # The catalog entry is no longer trusted after validation failed.
                    store.mark_incomplete(symbol, EQUITY_INTERVAL_S, session)
                pending.append(session)
            else:
                resumed_by_key[(symbol, session)] = existing
        for run in _pending_session_runs(calendar, pending):
            work.append((run[0], symbol, run))

    for result in resumed_by_key.values():
        # A verified complete session is already durable; do not rewrite it.
        record(result)

    # Fetch the oldest pending session across symbols first. Each work item is a
    # contiguous exchange-session run, so strict Atlas validation sees no cached gap.
    work.sort(key=lambda item: (item[0], item[1]))
    for _oldest, symbol, run in work:
        before_requests = atlas.requests
        fetched = await fetch_equity_sessions(atlas, calendar, symbol, run)
        requests += atlas.requests - before_requests
        fetched_by_day = {item.session: item for item in fetched}
        for session in run:
            result = fetched_by_day[session]
            current = store.status(symbol, EQUITY_INTERVAL_S, session)
            if result.status == "complete":
                store.write_session(symbol, EQUITY_INTERVAL_S, session, result.bars)
            elif result.status == "no_data":
                store.write_session(symbol, EQUITY_INTERVAL_S, session, ())
            elif current != "complete":
                # Do not downgrade a valid existing file when a replacement is bad.
                store.mark_incomplete(symbol, EQUITY_INTERVAL_S, session)
            record(result)

    ordered_results = tuple(
        sorted(completed_results, key=lambda item: (item.session, item.symbol))
    )
    return EquityPull(ordered_results, requests)


def manifest_timestamp_values(values: Iterable[Instant]) -> list[str]:
    """Stable human-readable timestamps used by the JSON/Markdown manifest."""
    return [_timestamp_text(value) for value in sorted(set(values))]
