"""Unit tests for the Experiment_Runner's shared session list (``fse.experiments.runner``).

A completed configuration that evaluated another session list than most of
the others is marked failed; an odd first configuration does not fail the
others (task 25 review, findings 3 and 4). Ranking and bootstrap settings
reach the result and the manifest.

**Validates: Requirements 19.15, 20.12, 20.14**
"""

from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path
from typing import Final

import pytest

from fse.analytics.metrics import MetricsCfg, summarize
from fse.analytics.montecarlo import PassEstimate
from fse.backtest.manifest import MANIFEST_FILE_NAME, DataRange
from fse.config.schema import StrategyConfig
from fse.experiments.runner import (
    ConfigResult,
    EvalTask,
    ExperimentConfig,
    ExperimentInputError,
    ExperimentResult,
    run_experiment,
)
from fse.logio import LogWriter, Redactor
from tests.fakes.configs import minimal_config_data
from tests.strategies.backtest_inputs import (
    CALENDAR,
    FAKE_SECRET,
    SESSIONS,
    SYMBOLS,
    bar_opens,
    build_session,
    write_cache,
    write_calendars,
)

PATHS: Final = 1_000
FIRST: Final[date] = SESSIONS[0]
LAST: Final[date] = SESSIONS[-1]


def evaluator(task: EvalTask, writer: LogWriter) -> ConfigResult:
    """Every session of the range, minus the first one for a config named ``odd*``."""
    sessions = CALENDAR.sessions(task.data_range.start, task.data_range.end)
    if task.name.startswith("odd"):
        sessions = sessions[1:]
    estimate = PassEstimate(
        seed=task.seed, paths=PATHS, max_days=60, min_sessions=40, sessions=len(sessions),
        truncated_by_run_account=0, passed=task.index, failed=0, unresolved=PATHS - task.index,
        median_days_to_pass=None, p90_days_to_pass=None,
    )  # fmt: skip
    return ConfigResult(
        run_id=f"fake-{task.index}",
        sessions=sessions,
        skipped=(),
        metrics=summarize([], sessions, MetricsCfg()),
        pass_estimate=estimate,
    )


@pytest.fixture(scope="module")
def dirs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("runner")
    sessions = [build_session(d, 7000 + d.day, bar_opens(d, 12, None)) for d in SESSIONS]
    return write_cache(root / "cache", sessions), write_calendars(root / "calendars")


def base() -> StrategyConfig:
    data = minimal_config_data()
    data["data"] = {"symbols": list(SYMBOLS), "nq_sources": ["QQQ"]}
    data["experiments"] = {"holdout_fraction": 0.05}
    return StrategyConfig.model_validate(data)


def run(dirs: tuple[Path, Path], out: Path, names: list[str], **kwargs: object) -> ExperimentResult:
    cfg = base()
    configs = [
        ExperimentConfig(
            n,
            cfg.model_copy(
                update={"orders": cfg.orders.model_copy(update={"max_open": 1 + i % 3})}
            ),
        )
        for i, n in enumerate(names)
    ]
    writer = LogWriter(Redactor([FAKE_SECRET]), stdout=io.StringIO(), stderr=io.StringIO())
    return run_experiment(
        "test", cfg, configs, DataRange(FIRST, LAST), cache_dir=dirs[0], calendar_dir=dirs[1],
        out_dir=out, writer=writer, seed=7, evaluator=evaluator, code_version="test-version",
        **kwargs,  # type: ignore[arg-type]
    )  # fmt: skip


def test_odd_first_config_fails_alone(dirs: tuple[Path, Path], tmp_path: Path) -> None:
    result = run(dirs, tmp_path / "x", ["odd", "a", "b"])
    statuses = [(o.name, o.status) for o in result.outcomes]
    assert statuses == [("odd", "failed"), ("a", "completed"), ("b", "completed")]
    odd = result.outcomes[0]
    assert odd.error is not None
    assert "not the" in odd.error
    completed = result.outcomes[1].result
    assert completed is not None
    assert result.compared_sessions == completed.sessions


def test_tie_keeps_definition_order(dirs: tuple[Path, Path], tmp_path: Path) -> None:
    result = run(dirs, tmp_path / "x", ["odd", "a"])
    statuses = [o.status for o in result.outcomes]
    assert statuses == ["completed", "failed"]


def test_ranking_and_bootstrap_settings(dirs: tuple[Path, Path], tmp_path: Path) -> None:
    result = run(
        dirs,
        tmp_path / "x",
        ["a", "b", "c"],
        ranking_objective="combine_pass_probability",
        bootstrap_resamples=1_000,
    )
    # Pass counts equal the index, so the last config ranks first.
    assert result.ranking == (2, 1, 0)
    comparison = json.loads((tmp_path / "x" / "comparison.json").read_text())
    assert comparison["ranking"] == {
        "objective": "combine_pass_probability",
        "order": ["c", "b", "a"],
    }
    manifest = json.loads((tmp_path / "x" / MANIFEST_FILE_NAME).read_text())
    assert manifest["status"] == "completed"


@pytest.mark.parametrize(
    ("kwargs", "text"),
    [
        ({"ranking_objective": "win_rate"}, "'win_rate'"),
        ({"bootstrap_resamples": 999}, "999"),
        ({"bootstrap_resamples": 100_001}, "100001"),
    ],
)
def test_bad_settings_write_nothing(
    dirs: tuple[Path, Path], tmp_path: Path, kwargs: dict[str, object], text: str
) -> None:
    with pytest.raises(ExperimentInputError, match=text):
        run(dirs, tmp_path / "x", ["a", "b"], **kwargs)
    assert not (tmp_path / "x").exists()
