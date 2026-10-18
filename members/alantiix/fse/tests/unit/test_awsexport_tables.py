"""Output tables, Glue types, row order and the path helpers (design §5-6, NIT 5).

**Validates: aws-export design §14 test 2**
"""

from __future__ import annotations

import re
from pathlib import Path

import pyarrow as pa
import pytest

from fse.awsexport import tables as t
from fse.data.aux_stores import BARS_SCHEMA, DARKPOOL_SCHEMA, VIX_DAILY_SCHEMA
from fse.data.cache import AXIS_SCHEMA, WINDOW_SCHEMA
from fse.data.medians import MEDIANS_SCHEMA

PACKAGE = Path(__file__).resolve().parents[2] / "src" / "fse" / "awsexport"

SAMPLE_VALUES = {
    "heatmaps": {"symbol": "SPX", "metric": "gamma", "month": "2025-10"},
    "bars": {"instrument": "MES"},
    "vix_bars": {"instrument": "VIX"},
    "vix_daily": {},
    "darkpool": {"ticker": "SPY"},
    "darkpool_fetched_dates": {},
    "regime_medians": {},
}


def test_registry_order_and_partition_keys() -> None:
    assert list(t.TABLES) == list(SAMPLE_VALUES)
    assert t.TABLES["heatmaps"].partition_keys == ("symbol", "metric", "month")
    assert t.TABLES["darkpool_fetched_dates"].dataset == "darkpool"
    assert t.TABLES["heatmaps"].projection_type("month") == "date"
    assert t.TABLES["heatmaps"].projection_type("symbol") == "enum"


@pytest.mark.parametrize("name", list(SAMPLE_VALUES))
def test_path_helpers(name: str) -> None:
    spec = t.TABLES[name]
    values = SAMPLE_VALUES[name]
    rel = t.partition_rel(spec, values)
    assert rel.startswith(f"{name}/")
    assert rel.endswith("/")
    assert "//" not in rel
    assert t.table_prefix("fse", spec) == f"fse/{name}/"
    assert t.partition_prefix("fse", spec, values) == f"fse/{rel}"
    assert t.table_uri("b", "fse", spec) == f"s3://b/fse/{name}/"
    key = t.partition_prefix("fse", spec, values) + "part-00000.parquet"
    assert "//" not in key
    assert t.parse_partition_key("fse", spec, key) == values
    assert t.staged_dir(Path("/s"), rel) == Path("/s/data", *rel.strip("/").split("/"))
    with pytest.raises(ValueError, match="partition keys"):
        t.partition_rel(spec, {**values, "extra": "x"})


def test_unpartitioned_paths() -> None:
    spec = t.TABLES["darkpool_fetched_dates"]
    assert t.partition_rel(spec, {}) == "darkpool_fetched_dates/"
    assert t.partition_prefix("fse", spec, {}) == "fse/darkpool_fetched_dates/"


def test_location_template_literal() -> None:
    assert (
        t.location_template("b", "fse", t.TABLES["heatmaps"])
        == "s3://b/fse/heatmaps/symbol=${symbol}/metric=${metric}/month=${month}/"
    )
    assert t.location_template("b", "fse", t.TABLES["vix_daily"]) == "s3://b/fse/vix_daily/"


def test_part_re() -> None:
    assert t.PART_RE.fullmatch("part-00000.parquet")
    for bad in ("part-0.parquet", "x/part-00000.parquet", "part-00000.parquet.tmp"):
        assert t.PART_RE.fullmatch(bad) is None, bad
    assert t.part_name(7) == "part-00007.parquet"
    with pytest.raises(ValueError, match="out of range"):
        t.part_name(100_000)


HEAT = "fse/heatmaps/"


@pytest.mark.parametrize(
    "key",
    [
        HEAT + "symbol=SPX/metric=gamma/month=2025-10/x=y/part-00000.parquet",  # nested
        HEAT + "symbol=a,b/metric=gamma/month=2025-10/part-00000.parquet",  # comma
        HEAT + "symbol=SPX/metric=gamma/month=2025-1/part-00000.parquet",
        HEAT + "symbol=SPX/metric=gamma/month=2025-13/part-00000.parquet",
        HEAT + "symbol=SPX/metric=gamma/month=foo/part-00000.parquet",
        HEAT + "metric=gamma/symbol=SPX/month=2025-10/part-00000.parquet",  # wrong order
        HEAT + "symbolSPX/metric=gamma/month=2025-10/part-00000.parquet",  # missing '='
        HEAT + "symbol=SPX/metric=gamma/month=2025-10/notes.txt",  # not a part
        "fse/bars/instrument=MES/part-00000.parquet",  # another table
        HEAT + "symbol=SPX/part-00000.parquet",  # too shallow
        "other/heatmaps/symbol=SPX/metric=gamma/month=2025-10/part-00000.parquet",
    ],
)
def test_parse_partition_key_rejects(key: str) -> None:
    assert t.parse_partition_key("fse", t.TABLES["heatmaps"], key) is None


def test_parse_unpartitioned() -> None:
    spec = t.TABLES["vix_daily"]
    assert t.parse_partition_key("fse", spec, "fse/vix_daily/part-00001.parquet") == {}
    assert t.parse_partition_key("fse", spec, "fse/vix_daily/x/part-00001.parquet") is None


def test_glue_type_mapping_covers_every_column() -> None:
    expected = {
        pa.int64(): "bigint",
        pa.int32(): "int",
        pa.float64(): "double",
        pa.bool_(): "boolean",
        pa.string(): "string",
        pa.date32(): "date",
        pa.list_(pa.float64()): "array<double>",
        pa.list_(pa.string()): "array<string>",
    }
    for dtype, glue in expected.items():
        assert t.glue_type(dtype) == glue
    for spec in t.TABLES.values():
        for f in spec.schema:
            assert t.glue_type(f.type) in expected.values()
    for bad in (pa.int8(), pa.timestamp("ns"), pa.list_(pa.int64()), pa.float32()):
        with pytest.raises(TypeError):
            t.glue_type(bad)


@pytest.mark.parametrize(
    ("table", "sources"),
    [
        ("heatmaps", (WINDOW_SCHEMA, AXIS_SCHEMA)),
        ("bars", (BARS_SCHEMA,)),
        ("vix_bars", (BARS_SCHEMA,)),
        ("vix_daily", (VIX_DAILY_SCHEMA,)),
        ("darkpool", (DARKPOOL_SCHEMA,)),
        ("regime_medians", (MEDIANS_SCHEMA,)),
    ],
)
def test_every_source_field_reaches_the_output(table: str, sources: tuple[pa.Schema, ...]) -> None:
    out = t.TABLES[table].schema
    renames = t.RENAMES.get(table, {})
    for schema in sources:
        for f in schema:
            if schema is AXIS_SCHEMA and f.name == "axis_id":
                continue  # the join key; the window's own axis_id column is kept
            name = renames.get(f.name, f.name)
            assert name in out.names, (table, f.name)
            assert out.field(name).type.equals(f.type), (table, f.name)


def test_sort_keys_cover_every_table_with_real_columns() -> None:
    assert set(t.SORT_KEYS) == set(t.TABLES)
    assert t.SORT_KEYS["bars"] == ("interval_s", "session", "open_ns")
    assert t.SORT_KEYS["vix_daily"] == ("session",)
    assert t.SORT_KEYS["darkpool_fetched_dates"] == ("ticker", "trade_date")
    assert t.SORT_KEYS["heatmaps"] == ("view_id", "session", "window_start_ns", "window_row")
    for name, keys in t.SORT_KEYS.items():
        assert set(keys) <= set(t.TABLES[name].schema.names), name


def test_partition_columns_are_not_file_columns() -> None:
    for spec in t.TABLES.values():
        assert not set(spec.partition_keys) & set(spec.schema.names), spec.name


def test_no_prefix_fstring_outside_tables() -> None:
    pattern = re.compile(r"f\"[^\"]*\{[^}]*prefix\}/|f'[^']*\{[^}]*prefix\}/")
    offenders = [
        p.name
        for p in sorted(PACKAGE.glob("*.py"))
        if p.name != "tables.py" and pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []
