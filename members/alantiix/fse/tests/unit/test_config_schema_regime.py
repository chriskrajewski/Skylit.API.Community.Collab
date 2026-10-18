"""Unit tests for the ``regime`` Config_Schema section.

Design "Strategy_Config shape", D6 and §7: strict types, unknown keys
rejected, frozen, defaults from the design sketch, and ``min_abs_value``
required with no default. Inputs are plain dicts, as a YAML safe loader
produces them.

**Validates: Requirements 7.2, 7.3, 7.4, 7.5, 7.8, 7.9**
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from fse.config.schema.regime import GradeConfig, RegimeConfig, VixConditionConfig

DESIGN_REGIME_YAML = """
{regime_distance_pct: 1.0, vanna_multiple: 2.0, min_abs_value: 250000000,
 whipsaw_pct: 15.0, vix_condition: {enabled: true, pct: 5.0},
 grade: {major_fraction: 0.50, floor_ceiling_ratio: 1.5}}
"""


def errors(value: dict[str, Any]) -> list[tuple[Any, ...]]:
    with pytest.raises(ValidationError) as info:
        RegimeConfig.model_validate(value)
    return [(e["loc"], e["type"]) for e in info.value.errors()]


def test_min_abs_value_is_required() -> None:
    assert errors({}) == [(("min_abs_value",), "missing")]


def test_defaults_match_the_design_sketch() -> None:
    cfg = RegimeConfig.model_validate({"min_abs_value": 250_000_000})
    assert cfg == RegimeConfig.model_validate(yaml.safe_load(DESIGN_REGIME_YAML))
    assert (cfg.regime_distance_pct, cfg.vanna_multiple, cfg.whipsaw_pct) == (1.0, 2.0, 15.0)
    assert cfg.min_abs_value == 250_000_000
    assert cfg.vix_condition == VixConditionConfig(enabled=True, pct=5.0, intraday_source=False)
    assert cfg.grade == GradeConfig(major_fraction=0.50, floor_ceiling_ratio=1.5)


def test_dump_is_plain_yaml_and_round_trips() -> None:
    cfg = RegimeConfig.model_validate(
        {
            "regime_distance_pct": 100,
            "vanna_multiple": 0.5,
            "min_abs_value": 1.5e9,
            "whipsaw_pct": 0.1,
            "vix_condition": {"enabled": False, "pct": 0, "intraday_source": True},
            "grade": {"major_fraction": 1, "floor_ceiling_ratio": 1},
        }
    )
    text = yaml.safe_dump(cfg.model_dump(mode="python"), sort_keys=False)
    assert RegimeConfig.model_validate(yaml.safe_load(text)) == cfg
    assert list(cfg.model_dump()) == [
        "regime_distance_pct",
        "vanna_multiple",
        "min_abs_value",
        "whipsaw_pct",
        "vix_condition",
        "grade",
    ]


def test_model_is_frozen_and_hashable() -> None:
    cfg = RegimeConfig(min_abs_value=1.0)
    assert hash(cfg) == hash(RegimeConfig(min_abs_value=1.0))
    with pytest.raises(ValidationError):
        cfg.min_abs_value = 2.0  # type: ignore[misc]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"min_abs_value": 0}, [(("min_abs_value",), "greater_than")]),
        ({"min_abs_value": float("inf")}, [(("min_abs_value",), "finite_number")]),
        ({"min_abs_value": "1e9"}, [(("min_abs_value",), "float_type")]),
        ({"min_abs_value": None}, [(("min_abs_value",), "float_type")]),
        ({"regime_distance_pct": 0}, [(("regime_distance_pct",), "greater_than")]),
        ({"regime_distance_pct": 100.5}, [(("regime_distance_pct",), "less_than_equal")]),
        ({"vanna_multiple": 0.0}, [(("vanna_multiple",), "greater_than")]),
        ({"vanna_multiple": float("nan")}, [(("vanna_multiple",), "finite_number")]),
        ({"whipsaw_pct": 0}, [(("whipsaw_pct",), "greater_than")]),
        ({"whipsaw_pct": True}, [(("whipsaw_pct",), "float_type")]),
        ({"vix_condition": {"pct": -0.1}}, [(("vix_condition", "pct"), "greater_than_equal")]),
        ({"vix_condition": {"enabled": "on"}}, [(("vix_condition", "enabled"), "bool_type")]),
        (
            {"vix_condition": {"source": "atlas"}},
            [(("vix_condition", "source"), "extra_forbidden")],
        ),
        ({"grade": {"major_fraction": 0.0}}, [(("grade", "major_fraction"), "greater_than_equal")]),
        ({"grade": {"major_fraction": 1.5}}, [(("grade", "major_fraction"), "less_than_equal")]),
        (
            {"grade": {"floor_ceiling_ratio": 0.9}},
            [(("grade", "floor_ceiling_ratio"), "greater_than_equal")],
        ),
        ({"regime_dist": 1.0}, [(("regime_dist",), "extra_forbidden")]),
    ],
)
def test_invalid_values_name_the_key_path(
    value: dict[str, Any], expected: list[tuple[Any, ...]]
) -> None:
    assert errors({"min_abs_value": 1.0, **value}) == expected
