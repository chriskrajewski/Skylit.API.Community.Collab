"""Mocked end-to-end coverage for the dedicated equity-bar command."""

from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from fse import cli
from fse.calendars import load_exchange_calendar
from fse.data.cache import DataCache
from fse.engine.types import Bar
from fse.skylit.endpoints import Host
from fse.timekit import NS_PER_SECOND

PROJECT = Path(__file__).resolve().parents[2]
CALENDARS = PROJECT / "calendars"


@pytest.mark.integration
def test_two_symbols_and_two_sessions_write_separate_coverage(
    tmp_path: Path, respx_router: respx.MockRouter
) -> None:
    calendar = load_exchange_calendar(CALENDARS / "exchange_calendar.yaml").sessions
    sessions = (date(2026, 3, 5), date(2026, 3, 6))
    account = respx_router.route(
        method="GET", scheme="https", host=Host.API.value, path="/v1/account"
    ).mock(return_value=httpx.Response(200, json={"data": {"limits": {}}}))
    config = respx_router.route(
        method="GET", scheme="https", host=Host.ATLAS.value, path="/v1/config"
    ).mock(return_value=httpx.Response(200, json={"max_fetch_trading_days": {"1": 90}}))

    def history(request: httpx.Request) -> httpx.Response:
        timestamps = [
            value // NS_PER_SECOND
            for session in sessions
            for value in range(
                calendar.rth_open(session),
                calendar.rth_close(session),
                60 * NS_PER_SECOND,
            )
        ]
        prices = [100.0] * len(timestamps)
        return httpx.Response(
            200,
            json={
                "s": "ok",
                "t": timestamps,
                "o": prices,
                "h": [100.2] * len(timestamps),
                "l": [99.8] * len(timestamps),
                "c": [100.1] * len(timestamps),
                "v": [1.0] * len(timestamps),
            },
        )

    history_route = respx_router.route(
        method="GET", scheme="https", host=Host.ATLAS.value, path="/v1/history"
    ).mock(side_effect=history)
    out = tmp_path / "out"
    code = cli.run(
        [
            "pull-equity-bars",
            "--start",
            "2026-03-05",
            "--end",
            "2026-03-06",
            "--symbols",
            "QQQ,SPY",
            "--calendar-dir",
            str(CALENDARS),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--out",
            str(out),
        ],
        project_dir=None,
        environ={"SKYLIT_API_KEY": "fake-skylit-key-0000"},
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert code == 0
    assert account.call_count == 1
    assert config.call_count == 1
    assert history_route.call_count == 2
    manifests = list(out.glob("equity_coverage_*.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest["outcome"] == "completed"
    assert manifest["symbols"] == ["QQQ", "SPY"]
    assert manifest["by_symbol"]["QQQ"]["sessions_complete"] == 2
    assert manifest["by_symbol"]["SPY"]["sessions_complete"] == 2
    assert (
        tmp_path / "cache" / "equity_bars" / "QQQ" / "60s" / "2026-03-05.parquet"
    ).exists()


@pytest.mark.integration
def test_pending_sessions_are_scheduled_oldest_first_across_symbols(
    tmp_path: Path, respx_router: respx.MockRouter
) -> None:
    calendar = load_exchange_calendar(CALENDARS / "exchange_calendar.yaml").sessions
    sessions = (date(2026, 3, 5), date(2026, 3, 6))
    with DataCache(tmp_path / "cache", calendar=calendar) as cache:
        rows = tuple(
            Bar(
                "QQQ",
                "QQQ",
                60,
                open_ns,
                open_ns + 60 * NS_PER_SECOND,
                100.0,
                100.2,
                99.8,
                100.1,
                1.0,
                None,
                None,
                None,
                None,
                "atlas",
            )
            for open_ns in range(
                calendar.rth_open(sessions[0]),
                calendar.rth_close(sessions[0]),
                60 * NS_PER_SECOND,
            )
        )
        cache.equity_bars.write_session("QQQ", 60, sessions[0], rows)

    respx_router.route(
        method="GET", scheme="https", host=Host.API.value, path="/v1/account"
    ).mock(return_value=httpx.Response(200, json={"data": {"limits": {}}}))
    respx_router.route(
        method="GET", scheme="https", host=Host.ATLAS.value, path="/v1/config"
    ).mock(return_value=httpx.Response(200, json={"max_fetch_trading_days": {"1": 90}}))
    order: list[tuple[str, int, int]] = []

    def history(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        symbol = params["symbol"]
        start, end = int(params["from"]), int(params["to"])
        order.append((symbol, start, end))
        timestamps = [
            value // NS_PER_SECOND
            for session in sessions
            for value in range(
                calendar.rth_open(session),
                calendar.rth_close(session),
                60 * NS_PER_SECOND,
            )
            if start <= value // NS_PER_SECOND < end
        ]
        prices = [100.0] * len(timestamps)
        return httpx.Response(
            200,
            json={
                "s": "ok",
                "t": timestamps,
                "o": prices,
                "h": [100.2] * len(timestamps),
                "l": [99.8] * len(timestamps),
                "c": [100.1] * len(timestamps),
                "v": [1.0] * len(timestamps),
            },
        )

    history_route = respx_router.route(
        method="GET", scheme="https", host=Host.ATLAS.value, path="/v1/history"
    ).mock(side_effect=history)
    code = cli.run(
        [
            "pull-equity-bars",
            "--start",
            "2026-03-05",
            "--end",
            "2026-03-06",
            "--symbols",
            "QQQ,SPY",
            "--calendar-dir",
            str(CALENDARS),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--out",
            str(tmp_path / "out"),
        ],
        project_dir=None,
        environ={"SKYLIT_API_KEY": "fake-skylit-key-0000"},
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert code == 0
    assert history_route.call_count == 2
    assert [(symbol, start) for symbol, start, _end in order] == [
        ("SPY", calendar.rth_open(sessions[0]) // NS_PER_SECOND),
        ("QQQ", calendar.rth_open(sessions[1]) // NS_PER_SECOND),
    ]
    assert order[0][2] == calendar.rth_close(sessions[1]) // NS_PER_SECOND
    assert order[1][2] == calendar.rth_close(sessions[1]) // NS_PER_SECOND
