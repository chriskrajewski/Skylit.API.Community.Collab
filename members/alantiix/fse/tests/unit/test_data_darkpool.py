"""Unit tests for the dark-pool fetch (``fse.data.darkpool``).

Most tests use :class:`FakeTape`, an in-memory ``/v1/dark-pool/trades`` that
checks every request against the documented caps (31 trade dates, ``limit`` up
to 5,000, ``offset`` up to 50,000) with the real parameter builder, as Skylit
answers an over-cap request with 400. Two tests run the real ``SkylitClient``
against respx mocks. Every print, ticker and key is a fake value; nothing
reaches Skylit. Every cache directory is under ``tmp_path``.

**Validates: Requirements 4.12, 4.13**
"""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, time, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

import httpx
import pytest
import respx

from fse.data.aux_stores import trade_date_of
from fse.data.cache import DataCache, HeatmapView
from fse.data.coverage import CoverageCollector, CoverageRequest, build_coverage_report
from fse.data.darkpool import (
    DEFAULT_DARK_POOL_TICKERS,
    OFFSET_CAP,
    PAGE_LIMIT,
    SESSION_IN_PROGRESS,
    fetch_dark_pool,
)
from fse.engine.types import DarkPoolPrint
from fse.logio import LogWriter, Redactor
from fse.secrets.env import EnvView
from fse.skylit import endpoints as ep
from fse.skylit.client import ClientConfig, Failed, SkylitClient
from fse.skylit.endpoints import Host
from fse.skylit.fetch_log import FETCH_LOG_FILE_NAME, FetchLog
from fse.skylit.models import DarkPoolPage, DarkPoolTradesResponse, WireDarkPoolPrint
from fse.timekit import NS_PER_SECOND, SessionCalendar, ny_instant
from tests.fakes.clock import FakeClock

KEY = "fake-skylit-key-0000"
D_TUE, D_WED, D_THU, D_FRI = (date(2026, 3, d) for d in (3, 4, 5, 6))
PULL_DAY = date(2026, 3, 9)
CALENDAR = SessionCalendar(date(2026, 3, 2), date(2026, 3, 13))


def started(day: date = PULL_DAY) -> int:
    return ny_instant(day, time(8, 0))


def collector(
    sessions: Sequence[date], *, tickers: tuple[str, ...] = ("SPY",), pull_day: date = PULL_DAY
) -> CoverageCollector:
    return CoverageCollector(
        CoverageRequest(
            pull_id="pull-1",
            started_at=started(pull_day),
            first=sessions[0],
            last=sessions[-1],
            sessions=tuple(sessions),
            symbols=("SPX",),
            view=HeatmapView(),
            dark_pool_tickers=tickers,
        )
    )


def wire(ticker: str, ts_ns: int, price: float = 580.0, size: int = 2_000) -> WireDarkPoolPrint:
    return WireDarkPoolPrint(
        timestamp_raw=ep.format_rfc3339(ts_ns),
        ts_ns=ts_ns,
        ticker=ticker,
        price=price,
        size=size,
        notional=price * size,
        venue="TRF",
        sector=None,
        industry=None,
        pct_avg_vol=None,
    )


def tape_day(ticker: str, day: date, count: int) -> list[WireDarkPoolPrint]:
    """``count`` prints from 09:30 on ``day``, 1 ms apart."""
    start = ny_instant(day, time(9, 30))
    return [wire(ticker, start + i * 1_000_000, price=580.0 + i % 7) for i in range(count)]


def weekdays(first: date, last: date) -> tuple[date, ...]:
    return SessionCalendar(first, last).sessions()


@dataclass(frozen=True, slots=True)
class Call:
    ticker: str
    first: date
    last: date
    limit: int
    offset: int
    min_notional: float | None


class FakeTape:
    """An in-memory ``/v1/dark-pool/trades``. ``fail`` may answer a call with a ``Failed``."""

    def __init__(
        self,
        prints: Iterable[WireDarkPoolPrint],
        fail: Callable[[Call], Failed | None] | None = None,
    ) -> None:
        self._by_day: dict[tuple[str, date], list[WireDarkPoolPrint]] = defaultdict(list)
        for p in sorted(prints, key=lambda p: p.ts_ns):
            self._by_day[(p.ticker, trade_date_of(p.ts_ns))].append(p)
        self._fail = fail
        self.calls: list[Call] = []

    async def dark_pool_trades(
        self,
        tickers: Iterable[str],
        *,
        date_start: date,
        date_end: date,
        limit: int = PAGE_LIMIT,
        offset: int = 0,
        min_notional: float | None = None,
    ) -> DarkPoolTradesResponse | Failed:
        names = tuple(tickers)
        # The real builder raises on anything Skylit would refuse with 400.
        ep.dark_pool_trades_params(
            names,
            date_start=date_start,
            date_end=date_end,
            limit=limit,
            offset=offset,
            min_notional=min_notional,
        )
        assert len(names) == 1
        call = Call(names[0], date_start, date_end, limit, offset, min_notional)
        self.calls.append(call)
        if self._fail is not None and (failed := self._fail(call)) is not None:
            return failed
        rows: list[WireDarkPoolPrint] = []
        day = date_start
        while day <= date_end:
            rows += self._by_day.get((names[0], day), [])
            day += timedelta(days=1)
        page = tuple(rows[offset : offset + limit])
        return DarkPoolTradesResponse(
            page, DarkPoolPage(limit, offset, len(page), has_more=len(page) == limit)
        )


def failed(status: int, code: str, call: Call) -> Failed:
    return Failed(
        Host.API,
        ep.DARK_POOL_TRADES.path,
        {"tickers": call.ticker},
        "rejected" if status == 404 else "failed",
        attempts=1 if status == 404 else 5,
        status=status,
        error_code=code,
    )


@pytest.fixture
def cache(tmp_path: Path) -> Iterator[DataCache]:
    with DataCache(tmp_path / "cache") as c:
        yield c


def as_prints(rows: Iterable[WireDarkPoolPrint]) -> list[DarkPoolPrint]:
    return [p.to_print() for p in rows]


# ---------------------------------------------------------------- pages, store and coverage


@pytest.mark.asyncio
async def test_pages_by_offset_stores_prints_and_reports_no_print_sessions(
    cache: DataCache,
) -> None:
    spy = tape_day("SPY", D_TUE, PAGE_LIMIT + 2_000)
    qqq = tape_day("QQQ", D_THU, 3)
    tape = FakeTape([*spy, *qqq])
    sessions = (D_TUE, D_WED, D_THU, D_FRI)
    cov = collector(sessions, tickers=DEFAULT_DARK_POOL_TICKERS)

    results = await fetch_dark_pool(tape, cache.darkpool, cov)

    # One span per ticker; SPY needs a second page at offset 5000.
    assert [(c.ticker, c.first, c.last, c.limit, c.offset) for c in tape.calls] == [
        ("SPY", D_TUE, D_FRI, PAGE_LIMIT, 0),
        ("SPY", D_TUE, D_FRI, PAGE_LIMIT, PAGE_LIMIT),
        ("QQQ", D_TUE, D_FRI, PAGE_LIMIT, 0),
    ]
    assert all(c.min_notional is None for c in tape.calls)
    assert [(r.ticker, r.stored, r.prints, r.requests, r.splits) for r in results] == [
        ("SPY", sessions, len(spy), 2, 0),
        ("QQQ", sessions, 3, 1, 0),
    ]
    assert cache.darkpool.read("SPY", D_TUE, D_FRI) == as_prints(spy)
    assert cache.darkpool.read("QQQ", D_TUE, D_FRI) == as_prints(qqq)

    report = build_coverage_report(cov, cache=cache, calendar=CALENDAR, ended_at=started() + 1)
    gaps = {
        d.ticker: [(g.session, g.cause, g.error_code) for g in d.gaps] for d in report.dark_pool
    }
    assert gaps == {
        "SPY": [(d, "no_prints", None) for d in (D_WED, D_THU, D_FRI)],
        "QQQ": [(d, "no_prints", None) for d in (D_TUE, D_WED, D_FRI)],
    }


@pytest.mark.asyncio
async def test_spans_are_31_days_skip_cached_sessions_and_hold_back_the_pull_day(
    cache: DataCache,
) -> None:
    pull_day = date(2026, 3, 13)
    sessions = weekdays(date(2026, 1, 2), pull_day)
    cached = date(2026, 2, 2)
    cache.darkpool.write_trade_dates("SPY", [cached], [])
    tape = FakeTape(tape_day("SPY", date(2026, 1, 20), 4))
    cov = collector(sessions, pull_day=pull_day)

    (result,) = await fetch_dark_pool(tape, cache.darkpool, cov)

    spans = [(c.first, c.last) for c in tape.calls]
    assert all((last - first).days + 1 <= ep.DARK_POOL_MAX_SPAN_DAYS for first, last in spans)
    # Two runs of sessions, split around the cached session; spans in a run are contiguous.
    assert spans == [
        (date(2026, 1, 2), date(2026, 1, 30)),
        (date(2026, 2, 3), date(2026, 3, 5)),
        (date(2026, 3, 6), date(2026, 3, 12)),
    ]
    assert result.cached == (cached,)
    assert result.stored == tuple(s for s in sessions if s not in (cached, pull_day))
    assert dict(result.errors) == {pull_day: SESSION_IN_PROGRESS}
    assert cov.dark_pool_error("SPY", pull_day) == SESSION_IN_PROGRESS
    assert pull_day not in cache.darkpool.fetched_dates("SPY")
    assert result.prints == 4


@pytest.mark.asyncio
async def test_a_span_past_offset_50000_is_halved_and_one_overfull_day_is_an_error(
    cache: DataCache,
) -> None:
    per_day = {D_TUE: 30_000, D_WED: 30_000, D_THU: 56_000}
    rows = [p for day, n in per_day.items() for p in tape_day("SPY", day, n)]
    tape = FakeTape(rows)
    cov = collector((D_TUE, D_WED, D_THU))

    (result,) = await fetch_dark_pool(tape, cache.darkpool, cov)

    # [Tue-Thu] overflows -> [Tue] + [Wed-Thu]; [Wed-Thu] overflows -> [Wed] + [Thu]; [Thu] alone
    # still overflows, so Thu is recorded with the offset-cap code and nothing is stored for it.
    assert [(c.first, c.last) for c in tape.calls if c.offset == 0] == [
        (D_TUE, D_THU),
        (D_TUE, D_TUE),
        (D_WED, D_THU),
        (D_WED, D_WED),
        (D_THU, D_THU),
    ]
    assert max(c.offset for c in tape.calls) == ep.DARK_POOL_MAX_OFFSET
    assert {c.limit for c in tape.calls} == {PAGE_LIMIT}
    assert result.splits == 2
    assert result.stored == (D_TUE, D_WED)
    assert dict(result.errors) == {D_THU: OFFSET_CAP}
    assert cov.dark_pool_error("SPY", D_THU) == OFFSET_CAP
    assert result.prints == 60_000
    assert len(cache.darkpool.read("SPY", D_TUE, D_THU)) == 60_000
    assert cache.darkpool.fetched_dates("SPY") == {D_TUE, D_WED}


@pytest.mark.asyncio
async def test_a_failed_span_records_its_last_error_code_and_the_next_span_continues(
    cache: DataCache,
) -> None:
    sessions = weekdays(date(2026, 1, 5), date(2026, 2, 27))
    first_span = [s for s in sessions if s <= date(2026, 2, 4)]
    second_span = [s for s in sessions if s > date(2026, 2, 4)]

    def fail(call: Call) -> Failed | None:
        if call.first == sessions[0] and call.offset == PAGE_LIMIT:
            return failed(503, "unavailable", call)  # second page of the first span
        if call.first == second_span[0]:
            return failed(404, "no_data", call)  # no prints at all: an empty last page
        return None

    tape = FakeTape(tape_day("SPY", date(2026, 1, 6), PAGE_LIMIT), fail)
    cov = collector(sessions)

    (result,) = await fetch_dark_pool(tape, cache.darkpool, cov)

    assert result.requests == 3
    assert dict(result.errors) == dict.fromkeys(first_span, "HTTP 503 (unavailable)")
    assert all(cov.dark_pool_error("SPY", s) == "HTTP 503 (unavailable)" for s in first_span)
    assert result.stored == tuple(second_span)
    assert result.prints == 0
    assert cache.darkpool.fetched_dates("SPY") >= set(second_span)
    assert cache.darkpool.fetched_dates("SPY").isdisjoint(first_span)
    assert cache.darkpool.read("SPY", sessions[0], sessions[-1]) == []


@pytest.mark.asyncio
async def test_prints_for_another_ticker_or_outside_the_span_are_dropped(
    cache: DataCache,
) -> None:
    inside = wire("SPY", ny_instant(D_TUE, time(10, 0)))
    outside = wire("SPY", ny_instant(D_WED, time(10, 0)))
    other = wire("QQQ", ny_instant(D_TUE, time(11, 0)))

    class Leaky(FakeTape):
        async def dark_pool_trades(self, *args: Any, **kwargs: Any) -> Any:
            await super().dark_pool_trades(*args, **kwargs)
            return DarkPoolTradesResponse((inside, outside, other), DarkPoolPage(5000, 0, 3, False))

    cov = collector((D_TUE,))

    (result,) = await fetch_dark_pool(Leaky([]), cache.darkpool, cov)

    assert (result.prints, result.dropped) == (1, 2)
    assert cache.darkpool.read("SPY", D_TUE, D_WED) == [inside.to_print()]


@pytest.mark.asyncio
async def test_no_dark_pool_tickers_sends_nothing(cache: DataCache) -> None:
    tape = FakeTape([])
    assert await fetch_dark_pool(tape, cache.darkpool, collector((D_TUE,), tickers=())) == ()
    assert tape.calls == []


# ---------------------------------------------------------------- the real client (respx)


def _ok(body: object) -> httpx.Response:
    return httpx.Response(200, json=body)


def _trades_route(router: respx.MockRouter, ticker: str) -> respx.Route:
    return router.route(
        method="GET",
        scheme="https",
        host=Host.API.value,
        path=ep.DARK_POOL_TRADES.path,
        params={"tickers": ticker},
    )


def _print_json(ticker: str, ts: str) -> dict[str, Any]:
    return {
        "timestamp": ts,
        "ticker": ticker,
        "price": 581.25,
        "size": 4_000,
        "notional": 2_325_000.0,
        "venue": "TRF",
    }


@pytest.mark.asyncio
async def test_real_client_sends_the_documented_query_and_records_a_failure_after_retries(
    respx_router: respx.MockRouter, cache: DataCache, tmp_path: Path
) -> None:
    account = {"data": {"limits": {"requestsPerMinute": 600, "historicalInFlight": 1}}}
    respx_router.route(method="GET", host=Host.API.value, path="/v1/account").mock(
        return_value=_ok(account)
    )
    spy_body = {
        "data": [
            _print_json("SPY", "2026-03-03T15:00:00.123Z"),
            _print_json("SPY", "2026-03-04T19:59:59Z"),
        ],
        "meta": {"limit": 5000, "offset": 0, "count": 2, "hasMore": False},
    }
    spy = _trades_route(respx_router, "SPY").mock(return_value=_ok(spy_body))
    qqq = _trades_route(respx_router, "QQQ").mock(
        return_value=httpx.Response(
            503, json={"error": {"code": "unavailable", "message": "fake message"}}
        )
    )
    cov = collector((D_TUE, D_WED), tickers=("SPY", "QQQ"))
    clock = FakeClock(started())

    with FetchLog(LogWriter(Redactor([KEY])), tmp_path / FETCH_LOG_FILE_NAME) as log:
        env = EnvView({"SKYLIT_API_KEY": KEY}, {})
        async with SkylitClient(env, ClientConfig(), log, clock, random.Random(3)) as client:
            spy_result, qqq_result = await clock.run(fetch_dark_pool(client, cache.darkpool, cov))

    assert spy.call_count == 1
    query: Mapping[str, str] = dict(parse_qsl(spy.calls.last.request.url.query.decode()))
    assert query == {
        "tickers": "SPY",
        "date_start": "2026-03-03",
        "date_end": "2026-03-04",
        "limit": "5000",
        "offset": "0",
        "order": "asc",
    }
    assert spy_result.stored == (D_TUE, D_WED)
    assert [p.ts_ns for p in cache.darkpool.read("SPY", D_TUE, D_WED)] == [
        ny_instant(D_TUE, time(10, 0)) + 123_000_000,
        ny_instant(D_WED, time(14, 59, 59)),
    ]
    assert qqq.call_count == 5  # every attempt of the retry policy
    assert dict(qqq_result.errors) == dict.fromkeys((D_TUE, D_WED), "HTTP 503 (unavailable)")
    report = build_coverage_report(
        cov, cache=cache, calendar=CALENDAR, ended_at=clock.now() + NS_PER_SECOND
    )
    by_ticker = {d.ticker: d.gaps for d in report.dark_pool}
    assert by_ticker["SPY"] == ()
    assert [(g.session, g.cause, g.error_code) for g in by_ticker["QQQ"]] == [
        (D_TUE, "error", "HTTP 503 (unavailable)"),
        (D_WED, "error", "HTTP 503 (unavailable)"),
    ]
