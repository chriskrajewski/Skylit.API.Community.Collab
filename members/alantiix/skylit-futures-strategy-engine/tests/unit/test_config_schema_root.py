"""Unit tests for the root Config_Schema model (design §17, "Strategy_Config shape").

Section keys and order, the Paper default, the two required inputs, and an
id plus enabled flag for every Pattern, Gate, Exit_Mode, account rule and
Kill_Switch.

**Validates: Requirements 17.1, 17.2**
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from fse.config.schema import (
    ORDER_MODES,
    SECTION_KEYS,
    StrategyConfig,
)
from fse.config.schema.account import ACCOUNT_RULE_IDS
from fse.config.schema.exits import EXIT_MODES
from fse.config.schema.gates import GATE_IDS
from fse.config.schema.kill_switches import KILL_SWITCH_IDS
from fse.config.schema.patterns import DETECTOR_IDS
from tests.fakes.configs import minimal_config_data

# The top-level keys of the design's "Strategy_Config shape" sketch, in order.
DESIGN_ROOT_KEYS = (
    "schema_version",
    "config_id",
    "order_mode",
    "time",
    "data",
    "nodes",
    "regime",
    "levels",
    "chart",
    "patterns",
    "gates",
    "exits",
    "orders",
    "fills",
    "sizing",
    "kill_switches",
    "account",
    "live",
    "notify",
    "reporting",
    "experiments",
)


def errors_of(value: dict[str, Any]) -> list[tuple[Any, ...]]:
    with pytest.raises(ValidationError) as info:
        StrategyConfig.model_validate(value)
    return [(e["loc"], e["type"]) for e in info.value.errors()]


def test_root_keys_follow_the_design_sketch() -> None:
    assert tuple(StrategyConfig.model_fields) == DESIGN_ROOT_KEYS
    assert DESIGN_ROOT_KEYS[3:] == SECTION_KEYS


def test_defaults_paper_mode_and_every_rule_has_an_enabled_flag() -> None:
    cfg = StrategyConfig.model_validate(minimal_config_data())
    assert (cfg.schema_version, cfg.config_id, cfg.order_mode) == (1, "unnamed", "paper")
    assert cfg.regime.min_abs_value == 1500.0
    for detector in DETECTOR_IDS:
        assert isinstance(cfg.patterns.enabled(detector), bool)
    for gate in GATE_IDS:
        assert isinstance(cfg.gates.enabled(gate), bool)
    for mode in EXIT_MODES:
        assert isinstance(cfg.exits.modes.enabled(mode), bool)
    for rule in ACCOUNT_RULE_IDS:
        assert isinstance(getattr(cfg.account, rule).enabled, bool)
    for switch in KILL_SWITCH_IDS:
        assert isinstance(getattr(cfg.kill_switches, switch).enabled, bool)
    assert cfg.traded_instruments() == ("MES", "MNQ")


@pytest.mark.parametrize("mode", ORDER_MODES)
def test_order_modes(mode: str) -> None:
    cfg = StrategyConfig.model_validate({**minimal_config_data(), "order_mode": mode})
    assert cfg.order_mode == mode


def test_omitted_regime_reports_its_required_key() -> None:
    assert errors_of({}) == [(("regime", "min_abs_value"), "missing")]


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"order_mode": "live"}, [(("order_mode",), "literal_error")]),
        ({"schema_version": 2}, [(("schema_version",), "literal_error")]),
        ({"config_id": "has space"}, [(("config_id",), "string_pattern_mismatch")]),
        ({"config_id": 7}, [(("config_id",), "string_type")]),
        ({"strategy": {}}, [(("strategy",), "extra_forbidden")]),
        ({"live": []}, [(("live",), "model_type")]),
    ],
)
def test_root_keys_are_strict(override: dict[str, Any], expected: list[tuple[Any, ...]]) -> None:
    assert errors_of({**minimal_config_data(), **override}) == expected


def test_root_is_frozen() -> None:
    cfg = StrategyConfig.model_validate(minimal_config_data())
    with pytest.raises(ValidationError):
        cfg.order_mode = "combine"  # type: ignore[misc]
