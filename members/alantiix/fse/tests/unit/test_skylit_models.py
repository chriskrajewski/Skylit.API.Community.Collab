"""Unit tests for ``fse.skylit.models``: documented shapes, Snapshot conversion, safe errors.

Bodies are synthetic, shaped like the examples in the Skylit OpenAPI specs.

**Validates: Requirements 2.1, 2.2, 3.5, 3.10**
"""

from __future__ import annotations

import copy
import json
from datetime import date
from typing import Any

import pytest

from fse.engine.types import DarkPoolPrint
from fse.skylit.models import (
    AccountInfo,
    AccountLimits,
    AtlasConfig,
    AtlasHistoryError,
    DarkPoolTradesResponse,
    HeatmapResponse,
    HistoryBars,
    HistoryNoData,
    LevelsResponse,
    MalformedResponseError,
    RangeResponse,
    SearchResults,
    StreamClosed,
    StreamConnected,
    StreamSnapshot,
    StreamUnknown,
    SymbolCatalog,
    parse_atlas_history,
    parse_event_id,
    parse_stream_event,
)
from fse.timekit import parse_rfc3339

VIEW_ID = "0123456789abcdef"
ODD = 765432.125  # a distinctive value: no error message may contain it

HISTORICAL: dict[str, Any] = {
    "data": {
        "symbols": [
            {
                "symbol": "SPY",
                "asOf": "2026-03-05T15:01:00Z",
                "spot": 512.4,
                "previousClose": 510.02,
                "priceChange": 2.38,
                "priceChangePercent": 0.47,
                "expirations": ["2026-03-05", "2026-03-06"],
                "strikes": [
                    {"strike": 510, "value": 88010.0, "nodeType": "normal"},
                    {"strike": 512, "value": 1500200.0, "nodeType": "king"},
                    {"strike": 515, "value": -410000.5, "nodeType": "gatekeeper"},
                ],
            }
        ]
    },
    "meta": {"metric": "gamma", "resolution": "1s", "mode": "historical", "cached": False},
}

RANGE: dict[str, Any] = {
    "data": {
        "from": "2026-03-05T14:30:00Z",
        "to": "2026-03-05T14:30:02Z",
        "symbols": [
            {
                "symbol": "SPY",
                "axes": [
                    {"id": 0, "strikes": [510, 512, 515], "expirations": ["2026-03-05"]},
                    {"id": 7, "strikes": [512, 515], "expirations": ["2026-03-05"]},
                ],
                "frames": [
                    {
                        "asOf": "2026-03-05T14:30:00.000Z",
                        "axis": 0,
                        "spot": 512.4,
                        "previousClose": 510.02,
                        "values": [88010.0, 1500200.0, 410000.0],
                    },
                    {
                        "asOf": "2026-03-05T14:30:01.000Z",
                        "axis": 7,
                        "spot": 512.43,
                        "previousClose": 510.02,
                        "values": [1502113.2, 409877.1],
                    },
                ],
            }
        ],
    },
    "meta": {"metric": "vanna", "resolution": "1s", "mode": "historical", "cached": False},
}


def _messages_have_no_value(exc: pytest.ExceptionInfo[MalformedResponseError]) -> None:
    assert str(ODD) not in str(exc.value)
    assert "765432" not in str(exc.value)


# ---------------------------------------------------------------- account and symbols


def test_account_limits_and_info() -> None:
    body = {
        "data": {
            "customerId": "fake-customer-0000",
            "status": "active",
            "apiEligible": True,
            "unlimited": False,
            "creditsBalance": 4321,
            "balanceUsd": 4.321,
            "limits": {"requestsPerMinute": 600, "historicalInFlight": 2, "activeKeys": 5},
        }
    }
    info = AccountInfo.parse(body)
    assert info.limits.requests_per_minute == 600
    assert info.limits.historical_in_flight == 2
    assert info.limits.active_keys == 5
    assert info.credits_balance == 4321
    assert "4321" not in repr(info)
    assert "fake-customer-0000" not in repr(info)
    assert not hasattr(info, "customer_id")


@pytest.mark.parametrize("bad", [0, -3, True, "120", 120.5, None, [120]])
def test_unusable_limits_read_as_none(bad: object) -> None:
    limits = AccountLimits.parse({"requestsPerMinute": bad, "historicalInFlight": bad})
    assert limits.requests_per_minute is None
    assert limits.historical_in_flight is None
    assert AccountLimits.parse("not an object") == AccountLimits()


def test_account_without_data_is_malformed() -> None:
    with pytest.raises(MalformedResponseError, match="data"):
        AccountInfo.parse({"error": {"code": "x", "message": "y"}})


def test_symbol_catalog() -> None:
    body = {
        "data": {
            "symbols": [
                {
                    "symbol": "SPX",
                    "isIndex": True,
                    "metrics": ["gamma", "vanna"],
                    "history": {"from": "2023-03-28", "to": "2026-03-05"},
                },
                {
                    "symbol": "NEWCO",
                    "isIndex": False,
                    "previousNames": ["OLDCO"],
                    "metrics": ["gamma"],
                    "history": None,
                },
            ]
        }
    }
    catalog = SymbolCatalog.parse(body)
    assert [s.symbol for s in catalog.symbols] == ["SPX", "NEWCO"]
    spx = catalog.get("SPX")
    assert spx is not None
    assert spx.history is not None
    assert spx.history.first == date(2023, 3, 28)
    assert catalog.get("OLDCO") is catalog.symbols[1]
    assert catalog.get("ZZZZ") is None
    assert catalog.first_history_dates() == {"SPX": date(2023, 3, 28)}

    missing = copy.deepcopy(body)
    del missing["data"]["symbols"][0]["history"]
    with pytest.raises(MalformedResponseError, match=r"data\.symbols\[0\]\.history is missing"):
        SymbolCatalog.parse(missing)


# ---------------------------------------------------------------- heatmaps


def test_historical_board_becomes_an_exact_snapshot() -> None:
    response = HeatmapResponse.parse(HISTORICAL, where="GET /v1/historical")
    [snap] = response.snapshots(view_id=VIEW_ID, source_endpoint="historical")
    assert snap.symbol == "SPY"
    assert snap.metric == "gamma"
    assert snap.resolution == "1s"
    assert snap.as_of_raw == "2026-03-05T15:01:00Z"
    assert snap.as_of_ns == parse_rfc3339("2026-03-05T15:01:00Z")
    assert snap.strikes == (510.0, 512.0, 515.0)
    assert snap.values == (88010.0, 1500200.0, -410000.5)
    assert snap.node_types == ("normal", "king", "gatekeeper")
    assert snap.expirations == ("2026-03-05", "2026-03-06")
    assert snap.previous_close == 510.02
    assert json.loads(snap.extra_json) == {"priceChange": 2.38, "priceChangePercent": 0.47}


def test_live_board_keeps_velocity_and_unknown_fields_in_extra_json() -> None:
    body = copy.deepcopy(HISTORICAL)
    board = body["data"]["symbols"][0]
    board["strikes"][1]["velocityPct"] = 12.4
    board["newField"] = {"x": 1}
    del board["strikes"][2]["nodeType"]  # stored history may lack labels (OQ3)
    response = HeatmapResponse.parse(body)
    assert response.symbols[0].strikes[1].velocity_pct == 12.4
    [snap] = response.snapshots(view_id=VIEW_ID, source_endpoint="heatmap")
    assert snap.node_types == ("normal", "king", None)
    extra = json.loads(snap.extra_json)
    assert extra["newField"] == {"x": 1}
    assert extra["strikes"] == [{}, {"velocityPct": 12.4}, {}]


@pytest.mark.parametrize(
    ("mutate", "where"),
    [
        (lambda b: b["data"]["symbols"][0]["strikes"][0].update(value=float("nan")), "value"),
        (lambda b: b["data"]["symbols"][0]["strikes"][0].update(value=True), "value"),
        (lambda b: b["data"]["symbols"][0].update(asOf="yesterday"), "asOf"),
        (lambda b: b["meta"].update(resolution="5s"), "resolution"),
        (lambda b: b["data"]["symbols"][0]["strikes"].append({"strike": ODD}), "value"),
    ],
)
def test_bad_heatmap_fields_are_named_without_values(mutate: Any, where: str) -> None:
    body = copy.deepcopy(HISTORICAL)
    mutate(body)
    with pytest.raises(MalformedResponseError, match=where) as exc:
        HeatmapResponse.parse(body)
    _messages_have_no_value(exc)


# ---------------------------------------------------------------- range


def test_range_frames_take_strikes_from_their_axis() -> None:
    response = RangeResponse.parse(RANGE)
    assert response.from_ns == parse_rfc3339("2026-03-05T14:30:00Z")
    snaps = response.snapshots(view_id=VIEW_ID)["SPY"]
    assert [s.as_of_raw for s in snaps] == ["2026-03-05T14:30:00.000Z", "2026-03-05T14:30:01.000Z"]
    assert snaps[0].strikes == (510.0, 512.0, 515.0)
    assert snaps[1].strikes == (512.0, 515.0)
    assert snaps[1].values == (1502113.2, 409877.1)
    assert all(s.node_types is None and s.source_endpoint == "range" for s in snaps)
    assert all(s.metric == "vanna" and s.extra_json == "{}" for s in snaps)


def test_range_frame_naming_a_missing_axis_is_malformed() -> None:
    body = copy.deepcopy(RANGE)
    body["data"]["symbols"][0]["frames"][1]["axis"] = 3
    with pytest.raises(MalformedResponseError, match=r"frames\[1\]\.axis names no listed axis"):
        RangeResponse.parse(body)


def test_range_frame_with_the_wrong_value_count_is_malformed() -> None:
    body = copy.deepcopy(RANGE)
    body["data"]["symbols"][0]["frames"][0]["values"].append(ODD)
    with pytest.raises(MalformedResponseError, match="one value per axis strike") as exc:
        RangeResponse.parse(body)
    _messages_have_no_value(exc)


def test_repeated_axis_id_is_malformed() -> None:
    body = copy.deepcopy(RANGE)
    body["data"]["symbols"][0]["axes"][1]["id"] = 0
    with pytest.raises(MalformedResponseError, match="repeats"):
        RangeResponse.parse(body)


# ---------------------------------------------------------------- stream


def test_stream_events() -> None:
    connected = parse_stream_event(
        "connected",
        json.dumps({"format": "v2", "symbols": ["SPY"], "metric": "gamma", "creditsPerMinute": 1}),
    )
    assert isinstance(connected, StreamConnected)
    assert connected.symbols == ("SPY",)

    board = dict(HISTORICAL["data"]["symbols"][0], lagged=True)
    event = parse_stream_event("snapshot", json.dumps(board), event_id="SPY:1790000000123")
    assert isinstance(event, StreamSnapshot)
    assert event.lagged
    snap = event.to_snapshot(metric="gamma", view_id=VIEW_ID)
    assert snap.source_endpoint == "stream"
    assert snap.resolution == "1s"
    assert "lagged" not in json.loads(snap.extra_json)

    assert parse_stream_event("closed", '{"reason": "key_revoked"}') == StreamClosed("key_revoked")
    assert isinstance(parse_stream_event("initial_data", "{}"), StreamUnknown)
    with pytest.raises(MalformedResponseError, match="not JSON"):
        parse_stream_event("credits", "{nope")
    assert parse_event_id("SPY:1790000000123,QQQ:1790000000456") == {
        "SPY": 1790000000123,
        "QQQ": 1790000000456,
    }


# ---------------------------------------------------------------- levels and dark pool


def test_gex_levels() -> None:
    level = {"strike": 512, "value": 1500200.0, "nodeType": "king", "distancePct": -0.08}
    body = {
        "data": {
            "symbols": [
                {
                    "symbol": "SPY",
                    "asOf": "2026-03-05T15:01:00Z",
                    "spot": 512.4,
                    "previousClose": 510.02,
                    "kingNode": level,
                    "levels": [level],
                    "summary": {
                        "netExposure": 1.5e6,
                        "positiveWall": level,
                        "negativeWall": None,
                        "flip": {"level": 508.5, "distancePct": -0.76},
                        "lowStrike": 470,
                        "highStrike": 555,
                    },
                }
            ]
        },
        "meta": {"metric": "gamma", "resolution": "1s", "mode": "live", "cached": True},
    }
    [spy] = LevelsResponse.parse(body).symbols
    assert spy.king_node is not None
    assert spy.king_node.node_type == "king"
    assert spy.summary is not None
    assert spy.summary.negative_wall is None
    assert spy.summary.flip is not None
    assert spy.summary.flip.level == 508.5


def test_dark_pool_page() -> None:
    body = {
        "data": [
            {
                "timestamp": "2026-03-05T14:31:05.123Z",
                "ticker": "SPY",
                "price": 512.1,
                "size": 25000,
                "notional": 12802500.0,
                "venue": "FINN",
                "sector": "Funds",
                "industry": "ETF",
            }
        ],
        "meta": {
            "timestamp": "2026-03-05T21:00:00Z",
            "requestId": "abc",
            "limit": 1,
            "offset": 0,
            "count": 1,
            "hasMore": True,
        },
    }
    response = DarkPoolTradesResponse.parse(body)
    assert response.page.has_more
    assert response.prints[0].to_print() == DarkPoolPrint(
        ticker="SPY",
        ts_ns=parse_rfc3339("2026-03-05T14:31:05.123Z"),
        price=512.1,
        size=25000,
        notional=12802500.0,
        venue="FINN",
    )


# ---------------------------------------------------------------- Atlas


def test_atlas_config_search_and_history() -> None:
    config = AtlasConfig.parse(
        {
            "supported_resolutions": ["1", "60", "D"],
            "max_fetch_trading_days": {"1": 90, "60": 720},
            "symbols_types": [{"name": "futures", "value": "futures"}],
        }
    )
    assert config.max_days("1") == 90
    assert config.max_days("W", default=2600) == 2600
    assert config.symbols_types == ("futures",)

    results = SearchResults.parse(
        [
            {
                "symbol": "ES1!",
                "ticker": "ES1!",
                "description": "E-mini",
                "exchange": "CME",
                "type": "futures",
            }
        ]
    )
    assert results.results[0].symbol == "ES1!"

    bars = parse_atlas_history(
        {
            "s": "ok",
            "t": [60, 120],
            "o": [1, 2],
            "h": [1.5, 2.5],
            "l": [0.5, 1.5],
            "c": [1.25, 2.25],
            "v": [10, 20],
        }
    )
    assert isinstance(bars, HistoryBars)
    assert len(bars) == 2
    assert bars.open_ns() == (60_000_000_000, 120_000_000_000)
    assert parse_atlas_history({"s": "no_data", "nextTime": 30}) == HistoryNoData(30)
    with pytest.raises(MalformedResponseError, match="one value per bar"):
        parse_atlas_history({"s": "ok", "t": [60], "o": [], "h": [], "l": [], "c": [], "v": []})

    error = AtlasHistoryError.parse(
        {"s": "error", "errmsg": "too wide", "requested_days": 171, "max_days": 90}
    )
    assert (error.requested_days, error.max_days) == (171, 90)
