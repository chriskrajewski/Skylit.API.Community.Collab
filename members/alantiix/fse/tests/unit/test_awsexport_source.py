"""The read-only planner of ``fse export-aws`` (design §8.1, review NIT 6).

The synthetic cache is written with the real writers under ``tmp_path``.

**Validates: aws-export design §14 test 3**
"""

from __future__ import annotations

import io
import json
import shutil
import sqlite3
from datetime import time
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from fse.awsexport import source, staging
from fse.awsexport.errors import ExportDataError, ExportInputError
from fse.data.cache import DataCache
from fse.data.cache_io import CacheIntegrityError
from fse.logio import LogWriter, Redactor
from fse.timekit import ny_instant
from tests.fakes.export_cache import (
    SESSIONS,
    VIEW_A,
    BuiltCache,
    build_cache,
    opts,
    snapshot,
    tree_snapshot,
)


@pytest.fixture
def built(tmp_path: Path) -> BuiltCache:
    return build_cache(tmp_path / "cache")


def _plan(tmp_path: Path, **kw: object) -> source.Plan:
    return source.plan(opts(tmp_path / "cache", tmp_path / "stage", **kw))


def _by_rel(plan: source.Plan) -> dict[str, source.PartitionPlan]:
    return {p.rel_path: p for p in plan.partitions}


def test_plan_counts_and_skips(tmp_path: Path, built: BuiltCache) -> None:
    plan = _plan(tmp_path)
    assert plan.window_count == len(built.complete_windows) == 17
    assert plan.bar_session_count == 2
    assert dict(plan.skipped["heatmaps"]) == {"incomplete": 1, "no_data": 1}
    assert plan.tables_without_data == ("vix_bars",)
    rels = list(_by_rel(plan))
    assert rels[:2] == [
        "heatmaps/symbol=SPX/metric=gamma/month=2025-10/",
        "heatmaps/symbol=SPX/metric=gamma/month=2025-11/",
    ]
    assert rels[8:] == [
        "bars/instrument=MES/",
        "vix_daily/",
        "darkpool/ticker=QQQ/",
        "darkpool/ticker=SPY/",
        "darkpool_fetched_dates/",
        "regime_medians/",
    ]
    heat = _by_rel(plan)["heatmaps/symbol=SPX/metric=gamma/month=2025-10/"]
    windows = [i for i in heat.inputs if isinstance(i, source.WindowInput)]
    assert [w.view_id for w in windows] == sorted(w.view_id for w in windows)
    assert len({w.view_id for w in windows}) == 2  # both views in one month file


def test_source_rows_and_bytes_match_independent_definitions(
    tmp_path: Path, built: BuiltCache
) -> None:
    root = tmp_path / "cache"
    parts = _by_rel(_plan(tmp_path))
    conn = sqlite3.connect(root / "catalog.sqlite")
    try:
        rows = conn.execute(
            "SELECT SUM(\"rows\") FROM windows WHERE status = 'complete' AND symbol = 'SPX' "
            "AND metric = 'gamma' AND session LIKE '2025-10-%'"
        ).fetchone()[0]
        bar_rows = conn.execute(
            "SELECT SUM(\"rows\") FROM bars_coverage WHERE status = 'complete'"
        ).fetchone()[0]
    finally:
        conn.close()
    heat = parts["heatmaps/symbol=SPX/metric=gamma/month=2025-10/"]
    assert heat.source_rows == rows == 10
    files = sorted((root / "heatmaps/SPX/gamma").glob("*/2025-10-*/*.parquet"))
    assert heat.source_bytes == sum(f.stat().st_size for f in files)
    assert parts["bars/instrument=MES/"].source_rows == bar_rows == 6
    spy = sorted((root / "darkpool/SPY").glob("*.parquet"))
    assert parts["darkpool/ticker=SPY/"].source_rows == sum(
        pq.read_metadata(f).num_rows for f in spy
    )
    assert parts["darkpool/ticker=SPY/"].source_bytes == sum(f.stat().st_size for f in spy)
    every = sorted((root / "darkpool").glob("*/*.parquet"))
    assert parts["darkpool_fetched_dates/"].source_rows == sum(
        len(json.loads(pq.read_schema(f).metadata[b"fse.trade_dates"])) for f in every
    )
    assert parts["vix_daily/"].source_rows == 2
    assert parts["regime_medians/"].source_rows == 4


def test_fingerprint_changes_only_with_its_inputs(tmp_path: Path, built: BuiltCache) -> None:
    before = {p.rel_path: p.fingerprint for p in _plan(tmp_path).partitions}
    key = built.complete_windows[0]  # SPX gamma 2025-10-31 09:30
    with DataCache(tmp_path / "cache") as cache:
        cache.write_window(key, [snapshot(key, i) for i in range(2)], "range", view=VIEW_A)
    after = {p.rel_path: p.fingerprint for p in _plan(tmp_path).partitions}
    changed = sorted(rel for rel in before if before[rel] != after[rel])
    assert changed == ["heatmaps/symbol=SPX/metric=gamma/month=2025-10/"]


def test_catalog_is_opened_read_only(tmp_path: Path, built: BuiltCache) -> None:
    conn = source.open_catalog_ro(tmp_path / "cache" / "catalog.sqlite")
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE x (a)")
    finally:
        conn.close()


def test_plan_and_stage_never_write_the_cache(tmp_path: Path, built: BuiltCache) -> None:
    root = tmp_path / "cache"
    (root / ".DS_Store").write_bytes(b"x")
    (root / "darkpool" / "SPY" / "2025-12.parquet.tmp").write_bytes(b"partial")
    before = tree_snapshot(root)
    plan = _plan(tmp_path)
    staging.stage(plan, tmp_path / "stage", writer=LogWriter(Redactor(()), stdout=io.StringIO()))
    assert tree_snapshot(root) == before
    assert not any(p.name.endswith(".tmp") for pp in plan.partitions for p in _paths(pp))


def _paths(pp: source.PartitionPlan) -> list[Path]:
    return [i.path for i in pp.inputs]


def test_missing_cache_or_catalog_is_exit_4(tmp_path: Path) -> None:
    (tmp_path / "cache").mkdir()
    with pytest.raises(ExportDataError, match="no Data_Cache catalog") as info:
        _plan(tmp_path)
    assert info.value.exit_code == 4
    with pytest.raises(ExportDataError, match="does not exist"):
        source.plan(opts(tmp_path / "nowhere", tmp_path / "stage"))


def test_wrong_catalog_version_is_exit_4(tmp_path: Path, built: BuiltCache) -> None:
    catalog = tmp_path / "cache" / "catalog.sqlite"
    conn = sqlite3.connect(catalog)
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    conn.close()
    with pytest.raises(ExportDataError, match="schema version 3") as info:
        _plan(tmp_path)
    assert info.value.exit_code == 4


def test_not_a_catalog_is_exit_4(tmp_path: Path) -> None:
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "catalog.sqlite").write_bytes(b"not sqlite at all" * 100)
    with pytest.raises(ExportDataError) as info:
        _plan(tmp_path)
    assert info.value.exit_code == 4


def test_unknown_symbol_is_exit_2(tmp_path: Path, built: BuiltCache) -> None:
    with pytest.raises(ExportInputError, match="SPXX") as info:
        _plan(tmp_path, symbols="SPX,SPXX")
    assert info.value.exit_code == 2
    assert "SPX" in str(info.value)


def test_symbols_and_months_select_whole_partitions(tmp_path: Path, built: BuiltCache) -> None:
    plan = _plan(tmp_path, datasets="heatmaps", symbols="SPY", months="2025-11,2026-01")
    assert [p.rel_path for p in plan.partitions] == [
        "heatmaps/symbol=SPY/metric=gamma/month=2025-11/",
        "heatmaps/symbol=SPY/metric=vanna/month=2025-11/",
    ]
    assert plan.warnings == ("--months 2026-01: no complete heatmap window",)
    assert plan.in_scope("heatmaps", {"symbol": "SPY", "metric": "gamma", "month": "2025-11"})
    assert not plan.in_scope("heatmaps", {"symbol": "SPX", "metric": "gamma", "month": "2025-11"})
    assert not plan.in_scope("bars", {"instrument": "MES"})  # not selected


def test_test_seam_filters(tmp_path: Path, built: BuiltCache) -> None:
    plan = source.plan(
        opts(tmp_path / "cache", tmp_path / "stage", datasets="heatmaps,bars", symbols="SPX"),
        sessions=frozenset({SESSIONS[0]}),
        metrics=frozenset({"gamma"}),
        instruments=frozenset({"MES"}),
    )
    assert plan.window_count == 3
    assert plan.bar_session_count == 1
    assert [p.rel_path for p in plan.partitions] == [
        "heatmaps/symbol=SPX/metric=gamma/month=2025-10/",
        "bars/instrument=MES/",
    ]
    assert plan.partial == frozenset({"heatmaps", "bars", "vix_bars"})
    assert not plan.in_scope("heatmaps", {"symbol": "SPX", "metric": "gamma", "month": "2025-10"})


def test_unsafe_partition_value_is_exit_4(tmp_path: Path, built: BuiltCache) -> None:
    bad = tmp_path / "cache" / "darkpool" / "A,B"
    bad.mkdir()
    shutil.copy(
        tmp_path / "cache" / "darkpool" / "QQQ" / "2025-10.parquet", bad / "2025-10.parquet"
    )
    with pytest.raises(ExportDataError, match="unsafe") as info:
        _plan(tmp_path, datasets="darkpool")
    assert info.value.exit_code == 4


def test_missing_window_file_is_exit_4(tmp_path: Path, built: BuiltCache) -> None:
    key = built.complete_windows[0]
    DataCache(tmp_path / "cache").window_path(key).unlink()
    with pytest.raises(CacheIntegrityError, match="missing") as info:
        _plan(tmp_path)
    assert info.value.exit_code == 4


def test_invalid_catalog_key_is_exit_4(tmp_path: Path, built: BuiltCache) -> None:
    conn = sqlite3.connect(tmp_path / "cache" / "catalog.sqlite")
    conn.execute(
        "INSERT INTO windows VALUES ('SPX', 'gamma', 'not-hex', '2025-10-31', ?, 'complete', 1, "
        "'range', ?, 0)",
        (ny_instant(SESSIONS[0], time(11, 0)), "0" * 64),
    )
    conn.commit()
    conn.close()
    with pytest.raises(ExportDataError, match="not a valid Cache_Window key") as info:
        _plan(tmp_path)
    assert info.value.exit_code == 4
