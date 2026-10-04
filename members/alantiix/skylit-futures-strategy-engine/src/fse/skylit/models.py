"""Skylit response models (design §2, "Data Models" and "Research findings").

Sources (official Skylit docs and OpenAPI specs; content rephrased for
compliance with licensing restrictions):

- Envelope and node types: https://www.skylit.ai/docs/api-reference/introduction
- Heatseeker schemas (``SymbolHeatmap``, ``StrikeNode``, ``Meta``,
  ``SymbolRange``, ``Level``, ``LevelsSummary``, stream payloads):
  https://www.skylit.ai/docs/openapi.yaml
- Dark-pool schemas (``DarkPoolPrint``, ``DarkPoolTradesMeta``):
  https://www.skylit.ai/docs/flowseeker-openapi.yaml
- Atlas schemas (``HistoryOk``, ``HistoryNoData``, ``HistoryError``,
  ``SearchResult``, ``DatafeedConfig``): https://www.skylit.ai/docs/atlas-openapi.yaml
- Per endpoint: ``GET /v1/account``
  https://www.skylit.ai/docs/api-reference/account/your-balance-and-limits,
  ``GET /v1/symbols`` https://www.skylit.ai/docs/api-reference/meta/symbol-catalog,
  ``GET /v1/heatmap``
  https://www.skylit.ai/docs/api-reference/heatmap/live-per-strike-heatmap-one-or-more-symbols,
  ``GET /v1/historical``
  https://www.skylit.ai/docs/api-reference/heatmap/replay-per-strike-heatmap-at-a-past-instant-one-or-more-symbols,
  ``GET /v1/historical/range``
  https://www.skylit.ai/docs/api-reference/heatmap/replay-every-snapshot-in-a-window-up-to-15-minutes-5-symbols,
  ``GET /v1/stream``
  https://www.skylit.ai/docs/api-reference/heatmap/live-sse-stream-up-to-10-symbols-per-connection,
  ``GET /v1/gex/levels``
  https://www.skylit.ai/docs/api-reference/heatmap/key-levels-classified-nodes-for-one-or-more-symbols,
  ``GET /v1/dark-pool/trades``
  https://www.skylit.ai/docs/api-reference/dark-pool/paginated-off-exchange-trf-prints,
  Atlas ``GET /v1/config``
  https://www.skylit.ai/docs/api-reference/meta/datafeed-configuration,
  ``GET /v1/search`` https://www.skylit.ai/docs/api-reference/symbols/search-symbols and
  ``GET /v1/history``
  https://www.skylit.ai/docs/api-reference/history/ohlcv-price-bars-for-a-symbol-and-resolution.

Each ``parse`` takes a decoded JSON body (``json.loads`` output). Parsing is
hand-written, as in :mod:`fse.projectx.models`: a
:class:`MalformedResponseError` names the endpoint and the field path
(``GET /v1/historical/range data.symbols[0].frames[3].values``), never a
value, so a body can never leak into an error line.

Shapes worth knowing:

- **Heatmaps.** ``/v1/heatmap``, ``/v1/historical`` and stream ``snapshot``
  events share ``SymbolHeatmap``: per-strike ``strike``, ``value``,
  ``nodeType`` and (live only) ``velocityPct``. ``nodeType`` is optional here
  because stored history may lack it (OQ3).
- **Range frames.** ``/v1/historical/range`` lists each symbol's axes once
  (``id``, ``strikes``, ``expirations``); a frame's ``values[i]`` belongs to
  ``axes[frame.axis].strikes[i]``. Frames carry no ``nodeType``. A frame that
  names a missing axis, or whose value count differs from its axis's strike
  count, is malformed.
- **Snapshots.** :meth:`SymbolHeatmap.to_snapshot` and
  :meth:`SymbolRange.to_snapshots` build :class:`~fse.engine.types.Snapshot`
  values with every number exactly as decoded and ``asOf`` kept raw (Req
  3.10). ``extra_json`` holds, as canonical JSON, every field the Snapshot
  has no slot for: unrecognized keys, ``priceChange``, ``priceChangePercent``
  and ``matrix``; per-strike extras (such as ``velocityPct``) under
  ``"strikes"`` as one object per strike; and a range axis's extra keys
  under ``"axis"``.
- **Account.** Limits are read leniently: a limit that is not a positive
  integer is ``None``, which is what Req 2.2's fallbacks key off. The
  ``customerId`` is never kept, and :class:`AccountInfo`'s repr omits the
  balance.
- **Atlas history.** A UDF ``200`` is either bars (``s: ok``, equal-length
  column arrays, ``t`` = bar open time in Unix seconds, OQ10) or
  ``s: no_data``. An over-wide window is a ``400`` whose body
  :meth:`AtlasHistoryError.parse` reads for ``max_days``.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType
from typing import Final, cast

from fse.engine.types import DarkPoolPrint, Metric, Resolution, Snapshot, SourceEndpoint
from fse.logio import canonical_json
from fse.timekit import Instant, from_unix_seconds, parse_rfc3339

__all__ = [
    "AccountInfo",
    "AccountLimits",
    "AtlasConfig",
    "AtlasHistory",
    "AtlasHistoryError",
    "DarkPoolPage",
    "DarkPoolTradesResponse",
    "HeatmapMeta",
    "HeatmapResponse",
    "HistoryBars",
    "HistoryNoData",
    "Level",
    "LevelFlip",
    "LevelsResponse",
    "LevelsSummary",
    "MalformedResponseError",
    "NodeVelocity",
    "RangeAxis",
    "RangeFrame",
    "RangeResponse",
    "ResumedSymbol",
    "SearchResult",
    "SearchResults",
    "StreamClosed",
    "StreamConnected",
    "StreamCredits",
    "StreamEvent",
    "StreamReconnect",
    "StreamResumed",
    "StreamSnapshot",
    "StreamSymbolUnavailable",
    "StreamUnknown",
    "StreamVelocity",
    "StrikeNode",
    "SymbolCatalog",
    "SymbolHeatmap",
    "SymbolHistory",
    "SymbolInfo",
    "SymbolLevels",
    "SymbolRange",
    "WireDarkPoolPrint",
    "parse_atlas_history",
    "parse_event_id",
    "parse_stream_event",
]

type Json = object
"""A decoded JSON value."""
type JsonObject = dict[str, object]

_EMPTY: Final[Mapping[str, object]] = MappingProxyType({})
_ISO_DATE: Final = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)
_RESOLUTIONS: Final = frozenset({"1s", "1m"})
_METRICS: Final = frozenset({"gamma", "vanna"})


class MalformedResponseError(ValueError):
    """A Skylit body does not have the documented shape.

    The message names the endpoint and field path only, never a value.
    """


# ---------------------------------------------------------------- account


@dataclass(frozen=True, slots=True)
class AccountLimits:
    """``data.limits`` from ``GET /v1/account``. A value that is not usable is ``None``.

    ``requests_per_minute`` and ``historical_in_flight`` are ``None`` unless
    they are positive integers (Req 2.2); the other limits are ``None``
    unless they are non-negative integers.
    """

    requests_per_minute: int | None = None
    historical_in_flight: int | None = None
    symbols_per_heatmap_call: int | None = None
    symbols_per_stream: int | None = None
    stream_symbols_concurrent: int | None = None
    active_keys: int | None = None
    stream_max_duration_minutes: int | None = None

    @classmethod
    def parse(cls, value: Json) -> AccountLimits:
        """Read every limit it can; never raises."""
        if not isinstance(value, dict):
            return cls()
        return cls(
            requests_per_minute=_lenient_int(value, "requestsPerMinute", minimum=1),
            historical_in_flight=_lenient_int(value, "historicalInFlight", minimum=1),
            symbols_per_heatmap_call=_lenient_int(value, "symbolsPerHeatmapCall", minimum=0),
            symbols_per_stream=_lenient_int(value, "symbolsPerStream", minimum=0),
            stream_symbols_concurrent=_lenient_int(value, "streamSymbolsConcurrent", minimum=0),
            active_keys=_lenient_int(value, "activeKeys", minimum=0),
            stream_max_duration_minutes=_lenient_int(value, "streamMaxDurationMinutes", minimum=0),
        )


@dataclass(frozen=True, slots=True, repr=False)
class AccountInfo:
    """``GET /v1/account``: status, eligibility and limits.

    ``customerId`` is dropped on purpose. ``credits_balance`` is kept for the
    pull estimate but left out of the repr, so it cannot reach a log line
    through one.
    """

    status: str | None
    api_eligible: bool | None
    unlimited: bool | None
    credits_balance: int | None
    limits: AccountLimits

    @classmethod
    def parse(cls, body: Json) -> AccountInfo:
        """Raises only when ``data`` is not an object; every field inside is optional."""
        where = "GET /v1/account"
        data = _obj(_req(_obj(body, where), "data", where), f"{where} data")
        status = data.get("status")
        return cls(
            status=status if isinstance(status, str) else None,
            api_eligible=_lenient_bool(data, "apiEligible"),
            unlimited=_lenient_bool(data, "unlimited"),
            credits_balance=_lenient_int(data, "creditsBalance", minimum=None),
            limits=AccountLimits.parse(data.get("limits")),
        )

    def __repr__(self) -> str:
        return (
            f"AccountInfo(status={self.status!r}, api_eligible={self.api_eligible!r}, "
            f"unlimited={self.unlimited!r}, limits={self.limits!r})"
        )


# ---------------------------------------------------------------- symbols


@dataclass(frozen=True, slots=True)
class SymbolHistory:
    """The UTC dates a symbol's heatmap history covers (``history.from`` and ``history.to``)."""

    first: date
    last: date


@dataclass(frozen=True, slots=True)
class SymbolInfo:
    """One ``GET /v1/symbols`` entry. ``history`` is ``None`` when Skylit could not read it."""

    symbol: str
    is_index: bool
    previous_names: tuple[str, ...]
    metrics: tuple[str, ...]
    history: SymbolHistory | None


@dataclass(frozen=True, slots=True)
class SymbolCatalog:
    """``GET /v1/symbols``: every symbol the API serves, in the order listed."""

    symbols: tuple[SymbolInfo, ...]

    @classmethod
    def parse(cls, body: Json) -> SymbolCatalog:
        where = "GET /v1/symbols"
        data = _obj(_req(_obj(body, where), "data", where), f"{where} data")
        items = _list(data, "symbols", f"{where} data")
        return cls(
            tuple(_symbol_info(item, f"{where} data.symbols[{i}]") for i, item in enumerate(items))
        )

    def get(self, symbol: str) -> SymbolInfo | None:
        """The entry for ``symbol``, matching a canonical ticker or a previous name."""
        for info in self.symbols:
            if info.symbol == symbol:
                return info
        for info in self.symbols:
            if symbol in info.previous_names:
                return info
        return None

    def first_history_dates(self) -> dict[str, date]:
        """Canonical ticker → first history date, for each symbol whose history is known."""
        return {s.symbol: s.history.first for s in self.symbols if s.history is not None}


def _symbol_info(item: Json, where: str) -> SymbolInfo:
    obj = _obj(item, where)
    raw_history = obj.get("history")
    history = None
    if raw_history is not None:
        h = _obj(raw_history, f"{where}.history")
        history = SymbolHistory(
            first=_date(_str(h, "from", f"{where}.history"), f"{where}.history.from"),
            last=_date(_str(h, "to", f"{where}.history"), f"{where}.history.to"),
        )
    elif "history" not in obj:
        raise MalformedResponseError(f"{where}.history is missing")
    previous = obj.get("previousNames")
    return SymbolInfo(
        symbol=_str(obj, "symbol", where),
        is_index=_bool(obj, "isIndex", where),
        previous_names=() if previous is None else _str_tuple(previous, f"{where}.previousNames"),
        metrics=_str_tuple(_req(obj, "metrics", where), f"{where}.metrics"),
        history=history,
    )


# ---------------------------------------------------------------- heatmaps


@dataclass(frozen=True, slots=True)
class HeatmapMeta:
    """The ``meta`` of a heatmap-family response."""

    metric: Metric
    resolution: Resolution
    mode: str | None
    cached: bool | None

    @classmethod
    def parse(cls, value: Json, where: str) -> HeatmapMeta:
        obj = _obj(value, where)
        metric = _str(obj, "metric", where)
        if metric not in _METRICS:
            raise MalformedResponseError(f"{where}.metric is not gamma or vanna")
        resolution = _str(obj, "resolution", where)
        if resolution not in _RESOLUTIONS:
            raise MalformedResponseError(f"{where}.resolution is not 1s or 1m")
        return cls(
            metric=cast(Metric, metric),
            resolution=cast(Resolution, resolution),
            mode=_opt_str(obj, "mode", where),
            cached=_opt_bool(obj, "cached", where),
        )


_STRIKE_KEYS: Final = frozenset({"strike", "value", "nodeType"})


@dataclass(frozen=True, slots=True)
class StrikeNode:
    """One per-strike entry: ``value`` is the net exposure summed over the expirations."""

    strike: float
    value: float
    node_type: str | None
    velocity_pct: float | None
    extra: Mapping[str, object] = field(default=_EMPTY, compare=False, repr=False)
    """Every field except ``strike``, ``value`` and ``nodeType``, as decoded."""


# Fields of SymbolHeatmap that a Snapshot holds directly. Everything else goes to extra_json.
_HEATMAP_SNAPSHOT_KEYS: Final = frozenset(
    {"symbol", "asOf", "spot", "previousClose", "expirations", "strikes"}
)


@dataclass(frozen=True, slots=True)
class SymbolHeatmap:
    """One symbol's board from ``/v1/heatmap``, ``/v1/historical`` or a stream snapshot."""

    symbol: str
    as_of_raw: str
    as_of_ns: Instant
    spot: float
    previous_close: float | None
    price_change: float | None
    price_change_percent: float | None
    expirations: tuple[str, ...]
    strikes: tuple[StrikeNode, ...]
    matrix: tuple[tuple[float, ...], ...] | None
    extra: Mapping[str, object] = field(default=_EMPTY, compare=False, repr=False)
    """Every field a Snapshot has no slot for, as decoded (see the module notes)."""

    @classmethod
    def parse(cls, value: Json, where: str) -> SymbolHeatmap:
        obj = _obj(value, where)
        as_of_raw = _str(obj, "asOf", where)
        expirations = _str_tuple(_req(obj, "expirations", where), f"{where}.expirations")
        raw_strikes = _list(obj, "strikes", where)
        strikes = tuple(
            _strike_node(item, f"{where}.strikes[{i}]") for i, item in enumerate(raw_strikes)
        )
        matrix = None
        if obj.get("matrix") is not None:
            matrix = _matrix(obj["matrix"], len(strikes), len(expirations), f"{where}.matrix")
        extra: JsonObject = {k: v for k, v in obj.items() if k not in _HEATMAP_SNAPSHOT_KEYS}
        if any(s.extra for s in strikes):
            extra["strikes"] = [dict(s.extra) for s in strikes]
        return cls(
            symbol=_str(obj, "symbol", where),
            as_of_raw=as_of_raw,
            as_of_ns=_instant(as_of_raw, f"{where}.asOf"),
            spot=_num(obj, "spot", where),
            previous_close=_opt_num(obj, "previousClose", where),
            price_change=_opt_num(obj, "priceChange", where),
            price_change_percent=_opt_num(obj, "priceChangePercent", where),
            expirations=expirations,
            strikes=strikes,
            matrix=matrix,
            extra=MappingProxyType(extra),
        )

    def to_snapshot(
        self,
        *,
        metric: Metric,
        resolution: Resolution,
        view_id: str,
        source_endpoint: SourceEndpoint,
    ) -> Snapshot:
        """The board as a Snapshot, numbers and ``asOf`` exactly as received (Req 3.10)."""
        return Snapshot(
            symbol=self.symbol,
            metric=metric,
            view_id=view_id,
            as_of_ns=self.as_of_ns,
            as_of_raw=self.as_of_raw,
            spot=self.spot,
            previous_close=self.previous_close,
            strikes=tuple(s.strike for s in self.strikes),
            values=tuple(s.value for s in self.strikes),
            node_types=tuple(s.node_type for s in self.strikes),
            expirations=self.expirations,
            resolution=resolution,
            source_endpoint=source_endpoint,
            extra_json=_extra_json(self.extra),
        )


@dataclass(frozen=True, slots=True)
class HeatmapResponse:
    """``GET /v1/heatmap`` or ``GET /v1/historical``: one board per available symbol."""

    symbols: tuple[SymbolHeatmap, ...]
    meta: HeatmapMeta

    @classmethod
    def parse(cls, body: Json, *, where: str = "GET /v1/heatmap") -> HeatmapResponse:
        obj = _obj(body, where)
        data = _obj(_req(obj, "data", where), f"{where} data")
        items = _list(data, "symbols", f"{where} data")
        return cls(
            symbols=tuple(
                SymbolHeatmap.parse(item, f"{where} data.symbols[{i}]")
                for i, item in enumerate(items)
            ),
            meta=HeatmapMeta.parse(_req(obj, "meta", where), f"{where} meta"),
        )

    def snapshots(self, *, view_id: str, source_endpoint: SourceEndpoint) -> list[Snapshot]:
        """Every board as a Snapshot with the response's metric and resolution."""
        return [
            s.to_snapshot(
                metric=self.meta.metric,
                resolution=self.meta.resolution,
                view_id=view_id,
                source_endpoint=source_endpoint,
            )
            for s in self.symbols
        ]


def _strike_node(value: Json, where: str) -> StrikeNode:
    obj = _obj(value, where)
    extra = {k: v for k, v in obj.items() if k not in _STRIKE_KEYS}
    return StrikeNode(
        strike=_num(obj, "strike", where),
        value=_num(obj, "value", where),
        node_type=_opt_str(obj, "nodeType", where),
        velocity_pct=_opt_num(obj, "velocityPct", where),
        extra=MappingProxyType(extra) if extra else _EMPTY,
    )


def _matrix(value: Json, rows: int, cols: int, where: str) -> tuple[tuple[float, ...], ...]:
    if not isinstance(value, list):
        raise MalformedResponseError(f"{where} is not a list")
    if len(value) != rows:
        raise MalformedResponseError(f"{where} does not have one row per strike")
    out = tuple(_num_tuple(row, f"{where}[{i}]") for i, row in enumerate(value))
    for i, row in enumerate(out):
        if len(row) != cols:
            raise MalformedResponseError(f"{where}[{i}] does not have one value per expiration")
    return out


# ---------------------------------------------------------------- range


@dataclass(frozen=True, slots=True)
class RangeAxis:
    """One strike/expiration axis of a range response, referenced by frames through ``id``."""

    id: int
    strikes: tuple[float, ...]
    expirations: tuple[str, ...]
    extra: Mapping[str, object] = field(default=_EMPTY, compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class RangeFrame:
    """One Snapshot of a range response: ``values[i]`` is at ``axes[axis].strikes[i]``."""

    as_of_raw: str
    as_of_ns: Instant
    axis: int
    spot: float
    previous_close: float | None
    values: tuple[float, ...]
    extra: Mapping[str, object] = field(default=_EMPTY, compare=False, repr=False)


_AXIS_KEYS: Final = frozenset({"id", "strikes", "expirations"})
_FRAME_KEYS: Final = frozenset({"asOf", "axis", "spot", "previousClose", "values"})


@dataclass(frozen=True, slots=True)
class SymbolRange:
    """One symbol's frames, oldest first, with the axes they reference."""

    symbol: str
    axes: Mapping[int, RangeAxis]
    frames: tuple[RangeFrame, ...]

    @classmethod
    def parse(cls, value: Json, where: str) -> SymbolRange:
        obj = _obj(value, where)
        axes: dict[int, RangeAxis] = {}
        for i, raw in enumerate(_list(obj, "axes", where)):
            axis = _range_axis(raw, f"{where}.axes[{i}]")
            if axis.id in axes:
                raise MalformedResponseError(f"{where}.axes[{i}].id repeats an earlier axis id")
            axes[axis.id] = axis
        frames: list[RangeFrame] = []
        for i, raw in enumerate(_list(obj, "frames", where)):
            frame = _range_frame(raw, f"{where}.frames[{i}]")
            ref = axes.get(frame.axis)
            if ref is None:
                raise MalformedResponseError(f"{where}.frames[{i}].axis names no listed axis")
            if len(frame.values) != len(ref.strikes):
                raise MalformedResponseError(
                    f"{where}.frames[{i}].values does not have one value per axis strike"
                )
            frames.append(frame)
        return cls(_str(obj, "symbol", where), MappingProxyType(axes), tuple(frames))

    def to_snapshots(
        self, *, metric: Metric, resolution: Resolution, view_id: str
    ) -> list[Snapshot]:
        """Every frame as a Snapshot, strikes and expirations from its axis; no node types."""
        out: list[Snapshot] = []
        for frame in self.frames:
            axis = self.axes[frame.axis]
            extra: JsonObject = dict(frame.extra)
            if axis.extra:
                extra["axis"] = dict(axis.extra)
            out.append(
                Snapshot(
                    symbol=self.symbol,
                    metric=metric,
                    view_id=view_id,
                    as_of_ns=frame.as_of_ns,
                    as_of_raw=frame.as_of_raw,
                    spot=frame.spot,
                    previous_close=frame.previous_close,
                    strikes=axis.strikes,
                    values=frame.values,
                    node_types=None,
                    expirations=axis.expirations,
                    resolution=resolution,
                    source_endpoint="range",
                    extra_json=_extra_json(extra),
                )
            )
        return out


@dataclass(frozen=True, slots=True)
class RangeResponse:
    """``GET /v1/historical/range``: every Snapshot in ``[from, to]`` per symbol."""

    from_raw: str
    from_ns: Instant
    to_raw: str
    to_ns: Instant
    symbols: tuple[SymbolRange, ...]
    meta: HeatmapMeta

    @classmethod
    def parse(cls, body: Json) -> RangeResponse:
        where = "GET /v1/historical/range"
        obj = _obj(body, where)
        data = _obj(_req(obj, "data", where), f"{where} data")
        from_raw = _str(data, "from", f"{where} data")
        to_raw = _str(data, "to", f"{where} data")
        items = _list(data, "symbols", f"{where} data")
        return cls(
            from_raw=from_raw,
            from_ns=_instant(from_raw, f"{where} data.from"),
            to_raw=to_raw,
            to_ns=_instant(to_raw, f"{where} data.to"),
            symbols=tuple(
                SymbolRange.parse(item, f"{where} data.symbols[{i}]")
                for i, item in enumerate(items)
            ),
            meta=HeatmapMeta.parse(_req(obj, "meta", where), f"{where} meta"),
        )

    def snapshots(self, *, view_id: str) -> dict[str, list[Snapshot]]:
        """Symbol → its Snapshots, oldest first, with the response's metric and resolution."""
        return {
            s.symbol: s.to_snapshots(
                metric=self.meta.metric, resolution=self.meta.resolution, view_id=view_id
            )
            for s in self.symbols
        }


def _range_axis(value: Json, where: str) -> RangeAxis:
    obj = _obj(value, where)
    extra = {k: v for k, v in obj.items() if k not in _AXIS_KEYS}
    return RangeAxis(
        id=_int(obj, "id", where),
        strikes=_num_tuple(_req(obj, "strikes", where), f"{where}.strikes"),
        expirations=_str_tuple(_req(obj, "expirations", where), f"{where}.expirations"),
        extra=MappingProxyType(extra) if extra else _EMPTY,
    )


def _range_frame(value: Json, where: str) -> RangeFrame:
    obj = _obj(value, where)
    as_of_raw = _str(obj, "asOf", where)
    extra = {k: v for k, v in obj.items() if k not in _FRAME_KEYS}
    return RangeFrame(
        as_of_raw=as_of_raw,
        as_of_ns=_instant(as_of_raw, f"{where}.asOf"),
        axis=_int(obj, "axis", where),
        spot=_num(obj, "spot", where),
        previous_close=_opt_num(obj, "previousClose", where),
        values=_num_tuple(_req(obj, "values", where), f"{where}.values"),
        extra=MappingProxyType(extra) if extra else _EMPTY,
    )


# ---------------------------------------------------------------- stream (v2)


@dataclass(frozen=True, slots=True)
class StreamConnected:
    """``connected``: the connection's symbols, metric, credits and maximum duration."""

    format: str | None
    symbols: tuple[str, ...]
    metric: str | None
    credits_remaining: int | None
    credits_per_minute: int | None
    max_duration_seconds: int | None


@dataclass(frozen=True, slots=True)
class StreamSnapshot:
    """``snapshot``: a full board (no ``velocityPct``) plus ``lagged`` for slow readers."""

    heatmap: SymbolHeatmap
    lagged: bool
    event_id: str | None

    def to_snapshot(self, *, metric: Metric, view_id: str) -> Snapshot:
        """A live board is a 1-second series, so its resolution is ``1s``."""
        return self.heatmap.to_snapshot(
            metric=metric, resolution="1s", view_id=view_id, source_endpoint="stream"
        )


@dataclass(frozen=True, slots=True)
class NodeVelocity:
    """One per-node velocity reading; the other documented fields stay in ``extra``."""

    strike: float | None
    expiration: str | None
    current_value: float | None
    velocity: float | None
    trend: str | None
    extra: Mapping[str, object] = field(default=_EMPTY, compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class StreamVelocity:
    """``velocity``: per-node velocity for the stream's metric."""

    symbol: str
    metric: str | None
    as_of_raw: str | None
    nodes: tuple[NodeVelocity, ...]
    event_id: str | None


@dataclass(frozen=True, slots=True)
class ResumedSymbol:
    symbol: str
    status: str
    last_as_of: str | None
    current_as_of: str | None


@dataclass(frozen=True, slots=True)
class StreamResumed:
    """``resumed``: per-symbol status after a ``Last-Event-ID`` reconnect."""

    last_event_id: str | None
    gap_covered: bool | None
    state_restored: bool | None
    symbols: tuple[ResumedSymbol, ...]


@dataclass(frozen=True, slots=True)
class StreamSymbolUnavailable:
    symbol: str
    reason: str | None


@dataclass(frozen=True, slots=True)
class StreamCredits:
    remaining: int | None
    charged: int | None


@dataclass(frozen=True, slots=True)
class StreamClosed:
    """``closed``: the stream ended; only ``credit_check_failed`` is worth a reconnect."""

    reason: str | None


@dataclass(frozen=True, slots=True)
class StreamReconnect:
    """``reconnect``: reconnect now with ``Last-Event-ID``."""

    reason: str | None


@dataclass(frozen=True, slots=True)
class StreamUnknown:
    """An event this client does not model (including v1 events); kept as received."""

    event: str
    data: str


type StreamEvent = (
    StreamConnected
    | StreamSnapshot
    | StreamVelocity
    | StreamResumed
    | StreamSymbolUnavailable
    | StreamCredits
    | StreamClosed
    | StreamReconnect
    | StreamUnknown
)


def parse_stream_event(event: str, data: str, *, event_id: str | None = None) -> StreamEvent:
    """Decode one v2 SSE event (``event:`` name, ``data:`` text, optional ``id:``)."""
    parser = _STREAM_PARSERS.get(event)
    if parser is None:
        return StreamUnknown(event, data)
    where = f"GET /v1/stream {event} event"
    try:
        body = json.loads(data)
    except ValueError:
        raise MalformedResponseError(f"{where} data is not JSON") from None
    return parser(_obj(body, f"{where} data"), where, event_id)


def parse_event_id(event_id: str) -> dict[str, int]:
    """A v2 resume cursor (``SPY:1790000000123,QQQ:...``) as symbol → ``asOfUnixMs``."""
    out: dict[str, int] = {}
    for part in event_id.split(","):
        symbol, sep, ms = part.strip().rpartition(":")
        if not sep or not symbol or not ms.isascii() or not ms.isdigit():
            raise MalformedResponseError("GET /v1/stream event id is not SYMBOL:asOfUnixMs pairs")
        out[symbol] = int(ms)
    return out


def _stream_connected(obj: JsonObject, where: str, _id: str | None) -> StreamConnected:
    symbols = obj.get("symbols")
    return StreamConnected(
        format=_opt_str(obj, "format", where),
        symbols=() if symbols is None else _str_tuple(symbols, f"{where}.symbols"),
        metric=_opt_str(obj, "metric", where),
        credits_remaining=_opt_int(obj, "creditsRemaining", where),
        credits_per_minute=_opt_int(obj, "creditsPerMinute", where),
        max_duration_seconds=_opt_int(obj, "maxDurationSeconds", where),
    )


def _stream_snapshot(obj: JsonObject, where: str, event_id: str | None) -> StreamSnapshot:
    lagged = _opt_bool(obj, "lagged", where)
    body = {k: v for k, v in obj.items() if k != "lagged"}
    return StreamSnapshot(SymbolHeatmap.parse(body, f"{where} data"), bool(lagged), event_id)


_VELOCITY_KEYS: Final = frozenset({"strike", "expiration", "currentValue", "velocity", "trend"})


def _stream_velocity(obj: JsonObject, where: str, event_id: str | None) -> StreamVelocity:
    raw_nodes = obj.get("nodes")
    nodes: list[NodeVelocity] = []
    if raw_nodes is not None:
        if not isinstance(raw_nodes, list):
            raise MalformedResponseError(f"{where}.nodes is not a list")
        for i, raw in enumerate(raw_nodes):
            node = _obj(raw, f"{where}.nodes[{i}]")
            extra = {k: v for k, v in node.items() if k not in _VELOCITY_KEYS}
            nodes.append(
                NodeVelocity(
                    strike=_opt_num(node, "strike", f"{where}.nodes[{i}]"),
                    expiration=_opt_str(node, "expiration", f"{where}.nodes[{i}]"),
                    current_value=_opt_num(node, "currentValue", f"{where}.nodes[{i}]"),
                    velocity=_opt_num(node, "velocity", f"{where}.nodes[{i}]"),
                    trend=_opt_str(node, "trend", f"{where}.nodes[{i}]"),
                    extra=MappingProxyType(extra) if extra else _EMPTY,
                )
            )
    return StreamVelocity(
        symbol=_str(obj, "symbol", where),
        metric=_opt_str(obj, "metric", where),
        as_of_raw=_opt_str(obj, "asOf", where),
        nodes=tuple(nodes),
        event_id=event_id,
    )


def _stream_resumed(obj: JsonObject, where: str, _id: str | None) -> StreamResumed:
    raw = obj.get("symbols")
    symbols: list[ResumedSymbol] = []
    if raw is not None:
        if not isinstance(raw, list):
            raise MalformedResponseError(f"{where}.symbols is not a list")
        for i, item in enumerate(raw):
            s = _obj(item, f"{where}.symbols[{i}]")
            symbols.append(
                ResumedSymbol(
                    symbol=_str(s, "symbol", f"{where}.symbols[{i}]"),
                    status=_str(s, "status", f"{where}.symbols[{i}]"),
                    last_as_of=_opt_str(s, "lastAsOf", f"{where}.symbols[{i}]"),
                    current_as_of=_opt_str(s, "currentAsOf", f"{where}.symbols[{i}]"),
                )
            )
    return StreamResumed(
        last_event_id=_opt_str(obj, "lastEventId", where),
        gap_covered=_opt_bool(obj, "gapCovered", where),
        state_restored=_opt_bool(obj, "stateRestored", where),
        symbols=tuple(symbols),
    )


def _stream_unavailable(obj: JsonObject, where: str, _id: str | None) -> StreamSymbolUnavailable:
    return StreamSymbolUnavailable(_str(obj, "symbol", where), _opt_str(obj, "reason", where))


def _stream_credits(obj: JsonObject, where: str, _id: str | None) -> StreamCredits:
    return StreamCredits(_opt_int(obj, "remaining", where), _opt_int(obj, "charged", where))


def _stream_closed(obj: JsonObject, where: str, _id: str | None) -> StreamClosed:
    return StreamClosed(_opt_str(obj, "reason", where))


def _stream_reconnect(obj: JsonObject, where: str, _id: str | None) -> StreamReconnect:
    return StreamReconnect(_opt_str(obj, "reason", where))


_STREAM_PARSERS: Final[Mapping[str, Callable[[JsonObject, str, str | None], StreamEvent]]] = (
    MappingProxyType(
        {
            "connected": _stream_connected,
            "snapshot": _stream_snapshot,
            "velocity": _stream_velocity,
            "resumed": _stream_resumed,
            "symbol_unavailable": _stream_unavailable,
            "credits": _stream_credits,
            "closed": _stream_closed,
            "reconnect": _stream_reconnect,
        }
    )
)


# ---------------------------------------------------------------- gex levels


@dataclass(frozen=True, slots=True)
class Level:
    """One classified strike; ``distance_pct`` is the percent distance from spot (below < 0)."""

    strike: float
    value: float
    node_type: str | None
    distance_pct: float


@dataclass(frozen=True, slots=True)
class LevelFlip:
    """Where the running sum of ``value`` from the lowest strike changes sign."""

    level: float
    distance_pct: float


@dataclass(frozen=True, slots=True)
class LevelsSummary:
    net_exposure: float
    positive_wall: Level | None
    negative_wall: Level | None
    flip: LevelFlip | None
    low_strike: float
    high_strike: float


@dataclass(frozen=True, slots=True)
class SymbolLevels:
    """One symbol of ``GET /v1/gex/levels``: nodes strongest first."""

    symbol: str
    as_of_raw: str
    as_of_ns: Instant
    spot: float
    previous_close: float | None
    king_node: Level | None
    levels: tuple[Level, ...]
    summary: LevelsSummary | None


@dataclass(frozen=True, slots=True)
class LevelsResponse:
    """``GET /v1/gex/levels`` (live only; the Live_Runner logs it for comparison)."""

    symbols: tuple[SymbolLevels, ...]
    meta: HeatmapMeta

    @classmethod
    def parse(cls, body: Json) -> LevelsResponse:
        where = "GET /v1/gex/levels"
        obj = _obj(body, where)
        data = _obj(_req(obj, "data", where), f"{where} data")
        items = _list(data, "symbols", f"{where} data")
        return cls(
            symbols=tuple(
                _symbol_levels(item, f"{where} data.symbols[{i}]") for i, item in enumerate(items)
            ),
            meta=HeatmapMeta.parse(_req(obj, "meta", where), f"{where} meta"),
        )


def _level(value: Json, where: str) -> Level:
    obj = _obj(value, where)
    return Level(
        strike=_num(obj, "strike", where),
        value=_num(obj, "value", where),
        node_type=_opt_str(obj, "nodeType", where),
        distance_pct=_num(obj, "distancePct", where),
    )


def _opt_level(obj: JsonObject, name: str, where: str) -> Level | None:
    value = obj.get(name)
    return None if value is None else _level(value, f"{where}.{name}")


def _symbol_levels(value: Json, where: str) -> SymbolLevels:
    obj = _obj(value, where)
    as_of_raw = _str(obj, "asOf", where)
    summary = None
    if obj.get("summary") is not None:
        s = _obj(obj["summary"], f"{where}.summary")
        sw = f"{where}.summary"
        flip = None
        if s.get("flip") is not None:
            f = _obj(s["flip"], f"{sw}.flip")
            flip = LevelFlip(_num(f, "level", f"{sw}.flip"), _num(f, "distancePct", f"{sw}.flip"))
        summary = LevelsSummary(
            net_exposure=_num(s, "netExposure", sw),
            positive_wall=_opt_level(s, "positiveWall", sw),
            negative_wall=_opt_level(s, "negativeWall", sw),
            flip=flip,
            low_strike=_num(s, "lowStrike", sw),
            high_strike=_num(s, "highStrike", sw),
        )
    return SymbolLevels(
        symbol=_str(obj, "symbol", where),
        as_of_raw=as_of_raw,
        as_of_ns=_instant(as_of_raw, f"{where}.asOf"),
        spot=_num(obj, "spot", where),
        previous_close=_opt_num(obj, "previousClose", where),
        king_node=_opt_level(obj, "kingNode", where),
        levels=tuple(
            _level(item, f"{where}.levels[{i}]")
            for i, item in enumerate(_list(obj, "levels", where))
        ),
        summary=summary,
    )


# ---------------------------------------------------------------- dark pool


@dataclass(frozen=True, slots=True)
class WireDarkPoolPrint:
    """One off-exchange print as sent: no side, BBO or greeks."""

    timestamp_raw: str
    ts_ns: Instant
    ticker: str
    price: float
    size: int
    notional: float
    venue: str
    sector: str | None
    industry: str | None
    pct_avg_vol: float | None

    def to_print(self) -> DarkPoolPrint:
        """The engine value; ``ts_ns`` (the print ``timestamp``) is its Observation_Time."""
        return DarkPoolPrint(
            ticker=self.ticker,
            ts_ns=self.ts_ns,
            price=self.price,
            size=self.size,
            notional=self.notional,
            venue=self.venue,
        )


@dataclass(frozen=True, slots=True)
class DarkPoolPage:
    """Paging state from ``meta``; ``has_more`` is true when the page is full."""

    limit: int
    offset: int
    count: int
    has_more: bool


@dataclass(frozen=True, slots=True)
class DarkPoolTradesResponse:
    """``GET /v1/dark-pool/trades``: one page of prints."""

    prints: tuple[WireDarkPoolPrint, ...]
    page: DarkPoolPage

    @classmethod
    def parse(cls, body: Json) -> DarkPoolTradesResponse:
        where = "GET /v1/dark-pool/trades"
        obj = _obj(body, where)
        prints = tuple(
            _dark_pool_print(item, f"{where} data[{i}]")
            for i, item in enumerate(_list(obj, "data", where))
        )
        meta = _obj(_req(obj, "meta", where), f"{where} meta")
        mw = f"{where} meta"
        page = DarkPoolPage(
            limit=_int(meta, "limit", mw),
            offset=_int(meta, "offset", mw),
            count=_int(meta, "count", mw),
            has_more=_bool(meta, "hasMore", mw),
        )
        return cls(prints, page)


def _dark_pool_print(value: Json, where: str) -> WireDarkPoolPrint:
    obj = _obj(value, where)
    raw = _str(obj, "timestamp", where)
    size = _int(obj, "size", where)
    if size < 0:
        raise MalformedResponseError(f"{where}.size is negative")
    return WireDarkPoolPrint(
        timestamp_raw=raw,
        ts_ns=_instant(raw, f"{where}.timestamp"),
        ticker=_str(obj, "ticker", where),
        price=_num(obj, "price", where),
        size=size,
        notional=_num(obj, "notional", where),
        venue=_str(obj, "venue", where),
        sector=_opt_str(obj, "sector", where),
        industry=_opt_str(obj, "industry", where),
        pct_avg_vol=_opt_num(obj, "pctAvgVol", where),
    )


# ---------------------------------------------------------------- Atlas


@dataclass(frozen=True, slots=True)
class AtlasConfig:
    """Atlas ``GET /v1/config`` (free): resolutions, symbol types and history window caps."""

    supported_resolutions: tuple[str, ...]
    max_fetch_trading_days: Mapping[str, int]
    symbols_types: tuple[str, ...]
    supports_search: bool | None
    supports_time: bool | None

    @classmethod
    def parse(cls, body: Json) -> AtlasConfig:
        where = "Atlas GET /v1/config"
        obj = _obj(body, where)
        caps: dict[str, int] = {}
        raw_caps = obj.get("max_fetch_trading_days")
        if raw_caps is not None:
            for key, value in _obj(raw_caps, f"{where}.max_fetch_trading_days").items():
                if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                    raise MalformedResponseError(
                        f"{where}.max_fetch_trading_days entry is not a positive integer"
                    )
                caps[key] = value
        types: list[str] = []
        raw_types = obj.get("symbols_types")
        if raw_types is not None:
            if not isinstance(raw_types, list):
                raise MalformedResponseError(f"{where}.symbols_types is not a list")
            for i, item in enumerate(raw_types):
                tw = f"{where}.symbols_types[{i}]"
                types.append(_str(_obj(item, tw), "value", tw))
        resolutions = obj.get("supported_resolutions")
        return cls(
            supported_resolutions=(
                ()
                if resolutions is None
                else _str_tuple(resolutions, f"{where}.supported_resolutions")
            ),
            max_fetch_trading_days=MappingProxyType(caps),
            symbols_types=tuple(types),
            supports_search=_opt_bool(obj, "supports_search", where),
            supports_time=_opt_bool(obj, "supports_time", where),
        )

    def max_days(self, resolution: str, default: int = 90) -> int:
        """The trading-day cap of one ``/v1/history`` request at ``resolution``."""
        return self.max_fetch_trading_days.get(resolution, default)


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One Atlas search match; ``symbol`` is what ``/v1/history`` takes."""

    symbol: str
    ticker: str
    description: str | None
    exchange: str | None
    type: str | None
    kind: str | None


@dataclass(frozen=True, slots=True)
class SearchResults:
    """Atlas ``GET /v1/search``: ranked matches (a bare JSON array)."""

    results: tuple[SearchResult, ...]

    @classmethod
    def parse(cls, body: Json) -> SearchResults:
        where = "Atlas GET /v1/search"
        if not isinstance(body, list):
            raise MalformedResponseError(f"{where} body is not a list")
        out: list[SearchResult] = []
        for i, item in enumerate(body):
            w = f"{where} [{i}]"
            obj = _obj(item, w)
            out.append(
                SearchResult(
                    symbol=_str(obj, "symbol", w),
                    ticker=_str(obj, "ticker", w),
                    description=_opt_str(obj, "description", w),
                    exchange=_opt_str(obj, "exchange", w),
                    type=_opt_str(obj, "type", w),
                    kind=_opt_str(obj, "kind", w),
                )
            )
        return cls(tuple(out))


@dataclass(frozen=True, slots=True)
class HistoryBars:
    """UDF bars (``s: ok``), oldest first. ``t`` is each bar's open time in Unix seconds."""

    t: tuple[int, ...]
    o: tuple[float, ...]
    h: tuple[float, ...]
    l: tuple[float, ...]  # noqa: E741 - UDF column name
    c: tuple[float, ...]
    v: tuple[float, ...]
    bv: tuple[float, ...] | None = None
    sv: tuple[float, ...] | None = None
    uv: tuple[float, ...] | None = None

    def __len__(self) -> int:
        return len(self.t)

    def open_ns(self) -> tuple[Instant, ...]:
        """Bar open Instants (OQ10: UDF ``t`` is taken as the open time)."""
        return tuple(from_unix_seconds(t) for t in self.t)


@dataclass(frozen=True, slots=True)
class HistoryNoData:
    """``s: no_data``: the window holds no bars; ``next_time`` is the nearest earlier bar."""

    next_time: int | None


type AtlasHistory = HistoryBars | HistoryNoData


def parse_atlas_history(body: Json) -> AtlasHistory:
    """Atlas ``GET /v1/history`` with status 200: bars or ``no_data``."""
    where = "Atlas GET /v1/history"
    obj = _obj(body, where)
    status = _str(obj, "s", where)
    if status == "no_data":
        return HistoryNoData(_opt_int(obj, "nextTime", where))
    if status != "ok":
        raise MalformedResponseError(f"{where}.s is neither ok nor no_data")
    t = _int_tuple(_req(obj, "t", where), f"{where}.t")
    columns = {
        name: _num_tuple(_req(obj, name, where), f"{where}.{name}")
        for name in ("o", "h", "l", "c", "v")
    }
    sided: dict[str, tuple[float, ...] | None] = {}
    for name in ("bv", "sv", "uv"):
        raw = obj.get(name)
        sided[name] = None if raw is None else _num_tuple(raw, f"{where}.{name}")
    for name, col in (*columns.items(), *sided.items()):
        if col is not None and len(col) != len(t):
            raise MalformedResponseError(f"{where}.{name} does not have one value per bar")
    return HistoryBars(
        t=t,
        o=columns["o"],
        h=columns["h"],
        l=columns["l"],
        c=columns["c"],
        v=columns["v"],
        bv=sided["bv"],
        sv=sided["sv"],
        uv=sided["uv"],
    )


@dataclass(frozen=True, slots=True)
class AtlasHistoryError:
    """A rejected ``/v1/history`` body (``s: error``), read leniently.

    ``max_days`` is the trading-day cap a too-wide window hit; size the next
    windows from it.
    """

    requested_days: int | None
    max_days: int | None
    code: str | None

    @classmethod
    def parse(cls, body: Json) -> AtlasHistoryError:
        """Never raises; unreadable fields are ``None``. ``errmsg`` is not kept."""
        if not isinstance(body, dict):
            return cls(None, None, None)
        error = body.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        return cls(
            requested_days=_lenient_int(body, "requested_days", minimum=0),
            max_days=_lenient_int(body, "max_days", minimum=1),
            code=code if isinstance(code, str) and code else None,
        )


# ---------------------------------------------------------------- field helpers


def _obj(value: Json, where: str) -> JsonObject:
    if not isinstance(value, dict):
        raise MalformedResponseError(f"{where} is not a JSON object")
    return value


def _req(obj: JsonObject, name: str, where: str) -> Json:
    if name not in obj:
        raise MalformedResponseError(f"{where}.{name} is missing")
    return obj[name]


def _str(obj: JsonObject, name: str, where: str) -> str:
    value = _req(obj, name, where)
    if not isinstance(value, str) or not value:
        raise MalformedResponseError(f"{where}.{name} is not a non-empty string")
    return value


def _opt_str(obj: JsonObject, name: str, where: str) -> str | None:
    value = obj.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise MalformedResponseError(f"{where}.{name} is not a string")
    return value


def _bool(obj: JsonObject, name: str, where: str) -> bool:
    value = _req(obj, name, where)
    if not isinstance(value, bool):
        raise MalformedResponseError(f"{where}.{name} is not a boolean")
    return value


def _opt_bool(obj: JsonObject, name: str, where: str) -> bool | None:
    value = obj.get(name)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise MalformedResponseError(f"{where}.{name} is not a boolean")
    return value


def _int(obj: JsonObject, name: str, where: str) -> int:
    value = _req(obj, name, where)
    if isinstance(value, bool) or not isinstance(value, int):
        raise MalformedResponseError(f"{where}.{name} is not an integer")
    return value


def _opt_int(obj: JsonObject, name: str, where: str) -> int | None:
    if obj.get(name) is None:
        return None
    return _int(obj, name, where)


def _num(obj: JsonObject, name: str, where: str) -> float:
    return _number(_req(obj, name, where), f"{where}.{name}")


def _opt_num(obj: JsonObject, name: str, where: str) -> float | None:
    value = obj.get(name)
    return None if value is None else _number(value, f"{where}.{name}")


def _number(value: Json, where: str) -> float:
    """A finite JSON number as a float; JSON integers convert exactly when they fit."""
    if type(value) is float:
        if not math.isfinite(value):
            raise MalformedResponseError(f"{where} is not a finite number")
        return value
    if type(value) is int:
        try:
            return float(value)
        except OverflowError:
            raise MalformedResponseError(f"{where} is not a finite number") from None
    raise MalformedResponseError(f"{where} is not a number")


def _list(obj: JsonObject, name: str, where: str) -> list[object]:
    value = _req(obj, name, where)
    if not isinstance(value, list):
        raise MalformedResponseError(f"{where}.{name} is not a list")
    return value


def _num_tuple(value: Json, where: str) -> tuple[float, ...]:
    if not isinstance(value, list):
        raise MalformedResponseError(f"{where} is not a list")
    out: list[float] = []
    append = out.append
    for i, item in enumerate(value):
        # Fast path for the common case; `_number` names the bad index otherwise.
        if type(item) is float and math.isfinite(item):
            append(item)
        else:
            append(_number(item, f"{where}[{i}]"))
    return tuple(out)


def _int_tuple(value: Json, where: str) -> tuple[int, ...]:
    if not isinstance(value, list):
        raise MalformedResponseError(f"{where} is not a list")
    for i, item in enumerate(value):
        if type(item) is not int:
            raise MalformedResponseError(f"{where}[{i}] is not an integer")
    return tuple(cast(list[int], value))


def _str_tuple(value: Json, where: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise MalformedResponseError(f"{where} is not a list")
    for i, item in enumerate(value):
        if not isinstance(item, str):
            raise MalformedResponseError(f"{where}[{i}] is not a string")
    return tuple(cast(list[str], value))


def _instant(raw: str, where: str) -> Instant:
    try:
        return parse_rfc3339(raw)
    except ValueError:
        raise MalformedResponseError(f"{where} is not an RFC 3339 timestamp") from None


def _date(raw: str, where: str) -> date:
    if _ISO_DATE.fullmatch(raw) is None:
        raise MalformedResponseError(f"{where} is not a YYYY-MM-DD date")
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise MalformedResponseError(f"{where} is not a valid date") from None


def _lenient_int(obj: JsonObject, name: str, *, minimum: int | None) -> int | None:
    value = obj.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if minimum is not None and value < minimum:
        return None
    return value


def _lenient_bool(obj: JsonObject, name: str) -> bool | None:
    value = obj.get(name)
    return value if isinstance(value, bool) else None


def _extra_json(extra: Mapping[str, object]) -> str:
    """Canonical JSON of the unmapped fields (``{}`` when there are none)."""
    try:
        return canonical_json.dumps(dict(extra))
    except TypeError, ValueError:
        # Only a non-finite number can get here from json.loads output.
        raise MalformedResponseError("an unmapped field holds a non-finite number") from None
