"""The pull coverage report: ``coverage_{pull_id}.md`` and ``.json`` (design §3-4).

Requirements 3.11, 4.2, 4.6, 4.8, 4.11 and 4.13. A pull wraps its work in
:func:`coverage_report`, which writes both files from a ``finally`` block, so a
pull that completes, stops on an error or is interrupted by the Operator still
gets its report (exit 130 comes after the report)::

    with coverage_report(request, cache=cache, calendar=sessions, writer=writer,
                         out_dir=run_dir, roll=roll) as coverage:
        ...  # the puller, Bar_Source and dark-pool fetch call coverage.record_*()

Two sources feed the report:

- **The Data_Cache is the record of what is stored.** Catalog windows and
  ``bars_coverage`` rows, stored bar files, VIX daily values, and dark-pool
  fetched dates and prints. Sessions served from the cache, fetched in this
  pull, or never reached because the pull stopped are all reported the same way.
- **The** :class:`CoverageCollector` **holds what only the running pull knows.**
  ``meta.resolution`` of fetched responses, ``no_data`` samples inside a
  window, the Atlas probe result, each bar fetch's contract, source and last
  error code, off-grid prices, dark-pool request errors, and how the pull ended.

Report contents:

- Per symbol and metric (Req 3.11): sessions requested, sessions with at least
  one stored Snapshot, sessions skipped as dated before the symbol's first
  history date, ``meta.resolution`` per session, ``no_data`` gaps with start
  and end, and Cache_Windows left incomplete (``incomplete`` in the catalog, or
  ``absent`` when no request for the window started).
- Per futures instrument: the Atlas probe (Req 4.2), the contract and source per
  session (Req 4.6), and missing RTH minute ranges (09:30 to 16:00 or the early
  close) with their cause (Req 4.8).
- VIX: per session, each missing item (daily open, prior-session close, and
  1-minute RTH bars where intraday VIX is configured) (Req 4.11).
- Dark pool: per ticker and session, no prints or the last error code (Req 4.13).

For a session whose windows all came from the cache, ``meta.resolution`` is
read from the ``resolution`` column of its first stored window. Anything the
report cannot read is listed under ``problems`` and the rest is still written.

Both files go through the Log_Writer (redacted, temp file, fsync, rename). The
JSON is canonical, so equal inputs give equal bytes. The output directory goes
through the path guard (Req 1.11) before the pull starts and again before the
write.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Final, Literal, cast

import pyarrow as pa
import pyarrow.parquet as pq

from fse.calendars import RollCalendar
from fse.data.aux_stores import VixDailyRecord, trade_date_of
from fse.data.cache import (
    CacheWindowKey,
    DataCache,
    HeatmapView,
    cache_window_end,
    cache_window_starts,
)
from fse.data.cache_io import CacheError, check_component
from fse.data.catalog import BarsCoverageRecord, WindowRecord, WindowStatus
from fse.data.path_guard import check_output_dir
from fse.engine.types import BAR_SOURCES, METRICS, RESOLUTIONS, BarSourceName, Metric
from fse.logio import LogWriter
from fse.logio.canonical_json import JsonValue, instant_fields, ny_iso, to_jsonable
from fse.timekit import NS_PER_MINUTE, Instant, SessionCalendar, ny_datetime

__all__ = [
    "BAR_GAP_CAUSES",
    "COVERAGE_FORMAT",
    "DARK_POOL_GAP_CAUSES",
    "EXIT_INTERRUPTED",
    "OUTPUT_DIR_LABEL",
    "PULL_OUTCOMES",
    "VIX_ITEMS",
    "AtlasProbe",
    "BarFetch",
    "BarGapCause",
    "CoverageCollector",
    "CoverageFiles",
    "CoverageReport",
    "CoverageRequest",
    "DarkPoolCoverage",
    "DarkPoolGap",
    "DarkPoolGapCause",
    "FuturesCoverage",
    "FuturesSessionCoverage",
    "HeatmapCoverage",
    "HeatmapSessionCoverage",
    "IncompleteWindow",
    "MissingMinutes",
    "OffGridPrice",
    "PullOutcome",
    "TimeRange",
    "VixCoverage",
    "VixGap",
    "VixItem",
    "WindowCounts",
    "build_coverage_report",
    "coverage_paths",
    "coverage_report",
    "merge_ranges",
    "missing_minute_ranges",
    "render_markdown",
    "write_coverage_files",
]

COVERAGE_FORMAT: Final = "fse.coverage/1"
OUTPUT_DIR_LABEL: Final = "coverage report directory"

# Design "Exit codes": 130 = Operator interrupt, after the coverage report is written.
EXIT_INTERRUPTED: Final = 130

type PullOutcome = Literal["completed", "interrupted", "failed"]
PULL_OUTCOMES: frozenset[str] = frozenset({"completed", "interrupted", "failed"})

type BarGapCause = Literal["no_contract", "no_bars", "error", "not_fetched", "unreadable"]
"""Why RTH minutes have no bar: no contract in the roll calendar, no bars
returned, the last error code of a failed request (Req 4.8), the pull ended
before the session's bars were stored, or the stored file could not be read."""
BAR_GAP_CAUSES: frozenset[str] = frozenset(
    {"no_contract", "no_bars", "error", "not_fetched", "unreadable"}
)

type DarkPoolGapCause = Literal["no_prints", "error", "not_fetched", "unreadable"]
DARK_POOL_GAP_CAUSES: frozenset[str] = frozenset(
    {"no_prints", "error", "not_fetched", "unreadable"}
)

type VixItem = Literal["daily_open", "prior_close", "intraday_bars"]
VIX_ITEMS: Final[tuple[VixItem, ...]] = ("daily_open", "prior_close", "intraday_bars")

type PriceColumn = Literal["o", "h", "l", "c"]
_PRICE_COLUMNS: Final = frozenset({"o", "h", "l", "c"})

_VIX_INTERVAL_S: Final = 60
_ONE_DAY: Final = timedelta(days=1)

# What a cache read can raise. Each failure becomes a ``problems`` entry, so one
# unreadable file never stops the report from being written.
_READ_ERRORS: Final[tuple[type[Exception], ...]] = (
    CacheError,
    OSError,
    ValueError,
    sqlite3.Error,
    pa.ArrowException,
)


# ---------------------------------------------------------------- validation helpers


def _check_date(label: str, value: object) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError(f"{label} must be a date, got {value!r}")
    return value


def _check_instant(label: str, value: object) -> Instant:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an integer Instant, got {value!r}")
    return value


def _check_code(label: str, value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be None or a non-blank string, got {value!r}")
    return value


def _names(label: str, value: Iterable[str], *, allow_empty: bool = True) -> tuple[str, ...]:
    if isinstance(value, str):
        raise ValueError(f"{label} must be a sequence of names, not one string: {value!r}")
    items = tuple(value)
    if not items and not allow_empty:
        raise ValueError(f"{label} must name at least one entry")
    for item in items:
        check_component(f"{label} entry", item)
    if len(set(items)) != len(items):
        raise ValueError(f"{label} must not repeat an entry: {list(items)}")
    return items


def _describe(exc: BaseException) -> str:
    text = str(exc)
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


# ---------------------------------------------------------------- the request


@dataclass(frozen=True, slots=True)
class CoverageRequest:
    """What the pull was asked for: the frame every report section is built on.

    ``sessions`` are the requested exchange sessions in ascending order, all in
    ``[first, last]``. ``first_history`` maps a symbol to its first history date
    from ``GET /v1/symbols``; a symbol absent from it has no skipped sessions.
    ``dark_pool_tickers`` is empty when the pull does not fetch dark-pool prints.
    """

    pull_id: str
    started_at: Instant
    first: date
    last: date
    sessions: tuple[date, ...]
    symbols: tuple[str, ...]
    view: HeatmapView
    metrics: tuple[Metric, ...] = ("gamma", "vanna")
    first_history: Mapping[str, date] = field(default_factory=dict)
    instruments: tuple[str, ...] = ("ES", "NQ")
    bar_interval_s: int = 60
    vix_instrument: str = "VIX"
    vix_intraday: bool = False
    dark_pool_tickers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        check_component("pull_id", self.pull_id)
        _check_instant("started_at", self.started_at)
        _check_date("first", self.first)
        _check_date("last", self.last)
        if self.first > self.last:
            raise ValueError(f"first {self.first} is after last {self.last}")
        sessions = tuple(self.sessions)
        previous: date | None = None
        for d in sessions:
            _check_date("sessions entry", d)
            if not self.first <= d <= self.last:
                raise ValueError(f"session {d} is outside {self.first} to {self.last}")
            if previous is not None and d <= previous:
                raise ValueError("sessions must be in strictly ascending order")
            previous = d
        symbols = _names("symbols", self.symbols, allow_empty=False)
        metrics = tuple(self.metrics)
        if not metrics or len(set(metrics)) != len(metrics):
            raise ValueError(f"metrics must name each metric at most once: {list(metrics)}")
        for metric in metrics:
            if metric not in METRICS:
                raise ValueError(f"metric must be one of {sorted(METRICS)}, got {metric!r}")
        if not isinstance(self.view, HeatmapView):
            raise ValueError(f"view must be a HeatmapView, got {type(self.view).__name__}")
        history = dict(self.first_history)
        for symbol, first_date in history.items():
            if symbol not in symbols:
                raise ValueError(f"first_history names {symbol!r}, which is not a symbol")
            _check_date(f"first_history[{symbol!r}]", first_date)
        instruments = _names("instruments", self.instruments)
        if (
            not isinstance(self.bar_interval_s, int)
            or isinstance(self.bar_interval_s, bool)
            or self.bar_interval_s <= 0
        ):
            raise ValueError(f"bar_interval_s must be a positive integer: {self.bar_interval_s!r}")
        check_component("vix_instrument", self.vix_instrument)
        if not isinstance(self.vix_intraday, bool):
            raise ValueError(f"vix_intraday must be a bool, got {self.vix_intraday!r}")
        tickers = _names("dark_pool_tickers", self.dark_pool_tickers)
        object.__setattr__(self, "sessions", sessions)
        object.__setattr__(self, "symbols", symbols)
        object.__setattr__(self, "metrics", metrics)
        object.__setattr__(self, "first_history", MappingProxyType(history))
        object.__setattr__(self, "instruments", instruments)
        object.__setattr__(self, "dark_pool_tickers", tickers)

    def skipped(self, symbol: str, session: date) -> bool:
        """True when ``session`` is dated before ``symbol``'s first history date (Req 3.5)."""
        first = self.first_history.get(symbol)
        return first is not None and session < first


# ---------------------------------------------------------------- ranges


@dataclass(frozen=True, slots=True, order=True)
class TimeRange:
    """``[start_ns, end_ns)``, with ``start_ns < end_ns``."""

    start_ns: Instant
    end_ns: Instant

    def __post_init__(self) -> None:
        _check_instant("start_ns", self.start_ns)
        _check_instant("end_ns", self.end_ns)
        if self.start_ns >= self.end_ns:
            raise ValueError(f"a range must start before it ends: {self.start_ns}, {self.end_ns}")

    @property
    def minutes(self) -> int:
        """Whole minutes in the range."""
        return (self.end_ns - self.start_ns) // NS_PER_MINUTE

    def to_json(self) -> dict[str, JsonValue]:
        return {**instant_fields("start", self.start_ns), **instant_fields("end", self.end_ns)}


def merge_ranges(ranges: Iterable[TimeRange]) -> tuple[TimeRange, ...]:
    """The union of ``ranges``, sorted, with overlapping and touching ranges joined."""
    out: list[TimeRange] = []
    for r in sorted(ranges):
        if out and r.start_ns <= out[-1].end_ns:
            if r.end_ns > out[-1].end_ns:
                out[-1] = TimeRange(out[-1].start_ns, r.end_ns)
        else:
            out.append(r)
    return tuple(out)


def missing_minute_ranges(
    bars: Iterable[tuple[Instant, Instant]], start_ns: Instant, end_ns: Instant
) -> tuple[TimeRange, ...]:
    """Minute ranges of ``[start_ns, end_ns)`` that no bar ``[open, close)`` overlaps.

    Minutes are counted from ``start_ns`` (09:30 for RTH). A minute counts as
    covered when any bar overlaps it, so 5 s bars cover their minute too.
    Consecutive missing minutes form one range. Nothing is synthesized (Req 4.8).
    """
    if start_ns >= end_ns:
        return ()
    count = -((start_ns - end_ns) // NS_PER_MINUTE)  # ceil
    covered = bytearray(count)
    for open_ns, close_ns in bars:
        if close_ns <= start_ns or open_ns >= end_ns:
            continue
        lo = max(0, (open_ns - start_ns) // NS_PER_MINUTE)
        hi = min(count, -((start_ns - close_ns) // NS_PER_MINUTE))
        covered[lo:hi] = b"\x01" * (hi - lo)
    out: list[TimeRange] = []
    i = 0
    while i < count:
        if covered[i]:
            i += 1
            continue
        j = i
        while j < count and not covered[j]:
            j += 1
        out.append(
            TimeRange(start_ns + i * NS_PER_MINUTE, min(end_ns, start_ns + j * NS_PER_MINUTE))
        )
        i = j
    return tuple(out)


# ---------------------------------------------------------------- collector records


@dataclass(frozen=True, slots=True)
class AtlasProbe:
    """Whether Atlas ``/v1/history`` serves an instrument (Req 4.2).

    Served when the 1-minute requests for the first and last sessions of the
    range each returned at least one bar; ``error_code`` is the last error code
    when a probe request failed.
    """

    instrument: str
    served: bool
    error_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.served, bool):
            raise ValueError(f"served must be a bool, got {self.served!r}")
        _check_code("error_code", self.error_code)

    def to_json(self) -> dict[str, JsonValue]:
        return {"served": self.served, "error_code": self.error_code}


@dataclass(frozen=True, slots=True)
class BarFetch:
    """One session's bar fetch: ``contract`` is ``None`` when the roll calendar has none."""

    instrument: str
    session: date
    contract: str | None
    source: BarSourceName | None
    error_code: str | None = None

    def __post_init__(self) -> None:
        _check_code("contract", self.contract)
        if self.source is not None and self.source not in BAR_SOURCES:
            raise ValueError(f"source must be one of {sorted(BAR_SOURCES)}, got {self.source!r}")
        _check_code("error_code", self.error_code)


@dataclass(frozen=True, slots=True)
class OffGridPrice:
    """A received futures price that is not a whole tick (design §4 "Normalization")."""

    open_ns: Instant
    column: PriceColumn
    price: float

    def __post_init__(self) -> None:
        _check_instant("open_ns", self.open_ns)
        if self.column not in _PRICE_COLUMNS:
            raise ValueError(f"column must be one of {sorted(_PRICE_COLUMNS)}: {self.column!r}")

    def to_json(self) -> dict[str, JsonValue]:
        return {**instant_fields("open", self.open_ns), "column": self.column, "price": self.price}


class CoverageCollector:
    """What only the running pull knows, recorded as it happens.

    Every ``record_*`` call checks that its symbol, metric, instrument, ticker
    and session belong to the request, and raises ``ValueError`` otherwise.
    Each call makes one container update, so an interrupt between calls leaves
    a consistent state for the ``finally`` block. Use it from the pull's event
    loop thread; it holds no lock.
    """

    __slots__ = (
        "_bar_fetches",
        "_dark_pool_errors",
        "_no_data",
        "_off_grid",
        "_probes",
        "_request",
        "_resolutions",
        "_sessions",
        "_stopped",
    )

    def __init__(self, request: CoverageRequest) -> None:
        if not isinstance(request, CoverageRequest):
            raise ValueError(f"request must be a CoverageRequest, got {type(request).__name__}")
        self._request = request
        self._sessions = frozenset(request.sessions)
        self._resolutions: dict[tuple[str, str, date], set[str]] = {}
        self._no_data: dict[tuple[str, str, date], list[TimeRange]] = {}
        self._probes: dict[str, AtlasProbe] = {}
        self._bar_fetches: dict[tuple[str, date], BarFetch] = {}
        self._off_grid: dict[tuple[str, date], list[OffGridPrice]] = {}
        self._dark_pool_errors: dict[tuple[str, date], str] = {}
        self._stopped: tuple[PullOutcome, str | None] | None = None

    @property
    def request(self) -> CoverageRequest:
        return self._request

    # ---------------------------------------------------------------- checks

    def _session(self, session: date) -> date:
        _check_date("session", session)
        if session not in self._sessions:
            raise ValueError(f"session {session} is not a requested session of this pull")
        return session

    def _heatmap_key(self, symbol: str, metric: str, session: date) -> tuple[str, str, date]:
        if symbol not in self._request.symbols:
            raise ValueError(f"symbol {symbol!r} is not a requested symbol")
        if metric not in self._request.metrics:
            raise ValueError(f"metric {metric!r} is not a requested metric")
        return (symbol, metric, self._session(session))

    def _instrument(self, instrument: str) -> str:
        if instrument not in self._request.instruments:
            raise ValueError(f"instrument {instrument!r} is not a requested instrument")
        return instrument

    # ---------------------------------------------------------------- heatmaps

    def record_resolution(self, symbol: str, metric: str, session: date, resolution: str) -> None:
        """Note the ``meta.resolution`` of a response for this symbol, metric and session."""
        key = self._heatmap_key(symbol, metric, session)
        if resolution not in RESOLUTIONS:
            raise ValueError(f"resolution must be one of {sorted(RESOLUTIONS)}: {resolution!r}")
        self._resolutions.setdefault(key, set()).add(resolution)

    def record_no_data(
        self, symbol: str, metric: str, session: date, start_ns: Instant, end_ns: Instant
    ) -> None:
        """Note ``[start_ns, end_ns)`` as ``no_data``, such as one ``/v1/historical`` sample.

        Whole ``no_data`` windows come from the catalog; this adds the gaps
        inside windows that also returned Snapshots.
        """
        key = self._heatmap_key(symbol, metric, session)
        self._no_data.setdefault(key, []).append(TimeRange(start_ns, end_ns))

    def resolutions(self, symbol: str, metric: str, session: date) -> tuple[str, ...]:
        return tuple(sorted(self._resolutions.get((symbol, metric, session), ())))

    def no_data_ranges(self, symbol: str, metric: str, session: date) -> tuple[TimeRange, ...]:
        return tuple(self._no_data.get((symbol, metric, session), ()))

    # ---------------------------------------------------------------- futures

    def record_atlas_probe(
        self, instrument: str, *, served: bool, error_code: str | None = None
    ) -> None:
        """The probe result for ``instrument`` (Req 4.2)."""
        self._probes[self._instrument(instrument)] = AtlasProbe(instrument, served, error_code)

    def record_bar_fetch(
        self,
        instrument: str,
        session: date,
        *,
        contract: str | None,
        source: BarSourceName | None,
        error_code: str | None = None,
    ) -> None:
        """One session's bar fetch; a later call for the same session replaces it.

        ``contract=None`` means the roll calendar assigns no contract to the
        session. ``error_code`` is the last error code of a failed request.
        """
        key = (self._instrument(instrument), self._session(session))
        self._bar_fetches[key] = BarFetch(instrument, session, contract, source, error_code)

    def record_off_grid_price(
        self, instrument: str, session: date, *, open_ns: Instant, column: PriceColumn, price: float
    ) -> None:
        """A received price off the 0.25-point tick grid, for the bar opening at ``open_ns``."""
        key = (self._instrument(instrument), self._session(session))
        self._off_grid.setdefault(key, []).append(OffGridPrice(open_ns, column, price))

    def atlas_probe(self, instrument: str) -> AtlasProbe | None:
        return self._probes.get(instrument)

    def bar_fetch(self, instrument: str, session: date) -> BarFetch | None:
        return self._bar_fetches.get((instrument, session))

    def off_grid_prices(self, instrument: str, session: date) -> tuple[OffGridPrice, ...]:
        return tuple(self._off_grid.get((instrument, session), ()))

    # ---------------------------------------------------------------- dark pool

    def record_dark_pool_error(
        self, ticker: str, sessions: Iterable[date], error_code: str
    ) -> None:
        """``GET /v1/dark-pool/trades`` failed after retries for these sessions (Req 4.13)."""
        if ticker not in self._request.dark_pool_tickers:
            raise ValueError(f"ticker {ticker!r} is not a requested dark-pool ticker")
        code = _check_code("error_code", error_code)
        if code is None:
            raise ValueError("error_code must be a non-blank string")
        days = [self._session(d) for d in sessions]
        for d in days:
            self._dark_pool_errors[(ticker, d)] = code

    def dark_pool_error(self, ticker: str, session: date) -> str | None:
        return self._dark_pool_errors.get((ticker, session))

    # ---------------------------------------------------------------- outcome

    def mark_stopped(self, outcome: PullOutcome, detail: str | None = None) -> None:
        """Set how the pull ended when it returns instead of raising (for example exit 4)."""
        if outcome not in PULL_OUTCOMES:
            raise ValueError(f"outcome must be one of {sorted(PULL_OUTCOMES)}, got {outcome!r}")
        self._stopped = (outcome, _check_code("detail", detail))

    @property
    def stopped(self) -> tuple[PullOutcome, str | None] | None:
        return self._stopped


# ---------------------------------------------------------------- report: heatmaps


@dataclass(frozen=True, slots=True)
class WindowCounts:
    """Cache_Windows of one session by catalog status (``absent`` = no catalog row)."""

    complete: int = 0
    no_data: int = 0
    incomplete: int = 0
    absent: int = 0

    @property
    def total(self) -> int:
        return self.complete + self.no_data + self.incomplete + self.absent

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "total": self.total,
            "complete": self.complete,
            "no_data": self.no_data,
            "incomplete": self.incomplete,
            "absent": self.absent,
        }


@dataclass(frozen=True, slots=True)
class IncompleteWindow:
    """A Cache_Window left incomplete: ``incomplete`` in the catalog, or ``absent``."""

    start_ns: Instant
    end_ns: Instant
    status: Literal["incomplete", "absent"]

    def to_json(self) -> dict[str, JsonValue]:
        return {
            **instant_fields("start", self.start_ns),
            **instant_fields("end", self.end_ns),
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class HeatmapSessionCoverage:
    """One symbol, metric and session."""

    session: date
    skipped: bool
    snapshots: int
    resolution: tuple[str, ...]
    windows: WindowCounts
    no_data_gaps: tuple[TimeRange, ...]
    incomplete_windows: tuple[IncompleteWindow, ...]

    @property
    def stored(self) -> bool:
        """At least one Snapshot is stored for the session."""
        return self.snapshots > 0

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "session": self.session.isoformat(),
            "skipped": self.skipped,
            "stored": self.stored,
            "snapshots": self.snapshots,
            "resolution": list(self.resolution),
            "windows": self.windows.to_json(),
            "no_data_gaps": [g.to_json() for g in self.no_data_gaps],
            "incomplete_windows": [w.to_json() for w in self.incomplete_windows],
        }


@dataclass(frozen=True, slots=True)
class HeatmapCoverage:
    """Every requested session of one symbol and metric (Req 3.11)."""

    symbol: str
    metric: str
    first_history: date | None
    sessions: tuple[HeatmapSessionCoverage, ...]

    @property
    def sessions_requested(self) -> int:
        return len(self.sessions)

    @property
    def sessions_stored(self) -> int:
        return sum(1 for s in self.sessions if s.stored)

    @property
    def sessions_skipped(self) -> int:
        return sum(1 for s in self.sessions if s.skipped)

    @property
    def no_data_gaps(self) -> int:
        return sum(len(s.no_data_gaps) for s in self.sessions)

    @property
    def incomplete_windows(self) -> int:
        return sum(len(s.incomplete_windows) for s in self.sessions)

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "symbol": self.symbol,
            "metric": self.metric,
            "first_history": None if self.first_history is None else self.first_history.isoformat(),
            "sessions_requested": self.sessions_requested,
            "sessions_stored": self.sessions_stored,
            "sessions_skipped": self.sessions_skipped,
            "no_data_gaps": self.no_data_gaps,
            "incomplete_windows": self.incomplete_windows,
            "sessions": [s.to_json() for s in self.sessions],
        }


# ---------------------------------------------------------------- report: futures


@dataclass(frozen=True, slots=True)
class MissingMinutes:
    """RTH minutes with no bar, and why (Req 4.8)."""

    start_ns: Instant
    end_ns: Instant
    cause: BarGapCause
    error_code: str | None = None

    @property
    def minutes(self) -> int:
        return (self.end_ns - self.start_ns) // NS_PER_MINUTE

    def to_json(self) -> dict[str, JsonValue]:
        return {
            **instant_fields("start", self.start_ns),
            **instant_fields("end", self.end_ns),
            "minutes": self.minutes,
            "cause": self.cause,
            "error_code": self.error_code,
        }


@dataclass(frozen=True, slots=True)
class FuturesSessionCoverage:
    """One instrument and session: contract, source and missing RTH minutes (Req 4.6, 4.8)."""

    session: date
    status: WindowStatus
    contract: str | None
    source: str | None
    bars: int
    missing_rth: tuple[MissingMinutes, ...]
    off_grid_prices: tuple[OffGridPrice, ...] = ()

    @property
    def missing_minutes(self) -> int:
        return sum(m.minutes for m in self.missing_rth)

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "session": self.session.isoformat(),
            "status": self.status,
            "contract": self.contract,
            "source": self.source,
            "bars": self.bars,
            "missing_rth_minutes": self.missing_minutes,
            "missing_rth": [m.to_json() for m in self.missing_rth],
            "off_grid_prices": [p.to_json() for p in self.off_grid_prices],
        }


@dataclass(frozen=True, slots=True)
class FuturesCoverage:
    """One futures instrument over every requested session."""

    instrument: str
    interval_s: int
    atlas_probe: AtlasProbe | None  # None: the pull ended before the probe
    sessions: tuple[FuturesSessionCoverage, ...]

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "instrument": self.instrument,
            "interval_s": self.interval_s,
            "atlas_probe": None if self.atlas_probe is None else self.atlas_probe.to_json(),
            "sessions_with_bars": sum(1 for s in self.sessions if s.bars > 0),
            "sessions_missing_rth": sum(1 for s in self.sessions if s.missing_rth),
            "sessions": [s.to_json() for s in self.sessions],
        }


# ---------------------------------------------------------------- report: VIX and dark pool


@dataclass(frozen=True, slots=True)
class VixGap:
    """A session that lacks one or more VIX items (Req 4.11)."""

    session: date
    items: tuple[VixItem, ...]
    intraday_missing: tuple[TimeRange, ...] = ()

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "session": self.session.isoformat(),
            "items": list(self.items),
            "intraday_missing": [r.to_json() for r in self.intraday_missing],
        }


@dataclass(frozen=True, slots=True)
class VixCoverage:
    instrument: str
    intraday: bool
    gaps: tuple[VixGap, ...]

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "instrument": self.instrument,
            "intraday": self.intraday,
            "gaps": [g.to_json() for g in self.gaps],
        }


@dataclass(frozen=True, slots=True)
class DarkPoolGap:
    """A ticker and session with no dark-pool prints, and why (Req 4.13)."""

    session: date
    cause: DarkPoolGapCause
    error_code: str | None = None

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "session": self.session.isoformat(),
            "cause": self.cause,
            "error_code": self.error_code,
        }


@dataclass(frozen=True, slots=True)
class DarkPoolCoverage:
    ticker: str
    gaps: tuple[DarkPoolGap, ...]

    def to_json(self) -> dict[str, JsonValue]:
        return {"ticker": self.ticker, "gaps": [g.to_json() for g in self.gaps]}


# ---------------------------------------------------------------- the report


@dataclass(frozen=True, slots=True)
class CoverageReport:
    """Everything in ``coverage_{pull_id}.json``; :func:`render_markdown` writes the ``.md``."""

    request: CoverageRequest
    outcome: PullOutcome
    error: str | None
    ended_at: Instant
    heatmaps: tuple[HeatmapCoverage, ...]
    futures: tuple[FuturesCoverage, ...]
    vix: VixCoverage
    dark_pool: tuple[DarkPoolCoverage, ...]
    problems: tuple[str, ...]

    def to_json(self) -> dict[str, JsonValue]:
        req = self.request
        return {
            "format": COVERAGE_FORMAT,
            "pull_id": req.pull_id,
            **instant_fields("started_at", req.started_at),
            **instant_fields("ended_at", self.ended_at),
            "outcome": self.outcome,
            "error": self.error,
            "range": {"first": req.first.isoformat(), "last": req.last.isoformat()},
            "sessions_requested": len(req.sessions),
            "view": to_jsonable(req.view.to_json_obj()),
            "view_id": req.view.view_id(),
            "heatmaps": [h.to_json() for h in self.heatmaps],
            "futures": [f.to_json() for f in self.futures],
            "vix": self.vix.to_json(),
            "dark_pool": {
                "enabled": bool(req.dark_pool_tickers),
                "tickers": [d.to_json() for d in self.dark_pool],
            },
            "problems": list(self.problems),
        }


def build_coverage_report(
    collector: CoverageCollector,
    *,
    cache: DataCache,
    calendar: SessionCalendar,
    ended_at: Instant,
    outcome: PullOutcome | None = None,
    error: str | None = None,
    roll: RollCalendar | None = None,
) -> CoverageReport:
    """Build the report from the Data_Cache and ``collector``.

    ``outcome`` defaults to the collector's :meth:`~CoverageCollector.mark_stopped`
    value, else ``completed``. ``roll`` lets the report name "no contract in
    the roll calendar" for sessions the Bar_Source never reached. Cache read
    failures become ``problems`` entries; only an invalid argument raises.
    """
    _check_instant("ended_at", ended_at)
    if outcome is None:
        outcome, stopped_detail = collector.stopped or ("completed", None)
        error = error if error is not None else stopped_detail
    elif outcome not in PULL_OUTCOMES:
        raise ValueError(f"outcome must be one of {sorted(PULL_OUTCOMES)}, got {outcome!r}")
    problems: list[str] = []
    return CoverageReport(
        request=collector.request,
        outcome=outcome,
        error=error,
        ended_at=ended_at,
        heatmaps=_heatmaps(collector, cache, calendar, problems),
        futures=_futures(collector, cache, calendar, roll, problems),
        vix=_vix(collector.request, cache, calendar, problems),
        dark_pool=_dark_pool(collector, cache, problems),
        problems=tuple(problems),
    )


# ---------------------------------------------------------------- builders


def _heatmaps(
    collector: CoverageCollector,
    cache: DataCache,
    calendar: SessionCalendar,
    problems: list[str],
) -> tuple[HeatmapCoverage, ...]:
    req = collector.request
    view_id = req.view.view_id()
    out: list[HeatmapCoverage] = []
    for symbol in req.symbols:
        for metric in req.metrics:
            try:
                rows = cache.catalog.windows(symbol=symbol, metric=metric, view_id=view_id)
            except _READ_ERRORS as exc:
                problems.append(f"{symbol} {metric}: cannot read the catalog: {_describe(exc)}")
                rows = []
            by_session: dict[date, dict[Instant, WindowRecord]] = {}
            for row in rows:
                by_session.setdefault(row.session, {})[row.start_ns] = row
            sessions = tuple(
                _heatmap_session(
                    collector, cache, calendar, symbol, metric, d, by_session.get(d, {}), problems
                )
                for d in req.sessions
            )
            out.append(HeatmapCoverage(symbol, metric, req.first_history.get(symbol), sessions))
    return tuple(out)


def _heatmap_session(
    collector: CoverageCollector,
    cache: DataCache,
    calendar: SessionCalendar,
    symbol: str,
    metric: Metric,
    session: date,
    records: Mapping[Instant, WindowRecord],
    problems: list[str],
) -> HeatmapSessionCoverage:
    if collector.request.skipped(symbol, session):
        return HeatmapSessionCoverage(session, True, 0, (), WindowCounts(), (), ())
    pull_start, pull_end = calendar.pull_window(session)
    counts = {"complete": 0, "no_data": 0, "incomplete": 0, "absent": 0}
    snapshots = 0
    gaps = list(collector.no_data_ranges(symbol, metric, session))
    incomplete: list[IncompleteWindow] = []
    first_stored: Instant | None = None
    for start in cache_window_starts(pull_start, pull_end):
        end = cache_window_end(start, pull_end)
        record = records.get(start)
        if record is None:
            counts["absent"] += 1
            incomplete.append(IncompleteWindow(start, end, "absent"))
            continue
        counts[record.status] += 1
        if record.status == "no_data":
            gaps.append(TimeRange(start, end))
        elif record.status == "complete":
            rows = record.rows or 0
            snapshots += rows
            if rows and first_stored is None:
                first_stored = start
        else:
            incomplete.append(IncompleteWindow(start, end, "incomplete"))
    resolution = collector.resolutions(symbol, metric, session)
    if not resolution and first_stored is not None:
        key = CacheWindowKey(
            symbol, metric, collector.request.view.view_id(), session, first_stored
        )
        resolution = _stored_resolutions(cache, key, problems)
    return HeatmapSessionCoverage(
        session=session,
        skipped=False,
        snapshots=snapshots,
        resolution=resolution,
        windows=WindowCounts(**counts),
        no_data_gaps=merge_ranges(gaps),
        incomplete_windows=tuple(incomplete),
    )


def _stored_resolutions(
    cache: DataCache, key: CacheWindowKey, problems: list[str]
) -> tuple[str, ...]:
    """The distinct ``resolution`` values of one stored window (one column is read)."""
    path = cache.window_path(key)
    try:
        with pq.ParquetFile(path) as parquet:
            values = cast(
                "list[object]", parquet.read(columns=["resolution"]).column(0).to_pylist()
            )
    except _READ_ERRORS as exc:
        problems.append(f"{key.describe()}: cannot read the stored resolution: {_describe(exc)}")
        return ()
    return tuple(sorted({str(v) for v in values if v is not None}))


def _futures(
    collector: CoverageCollector,
    cache: DataCache,
    calendar: SessionCalendar,
    roll: RollCalendar | None,
    problems: list[str],
) -> tuple[FuturesCoverage, ...]:
    req = collector.request
    out: list[FuturesCoverage] = []
    for instrument in req.instruments:
        try:
            rows = cache.catalog.bars_coverage(
                dataset="bars", instrument=instrument, interval_s=req.bar_interval_s
            )
        except _READ_ERRORS as exc:
            problems.append(f"{instrument} bars: cannot read the catalog: {_describe(exc)}")
            rows = []
        records = {row.session: row for row in rows}
        sessions = tuple(
            _futures_session(
                collector, cache, calendar, roll, instrument, d, records.get(d), problems
            )
            for d in req.sessions
        )
        out.append(
            FuturesCoverage(
                instrument, req.bar_interval_s, collector.atlas_probe(instrument), sessions
            )
        )
    return tuple(out)


def _futures_session(
    collector: CoverageCollector,
    cache: DataCache,
    calendar: SessionCalendar,
    roll: RollCalendar | None,
    instrument: str,
    session: date,
    record: BarsCoverageRecord | None,
    problems: list[str],
) -> FuturesSessionCoverage:
    fetch = collector.bar_fetch(instrument, session)
    status: WindowStatus = "absent" if record is None else record.status
    contract = record.contract if record is not None and record.contract else None
    source = record.source if record is not None and record.source else None
    if fetch is not None:
        contract = contract or fetch.contract
        source = source or fetch.source
    error_code = None if fetch is None else fetch.error_code
    rth_start, rth_end = calendar.rth_open(session), calendar.rth_close(session)
    bars = 0
    if status == "complete":
        try:
            stored = cache.bars.read_session(instrument, collector.request.bar_interval_s, session)
        except _READ_ERRORS as exc:
            problems.append(f"{instrument} bars {session}: cannot read: {_describe(exc)}")
            missing: tuple[MissingMinutes, ...] = (
                MissingMinutes(rth_start, rth_end, "unreadable"),
            )
        else:
            bars = len(stored)
            cause: BarGapCause = "no_bars" if error_code is None else "error"
            missing = tuple(
                MissingMinutes(r.start_ns, r.end_ns, cause, error_code)
                for r in missing_minute_ranges(
                    ((b.open_ns, b.close_ns) for b in stored), rth_start, rth_end
                )
            )
    else:
        cause = _absent_cause(status, fetch, record, roll, instrument, session)
        code = error_code if cause == "error" else None
        missing = (MissingMinutes(rth_start, rth_end, cause, code),)
    return FuturesSessionCoverage(
        session=session,
        status=status,
        contract=contract,
        source=source,
        bars=bars,
        missing_rth=missing,
        off_grid_prices=collector.off_grid_prices(instrument, session),
    )


def _absent_cause(
    status: WindowStatus,
    fetch: BarFetch | None,
    record: BarsCoverageRecord | None,
    roll: RollCalendar | None,
    instrument: str,
    session: date,
) -> BarGapCause:
    """The cause for a session with no stored bars (``no_data``, ``incomplete`` or ``absent``)."""
    error_code = None if fetch is None else fetch.error_code
    if status == "no_data":
        return "no_bars" if error_code is None else "error"
    if fetch is not None:
        if fetch.contract is None:
            return "no_contract"
    elif (
        (record is None or not record.contract)
        and roll is not None
        and roll.contract_for(instrument, session) is None
    ):
        return "no_contract"
    return "not_fetched" if error_code is None else "error"


def _prior_session(calendar: SessionCalendar, session: date) -> date | None:
    """The most recent session before ``session``, or None outside the calendar."""
    d = session - _ONE_DAY
    while calendar.covers(d):
        if calendar.is_session(d):
            return d
        d -= _ONE_DAY
    return None


def _vix(
    req: CoverageRequest, cache: DataCache, calendar: SessionCalendar, problems: list[str]
) -> VixCoverage:
    daily: Mapping[date, VixDailyRecord]
    try:
        daily = cache.vix.read_daily()
    except _READ_ERRORS as exc:
        problems.append(f"VIX daily values: cannot read: {_describe(exc)}")
        daily = {}
    intraday: dict[date, BarsCoverageRecord] = {}
    if req.vix_intraday:
        try:
            rows = cache.catalog.bars_coverage(
                dataset="vix", instrument=req.vix_instrument, interval_s=_VIX_INTERVAL_S
            )
        except _READ_ERRORS as exc:
            problems.append(f"{req.vix_instrument} bars: cannot read the catalog: {_describe(exc)}")
            rows = []
        intraday = {row.session: row for row in rows}
    gaps: list[VixGap] = []
    for session in req.sessions:
        items: list[VixItem] = []
        today = daily.get(session)
        if today is None or today.open is None:
            items.append("daily_open")
        prior = _prior_session(calendar, session)
        before = None if prior is None else daily.get(prior)
        if before is None or before.close is None:
            items.append("prior_close")
        missing: tuple[TimeRange, ...] = ()
        if req.vix_intraday:
            missing = _vix_intraday_missing(
                req, cache, calendar, session, intraday.get(session), problems
            )
            if missing:
                items.append("intraday_bars")
        if items:
            gaps.append(VixGap(session, tuple(items), missing))
    return VixCoverage(req.vix_instrument, req.vix_intraday, tuple(gaps))


def _vix_intraday_missing(
    req: CoverageRequest,
    cache: DataCache,
    calendar: SessionCalendar,
    session: date,
    record: BarsCoverageRecord | None,
    problems: list[str],
) -> tuple[TimeRange, ...]:
    rth_start, rth_end = calendar.rth_open(session), calendar.rth_close(session)
    if record is None or record.status != "complete":
        return (TimeRange(rth_start, rth_end),)
    try:
        stored = cache.vix.bars.read_session(req.vix_instrument, _VIX_INTERVAL_S, session)
    except _READ_ERRORS as exc:
        problems.append(f"{req.vix_instrument} bars {session}: cannot read: {_describe(exc)}")
        return (TimeRange(rth_start, rth_end),)
    return missing_minute_ranges(((b.open_ns, b.close_ns) for b in stored), rth_start, rth_end)


def _dark_pool(
    collector: CoverageCollector, cache: DataCache, problems: list[str]
) -> tuple[DarkPoolCoverage, ...]:
    req = collector.request
    out: list[DarkPoolCoverage] = []
    for ticker in req.dark_pool_tickers:
        unreadable = False
        fetched: frozenset[date] = frozenset()
        with_prints: set[date] = set()
        if req.sessions:
            try:
                fetched = cache.darkpool.fetched_dates(ticker)
                prints = cache.darkpool.read(ticker, req.sessions[0], req.sessions[-1])
                with_prints = {trade_date_of(p.ts_ns) for p in prints}
            except _READ_ERRORS as exc:
                problems.append(f"dark-pool prints {ticker}: cannot read: {_describe(exc)}")
                unreadable = True
        gaps: list[DarkPoolGap] = []
        for session in req.sessions:
            if unreadable:
                gaps.append(DarkPoolGap(session, "unreadable"))
            elif session in fetched:
                if session not in with_prints:
                    gaps.append(DarkPoolGap(session, "no_prints"))
            else:
                code = collector.dark_pool_error(ticker, session)
                if code is None:
                    gaps.append(DarkPoolGap(session, "not_fetched"))
                else:
                    gaps.append(DarkPoolGap(session, "error", code))
        out.append(DarkPoolCoverage(ticker, tuple(gaps)))
    return tuple(out)


# ---------------------------------------------------------------- Markdown

_OUTCOME_TEXT: Final[Mapping[str, str]] = MappingProxyType(
    {
        "completed": "completed",
        "interrupted": "interrupted by the Operator",
        "failed": "stopped by an error",
    }
)
_BAR_CAUSE_TEXT: Final[Mapping[str, str]] = MappingProxyType(
    {
        "no_contract": "no contract in the roll calendar",
        "no_bars": "no bars returned",
        "error": "failed request",
        "not_fetched": "not fetched (the pull ended first)",
        "unreadable": "stored bars could not be read (see Problems)",
    }
)
_DARK_POOL_CAUSE_TEXT: Final[Mapping[str, str]] = MappingProxyType(
    {
        "no_prints": "no prints",
        "error": "failed request",
        "not_fetched": "not fetched (the pull ended first)",
        "unreadable": "stored prints could not be read (see Problems)",
    }
)
_VIX_ITEM_TEXT: Final[Mapping[str, str]] = MappingProxyType(
    {
        "daily_open": "daily open",
        "prior_close": "prior-session close",
        "intraday_bars": "1-minute bars",
    }
)


def _hm(t: Instant) -> str:
    return ny_datetime(t).strftime("%H:%M")


def _span(start_ns: Instant, end_ns: Instant) -> str:
    return f"{_hm(start_ns)} to {_hm(end_ns)}"


def _inline(text: str) -> str:
    """One line of untrusted text as a Markdown code span."""
    flat = " ".join(text.split())
    return f"`{flat.replace('`', "'")}`"


def _with_code(text: str, code: str | None) -> str:
    return text if code is None else f"{text}, last error code {_inline(code)}"


def _runs[K](items: Sequence[tuple[date, K]]) -> list[tuple[date, date, int, K]]:
    """Consecutive entries with an equal key, as ``(first, last, count, key)``."""
    out: list[tuple[date, date, int, K]] = []
    for d, key in items:
        if out and out[-1][3] == key:
            first, _, count, _ = out[-1]
            out[-1] = (first, d, count + 1, key)
        else:
            out.append((d, d, 1, key))
    return out


def _sessions_text(first: date, last: date, count: int) -> str:
    if count == 1:
        return first.isoformat()
    return f"{first.isoformat()} to {last.isoformat()} ({count} sessions)"


def _bullets(lines: Sequence[str], *, indent: str = "") -> list[str]:
    return [f"{indent}- {line}" for line in lines] if lines else [f"{indent}- none"]


def render_markdown(report: CoverageReport) -> str:
    """The Operator-facing ``coverage_{pull_id}.md``; the JSON holds every detail."""
    req = report.request
    outcome = _OUTCOME_TEXT[report.outcome]
    if report.error is not None:
        outcome = f"{outcome}: {_inline(report.error)}"
    lines = [
        f"# Coverage report for pull {req.pull_id}",
        "",
        f"- Outcome: {outcome}",
        f"- Started: {ny_iso(req.started_at)}; ended: {ny_iso(report.ended_at)}",
        f"- Requested range: {req.first.isoformat()} to {req.last.isoformat()}, "
        f"{len(req.sessions)} sessions",
        f"- Heatmap_View: {req.view.view_id()} {_inline(req.view.canonical_json())}",
        "",
    ]
    lines += _heatmaps_md(report.heatmaps)
    lines += _futures_md(req, report.futures)
    lines += _vix_md(report.vix)
    lines += _dark_pool_md(req, report.dark_pool)
    if report.problems:
        lines += ["## Problems", "", *_bullets([_inline(p) for p in report.problems]), ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def _heatmaps_md(heatmaps: Sequence[HeatmapCoverage]) -> list[str]:
    lines = [
        "## Heatmaps",
        "",
        "| Symbol | Metric | First history | Requested | Stored | Skipped "
        "| no_data gaps | Incomplete windows |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for h in heatmaps:
        first = "unknown" if h.first_history is None else h.first_history.isoformat()
        lines.append(
            f"| {h.symbol} | {h.metric} | {first} | {h.sessions_requested} | "
            f"{h.sessions_stored} | {h.sessions_skipped} | {h.no_data_gaps} | "
            f"{h.incomplete_windows} |"
        )
    lines.append("")
    for h in heatmaps:
        lines += [f"### {h.symbol} {h.metric}", "", "Sessions by `meta.resolution`:", ""]
        keyed: list[tuple[date, str]] = []
        for s in h.sessions:
            if s.skipped:
                label = "skipped, dated before the first history date"
            elif s.resolution:
                label = ", ".join(s.resolution)
            elif s.stored:
                label = "resolution unknown"
            else:
                label = "no Snapshot stored"
            keyed.append((s.session, label))
        lines += _bullets([f"{_sessions_text(a, b, n)}: {k}" for a, b, n, k in _runs(keyed)])
        gaps = [
            f"{s.session.isoformat()} {_span(g.start_ns, g.end_ns)}"
            for s in h.sessions
            for g in s.no_data_gaps
        ]
        lines += ["", "`no_data` gaps:", "", *_bullets(gaps)]
        lines += ["", "Cache_Windows left incomplete:", "", *_bullets(_incomplete_md(h)), ""]
    return lines


def _incomplete_md(h: HeatmapCoverage) -> list[str]:
    """Window runs per session; consecutive sessions with the same runs share a line."""
    keyed: list[tuple[date, str | None]] = []
    for s in h.sessions:
        if not s.incomplete_windows:
            keyed.append((s.session, None))
        elif s.windows.absent == s.windows.total:
            keyed.append((s.session, "every window absent"))
        else:
            keyed.append((s.session, "; ".join(_window_runs(s.incomplete_windows))))
    return [
        f"{_sessions_text(first, last, count)}: {text}"
        for first, last, count, text in _runs(keyed)
        if text is not None
    ]


def _window_runs(windows: Sequence[IncompleteWindow]) -> list[str]:
    """Adjacent windows with the same status as ``09:15 to 09:45 absent (2 windows)``."""
    parts: list[str] = []
    run: list[IncompleteWindow] = []
    for w in (*windows, None):
        if run and (w is None or w.status != run[-1].status or w.start_ns != run[-1].end_ns):
            n = len(run)
            plural = "window" if n == 1 else "windows"
            parts.append(f"{_span(run[0].start_ns, run[-1].end_ns)} {run[0].status} ({n} {plural})")
            run = []
        if w is not None:
            run.append(w)
    return parts


def _futures_md(req: CoverageRequest, futures: Sequence[FuturesCoverage]) -> list[str]:
    lines = [f"## Futures bars ({req.bar_interval_s} s)", ""]
    if not futures:
        return [*lines, "No futures instruments were requested.", ""]
    lines += [
        "| Instrument | Atlas probe | Sessions | With bars | Missing RTH minutes |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for f in futures:
        lines.append(
            f"| {f.instrument} | {_probe_md(f.atlas_probe)} | {len(f.sessions)} | "
            f"{sum(1 for s in f.sessions if s.bars > 0)} | "
            f"{sum(s.missing_minutes for s in f.sessions)} |"
        )
    lines.append("")
    for f in futures:
        keyed = [(s.session, _contract_md(s)) for s in f.sessions]
        missing = [
            f"{s.session.isoformat()} {_span(m.start_ns, m.end_ns)} ({m.minutes} min): "
            + _with_code(_BAR_CAUSE_TEXT[m.cause], m.error_code)
            for s in f.sessions
            for m in s.missing_rth
        ]
        lines += [f"### {f.instrument}", "", "Contract and source by session:", ""]
        lines += _bullets([f"{_sessions_text(a, b, n)}: {k}" for a, b, n, k in _runs(keyed)])
        lines += ["", "Missing RTH minutes:", "", *_bullets(missing), ""]
        off_grid = [
            f"{s.session.isoformat()} bar {_hm(p.open_ns)} {p.column} {p.price!r}"
            for s in f.sessions
            for p in s.off_grid_prices
        ]
        if off_grid:
            lines += ["Prices off the tick grid:", "", *_bullets(off_grid), ""]
    return lines


def _probe_md(probe: AtlasProbe | None) -> str:
    if probe is None:
        return "not probed"
    text = "served" if probe.served else "not served"
    return _with_code(text, probe.error_code)


def _contract_md(s: FuturesSessionCoverage) -> str:
    if s.status in ("complete", "no_data"):
        text = f"{s.contract or 'unknown contract'} from {s.source or 'unknown source'}"
        return text if s.status == "complete" else f"{text}, no bars returned"
    if s.missing_rth and s.missing_rth[0].cause == "no_contract":
        return "no contract in the roll calendar"
    if s.contract is not None or s.source is not None:
        tried = f"{s.contract or 'unknown contract'} from {s.source or 'unknown source'}"
        return f"{tried}, no bars stored"
    return "no bars stored"


def _vix_md(vix: VixCoverage) -> list[str]:
    configured = "configured" if vix.intraday else "not configured"
    lines = [
        "## VIX",
        "",
        f"- Instrument: {vix.instrument}; intraday 1-minute bars: {configured}",
        "",
        "Sessions with missing VIX items:",
        "",
    ]
    entries: list[str] = []
    for g in vix.gaps:
        items = [_VIX_ITEM_TEXT[i] for i in g.items if i != "intraday_bars"]
        if g.intraday_missing:
            spans = ", ".join(
                f"{_span(r.start_ns, r.end_ns)} ({r.minutes} min)" for r in g.intraday_missing
            )
            items.append(f"1-minute bars {spans}")
        entries.append(f"{g.session.isoformat()}: {', '.join(items)}")
    return [*lines, *_bullets(entries), ""]


def _dark_pool_md(req: CoverageRequest, dark_pool: Sequence[DarkPoolCoverage]) -> list[str]:
    lines = ["## Dark pool", ""]
    if not req.dark_pool_tickers:
        return [*lines, "Not fetched: this pull does not fetch dark-pool prints.", ""]
    for d in dark_pool:
        keyed = [
            (g.session, _with_code(_DARK_POOL_CAUSE_TEXT[g.cause], g.error_code)) for g in d.gaps
        ]
        lines += [f"### {d.ticker}", "", "Sessions without prints:", ""]
        lines += _bullets([f"{_sessions_text(a, b, n)}: {k}" for a, b, n, k in _runs(keyed)])
        lines.append("")
    return lines


# ---------------------------------------------------------------- writing


@dataclass(frozen=True, slots=True)
class CoverageFiles:
    """The two report files of one pull."""

    markdown: Path
    json: Path


def coverage_paths(out_dir: Path, pull_id: str) -> CoverageFiles:
    """``out_dir/coverage_{pull_id}.md`` and ``.json``."""
    check_component("pull_id", pull_id)
    stem = f"coverage_{pull_id}"
    return CoverageFiles(markdown=out_dir / f"{stem}.md", json=out_dir / f"{stem}.json")


def write_coverage_files(
    report: CoverageReport, *, writer: LogWriter, out_dir: str | Path
) -> CoverageFiles:
    """Run the path guard on ``out_dir``, then write the JSON and the Markdown.

    Both go through the Log_Writer: redacted, temp file, fsync and rename.
    """
    target = check_output_dir(out_dir, label=OUTPUT_DIR_LABEL)
    paths = coverage_paths(target, report.request.pull_id)
    writer.write_json(paths.json, report.to_json())
    writer.write_text(paths.markdown, render_markdown(report))
    return paths


def _outcome_of(exc: BaseException | None) -> tuple[PullOutcome, str | None]:
    if exc is None:
        return "completed", None
    if isinstance(exc, KeyboardInterrupt):
        return "interrupted", None
    if isinstance(exc, SystemExit):
        if exc.code is None or exc.code == 0:
            return "completed", None
        if exc.code == EXIT_INTERRUPTED:
            return "interrupted", None
        return "failed", f"exit status {exc.code}"
    return "failed", _describe(exc)


@contextmanager
def coverage_report(
    request: CoverageRequest,
    *,
    cache: DataCache,
    calendar: SessionCalendar,
    writer: LogWriter,
    out_dir: str | Path,
    roll: RollCalendar | None = None,
    clock: Callable[[], Instant] = time.time_ns,
) -> Iterator[CoverageCollector]:
    """Yield a :class:`CoverageCollector`; write the report in ``finally``.

    Before yielding, the path guard checks ``out_dir`` (``PathGuardError``,
    exit 2, before any request) and every requested session must be a session
    of ``calendar``. On exit the outcome is the collector's
    :meth:`~CoverageCollector.mark_stopped` value if set, else ``completed``
    for a normal exit, ``interrupted`` for ``KeyboardInterrupt`` or
    ``SystemExit(130)``, and ``failed`` with the error text for any other
    exception. The pull's own exception always propagates: if the report
    cannot be written while one is in flight, the write error is printed to
    stderr and added to that exception as a note. After a normal exit a write
    error is raised.
    """
    target = check_output_dir(out_dir, label=OUTPUT_DIR_LABEL)
    for session in request.sessions:
        calendar.pull_window(session)  # raises for a date that is not a session
    collector = CoverageCollector(request)
    pending: BaseException | None = None
    try:
        yield collector
    except BaseException as exc:
        pending = exc
        raise
    finally:
        _write_on_exit(collector, pending, cache, calendar, roll, writer, target, clock)


def _write_on_exit(
    collector: CoverageCollector,
    pending: BaseException | None,
    cache: DataCache,
    calendar: SessionCalendar,
    roll: RollCalendar | None,
    writer: LogWriter,
    out_dir: Path,
    clock: Callable[[], Instant],
) -> None:
    outcome, detail = collector.stopped or _outcome_of(pending)
    try:
        report = build_coverage_report(
            collector,
            cache=cache,
            calendar=calendar,
            ended_at=clock(),
            outcome=outcome,
            error=detail,
            roll=roll,
        )
        write_coverage_files(report, writer=writer, out_dir=out_dir)
    except Exception as exc:
        if pending is None:
            raise
        message = (
            f"the coverage report for pull {collector.request.pull_id} could not be written: "
            f"{_describe(exc)}"
        )
        writer.error(f"error: {message}")
        pending.add_note(writer.redact(message))
