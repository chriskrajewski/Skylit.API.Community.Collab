"""Opt-in: stage a small subset of the Operator's real Data_Cache, read-only (design §14).

Run with ``python -m pytest -m localcache tests/integration/test_aws_export_localcache.py -rP``.
The default run deselects it. The cache root is ``FSE_LOCALCACHE_DIR``, else
``~/.skylit-fse/cache`` of the real user (the suite replaces ``HOME``).

Nothing in the cache is written: the catalog is opened ``mode=ro`` and the
test compares the catalog sha256 and the size and mtime of every file it
touched before and after. Staging goes to a ``tempfile.mkdtemp()`` directory
that is removed in ``finally``; the test asserts it is gone. No AWS call is
made.

Subsets:

- SPX gamma heatmaps of session 2025-10-06 (28 complete windows, a fixed
  historical fact) and the MES bars of that session, through the test-only
  plan seam;
- ``vix_daily`` and ``darkpool`` (both tables) in full; they are small.

Assertions: staged row counts equal the source; every column is bit-identical
(float64 via ``.view(pa.int64())``, lists by lengths and flattened children).
Only layout facts of the cache are asserted, never counts that a pull changes.

**Validates: aws-export design §14 "Real-cache verification"**
"""

from __future__ import annotations

import hashlib
import io
import os
import pwd
import shutil
import sqlite3
import tempfile
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest

from fse.awsexport import source, staging
from fse.awsexport.tables import SORT_KEYS
from fse.data.aux_stores import trade_date_of
from fse.logio import LogWriter, Redactor
from tests.fakes.export_cache import (
    assert_column_exact,
    expected_window_columns,
    opts,
    read_staged,
)

pytestmark = pytest.mark.localcache

SESSION = date(2025, 10, 6)
WINDOW_KEYS = {
    b"fse.format",
    b"fse.symbol",
    b"fse.metric",
    b"fse.view_id",
    b"fse.view",
    b"fse.session",
    b"fse.window_start_ns",
    b"fse.source_endpoint",
    b"fse.storage_interval_s",
    b"fse.axes",
}


def _root() -> Path:
    configured = os.environ.get("FSE_LOCALCACHE_DIR")
    if configured:
        return Path(configured)
    return Path(pwd.getpwuid(os.getuid()).pw_dir) / ".skylit-fse" / "cache"


@pytest.fixture(scope="module")
def root() -> Path:
    path = _root()
    if not (path / "catalog.sqlite").is_file():
        pytest.skip(f"no Data_Cache catalog at {path}")
    return path


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _snapshot(root: Path, files: list[Path]) -> dict[str, object]:
    catalog = root / "catalog.sqlite"
    out: dict[str, object] = {"catalog.sha256": _sha(catalog)}
    for path in [catalog, *files]:
        st = path.stat()
        out[str(path)] = (st.st_size, st.st_mtime_ns)
    return out


def _writer() -> LogWriter:
    return LogWriter(Redactor(()), stdout=io.StringIO())


def test_heatmap_and_bar_subset_round_trip(root: Path) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="fse-awsexport-localcache-"))
    try:
        options = opts(root, tmp / "stage", datasets="heatmaps,bars", symbols="SPX")
        plan = source.plan(
            options,
            sessions=frozenset({SESSION}),
            metrics=frozenset({"gamma"}),
            instruments=frozenset({"MES"}),
        )
        if plan.window_count != 28:
            pytest.skip(f"SPX gamma {SESSION} has {plan.window_count} windows in this cache")
        assert plan.bar_session_count == 1
        heat, bars = plan.partitions
        windows = [w for w in heat.inputs if isinstance(w, source.WindowInput)]
        bar_inputs = [b for b in bars.inputs if isinstance(b, source.BarInput)]
        touched = [w.path for w in windows] + [b.path for b in bar_inputs]

        # 1. layout facts of one real window
        first = pq.read_table(windows[0].path)
        md = first.schema.metadata
        assert set(md) >= WINDOW_KEYS
        assert md[b"fse.format"] == b"fse.heatmap-window/1"
        axes = pa.ipc.open_stream(pa.BufferReader(md[b"fse.axes"])).read_all()
        assert axes.column_names == ["axis_id", "strikes", "expirations"]
        assert first.column_names == [
            "as_of_ns",
            "as_of_raw",
            "spot",
            "previous_close",
            "axis_id",
            "values",
            "node_types",
            "resolution",
            "source_endpoint",
            "extra_json",
        ]
        row0 = first.slice(0, 1).to_pylist()[0]
        axis0 = axes.filter(pc.equal(axes["axis_id"], row0["axis_id"])).to_pylist()[0]
        assert len(row0["values"]) == len(axis0["strikes"])

        # 2. snapshot, 3. stage
        before = _snapshot(root, touched)
        report = staging.stage(plan, tmp / "stage", writer=_writer())
        assert report.windows_read == 28
        assert report.bar_sessions_read == 1

        # 4. counts and bit-exact columns
        conn = sqlite3.connect(f"{(root / 'catalog.sqlite').as_uri()}?mode=ro", uri=True)
        try:
            catalog_rows = conn.execute(
                "SELECT SUM(\"rows\") FROM windows WHERE symbol = 'SPX' AND metric = 'gamma' "
                "AND session = ? AND status = 'complete'",
                (SESSION.isoformat(),),
            ).fetchone()[0]
            bar_rows = conn.execute(
                "SELECT \"rows\" FROM bars_coverage WHERE dataset = 'bars' AND instrument = 'MES' "
                "AND session = ? AND status = 'complete'",
                (SESSION.isoformat(),),
            ).fetchone()[0]
        finally:
            conn.close()
        out = read_staged(tmp / "stage", heat.rel_path)
        assert out.num_rows == heat.source_rows == catalog_rows
        for w in windows:
            mask = pc.and_(
                pc.equal(out["view_id"], w.view_id), pc.equal(out["window_start_ns"], w.start_ns)
            )
            got = out.filter(mask)
            assert got["window_row"].to_pylist() == list(range(w.rows))
            for name, col in expected_window_columns(w.path).items():
                assert_column_exact(f"{w.start_ns}.{name}", got[name], col)
        staged_bars = read_staged(tmp / "stage", bars.rel_path)
        src_bars = pq.read_table(bar_inputs[0].path)
        assert staged_bars.num_rows == bars.source_rows == bar_rows == src_bars.num_rows
        for name in src_bars.column_names:
            assert_column_exact(f"bars.{name}", staged_bars[name], src_bars[name])

        # 5. only the two partitions were staged
        staged_dirs = sorted(
            p.parent.relative_to(tmp / "stage" / "data").as_posix()
            for p in (tmp / "stage" / "data").rglob("part-*.parquet")
        )
        assert staged_dirs == [
            "bars/instrument=MES",
            "heatmaps/symbol=SPX/metric=gamma/month=2025-10",
        ]

        # 6. the cache is unchanged
        assert _snapshot(root, touched) == before

        # 7. the measured staged/source byte ratio
        heat_bytes = sum(p.stat().st_size for p in (tmp / "stage/data/heatmaps").rglob("*.parquet"))
        bar_bytes = sum(p.stat().st_size for p in (tmp / "stage/data/bars").rglob("*.parquet"))
        print(
            f"heatmaps SPX gamma {SESSION}: windows={len(windows)} rows={out.num_rows} "
            f"(catalog {catalog_rows}) source_bytes={heat.source_bytes} staged_bytes={heat_bytes} "
            f"ratio={heat_bytes / heat.source_bytes:.3f}"
        )
        print(
            f"bars MES {SESSION}: rows={staged_bars.num_rows} (catalog {bar_rows}) "
            f"source_bytes={bars.source_bytes} staged_bytes={bar_bytes} "
            f"ratio={bar_bytes / bars.source_bytes:.3f}"
        )
        print("bit-exact: every column of all 28 windows and the bar session matched")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    assert not tmp.exists()


def test_vix_daily_and_darkpool_round_trip(root: Path) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="fse-awsexport-localcache-"))
    try:
        plan = source.plan(opts(root, tmp / "stage", datasets="vix_daily,darkpool"))
        touched = [i.path for pp in plan.partitions for i in pp.inputs]
        before = _snapshot(root, touched)
        staging.stage(plan, tmp / "stage", writer=_writer())

        vix = next(p for p in plan.partitions if p.table == "vix_daily")
        out = read_staged(tmp / "stage", vix.rel_path)
        src = pq.read_table(vix.inputs[0].path)
        want = src.take(pc.sort_indices(src, sort_keys=[("session", "ascending")]))
        assert out.num_rows == vix.source_rows == src.num_rows
        for name in want.column_names:
            assert_column_exact(f"vix.{name}", out[name], want[name])
        print(f"vix_daily: rows={out.num_rows} (source {src.num_rows}) bit-exact")

        for pp in (p for p in plan.partitions if p.table == "darkpool"):
            files = [i for i in pp.inputs if isinstance(i, source.FileInput)]
            staged = read_staged(tmp / "stage", pp.rel_path)
            assert staged.num_rows == pp.source_rows
            # One month checked file by file: its prints are exactly the staged
            # rows whose trade_date falls in that month, in source order.
            for f in files:
                month = f.key[1]
                src_month = pq.read_table(f.path)
                year, mon = int(month[:4]), int(month[5:])
                in_month = pc.and_(
                    pc.equal(pc.year(staged["trade_date"]), year),
                    pc.equal(pc.month(staged["trade_date"]), mon),
                )
                got = staged.filter(in_month)
                want_m = src_month.take(
                    pc.sort_indices(
                        pa.table(
                            {
                                "trade_date": pa.array(
                                    [trade_date_of(t) for t in src_month["ts_ns"].to_pylist()],
                                    pa.date32(),
                                ),
                                "ts_ns": src_month["ts_ns"],
                            }
                        ),
                        sort_keys=[(k, "ascending") for k in SORT_KEYS["darkpool"]],
                    )
                )
                assert got.num_rows == src_month.num_rows, (pp.rel_path, month)
                for name in src_month.column_names:
                    assert_column_exact(f"{pp.rel_path}{month}.{name}", got[name], want_m[name])
            print(
                f"{pp.rel_path}: months={len(files)} rows={staged.num_rows} "
                f"(source {pp.source_rows}) bit-exact per month"
            )
        fetched = next(p for p in plan.partitions if p.table == "darkpool_fetched_dates")
        assert read_staged(tmp / "stage", fetched.rel_path).num_rows == fetched.source_rows
        print(f"darkpool_fetched_dates: rows={fetched.source_rows}")
        assert _snapshot(root, touched) == before
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    assert not tmp.exists()
