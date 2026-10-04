"""The heatmap Data_Cache (design §3 "Puller and Data_Cache", D5, "Storage schemas").

Layout under the cache root (default ``~/.skylit-fse/cache``)::

    heatmaps/{symbol}/{metric}/{view_id}/{YYYY-MM-DD}/{HHMM}.parquet
    bars/{instrument}/{interval}s/{YYYY-MM-DD}.parquet
    vix/bars/{instrument}/{interval}s/{YYYY-MM-DD}.parquet
    vix/daily.parquet
    darkpool/{ticker}/{YYYY-MM}.parquet
    catalog.sqlite

``HHMM`` is the Cache_Window start in New York time. A Cache_Window is keyed by
symbol, metric, Heatmap_View (``view_id``), session date and start (Req 3.6).

Window write protocol (Req 3.9-3.10):

1. Mark the window ``incomplete`` in the catalog.
2. Write the Parquet file to ``{HHMM}.parquet.tmp``, fsync it, rename it.
3. In one transaction, set ``complete`` (or ``no_data`` when no Snapshot was
   returned) with the row count, ``source_endpoint`` and file sha256.

Any exception before step 3 leaves the window ``incomplete``, so the next pull
requests it again. Write failures raise :class:`CacheWriteError` naming the
symbol, metric, session date and window start (Req 3.15, exit 4). The path
guard runs before the first write (Req 1.11, exit 2).

Window file: one row per Snapshot with ``as_of_ns``, ``as_of_raw``, ``spot``,
``previous_close``, ``axis_id``, ``values``, ``node_types``, ``resolution``,
``source_endpoint`` and ``extra_json``. Strikes and expirations live once per
distinct axis in an axis table (``axis_id``, ``strikes``, ``expirations``),
stored as an Arrow IPC stream in the file metadata next to the symbol, metric,
view JSON, session, window start and window ``source_endpoint``. Every float is
stored as float64 and every string as written, so a read returns Snapshots
equal in every field to those written (Req 3.10). ``source_endpoint`` is kept
per row as well as per window, because a window may mix range frames with
``/v1/historical`` label samples and the Snapshot field must round-trip.
"""

from __future__ import annotations

import json
import re
import time
from array import array
from bisect import bisect_right
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Final, Literal, Self

import pyarrow as pa

from fse.data.aux_stores import BarStore, DarkPoolStore, VixStore
from fse.data.cache_io import (
    CacheIntegrityError,
    CacheReadError,
    CacheRoot,
    CacheWriteError,
    check_component,
    expect_meta,
    ipc_to_table,
    meta_bytes,
    parquet_to_table,
    read_verified,
    require_columns,
    sha256_hex,
    table_to_ipc,
    table_to_parquet,
    write_atomic,
)
from fse.data.catalog import CATALOG_FILE_NAME, Catalog, WindowRecord, WindowStatus
from fse.data.path_guard import PathGuardError
from fse.engine.types import METRICS, SOURCE_ENDPOINTS, Metric, Snapshot
from fse.settings import default_paths
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, Instant, SessionCalendar, ny_datetime

__all__ = [
    "CACHE_WINDOW_MINUTES",
    "CACHE_WINDOW_NS",
    "DEFAULT_MAX_EXPIRATIONS",
    "DEFAULT_MAX_STRIKES",
    "STORAGE_INTERVAL_MAX_S",
    "STORAGE_INTERVAL_MIN_S",
    "WINDOW_FORMAT",
    "CacheWindowKey",
    "DataCache",
    "HeatmapView",
    "cache_window_end",
    "cache_window_starts",
    "check_storage_interval",
    "filter_storage_interval",
    "storage_boundaries",
]

CACHE_WINDOW_MINUTES: Final = 15
CACHE_WINDOW_NS: Final = CACHE_WINDOW_MINUTES * NS_PER_MINUTE

DEFAULT_MAX_STRIKES: Final = 92
DEFAULT_MAX_EXPIRATIONS: Final = 5

STORAGE_INTERVAL_MIN_S: Final = 1
STORAGE_INTERVAL_MAX_S: Final = 300
_CACHE_WINDOW_S: Final = CACHE_WINDOW_NS // NS_PER_SECOND
_STORAGE_INTERVALS_S: Final = tuple(
    s for s in range(STORAGE_INTERVAL_MIN_S, STORAGE_INTERVAL_MAX_S + 1) if _CACHE_WINDOW_S % s == 0
)

WINDOW_FORMAT: Final = "fse.heatmap-window/1"
_VIEW_ID_HEX: Final = 16
_VIEW_ID_RE: Final = re.compile(r"[0-9a-f]{16}")

type ViewLimit = int | Literal["all"]


def _canonical_json(value: object) -> str:
    """Sorted keys, compact separators, UTF-8, no NaN.

    A private stand-in until ``fse.logio.canonical_json`` (task 2.2) lands. The
    view JSON holds only ints, bools, ASCII strings and nulls, for which every
    canonical form of that design definition gives the same bytes.
    """
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _check_limit(name: str, value: object) -> None:
    if value == "all":
        return
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer or 'all', got {value!r}")


# ---------------------------------------------------------------- keys


@dataclass(frozen=True, slots=True)
class HeatmapView:
    """The Heatmap_View parameters a Snapshot was fetched with.

    ``view_id()`` is the first 16 hex characters of the sha256 of the view's
    canonical JSON. Different parameters give different ids, so Snapshots of
    different views never share a Cache_Window key.
    """

    max_strikes: ViewLimit = DEFAULT_MAX_STRIKES
    max_expirations: ViewLimit = DEFAULT_MAX_EXPIRATIONS
    expirations: tuple[str, ...] | None = None
    include_empty: bool = False

    def __post_init__(self) -> None:
        _check_limit("max_strikes", self.max_strikes)
        _check_limit("max_expirations", self.max_expirations)
        if self.expirations is not None:
            if not isinstance(self.expirations, tuple) or not self.expirations:
                raise ValueError("expirations must be None or a non-empty tuple of strings")
            for item in self.expirations:
                if not isinstance(item, str) or not item.strip():
                    raise ValueError(f"expirations entries must be non-blank strings: {item!r}")
        if not isinstance(self.include_empty, bool):
            raise ValueError(f"include_empty must be a bool, got {self.include_empty!r}")

    def to_json_obj(self) -> dict[str, object]:
        return {
            "expirations": None if self.expirations is None else list(self.expirations),
            "include_empty": self.include_empty,
            "max_expirations": self.max_expirations,
            "max_strikes": self.max_strikes,
        }

    def canonical_json(self) -> str:
        return _canonical_json(self.to_json_obj())

    def view_id(self) -> str:
        return sha256_hex(self.canonical_json().encode())[:_VIEW_ID_HEX]


@dataclass(frozen=True, slots=True)
class CacheWindowKey:
    """Symbol, metric, Heatmap_View, session date and Cache_Window start (Req 3.6).

    ``start_ns`` must fall on a whole minute of the session's New York date.
    """

    symbol: str
    metric: Metric
    view_id: str
    session: date
    start_ns: Instant

    def __post_init__(self) -> None:
        check_component("symbol", self.symbol)
        if self.metric not in METRICS:
            raise ValueError(f"metric must be one of {sorted(METRICS)}, got {self.metric!r}")
        if not isinstance(self.view_id, str) or _VIEW_ID_RE.fullmatch(self.view_id) is None:
            raise ValueError(f"view_id must be 16 lowercase hex characters, got {self.view_id!r}")
        if not isinstance(self.session, date) or isinstance(self.session, datetime):
            raise ValueError(f"session must be a date, got {self.session!r}")
        if not isinstance(self.start_ns, int) or isinstance(self.start_ns, bool):
            raise ValueError(f"start_ns must be an integer Instant, got {self.start_ns!r}")
        if self.start_ns % NS_PER_MINUTE:
            raise ValueError(f"start_ns {self.start_ns} is not on a whole minute")
        local = ny_datetime(self.start_ns)
        if local.date() != self.session:
            raise ValueError(
                f"start_ns {self.start_ns} ({local.isoformat()}) is not on session {self.session}"
            )

    @classmethod
    def for_view(
        cls, symbol: str, metric: Metric, view: HeatmapView, session: date, start_ns: Instant
    ) -> CacheWindowKey:
        return cls(symbol, metric, view.view_id(), session, start_ns)

    def hhmm(self) -> str:
        """The New York start time as ``HHMM``, the window's file name."""
        return ny_datetime(self.start_ns).strftime("%H%M")

    def describe(self) -> str:
        start = ny_datetime(self.start_ns).strftime("%H:%M")
        return (
            f"{self.symbol} {self.metric} (view {self.view_id}) session {self.session} "
            f"Cache_Window start {start} New York (start_ns {self.start_ns})"
        )


# ---------------------------------------------------------------- window tiling


def cache_window_starts(pull_start_ns: Instant, pull_end_ns: Instant) -> range:
    """Cache_Window starts tiling ``[pull_start, pull_end)`` every 15 minutes."""
    return range(pull_start_ns, pull_end_ns, CACHE_WINDOW_NS)


def cache_window_end(start_ns: Instant, pull_end_ns: Instant) -> Instant:
    """The window end: 15 minutes after the start, or the Pull_Window end if sooner."""
    return min(start_ns + CACHE_WINDOW_NS, pull_end_ns)


# ---------------------------------------------------------------- storage-interval filter


def check_storage_interval(interval_s: object) -> int:
    """``interval_s`` if it is a whole number of seconds from 1 to 300 dividing 900 (Req 3.7).

    Each Cache_Window is filtered on its own, with boundaries from its start.
    An interval that divides the 900 s Cache_Window keeps those boundaries
    evenly spaced across window seams.
    """
    if (
        not isinstance(interval_s, int)
        or isinstance(interval_s, bool)
        or not STORAGE_INTERVAL_MIN_S <= interval_s <= STORAGE_INTERVAL_MAX_S
    ):
        raise ValueError(
            f"storage interval must be a whole number of seconds from {STORAGE_INTERVAL_MIN_S} "
            f"to {STORAGE_INTERVAL_MAX_S}, got {interval_s!r}"
        )
    if _CACHE_WINDOW_S % interval_s:
        raise ValueError(
            f"storage interval must divide the {_CACHE_WINDOW_S} s Cache_Window, got "
            f"{interval_s} s; use one of {', '.join(map(str, _STORAGE_INTERVALS_S))}"
        )
    return interval_s


def storage_boundaries(
    origin_ns: Instant, interval_s: int, *, last_ns: Instant, first_ns: Instant | None = None
) -> range:
    """Boundaries ``origin + k * interval`` (k >= 0) with ``first <= b <= last``.

    ``origin`` is the Cache_Window start and ``last`` its end (Req 3.7);
    ``first`` defaults to ``origin``.
    """
    step = check_storage_interval(interval_s) * NS_PER_SECOND
    lo = origin_ns if first_ns is None else max(first_ns, origin_ns)
    k = -((origin_ns - lo) // step)  # ceil((lo - origin) / step)
    return range(origin_ns + k * step, last_ns + 1, step)


def filter_storage_interval(
    snapshots: Iterable[Snapshot],
    *,
    origin_ns: Instant,
    interval_s: int,
    last_ns: Instant,
    first_ns: Instant | None = None,
) -> list[Snapshot]:
    """Keep, for each boundary, the Snapshot with the latest ``asOf`` at or before it.

    Boundaries are :func:`storage_boundaries`. A Snapshot that is the latest for
    several consecutive boundaries is kept once. Among Snapshots with the same
    ``asOf``, the last one given wins. The result is in ``asOf`` order, and every
    other Snapshot is dropped (Req 3.7).
    """
    ordered = sorted(snapshots, key=lambda s: s.as_of_ns)  # stable: input order on ties
    as_of = [s.as_of_ns for s in ordered]
    keep: set[int] = set()
    for boundary in storage_boundaries(origin_ns, interval_s, last_ns=last_ns, first_ns=first_ns):
        i = bisect_right(as_of, boundary)
        if i:
            keep.add(i - 1)
    return [ordered[i] for i in sorted(keep)]


# ---------------------------------------------------------------- window files

_WINDOW_SCHEMA: Final = pa.schema(
    [
        pa.field("as_of_ns", pa.int64(), nullable=False),
        pa.field("as_of_raw", pa.string(), nullable=False),
        pa.field("spot", pa.float64(), nullable=False),
        pa.field("previous_close", pa.float64()),
        pa.field("axis_id", pa.int32(), nullable=False),
        pa.field("values", pa.list_(pa.float64()), nullable=False),
        pa.field("node_types", pa.list_(pa.string())),
        pa.field("resolution", pa.string(), nullable=False),
        pa.field("source_endpoint", pa.string(), nullable=False),
        pa.field("extra_json", pa.string(), nullable=False),
    ]
)
_AXIS_SCHEMA: Final = pa.schema(
    [
        pa.field("axis_id", pa.int32(), nullable=False),
        pa.field("strikes", pa.list_(pa.float64()), nullable=False),
        pa.field("expirations", pa.list_(pa.string()), nullable=False),
    ]
)
_REQUIRED_COLUMNS: Final = tuple(f.name for f in _WINDOW_SCHEMA if not f.nullable)


def _encode_window(
    key: CacheWindowKey,
    snapshots: Sequence[Snapshot],
    source_endpoint: str,
    view: HeatmapView | None,
    interval_s: int | None,
) -> bytes:
    axis_ids: dict[tuple[bytes, tuple[str, ...]], int] = {}
    axis_strikes: list[list[float]] = []
    axis_expirations: list[list[str]] = []
    frame_axis: list[int] = []
    for snap in snapshots:
        # Bit patterns, so -0.0 and 0.0 (or two NaNs) never share an axis.
        axis_key = (array("d", snap.strikes).tobytes(), snap.expirations)
        axis_id = axis_ids.get(axis_key)
        if axis_id is None:
            axis_id = len(axis_ids)
            axis_ids[axis_key] = axis_id
            axis_strikes.append(list(snap.strikes))
            axis_expirations.append(list(snap.expirations))
        frame_axis.append(axis_id)
    axes = pa.Table.from_pydict(
        {
            "axis_id": list(range(len(axis_strikes))),
            "strikes": axis_strikes,
            "expirations": axis_expirations,
        },
        schema=_AXIS_SCHEMA,
    )
    frames = pa.Table.from_pydict(
        {
            "as_of_ns": [s.as_of_ns for s in snapshots],
            "as_of_raw": [s.as_of_raw for s in snapshots],
            "spot": [s.spot for s in snapshots],
            "previous_close": [s.previous_close for s in snapshots],
            "axis_id": frame_axis,
            "values": [list(s.values) for s in snapshots],
            "node_types": [None if s.node_types is None else list(s.node_types) for s in snapshots],
            "resolution": [s.resolution for s in snapshots],
            "source_endpoint": [s.source_endpoint for s in snapshots],
            "extra_json": [s.extra_json for s in snapshots],
        },
        schema=_WINDOW_SCHEMA,
    )
    metadata: dict[bytes, bytes] = {
        b"fse.format": WINDOW_FORMAT.encode(),
        b"fse.symbol": key.symbol.encode(),
        b"fse.metric": key.metric.encode(),
        b"fse.view_id": key.view_id.encode(),
        b"fse.view": b"" if view is None else view.canonical_json().encode(),
        b"fse.session": key.session.isoformat().encode(),
        b"fse.window_start_ns": str(key.start_ns).encode(),
        b"fse.source_endpoint": source_endpoint.encode(),
        b"fse.storage_interval_s": b"" if interval_s is None else str(interval_s).encode(),
        b"fse.axes": table_to_ipc(axes),
    }
    return table_to_parquet(frames.replace_schema_metadata(metadata))


def _decode_window(key: CacheWindowKey, data: bytes, path: Path) -> list[Snapshot]:
    table = parquet_to_table(data, source=path)
    metadata = table.schema.metadata
    expect_meta(
        metadata,
        {
            "fse.format": WINDOW_FORMAT,
            "fse.symbol": key.symbol,
            "fse.metric": key.metric,
            "fse.view_id": key.view_id,
            "fse.session": key.session.isoformat(),
            "fse.window_start_ns": str(key.start_ns),
        },
        source=path,
    )
    axes = ipc_to_table(meta_bytes(metadata, "fse.axes", source=path), source=path)
    require_columns(table, _WINDOW_SCHEMA.names, non_null=_REQUIRED_COLUMNS, source=path)
    require_columns(axes, _AXIS_SCHEMA.names, non_null=_AXIS_SCHEMA.names, source=path)
    names = table.column_names
    axis_map: dict[int, tuple[tuple[float, ...], tuple[str, ...]]] = {
        axis_id: (tuple(strikes), tuple(expirations))
        for axis_id, strikes, expirations in zip(
            axes.column("axis_id").to_pylist(),
            axes.column("strikes").to_pylist(),
            axes.column("expirations").to_pylist(),
            strict=True,
        )
    }
    cols: dict[str, list[Any]] = {name: table.column(name).to_pylist() for name in names}
    out: list[Snapshot] = []
    try:
        for i in range(table.num_rows):
            strikes, expirations = axis_map[cols["axis_id"][i]]
            node_types = cols["node_types"][i]
            out.append(
                Snapshot(
                    symbol=key.symbol,
                    metric=key.metric,
                    view_id=key.view_id,
                    as_of_ns=cols["as_of_ns"][i],
                    as_of_raw=cols["as_of_raw"][i],
                    spot=cols["spot"][i],
                    previous_close=cols["previous_close"][i],
                    strikes=strikes,
                    values=tuple(cols["values"][i]),
                    node_types=None if node_types is None else tuple(node_types),
                    expirations=expirations,
                    resolution=cols["resolution"][i],
                    source_endpoint=cols["source_endpoint"][i],
                    extra_json=cols["extra_json"][i],
                )
            )
    except (KeyError, ValueError, TypeError) as exc:
        raise CacheIntegrityError(f"cached file {path} holds an invalid Snapshot: {exc}") from exc
    return out


# ---------------------------------------------------------------- the cache


@dataclass(frozen=True, slots=True)
class _Bounds:
    start: Instant
    end: Instant


class DataCache:
    """Heatmap windows plus the bar, VIX and dark-pool stores under one root.

    ``calendar`` lets the cache check that each key is a Cache_Window start of
    its session's Pull_Window, and is required when ``storage_interval_s`` is
    set: the filter needs each window's start and end. Nothing is created on
    disk until the first write, and the path guard runs first.
    """

    __slots__ = ("_calendar", "_interval_s", "_root", "bars", "catalog", "darkpool", "vix")

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        calendar: SessionCalendar | None = None,
        storage_interval_s: int | None = None,
        clock: Callable[[], Instant] = time.time_ns,
    ) -> None:
        if storage_interval_s is not None:
            check_storage_interval(storage_interval_s)
            if calendar is None:
                raise ValueError("a storage interval needs the session calendar")
        self._root = CacheRoot(default_paths().cache if root is None else root)
        self._calendar = calendar
        self._interval_s = storage_interval_s
        self.catalog = Catalog(
            self._root.path / CATALOG_FILE_NAME,
            before_write=self._root.ensure_writable,
            clock=clock,
        )
        self.bars = BarStore(self._root, self.catalog, dataset="bars", subdir=("bars",))
        self.vix = VixStore(self._root, self.catalog)
        self.darkpool = DarkPoolStore(self._root)

    @property
    def root(self) -> Path:
        return self._root.path

    @property
    def storage_interval_s(self) -> int | None:
        return self._interval_s

    def close(self) -> None:
        self.catalog.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ---------------------------------------------------------------- windows

    def window_path(self, key: CacheWindowKey) -> Path:
        return (
            self._root.path
            / "heatmaps"
            / key.symbol
            / key.metric
            / key.view_id
            / key.session.isoformat()
            / f"{key.hhmm()}.parquet"
        )

    def window_bounds(self, key: CacheWindowKey) -> tuple[Instant, Instant]:
        """``(start, end)`` of the window; needs the calendar."""
        bounds = self._bounds(key)
        if bounds is None:
            raise ValueError("window bounds need the session calendar")
        return bounds.start, bounds.end

    def _bounds(self, key: CacheWindowKey) -> _Bounds | None:
        if self._calendar is None:
            return None
        pull_start, pull_end = self._calendar.pull_window(key.session)
        if key.start_ns not in cache_window_starts(pull_start, pull_end):
            raise ValueError(
                f"{key.describe()} is not a Cache_Window start of the session's Pull_Window"
            )
        return _Bounds(key.start_ns, cache_window_end(key.start_ns, pull_end))

    def record(self, key: CacheWindowKey) -> WindowRecord | None:
        return self.catalog.window(key)

    def status(self, key: CacheWindowKey) -> WindowStatus:
        """``absent``, ``incomplete``, ``complete`` or ``no_data``. Creates nothing."""
        self._bounds(key)
        return self.catalog.window_status(key)

    def mark_incomplete(self, key: CacheWindowKey) -> None:
        """Mark the window ``incomplete`` (before its first Replay_Request)."""
        self._bounds(key)
        try:
            self.catalog.mark_window_incomplete(key)
        except PathGuardError:
            raise
        except Exception as exc:
            raise CacheWriteError(key.describe(), exc) from exc

    def write_window(
        self,
        key: CacheWindowKey,
        snapshots: Iterable[Snapshot],
        source_endpoint: str,
        *,
        view: HeatmapView | None = None,
    ) -> None:
        """Store a window's Snapshots with the three-step write protocol.

        An empty ``snapshots`` (every request returned ``no_data``) stores the
        window as ``no_data`` with 0 rows. With a storage interval, only the
        Snapshots that :func:`filter_storage_interval` keeps for the window's
        boundaries (window start + k x interval, from the window start to the
        window end, both inclusive) are stored.
        Arguments that do not fit the key raise ``ValueError`` before anything
        is written.
        """
        snaps = tuple(snapshots)
        bounds = self._bounds(key)
        if source_endpoint not in SOURCE_ENDPOINTS:
            raise ValueError(
                f"source_endpoint must be one of {sorted(SOURCE_ENDPOINTS)}, "
                f"got {source_endpoint!r}"
            )
        if view is not None and view.view_id() != key.view_id:
            raise ValueError(f"view {view.canonical_json()} does not have view_id {key.view_id}")
        for i, snap in enumerate(snaps):
            if not isinstance(snap, Snapshot):
                raise ValueError(f"snapshots[{i}] is not a Snapshot: {type(snap).__name__}")
            if (snap.symbol, snap.metric, snap.view_id) != (key.symbol, key.metric, key.view_id):
                raise ValueError(
                    f"snapshots[{i}] is {snap.symbol} {snap.metric} view {snap.view_id}, "
                    f"not {key.symbol} {key.metric} view {key.view_id}"
                )
        try:
            self.catalog.mark_window_incomplete(key)  # 1
            if self._interval_s is None or bounds is None:
                kept = sorted(snaps, key=lambda s: s.as_of_ns)
            else:
                kept = filter_storage_interval(
                    snaps,
                    origin_ns=bounds.start,
                    interval_s=self._interval_s,
                    last_ns=bounds.end,
                )
            data = _encode_window(key, kept, source_endpoint, view, self._interval_s)
            write_atomic(self.window_path(key), data)  # 2
            self.catalog.finish_window(  # 3
                key,
                status="complete" if snaps else "no_data",
                rows=len(kept),
                source_endpoint=source_endpoint,
                sha256=sha256_hex(data),
            )
        except PathGuardError:
            raise
        except Exception as exc:
            raise CacheWriteError(key.describe(), exc) from exc

    def read_window(self, key: CacheWindowKey) -> list[Snapshot]:
        """The stored Snapshots in ``asOf`` order; ``[]`` for a ``no_data`` window.

        Raises :class:`CacheReadError` for an absent or incomplete window and
        :class:`CacheIntegrityError` when the file is missing, does not match
        the catalog's sha256 or row count, or does not belong to ``key``.
        """
        self._bounds(key)
        record = self.catalog.window(key)
        if record is None or record.status == "incomplete":
            status = "absent" if record is None else record.status
            raise CacheReadError(
                f"{key.describe()} is {status}; only complete and no_data windows can be read"
            )
        path = self.window_path(key)
        snaps = _decode_window(key, read_verified(path, record.sha256), path)
        if len(snaps) != record.rows:
            raise CacheIntegrityError(
                f"cached file {path} holds {len(snaps)} Snapshots; the catalog records "
                f"{record.rows}"
            )
        return snaps
