"""Plan: read-only discovery of the Data_Cache (design §8.1, review NIT 6).

The catalog is opened with ``sqlite3`` in ``mode=ro`` (never through
:class:`fse.data.catalog.Catalog`, whose connection is ``mode=rw``). Read-only
mode with ``journal_mode=delete`` creates no file, takes only shared locks and
coexists with a running ``fse pull``. Cache paths come only from the pure
layout functions of :class:`fse.data.cache.DataCache`; no other ``DataCache``
or ``Catalog`` method is called. Uncatalogued stores are found by ``*.parquet``
globs, so ``.DS_Store`` and ``*.tmp`` files are ignored.

This module writes nothing. It reads only the catalog, ``stat`` results,
Parquet footers and the bytes of the small uncatalogued files (to hash them).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Final

import pyarrow as pa
import pyarrow.parquet as pq

from fse.awsexport.errors import ExportDataError, ExportInputError
from fse.awsexport.options import ExportOptions
from fse.awsexport.tables import (
    EXPORT_FORMAT,
    MONTH_RE,
    PARTITION_VALUE_RE,
    TABLES,
    partition_rel,
)
from fse.data.cache import CacheWindowKey, DataCache
from fse.data.cache_io import CacheIntegrityError
from fse.data.catalog import CATALOG_FILE_NAME, SCHEMA_VERSION

__all__ = [
    "BarInput",
    "FileInput",
    "PartitionPlan",
    "Plan",
    "SourceInput",
    "WindowInput",
    "open_catalog_ro",
    "plan",
]

_BUSY_TIMEOUT_S: Final = 30
_BAR_TABLES: Final = {"bars": "bars", "vix_bars": "vix"}  # table -> catalog dataset
_HASH_CHUNK: Final = 1 << 20


# ---------------------------------------------------------------- inputs


@dataclass(frozen=True, slots=True)
class WindowInput:
    """One ``complete`` heatmap Cache_Window from the catalog."""

    path: Path
    symbol: str
    metric: str
    view_id: str
    session: date
    start_ns: int
    rows: int
    sha256: str
    source_endpoint: str


@dataclass(frozen=True, slots=True)
class BarInput:
    """One ``complete`` bar session from ``bars_coverage``."""

    path: Path
    dataset: str
    instrument: str
    interval_s: int
    session: date
    rows: int
    sha256: str
    contract: str
    source: str


@dataclass(frozen=True, slots=True)
class FileInput:
    """One uncatalogued file: ``vix/daily.parquet``, a dark-pool month or a medians file.

    ``key`` holds the values the path encodes: ``(ticker, month)`` for dark
    pool, ``(view_id, params_hash)`` for medians, ``()`` for VIX daily.
    """

    path: Path
    rel: str  # relative to the cache root, POSIX separators
    size: int
    sha256: str
    key: tuple[str, ...]


type SourceInput = WindowInput | BarInput | FileInput


@dataclass(frozen=True, slots=True)
class PartitionPlan:
    """One output partition and the source inputs it is built from."""

    table: str
    values: tuple[tuple[str, str], ...]  # in partition-key order
    rel_path: str
    inputs: tuple[SourceInput, ...]
    fingerprint: str
    source_rows: int  # computed from the source, independently of the transform
    source_bytes: int

    @property
    def value_map(self) -> dict[str, str]:
        return dict(self.values)


@dataclass(frozen=True, slots=True)
class Plan:
    """Everything the stage needs, plus what the report shows."""

    cache_dir: Path
    tables: tuple[str, ...]  # the selected tables, in registry order
    partitions: tuple[PartitionPlan, ...]
    window_count: int
    bar_session_count: int
    skipped: Mapping[str, Mapping[str, int]]  # table -> {"incomplete": n, "no_data": n}
    tables_without_data: tuple[str, ...]
    warnings: tuple[str, ...]
    # Partition-key filters per table, for pruning: a staged partition outside
    # them is not this run's business. Tables in ``partial`` were planned with a
    # test-only session filter and are never pruned.
    scope: Mapping[str, Mapping[str, frozenset[str]]]
    partial: frozenset[str]

    @property
    def source_bytes(self) -> int:
        return sum(p.source_bytes for p in self.partitions)

    def in_scope(self, table: str, values: Mapping[str, str]) -> bool:
        """Whether a staged partition of ``table`` falls under this plan's filters."""
        if table not in self.tables or table in self.partial:
            return False
        filters = self.scope.get(table, {})
        return all(values.get(key) in allowed for key, allowed in filters.items())


# ---------------------------------------------------------------- the catalog


def open_catalog_ro(path: Path) -> sqlite3.Connection:
    """A read-only connection to ``catalog.sqlite``; ``ExportDataError`` (exit 4) if unusable."""
    journal = path.with_name(path.name + "-journal")
    hint = (
        f"; {journal} exists (a hot journal): run any fse command that writes the cache to "
        "recover it"
        if journal.exists()
        else ""
    )
    if not path.is_file():
        raise ExportDataError(f"no Data_Cache catalog at {path}; check --cache-dir")
    try:
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=_BUSY_TIMEOUT_S)
    except sqlite3.Error as exc:
        raise ExportDataError(f"cannot open the Data_Cache catalog {path}: {exc}{hint}") from exc
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        conn.close()
        raise ExportDataError(f"{path} is not a Data_Cache catalog: {exc}{hint}") from exc
    if version != SCHEMA_VERSION:
        conn.close()
        raise ExportDataError(
            f"{path} has catalog schema version {version}; this export reads only {SCHEMA_VERSION}"
        )
    return conn


def _in(column: str, values: Iterable[str] | None, where: list[str], args: list[object]) -> None:
    if values is None:
        return
    items = sorted(values)
    where.append(f"{column} IN ({', '.join('?' * len(items))})" if items else "0")
    args.extend(items)


# ---------------------------------------------------------------- helpers


def _check_value(table: str, key: str, value: str) -> str:
    if PARTITION_VALUE_RE.fullmatch(value) is None or (
        key == "month" and MONTH_RE.fullmatch(value) is None
    ):
        raise ExportDataError(
            f"partition value {key}={value!r} of table {table} has unsafe characters; "
            "allowed: letters, digits, '_', '.', '-' (month: YYYY-MM)"
        )
    return value


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError as exc:
        raise CacheIntegrityError(f"cached file {path} is missing") from exc
    except OSError as exc:
        raise CacheIntegrityError(f"cached file {path} cannot be read: {exc}") from exc


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            while chunk := fh.read(_HASH_CHUNK):
                h.update(chunk)
    except OSError as exc:
        raise CacheIntegrityError(f"cached file {path} cannot be read: {exc}") from exc
    return h.hexdigest()


def _footer_rows(path: Path) -> int:
    try:
        return int(pq.read_metadata(path).num_rows)
    except (pa.ArrowException, OSError) as exc:
        raise CacheIntegrityError(f"cached file {path} is not a readable Parquet file") from exc


def _trade_date_count(path: Path) -> int:
    try:
        metadata = pq.read_schema(path).metadata or {}
    except (pa.ArrowException, OSError) as exc:
        raise CacheIntegrityError(f"cached file {path} is not a readable Parquet file") from exc
    try:
        dates = json.loads(metadata[b"fse.trade_dates"])
    except (KeyError, ValueError) as exc:
        raise CacheIntegrityError(f"cached file {path} has invalid trade dates") from exc
    if not isinstance(dates, list):
        raise CacheIntegrityError(f"cached file {path} has invalid trade dates")
    return len(dates)


def _fingerprint(lines: Iterable[str]) -> str:
    h = hashlib.sha256(EXPORT_FORMAT.encode())
    for line in sorted(lines):
        h.update(b"\n" + line.encode())
    return h.hexdigest()


def _partition(
    table: str, values: Sequence[tuple[str, str]], inputs: Sequence[SourceInput], lines: list[str]
) -> PartitionPlan:
    spec = TABLES[table]
    vals = tuple((k, _check_value(table, k, v)) for k, v in values)
    return PartitionPlan(
        table=table,
        values=vals,
        rel_path=partition_rel(spec, dict(vals)),
        inputs=tuple(inputs),
        fingerprint=_fingerprint(lines),
        source_rows=0,
        source_bytes=0,
    )


def _with_counts(p: PartitionPlan, rows: int, size: int) -> PartitionPlan:
    return PartitionPlan(
        table=p.table,
        values=p.values,
        rel_path=p.rel_path,
        inputs=p.inputs,
        fingerprint=p.fingerprint,
        source_rows=rows,
        source_bytes=size,
    )


def _file_input(root: Path, path: Path, key: tuple[str, ...]) -> FileInput:
    return FileInput(
        path=path,
        rel=path.relative_to(root).as_posix(),
        size=_size(path),
        sha256=_file_sha256(path),
        key=key,
    )


def _file_line(f: FileInput) -> str:
    return f"F {f.rel} {f.size} {f.sha256}"


# ---------------------------------------------------------------- heatmaps


def _heatmaps(
    conn: sqlite3.Connection,
    cache: DataCache,
    options: ExportOptions,
    sessions: frozenset[date] | None,
    metrics: frozenset[str] | None,
    skipped: dict[str, dict[str, int]],
    warnings: list[str],
) -> tuple[list[PartitionPlan], int]:
    if options.symbols is not None:
        known = {
            row[0]
            for row in conn.execute("SELECT DISTINCT symbol FROM windows WHERE status = 'complete'")
        }
        missing = sorted(options.symbols - known)
        if missing:
            raise ExportInputError(
                f"--symbols: no complete heatmap window for {', '.join(missing)}; symbols in "
                f"the catalog: {', '.join(sorted(known)) or 'none'}"
            )
    where: list[str] = []
    args: list[object] = []
    _in("symbol", options.symbols, where, args)
    _in("substr(session, 1, 7)", options.months, where, args)
    _in("session", None if sessions is None else {d.isoformat() for d in sessions}, where, args)
    _in("metric", metrics, where, args)
    sql = (
        'SELECT symbol, metric, view_id, session, start_ns, status, "rows", source_endpoint, '
        "sha256 FROM windows"
        + (f" WHERE {' AND '.join(where)}" if where else "")
        + " ORDER BY symbol, metric, view_id, session, start_ns"
    )
    counts = skipped.setdefault("heatmaps", {"incomplete": 0, "no_data": 0})
    groups: dict[tuple[str, str, str], list[WindowInput]] = defaultdict(list)
    for symbol, metric, view_id, session, start_ns, status, rows, endpoint, sha in conn.execute(
        sql, args
    ):
        if status != "complete" or not rows:
            key = "incomplete" if status == "incomplete" else "no_data"
            counts[key] += 1
            continue
        try:
            day = date.fromisoformat(session)
            wkey = CacheWindowKey(symbol, metric, view_id, day, start_ns)
        except (TypeError, ValueError) as exc:
            raise ExportDataError(
                f"catalog window {symbol} {metric} {view_id} {session} {start_ns} is not a "
                f"valid Cache_Window key: {exc}"
            ) from exc
        if sha is None or endpoint is None:
            raise ExportDataError(f"catalog window {wkey.describe()} is complete without sha256")
        groups[(symbol, metric, session[:7])].append(
            WindowInput(
                path=cache.window_path(wkey),
                symbol=symbol,
                metric=metric,
                view_id=view_id,
                session=day,
                start_ns=start_ns,
                rows=rows,
                sha256=sha,
                source_endpoint=endpoint,
            )
        )
    if options.months is not None:
        found = {month for _, _, month in groups}
        for month in sorted(options.months - found):
            warnings.append(f"--months {month}: no complete heatmap window")
    out: list[PartitionPlan] = []
    window_count = 0
    for (symbol, metric, month), windows in sorted(groups.items()):
        windows.sort(key=lambda w: (w.view_id, w.session, w.start_ns))
        window_count += len(windows)
        lines = [f"W {w.view_id} {w.session} {w.start_ns} {w.rows} {w.sha256}" for w in windows]
        p = _partition(
            "heatmaps", (("symbol", symbol), ("metric", metric), ("month", month)), windows, lines
        )
        out.append(
            _with_counts(p, sum(w.rows for w in windows), sum(_size(w.path) for w in windows))
        )
    return out, window_count


# ---------------------------------------------------------------- bars


def _bars(
    conn: sqlite3.Connection,
    cache: DataCache,
    table: str,
    sessions: frozenset[date] | None,
    instruments: frozenset[str] | None,
    skipped: dict[str, dict[str, int]],
) -> tuple[list[PartitionPlan], int]:
    dataset = _BAR_TABLES[table]
    store = cache.bars if dataset == "bars" else cache.vix.bars
    where = ["dataset = ?"]
    args: list[object] = [dataset]
    _in("instrument", instruments, where, args)
    _in("session", None if sessions is None else {d.isoformat() for d in sessions}, where, args)
    sql = (
        'SELECT instrument, interval_s, session, status, contract, source, "rows", sha256 '
        f"FROM bars_coverage WHERE {' AND '.join(where)} ORDER BY instrument, interval_s, session"
    )
    counts = skipped.setdefault(table, {"incomplete": 0, "no_data": 0})
    groups: dict[str, list[BarInput]] = defaultdict(list)
    for instrument, interval_s, session, status, contract, source, rows, sha in conn.execute(
        sql, args
    ):
        if status != "complete" or not rows:
            counts["incomplete" if status == "incomplete" else "no_data"] += 1
            continue
        try:
            day = date.fromisoformat(session)
            path = store.path(instrument, interval_s, day)
        except (TypeError, ValueError) as exc:
            raise ExportDataError(
                f"catalog bars {dataset} {instrument} {interval_s}s {session} are not a valid "
                f"key: {exc}"
            ) from exc
        if sha is None or contract is None or source is None:
            raise ExportDataError(
                f"catalog bars {dataset} {instrument} {interval_s}s {session} are complete "
                "without sha256, contract or source"
            )
        groups[instrument].append(
            BarInput(path, dataset, instrument, interval_s, day, rows, sha, contract, source)
        )
    out: list[PartitionPlan] = []
    count = 0
    for instrument, bars in sorted(groups.items()):
        bars.sort(key=lambda b: (b.interval_s, b.session))
        count += len(bars)
        lines = [f"B {b.instrument} {b.interval_s} {b.session} {b.rows} {b.sha256}" for b in bars]
        p = _partition(table, (("instrument", instrument),), bars, lines)
        out.append(_with_counts(p, sum(b.rows for b in bars), sum(_size(b.path) for b in bars)))
    return out, count


# ---------------------------------------------------------------- uncatalogued stores


def _files(root: Path, pattern: str) -> list[Path]:
    return sorted(p for p in root.glob(pattern) if p.is_file())


def _vix_daily(root: Path, cache: DataCache) -> list[PartitionPlan]:
    path = cache.vix.daily_path
    if not path.is_file():
        return []
    f = _file_input(root, path, ())
    p = _partition("vix_daily", (), (f,), [_file_line(f)])
    return [_with_counts(p, _footer_rows(path), f.size)]


def _darkpool_inputs(root: Path) -> list[FileInput]:
    out: list[FileInput] = []
    for path in _files(root, "darkpool/*/*.parquet"):
        ticker, month = path.parent.name, path.stem
        _check_value("darkpool", "ticker", ticker)
        _check_value("darkpool_fetched_dates", "month", month)
        out.append(_file_input(root, path, (ticker, month)))
    return out


def _darkpool(files: Sequence[FileInput]) -> list[PartitionPlan]:
    by_ticker: dict[str, list[FileInput]] = defaultdict(list)
    for f in files:
        by_ticker[f.key[0]].append(f)
    out: list[PartitionPlan] = []
    for ticker, months in sorted(by_ticker.items()):
        p = _partition("darkpool", (("ticker", ticker),), months, [_file_line(f) for f in months])
        out.append(
            _with_counts(p, sum(_footer_rows(f.path) for f in months), sum(f.size for f in months))
        )
    return out


def _fetched_dates(files: Sequence[FileInput]) -> list[PartitionPlan]:
    if not files:
        return []
    p = _partition("darkpool_fetched_dates", (), files, [_file_line(f) for f in files])
    rows = sum(_trade_date_count(f.path) for f in files)
    return [_with_counts(p, rows, sum(f.size for f in files))]


def _medians(root: Path) -> list[PartitionPlan]:
    files = []
    for path in _files(root, "derived/regime_medians/*/*.parquet"):
        files.append(_file_input(root, path, (path.parent.name, path.stem)))
    if not files:
        return []
    p = _partition("regime_medians", (), files, [_file_line(f) for f in files])
    rows = sum(_footer_rows(f.path) for f in files)
    return [_with_counts(p, rows, sum(f.size for f in files))]


# ---------------------------------------------------------------- the entry point


def plan(
    options: ExportOptions,
    *,
    sessions: frozenset[date] | None = None,
    metrics: frozenset[str] | None = None,
    instruments: frozenset[str] | None = None,
) -> Plan:
    """The export plan for ``options``. Reads only; writes nothing anywhere.

    The keyword filters are a test-only seam (never CLI options): ``sessions``
    restricts heatmap windows and bar sessions, ``metrics`` heatmap windows and
    ``instruments`` bars and VIX bars. They can produce partial partitions,
    which is allowed only when staging into a temporary directory.

    Raises ``ExportDataError`` (exit 4) for a missing or wrong-version catalog
    or an unsafe partition value, ``CacheIntegrityError`` (exit 4) for a
    missing or unreadable file, and ``ExportInputError`` (exit 2) for a
    ``--symbols`` value with no complete window.
    """
    root = options.cache_dir
    if not root.is_dir():
        raise ExportDataError(f"Data_Cache directory {root} does not exist; check --cache-dir")
    cache = DataCache(root)  # path functions only: creates and opens nothing
    tables = options.tables
    partitions: list[PartitionPlan] = []
    skipped: dict[str, dict[str, int]] = {}
    warnings: list[str] = []
    window_count = bar_count = 0
    conn = open_catalog_ro(root / CATALOG_FILE_NAME)
    try:
        if "heatmaps" in tables:
            found, window_count = _heatmaps(
                conn, cache, options, sessions, metrics, skipped, warnings
            )
            partitions.extend(found)
        for table in ("bars", "vix_bars"):
            if table in tables:
                found, n = _bars(conn, cache, table, sessions, instruments, skipped)
                partitions.extend(found)
                bar_count += n
    finally:
        conn.close()
    if "vix_daily" in tables:
        partitions.extend(_vix_daily(root, cache))
    if "darkpool" in tables or "darkpool_fetched_dates" in tables:
        files = _darkpool_inputs(root)
        partitions.extend(_darkpool(files))
        partitions.extend(_fetched_dates(files))
    if "regime_medians" in tables:
        partitions.extend(_medians(root))
    partitions = [p for p in partitions if p.source_rows > 0]
    order = {name: i for i, name in enumerate(TABLES)}
    partitions.sort(key=lambda p: (order[p.table], p.rel_path))
    with_data = {p.table for p in partitions}
    scope: dict[str, Mapping[str, frozenset[str]]] = {}
    heat_scope: dict[str, frozenset[str]] = {}
    if options.symbols is not None:
        heat_scope["symbol"] = options.symbols
    if options.months is not None:
        heat_scope["month"] = options.months
    if metrics is not None:
        heat_scope["metric"] = metrics
    scope["heatmaps"] = MappingProxyType(heat_scope)
    if instruments is not None:
        scope["bars"] = scope["vix_bars"] = MappingProxyType({"instrument": instruments})
    partial = frozenset({"heatmaps", "bars", "vix_bars"} if sessions is not None else ())
    return Plan(
        cache_dir=root,
        tables=tables,
        partitions=tuple(partitions),
        window_count=window_count,
        bar_session_count=bar_count,
        skipped=MappingProxyType({k: MappingProxyType(v) for k, v in skipped.items()}),
        tables_without_data=tuple(t for t in tables if t not in with_data),
        warnings=tuple(warnings),
        scope=MappingProxyType(scope),
        partial=partial,
    )
