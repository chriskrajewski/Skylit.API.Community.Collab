"""Unit tests for ``fse.skylit.endpoints``: documented paths, parameters and caps.

No request is sent here; the builders are pure.

**Validates: Requirements 2.4, 3.3, 3.4**
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from fse.data.cache import HeatmapView
from fse.skylit import endpoints as ep
from fse.skylit.endpoints import Host
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND

VIEW = HeatmapView()
T = int(datetime(2026, 3, 5, 14, 30, tzinfo=UTC).timestamp()) * NS_PER_SECOND


def test_endpoint_table_matches_the_docs() -> None:
    table = {e.name: (e.host, e.path, e.credits, e.historical) for e in ep.ENDPOINTS}
    assert table == {
        "account": (Host.API, "/v1/account", 0, False),
        "symbols": (Host.API, "/v1/symbols", 0, False),
        "heatmap": (Host.API, "/v1/heatmap", 1, False),
        "historical": (Host.API, "/v1/historical", 5, True),
        "historical_range": (Host.API, "/v1/historical/range", 25, True),
        "stream": (Host.API, "/v1/stream", 1, False),
        "gex_levels": (Host.API, "/v1/gex/levels", 1, False),
        "dark_pool_trades": (Host.API, "/v1/dark-pool/trades", 5, False),
        "atlas_config": (Host.ATLAS, "/v1/config", 0, False),
        "atlas_search": (Host.ATLAS, "/v1/search", 1, False),
        "atlas_history": (Host.ATLAS, "/v1/history", 1, False),
    }
    assert ep.HISTORICAL_RANGE.url == "https://api.skylit.ai/v1/historical/range"
    assert ep.ATLAS_HISTORY.url == "https://atlas-api.skylit.ai/v1/history"
    assert all(
        e.doc_url.startswith("https://www.skylit.ai/docs/api-reference/") for e in ep.ENDPOINTS
    )


def test_only_the_two_replay_paths_on_the_api_host_are_replays() -> None:
    assert ep.is_replay(Host.API, "/v1/historical")
    assert ep.is_replay(Host.API, "/v1/historical/range")
    assert not ep.is_replay(Host.API, "/v1/heatmap")
    assert not ep.is_replay(Host.ATLAS, "/v1/historical")


def test_historical_params_send_the_whole_view() -> None:
    params = ep.historical_params(["SPX", "SPY"], at_ns=T, metric="vanna", view=VIEW)
    assert params == {
        "symbols": "SPX,SPY",
        "at": "2026-03-05T14:30:00Z",
        "metric": "vanna",
        "maxStrikes": "92",
        "maxExpirations": "5",
        "includeEmpty": "false",
    }


def test_range_params_never_send_include_empty_and_allow_exactly_15_minutes() -> None:
    view = HeatmapView(max_strikes="all", include_empty=True)
    params = ep.range_params(
        ["SPX"], from_ns=T, to_ns=T + 15 * NS_PER_MINUTE, metric="gamma", view=view
    )
    assert params == {
        "symbols": "SPX",
        "from": "2026-03-05T14:30:00Z",
        "to": "2026-03-05T14:45:00Z",
        "metric": "gamma",
        "maxStrikes": "all",
        "maxExpirations": "5",
    }


@pytest.mark.parametrize(
    ("symbols", "to_offset_ns", "message"),
    [
        (["SPX"], 15 * NS_PER_MINUTE + 1, "15 minutes"),
        (["SPX"], -1, "before its start"),
        (["A", "B", "C", "D", "E", "F"], 0, "at most 5"),
    ],
)
def test_range_params_reject_over_cap_requests(
    symbols: list[str], to_offset_ns: int, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        ep.range_params(symbols, from_ns=T, to_ns=T + to_offset_ns, metric="gamma", view=VIEW)


def test_expirations_supersede_max_expirations() -> None:
    view = HeatmapView(expirations=("2026-03-05", "2026-03-06"))
    params = ep.heatmap_params(["QQQ"], metric="gamma", view=view)
    assert params["expirations"] == "2026-03-05,2026-03-06"
    assert "maxExpirations" not in params


@pytest.mark.parametrize(
    "symbols",
    [[], ["SPX", "SPX"], ["SPX,SPY"], [" SPX"], [""], [f"S{i}" for i in range(11)]],
)
def test_symbol_lists_are_checked_before_sending(symbols: list[str]) -> None:
    with pytest.raises(ValueError, match="symbol"):
        ep.heatmap_params(symbols, metric="gamma", view=VIEW)


def test_a_bare_string_is_not_a_symbol_list() -> None:
    with pytest.raises(TypeError):
        ep.heatmap_params("SPX", metric="gamma", view=VIEW)


def test_metric_and_history_start_are_checked() -> None:
    with pytest.raises(ValueError, match="metric"):
        ep.gex_levels_params(["SPX"], metric="delta", view=VIEW)
    before = int(datetime(2023, 3, 27, 23, 59, 59, tzinfo=UTC).timestamp()) * NS_PER_SECOND
    with pytest.raises(ValueError, match="2023-03-28"):
        ep.historical_params(["SPX"], at_ns=before, metric="gamma", view=VIEW)


def test_stream_params_select_v2_and_resume_cursor() -> None:
    params = ep.stream_params(["SPY", "QQQ"], metric="gamma", view=VIEW, last_event_id="SPY:1")
    assert params["format"] == "v2"
    assert params["symbols"] == "SPY,QQQ"
    assert params["lastEventId"] == "SPY:1"


def test_dark_pool_params_and_caps() -> None:
    params = ep.dark_pool_trades_params(
        ["SPY", "QQQ"], date_start=date(2026, 1, 1), date_end=date(2026, 1, 31), min_notional=0
    )
    assert params == {
        "tickers": "SPY,QQQ",
        "date_start": "2026-01-01",
        "date_end": "2026-01-31",
        "limit": "5000",
        "offset": "0",
        "order": "asc",
        "min_notional": "0",
    }
    with pytest.raises(ValueError, match="31 trade dates"):
        ep.dark_pool_trades_params(["SPY"], date_start=date(2026, 1, 1), date_end=date(2026, 2, 1))
    with pytest.raises(ValueError, match="offset"):
        ep.dark_pool_trades_params(
            ["SPY"], date_start=date(2026, 1, 1), date_end=date(2026, 1, 1), offset=50_001
        )
    with pytest.raises(ValueError, match="limit"):
        ep.dark_pool_trades_params(
            ["SPY"], date_start=date(2026, 1, 1), date_end=date(2026, 1, 1), limit=0
        )


@pytest.mark.parametrize(
    ("value", "text"), [(1_000_000, "1000000"), (1e6, "1000000"), (2.5, "2.5"), (1e-7, "0.0000001")]
)
def test_min_notional_is_written_without_an_exponent(value: float, text: str) -> None:
    params = ep.dark_pool_trades_params(
        ["SPY"], date_start=date(2026, 1, 2), date_end=date(2026, 1, 2), min_notional=value
    )
    assert params["min_notional"] == text


def test_atlas_params() -> None:
    assert ep.atlas_history_params(
        "ES1!", resolution="1", from_s=1_790_602_200, to_s=1_790_625_600, extended=True
    ) == {
        "symbol": "ES1!",
        "resolution": "1",
        "from": "1790602200",
        "to": "1790625600",
        "extended": "true",
    }
    with pytest.raises(ValueError, match="resolution"):
        ep.atlas_history_params("SPY", resolution="7", from_s=0, to_s=60)
    with pytest.raises(ValueError, match="after"):
        ep.atlas_history_params("SPY", resolution="1", from_s=60, to_s=60)
    assert ep.atlas_search_params(" ES ", limit=10) == {"query": "ES", "limit": "10"}
    with pytest.raises(ValueError, match="limit"):
        ep.atlas_search_params("ES", limit=501)


@pytest.mark.parametrize(
    ("t", "text"),
    [
        (0, "1970-01-01T00:00:00Z"),
        (T + 500_000_000, "2026-03-05T14:30:00.5Z"),
        (T + 1, "2026-03-05T14:30:00.000000001Z"),
        (-1, "1969-12-31T23:59:59.999999999Z"),
    ],
)
def test_format_rfc3339(t: int, text: str) -> None:
    assert ep.format_rfc3339(t) == text
