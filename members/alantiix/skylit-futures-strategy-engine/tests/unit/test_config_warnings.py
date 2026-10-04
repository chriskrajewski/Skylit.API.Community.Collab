"""Unit tests for the Strategy_Config contradiction warnings (design §17 step 4).

One warning per enabled Fixed_R or Opposition_Or_Fixed_R R multiple below an
enabled min_reward_risk ``min``, naming both key paths and both values. The
property over generated configs is Property 52 (task 20.7).

**Validates: Requirements 17.6**
"""

from __future__ import annotations

from typing import Any

import pytest

from fse.config.schema import StrategyConfig
from fse.config.warnings import ConfigWarning, contradiction_warnings
from tests.fakes.configs import minimal_config_data


def config(
    *,
    exits: dict[str, Any] | None = None,
    min_rr: dict[str, Any] | None = None,
) -> StrategyConfig:
    data = minimal_config_data()
    if exits is not None:
        data["exits"] = exits
    if min_rr is not None:
        data["gates"] = {"min_reward_risk": min_rr}
    return StrategyConfig.model_validate(data)


def test_baseline_defaults_have_no_contradiction() -> None:
    assert contradiction_warnings(config()) == ()


def test_fixed_r_below_the_threshold_names_both_paths_and_values() -> None:
    cfg = config(
        exits={
            "global": {"mode": "fixed_r", "stop_rule": "one_node_beyond"},
            "modes": {"fixed_r": {"r_multiple": 2.0}},
        }
    )
    (warning,) = contradiction_warnings(cfg)
    assert warning == ConfigWarning(
        kind="contradiction",
        key_paths=("exits.modes.fixed_r.r_multiple", "gates.min_reward_risk.min"),
        values=(2.0, 3.0),
        message=warning.message,
    )
    assert "exits.modes.fixed_r.r_multiple = 2.0" in warning.message
    assert "gates.min_reward_risk.min = 3.0" in warning.message
    assert "selected by exits.global.mode" in warning.message
    assert str(warning).startswith("warning: contradiction: ")


def test_one_warning_per_r_multiple_with_per_regime_selections() -> None:
    cfg = config(
        exits={
            "per_regime": {
                "Whipsaw": {"mode": "opposition_or_fixed_r", "stop_rule": "one_node_beyond"},
                "Negative_Gamma": {"mode": "opposition_or_fixed_r", "stop_rule": "fixed_ticks"},
            },
            "modes": {
                "fixed_r": {"r_multiple": 1.5},
                "opposition_or_fixed_r": {"r_multiple": 2.5},
            },
        },
        min_rr={"min": 4.0},
    )
    warnings = contradiction_warnings(cfg)
    assert [(w.key_paths[0], w.values) for w in warnings] == [
        ("exits.modes.fixed_r.r_multiple", (1.5, 4.0)),
        ("exits.modes.opposition_or_fixed_r.r_multiple", (2.5, 4.0)),
    ]
    assert "exits.per_regime.Negative_Gamma.mode, exits.per_regime.Whipsaw.mode" in (
        warnings[1].message
    )
    assert "no exits.global or exits.per_regime setting selects it" in warnings[0].message


@pytest.mark.parametrize(
    ("exits", "min_rr"),
    [
        ({"modes": {"fixed_r": {"enabled": False, "r_multiple": 1.0}}}, None),
        ({"modes": {"fixed_r": {"r_multiple": 1.0}}}, {"enabled": False}),
        ({"modes": {"fixed_r": {"r_multiple": 3.0}}}, {"min": 3.0}),
        ({"modes": {"tp1_partial_be": {"enabled": True, "tp1": {"r_multiple": 0.5}}}}, None),
    ],
)
def test_no_warning_when_disabled_equal_or_another_mode(
    exits: dict[str, Any], min_rr: dict[str, Any] | None
) -> None:
    assert contradiction_warnings(config(exits=exits, min_rr=min_rr)) == ()
