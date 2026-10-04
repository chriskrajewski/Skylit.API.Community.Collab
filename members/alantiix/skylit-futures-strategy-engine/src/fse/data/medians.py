"""Trailing regime medians: a derived Data_Cache store (design §7, "Storage schemas").

The Regime_Classifier divides each raw GEX and VEX magnitude by the trailing
median of the same quantity (Req 7.2). This module precomputes those medians
once per Data_Cache, for every session of the exchange calendar:

- **Raw magnitude**: :func:`fse.engine.regime.raw_magnitude` of a Snapshot of
  the Regime_Symbol in one Heatmap_View, with the configured
  ``regime_distance_pct``.
- **RTH Snapshot**: ``asOf`` from the RTH open (09:30) up to but not including
  the RTH close (16:00, or the early close), as in ``fse calibrate``. A Snapshot
  stored in two adjacent Cache_Windows (the shared boundary) counts once, by
  its ``asOf``.
- **Trailing window** of session ``d`` and metric ``m``: the
  :data:`~fse.engine.regime.TRAILING_SESSIONS` (20) most recent sessions before
  ``d`` with at least one RTH Snapshot of ``m``. Sessions without one are
  skipped and not counted. Only earlier sessions are read, so no value at a
  Decision_Time depends on a later Snapshot (Req 5.3).
- **Median**: of every raw magnitude in the window, pooled (the mean of the
  two middle values for an even count).

Each session gets a :class:`~fse.engine.regime.TrailingMedian` with the number
of sessions it covers (1 to 20), or ``Unavailable`` when no earlier session
qualifies. A median covering fewer than 20 sessions or equal to 0 is stored as
such and marked ``usable = false``; the Regime then returns ``MissingInput``
for it (Req 7.12) through :attr:`TrailingMedian.usable`.

Layout under the cache root::

    derived/regime_medians/{view_id}/{params_hash}.parquet

``params_hash`` (:meth:`MedianParams.params_hash`) covers only what the medians
depend on: the Regime_Symbol, ``regime_distance_pct``, the trailing session
count and the file format. Other ``regime`` settings (``vanna_multiple``,
``min_abs_value``, ...) reuse the same file. Columns: ``session``, ``metric``,
``view_id``, ``params_hash``, ``median_raw_mag`` (null for ``Unavailable``),
``sessions`` (0 for ``Unavailable``) and ``usable``. One row per calendar
session and metric.

The file metadata holds ``inputs_sha256``: a hash of the parameters, the
calendar's sessions with their RTH bounds and every catalog row (status, row
count, sha256) of the Regime_Symbol's windows in the view.
:meth:`RegimeMedianStore.load_or_build` rebuilds when it differs, so a new
pull, a re-pulled window or a calendar change never leaves stale medians.
``complete`` windows are read and verified; ``no_data`` windows add nothing;
``incomplete`` windows are skipped and counted in
:attr:`RegimeMedians.incomplete_windows`.

Writes are atomic (temp file, fsync, rename) and run the path guard first
(Req 1.11, exit 2); other write failures raise ``CacheWriteError`` (exit 4).
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import deque
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

import numpy as np
import numpy.typing as npt
import pyarrow as pa

from fse.config.schema.data import DataConfig
from fse.config.schema.nodes import PCT_MAX
from fse.config.schema.regime import RegimeConfig
from fse.data.cache import CacheWindowKey, DataCache
from fse.data.cache_io import (
    CacheIntegrityError,
    CacheRoot,
    CacheWriteError,
    check_component,
    expect_meta,
    meta_text,
    parquet_to_table,
    read_verified,
    require_columns,
    sha256_hex,
    table_to_parquet,
    write_atomic,
)
from fse.data.catalog import WindowRecord
from fse.data.path_guard import PathGuardError
from fse.engine.regime import (
    TRAILING_SESSIONS,
    RegimeParams,
    TrailingMedian,
    TrailingMedians,
    raw_magnitude,
)
from fse.engine.types import Metric, Unavailable
from fse.logio.canonical_json import JsonValue, dumps, dumps_bytes
from fse.timekit import SessionCalendar

__all__ = [
    "MEDIANS_FORMAT",
    "MEDIAN_METRICS",
    "MedianParams",
    "RegimeMedianStore",
    "RegimeMedians",
    "no_history",
    "trailing_medians",
]

MEDIANS_FORMAT: Final = "fse.regime-medians/1"
MEDIAN_METRICS: Final[tuple[Metric, ...]] = ("gamma", "vanna")
"""The metrics with a trailing median: GEX (gamma) and VEX (vanna) magnitudes."""

_PARAMS_HASH_HEX: Final = 16
_SHA256_RE: Final = re.compile(r"[0-9a-f]{64}")
_SUBDIR: Final = ("derived", "regime_medians")

_SCHEMA: Final = pa.schema(
    [
        pa.field("session", pa.date32(), nullable=False),
        pa.field("metric", pa.string(), nullable=False),
        pa.field("view_id", pa.string(), nullable=False),
        pa.field("params_hash", pa.string(), nullable=False),
        pa.field("median_raw_mag", pa.float64()),
        pa.field("sessions", pa.int32(), nullable=False),
        pa.field("usable", pa.bool_(), nullable=False),
    ]
)
_REQUIRED: Final = tuple(f.name for f in _SCHEMA if not f.nullable)

type MedianValue = TrailingMedian | Unavailable


# ---------------------------------------------------------------- parameters


@dataclass(frozen=True, slots=True)
class MedianParams:
    """What the trailing medians depend on: the Regime_Symbol and the regime distance."""

    symbol: str
    regime_distance_pct: float

    def __post_init__(self) -> None:
        check_component("MedianParams.symbol", self.symbol)
        d = self.regime_distance_pct
        if (
            isinstance(d, bool)
            or not isinstance(d, int | float)
            or not math.isfinite(d)
            or not 0 < d <= PCT_MAX
        ):
            raise ValueError(
                f"MedianParams.regime_distance_pct must be above 0 and at most {PCT_MAX}, got {d!r}"
            )

    @classmethod
    def from_regime(cls, p: RegimeParams) -> MedianParams:
        """The parameters of the Regime_Classifier's :class:`RegimeParams`."""
        return cls(symbol=p.symbol, regime_distance_pct=p.config.regime_distance_pct)

    @classmethod
    def from_config(cls, regime: RegimeConfig, data: DataConfig) -> MedianParams:
        """The parameters of the ``regime`` and ``data`` config sections."""
        return cls(symbol=data.regime_symbol, regime_distance_pct=regime.regime_distance_pct)

    def to_json_obj(self) -> dict[str, JsonValue]:
        return {
            "format": MEDIANS_FORMAT,
            "regime_distance_pct": float(self.regime_distance_pct),
            "symbol": self.symbol,
            "trailing_sessions": TRAILING_SESSIONS,
        }

    def params_hash(self) -> str:
        """The first 16 hex characters of the sha256 of :meth:`to_json_obj` as canonical JSON."""
        return sha256_hex(dumps_bytes(self.to_json_obj()))[:_PARAMS_HASH_HEX]


# ---------------------------------------------------------------- the pure computation


def no_history(symbol: str, metric: Metric, session: date) -> Unavailable:
    """The trailing median of a session with no earlier qualifying session."""
    return Unavailable(
        f"no session before {session.isoformat()} has RTH {symbol} {metric} Snapshots"
    )


def trailing_medians(
    daily: Iterable[tuple[date, Sequence[float] | npt.NDArray[np.float64]]],
    *,
    symbol: str,
    metric: Metric,
) -> Iterator[tuple[date, MedianValue]]:
    """Each session's trailing median from the raw magnitudes of the sessions before it.

    ``daily`` gives every session in strictly increasing order with the raw
    magnitudes of its RTH Snapshots (empty when it has none). The median of a
    session pools the values of the :data:`TRAILING_SESSIONS` most recent
    earlier sessions with values; it is computed before the session's own
    values join the window. Raises ``ValueError`` for out-of-order sessions or
    a magnitude that is negative or not finite.
    """
    window: deque[npt.NDArray[np.float64]] = deque(maxlen=TRAILING_SESSIONS)
    previous: date | None = None
    for session, values in daily:
        if previous is not None and session <= previous:
            raise ValueError(f"sessions must strictly increase: {session} after {previous}")
        previous = session
        if window:
            median = float(np.median(np.concatenate(window)))
            yield session, TrailingMedian(median, len(window))
        else:
            yield session, no_history(symbol, metric, session)
        arr = np.asarray(values, dtype=np.float64)
        if arr.size:
            if not bool(np.all(np.isfinite(arr))) or bool(np.any(arr < 0)):
                raise ValueError(
                    f"raw magnitudes of {session} must be finite and >= 0 ({symbol} {metric})"
                )
            window.append(arr)


# ---------------------------------------------------------------- the result


@dataclass(frozen=True, slots=True)
class RegimeMedians:
    """The trailing medians of every calendar session for one view and parameter set.

    ``by_session`` maps each session to its :class:`TrailingMedians`, in
    session order. ``inputs_sha256`` identifies the inputs they were computed
    from; ``incomplete_windows`` counts the windows skipped because their
    write never finished.
    """

    view_id: str
    params: MedianParams
    inputs_sha256: str
    by_session: Mapping[date, TrailingMedians]
    incomplete_windows: int = 0

    def __post_init__(self) -> None:
        check_component("RegimeMedians.view_id", self.view_id)
        if not isinstance(self.params, MedianParams):
            raise ValueError(f"RegimeMedians.params must be MedianParams, got {self.params!r}")
        if not isinstance(self.inputs_sha256, str) or not _SHA256_RE.fullmatch(self.inputs_sha256):
            raise ValueError("RegimeMedians.inputs_sha256 must be 64 lowercase hex characters")
        n = self.incomplete_windows
        if isinstance(n, bool) or not isinstance(n, int) or n < 0:
            raise ValueError(f"RegimeMedians.incomplete_windows must be >= 0, got {n!r}")
        items = dict(self.by_session)
        for session, medians in items.items():
            if not isinstance(session, date) or isinstance(session, datetime):
                raise ValueError(f"RegimeMedians.by_session keys must be dates, got {session!r}")
            if not isinstance(medians, TrailingMedians):
                raise ValueError(f"RegimeMedians.by_session[{session}] must be TrailingMedians")
        object.__setattr__(self, "by_session", MappingProxyType(dict(sorted(items.items()))))

    @property
    def params_hash(self) -> str:
        return self.params.params_hash()

    def for_session(self, session: date) -> TrailingMedians:
        """The Regime_Classifier input of ``session``; ``ValueError`` if it is not a session."""
        try:
            return self.by_session[session]
        except KeyError:
            raise ValueError(
                f"no trailing medians for {session}: it is not a session of the calendar they "
                "were computed with"
            ) from None


def _of(medians: TrailingMedians, metric: Metric) -> MedianValue:
    return medians.gamma if metric == "gamma" else medians.vanna


# ---------------------------------------------------------------- the store


class RegimeMedianStore:
    """Build, write and read the derived trailing-median Parquet files of a Data_Cache."""

    __slots__ = ("_cache", "_root")

    def __init__(self, cache: DataCache) -> None:
        self._cache = cache
        self._root = CacheRoot(cache.root)

    def path(self, view_id: str, params: MedianParams) -> Path:
        check_component("view_id", view_id)
        return self._root.path.joinpath(*_SUBDIR, view_id, f"{params.params_hash()}.parquet")

    # ---------------------------------------------------------------- inputs

    def _records(self, view_id: str, params: MedianParams) -> dict[Metric, list[WindowRecord]]:
        catalog = self._cache.catalog
        return {
            m: catalog.windows(symbol=params.symbol, metric=m, view_id=view_id)
            for m in MEDIAN_METRICS
        }

    @staticmethod
    def _fingerprint(
        calendar: SessionCalendar,
        sessions: Sequence[date],
        view_id: str,
        params: MedianParams,
        records: Mapping[Metric, Sequence[WindowRecord]],
    ) -> str:
        h = hashlib.sha256(dumps_bytes({"params": params.to_json_obj(), "view_id": view_id}))
        for d in sessions:
            rth = f"{calendar.rth_open(d)} {calendar.rth_close(d)}"
            h.update(f"\nS {d.isoformat()} {rth}".encode())
        for metric in MEDIAN_METRICS:
            for r in records[metric]:
                h.update(
                    f"\nW {metric} {r.session.isoformat()} {r.start_ns} {r.status} {r.rows} "
                    f"{r.sha256}".encode()
                )
        return h.hexdigest()

    def inputs_sha256(
        self, calendar: SessionCalendar, *, view_id: str, params: MedianParams
    ) -> str:
        """The current inputs' hash; reads the catalog only."""
        records = self._records(view_id, params)
        return self._fingerprint(calendar, calendar.sessions(), view_id, params, records)

    def _rth_magnitudes(
        self,
        calendar: SessionCalendar,
        session: date,
        records: Sequence[WindowRecord],
        *,
        metric: Metric,
        view_id: str,
        params: MedianParams,
    ) -> npt.NDArray[np.float64]:
        lo, hi = calendar.rth_open(session), calendar.rth_close(session)
        seen: set[int] = set()
        values: list[float] = []
        for record in records:
            if record.status != "complete":
                continue
            key = CacheWindowKey(params.symbol, metric, view_id, session, record.start_ns)
            for snap in self._cache.read_window(key):
                if lo <= snap.as_of_ns < hi and snap.as_of_ns not in seen:
                    seen.add(snap.as_of_ns)
                    values.append(raw_magnitude(snap, params.regime_distance_pct))
        return np.asarray(values, dtype=np.float64)

    # ---------------------------------------------------------------- build

    def build(
        self, calendar: SessionCalendar, *, view_id: str, params: MedianParams
    ) -> RegimeMedians:
        """Compute the medians of every calendar session from the cached windows.

        Writes nothing. A cached window that fails its check raises
        ``CacheIntegrityError`` (exit 4).
        """
        check_component("view_id", view_id)
        sessions = calendar.sessions()
        records = self._records(view_id, params)
        incomplete = 0
        per_metric: dict[Metric, dict[date, MedianValue]] = {}
        for metric in MEDIAN_METRICS:
            by_session: dict[date, list[WindowRecord]] = {}
            for record in records[metric]:
                by_session.setdefault(record.session, []).append(record)
                if record.status == "incomplete":
                    incomplete += 1
            daily = (
                (
                    d,
                    self._rth_magnitudes(
                        calendar,
                        d,
                        by_session.get(d, ()),
                        metric=metric,
                        view_id=view_id,
                        params=params,
                    ),
                )
                for d in sessions
            )
            per_metric[metric] = dict(trailing_medians(daily, symbol=params.symbol, metric=metric))
        return RegimeMedians(
            view_id=view_id,
            params=params,
            inputs_sha256=self._fingerprint(calendar, sessions, view_id, params, records),
            by_session={
                d: TrailingMedians(gamma=per_metric["gamma"][d], vanna=per_metric["vanna"][d])
                for d in sessions
            },
            incomplete_windows=incomplete,
        )

    def load_or_build(
        self, calendar: SessionCalendar, *, view_id: str, params: MedianParams
    ) -> RegimeMedians:
        """The stored medians when their inputs are unchanged, else rebuilt and written.

        A stored file that is unreadable or malformed is rebuilt too: it holds
        derived data only.
        """
        current = self.inputs_sha256(calendar, view_id=view_id, params=params)
        try:
            stored = self.read(view_id, params)
        except CacheIntegrityError:
            stored = None
        if stored is not None and stored.inputs_sha256 == current:
            return stored
        built = self.build(calendar, view_id=view_id, params=params)
        self.write(built)
        return built

    # ---------------------------------------------------------------- files

    def write(self, medians: RegimeMedians) -> Path:
        """Store ``medians`` atomically; returns the file path."""
        path = self.path(medians.view_id, medians.params)
        target = f"trailing regime medians (view {medians.view_id}, params {medians.params_hash})"
        try:
            self._root.ensure_writable()
            write_atomic(path, _encode(medians))
        except PathGuardError:
            raise
        except Exception as exc:
            raise CacheWriteError(target, exc) from exc
        return path

    def read(self, view_id: str, params: MedianParams) -> RegimeMedians | None:
        """The stored medians, or ``None`` when no file exists. Creates nothing.

        Raises ``CacheIntegrityError`` when the file is unreadable or does not
        hold medians of this view and parameter set.
        """
        path = self.path(view_id, params)
        if not path.exists():
            return None
        return _decode(read_verified(path, None), path, view_id, params)


# ---------------------------------------------------------------- encoding


def _encode(m: RegimeMedians) -> bytes:
    cols: dict[str, list[Any]] = {name: [] for name in _SCHEMA.names}
    params_hash = m.params_hash
    for session, medians in m.by_session.items():
        for metric in MEDIAN_METRICS:
            value = _of(medians, metric)
            cols["session"].append(session)
            cols["metric"].append(metric)
            cols["view_id"].append(m.view_id)
            cols["params_hash"].append(params_hash)
            if isinstance(value, TrailingMedian):
                cols["median_raw_mag"].append(value.median)
                cols["sessions"].append(value.sessions)
                cols["usable"].append(value.usable)
            else:
                cols["median_raw_mag"].append(None)
                cols["sessions"].append(0)
                cols["usable"].append(False)
    metadata = {
        b"fse.format": MEDIANS_FORMAT.encode(),
        b"fse.symbol": m.params.symbol.encode(),
        b"fse.view_id": m.view_id.encode(),
        b"fse.params_hash": params_hash.encode(),
        b"fse.params": dumps(m.params.to_json_obj()).encode(),
        b"fse.inputs_sha256": m.inputs_sha256.encode(),
        b"fse.incomplete_windows": str(m.incomplete_windows).encode(),
    }
    table = pa.Table.from_pydict(cols, schema=_SCHEMA).replace_schema_metadata(metadata)
    return table_to_parquet(table)


def _decode(data: bytes, path: Path, view_id: str, params: MedianParams) -> RegimeMedians:
    table = parquet_to_table(data, source=path)
    metadata = table.schema.metadata
    params_hash = params.params_hash()
    expect_meta(
        metadata,
        {
            "fse.format": MEDIANS_FORMAT,
            "fse.symbol": params.symbol,
            "fse.view_id": view_id,
            "fse.params_hash": params_hash,
            "fse.params": dumps(params.to_json_obj()),
        },
        source=path,
    )
    require_columns(table, _SCHEMA.names, non_null=_REQUIRED, source=path)
    cols: dict[str, list[Any]] = {name: table.column(name).to_pylist() for name in _SCHEMA.names}
    values: dict[date, dict[Metric, MedianValue]] = {}
    try:
        incomplete = int(meta_text(metadata, "fse.incomplete_windows", source=path))
        for i in range(table.num_rows):
            session, metric = cols["session"][i], cols["metric"][i]
            if (cols["view_id"][i], cols["params_hash"][i]) != (view_id, params_hash):
                raise ValueError(f"row {i} belongs to another view or parameter set")
            if metric not in MEDIAN_METRICS:
                raise ValueError(f"row {i} has metric {metric!r}")
            median, sessions = cols["median_raw_mag"][i], cols["sessions"][i]
            value: MedianValue
            if sessions == 0 and median is None:
                value = no_history(params.symbol, metric, session)
            elif median is None:
                raise ValueError(f"row {i} covers {sessions} sessions but has no median")
            else:
                value = TrailingMedian(median, sessions)
            usable = isinstance(value, TrailingMedian) and value.usable
            if cols["usable"][i] != usable:
                raise ValueError(f"row {i} has usable {cols['usable'][i]}, expected {usable}")
            if metric in values.setdefault(session, {}):
                raise ValueError(f"session {session} has more than one {metric} row")
            values[session][metric] = value
        by_session: dict[date, TrailingMedians] = {}
        for d, v in values.items():
            if set(v) != set(MEDIAN_METRICS):
                raise ValueError(f"session {d} lacks a row for one of {MEDIAN_METRICS}")
            by_session[d] = TrailingMedians(gamma=v["gamma"], vanna=v["vanna"])
        return RegimeMedians(
            view_id=view_id,
            params=params,
            inputs_sha256=meta_text(metadata, "fse.inputs_sha256", source=path),
            by_session=by_session,
            incomplete_windows=incomplete,
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise CacheIntegrityError(f"cached file {path} holds invalid medians: {exc}") from exc
