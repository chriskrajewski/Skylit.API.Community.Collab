"""Skylit hosts, endpoints and query parameters (design §2 and "Research findings").

Sources (official Skylit docs; content rephrased for compliance with licensing
restrictions):

- Hosts, bearer authentication and the ``data``/``meta`` envelope:
  https://www.skylit.ai/docs/api-reference/introduction
- Limits, retryable statuses and concurrency:
  https://www.skylit.ai/docs/api-reference/rate-limits-and-retries
- OpenAPI specs (paths, parameters, caps and credit costs):
  https://www.skylit.ai/docs/openapi.yaml (Heatseeker),
  https://www.skylit.ai/docs/flowseeker-openapi.yaml (dark pool) and
  https://www.skylit.ai/docs/atlas-openapi.yaml (Atlas)
- One page per endpoint, in each :class:`Endpoint`'s ``doc_url``.

Two hosts serve everything the Project uses: ``api.skylit.ai`` (Heatseeker
and Flowseeker) and ``atlas-api.skylit.ai`` (Atlas bars and symbol search).
One per-key request limit covers both (``fse.skylit.ratelimit``).

The builders return query parameters as ``dict[str, str]`` in a fixed order,
ready for ``SkylitClient.get``. They check every documented cap before
anything is sent, because Skylit answers an over-cap request with ``400`` and
never shortens it: at most 10 distinct symbols per heatmap-family call, 5 per
``/v1/historical/range`` call, a range window of at most 15 minutes,
``maxStrikes`` 1 to 1000 or ``all``, ``maxExpirations`` 1 to 60 or ``all``,
dark-pool spans of at most 31 trade dates with ``limit`` up to 5,000 and
``offset`` up to 50,000, and Atlas search ``limit`` 1 to 500. A builder never
takes or returns the API key: the key travels only in the ``Authorization``
header.

``/v1/historical/range`` documents no ``includeEmpty`` parameter, so the range
builder never sends it (design OQ6); the other heatmap builders always send
the view's ``includeEmpty``. Instants go out as RFC 3339 UTC (``...Z``) with
the fractional seconds trimmed.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Final, Literal, Protocol

from fse.engine.types import METRICS
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, Instant

__all__ = [
    "ACCOUNT",
    "ATLAS_CONFIG",
    "ATLAS_HISTORY",
    "ATLAS_RESOLUTIONS",
    "ATLAS_SEARCH",
    "DARK_POOL_MAX_LIMIT",
    "DARK_POOL_MAX_OFFSET",
    "DARK_POOL_MAX_SPAN_DAYS",
    "DARK_POOL_MAX_TICKERS",
    "DARK_POOL_TRADES",
    "ENDPOINTS",
    "GEX_LEVELS",
    "HEATMAP",
    "HISTORICAL",
    "HISTORICAL_RANGE",
    "HISTORY_START",
    "MAX_EXPIRATIONS_CAP",
    "MAX_STRIKES_CAP",
    "MAX_SYMBOLS_PER_CALL",
    "RANGE_MAX_SYMBOLS",
    "RANGE_MAX_WINDOW_NS",
    "REPLAY_PATHS",
    "SEARCH_MAX_LIMIT",
    "STREAM",
    "SYMBOLS",
    "Endpoint",
    "Host",
    "ViewParams",
    "atlas_history_params",
    "atlas_search_params",
    "dark_pool_trades_params",
    "format_rfc3339",
    "gex_levels_params",
    "heatmap_params",
    "historical_params",
    "is_replay",
    "range_params",
    "stream_params",
]


class Host(StrEnum):
    """A Skylit API host. The value is the host name the Fetch_Log records."""

    API = "api.skylit.ai"
    """Heatseeker and Flowseeker (``flow-api.skylit.ai`` is an alias of the same API)."""
    ATLAS = "atlas-api.skylit.ai"
    """Atlas OHLCV bars, symbol search and datafeed configuration."""

    @property
    def base_url(self) -> str:
        return f"https://{self.value}"


@dataclass(frozen=True, slots=True)
class Endpoint:
    """One documented ``GET`` endpoint.

    ``credits`` is the documented cost of one successful call (failed calls
    are refunded). ``historical`` marks the historical replays that
    ``limits.historicalInFlight`` caps (Req 2.4).
    """

    name: str
    host: Host
    path: str
    credits: int
    historical: bool
    doc_url: str

    @property
    def url(self) -> str:
        return self.host.base_url + self.path


_DOCS: Final = "https://www.skylit.ai/docs/api-reference"

ACCOUNT: Final = Endpoint(
    "account", Host.API, "/v1/account", 0, False, f"{_DOCS}/account/your-balance-and-limits"
)
SYMBOLS: Final = Endpoint(
    "symbols", Host.API, "/v1/symbols", 0, False, f"{_DOCS}/meta/symbol-catalog"
)
HEATMAP: Final = Endpoint(
    "heatmap",
    Host.API,
    "/v1/heatmap",
    1,
    False,
    f"{_DOCS}/heatmap/live-per-strike-heatmap-one-or-more-symbols",
)
HISTORICAL: Final = Endpoint(
    "historical",
    Host.API,
    "/v1/historical",
    5,
    True,
    f"{_DOCS}/heatmap/replay-per-strike-heatmap-at-a-past-instant-one-or-more-symbols",
)
HISTORICAL_RANGE: Final = Endpoint(
    "historical_range",
    Host.API,
    "/v1/historical/range",
    25,
    True,
    f"{_DOCS}/heatmap/replay-every-snapshot-in-a-window-up-to-15-minutes-5-symbols",
)
STREAM: Final = Endpoint(
    "stream",
    Host.API,
    "/v1/stream",
    1,  # per symbol to open, then 1 per symbol per minute open
    False,
    f"{_DOCS}/heatmap/live-sse-stream-up-to-10-symbols-per-connection",
)
GEX_LEVELS: Final = Endpoint(
    "gex_levels",
    Host.API,
    "/v1/gex/levels",
    1,
    False,
    f"{_DOCS}/heatmap/key-levels-classified-nodes-for-one-or-more-symbols",
)
DARK_POOL_TRADES: Final = Endpoint(
    "dark_pool_trades",
    Host.API,
    "/v1/dark-pool/trades",
    5,
    False,
    f"{_DOCS}/dark-pool/paginated-off-exchange-trf-prints",
)
ATLAS_CONFIG: Final = Endpoint(
    "atlas_config", Host.ATLAS, "/v1/config", 0, False, f"{_DOCS}/meta/datafeed-configuration"
)
ATLAS_SEARCH: Final = Endpoint(
    "atlas_search", Host.ATLAS, "/v1/search", 1, False, f"{_DOCS}/symbols/search-symbols"
)
ATLAS_HISTORY: Final = Endpoint(
    "atlas_history",
    Host.ATLAS,
    "/v1/history",
    1,
    False,
    f"{_DOCS}/history/ohlcv-price-bars-for-a-symbol-and-resolution",
)

ENDPOINTS: Final[tuple[Endpoint, ...]] = (
    ACCOUNT,
    SYMBOLS,
    HEATMAP,
    HISTORICAL,
    HISTORICAL_RANGE,
    STREAM,
    GEX_LEVELS,
    DARK_POOL_TRADES,
    ATLAS_CONFIG,
    ATLAS_SEARCH,
    ATLAS_HISTORY,
)

REPLAY_PATHS: Final = frozenset({HISTORICAL.path, HISTORICAL_RANGE.path})
"""Paths of the Replay_Requests that ``limits.historicalInFlight`` caps (Req 2.4)."""


def is_replay(host: Host, path: str) -> bool:
    """True for ``GET /v1/historical`` and ``GET /v1/historical/range`` on the API host."""
    return host is Host.API and path in REPLAY_PATHS


# ---------------------------------------------------------------- documented caps

MAX_SYMBOLS_PER_CALL: Final = 10
"""Distinct symbols per heatmap, historical, levels or stream call."""
RANGE_MAX_SYMBOLS: Final = 5
RANGE_MAX_WINDOW_NS: Final = 15 * NS_PER_MINUTE
MAX_STRIKES_CAP: Final = 1000
MAX_EXPIRATIONS_CAP: Final = 60
HISTORY_START: Final = date(2023, 3, 28)
"""Heatmap history begins on this date; ``at`` may not be earlier."""
DARK_POOL_MAX_TICKERS: Final = 50
DARK_POOL_MAX_SPAN_DAYS: Final = 31
DARK_POOL_MAX_LIMIT: Final = 5000
DARK_POOL_MAX_OFFSET: Final = 50_000
SEARCH_MAX_LIMIT: Final = 500
ATLAS_RESOLUTIONS: Final = frozenset({"1", "2", "3", "5", "15", "30", "60", "240", "480", "D", "W"})

_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_HISTORY_START_NS: Final = int(datetime(2023, 3, 28, tzinfo=UTC).timestamp()) * NS_PER_SECOND
# Printable ASCII without spaces or commas: a comma separates symbols in a list.
# Futures tickers are undocumented (Atlas search resolves them), so nothing
# narrower is assumed.
_TICKER: Final = re.compile(r"[\x21-\x2b\x2d-\x7e]+", re.ASCII)
_ISO_DATE: Final = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)
_HH_MM: Final = re.compile(r"([01]\d|2[0-3]):[0-5]\d", re.ASCII)


class ViewParams(Protocol):
    """The Heatmap_View settings a request carries (``fse.data.cache.HeatmapView`` is one)."""

    @property
    def max_strikes(self) -> int | Literal["all"]: ...

    @property
    def max_expirations(self) -> int | Literal["all"]: ...

    @property
    def expirations(self) -> tuple[str, ...] | None: ...

    @property
    def include_empty(self) -> bool: ...


# ---------------------------------------------------------------- Heatseeker


def heatmap_params(symbols: Iterable[str], *, metric: str, view: ViewParams) -> dict[str, str]:
    """``GET /v1/heatmap``: the live board for up to 10 symbols."""
    return {
        "symbols": _symbols(symbols, MAX_SYMBOLS_PER_CALL),
        "metric": _metric(metric),
        **_view(view, include_empty=True),
    }


def historical_params(
    symbols: Iterable[str], *, at_ns: Instant, metric: str, view: ViewParams
) -> dict[str, str]:
    """``GET /v1/historical``: the latest Snapshot at or before ``at_ns``, up to 10 symbols."""
    if at_ns < _HISTORY_START_NS:
        raise ValueError(f"at must not be before {HISTORY_START.isoformat()}, where history begins")
    return {
        "symbols": _symbols(symbols, MAX_SYMBOLS_PER_CALL),
        "at": format_rfc3339(at_ns),
        "metric": _metric(metric),
        **_view(view, include_empty=True),
    }


def range_params(
    symbols: Iterable[str], *, from_ns: Instant, to_ns: Instant, metric: str, view: ViewParams
) -> dict[str, str]:
    """``GET /v1/historical/range``: every Snapshot in ``[from_ns, to_ns]``, up to 5 symbols.

    Both ends are inclusive and at most 15 minutes apart. ``includeEmpty`` is
    not a parameter of this endpoint and is never sent (OQ6).
    """
    if to_ns < from_ns:
        raise ValueError("the range end must not be before its start")
    if to_ns - from_ns > RANGE_MAX_WINDOW_NS:
        raise ValueError("a range window spans at most 15 minutes")
    return {
        "symbols": _symbols(symbols, RANGE_MAX_SYMBOLS),
        "from": format_rfc3339(from_ns),
        "to": format_rfc3339(to_ns),
        "metric": _metric(metric),
        **_view(view, include_empty=False),
    }


def gex_levels_params(symbols: Iterable[str], *, metric: str, view: ViewParams) -> dict[str, str]:
    """``GET /v1/gex/levels``: classified nodes for up to 10 symbols (live only)."""
    return {
        "symbols": _symbols(symbols, MAX_SYMBOLS_PER_CALL),
        "metric": _metric(metric),
        **_view(view, include_empty=True),
    }


def stream_params(
    symbols: Iterable[str],
    *,
    metric: str,
    view: ViewParams,
    last_event_id: str | None = None,
) -> dict[str, str]:
    """``GET /v1/stream`` in the v2 format (``symbols=``), up to 10 symbols per connection.

    ``last_event_id`` resumes a stream through the query string; a client that
    can set the ``Last-Event-ID`` header should send that instead.
    """
    params = {
        "symbols": _symbols(symbols, MAX_SYMBOLS_PER_CALL),
        "format": "v2",
        "metric": _metric(metric),
        **_view(view, include_empty=True),
    }
    if last_event_id is not None:
        if not last_event_id.strip():
            raise ValueError("last_event_id must not be blank")
        params["lastEventId"] = last_event_id
    return params


# ---------------------------------------------------------------- Flowseeker dark pool


def dark_pool_trades_params(
    tickers: Iterable[str],
    *,
    date_start: date,
    date_end: date,
    limit: int = DARK_POOL_MAX_LIMIT,
    offset: int = 0,
    min_notional: float | None = None,
    time_start: str | None = None,
    time_end: str | None = None,
    order: Literal["asc", "desc"] = "asc",
) -> dict[str, str]:
    """``GET /v1/dark-pool/trades``: prints for up to 50 tickers over at most 31 trade dates.

    Dates are New York trade dates, both inclusive. ``min_notional`` defaults
    to Skylit's $1,000,000 when ``None``; ``0`` asks for every print.
    ``order="asc"`` keeps offset pages stable while the tape grows.
    """
    for name, value in (("date_start", date_start), ("date_end", date_end)):
        if not isinstance(value, date) or isinstance(value, datetime):
            raise ValueError(f"{name} must be a date")
    if date_end < date_start:
        raise ValueError("date_end must not be before date_start")
    if (date_end - date_start).days + 1 > DARK_POOL_MAX_SPAN_DAYS:
        raise ValueError(f"a dark-pool request spans at most {DARK_POOL_MAX_SPAN_DAYS} trade dates")
    _int_in("limit", limit, 1, DARK_POOL_MAX_LIMIT)
    _int_in("offset", offset, 0, DARK_POOL_MAX_OFFSET)
    if order not in ("asc", "desc"):
        raise ValueError("order must be asc or desc")
    params = {
        "tickers": _symbols(tickers, DARK_POOL_MAX_TICKERS),
        "date_start": date_start.isoformat(),
        "date_end": date_end.isoformat(),
        "limit": str(limit),
        "offset": str(offset),
        "order": order,
    }
    if min_notional is not None:
        params["min_notional"] = _plain_number("min_notional", min_notional)
    for bound, clock_time in (("time_start", time_start), ("time_end", time_end)):
        if clock_time is not None:
            if _HH_MM.fullmatch(clock_time) is None:
                raise ValueError(f"{bound} must be HH:MM")
            params[bound] = clock_time
    return params


# ---------------------------------------------------------------- Atlas


def atlas_history_params(
    symbol: str, *, resolution: str, from_s: int, to_s: int, extended: bool = False
) -> dict[str, str]:
    """Atlas ``GET /v1/history``: UDF bars over ``[from_s, to_s)`` in Unix seconds.

    The window must stay within the resolution tier's trading-day cap
    (``max_fetch_trading_days`` from ``GET /v1/config``); the caller splits
    wider ranges. ``extended=True`` adds the bars outside 09:30-16:00.
    """
    if resolution not in ATLAS_RESOLUTIONS:
        raise ValueError(f"resolution must be one of {sorted(ATLAS_RESOLUTIONS)}")
    for name, value in (("from_s", from_s), ("to_s", to_s)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative int of Unix seconds")
    if to_s <= from_s:
        raise ValueError("to_s must be after from_s")
    if not isinstance(extended, bool):
        raise ValueError("extended must be a bool")
    return {
        "symbol": _ticker("symbol", symbol),
        "resolution": resolution,
        "from": str(from_s),
        "to": str(to_s),
        "extended": "true" if extended else "false",
    }


def atlas_search_params(query: str, *, limit: int = 25) -> dict[str, str]:
    """Atlas ``GET /v1/search``: ranked matches for a ticker or name fragment."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must not be blank")
    _int_in("limit", limit, 1, SEARCH_MAX_LIMIT)
    return {"query": query.strip(), "limit": str(limit)}


# ---------------------------------------------------------------- formatting


def format_rfc3339(t: Instant) -> str:
    """``t`` as RFC 3339 UTC: ``2026-03-05T14:30:00Z``, fractional digits only when non-zero."""
    if isinstance(t, bool) or not isinstance(t, int):
        raise TypeError(f"an Instant must be an int, got {type(t).__name__}")
    seconds, frac = divmod(t, NS_PER_SECOND)
    text = (_EPOCH + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S")
    if frac:
        text += "." + f"{frac:09d}".rstrip("0")
    return text + "Z"


# ---------------------------------------------------------------- internals


def _symbols(symbols: Iterable[str], cap: int) -> str:
    if isinstance(symbols, str):
        raise TypeError("pass symbols as a sequence of tickers, not one string")
    items = tuple(_ticker("symbol", s) for s in symbols)
    if not items:
        raise ValueError("at least one symbol is required")
    if len(set(items)) != len(items):
        raise ValueError("symbols must be distinct")
    if len(items) > cap:
        raise ValueError(f"at most {cap} symbols per request, got {len(items)}")
    return ",".join(items)


def _ticker(name: str, value: object) -> str:
    if not isinstance(value, str) or _TICKER.fullmatch(value) is None:
        raise ValueError(f"{name} must be a non-blank ticker without spaces or commas")
    return value


def _metric(metric: str) -> str:
    if metric not in METRICS:
        raise ValueError(f"metric must be one of {sorted(METRICS)}")
    return metric


def _view(view: ViewParams, *, include_empty: bool) -> dict[str, str]:
    params = {"maxStrikes": _limit("max_strikes", view.max_strikes, MAX_STRIKES_CAP)}
    if view.expirations is not None:
        # `expirations` supersedes `maxExpirations`, so only one of them is sent.
        if not view.expirations:
            raise ValueError("expirations must be None or non-empty")
        for item in view.expirations:
            if not isinstance(item, str) or _ISO_DATE.fullmatch(item) is None:
                raise ValueError("expirations entries must be YYYY-MM-DD dates")
        params["expirations"] = ",".join(view.expirations)
    else:
        params["maxExpirations"] = _limit(
            "max_expirations", view.max_expirations, MAX_EXPIRATIONS_CAP
        )
    if include_empty:
        if not isinstance(view.include_empty, bool):
            raise ValueError("include_empty must be a bool")
        params["includeEmpty"] = "true" if view.include_empty else "false"
    return params


def _limit(name: str, value: object, cap: int) -> str:
    if value == "all":
        return "all"
    _int_in(name, value, 1, cap)
    return str(value)


def _int_in(name: str, value: object, lo: int, hi: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ValueError(f"{name} must be an int from {lo} to {hi}")


def _plain_number(name: str, value: float) -> str:
    """A non-negative finite number without exponent notation (``1000000``, ``2.5``)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a number")
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"{name} must be at least 0")
        return str(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite number of at least 0")
    if value.is_integer():
        return str(int(value))
    # repr is the shortest round-trip form; Decimal spells it without an exponent.
    return format(Decimal(repr(value)), "f")
