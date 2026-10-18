"""Unit tests for the bootstrap intervals of a Backtester run (task 26.1, review fix).

The pinned :func:`~tests.strategies.backtest_inputs.funnel_case` run is
backtested twice with the same seed:

- the Run_Manifest records the run's seed and ``reporting.bootstrap_resamples``
  as its ``bootstrap`` entry, and ``report_inputs.json`` holds the bounds that
  :func:`bootstrap_intervals` gives for the run's trades with that seed
  (Req 20.12);
- the rerun gives identical bounds and an identical manifest entry;
- ``fse report`` shows both 95% intervals in the Markdown, low-sample
  labelled when the run has fewer trades than the minimum (Req 20.13), and
  the same values in ``report.json``.

**Validates: Requirements 20.12, 20.13**
"""

from __future__ import annotations

import io
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from fse import cli
from fse.analytics.bootstrap import Interval, bootstrap_intervals
from fse.backtest.manifest import MANIFEST_FILE_NAME
from fse.backtest.runner import (
    REPORT_INPUTS_FILE_NAME,
    BacktestResult,
    intervals_to_jsonable,
    metrics_cfg,
)
from fse.commands.report import REPORT_DIR_NAME, REPORT_JSON_FILE_NAME, REPORT_MD_FILE_NAME
from fse.config.schema import StrategyConfig
from fse.reports.markdown import LOW_SAMPLE_LABEL
from fse.reports.tables import load_run
from tests.strategies.backtest_inputs import SEED, funnel_case, run, write_cache, write_calendars


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def runs(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[StrategyConfig, BacktestResult, BacktestResult]:
    """The funnel_case config and two runs of it with the same seed."""
    root = tmp_path_factory.mktemp("bootstrap")
    market, cfg = funnel_case()
    cache = write_cache(root / "cache", market)
    calendars = write_calendars(root / "calendars")
    first = run(cache, calendars, cfg, market.data_range, root / "runs" / "first")
    again = run(cache, calendars, cfg, market.data_range, root / "runs" / "again")
    return cfg, first, again


def test_the_manifest_records_the_seed_and_resamples(
    runs: tuple[StrategyConfig, BacktestResult, BacktestResult],
) -> None:
    cfg, first, _ = runs
    manifest = read_json(first.run_dir / MANIFEST_FILE_NAME)
    resamples = cfg.reporting.bootstrap_resamples
    assert manifest["seed"] == SEED
    assert manifest["bootstrap"] == {"seed": SEED, "resamples": resamples}
    assert (first.intervals.seed, first.intervals.resamples) == (SEED, resamples)


def test_the_stored_bounds_are_the_seeded_intervals_of_the_trades(
    runs: tuple[StrategyConfig, BacktestResult, BacktestResult],
) -> None:
    cfg, first, _ = runs
    assert any(not t.shadow for t in first.trades)  # the intervals are numbers
    expected = bootstrap_intervals(
        load_run(first.run_dir).trades,  # trades.json read back
        metrics_cfg(cfg),
        seed=SEED,
        resamples=cfg.reporting.bootstrap_resamples,
    )
    assert expected == first.intervals
    assert isinstance(expected.expectancy_r, Interval)
    stored = read_json(first.run_dir / REPORT_INPUTS_FILE_NAME)["bootstrap"]
    assert stored == intervals_to_jsonable(expected)


def test_a_rerun_with_the_same_seed_gives_identical_bounds(
    runs: tuple[StrategyConfig, BacktestResult, BacktestResult],
) -> None:
    _, first, again = runs
    assert again.intervals == first.intervals
    for run_dir in (first.run_dir, again.run_dir):
        assert read_json(run_dir / MANIFEST_FILE_NAME)["bootstrap"] == {
            "seed": SEED,
            "resamples": first.intervals.resamples,
        }
    assert (
        read_json(again.run_dir / REPORT_INPUTS_FILE_NAME)["bootstrap"]
        == read_json(first.run_dir / REPORT_INPUTS_FILE_NAME)["bootstrap"]
    )


def bounds(interval: object, suffix: str) -> str:
    assert isinstance(interval, Interval)
    lo, hi = (Decimal(repr(v)).quantize(Decimal("0.01")) for v in (interval.lower, interval.upper))
    return f"{lo}{suffix} to {hi}{suffix}"


def test_the_report_shows_both_intervals(
    runs: tuple[StrategyConfig, BacktestResult, BacktestResult],
) -> None:
    cfg, first, _ = runs
    out, err = io.StringIO(), io.StringIO()
    code = cli.run(
        ["report", "--run", "first", "--runs-dir", str(first.run_dir.parent)],
        project_dir=None,
        environ={},
        stdout=out,
        stderr=err,
    )
    assert (code, err.getvalue()) == (0, "")
    folder = first.run_dir / REPORT_DIR_NAME
    b = first.intervals
    assert b.low_sample is (b.trade_count < cfg.reporting.min_sample_trades)
    label = f" {LOW_SAMPLE_LABEL}" if b.low_sample else ""

    lines = (folder / REPORT_MD_FILE_NAME).read_text(encoding="utf-8").splitlines()
    start = lines.index("## Confidence intervals")
    assert start > lines.index("## Metrics")
    section = lines[start : start + 8]
    assert (
        f"95% percentile bootstrap intervals: {b.resamples:,} resamples of the "
        f"{b.trade_count} accepted trade(s), drawn with replacement; seed {SEED}."
    ) in section
    assert (
        f"| Primary_Win_Rate ({b.primary_win_rate}) | "
        f"{bounds(b.primary_win_rate_pct, '%')}{label} |"
    ) in section
    assert f"| Expectancy (R) | {bounds(b.expectancy_r, ' R')}{label} |" in section

    report = read_json(folder / REPORT_JSON_FILE_NAME)
    stored = read_json(first.run_dir / REPORT_INPUTS_FILE_NAME)["bootstrap"]
    assert report["bootstrap_intervals"] == stored
