"""Stage transforms: cache files to compacted export parts (design §8.2, §5).

Each table has one transform, a generator over the partition's inputs that
yields Arrow tables in the output schema; each yielded table becomes one row
group. :func:`write_partition` writes them through a :class:`PartWriter`
(zstd level 3, explicit schema, footer metadata) that rolls over to the next
``part-NNNNN.parquet`` once a part reaches :data:`MAX_FILE_BYTES`.

Exactness: only Arrow operations touch values (``take``, ``concat_tables``,
zero-copy column selection, list casts between identical value types), so
every float64 keeps its bits, ``-0.0`` and NaN payloads included. Rows are
ordered with stable :func:`pyarrow.compute.sort_indices` on
:data:`fse.awsexport.tables.SORT_KEYS`.

Every source file is read with :func:`fse.data.cache_io.read_verified` against
the catalog sha256 (or the plan's hash for uncatalogued files) and checked with
``expect_meta`` and ``require_columns``. A failure is a ``CacheIntegrityError``
(exit 4) naming the file.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final, cast

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from fse.awsexport.source import BarInput, FileInput, PartitionPlan, SourceInput, WindowInput
from fse.awsexport.tables import EXPORT_FORMAT, RENAMES, SORT_KEYS, TABLES, TableSpec, part_name
from fse.data.aux_stores import (
    BARS_FORMAT,
    BARS_REQUIRED_COLUMNS,
    BARS_SCHEMA,
    DARKPOOL_FORMAT,
    DARKPOOL_SCHEMA,
    VIX_DAILY_FORMAT,
    VIX_DAILY_REQUIRED_COLUMNS,
    VIX_DAILY_SCHEMA,
    trade_date_of,
)
from fse.data.cache import AXIS_SCHEMA, WINDOW_FORMAT, WINDOW_REQUIRED_COLUMNS, WINDOW_SCHEMA
from fse.data.cache_io import (
    CacheIntegrityError,
    expect_meta,
    ipc_to_table,
    meta_bytes,
    meta_text,
    parquet_to_table,
    read_verified,
    require_columns,
)
from fse.data.medians import MEDIANS_FORMAT, MEDIANS_REQUIRED_COLUMNS, MEDIANS_SCHEMA

__all__ = [
    "BAR_FLUSH_ROWS",
    "COMPRESSION_LEVEL",
    "MAX_FILE_BYTES",
    "TRANSFORMS",
    "PartWriter",
    "Transform",
    "WrittenPart",
    "sort_rows",
    "write_partition",
]

# A part rolls over to the next once it reaches this size (tests patch it).
MAX_FILE_BYTES = 256 * 2**20
COMPRESSION_LEVEL: Final = 3
BAR_FLUSH_ROWS: Final = 131_072

type Transform = Callable[[PartitionPlan], Iterator[Any]]


@dataclass(frozen=True, slots=True)
class WrittenPart:
    """One staged part file; ``rows`` is read back from its Parquet footer."""

    name: str
    size: int
    rows: int


# ---------------------------------------------------------------- writing


class PartWriter:
    """Writes row groups into ``part-NNNNN.parquet`` files of one partition directory."""

    __slots__ = ("_dir", "_index", "_metadata", "_names", "_schema", "_sink", "_writer")

    def __init__(self, spec: TableSpec, values: Mapping[str, str], directory: Path) -> None:
        self._dir = directory
        self._schema = spec.schema
        self._metadata = {
            b"fse.export.format": EXPORT_FORMAT.encode(),
            b"fse.export.table": spec.name.encode(),
            b"fse.export.partition": json.dumps(dict(values), sort_keys=True).encode(),
        }
        self._index = 0
        self._names: list[str] = []
        self._sink: Any = None
        self._writer: Any = None

    def write(self, table: Any) -> None:
        """Write ``table`` as one row group (nothing for an empty table)."""
        if table.num_rows == 0:
            return
        if self._writer is None:
            self._open()
        self._writer.write_table(table, row_group_size=table.num_rows)
        if self._sink.tell() >= MAX_FILE_BYTES:
            self._close_part()

    def close(self) -> list[str]:
        """Close the open part; the names of every part written."""
        if self._writer is not None:
            self._close_part()
        return list(self._names)

    def abort(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
            finally:
                self._writer = self._sink = None

    def _open(self) -> None:
        name = part_name(self._index)
        self._index += 1
        self._sink = pa.OSFile(str(self._dir / name), "wb")
        self._writer = pq.ParquetWriter(
            self._sink,
            self._schema.with_metadata(self._metadata),
            compression="zstd",
            compression_level=COMPRESSION_LEVEL,
        )
        self._names.append(name)

    def _close_part(self) -> None:
        self._writer.close()
        self._sink.close()
        self._writer = self._sink = None
        path = self._dir / self._names[-1]
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def sort_rows(table: Any, keys: Sequence[str]) -> Any:
    """``table`` stably sorted by ``keys`` ascending (ties keep their order)."""
    if table.num_rows < 2:
        return table
    idx = pc.sort_indices(table, sort_keys=[(k, "ascending") for k in keys])
    return table.take(idx)


def _build(spec: TableSpec, columns: Mapping[str, Any], n: int) -> Any:
    """A table in ``spec.schema`` from ``columns``; list columns are cast to the schema type."""
    arrays = []
    for f in spec.schema:
        col = columns[f.name]
        if not col.type.equals(f.type):
            col = col.cast(f.type)
        arrays.append(col)
    table = pa.Table.from_arrays(arrays, schema=spec.schema)
    if table.num_rows != n:
        raise AssertionError(f"{spec.name}: built {table.num_rows} rows, expected {n}")
    return table


def _const(value: object, dtype: pa.DataType, n: int) -> Any:
    return pa.nulls(n, dtype) if value is None else pa.array([value] * n, dtype)


def _int_meta(md: Any, key: str, path: Path) -> int | None:
    """An optional integer metadata value: empty gives ``None``."""
    text = meta_text(md, key, source=path)
    if text == "":
        return None
    try:
        return int(text)
    except ValueError:
        raise CacheIntegrityError(f"cached file {path} has non-integer {key} {text!r}") from None


def _rows_check(table: Any, expected: int, path: Path) -> None:
    if table.num_rows != expected:
        raise CacheIntegrityError(
            f"cached file {path} holds {table.num_rows} rows; the catalog records {expected}"
        )


# ---------------------------------------------------------------- heatmaps


def _window_table(w: WindowInput, spec: TableSpec) -> Any:
    path = w.path
    table = parquet_to_table(read_verified(path, w.sha256), source=path)
    md = table.schema.metadata
    expect_meta(
        md,
        {
            "fse.format": WINDOW_FORMAT,
            "fse.symbol": w.symbol,
            "fse.metric": w.metric,
            "fse.view_id": w.view_id,
            "fse.session": w.session.isoformat(),
            "fse.window_start_ns": str(w.start_ns),
            "fse.source_endpoint": w.source_endpoint,
        },
        source=path,
    )
    axes = ipc_to_table(meta_bytes(md, "fse.axes", source=path), source=path)
    require_columns(table, WINDOW_SCHEMA.names, non_null=WINDOW_REQUIRED_COLUMNS, source=path)
    require_columns(axes, AXIS_SCHEMA.names, non_null=AXIS_SCHEMA.names, source=path)
    _rows_check(table, w.rows, path)
    interval = _int_meta(md, "fse.storage_interval_s", path)
    view_json = meta_text(md, "fse.view", source=path) or None
    n = table.num_rows
    idx = pc.index_in(table["axis_id"], value_set=axes["axis_id"].combine_chunks())
    if idx.null_count:
        raise CacheIntegrityError(f"cached file {path} references an unknown axis id")
    strikes = axes["strikes"].take(idx)
    expirations = axes["expirations"].take(idx)
    values = table["values"]
    lengths_differ = pc.any(
        pc.not_equal(pc.list_value_length(strikes), pc.list_value_length(values))
    ).as_py()
    if lengths_differ:
        raise CacheIntegrityError(
            f"cached file {path} (window {w.symbol} {w.metric} {w.view_id} {w.session} "
            f"{w.start_ns}) has a row whose strikes and values differ in length"
        )
    renames = RENAMES["heatmaps"]
    columns: dict[str, Any] = {renames.get(name, name): table[name] for name in WINDOW_SCHEMA.names}
    columns.update(
        {
            "view_id": _const(w.view_id, pa.string(), n),
            "view_json": _const(view_json, pa.string(), n),
            "session": _const(w.session, pa.date32(), n),
            "window_start_ns": _const(w.start_ns, pa.int64(), n),
            "window_source_endpoint": _const(w.source_endpoint, pa.string(), n),
            "storage_interval_s": _const(interval, pa.int32(), n),
            "window_row": pa.array(range(n), pa.int32()),
            "strikes": strikes,
            "expirations": expirations,
        }
    )
    return _build(spec, columns, n)


def _heatmaps(pp: PartitionPlan) -> Iterator[Any]:
    spec = TABLES["heatmaps"]
    windows = sorted(
        _only(pp.inputs, WindowInput), key=lambda w: (w.view_id, w.session, w.start_ns)
    )
    group: list[Any] = []
    current: tuple[str, date] | None = None
    for w in windows:
        if current is not None and (w.view_id, w.session) != current and group:
            yield sort_rows(pa.concat_tables(group), SORT_KEYS["heatmaps"])
            group = []
        current = (w.view_id, w.session)
        group.append(_window_table(w, spec))
    if group:
        yield sort_rows(pa.concat_tables(group), SORT_KEYS["heatmaps"])


# ---------------------------------------------------------------- bars


def _bar_table(b: BarInput, spec: TableSpec) -> Any:
    path = b.path
    table = parquet_to_table(read_verified(path, b.sha256), source=path)
    md = table.schema.metadata
    expect_meta(
        md,
        {
            "fse.format": BARS_FORMAT,
            "fse.dataset": b.dataset,
            "fse.instrument": b.instrument,
            "fse.interval_s": str(b.interval_s),
            "fse.session": b.session.isoformat(),
            "fse.contract": b.contract,
            "fse.source": b.source,
        },
        source=path,
    )
    require_columns(table, BARS_SCHEMA.names, non_null=BARS_REQUIRED_COLUMNS, source=path)
    _rows_check(table, b.rows, path)
    interval = _int_meta(md, "fse.interval_s", path)
    n = table.num_rows
    columns: dict[str, Any] = {name: table[name] for name in BARS_SCHEMA.names}
    columns.update(
        {
            "session": _const(b.session, pa.date32(), n),
            "interval_s": _const(interval, pa.int32(), n),
            "contract": _const(b.contract, pa.string(), n),
            "source": _const(b.source, pa.string(), n),
        }
    )
    return _build(spec, columns, n)


def _bars_of(table_name: str) -> Transform:
    def transform(pp: PartitionPlan) -> Iterator[Any]:
        spec = TABLES[table_name]
        bars = sorted(_only(pp.inputs, BarInput), key=lambda b: (b.interval_s, b.session))
        buffer: list[Any] = []
        buffered = 0
        for b in bars:
            t = _bar_table(b, spec)
            buffer.append(t)
            buffered += t.num_rows
            if buffered >= BAR_FLUSH_ROWS:
                yield sort_rows(pa.concat_tables(buffer), SORT_KEYS[table_name])
                buffer, buffered = [], 0
        if buffer:
            yield sort_rows(pa.concat_tables(buffer), SORT_KEYS[table_name])

    return transform


# ---------------------------------------------------------------- uncatalogued stores


def _read_file(f: FileInput) -> Any:
    return parquet_to_table(read_verified(f.path, f.sha256), source=f.path)


def _vix_daily(pp: PartitionPlan) -> Iterator[Any]:
    spec = TABLES["vix_daily"]
    for f in _only(pp.inputs, FileInput):
        table = _read_file(f)
        expect_meta(table.schema.metadata, {"fse.format": VIX_DAILY_FORMAT}, source=f.path)
        require_columns(
            table, VIX_DAILY_SCHEMA.names, non_null=VIX_DAILY_REQUIRED_COLUMNS, source=f.path
        )
        columns = {name: table[name] for name in VIX_DAILY_SCHEMA.names}
        yield sort_rows(_build(spec, columns, table.num_rows), SORT_KEYS["vix_daily"])


def _darkpool_month(f: FileInput) -> tuple[Any, list[date]]:
    ticker, month = f.key
    table = _read_file(f)
    md = table.schema.metadata
    expect_meta(
        md, {"fse.format": DARKPOOL_FORMAT, "fse.ticker": ticker, "fse.month": month}, source=f.path
    )
    require_columns(table, DARKPOOL_SCHEMA.names, non_null=DARKPOOL_SCHEMA.names, source=f.path)
    try:
        raw = json.loads(meta_text(md, "fse.trade_dates", source=f.path))
        if not isinstance(raw, list):
            raise TypeError("not a list")
        dates = [date.fromisoformat(cast(str, d)) for d in raw]
    except (ValueError, TypeError) as exc:
        raise CacheIntegrityError(f"cached file {f.path} has invalid trade dates") from exc
    return table, dates


def _darkpool(pp: PartitionPlan) -> Iterator[Any]:
    spec = TABLES["darkpool"]
    parts = []
    for f in _only(pp.inputs, FileInput):
        table, _ = _darkpool_month(f)
        n = table.num_rows
        columns: dict[str, Any] = {name: table[name] for name in DARKPOOL_SCHEMA.names}
        trade_dates = [trade_date_of(ts) for ts in table["ts_ns"].to_pylist()]
        columns["trade_date"] = pa.array(trade_dates, pa.date32())
        parts.append(_build(spec, columns, n))
    if parts:
        yield sort_rows(pa.concat_tables(parts), SORT_KEYS["darkpool"])


def _fetched_dates(pp: PartitionPlan) -> Iterator[Any]:
    spec = TABLES["darkpool_fetched_dates"]
    parts = []
    for f in _only(pp.inputs, FileInput):
        ticker, month = f.key
        _, dates = _darkpool_month(f)
        n = len(dates)
        columns = {
            "ticker": _const(ticker, pa.string(), n),
            "trade_date": pa.array(dates, pa.date32()),
            "month": _const(month, pa.string(), n),
        }
        parts.append(_build(spec, columns, n))
    if parts:
        yield sort_rows(pa.concat_tables(parts), SORT_KEYS["darkpool_fetched_dates"])


def _medians(pp: PartitionPlan) -> Iterator[Any]:
    spec = TABLES["regime_medians"]
    parts = []
    for f in _only(pp.inputs, FileInput):
        view_id, params_hash = f.key
        table = _read_file(f)
        md = table.schema.metadata
        expect_meta(
            md,
            {"fse.format": MEDIANS_FORMAT, "fse.view_id": view_id, "fse.params_hash": params_hash},
            source=f.path,
        )
        require_columns(
            table, MEDIANS_SCHEMA.names, non_null=MEDIANS_REQUIRED_COLUMNS, source=f.path
        )
        n = table.num_rows
        columns: dict[str, Any] = {name: table[name] for name in MEDIANS_SCHEMA.names}
        columns.update(
            {
                "regime_symbol": _const(meta_text(md, "fse.symbol", source=f.path), pa.string(), n),
                "params_json": _const(meta_text(md, "fse.params", source=f.path), pa.string(), n),
                "inputs_sha256": _const(
                    meta_text(md, "fse.inputs_sha256", source=f.path), pa.string(), n
                ),
                "incomplete_windows": _const(
                    _int_meta(md, "fse.incomplete_windows", f.path), pa.int32(), n
                ),
            }
        )
        parts.append(_build(spec, columns, n))
    if parts:
        yield sort_rows(pa.concat_tables(parts), SORT_KEYS["regime_medians"])


def _only[T](inputs: Sequence[SourceInput], kind: type[T]) -> list[T]:
    out: list[T] = [i for i in inputs if isinstance(i, kind)]
    if len(out) != len(inputs):
        raise AssertionError(f"partition inputs are not all {kind.__name__}")
    return out


# One transform per table; tests replace entries to exercise the row-count check.
TRANSFORMS: Final[dict[str, Transform]] = {
    "heatmaps": _heatmaps,
    "bars": _bars_of("bars"),
    "vix_bars": _bars_of("vix_bars"),
    "vix_daily": _vix_daily,
    "darkpool": _darkpool,
    "darkpool_fetched_dates": _fetched_dates,
    "regime_medians": _medians,
}


def write_partition(pp: PartitionPlan, work_dir: Path) -> list[WrittenPart]:
    """Write ``pp`` into ``work_dir`` (which must exist); the parts written.

    The row count of each part is read back from its footer. Nothing is
    written for a partition whose transform yields no rows.
    """
    spec = TABLES[pp.table]
    writer = PartWriter(spec, pp.value_map, work_dir)
    try:
        for table in TRANSFORMS[pp.table](pp):
            writer.write(table)
        names = writer.close()
    except BaseException:
        writer.abort()
        raise
    out = []
    for name in names:
        path = work_dir / name
        out.append(WrittenPart(name, path.stat().st_size, int(pq.read_metadata(path).num_rows)))
    return out
