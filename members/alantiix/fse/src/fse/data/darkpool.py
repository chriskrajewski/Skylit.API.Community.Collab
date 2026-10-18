"""Dark-pool prints for a pull: ``GET /v1/dark-pool/trades`` per ticker (design §4).

Requirements 4.12 and 4.13. The puller calls :func:`fetch_dark_pool` when the
``dark_pool_confluence`` Gate is enabled (or ``fse pull --dark-pool``). It
fetches every ticker and session of the pull's
:class:`~fse.data.coverage.CoverageRequest` (``dark_pool_tickers``, default
:data:`DEFAULT_DARK_POOL_TICKERS`), one ticker at a time.

Per ticker:

1. **Sessions.** A completed session (dated before the pull date) whose fetch
   result is already in the Data_Cache is served from it and costs nothing. A
   session dated on the pull date is still taking prints (the TRF tape runs
   to 20:00 New York time), so it is neither fetched nor stored: coverage
   records it with error code :data:`SESSION_IN_PROGRESS`, and the Gate fails
   with ``data_unavailable`` there instead of reading a partial tape. Every
   other session is fetched.
2. **Spans.** Each run of consecutive sessions to fetch is cut by
   :func:`~fse.data.spans.split_date_range` into contiguous, non-overlapping
   spans of at most 31 calendar days, fetched oldest first.
3. **Pages.** A span is read with ``limit=5000`` and ``order=asc``, with
   ``offset`` advanced by the prints received, until a page has
   ``hasMore: false``. When the next offset would pass the documented 50,000
   cap, the span's pages are discarded, and the span is split in half and each
   half fetched again. A single trade date that still does not fit is recorded
   with error code :data:`OFFSET_CAP`. A half that holds no requested session
   is not requested.
4. **Store.** A span whose pages all arrived is written in one call to
   :meth:`~fse.data.aux_stores.DarkPoolStore.write_trade_dates`, with every
   date of the span marked fetched, including dates without prints. The
   coverage report then lists a session without prints as "no prints"
   (Req 4.13). A print for another ticker, or whose New York date lies
   outside the span, is dropped and counted.
5. **Failures.** A request that fails after the client's retries, or is
   refused, ends its span: nothing of the span is stored, and each of its
   sessions is recorded with :attr:`Failed.cause <fse.skylit.client.Failed.cause>`
   as the last error code (Req 4.13). ``404 no_data`` counts as an empty last
   page, not a failure. The fetch continues with the next span.

A 401, 402 or 403 (:class:`~fse.skylit.client.SkylitStoppedError`), a cache
read or write error and a cancellation propagate. The span in flight is not
stored, so the report lists its sessions as not fetched.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from types import MappingProxyType
from typing import Final, Protocol

from fse.data.aux_stores import DarkPoolStore, trade_date_of
from fse.data.coverage import CoverageCollector
from fse.data.planner import pull_date
from fse.data.spans import DARK_POOL_MAX_SPAN_DAYS, DateSpan, split_date_range
from fse.engine.types import DarkPoolPrint
from fse.skylit.client import Failed
from fse.skylit.endpoints import DARK_POOL_MAX_LIMIT, DARK_POOL_MAX_OFFSET
from fse.skylit.models import DarkPoolTradesResponse

__all__ = [
    "DEFAULT_DARK_POOL_TICKERS",
    "OFFSET_CAP",
    "PAGE_LIMIT",
    "SESSION_IN_PROGRESS",
    "DarkPoolTradesClient",
    "TickerFetch",
    "fetch_dark_pool",
]

DEFAULT_DARK_POOL_TICKERS: Final = ("SPY", "QQQ")
"""The configured tickers when the Operator names none (Req 4.12)."""

PAGE_LIMIT: Final = DARK_POOL_MAX_LIMIT
"""Prints per page: the documented maximum ``limit`` of 5,000."""

SESSION_IN_PROGRESS: Final = "session_in_progress"
"""Coverage error code of a session dated on the pull date: not fetched yet."""

OFFSET_CAP: Final = "offset_cap"
"""Coverage error code of a trade date with more prints than one span can page through."""

_ONE_DAY: Final = timedelta(days=1)


class DarkPoolTradesClient(Protocol):
    """The part of :class:`~fse.skylit.client.SkylitClient` this module uses."""

    async def dark_pool_trades(
        self,
        tickers: Iterable[str],
        *,
        date_start: date,
        date_end: date,
        limit: int = ...,
        offset: int = ...,
        min_notional: float | None = ...,
    ) -> DarkPoolTradesResponse | Failed: ...


@dataclass(frozen=True, slots=True)
class TickerFetch:
    """What the fetch did for one ticker. Coverage holds the per-session gaps.

    ``cached`` sessions were served from the Data_Cache, ``stored`` sessions
    were fetched and written in this pull, and ``errors`` maps each session
    recorded in coverage with an error code to that code. ``requests`` counts
    every page requested (5 credits each when it succeeds), and ``splits`` the
    spans halved at the offset cap.
    """

    ticker: str
    cached: tuple[date, ...]
    stored: tuple[date, ...]
    errors: Mapping[date, str]
    prints: int
    dropped: int
    requests: int
    splits: int


async def fetch_dark_pool(
    client: DarkPoolTradesClient,
    store: DarkPoolStore,
    coverage: CoverageCollector,
    *,
    min_notional: float | None = None,
) -> tuple[TickerFetch, ...]:
    """Fetch and store the prints of every ticker and session in ``coverage.request``.

    ``min_notional`` is sent as is; ``None`` keeps Skylit's $1,000,000 default.
    Returns one :class:`TickerFetch` per ticker, in request order, and an empty
    tuple when the request names no dark-pool ticker.
    """
    request = coverage.request
    today = pull_date(request.started_at)
    results: list[TickerFetch] = []
    for ticker in request.dark_pool_tickers:
        fetcher = _TickerFetcher(client, store, coverage, ticker, request.sessions, min_notional)
        results.append(await fetcher.run(today))
    return tuple(results)


class _TickerFetcher:
    """One ticker's fetch, with its counters."""

    __slots__ = (
        "_cached",
        "_client",
        "_coverage",
        "_dropped",
        "_errors",
        "_min_notional",
        "_prints",
        "_requests",
        "_session_set",
        "_sessions",
        "_splits",
        "_store",
        "_stored",
        "_ticker",
    )

    def __init__(
        self,
        client: DarkPoolTradesClient,
        store: DarkPoolStore,
        coverage: CoverageCollector,
        ticker: str,
        sessions: Sequence[date],
        min_notional: float | None,
    ) -> None:
        self._client = client
        self._store = store
        self._coverage = coverage
        self._ticker = ticker
        self._sessions = tuple(sessions)
        self._session_set = frozenset(sessions)
        self._min_notional = min_notional
        self._cached: tuple[date, ...] = ()
        self._stored: list[date] = []
        self._errors: dict[date, str] = {}
        self._prints = 0
        self._dropped = 0
        self._requests = 0
        self._splits = 0

    async def run(self, today: date) -> TickerFetch:
        fetched = self._store.fetched_dates(self._ticker)
        in_progress = [s for s in self._sessions if s >= today]
        if in_progress:
            self._error(in_progress, SESSION_IN_PROGRESS)
        self._cached = tuple(s for s in self._sessions if s < today and s in fetched)
        wanted = [(i, s) for i, s in enumerate(self._sessions) if s < today and s not in fetched]
        for first, last in _consecutive_runs(wanted):
            for span in split_date_range(first, last, DARK_POOL_MAX_SPAN_DAYS):
                await self._fetch_span(span)
        return TickerFetch(
            ticker=self._ticker,
            cached=self._cached,
            stored=tuple(sorted(self._stored)),
            errors=MappingProxyType(dict(sorted(self._errors.items()))),
            prints=self._prints,
            dropped=self._dropped,
            requests=self._requests,
            splits=self._splits,
        )

    async def _fetch_span(self, span: DateSpan) -> None:
        pending = [span]
        while pending:
            part = pending.pop()
            days = [d for d in part.dates() if d in self._session_set]
            if not days:
                continue
            result = await self._pages(part)
            if isinstance(result, Failed):
                self._error(days, result.cause)
            elif result is None:
                if part.calendar_days == 1:
                    self._error(days, OFFSET_CAP)
                else:
                    self._splits += 1
                    first_half, second_half = _halves(part)
                    pending += [second_half, first_half]  # the first half is fetched first
            else:
                self._write(part, days, result)

    async def _pages(self, span: DateSpan) -> list[DarkPoolPrint] | Failed | None:
        """Every print of ``span``, the request that failed, or ``None`` past the offset cap."""
        prints: list[DarkPoolPrint] = []
        offset = 0
        while True:
            page = await self._client.dark_pool_trades(
                (self._ticker,),
                date_start=span.first,
                date_end=span.last,
                limit=PAGE_LIMIT,
                offset=offset,
                min_notional=self._min_notional,
            )
            self._requests += 1
            if isinstance(page, Failed):
                return prints if page.no_data else page
            prints.extend(p.to_print() for p in page.prints)
            if not page.page.has_more or not page.prints:
                return prints
            offset += len(page.prints)
            if offset > DARK_POOL_MAX_OFFSET:
                return None

    def _write(self, span: DateSpan, days: list[date], prints: list[DarkPoolPrint]) -> None:
        kept = [
            p for p in prints if p.ticker == self._ticker and span.contains(trade_date_of(p.ts_ns))
        ]
        self._store.write_trade_dates(self._ticker, span.dates(), kept)
        self._stored.extend(days)
        self._prints += len(kept)
        self._dropped += len(prints) - len(kept)

    def _error(self, days: list[date], code: str) -> None:
        self._coverage.record_dark_pool_error(self._ticker, days, code)
        self._errors.update(dict.fromkeys(days, code))


def _consecutive_runs(indexed: Sequence[tuple[int, date]]) -> list[tuple[date, date]]:
    """``(first, last)`` of each run of sessions whose indexes in the pull follow each other."""
    runs: list[tuple[date, date]] = []
    previous = -2
    for i, d in indexed:
        if runs and i == previous + 1:
            runs[-1] = (runs[-1][0], d)
        else:
            runs.append((d, d))
        previous = i
    return runs


def _halves(span: DateSpan) -> tuple[DateSpan, DateSpan]:
    """``span`` (at least 2 days) cut into two contiguous halves; the second is the longer."""
    mid = span.first + timedelta(days=span.calendar_days // 2)
    return DateSpan(span.first, mid - _ONE_DAY), DateSpan(mid, span.last)
