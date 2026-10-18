"""Staging directory, manifest and commit protocol of ``fse export-aws`` (design §8.3).

**Validates: aws-export design §14 test 6**
"""

from __future__ import annotations

import io
import shutil
from collections import namedtuple
from pathlib import Path
from typing import Any

import pytest

from fse.awsexport import source, staging, transform
from fse.awsexport.errors import ExportDataError
from fse.data.cache import DataCache
from fse.logio import LogWriter, Redactor
from tests.fakes.export_cache import BuiltCache, build_cache, opts, snapshot, tree_snapshot

HEAT_OCT = "heatmaps/symbol=SPX/metric=gamma/month=2025-10/"


@pytest.fixture
def built(tmp_path: Path) -> BuiltCache:
    return build_cache(tmp_path / "cache")


def _run(tmp_path: Path, *, dry_run: bool = False, **kw: object) -> tuple[staging.StageReport, str]:
    out = io.StringIO()
    plan = source.plan(opts(tmp_path / "cache", tmp_path / "stage", **kw))
    report = staging.stage(
        plan, tmp_path / "stage", writer=LogWriter(Redactor(()), stdout=out), dry_run=dry_run
    )
    return report, out.getvalue()


def _mtimes(stage: Path) -> dict[str, int]:
    return {
        p.relative_to(stage).as_posix(): p.stat().st_mtime_ns
        for p in sorted((stage / "data").rglob("*.parquet"))
    }


def test_first_run_stages_everything_and_writes_the_manifest(
    tmp_path: Path, built: BuiltCache
) -> None:
    report, out = _run(tmp_path)
    assert len(report.restaged) == 14
    assert report.up_to_date == ()
    assert report.windows_read == 17
    assert report.bar_sessions_read == 2
    manifest, warning = staging.load_manifest(tmp_path / "stage")
    assert warning is None
    assert set(manifest.partitions) == set(report.restaged)
    entry = manifest.partitions[HEAT_OCT]
    assert entry.rows == 10
    assert [f.name for f in entry.files] == ["part-00000.parquet"]
    part = tmp_path / "stage/data/heatmaps/symbol=SPX/metric=gamma/month=2025-10/part-00000.parquet"
    assert entry.files[0].sha256 == staging.file_sha256(part)
    assert entry.files[0].size == part.stat().st_size
    assert not (tmp_path / "stage/.work").exists()
    assert f"staged {HEAT_OCT}: 10 rows" in out


def test_second_run_restages_nothing(tmp_path: Path, built: BuiltCache) -> None:
    _run(tmp_path)
    before = _mtimes(tmp_path / "stage")
    report, _ = _run(tmp_path)
    assert report.restaged == ()
    assert len(report.up_to_date) == 14
    assert _mtimes(tmp_path / "stage") == before


def test_one_changed_window_restages_its_partition_only(tmp_path: Path, built: BuiltCache) -> None:
    _run(tmp_path)
    before = _mtimes(tmp_path / "stage")
    key = built.complete_windows[0]
    with DataCache(tmp_path / "cache") as cache:
        cache.write_window(key, [snapshot(key, 1)], "range")
    report, _ = _run(tmp_path)
    assert report.restaged == (HEAT_OCT,)
    after = _mtimes(tmp_path / "stage")
    changed = sorted(k for k in before if before[k] != after[k])
    assert changed == [f"data/{HEAT_OCT}part-00000.parquet"]


def test_missing_or_resized_staged_file_restages(tmp_path: Path, built: BuiltCache) -> None:
    _run(tmp_path)
    (tmp_path / "stage/data/vix_daily/part-00000.parquet").unlink()
    report, _ = _run(tmp_path)
    assert report.restaged == ("vix_daily/",)


def test_interrupt_mid_partition_keeps_the_manifest_consistent(
    tmp_path: Path, built: BuiltCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = transform.PartWriter.write
    calls = {"n": 0}

    def flaky(self: transform.PartWriter, table: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 4:
            raise KeyboardInterrupt
        original(self, table)

    monkeypatch.setattr(transform.PartWriter, "write", flaky)
    with pytest.raises(KeyboardInterrupt):
        _run(tmp_path)
    manifest, warning = staging.load_manifest(tmp_path / "stage")
    assert warning is None
    committed = set(manifest.partitions)
    assert len(committed) == 2  # two heatmap partitions finished before the interrupt
    for rel in committed:
        entry = manifest.partitions[rel]
        for f in entry.files:
            assert (
                staging.staged_dir(tmp_path / "stage", rel).joinpath(f.name).stat().st_size
                == f.size
            )
    monkeypatch.setattr(transform.PartWriter, "write", original)
    report, _ = _run(tmp_path)
    assert set(report.up_to_date) == committed
    assert len(report.restaged) == 14 - len(committed)
    assert not (tmp_path / "stage/.work").exists()


def test_leftover_work_dir_is_cleared(tmp_path: Path, built: BuiltCache) -> None:
    junk = tmp_path / "stage/.work/heatmaps/symbol=SPX"
    junk.mkdir(parents=True)
    (junk / "part-00000.parquet").write_bytes(b"torn")
    _run(tmp_path)
    assert not (tmp_path / "stage/.work").exists()


def test_unreadable_manifest_warns_and_rebuilds(tmp_path: Path, built: BuiltCache) -> None:
    _run(tmp_path)
    (tmp_path / "stage/manifest.json").write_text("{not json")
    report, out = _run(tmp_path)
    assert "warning: staging manifest unreadable; restaging everything" in out
    assert len(report.restaged) == 14
    manifest, warning = staging.load_manifest(tmp_path / "stage")
    assert warning is None
    assert len(manifest.partitions) == 14


def test_unknown_manifest_format_warns(tmp_path: Path, built: BuiltCache) -> None:
    (tmp_path / "stage").mkdir()
    (tmp_path / "stage/manifest.json").write_text('{"format": "other/9", "partitions": {}}')
    _, out = _run(tmp_path, datasets="vix_daily")
    assert "warning: staging manifest unreadable" in out


def test_removed_source_partition_is_pruned(tmp_path: Path, built: BuiltCache) -> None:
    _run(tmp_path)
    shutil.rmtree(tmp_path / "cache/darkpool/QQQ")
    report, out = _run(tmp_path)
    assert report.pruned == ("darkpool/ticker=QQQ/",)
    assert "pruned staged partition darkpool/ticker=QQQ/" in out
    assert not (tmp_path / "stage/data/darkpool/ticker=QQQ").exists()
    manifest, _ = staging.load_manifest(tmp_path / "stage")
    assert "darkpool/ticker=QQQ/" not in manifest.partitions
    assert report.restaged == ("darkpool_fetched_dates/",)  # its inputs changed too


def test_partitions_outside_the_filters_are_kept(tmp_path: Path, built: BuiltCache) -> None:
    _run(tmp_path)
    report, _ = _run(tmp_path, datasets="heatmaps", symbols="SPY")
    assert report.pruned == ()
    manifest, _ = staging.load_manifest(tmp_path / "stage")
    assert len(manifest.partitions) == 14


def test_disk_shortfall_is_exit_4_with_nothing_written(
    tmp_path: Path, built: BuiltCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(staging.shutil, "disk_usage", lambda path: usage(10, 10, 1000))
    with pytest.raises(ExportDataError, match="not enough free disk space") as info:
        _run(tmp_path)
    assert info.value.exit_code == 4
    assert not (tmp_path / "stage").exists()


def test_dry_run_writes_nothing(tmp_path: Path, built: BuiltCache) -> None:
    _run(tmp_path)
    shutil.rmtree(tmp_path / "cache/darkpool/QQQ")
    (tmp_path / "stage/.work").mkdir()
    before = tree_snapshot(tmp_path / "stage")
    report, out = _run(tmp_path, dry_run=True)
    assert tree_snapshot(tmp_path / "stage") == before
    assert (tmp_path / "stage/.work").is_dir()
    assert report.pruned == ("darkpool/ticker=QQQ/",)
    assert report.restaged == ("darkpool_fetched_dates/",)
    for line in out.splitlines():
        assert line.startswith(("would ", "plan: ", "warning: ")), line


def test_dry_run_on_a_missing_staging_dir_creates_nothing(
    tmp_path: Path, built: BuiltCache
) -> None:
    report, _ = _run(tmp_path, dry_run=True)
    assert len(report.restaged) == 14
    assert report.pending_source_bytes > 0
    assert not (tmp_path / "stage").exists()


def test_guarded_delete_refuses_paths_outside_work_and_data(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    (stage / "other").mkdir(parents=True)
    with pytest.raises(AssertionError, match="refusing to delete"):
        staging._rmtree_checked(stage / "other", stage, rel_path=None)
    with pytest.raises(AssertionError, match="refusing to delete"):
        staging._rmtree_checked(stage, stage, rel_path=None)
    with pytest.raises(AssertionError, match="refusing to delete"):
        staging._rmtree_checked(stage / "data", stage, rel_path="vix_daily/")
    assert (stage / "other").is_dir()
