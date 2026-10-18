"""Output tables, Glue types and the only path builders of the export (design §5-6).

:data:`TABLES` holds one :class:`TableSpec` per output table, in registry
order. File schemas are built from the cache's own schema constants plus the
promoted columns, so a cache schema change fails the export loudly instead of
diverging. Partition columns are never stored inside the files (Hive
convention); each file names them in its footer metadata instead.

Every S3 key, staging path and Glue location is built here and nowhere else:
:func:`partition_rel`, :func:`table_prefix`, :func:`partition_prefix`,
:func:`table_uri`, :func:`location_template` and :func:`staged_dir`.
:func:`parse_partition_key` is the single parser of remote keys.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Final

import pyarrow as pa

from fse.data.aux_stores import BARS_SCHEMA, DARKPOOL_SCHEMA, VIX_DAILY_SCHEMA
from fse.data.cache import AXIS_SCHEMA, WINDOW_SCHEMA
from fse.data.medians import MEDIANS_SCHEMA

__all__ = [
    "EXPORT_FORMAT",
    "MONTH_RE",
    "PARTITION_VALUE_RE",
    "PART_RE",
    "RENAMES",
    "SORT_KEYS",
    "TABLES",
    "TableSpec",
    "glue_type",
    "location_template",
    "parse_partition_key",
    "part_name",
    "partition_prefix",
    "partition_rel",
    "staged_dir",
    "table_prefix",
    "table_rel",
    "table_uri",
]

# Bumped on any change to a transform or layout: every partition restages.
EXPORT_FORMAT: Final = "fse.aws-export/1"

PART_RE: Final = re.compile(r"part-\d{5}\.parquet")  # always used with fullmatch
PARTITION_VALUE_RE: Final = re.compile(r"[A-Za-z0-9_.-]+")  # used with fullmatch
MONTH_RE: Final = re.compile(r"\d{4}-(0[1-9]|1[0-2])")  # used with fullmatch

# Source column -> output column, per table. VALUES is reserved in Athena (Trino).
RENAMES: Final[Mapping[str, Mapping[str, str]]] = MappingProxyType(
    {"heatmaps": MappingProxyType({"values": "strike_values"})}
)


@dataclass(frozen=True, slots=True)
class TableSpec:
    """One output table: its name (paths and Glue), dataset, file schema and partitions."""

    name: str
    dataset: str
    schema: pa.Schema
    partition_keys: tuple[str, ...]
    comments: Mapping[str, str] = field(default_factory=dict)

    def projection_type(self, key: str) -> str:
        """``date`` for ``month``, ``enum`` for every other partition key."""
        return "date" if key == "month" else "enum"


# ---------------------------------------------------------------- Glue types

_GLUE_SCALARS: Final[Mapping[pa.DataType, str]] = MappingProxyType(
    {
        pa.int64(): "bigint",
        pa.int32(): "int",
        pa.float64(): "double",
        pa.bool_(): "boolean",
        pa.string(): "string",
        pa.date32(): "date",
    }
)


def glue_type(dtype: pa.DataType) -> str:
    """The Glue (Hive) type of an Arrow type; ``TypeError`` for any other type."""
    if pa.types.is_list(dtype):
        inner = dtype.value_type
        if inner in (pa.float64(), pa.string()):
            return f"array<{_GLUE_SCALARS[inner]}>"
    elif dtype in _GLUE_SCALARS:
        return _GLUE_SCALARS[dtype]
    raise TypeError(f"no Glue type for Arrow type {dtype}")


# ---------------------------------------------------------------- the registry


def _f(name: str, dtype: pa.DataType, nullable: bool = True) -> pa.Field:
    return pa.field(name, dtype, nullable=nullable)


_HEATMAP_FIELDS: Final = (
    _f("view_id", pa.string(), False),
    _f("view_json", pa.string()),
    _f("session", pa.date32(), False),
    _f("window_start_ns", pa.int64(), False),
    _f("window_source_endpoint", pa.string(), False),
    _f("storage_interval_s", pa.int32()),
    _f("window_row", pa.int32(), False),
    WINDOW_SCHEMA.field("as_of_ns"),
    WINDOW_SCHEMA.field("as_of_raw"),
    WINDOW_SCHEMA.field("spot"),
    WINDOW_SCHEMA.field("previous_close"),
    WINDOW_SCHEMA.field("axis_id"),
    AXIS_SCHEMA.field("strikes"),
    AXIS_SCHEMA.field("expirations"),
    WINDOW_SCHEMA.field("values").with_name("strike_values"),
    WINDOW_SCHEMA.field("node_types"),
    WINDOW_SCHEMA.field("resolution"),
    WINDOW_SCHEMA.field("source_endpoint"),
    WINDOW_SCHEMA.field("extra_json"),
)
_HEATMAP_COMMENTS: Final = {
    "view_id": "Heatmap_View id; rows of different views are separate strike grids",
    "view_json": "canonical JSON of the Heatmap_View parameters, null when not recorded",
    "session": "trading session (New York date)",
    "window_start_ns": "Cache_Window start, ns since the Unix epoch, UTC",
    "window_source_endpoint": "the Cache_Window's source endpoint",
    "storage_interval_s": "storage interval of the window in seconds, null when every Snapshot",
    "window_row": "0-based row index inside the source Cache_Window file",
    "as_of_ns": "Snapshot asOf, ns since the Unix epoch, UTC",
    "as_of_raw": "Snapshot asOf as returned by the API",
    "axis_id": "axis id inside the source Cache_Window file",
    "strikes": "strike prices; strike_values[i] belongs to strikes[i]",
    "expirations": "expirations of the Snapshot",
    "strike_values": "per-strike exposure values (cache column 'values')",
    "node_types": "per-strike node labels (historical label samples only)",
    "source_endpoint": "range or historical (historical rows are label samples)",
    "extra_json": "unrecognized response fields, canonical JSON",
}

_BAR_FIELDS: Final = (
    _f("session", pa.date32(), False),
    _f("interval_s", pa.int32(), False),
    _f("contract", pa.string(), False),
    _f("source", pa.string(), False),
    *BARS_SCHEMA,
)
_BAR_COMMENTS: Final = {
    "session": "trading session (New York date)",
    "interval_s": "bar size in seconds",
    "contract": "futures contract of the session",
    "source": "bar source",
    "open_ns": "bar open, ns since the Unix epoch, UTC",
    "close_ns": "bar close, ns since the Unix epoch, UTC",
}

_DARKPOOL_FIELDS: Final = (_f("trade_date", pa.date32(), False), *DARKPOOL_SCHEMA)
_FETCHED_FIELDS: Final = (
    _f("ticker", pa.string(), False),
    _f("trade_date", pa.date32(), False),
    _f("month", pa.string(), False),
)
_MEDIAN_FIELDS: Final = (
    _f("regime_symbol", pa.string(), False),
    MEDIANS_SCHEMA.field("view_id"),
    MEDIANS_SCHEMA.field("params_hash"),
    _f("params_json", pa.string(), False),
    _f("inputs_sha256", pa.string(), False),
    _f("incomplete_windows", pa.int32()),
    MEDIANS_SCHEMA.field("session"),
    MEDIANS_SCHEMA.field("metric"),
    MEDIANS_SCHEMA.field("median_raw_mag"),
    MEDIANS_SCHEMA.field("sessions"),
    MEDIANS_SCHEMA.field("usable"),
)


def _spec(
    name: str,
    dataset: str,
    fields: tuple[pa.Field, ...],
    partition_keys: tuple[str, ...],
    comments: Mapping[str, str],
) -> TableSpec:
    schema = pa.schema(list(fields))
    for f in schema:
        glue_type(f.type)  # TypeError at import for an unmapped type
    return TableSpec(name, dataset, schema, partition_keys, MappingProxyType(dict(comments)))


TABLES: Final[Mapping[str, TableSpec]] = MappingProxyType(
    {
        spec.name: spec
        for spec in (
            _spec(
                "heatmaps",
                "heatmaps",
                _HEATMAP_FIELDS,
                ("symbol", "metric", "month"),
                _HEATMAP_COMMENTS,
            ),
            _spec("bars", "bars", _BAR_FIELDS, ("instrument",), _BAR_COMMENTS),
            _spec("vix_bars", "vix_bars", _BAR_FIELDS, ("instrument",), _BAR_COMMENTS),
            _spec(
                "vix_daily",
                "vix_daily",
                tuple(VIX_DAILY_SCHEMA),
                (),
                {"session": "trading session (New York date)"},
            ),
            _spec(
                "darkpool",
                "darkpool",
                _DARKPOOL_FIELDS,
                ("ticker",),
                {"trade_date": "New York date of ts_ns", "ts_ns": "print time, ns UTC"},
            ),
            _spec(
                "darkpool_fetched_dates",
                "darkpool",
                _FETCHED_FIELDS,
                (),
                {"trade_date": "a trade date with a stored fetch result, prints or not"},
            ),
            _spec(
                "regime_medians",
                "regime_medians",
                _MEDIAN_FIELDS,
                (),
                {"regime_symbol": "Regime_Symbol the medians were computed for"},
            ),
        )
    }
)

# Row order inside every file (design §5 plus review NIT 5). Sorts are stable,
# so ties keep source order.
SORT_KEYS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "heatmaps": ("view_id", "session", "window_start_ns", "window_row"),
        "bars": ("interval_s", "session", "open_ns"),
        "vix_bars": ("interval_s", "session", "open_ns"),
        "vix_daily": ("session",),
        "darkpool": ("trade_date", "ts_ns"),
        "darkpool_fetched_dates": ("ticker", "trade_date"),
        "regime_medians": ("regime_symbol", "view_id", "params_hash", "session", "metric"),
    }
)


# ---------------------------------------------------------------- path helpers


def part_name(index: int) -> str:
    """``part-00000.parquet`` for 0."""
    name = f"part-{index:05d}.parquet"
    if PART_RE.fullmatch(name) is None:
        raise ValueError(f"part index {index} out of range")
    return name


def table_rel(spec: TableSpec) -> str:
    """``'darkpool_fetched_dates/'``: the table root relative to the prefix."""
    return f"{spec.name}/"


def partition_rel(spec: TableSpec, values: Mapping[str, str]) -> str:
    """``'heatmaps/symbol=SPX/metric=gamma/month=2025-10/'``, or ``'vix_daily/'``."""
    if set(values) != set(spec.partition_keys):
        raise ValueError(
            f"table {spec.name} has partition keys {spec.partition_keys}, got {sorted(values)}"
        )
    return table_rel(spec) + "".join(f"{k}={values[k]}/" for k in spec.partition_keys)


def table_prefix(prefix: str, spec: TableSpec) -> str:
    """``'fse/darkpool_fetched_dates/'``."""
    return f"{prefix}/{table_rel(spec)}"


def partition_prefix(prefix: str, spec: TableSpec, values: Mapping[str, str]) -> str:
    return f"{prefix}/{partition_rel(spec, values)}"


def table_uri(bucket: str, prefix: str, spec: TableSpec) -> str:
    """The Glue ``Location``: ``s3://b/fse/darkpool/``."""
    return f"s3://{bucket}/{table_prefix(prefix, spec)}"


def location_template(bucket: str, prefix: str, spec: TableSpec) -> str:
    """``storage.location.template``: ``s3://b/fse/heatmaps/symbol=${symbol}/...``."""
    return table_uri(bucket, prefix, spec) + "".join(f"{k}=${{{k}}}/" for k in spec.partition_keys)


def staged_dir(staging_dir: Path, rel_path: str) -> Path:
    """``{staging}/data/{rel_path}``: where a committed partition's parts live."""
    return staging_dir.joinpath("data", *rel_path.strip("/").split("/"))


def parse_partition_key(prefix: str, spec: TableSpec, key: str) -> Mapping[str, str] | None:
    """Partition values of a well-formed part key, else ``None`` (the key is foreign)."""
    root = table_prefix(prefix, spec)
    if not key.startswith(root):
        return None
    segments = key[len(root) :].split("/")
    if len(segments) != len(spec.partition_keys) + 1:  # nested or too shallow
        return None
    *parts, name = segments
    if PART_RE.fullmatch(name) is None:
        return None
    values: dict[str, str] = {}
    for expected_key, segment in zip(spec.partition_keys, parts, strict=True):
        k, sep, v = segment.partition("=")
        if not sep or k != expected_key or PARTITION_VALUE_RE.fullmatch(v) is None:
            return None  # wrong order, missing '=', comma, etc.
        if k == "month" and MONTH_RE.fullmatch(v) is None:
            return None
        values[k] = v
    assert partition_prefix(prefix, spec, values) + name == key  # round trip; a bug if false
    return values
