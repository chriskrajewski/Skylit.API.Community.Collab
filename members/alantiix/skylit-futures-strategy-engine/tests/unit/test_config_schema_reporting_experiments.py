"""Unit tests for the ``reporting`` and ``experiments`` Config_Schema sections (design §19-22).

Defaults from the design sketch, the Req 19-22 ranges, the shared bootstrap
resample type, and constants kept equal to the analytics and experiments
modules.

**Validates: Requirements 17.1, 17.2**
"""

from __future__ import annotations

from typing import Any, get_args

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from fse.analytics.metrics import MIN_SAMPLE_TRADES_DEFAULT, PrimaryWinRate
from fse.config.schema.bootstrap import BOOTSTRAP_RESAMPLES_MAX, BOOTSTRAP_RESAMPLES_MIN
from fse.config.schema.experiments import (
    HOLDOUT_FRACTION_DEFAULT,
    HOLDOUT_FRACTION_MAX,
    HOLDOUT_FRACTION_MIN,
    ExperimentsConfig,
)
from fse.config.schema.reporting import WIN_RATE_DEFINITIONS, ReportingConfig, WinRateDefinition
from fse.experiments import holdout

DESIGN_REPORTING_YAML = """
primary_win_rate: a
scratch_tolerance_r: 0.1
min_sample_trades: 30
bootstrap_resamples: 5000
reference_win_rate: 80.0
shadow_min_sample: 30
"""

DESIGN_EXPERIMENTS_YAML = """
holdout_fraction: 0.20
ranking_objective: combine_pass_probability
walkforward: {train: 60, test: 20, objective: expectancy, min_trades: 30}
montecarlo: {paths: 10000, max_days: 60, min_sessions: 40}
"""


def error_types(model: type[BaseModel], value: dict[str, Any]) -> list[tuple[Any, ...]]:
    with pytest.raises(ValidationError) as info:
        model.model_validate(value)
    return [(e["loc"], e["type"]) for e in info.value.errors()]


def test_defaults_match_the_design_sketch() -> None:
    reporting = ReportingConfig.model_validate(yaml.safe_load(DESIGN_REPORTING_YAML))
    assert reporting == ReportingConfig()
    experiments = ExperimentsConfig.model_validate(yaml.safe_load(DESIGN_EXPERIMENTS_YAML))
    assert experiments == ExperimentsConfig()
    assert reporting.min_sample_trades == MIN_SAMPLE_TRADES_DEFAULT


def test_constants_match_analytics_and_experiments() -> None:
    assert get_args(WinRateDefinition.__value__) == get_args(PrimaryWinRate.__value__)
    assert get_args(WinRateDefinition.__value__) == WIN_RATE_DEFINITIONS
    assert (HOLDOUT_FRACTION_DEFAULT, HOLDOUT_FRACTION_MIN, HOLDOUT_FRACTION_MAX) == (
        holdout.HOLDOUT_FRACTION_DEFAULT,
        holdout.HOLDOUT_FRACTION_MIN,
        holdout.HOLDOUT_FRACTION_MAX,
    )


@pytest.mark.parametrize("count", [BOOTSTRAP_RESAMPLES_MIN, BOOTSTRAP_RESAMPLES_MAX])
def test_bootstrap_resamples_bounds_are_inclusive(count: int) -> None:
    assert ReportingConfig(bootstrap_resamples=count).bootstrap_resamples == count


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"primary_win_rate": "d"}, [(("primary_win_rate",), "literal_error")]),
        ({"scratch_tolerance_r": -0.1}, [(("scratch_tolerance_r",), "greater_than_equal")]),
        ({"min_sample_trades": 0}, [(("min_sample_trades",), "greater_than_equal")]),
        (
            {"bootstrap_resamples": BOOTSTRAP_RESAMPLES_MIN - 1},
            [(("bootstrap_resamples",), "greater_than_equal")],
        ),
        (
            {"bootstrap_resamples": BOOTSTRAP_RESAMPLES_MAX + 1},
            [(("bootstrap_resamples",), "less_than_equal")],
        ),
        ({"reference_win_rate": 0}, [(("reference_win_rate",), "greater_than")]),
        ({"reference_win_rate": 100.5}, [(("reference_win_rate",), "less_than_equal")]),
        ({"shadow_min_sample": 1.5}, [(("shadow_min_sample",), "int_type")]),
    ],
)
def test_reporting_rejects(value: dict[str, Any], expected: list[tuple[Any, ...]]) -> None:
    assert error_types(ReportingConfig, value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"holdout_fraction": 0.04}, [(("holdout_fraction",), "greater_than_equal")]),
        ({"holdout_fraction": 0.51}, [(("holdout_fraction",), "less_than_equal")]),
        ({"ranking_objective": "sharpe"}, [(("ranking_objective",), "literal_error")]),
        ({"walkforward": {"train": 9}}, [(("walkforward", "train"), "greater_than_equal")]),
        ({"walkforward": {"test": 251}}, [(("walkforward", "test"), "less_than_equal")]),
        (
            {"walkforward": {"min_trades": 1001}},
            [(("walkforward", "min_trades"), "less_than_equal")],
        ),
        ({"montecarlo": {"paths": 999}}, [(("montecarlo", "paths"), "greater_than_equal")]),
        ({"montecarlo": {"max_days": 251}}, [(("montecarlo", "max_days"), "less_than_equal")]),
        (
            {"montecarlo": {"min_sessions": 0}},
            [(("montecarlo", "min_sessions"), "greater_than_equal")],
        ),
        ({"cadences": [5, 60]}, [(("cadences",), "extra_forbidden")]),
    ],
)
def test_experiments_rejects(value: dict[str, Any], expected: list[tuple[Any, ...]]) -> None:
    assert error_types(ExperimentsConfig, value) == expected
