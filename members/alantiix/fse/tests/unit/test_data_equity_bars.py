"""Offline tests for the isolated QQQ/SPY Atlas equity-bar path."""

from __future__ import annotations

import math
import random
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import replace
from datetime import date, time
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
import respx

from fse.calendars import load_exchange_calendar
from fse.data.aux_stores import EquityBarStore
from fse.data.bar_source import AtlasBars
from fse.data.cache import DataCache
from fse.data.cache_io import CacheIntegrityError, CacheWriteError, sha256_hex
from fse.data.equity_bars import (
    estimate_equity_requests,
    fetch_equity_sessions,
    pull_equity_bars,
)
from fse.engine.types import Bar
from fse.logio import LogWriter, Redactor
from fse.secrets.env import EnvView
from fse.skylit.client import ClientConfig, SkylitClient
from fse.skylit.endpoints import Host
from fse.skylit.fetch_log import FetchLog
from fse.timekit import (
    NS_PER_MINUTE,
    NS_PER_SECOND,
    SessionCalendar,
    ny_datetime,
    ny_instant,
)

KEY = "fake-skylit-key-0000"
REGULAR = date(2026, 3, 5)
EARLY = date(2026, 3, 6)
LATE = date(2026, 3, 9)
CALENDAR = SessionCalendar(
    date(2026, 3, 1), date(2026, 3, 31), early_closes={EARLY: time(13, 15)}
)
PROJECT = Path(__file__).resolve().parents[2]
RECORDED_CALENDAR = load_exchange_calendar(
    PROJECT / "calendars" / "exchange_calendar.yaml"
).sessions
RECORDED_HISTORY_SPANS = (
    (1735828200, 1746648000),
    (1746711000, 1757534400),
    (1757597400, 1768424400),
    (1768487400, 1779307200),
    (1779370200, 1790193600),
    (1790256600, 1791316800),
)
RECORDED_FAILED_BLOCK = (date(2025, 5, 8), date(2026, 1, 14))
FULL_RECORDED_RANGE = (date(2025, 1, 2), date(2026, 10, 7))
EXPECTED_SAFE_SPANS = (
    (date(2025, 1, 2), date(2025, 5, 7)),
    (date(2025, 5, 8), date(2025, 7, 2)),
    (date(2025, 7, 3), date(2025, 7, 3)),
    (date(2025, 7, 7), date(2025, 9, 10)),
    (date(2025, 9, 11), date(2025, 11, 26)),
    (date(2025, 11, 28), date(2025, 11, 28)),
    (date(2025, 12, 1), date(2025, 12, 23)),
    (date(2025, 12, 24), date(2025, 12, 24)),
    (date(2025, 12, 26), date(2026, 1, 14)),
    (date(2026, 1, 15), date(2026, 5, 20)),
    (date(2026, 5, 21), date(2026, 9, 23)),
    (date(2026, 9, 24), date(2026, 10, 7)),
)


def ny(session: date, hour: int, minute: int) -> int:
    return ny_instant(session, time(hour, minute))


def bar(symbol: str, open_ns: int) -> Bar:
    return Bar(
        symbol,
        symbol,
        60,
        open_ns,
        open_ns + NS_PER_MINUTE,
        1.0,
        1.2,
        0.8,
        1.1,
        100.0,
        None,
        None,
        None,
        None,
        "atlas",
    )


def history_body(request: httpx.Request, *, missing: int | None = None) -> dict[str, object]:
    params = request.url.params
    start, end = int(params["from"]), int(params["to"])
    timestamps = [value for value in range(start, end, 60) if value != missing]
    prices = [100.0 + i / 100.0 for i in range(len(timestamps))]
    return {
        "s": "ok",
        "t": timestamps,
        "o": prices,
        "h": [price + 0.2 for price in prices],
        "l": [price - 0.2 for price in prices],
        "c": [price + 0.1 for price in prices],
        "v": [100.0] * len(timestamps),
    }


def recorded_regular_history(
    request: httpx.Request, calendar: SessionCalendar
) -> dict[str, object]:
    """Model Atlas returning regular-session bars through a requested range."""
    params = request.url.params
    start, end = int(params["from"]), int(params["to"])
    first = ny_datetime(start * NS_PER_SECOND).date()
    last = ny_datetime(end * NS_PER_SECOND).date()
    timestamps = [
        value // NS_PER_SECOND
        for session in calendar.sessions(first, last)
        for value in range(
            calendar.rth_open(session),
            ny_instant(session, time(16, 0)),
            NS_PER_MINUTE,
        )
        if start <= value // NS_PER_SECOND < end
    ]
    prices = [100.0] * len(timestamps)
    return {
        "s": "ok",
        "t": timestamps,
        "o": prices,
        "h": [100.2] * len(timestamps),
        "l": [99.8] * len(timestamps),
        "c": [100.1] * len(timestamps),
        "v": [1.0] * len(timestamps),
    }


@pytest.fixture
def fetch_log(tmp_path: Path) -> Iterator[FetchLog]:
    with FetchLog(LogWriter(Redactor([KEY])), tmp_path / "fetch_log.jsonl") as log:
        yield log


@pytest_asyncio.fixture
async def atlas_client(
    respx_router: respx.MockRouter, fetch_log: FetchLog
) -> AsyncIterator[tuple[SkylitClient, respx.Route]]:
    respx_router.route(
        method="GET", scheme="https", host=Host.API.value, path="/v1/account"
    ).mock(return_value=httpx.Response(200, json={"data": {"limits": {}}}))
    respx_router.route(
        method="GET", scheme="https", host=Host.ATLAS.value, path="/v1/config"
    ).mock(return_value=httpx.Response(200, json={"max_fetch_trading_days": {"1": 90}}))
    history = respx_router.route(
        method="GET", scheme="https", host=Host.ATLAS.value, path="/v1/history"
    )
    async with httpx.AsyncClient(follow_redirects=False) as http:
        client = SkylitClient(
            EnvView({"SKYLIT_API_KEY": KEY}, {}),
            ClientConfig(),
            fetch_log,
            __import__("fse.clock", fromlist=["SystemClock"]).SystemClock(),
            random.Random(1),
            http=http,
        )
        yield client, history


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol", ["QQQ", "SPY"])
async def test_exact_equity_request_and_regular_rth_window(
    atlas_client: tuple[SkylitClient, respx.Route], symbol: str
) -> None:
    client, route = atlas_client
    route.mock(side_effect=lambda request: httpx.Response(200, json=history_body(request)))

    result = await fetch_equity_sessions(AtlasBars(client), CALENDAR, symbol, [REGULAR])

    assert result[0].status == "complete"
    assert len(result[0].bars) == 390
    assert dict(route.calls[0].request.url.params) == {
        "symbol": symbol,
        "resolution": "1",
        "from": str(ny(REGULAR, 9, 30) // NS_PER_SECOND),
        "to": str(ny(REGULAR, 16, 0) // NS_PER_SECOND),
        "extended": "false",
    }
    assert all(item.o_t is None for item in result[0].bars)


@pytest.mark.asyncio
async def test_early_close_uses_calendar_close(
    atlas_client: tuple[SkylitClient, respx.Route]
) -> None:
    client, route = atlas_client
    route.mock(side_effect=lambda request: httpx.Response(200, json=history_body(request)))

    (result,) = await fetch_equity_sessions(AtlasBars(client), CALENDAR, "QQQ", [EARLY])

    assert result.status == "complete"
    assert result.quality.expected_rows == 225
    assert len(result.bars) == 225
    assert dict(route.calls[0].request.url.params)["to"] == str(ny(EARLY, 13, 15) // NS_PER_SECOND)


@pytest.mark.asyncio
async def test_recorded_wide_spans_reproduce_out_of_window_offline(
    atlas_client: tuple[SkylitClient, respx.Route]
) -> None:
    client, route = atlas_client
    route.mock(
        side_effect=lambda request: httpx.Response(
            200, json=recorded_regular_history(request, RECORDED_CALENDAR)
        )
    )
    days = RECORDED_CALENDAR.sessions(*RECORDED_FAILED_BLOCK)

    def session_range(session: date) -> tuple[int, int]:
        return RECORDED_CALENDAR.rth_open(session), RECORDED_CALENDAR.rth_close(session)

    fetched = await AtlasBars(client).fetch_sessions(
        "QQQ",
        days,
        instrument="QQQ",
        contract="QQQ",
        session_range=session_range,
        interval_s=60,
        extended=False,
        futures=False,
        strict=True,
    )

    calls = [
        (int(call.request.url.params["from"]), int(call.request.url.params["to"]))
        for call in route.calls
    ]
    assert calls == list(RECORDED_HISTORY_SPANS[1:3])
    assert len(fetched) == len(days)
    assert {item.error_code for item in fetched.values()} == {"out_of_window"}
    assert fetched[date(2025, 7, 3)].error_code == "out_of_window"


@pytest.mark.asyncio
async def test_recorded_range_is_partitioned_at_early_closes_offline(
    atlas_client: tuple[SkylitClient, respx.Route], tmp_path: Path
) -> None:
    client, route = atlas_client
    route.mock(
        side_effect=lambda request: httpx.Response(
            200, json=recorded_regular_history(request, RECORDED_CALENDAR)
        )
    )
    sessions = RECORDED_CALENDAR.sessions(*FULL_RECORDED_RANGE)
    with DataCache(tmp_path / "cache", calendar=RECORDED_CALENDAR) as cache:
        pulled = await pull_equity_bars(
            AtlasBars(client),
            cache.equity_bars,
            RECORDED_CALENDAR,
            sessions,
            ["QQQ"],
        )
        assert pulled.complete
        for session in (date(2025, 7, 3), date(2025, 11, 28), date(2025, 12, 24)):
            rows = cache.equity_bars.read_session("QQQ", 60, session)
            close = RECORDED_CALENDAR.rth_close(session)
            assert len(rows) == 225
            assert all(row.open_ns < close and row.close_ns <= close for row in rows)

    calls = [
        (
            ny_datetime(int(call.request.url.params["from"]) * NS_PER_SECOND).date(),
            ny_datetime(int(call.request.url.params["to"]) * NS_PER_SECOND).date(),
        )
        for call in route.calls
    ]
    assert all(
        dict(call.request.url.params)["symbol"] == "QQQ"
        and dict(call.request.url.params)["resolution"] == "1"
        and dict(call.request.url.params)["extended"] == "false"
        for call in route.calls
    )
    assert calls == list(EXPECTED_SAFE_SPANS)
    assert len(pulled.results) == len(sessions)
    assert all(item.status == "complete" for item in pulled.results)
    estimate = estimate_equity_requests(sessions, ["QQQ"], calendar=RECORDED_CALENDAR)
    assert estimate["history_requests"] == len(EXPECTED_SAFE_SPANS)
    assert pulled.history_requests == len(EXPECTED_SAFE_SPANS)


@pytest.mark.asyncio
async def test_no_data_and_missing_rows_are_not_complete(
    atlas_client: tuple[SkylitClient, respx.Route]
) -> None:
    client, route = atlas_client
    route.mock(return_value=httpx.Response(200, json={"s": "no_data"}))
    (no_data,) = await fetch_equity_sessions(AtlasBars(client), CALENDAR, "QQQ", [REGULAR])
    assert no_data.status == "no_data"
    assert no_data.quality.missing_minutes

    route.mock(
        side_effect=lambda request: httpx.Response(
            200,
            json=history_body(request, missing=ny(REGULAR, 10, 0) // NS_PER_SECOND),
        )
    )
    (partial,) = await fetch_equity_sessions(AtlasBars(client), CALENDAR, "SPY", [REGULAR])
    assert partial.status == "incomplete"
    assert partial.quality.missing_minutes[0].minutes == 1


@pytest.mark.asyncio
async def test_malformed_and_duplicate_responses_fail_closed(
    atlas_client: tuple[SkylitClient, respx.Route]
) -> None:
    client, route = atlas_client
    malformed = {"s": "ok", "t": [1], "o": [], "h": [], "l": [], "c": [], "v": []}
    route.mock(return_value=httpx.Response(200, json=malformed))
    (bad,) = await fetch_equity_sessions(AtlasBars(client), CALENDAR, "QQQ", [REGULAR])
    assert bad.status == "incomplete"
    assert bad.error == "malformed_response"

    start = ny(REGULAR, 9, 30) // NS_PER_SECOND
    duplicate = {
        "s": "ok",
        "t": [start, start],
        "o": [1.0, 1.0],
        "h": [1.2, 1.2],
        "l": [0.8, 0.8],
        "c": [1.1, 1.1],
        "v": [1.0, 1.0],
    }
    route.mock(return_value=httpx.Response(200, json=duplicate))
    (repeated,) = await fetch_equity_sessions(AtlasBars(client), CALENDAR, "SPY", [REGULAR])
    assert repeated.status == "incomplete"
    assert repeated.quality.duplicate_timestamps


def test_equity_store_is_separate_atomic_and_round_trips_null_ticks(tmp_path: Path) -> None:
    with DataCache(tmp_path / "cache") as cache:
        store: EquityBarStore = cache.equity_bars
        rows = [bar("QQQ", ny(REGULAR, 9, 30)), bar("QQQ", ny(REGULAR, 9, 31))]
        store.write_session("QQQ", 60, REGULAR, rows)

        assert store.path("QQQ", 60, REGULAR) == (
            cache.root / "equity_bars" / "QQQ" / "60s" / "2026-03-05.parquet"
        )
        assert store.read_session("QQQ", 60, REGULAR) == rows
        assert store.status("QQQ", 60, REGULAR) == "complete"
        assert cache.bars.status("QQQ", 60, REGULAR) == "absent"
        record = store.record("QQQ", 60, REGULAR)
        assert record is not None
        assert record.rows == 2
        assert len(record.sha256 or "") == 64
        assert all(value is None for value in (store.read_session("QQQ", 60, REGULAR)[0].o_t,))


def test_equity_store_rejects_nonfinite_volume_before_writing(tmp_path: Path) -> None:
    with DataCache(tmp_path / "cache") as cache:
        store = cache.equity_bars
        invalid = replace(bar("QQQ", ny(REGULAR, 9, 30)), v=math.nan)

        with pytest.raises(ValueError, match="invalid OHLCV"):
            store.write_session("QQQ", 60, REGULAR, [invalid])

        assert store.status("QQQ", 60, REGULAR) == "absent"
        assert not store.path("QQQ", 60, REGULAR).exists()


def test_equity_store_rejects_nonfinite_cached_volume(tmp_path: Path) -> None:
    with DataCache(tmp_path / "cache") as cache:
        store = cache.equity_bars
        key = store.key("QQQ", 60, REGULAR)
        rows = (replace(bar("QQQ", ny(REGULAR, 9, 30)), v=math.inf),)
        data = store._encode(key, rows, "atlas")
        store.path("QQQ", 60, REGULAR).parent.mkdir(parents=True, exist_ok=True)
        store.path("QQQ", 60, REGULAR).write_bytes(data)
        cache.catalog.finish_equity_bars(
            key,
            status="complete",
            rows=1,
            source="atlas",
            sha256=sha256_hex(data),
        )

        with pytest.raises(CacheIntegrityError, match="invalid equity Bar"):
            store.read_session("QQQ", 60, REGULAR)


@pytest.mark.asyncio
async def test_invalid_cached_complete_is_marked_incomplete_when_refetch_fails(
    atlas_client: tuple[SkylitClient, respx.Route], tmp_path: Path
) -> None:
    client, route = atlas_client
    calls = 0

    def responses(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json=history_body(request))
        return httpx.Response(200, json={"s": "ok", "t": [1], "o": []})

    route.mock(side_effect=responses)
    with DataCache(tmp_path / "cache") as cache:
        store = cache.equity_bars
        first = await pull_equity_bars(
            AtlasBars(client), store, CALENDAR, [REGULAR], ["QQQ"]
        )
        assert first.complete
        valid_rows = store.read_session("QQQ", 60, REGULAR)
        invalid_rows = [replace(valid_rows[0], h=0.9), *valid_rows[1:]]
        data = store._encode(store.key("QQQ", 60, REGULAR), tuple(invalid_rows), "atlas")
        path = store.path("QQQ", 60, REGULAR)
        path.write_bytes(data)
        cache.catalog.finish_equity_bars(
            store.key("QQQ", 60, REGULAR),
            status="complete",
            rows=len(invalid_rows),
            source="atlas",
            sha256=sha256_hex(data),
        )

        second = await pull_equity_bars(
            AtlasBars(client), store, CALENDAR, [REGULAR], ["QQQ"]
        )

        assert not second.complete
        assert second.results[0].status == "incomplete"
        assert store.status("QQQ", 60, REGULAR) == "incomplete"


@pytest.mark.asyncio
async def test_complete_invalid_ohlc_is_refetched_instead_of_resumed(
    atlas_client: tuple[SkylitClient, respx.Route], tmp_path: Path
) -> None:
    client, route = atlas_client
    route.mock(side_effect=lambda request: httpx.Response(200, json=history_body(request)))
    with DataCache(tmp_path / "cache") as cache:
        store = cache.equity_bars
        first = await pull_equity_bars(
            AtlasBars(client), store, CALENDAR, [REGULAR], ["QQQ"]
        )
        assert first.complete
        valid_rows = store.read_session("QQQ", 60, REGULAR)
        invalid_rows = [replace(valid_rows[0], h=0.9), *valid_rows[1:]]
        data = store._encode(store.key("QQQ", 60, REGULAR), tuple(invalid_rows), "atlas")
        path = store.path("QQQ", 60, REGULAR)
        path.write_bytes(data)
        cache.catalog.finish_equity_bars(
            store.key("QQQ", 60, REGULAR),
            status="complete",
            rows=len(invalid_rows),
            source="atlas",
            sha256=sha256_hex(data),
        )
        before = route.call_count
        second = await pull_equity_bars(
            AtlasBars(client), store, CALENDAR, [REGULAR], ["QQQ"]
        )

    assert second.complete
    assert route.call_count == before + 1


@pytest.mark.asyncio
async def test_gap_resume_fetches_pending_runs_without_refetching_cached_middle(
    atlas_client: tuple[SkylitClient, respx.Route], tmp_path: Path
) -> None:
    client, route = atlas_client
    route.mock(side_effect=lambda request: httpx.Response(200, json=history_body(request)))
    with DataCache(tmp_path / "cache") as cache:
        store = cache.equity_bars
        first = await pull_equity_bars(
            AtlasBars(client), store, CALENDAR, [EARLY], ["QQQ"]
        )
        middle_path = store.path("QQQ", 60, EARLY)
        middle_bytes = middle_path.read_bytes()
        before = route.call_count

        second = await pull_equity_bars(
            AtlasBars(client), store, CALENDAR, [REGULAR, EARLY, LATE], ["QQQ"]
        )

    assert first.complete
    assert second.complete
    assert route.call_count == before + 2
    assert [
        int(call.request.url.params["from"]) for call in route.calls[before:]
    ] == [
        ny(REGULAR, 9, 30) // NS_PER_SECOND,
        ny(LATE, 9, 30) // NS_PER_SECOND,
    ]
    assert middle_path.read_bytes() == middle_bytes


@pytest.mark.asyncio
async def test_failed_retry_preserves_complete_sessions_on_both_sides(
    atlas_client: tuple[SkylitClient, respx.Route], tmp_path: Path
) -> None:
    client, route = atlas_client
    regular_start = ny(REGULAR, 9, 30) // NS_PER_SECOND
    late_start = ny(LATE, 9, 30) // NS_PER_SECOND

    def responses(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params["from"])
        if start in (regular_start, late_start):
            return httpx.Response(200, json=history_body(request))
        return httpx.Response(200, json={"s": "ok", "t": [start], "o": []})

    route.mock(side_effect=responses)
    with DataCache(tmp_path / "cache", calendar=CALENDAR) as cache:
        store = cache.equity_bars
        first = await pull_equity_bars(
            AtlasBars(client), store, CALENDAR, [REGULAR, LATE], ["QQQ"]
        )
        regular_path = store.path("QQQ", 60, REGULAR)
        late_path = store.path("QQQ", 60, LATE)
        regular_bytes = regular_path.read_bytes()
        late_bytes = late_path.read_bytes()
        second = await pull_equity_bars(
            AtlasBars(client), store, CALENDAR, [REGULAR, EARLY, LATE], ["QQQ"]
        )

        assert first.complete
        assert not second.complete
        assert store.status("QQQ", 60, REGULAR) == "complete"
        assert store.status("QQQ", 60, LATE) == "complete"
        assert store.status("QQQ", 60, EARLY) == "incomplete"
        assert regular_path.read_bytes() == regular_bytes
        assert late_path.read_bytes() == late_bytes
        assert second.history_requests == 1


@pytest.mark.asyncio
async def test_pull_resumes_valid_sessions_without_another_history_request(
    atlas_client: tuple[SkylitClient, respx.Route], tmp_path: Path
) -> None:
    client, route = atlas_client
    route.mock(side_effect=lambda request: httpx.Response(200, json=history_body(request)))
    with DataCache(tmp_path / "cache") as cache:
        first = await pull_equity_bars(
            AtlasBars(client), cache.equity_bars, CALENDAR, [REGULAR], ["QQQ"]
        )
        calls = route.call_count
        second = await pull_equity_bars(
            AtlasBars(client), cache.equity_bars, CALENDAR, [REGULAR], ["QQQ"]
        )
    assert first.complete
    assert second.complete
    assert route.call_count == calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "diagnostic"),
    [
        (
            lambda start, end: {
                "s": "ok",
                "t": [start + 30],
                "o": [1.0],
                "h": [1.2],
                "l": [0.8],
                "c": [1.1],
                "v": [1.0],
            },
            "off_grid_timestamps",
        ),
        (
            lambda start, end: {
                "s": "ok",
                "t": [end],
                "o": [1.0],
                "h": [1.2],
                "l": [0.8],
                "c": [1.1],
                "v": [1.0],
            },
            "out_of_rth_timestamps",
        ),
        (
            lambda start, end: {
                "s": "ok",
                "t": [start],
                "o": [1.0],
                "h": [1.0],
                "l": [0.8],
                "c": [1.1],
                "v": [1.0],
            },
            "error",
        ),
    ],
)
async def test_strict_quality_rejections_report_diagnostics(
    atlas_client: tuple[SkylitClient, respx.Route],
    payload: Callable[[int, int], dict[str, object]],
    diagnostic: str,
) -> None:
    client, route = atlas_client
    start = ny(REGULAR, 9, 30) // NS_PER_SECOND
    end = ny(REGULAR, 16, 0) // NS_PER_SECOND
    route.mock(
        return_value=httpx.Response(
            200,
            json=payload(start, end),
        )
    )

    (result,) = await fetch_equity_sessions(AtlasBars(client), CALENDAR, "QQQ", [REGULAR])

    assert result.status == "incomplete"
    assert result.quality.missing_minutes
    if diagnostic == "error":
        assert result.error == "invalid_ohlc"
    else:
        assert getattr(result.quality, diagnostic)


def test_corrupt_equity_file_is_rejected_by_catalog_hash(tmp_path: Path) -> None:
    with DataCache(tmp_path / "cache") as cache:
        store = cache.equity_bars
        rows = [bar("QQQ", ny(REGULAR, 9, 30))]
        store.write_session("QQQ", 60, REGULAR, rows)
        path = store.path("QQQ", 60, REGULAR)
        original = path.read_bytes()
        path.write_bytes(original + b"corrupt")

        with pytest.raises(CacheIntegrityError, match="sha256"):
            store.read_session("QQQ", 60, REGULAR)


def test_failed_equity_write_keeps_file_and_marks_coverage_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with DataCache(tmp_path / "cache") as cache:
        store = cache.equity_bars
        rows = [bar("QQQ", ny(REGULAR, 9, 30))]
        store.write_session("QQQ", 60, REGULAR, rows)
        path = store.path("QQQ", 60, REGULAR)
        original = path.read_bytes()

        def fail_write(_path: Path, _data: bytes) -> None:
            raise OSError("disk full")

        monkeypatch.setattr("fse.data.aux_stores.write_atomic", fail_write)
        with pytest.raises(CacheWriteError, match="disk full"):
            store.write_session("QQQ", 60, REGULAR, rows)

        assert path.read_bytes() == original
        assert store.status("QQQ", 60, REGULAR) == "incomplete"
