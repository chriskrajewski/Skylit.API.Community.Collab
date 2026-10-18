"""Unit tests for the ``levels`` Config_Schema section.

Design §8 "Level_Converter" and "Strategy_Config shape": defaults from the
sketch, the Req 8 ranges, strict types and unknown keys rejected. Inputs are
plain dicts, as a YAML safe loader produces them.

**Validates: Requirements 8.3, 8.6, 8.7, 8.10**
"""

from __future__ import annotations

from typing import Any, get_args

import pytest
import yaml
from pydantic import ValidationError

from fse.config.schema.levels import LevelMethod, LevelMethodsConfig, LevelsConfig
from fse.engine.types import CONVERSION_METHODS, ConversionMethod

DESIGN_LEVELS_YAML = """
methods: {SPY: ratio, NDX: ratio, NDXP: ratio}
es_half_width_pts: 5.0
qqq_half_width_usd: 0.50
max_price_gap_s: 120
"""


def error_types(value: dict[str, Any]) -> list[tuple[Any, ...]]:
    with pytest.raises(ValidationError) as info:
        LevelsConfig.model_validate(value)
    return [(e["loc"], e["type"]) for e in info.value.errors()]


def test_defaults_match_the_design_sketch() -> None:
    cfg = LevelsConfig()
    assert cfg == LevelsConfig.model_validate(yaml.safe_load(DESIGN_LEVELS_YAML))
    assert cfg.methods == LevelMethodsConfig(SPY="ratio", NDX="ratio", NDXP="ratio")
    assert (cfg.es_half_width_pts, cfg.qqq_half_width_usd, cfg.max_price_gap_s) == (5.0, 0.5, 120)


def test_methods_match_the_engine_conversion_methods() -> None:
    assert set(get_args(LevelMethod.__value__)) == CONVERSION_METHODS
    assert set(get_args(ConversionMethod.__value__)) == CONVERSION_METHODS


def test_configured_values_and_yaml_round_trip() -> None:
    cfg = LevelsConfig.model_validate(
        {
            "methods": {"SPY": "offset", "NDXP": "offset"},
            "es_half_width_pts": 2,  # a whole number is a valid float
            "qqq_half_width_usd": 2.5,
            "max_price_gap_s": 600,
        }
    )
    assert cfg.methods == LevelMethodsConfig(SPY="offset", NDX="ratio", NDXP="offset")
    assert cfg.es_half_width_pts == 2.0
    text = yaml.safe_dump(cfg.model_dump(mode="python"), sort_keys=False)
    assert LevelsConfig.model_validate(yaml.safe_load(text)) == cfg


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"es_half_width_pts": 0.2}, [(("es_half_width_pts",), "greater_than_equal")]),
        ({"es_half_width_pts": 25.25}, [(("es_half_width_pts",), "less_than_equal")]),
        ({"qqq_half_width_usd": 0.0}, [(("qqq_half_width_usd",), "greater_than_equal")]),
        ({"qqq_half_width_usd": 2.51}, [(("qqq_half_width_usd",), "less_than_equal")]),
        ({"max_price_gap_s": 59}, [(("max_price_gap_s",), "greater_than_equal")]),
        ({"max_price_gap_s": 601}, [(("max_price_gap_s",), "less_than_equal")]),
        ({"max_price_gap_s": 120.0}, [(("max_price_gap_s",), "int_type")]),
        ({"es_half_width_pts": "5"}, [(("es_half_width_pts",), "float_type")]),
        ({"es_half_width_pts": True}, [(("es_half_width_pts",), "float_type")]),
        ({"methods": {"SPY": "Ratio"}}, [(("methods", "SPY"), "literal_error")]),
        # SPX is always offset and QQQ always ratio (Req 8.1-8.2): no key for them.
        ({"methods": {"SPX": "ratio"}}, [(("methods", "SPX"), "extra_forbidden")]),
        ({"methods": {"QQQ": "offset"}}, [(("methods", "QQQ"), "extra_forbidden")]),
        ({"band_ticks": 4}, [(("band_ticks",), "extra_forbidden")]),
    ],
)
def test_out_of_range_wrong_type_and_unknown_keys_are_rejected(
    value: dict[str, Any], expected: list[tuple[Any, ...]]
) -> None:
    assert error_types(value) == expected


def test_section_is_frozen() -> None:
    cfg = LevelsConfig()
    with pytest.raises(ValidationError):
        cfg.max_price_gap_s = 60  # type: ignore[misc]
