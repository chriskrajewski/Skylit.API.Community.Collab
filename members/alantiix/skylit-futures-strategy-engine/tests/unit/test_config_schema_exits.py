"""Unit tests for the ``exits`` Config_Schema section.

Design §12 and "Strategy_Config shape": defaults from the sketch, the Req 12
ranges, the ``global`` alias, per-Regime fallback, strict types and unknown
keys rejected. Inputs are plain dicts, as a YAML safe loader produces.

**Validates: Requirements 12.1, 12.2, 12.4, 12.6, 12.7, 12.9, 12.11, 12.13**
"""

from __future__ import annotations

from typing import Any, get_args

import pytest
import yaml
from pydantic import ValidationError

from fse.config.schema.exits import (
    EXIT_MODES,
    EXIT_REGIMES,
    EXIT_STOP_RULES,
    TARGET_RULES,
    TRAILING_MODES,
    ExitModeName,
    ExitsConfig,
    ExitSetting,
    ExitStopRule,
    GlobalExitSetting,
    PerRegimeExits,
    TargetRule,
    TrailingMode,
)
from fse.engine.types import REGIMES, STOP_RULES, Regime, StopRule

DESIGN_EXITS_YAML = """
global: {mode: opposition_or_fixed_r, stop_rule: one_node_beyond}
per_regime: {Positive_Gamma: {mode: next_node, stop_rule: one_node_beyond}}
modes:
  fixed_r: {enabled: true, r_multiple: 3.0}
  next_node: {enabled: true}
  tp1_partial_be: {enabled: false, tp1: {rule: r, r_multiple: 1.5}, tp2: {rule: node},
                   tp1_fraction: 0.5}
  opposition_or_fixed_r: {enabled: true, r_multiple: 3.0, fraction: 0.85}
  trailing: {enabled: false, mode: node_to_node, ticks: 40}
breakeven: {enabled: true, trigger_r: 1.0, offset_ticks: 1}
"""


def error_types(value: dict[str, Any]) -> list[tuple[Any, ...]]:
    with pytest.raises(ValidationError) as info:
        ExitsConfig.model_validate(value)
    return [(e["loc"], e["type"]) for e in info.value.errors()]


def test_defaults_match_the_design_sketch() -> None:
    cfg = ExitsConfig()
    assert cfg == ExitsConfig.model_validate(yaml.safe_load(DESIGN_EXITS_YAML))
    assert cfg.global_ == GlobalExitSetting(
        mode="opposition_or_fixed_r", stop_rule="one_node_beyond"
    )
    assert cfg.per_regime.Positive_Gamma == ExitSetting(
        mode="next_node", stop_rule="one_node_beyond"
    )
    assert [cfg.per_regime.get(r) for r in EXIT_REGIMES[1:]] == [None] * 4
    m = cfg.modes
    assert (m.fixed_r.enabled, m.fixed_r.r_multiple) == (True, 3.0)
    assert m.next_node.enabled
    tp = m.tp1_partial_be
    assert (tp.enabled, tp.tp1.rule, tp.tp1.r_multiple, tp.tp2.rule) == (False, "r", 1.5, "node")
    assert (tp.tp2.r_multiple, tp.tp1_fraction) == (3.0, 0.5)
    o = m.opposition_or_fixed_r
    assert (o.enabled, o.r_multiple, o.fraction) == (True, 3.0, 0.85)
    assert (m.trailing.enabled, m.trailing.mode, m.trailing.ticks) == (False, "node_to_node", 40)
    b = cfg.breakeven
    assert (b.enabled, b.trigger_r, b.offset_ticks) == (True, 1.0, 1)


def test_literals_match_their_constants_and_the_engine_types() -> None:
    assert get_args(ExitModeName.__value__) == EXIT_MODES
    assert set(get_args(ExitStopRule.__value__)) == EXIT_STOP_RULES == STOP_RULES
    assert set(get_args(StopRule.__value__)) == EXIT_STOP_RULES
    assert set(get_args(TargetRule.__value__)) == TARGET_RULES
    assert set(get_args(TrailingMode.__value__)) == TRAILING_MODES
    assert get_args(Regime.__value__) == EXIT_REGIMES
    assert set(EXIT_REGIMES) == REGIMES
    assert tuple(PerRegimeExits.model_fields) == EXIT_REGIMES


def test_configured_values_and_yaml_round_trip() -> None:
    cfg = ExitsConfig.model_validate(
        {
            "global": {"mode": "fixed_r"},  # stop_rule takes its default
            "per_regime": {
                "Positive_Gamma": None,
                "Whipsaw": {"mode": "tp1_partial_be", "stop_rule": "fixed_ticks"},
            },
            "modes": {
                "fixed_r": {"r_multiple": 2},  # a whole float is valid
                "tp1_partial_be": {
                    "enabled": True,
                    "tp1": {"rule": "node"},
                    "tp2": {"rule": "r", "r_multiple": 4.5},
                    "tp1_fraction": 0.9,
                },
                "trailing": {"mode": "fixed_ticks", "ticks": 400},
            },
            "breakeven": {"enabled": False, "trigger_r": 0.25, "offset_ticks": 0},
        }
    )
    assert cfg.global_ == GlobalExitSetting(mode="fixed_r", stop_rule="one_node_beyond")
    assert cfg.per_regime.Positive_Gamma is None
    assert cfg.modes.fixed_r.r_multiple == 2.0
    assert cfg.modes.tp1_partial_be.tp1.r_multiple == 1.5
    dumped = cfg.model_dump(mode="python")
    assert "global" in dumped
    assert "global_" not in dumped
    text = yaml.safe_dump(dumped, sort_keys=False)
    assert ExitsConfig.model_validate(yaml.safe_load(text)) == cfg


def test_setting_for_uses_the_regime_setting_else_global() -> None:
    cfg = ExitsConfig.model_validate(
        {"per_regime": {"Whipsaw": {"mode": "fixed_r", "stop_rule": "fixed_ticks"}}}
    )
    assert cfg.setting_for("Positive_Gamma") == ExitSetting(
        mode="next_node", stop_rule="one_node_beyond"
    )
    assert cfg.setting_for("Whipsaw") == ExitSetting(mode="fixed_r", stop_rule="fixed_ticks")
    for regime in ("Negative_Gamma", "Vanna_Dominant", "Structureless", None):
        assert cfg.setting_for(regime) is cfg.global_
    with pytest.raises(ValueError, match="Gamma_Flip"):
        cfg.setting_for("Gamma_Flip")


def test_enabled_reads_each_mode_flag() -> None:
    modes = ExitsConfig().modes
    assert [modes.enabled(m) for m in EXIT_MODES] == [True, True, False, True, False]
    with pytest.raises(ValueError, match="scalp"):
        modes.enabled("scalp")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"global": {"mode": "scalp"}}, [(("global", "mode"), "literal_error")]),
        ({"global": {"stop_rule": "atr"}}, [(("global", "stop_rule"), "literal_error")]),
        ({"global_": {"mode": "fixed_r"}}, [(("global_",), "extra_forbidden")]),
        (
            {"per_regime": {"Whipsaw": {"mode": "fixed_r"}}},
            [(("per_regime", "Whipsaw", "stop_rule"), "missing")],
        ),
        (
            {"per_regime": {"Gamma_Flip": {"mode": "fixed_r", "stop_rule": "fixed_ticks"}}},
            [(("per_regime", "Gamma_Flip"), "extra_forbidden")],
        ),
        (
            {"modes": {"fixed_r": {"r_multiple": 0.49}}},
            [(("modes", "fixed_r", "r_multiple"), "greater_than_equal")],
        ),
        (
            {"modes": {"fixed_r": {"r_multiple": 10.01}}},
            [(("modes", "fixed_r", "r_multiple"), "less_than_equal")],
        ),
        (
            {"modes": {"fixed_r": {"r_multiple": "3"}}},
            [(("modes", "fixed_r", "r_multiple"), "float_type")],
        ),
        (
            {"modes": {"next_node": {"r_multiple": 3.0}}},
            [(("modes", "next_node", "r_multiple"), "extra_forbidden")],
        ),
        (
            {"modes": {"tp1_partial_be": {"tp1": {"rule": "atr"}}}},
            [(("modes", "tp1_partial_be", "tp1", "rule"), "literal_error")],
        ),
        (
            {"modes": {"tp1_partial_be": {"tp2": {"r_multiple": 11}}}},
            [(("modes", "tp1_partial_be", "tp2", "r_multiple"), "less_than_equal")],
        ),
        (
            {"modes": {"tp1_partial_be": {"tp1_fraction": 0.05}}},
            [(("modes", "tp1_partial_be", "tp1_fraction"), "greater_than_equal")],
        ),
        (
            {"modes": {"tp1_partial_be": {"tp1_fraction": 0.95}}},
            [(("modes", "tp1_partial_be", "tp1_fraction"), "less_than_equal")],
        ),
        (
            {"modes": {"opposition_or_fixed_r": {"fraction": 0}}},
            [(("modes", "opposition_or_fixed_r", "fraction"), "greater_than_equal")],
        ),
        (
            {"modes": {"opposition_or_fixed_r": {"fraction": 1.5}}},
            [(("modes", "opposition_or_fixed_r", "fraction"), "less_than_equal")],
        ),
        (
            {"modes": {"trailing": {"ticks": 0}}},
            [(("modes", "trailing", "ticks"), "greater_than_equal")],
        ),
        (
            {"modes": {"trailing": {"ticks": 401}}},
            [(("modes", "trailing", "ticks"), "less_than_equal")],
        ),
        ({"modes": {"trailing": {"ticks": 40.0}}}, [(("modes", "trailing", "ticks"), "int_type")]),
        (
            {"modes": {"trailing": {"mode": "atr"}}},
            [(("modes", "trailing", "mode"), "literal_error")],
        ),
        (
            {"modes": {"trailing": {"enabled": "no"}}},
            [(("modes", "trailing", "enabled"), "bool_type")],
        ),
        ({"breakeven": {"trigger_r": 0.2}}, [(("breakeven", "trigger_r"), "greater_than_equal")]),
        (
            {"breakeven": {"trigger_r": float("nan")}},
            [(("breakeven", "trigger_r"), "finite_number")],
        ),
        (
            {"modes": {"fixed_r": {"r_multiple": float("inf")}}},
            [(("modes", "fixed_r", "r_multiple"), "finite_number")],
        ),
        (
            {"breakeven": {"offset_ticks": -1}},
            [(("breakeven", "offset_ticks"), "greater_than_equal")],
        ),
        ({"breakeven": {"offset_ticks": 21}}, [(("breakeven", "offset_ticks"), "less_than_equal")]),
        ({"modes": {"scalp": {"enabled": True}}}, [(("modes", "scalp"), "extra_forbidden")]),
        ({"max_age_min": 30}, [(("max_age_min",), "extra_forbidden")]),
    ],
)
def test_out_of_range_wrong_type_and_unknown_keys_are_rejected(
    value: dict[str, Any], expected: list[tuple[Any, ...]]
) -> None:
    assert error_types(value) == expected


def test_section_is_frozen() -> None:
    cfg = ExitsConfig()
    with pytest.raises(ValidationError):
        cfg.breakeven = cfg.breakeven  # type: ignore[misc]
