"""Stage transforms of ``fse export-aws``: exactness, rollover and integrity (design §8.2).

Expected rows are the source rows sorted with stable ``sort_indices`` on the
table's row-order keys, compared position by position: float64 by bit pattern
(``.view(pa.int64())``), lists by lengths and flattened children, never raw
offsets. Malformed source files are written by hand with pyarrow, bypassing
the cache writers, and the catalog sha256 is updated to match.

**Validates: aws-export design §14 test 4**
"""

from __future__ import annotations

import hashlib
import io
import json
import random
import sqlite3
from collections.abc import Callable, Iterator
from datetime import time
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest

from fse.awsexport import source, staging, transform
from fse.awsexport.errors import ExportDataError
from fse.awsexport.tables import SORT_KEYS, TABLES
from fse.data.cache import CacheWindowKey, DataCache, HeatmapView
from fse.data.cache_io import CacheIntegrityError, table_to_ipc
from fse.engine.types import Snapshot
from fse.logio import LogWriter, Redactor
from fse.timekit import NS_PER_SECOND, ny_instant
from tests.fakes.export_cache import (
    NAN_PAYLOAD,
    SESSIONS,
    BuiltCache,
    assert_column_exact,
    build_cache,
    expected_window_columns,
    opts,
    read_staged,
)


@pytest.fixture
def built(tmp_path: Path) -> BuiltCache:
    return build_cache(tmp_path / "cache")


def _writer() -> LogWriter:
    return LogWriter(Redactor(()), stdout=io.StringIO())


def _stage(tmp_path: Path, **kw: object) -> tuple[source.Plan, staging.StageReport]:
    plan = source.plan(opts(tmp_path / "cache", tmp_path / "stage", **kw))
    return plan, staging.stage(plan, tmp_path / "stage", writer=_writer())


def _sorted(table: pa.Table, name: str) -> pa.Table:
    return table.take(pc.sort_indices(table, sort_keys=[(k, "ascending") for k in SORT_KEYS[name]]))


def _assert_table_exact(got: pa.Table, want: pa.Table, label: str) -> None:
    assert got.schema.names == want.schema.names, label
    for name in want.schema.names:
        assert_column_exact(f"{label}.{name}", got[name], want[name])


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rewrite(path: Path, table: pa.Table, catalog: Path | None = None) -> None:
    """Replace a cache file by hand; update its catalog sha256 when it is catalogued."""
    old = _sha(path)
    pq.write_table(table, path, compression="zstd")
    if catalog is not None:
        conn = sqlite3.connect(catalog)
        for t in ("windows", "bars_coverage"):
            conn.execute(f"UPDATE {t} SET sha256 = ? WHERE sha256 = ?", (_sha(path), old))
        conn.commit()
        conn.close()


# ---------------------------------------------------------------- round trips


def test_heatmaps_round_trip_bit_identical_per_window(tmp_path: Path, built: BuiltCache) -> None:
    plan, _ = _stage(tmp_path, datasets="heatmaps")
    cache = DataCache(tmp_path / "cache")
    nan_seen = False
    for pp in plan.partitions:
        out = read_staged(tmp_path / "stage", pp.rel_path)
        assert out.schema.equals(TABLES["heatmaps"].schema), pp.rel_path
        assert out.num_rows == pp.source_rows
        _assert_table_exact(out, _sorted(out, "heatmaps"), f"{pp.rel_path} row order")
        for w in pp.inputs:
            assert isinstance(w, source.WindowInput)
            mask = pc.and_(
                pc.and_(
                    pc.equal(out["view_id"], w.view_id),
                    pc.equal(out["session"], pa.scalar(w.session)),
                ),
                pc.equal(out["window_start_ns"], w.start_ns),
            )
            got = out.filter(mask)
            assert got["window_row"].to_pylist() == list(range(w.rows))
            want = expected_window_columns(
                cache.window_path(
                    CacheWindowKey(
                        w.symbol,
                        w.metric,
                        w.view_id,
                        w.session,
                        w.start_ns,  # type: ignore[arg-type]
                    )
                )
            )
            for name, col in want.items():
                assert_column_exact(f"{pp.rel_path}{w.start_ns}.{name}", got[name], col)
            md = pq.read_schema(w.path).metadata
            view = md[b"fse.view"].decode() or None
            assert set(got["view_json"].to_pylist()) == {view}
            assert set(got["window_source_endpoint"].to_pylist()) == {"range"}
            assert got["storage_interval_s"].null_count == got.num_rows
            bits = pc.list_flatten(got["strike_values"]).combine_chunks().view(pa.int64())
            nan_seen |= 0x7FF8_0000_0000_0123 in bits.to_pylist()
    assert nan_seen, "the NaN payload did not survive"
    meta = pq.read_metadata(
        tmp_path / "stage/data/heatmaps/symbol=SPX/metric=gamma/month=2025-10/part-00000.parquet"
    )
    assert meta.num_row_groups == 2  # one per (view_id, session)
    kv = meta.metadata
    assert json.loads(kv[b"fse.export.partition"]) == {
        "symbol": "SPX",
        "metric": "gamma",
        "month": "2025-10",
    }
    assert kv[b"fse.export.table"] == b"heatmaps"


def test_bars_round_trip(tmp_path: Path, built: BuiltCache) -> None:
    plan, _ = _stage(tmp_path, datasets="bars")
    (pp,) = plan.partitions
    out = read_staged(tmp_path / "stage", pp.rel_path)
    assert out.schema.equals(TABLES["bars"].schema)
    pieces = []
    for b in pp.inputs:
        assert isinstance(b, source.BarInput)
        t = pq.read_table(b.path)
        n = t.num_rows
        pieces.append(
            pa.table(
                {
                    "session": pa.array([b.session] * n, pa.date32()),
                    "interval_s": pa.array([b.interval_s] * n, pa.int32()),
                    "contract": pa.array([b.contract] * n),
                    "source": pa.array([b.source] * n),
                    **{name: t[name] for name in t.column_names},
                }
            )
        )
    _assert_table_exact(out, _sorted(pa.concat_tables(pieces), "bars"), "bars")
    assert out["o_t"].null_count == 2  # one bar per session has no tick prices


def test_vix_daily_round_trip(tmp_path: Path, built: BuiltCache) -> None:
    _stage(tmp_path, datasets="vix_daily")
    out = read_staged(tmp_path / "stage", "vix_daily/")
    src = pq.read_table(tmp_path / "cache/vix/daily.parquet")
    _assert_table_exact(out, _sorted(src.select(out.schema.names), "vix_daily"), "vix_daily")
    assert out["open"].to_pylist()[0] == 0.0  # -0.0 kept: compared by bits above


def test_darkpool_round_trip(tmp_path: Path, built: BuiltCache) -> None:
    _stage(tmp_path, datasets="darkpool")
    from fse.data.aux_stores import trade_date_of

    for ticker in ("QQQ", "SPY"):
        out = read_staged(tmp_path / "stage", f"darkpool/ticker={ticker}/")
        src = pa.concat_tables(
            [
                pq.read_table(p)
                for p in sorted((tmp_path / f"cache/darkpool/{ticker}").glob("*.parquet"))
            ]
        )
        dates = pa.array([trade_date_of(ts) for ts in src["ts_ns"].to_pylist()], pa.date32())
        want = pa.table({"trade_date": dates, **{n: src[n] for n in src.column_names}})
        _assert_table_exact(out, _sorted(want, "darkpool"), ticker)
    fetched = read_staged(tmp_path / "stage", "darkpool_fetched_dates/")
    assert fetched.to_pylist() == [
        {"ticker": "QQQ", "trade_date": SESSIONS[0].replace(day=30), "month": "2025-10"},
        {"ticker": "SPY", "trade_date": SESSIONS[0].replace(day=30), "month": "2025-10"},
        {"ticker": "SPY", "trade_date": SESSIONS[0], "month": "2025-10"},  # no prints
        {"ticker": "SPY", "trade_date": SESSIONS[1], "month": "2025-11"},
    ]


def test_regime_medians_round_trip(tmp_path: Path, built: BuiltCache) -> None:
    _stage(tmp_path, datasets="regime_medians")
    out = read_staged(tmp_path / "stage", "regime_medians/")
    (path,) = sorted((tmp_path / "cache/derived/regime_medians").glob("*/*.parquet"))
    src = pq.read_table(path)
    md = src.schema.metadata
    n = src.num_rows
    want = pa.table(
        {
            "regime_symbol": pa.array([md[b"fse.symbol"].decode()] * n),
            "view_id": src["view_id"],
            "params_hash": src["params_hash"],
            "params_json": pa.array([md[b"fse.params"].decode()] * n),
            "inputs_sha256": pa.array([md[b"fse.inputs_sha256"].decode()] * n),
            "incomplete_windows": pa.array([1] * n, pa.int32()),
            **{c: src[c] for c in ("session", "metric", "median_raw_mag", "sessions", "usable")},
        }
    )
    _assert_table_exact(out, _sorted(want, "regime_medians"), "medians")


# ---------------------------------------------------------------- rollover


def _big_window(cache: DataCache, session_index: int, rng: random.Random) -> None:
    session = SESSIONS[0].replace(day=27 + session_index)  # 2025-10-27 .. 29
    view = HeatmapView()
    key = CacheWindowKey.for_view("NDX", "gamma", view, session, ny_instant(session, time(9, 30)))
    strikes = tuple(float(i) for i in range(300))
    snaps = [
        Snapshot(
            symbol="NDX",
            metric="gamma",
            view_id=key.view_id,
            as_of_ns=key.start_ns + i * NS_PER_SECOND,
            as_of_raw=str(i),
            spot=1.0,
            previous_close=None,
            strikes=strikes,
            values=tuple(rng.random() for _ in strikes),
            node_types=None,
            expirations=("2025-10-31",),
            resolution="1s",
            source_endpoint="range",
            extra_json="{}",
        )
        for i in range(40)
    ]
    cache.write_window(key, snaps, "range", view=view)


def test_rollover_at_max_file_bytes(
    tmp_path: Path, built: BuiltCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    rng = random.Random(7)
    with DataCache(tmp_path / "cache") as cache:
        for i in range(3):
            _big_window(cache, i, rng)
    monkeypatch.setattr(transform, "MAX_FILE_BYTES", 64 * 1024)
    plan, _ = _stage(tmp_path, datasets="heatmaps", symbols="NDX")
    (pp,) = plan.partitions
    directory = tmp_path / "stage/data/heatmaps/symbol=NDX/metric=gamma/month=2025-10"
    parts = sorted(directory.glob("part-*.parquet"))
    assert len(parts) > 1

    def compressed(meta: Any, i: int) -> int:
        rg = meta.row_group(i)
        return sum(rg.column(c).total_compressed_size for c in range(rg.num_columns))

    largest = max(
        compressed(pq.read_metadata(p), i)
        for p in parts
        for i in range(pq.read_metadata(p).num_row_groups)
    )
    for p in parts:
        assert p.stat().st_size < transform.MAX_FILE_BYTES + largest, p.name
    assert sum(pq.read_metadata(p).num_rows for p in parts) == pp.source_rows == 120


# ---------------------------------------------------------------- integrity failures


def _stage_fails(tmp_path: Path, match: str, **kw: object) -> None:
    with pytest.raises(CacheIntegrityError, match=match) as info:
        _stage(tmp_path, **kw)
    assert info.value.exit_code == 4


def test_sha256_mismatch_is_exit_4(tmp_path: Path, built: BuiltCache) -> None:
    cache = DataCache(tmp_path / "cache")
    a, b = built.complete_windows[0], built.complete_windows[1]
    plan = source.plan(opts(tmp_path / "cache", tmp_path / "stage", datasets="heatmaps"))
    cache.window_path(a).write_bytes(cache.window_path(b).read_bytes())
    with pytest.raises(CacheIntegrityError, match="sha256") as info:
        staging.stage(plan, tmp_path / "stage", writer=_writer())
    assert info.value.exit_code == 4


def _window_parts(path: Path) -> tuple[pa.Table, dict[bytes, bytes], pa.Table]:
    table = pq.read_table(path)
    md = dict(table.schema.metadata)
    axes = pa.ipc.open_stream(pa.BufferReader(md[b"fse.axes"])).read_all()
    return table, md, axes


def test_axis_values_length_mismatch_is_exit_4(tmp_path: Path, built: BuiltCache) -> None:
    key = built.complete_windows[0]
    path = DataCache(tmp_path / "cache").window_path(key)
    table, md, axes = _window_parts(path)
    short = axes.set_column(
        1,
        "strikes",
        pa.array([s[:-1] for s in axes["strikes"].to_pylist()], pa.list_(pa.float64())),
    )
    md[b"fse.axes"] = table_to_ipc(short)
    _rewrite(path, table.replace_schema_metadata(md), tmp_path / "cache/catalog.sqlite")
    _stage_fails(tmp_path, f"window SPX gamma {key.view_id}", datasets="heatmaps")


def test_unknown_axis_id_is_exit_4(tmp_path: Path, built: BuiltCache) -> None:
    path = DataCache(tmp_path / "cache").window_path(built.complete_windows[0])
    table, md, axes = _window_parts(path)
    md[b"fse.axes"] = table_to_ipc(axes.slice(0, 1))
    _rewrite(path, table.replace_schema_metadata(md), tmp_path / "cache/catalog.sqlite")
    _stage_fails(tmp_path, "unknown axis id", datasets="heatmaps")


def test_non_integer_storage_interval_is_exit_4(tmp_path: Path, built: BuiltCache) -> None:
    path = DataCache(tmp_path / "cache").window_path(built.complete_windows[0])
    table, md, _ = _window_parts(path)
    md[b"fse.storage_interval_s"] = b"ten"
    _rewrite(path, table.replace_schema_metadata(md), tmp_path / "cache/catalog.sqlite")
    _stage_fails(tmp_path, "fse.storage_interval_s", datasets="heatmaps")


def test_non_integer_incomplete_windows_is_exit_4(tmp_path: Path, built: BuiltCache) -> None:
    (path,) = sorted((tmp_path / "cache/derived/regime_medians").glob("*/*.parquet"))
    table = pq.read_table(path)
    md = dict(table.schema.metadata)
    md[b"fse.incomplete_windows"] = b"1.5"
    _rewrite(path, table.replace_schema_metadata(md))
    _stage_fails(tmp_path, "fse.incomplete_windows", datasets="regime_medians")


@pytest.mark.parametrize(
    ("dataset", "pick", "column"),
    [
        ("bars", lambda root: sorted((root / "bars/MES/60s").glob("*.parquet"))[0], "o"),
        ("vix_daily", lambda root: root / "vix/daily.parquet", "source"),
        ("darkpool", lambda root: root / "darkpool/QQQ/2025-10.parquet", "venue"),
        (
            "regime_medians",
            lambda root: sorted((root / "derived/regime_medians").glob("*/*.parquet"))[0],
            "usable",
        ),
    ],
)
def test_missing_required_column_is_exit_4(
    tmp_path: Path, built: BuiltCache, dataset: str, pick: Callable[[Path], Path], column: str
) -> None:
    path = pick(tmp_path / "cache")
    table = pq.read_table(path)
    dropped = table.drop_columns([column]).replace_schema_metadata(table.schema.metadata)
    _rewrite(path, dropped, tmp_path / "cache/catalog.sqlite")
    _stage_fails(tmp_path, "lacks columns", datasets=dataset)


def test_written_schemas_equal_the_registry(tmp_path: Path, built: BuiltCache) -> None:
    plan, _ = _stage(tmp_path)
    for pp in plan.partitions:
        for part in sorted((tmp_path / "stage").joinpath("data", pp.rel_path).glob("*.parquet")):
            assert pq.read_schema(part).remove_metadata().equals(TABLES[pp.table].schema), part


# ---------------------------------------------------------------- the row-count invariant


def _drop_last_row(original: transform.Transform) -> transform.Transform:
    def wrapped(pp: source.PartitionPlan) -> Iterator[Any]:
        tables = list(original(pp))
        last = tables.pop()
        yield from tables
        yield last.slice(0, last.num_rows - 1)

    return wrapped


@pytest.mark.parametrize(
    ("table", "dataset"),
    [
        ("heatmaps", "heatmaps"),
        ("bars", "bars"),
        ("vix_daily", "vix_daily"),
        ("darkpool", "darkpool"),
        ("darkpool_fetched_dates", "darkpool"),
        ("regime_medians", "regime_medians"),
    ],
)
def test_row_count_invariant(
    tmp_path: Path, built: BuiltCache, monkeypatch: pytest.MonkeyPatch, table: str, dataset: str
) -> None:
    stage_dir = tmp_path / "stage"
    monkeypatch.setitem(transform.TRANSFORMS, table, _drop_last_row(transform.TRANSFORMS[table]))
    plan = source.plan(opts(tmp_path / "cache", stage_dir, datasets=dataset))
    target = next(p for p in plan.partitions if p.table == table)
    with pytest.raises(ExportDataError) as info:
        staging.stage(plan, stage_dir, writer=_writer())
    message = str(info.value)
    assert info.value.exit_code == 4
    assert target.rel_path in message
    assert f"staged {target.source_rows - 1} rows" in message
    assert f"holds {target.source_rows}" in message
    manifest, warning = staging.load_manifest(stage_dir)
    assert warning is None
    assert target.rel_path not in manifest.partitions  # the manifest was not updated for it
    assert not staging_dir_of(stage_dir, target.rel_path).exists()
    assert not (stage_dir / ".work").joinpath(*target.rel_path.strip("/").split("/")).exists()


def staging_dir_of(stage_dir: Path, rel_path: str) -> Path:
    return stage_dir.joinpath("data", *rel_path.strip("/").split("/"))


def test_nan_payload_constant_is_a_nan() -> None:
    assert NAN_PAYLOAD != NAN_PAYLOAD
