"""Property: the heatmap export is bit-identical to the cache, window by window.

*For any* Cache_Window of generated Snapshots (the strategy of property 9:
special and subnormal floats, signed zeros, twin axes, shared ``asOf`` values,
null labels, any ``extra_json``), writing it to a Data_Cache and staging the
export gives one row per stored Snapshot, in stored order (``window_row``),
with every column equal to the source: float64 by bit pattern, lists by
lengths and flattened children, strikes and expirations equal to the axis
of the row.

Every cache root and staging directory is a fresh ``tempfile`` directory,
outside any git repository, created inside the test body.

**Validates: aws-export design §14 test 5**
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

import pyarrow.compute as pc
from hypothesis import given, settings

from fse.awsexport import source, staging
from fse.data.cache import DataCache
from fse.logio import LogWriter, Redactor
from tests.fakes.export_cache import (
    assert_column_exact,
    expected_window_columns,
    opts,
    read_staged,
)
from tests.property.test_p09_cache_round_trip import WindowCase, _window_cases


@settings(max_examples=60)
@given(case=_window_cases())
def test_heatmap_export_is_bit_identical(case: WindowCase) -> None:
    with tempfile.TemporaryDirectory(prefix="fse-awsexport-") as tmp:
        root, stage_dir = Path(tmp) / "cache", Path(tmp) / "stage"
        with DataCache(root) as cache:
            cache.write_window(case.key, case.snapshots, case.source_endpoint, view=case.view)
            path = cache.window_path(case.key)
        plan = source.plan(opts(root, stage_dir, datasets="heatmaps"))
        if not case.snapshots:  # a no_data window: nothing to export
            assert plan.partitions == ()
            assert dict(plan.skipped["heatmaps"]) == {"incomplete": 0, "no_data": 1}
            return
        (pp,) = plan.partitions
        assert pp.value_map == {
            "symbol": case.key.symbol,
            "metric": case.key.metric,
            "month": case.key.session.isoformat()[:7],
        }
        staging.stage(plan, stage_dir, writer=LogWriter(Redactor(()), stdout=io.StringIO()))
        out = read_staged(stage_dir, pp.rel_path)
        assert out.num_rows == len(case.snapshots)
        assert out["window_row"].to_pylist() == list(range(out.num_rows))
        assert pc.all(pc.equal(out["window_start_ns"], case.key.start_ns)).as_py()
        assert pc.all(pc.equal(out["window_source_endpoint"], case.source_endpoint)).as_py()
        for name, col in expected_window_columns(path).items():
            assert_column_exact(name, out[name], col)
