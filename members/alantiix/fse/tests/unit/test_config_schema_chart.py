"""Unit tests for the ``chart`` Config_Schema section (design §9, "Strategy_Config shape").

**Validates: Requirements 9.7, 9.8, 9.9, 9.10, 9.14, 9.15, 9.16, 9.17**
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from fse.config.schema.chart import CHART_TIMEFRAMES_S, ChartConfig

DESIGN_CHART_YAML = """
pivot_len: {"60": 3, "240": 3}
swings_kept: 5
bos_pivot_len: {"1": 3, "5": 3}
sweep_ticks: 2
candle_timeframe_s: 60
sweep_timeframe_s: 60
"""


def errors(value: dict[str, Any]) -> list[tuple[Any, ...]]:
    with pytest.raises(ValidationError) as info:
        ChartConfig.model_validate(value)
    return [(e["loc"], e["type"]) for e in info.value.errors()]


def test_defaults_match_the_design_sketch() -> None:
    assert ChartConfig() == ChartConfig.model_validate(yaml.safe_load(DESIGN_CHART_YAML))
    assert ChartConfig().model_dump() == yaml.safe_load(DESIGN_CHART_YAML)


def test_dump_uses_minute_keys_and_round_trips() -> None:
    cfg = ChartConfig.model_validate(
        {
            "pivot_len": {"240": 5},
            "bos_pivot_len": {"1": 2},
            "swings_kept": 50,
            "sweep_ticks": 20,
            "candle_timeframe_s": 300,
            "sweep_timeframe_s": 14400,
        }
    )
    dumped = cfg.model_dump()
    assert dumped["pivot_len"] == {"60": 3, "240": 5}
    assert dumped["bos_pivot_len"] == {"1": 2, "5": 3}
    assert ChartConfig.model_validate(yaml.safe_load(yaml.safe_dump(dumped))) == cfg


def test_pivot_len_for_each_timeframe() -> None:
    cfg = ChartConfig.model_validate(
        {"pivot_len": {"60": 4, "240": 5}, "bos_pivot_len": {"1": 1, "5": 2}}
    )
    assert [cfg.pivot_len_for(tf) for tf in CHART_TIMEFRAMES_S] == [1, 2, 4, 5]
    with pytest.raises(ValueError, match="not one of the chart timeframes"):
        cfg.pivot_len_for(120)


def test_model_is_frozen() -> None:
    with pytest.raises(ValidationError):
        ChartConfig().sweep_ticks = 3  # type: ignore[misc]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"pivot_len": {"60": 0}}, [(("pivot_len", "60"), "greater_than_equal")]),
        ({"pivot_len": {"240": 21}}, [(("pivot_len", "240"), "less_than_equal")]),
        ({"pivot_len": {"one_hour": 3}}, [(("pivot_len", "one_hour"), "extra_forbidden")]),
        ({"pivot_len": {"15": 3}}, [(("pivot_len", "15"), "extra_forbidden")]),
        ({"bos_pivot_len": {"5": 3.0}}, [(("bos_pivot_len", "5"), "int_type")]),
        ({"swings_kept": 0}, [(("swings_kept",), "greater_than_equal")]),
        ({"swings_kept": 51}, [(("swings_kept",), "less_than_equal")]),
        ({"sweep_ticks": 0}, [(("sweep_ticks",), "greater_than_equal")]),
        ({"sweep_ticks": 21}, [(("sweep_ticks",), "less_than_equal")]),
        ({"sweep_ticks": True}, [(("sweep_ticks",), "int_type")]),
        ({"candle_timeframe_s": 120}, [(("candle_timeframe_s",), "literal_error")]),
        ({"candle_timeframe_s": 60.0}, [(("candle_timeframe_s",), "int_type")]),
        ({"sweep_timeframe_s": "60"}, [(("sweep_timeframe_s",), "int_type")]),
        ({"max_legs": 3}, [(("max_legs",), "extra_forbidden")]),
    ],
)
def test_invalid_values_name_the_key_path(
    value: dict[str, Any], expected: list[tuple[Any, ...]]
) -> None:
    assert errors(value) == expected
