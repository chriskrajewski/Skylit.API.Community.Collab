"""Unit tests for the ``account`` Config_Schema section.

Defaults (Req 15.1), enabled flags (Req 15.2) and ranges (Req 15.4): each
error names the rule's key path, carries the configured value and states the
valid range. Inputs are plain dicts, as a YAML safe loader produces them.

**Validates: Requirements 15.1, 15.2, 15.3, 15.4, 15.15**
"""

from __future__ import annotations

from datetime import time
from decimal import Decimal
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from fse.config.schema.account import ACCOUNT_RULE_IDS, AccountConfig
from fse.timekit import SessionTimes

DESIGN_ACCOUNT_YAML = """
starting_balance: {value: "50000.00"}
profit_target: {enabled: true, value: "3000.00"}
maximum_loss_limit: {enabled: true, value: "2000.00"}
daily_loss_limit: {enabled: true, value: "1000.00"}
consistency_target: {enabled: true, pct: 55.0}
position_cap: {enabled: true, micro_equivalents: 50}
flat_deadline: "16:10"
early_close_offset_min: 15
"""


def errors(value: dict[str, Any]) -> list[dict[str, Any]]:
    with pytest.raises(ValidationError) as info:
        AccountConfig.model_validate(value)
    return [dict(e) for e in info.value.errors()]


def test_defaults_match_requirement_15_1_and_the_design_sketch() -> None:
    cfg = AccountConfig()
    assert cfg == AccountConfig.model_validate(yaml.safe_load(DESIGN_ACCOUNT_YAML))
    assert cfg.starting_balance.value == Decimal("50000.00")
    assert cfg.profit_target.value == Decimal("3000.00")
    assert cfg.maximum_loss_limit.value == Decimal("2000.00")
    assert cfg.daily_loss_limit.value == Decimal("1000.00")
    assert cfg.consistency_target.pct == 55.0
    assert cfg.position_cap.micro_equivalents == 50
    assert cfg.flat_deadline == time(16, 10)
    assert cfg.early_close_offset_min == 15
    # Req 15.2: every toggleable rule is enabled by default.
    assert cfg.disabled_rules() == ()


def test_dump_is_plain_yaml_with_money_as_strings_and_round_trips() -> None:
    cfg = AccountConfig.model_validate(
        {
            "profit_target": {"value": "3000"},
            "daily_loss_limit": {"enabled": False, "value": "750.5"},
            "flat_deadline": "15:45",
        }
    )
    dumped = cfg.model_dump(mode="python")
    assert dumped["profit_target"] == {"enabled": True, "value": "3000.00"}
    assert dumped["daily_loss_limit"] == {"enabled": False, "value": "750.50"}
    assert dumped["flat_deadline"] == "15:45"
    text = yaml.safe_dump(dumped, sort_keys=False)
    assert "'15:45'" in text  # quoted, so YAML 1.1 cannot read it as base 60
    assert AccountConfig.model_validate(yaml.safe_load(text)) == cfg


def test_disabled_rules_are_listed_in_rule_order() -> None:
    cfg = AccountConfig.model_validate(
        {rule: {"enabled": False} for rule in reversed(ACCOUNT_RULE_IDS)}
    )
    assert cfg.disabled_rules() == ACCOUNT_RULE_IDS
    only = AccountConfig.model_validate({"position_cap": {"enabled": False}})
    assert only.disabled_rules() == ("position_cap",)


def test_session_times_take_the_flat_deadline_settings() -> None:
    cfg = AccountConfig.model_validate({"flat_deadline": "15:30", "early_close_offset_min": 0})
    times = cfg.session_times(SessionTimes())
    assert times.flat_deadline == time(15, 30)
    assert times.flat_deadline_early_close_offset_min == 0
    assert times.flatten_time == SessionTimes().flatten_time


def nested(path: str, value: object) -> dict[str, Any]:
    """``"a.b"``, 1 -> ``{"a": {"b": 1}}``."""
    head, _, rest = path.partition(".")
    return {head: nested(rest, value) if rest else value}


@pytest.mark.parametrize(
    ("path", "value", "kind"),
    [
        ("starting_balance.value", "0.00", "greater_than_equal"),
        ("profit_target.value", "10000000.01", "less_than_equal"),
        ("daily_loss_limit.value", "-5", "greater_than_equal"),
        ("profit_target.value", "3000.001", "decimal_max_places"),
        ("profit_target.value", "$3,000", "decimal_parsing"),
        ("profit_target.value", "1e3", "decimal_parsing"),
        ("profit_target.value", 3000, "decimal_type"),
        ("profit_target.value", 3000.0, "decimal_type"),
        ("profit_target.enabled", "yes", "bool_type"),
        ("consistency_target.pct", 0, "greater_than"),
        ("consistency_target.pct", 100.5, "less_than_equal"),
        ("position_cap.micro_equivalents", 0, "greater_than_equal"),
        ("position_cap.micro_equivalents", 1001, "less_than_equal"),
        ("position_cap.micro_equivalents", 5.0, "int_type"),
        ("early_close_offset_min", -1, "greater_than_equal"),
        ("early_close_offset_min", 61, "less_than_equal"),
        ("flat_deadline", 970, "time_type"),  # an unquoted 16:10 in YAML 1.1
        ("flat_deadline", "4:10 PM", "time_parsing"),
        ("flat_deadline", "09:30", "greater_than"),
        ("flat_deadline", "18:00", "less_than"),
        ("daily_loss_limit.pct", 5, "extra_forbidden"),
        ("max_drawdown", "2000.00", "extra_forbidden"),
    ],
)
def test_out_of_range_values_name_the_rule(path: str, value: object, kind: str) -> None:
    [error] = errors(nested(path, value))
    assert (error["loc"], error["type"]) == (tuple(path.split(".")), kind)
    assert error["input"] == value


def test_a_dollar_error_reports_the_value_and_the_valid_range() -> None:
    [error] = errors({"maximum_loss_limit": {"value": "0.001"}})
    assert error["loc"] == ("maximum_loss_limit", "value")
    assert error["input"] == "0.001"
    assert error["msg"] == "0.001 should be from 0.01 to 10000000.00 dollars"


def test_mll_must_be_below_the_starting_balance() -> None:
    [error] = errors(
        {"starting_balance": {"value": "2000.00"}, "maximum_loss_limit": {"value": "2000.00"}}
    )
    assert (error["loc"], error["type"]) == (("maximum_loss_limit",), "less_than")
    assert error["msg"] == (
        "maximum_loss_limit.value 2000.00 should be less than starting_balance.value 2000.00"
    )
    # Values are validated whether or not the rule is enabled.
    [error] = errors(
        {
            "starting_balance": {"value": "1000.00"},
            "maximum_loss_limit": {"enabled": False, "value": "2000.00"},
        }
    )
    assert error["type"] == "less_than"
    cfg = AccountConfig.model_validate(
        {"starting_balance": {"value": "2000.01"}, "maximum_loss_limit": {"value": "2000.00"}}
    )
    assert cfg.maximum_loss_limit.value < cfg.starting_balance.value


@pytest.mark.parametrize(
    ("value", "expected"),
    [("0.01", Decimal("0.01")), ("10000000.00", Decimal("10000000.00")), ("12", Decimal("12.00"))],
)
def test_dollar_range_bounds_are_inclusive(value: str, expected: Decimal) -> None:
    cfg = AccountConfig.model_validate({"daily_loss_limit": {"value": value}})
    assert cfg.daily_loss_limit.value == expected
    assert str(cfg.daily_loss_limit.value) == str(expected)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        (("consistency_target", "pct"), 100.0),
        (("consistency_target", "pct"), 0.5),
        (("position_cap", "micro_equivalents"), 1),
        (("position_cap", "micro_equivalents"), 1000),
    ],
)
def test_other_range_bounds_validate(key: tuple[str, str], value: float) -> None:
    section, field = key
    cfg = AccountConfig.model_validate({section: {field: value}})
    assert getattr(getattr(cfg, section), field) == value


def test_decimal_and_time_objects_validate_for_programmatic_configs() -> None:
    cfg = AccountConfig.model_validate(
        {"profit_target": {"value": Decimal("2500")}, "flat_deadline": time(15, 0)}
    )
    assert str(cfg.profit_target.value) == "2500.00"
    assert cfg.flat_deadline == time(15, 0)
    [error] = errors({"flat_deadline": time(15, 0, 30)})
    assert error["type"] == "time_type"


def test_models_are_frozen() -> None:
    cfg = AccountConfig()
    with pytest.raises(ValidationError):
        cfg.early_close_offset_min = 5  # type: ignore[misc]
