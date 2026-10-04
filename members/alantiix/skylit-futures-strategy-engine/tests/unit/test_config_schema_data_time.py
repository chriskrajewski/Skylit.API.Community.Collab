"""Unit tests for the ``data`` and ``time`` Config_Schema sections.

Design "Strategy_Config shape", D6 and §17 step 3: strict types, unknown keys
rejected, frozen, defaults from the design sketch. Inputs are plain dicts and
lists, as a YAML safe loader produces them.

**Validates: Requirements 5.1, 5.2, 5.3**
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from fse.config.schema import data as data_schema
from fse.config.schema.data import DataConfig, HeatmapViewConfig, InstrumentsConfig
from fse.config.schema.time import TimeConfig
from fse.data import cache
from fse.skylit import endpoints

DESIGN_DATA_YAML = """
symbols: [SPX, SPY, QQQ, NDX, NDXP]
heatmap_view: {max_strikes: 92, max_expirations: 5, include_empty: false}
regime_symbol: SPX
es_source_symbol: SPX
nq_source_symbol: QQQ
nq_sources: [QQQ, NDX, NDXP]
instruments: {es_levels: MES, nq_levels: MNQ}
velocity_window_s: 60
"""


def errors(model: type[DataConfig | TimeConfig], value: dict[str, Any]) -> list[tuple[Any, ...]]:
    with pytest.raises(ValidationError) as info:
        model.model_validate(value)
    return [(e["loc"], e["type"]) for e in info.value.errors()]


# ---------------------------------------------------------------- defaults


def test_defaults_match_the_design_sketch() -> None:
    assert DataConfig() == DataConfig.model_validate(yaml.safe_load(DESIGN_DATA_YAML))
    cfg = DataConfig()
    assert cfg.symbols == ("SPX", "SPY", "QQQ", "NDX", "NDXP")
    assert cfg.heatmap_view == HeatmapViewConfig(
        max_strikes=92, max_expirations=5, expirations=None, include_empty=False
    )
    assert (cfg.regime_symbol, cfg.es_source_symbol, cfg.nq_source_symbol) == ("SPX", "SPX", "QQQ")
    assert cfg.nq_sources == ("QQQ", "NDX", "NDXP")
    assert cfg.instruments == InstrumentsConfig(es_levels="MES", nq_levels="MNQ")
    assert cfg.velocity_window_s == 60
    assert TimeConfig().model_dump() == {"decision_cadence_s": 60, "timezone": "America/New_York"}


def test_skylit_limits_and_view_defaults_match_the_adapters() -> None:
    assert data_schema.MAX_STRIKES_CAP == endpoints.MAX_STRIKES_CAP
    assert data_schema.MAX_EXPIRATIONS_CAP == endpoints.MAX_EXPIRATIONS_CAP
    assert data_schema.DEFAULT_MAX_STRIKES == cache.DEFAULT_MAX_STRIKES
    assert data_schema.DEFAULT_MAX_EXPIRATIONS == cache.DEFAULT_MAX_EXPIRATIONS


def test_dump_is_plain_yaml_and_round_trips() -> None:
    cfg = DataConfig.model_validate(
        {
            "symbols": ["SPX", "QQQ"],
            "heatmap_view": {"max_strikes": "all", "expirations": ["2026-03-20"]},
            "nq_sources": ["QQQ"],
            "velocity_window_s": 30,
        }
    )
    text = yaml.safe_dump(cfg.model_dump(mode="python"), sort_keys=False)
    assert DataConfig.model_validate(yaml.safe_load(text)) == cfg
    assert cfg.symbols == ("SPX", "QQQ")  # tuples inside the model
    assert TimeConfig.model_validate(yaml.safe_load(yaml.safe_dump(TimeConfig().model_dump())))


def test_models_are_frozen() -> None:
    cfg = DataConfig()
    with pytest.raises(ValidationError):
        cfg.velocity_window_s = 5  # type: ignore[misc]


# ---------------------------------------------------------------- data errors


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"symbol": ["SPX"]}, [(("symbol",), "extra_forbidden")]),
        ({"symbols": []}, [(("symbols",), "too_short")]),
        ({"symbols": ["SPX", "IWM"]}, [(("symbols", 1), "literal_error")]),
        ({"symbols": "SPX"}, [(("symbols",), "tuple_type")]),
        ({"symbols": ["SPX", "SPX", "QQQ", "NDX", "NDXP"]}, [(("symbols",), "duplicate_id")]),
        ({"regime_symbol": "spx"}, [(("regime_symbol",), "literal_error")]),
        ({"velocity_window_s": 60.0}, [(("velocity_window_s",), "int_type")]),
        ({"velocity_window_s": True}, [(("velocity_window_s",), "int_type")]),
        ({"velocity_window_s": 0}, [(("velocity_window_s",), "greater_than_equal")]),
        ({"velocity_window_s": 3601}, [(("velocity_window_s",), "less_than_equal")]),
        ({"instruments": {"es_levels": "NQ"}}, [(("instruments", "es_levels"), "literal_error")]),
        ({"instruments": {"nq": "MNQ"}}, [(("instruments", "nq"), "extra_forbidden")]),
    ],
)
def test_invalid_data_values_name_the_key_path(
    value: dict[str, Any], expected: list[tuple[Any, ...]]
) -> None:
    assert errors(DataConfig, value) == expected


@pytest.mark.parametrize(
    ("value", "loc", "kind"),
    [
        ({"max_strikes": 0}, ("max_strikes",), "greater_than_equal"),
        ({"max_strikes": 1001}, ("max_strikes",), "less_than_equal"),
        ({"max_strikes": "92"}, ("max_strikes",), "int_type"),
        ({"max_expirations": 61}, ("max_expirations",), "less_than_equal"),
        ({"expirations": []}, ("expirations",), "too_short"),
        ({"expirations": ["2026-03-20", "Mar 27"]}, ("expirations", 1), "string_pattern_mismatch"),
        ({"expirations": ["2026-03-20", "2026-03-20"]}, ("expirations",), "duplicate_id"),
        ({"include_empty": "false"}, ("include_empty",), "bool_type"),
    ],
)
def test_invalid_heatmap_view_values_name_the_key_path(
    value: dict[str, Any], loc: tuple[Any, ...], kind: str
) -> None:
    assert errors(DataConfig, {"heatmap_view": value}) == [(("heatmap_view", *loc), kind)]


def test_named_symbols_must_be_configured_including_defaults() -> None:
    # Leaving regime_symbol and es_source_symbol at their SPX defaults still checks them.
    found = errors(DataConfig, {"symbols": ["QQQ", "NDX", "NDXP"]})
    assert found == [
        (("regime_symbol",), "literal_error"),
        (("es_source_symbol",), "literal_error"),
    ]
    found = errors(DataConfig, {"symbols": ["SPX", "SPY", "QQQ"]})
    assert found == [(("nq_sources",), "literal_error")]


def test_nq_source_symbol_must_be_one_of_the_nq_sources() -> None:
    with pytest.raises(ValidationError) as info:
        DataConfig.model_validate({"nq_sources": ["NDX", "NDXP"]})
    [error] = info.value.errors()
    assert (error["loc"], error["type"]) == (("nq_sources",), "literal_error")
    assert "'QQQ'" in error["msg"]


def test_a_not_configured_message_names_the_value_and_the_choices() -> None:
    with pytest.raises(ValidationError) as info:
        DataConfig.model_validate({"symbols": ["SPX", "QQQ"], "nq_sources": ["QQQ", "NDX"]})
    [error] = info.value.errors()
    assert error["msg"] == "'NDX' should be one of data.symbols: 'SPX', 'QQQ'"


def test_other_symbol_choices_validate() -> None:
    cfg = DataConfig.model_validate(
        {
            "symbols": ["SPY", "QQQ", "NDX"],
            "regime_symbol": "SPY",
            "es_source_symbol": "SPY",
            "nq_source_symbol": "NDX",
            "nq_sources": ["NDX", "QQQ"],
            "instruments": {"es_levels": "ES", "nq_levels": "NQ"},
        }
    )
    assert (cfg.regime_symbol, cfg.nq_source_symbol, cfg.instruments.es_levels) == (
        "SPY",
        "NDX",
        "ES",
    )


# ---------------------------------------------------------------- time errors


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"decision_cadence_s": 0}, [(("decision_cadence_s",), "greater_than_equal")]),
        ({"decision_cadence_s": 3601}, [(("decision_cadence_s",), "less_than_equal")]),
        ({"decision_cadence_s": "60"}, [(("decision_cadence_s",), "int_type")]),
        ({"decision_cadence_s": 60.0}, [(("decision_cadence_s",), "int_type")]),
        ({"timezone": "UTC"}, [(("timezone",), "literal_error")]),
        ({"cadence": 60}, [(("cadence",), "extra_forbidden")]),
    ],
)
def test_invalid_time_values_name_the_key_path(
    value: dict[str, Any], expected: list[tuple[Any, ...]]
) -> None:
    assert errors(TimeConfig, value) == expected


@pytest.mark.parametrize("cadence", [1, 5, 60, 300, 3600])
def test_decision_cadence_range(cadence: int) -> None:
    assert TimeConfig.model_validate({"decision_cadence_s": cadence}).decision_cadence_s == cadence
