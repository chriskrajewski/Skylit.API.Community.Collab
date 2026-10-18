"""Unit tests for the ``kill_switches`` Config_Schema section.

Design "Strategy_Config shape", D6 and §17 step 3: strict types, unknown keys
rejected, frozen, defaults from the design sketch and minimums from Req 16.
Money is a quoted decimal string. Inputs are plain dicts, as a YAML safe loader
produces them.

**Validates: Requirements 16.1, 16.2, 16.3, 16.4, 16.5, 16.6**
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from fse.config.schema.kill_switches import KILL_SWITCH_IDS, KillSwitchesConfig

DESIGN_KILL_SWITCHES_YAML = """
max_trades: {enabled: true, max: 3}
max_losers: {enabled: true, limit: 2}
consecutive_losers: {enabled: true, limit: 3}
red_day: {enabled: true, threshold_usd: "0"}
daily_profit_cap: {enabled: true, cap_usd: "1200"}
internal_daily_loss_stop: {enabled: false, multiple: 2.0}
losing_trade_tolerance_usd: "0"
"""


def errors(value: dict[str, Any]) -> list[tuple[Any, ...]]:
    with pytest.raises(ValidationError) as info:
        KillSwitchesConfig.model_validate(value)
    return [(e["loc"], e["type"]) for e in info.value.errors()]


def test_defaults_match_the_design_sketch() -> None:
    cfg = KillSwitchesConfig()
    assert cfg == KillSwitchesConfig.model_validate(yaml.safe_load(DESIGN_KILL_SWITCHES_YAML))
    assert (cfg.max_trades.enabled, cfg.max_trades.max) == (True, 3)
    assert (cfg.max_losers.enabled, cfg.max_losers.limit) == (True, 2)
    assert (cfg.consecutive_losers.enabled, cfg.consecutive_losers.limit) == (True, 3)
    assert (cfg.red_day.enabled, cfg.red_day.threshold_usd) == (True, Decimal("0"))
    assert (cfg.daily_profit_cap.enabled, cfg.daily_profit_cap.cap_usd) == (True, Decimal("1200"))
    assert cfg.internal_daily_loss_stop.enabled is False
    assert cfg.internal_daily_loss_stop.multiple == 2.0
    assert cfg.losing_trade_tolerance_usd == Decimal("0")


def test_every_rule_id_is_a_section_key() -> None:
    keys = list(KillSwitchesConfig.model_fields)
    assert keys == [*KILL_SWITCH_IDS, "losing_trade_tolerance_usd"]


def test_dump_is_plain_yaml_with_money_strings_and_round_trips() -> None:
    cfg = KillSwitchesConfig.model_validate(
        {
            "max_trades": {"enabled": False, "max": 1},
            "red_day": {"threshold_usd": "-250.50"},
            "daily_profit_cap": {"cap_usd": "0.01"},
            "internal_daily_loss_stop": {"enabled": True, "multiple": 1.5},
            "losing_trade_tolerance_usd": "2.50",
        }
    )
    dumped = cfg.model_dump(mode="python")
    assert dumped["red_day"]["threshold_usd"] == "-250.50"
    assert dumped["losing_trade_tolerance_usd"] == "2.50"
    text = yaml.safe_dump(dumped, sort_keys=False)
    assert KillSwitchesConfig.model_validate(yaml.safe_load(text)) == cfg


def test_model_is_frozen() -> None:
    cfg = KillSwitchesConfig()
    with pytest.raises(ValidationError):
        cfg.max_trades.max = 5  # type: ignore[misc]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"max_trades": {"max": 0}}, [(("max_trades", "max"), "greater_than_equal")]),
        ({"max_trades": {"max": 3.0}}, [(("max_trades", "max"), "int_type")]),
        ({"max_losers": {"limit": 0}}, [(("max_losers", "limit"), "greater_than_equal")]),
        (
            {"consecutive_losers": {"limit": 0}},
            [(("consecutive_losers", "limit"), "greater_than_equal")],
        ),
        ({"red_day": {"threshold_usd": 0}}, [(("red_day", "threshold_usd"), "decimal_type")]),
        ({"red_day": {"threshold_usd": 0.5}}, [(("red_day", "threshold_usd"), "decimal_type")]),
        ({"red_day": {"threshold_usd": "nan"}}, [(("red_day", "threshold_usd"), "finite_number")]),
        (
            {"red_day": {"threshold_usd": "ten"}},
            [(("red_day", "threshold_usd"), "decimal_parsing")],
        ),
        (
            {"daily_profit_cap": {"cap_usd": "0"}},
            [(("daily_profit_cap", "cap_usd"), "greater_than")],
        ),
        (
            {"internal_daily_loss_stop": {"multiple": 0.0}},
            [(("internal_daily_loss_stop", "multiple"), "greater_than")],
        ),
        (
            {"internal_daily_loss_stop": {"multiple": float("inf")}},
            [(("internal_daily_loss_stop", "multiple"), "finite_number")],
        ),
        (
            {"internal_daily_loss_stop": {"enabled": "yes"}},
            [(("internal_daily_loss_stop", "enabled"), "bool_type")],
        ),
        (
            {"losing_trade_tolerance_usd": "-0.01"},
            [(("losing_trade_tolerance_usd",), "greater_than_equal")],
        ),
        ({"max_trades": {"limit": 3}}, [(("max_trades", "limit"), "extra_forbidden")]),
        ({"two_loser_lockout": {}}, [(("two_loser_lockout",), "extra_forbidden")]),
    ],
)
def test_invalid_values_name_the_key_path(
    value: dict[str, Any], expected: list[tuple[Any, ...]]
) -> None:
    assert errors(value) == expected


def test_every_error_is_reported_together() -> None:
    found = errors({"max_trades": {"max": 0}, "daily_profit_cap": {"cap_usd": "-1"}, "typo": 1})
    assert found == [
        (("max_trades", "max"), "greater_than_equal"),
        (("daily_profit_cap", "cap_usd"), "greater_than"),
        (("typo",), "extra_forbidden"),
    ]
