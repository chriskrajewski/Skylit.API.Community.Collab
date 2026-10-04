"""Unit tests for the shipped Playbook_Baseline, ``configs/playbook_baseline.yaml``.

As shipped, the file leaves out the values the Skill_Documents do not settle:
``regime.min_abs_value`` (Req 7.4) and the commission and exchange fee of each
traded instrument (Req 13.8). So the Config_Loader rejects it with exactly
those missing keys. With a test-only overlay of fake values for them, it loads
with no error in Paper Order_Mode (Req 17.9). The design's codification-table
values are checked as written in the file, not as schema defaults.

The fake values live in this module only and are never written to the file.

**Validates: Requirements 7.4, 13.8, 17.9**
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any, Final

import pytest

from fse.config.loader import (
    KIND_MISSING_KEY,
    LoadErr,
    LoadOk,
    UniqueKeySafeLoader,
    load,
    validate_data,
)
from fse.config.schema.gates import GATE_IDS
from fse.config.schema.kill_switches import KILL_SWITCH_IDS
from fse.config.schema.orders import CANCEL_TRIGGERS

PROJECT_DIR: Final = Path(__file__).resolve().parents[2]
BASELINE: Final = PROJECT_DIR / "configs" / "playbook_baseline.yaml"

OPERATOR_MARK: Final = "# OPERATOR: required"

OPERATOR_REQUIRED: Final = frozenset(
    {
        "regime.min_abs_value",
        "fills.costs.MES.commission",
        "fills.costs.MES.exchange_fee",
        "fills.costs.MNQ.commission",
        "fills.costs.MNQ.exchange_fee",
    }
)
"""The key paths the Operator enters before the first backtest (task 21)."""

FAKE_OPERATOR_VALUES: Final[Mapping[str, Any]] = {
    "regime": {"min_abs_value": 1234.5},
    "fills": {
        "costs": {
            "MES": {"commission": "1.11", "exchange_fee": "2.22"},
            "MNQ": {"commission": "3.33", "exchange_fee": "4.44"},
        }
    },
}
"""Fake values for :data:`OPERATOR_REQUIRED`; test numbers, not Topstep's fees."""

DISABLED_GATES: Final = frozenset({"dark_pool_confluence", "stdev_fib_zone"})

# Design "Playbook_Baseline choices that codify the Skill_Documents": key path ->
# the value written in the file. `kill_switches.*` and `sizing.*` list the
# values the Skill_Documents give.
CODIFIED: Final[Mapping[str, object]] = {
    "schema_version": 1,
    "config_id": "playbook_baseline",
    "order_mode": "paper",
    "exits.per_regime.Positive_Gamma.mode": "next_node",
    "exits.global.mode": "opposition_or_fixed_r",
    "exits.modes.opposition_or_fixed_r.r_multiple": 3.0,
    "gates.min_reward_risk.min": 3.0,
    "gates.min_reward_risk.alert_min": 2.0,
    "gates.opposition_inside_target.fraction": 0.85,
    "gates.opposition_inside_target.window_r": 3.0,
    "exits.breakeven.trigger_r": 1.0,
    "orders.max_open": 1,
    "orders.flatten_time": "15:55",
    "orders.invalidation.king_flip": "exit_market",
    "orders.invalidation.source_node_gone": "exit_market",
    "orders.invalidation.sign_flip": "exit_market",
    "kill_switches.max_trades.max": 3,
    "kill_switches.max_losers.limit": 2,
    "kill_switches.consecutive_losers.limit": 3,
    "kill_switches.red_day.threshold_usd": "0",
    "kill_switches.daily_profit_cap.cap_usd": "1200",
    "sizing.mode": "fixed_contracts",
    "sizing.fixed.MES": 5,
    "sizing.fixed.MNQ": 3,
    "sizing.risk_usd": "375",
    "sizing.big_win.usd": "1200",
    "sizing.big_win.r": 3.0,
    "sizing.big_win.reduced.MES": 3,
    "sizing.big_win.reduced.MNQ": 2,
    "sizing.trinity_size_down.enabled": True,
    "sizing.vix_gap.enabled": True,
    "sizing.vix_gap.pct": 15.0,
    **{f"kill_switches.{k}.enabled": k != "internal_daily_loss_stop" for k in KILL_SWITCH_IDS},
    **{f"orders.cancel_triggers.{t}": True for t in CANCEL_TRIGGERS},
    **{f"gates.{g}.enabled": g not in DISABLED_GATES for g in GATE_IDS},
}


@cache
def parsed_baseline() -> Mapping[str, Any]:
    """The shipped file as the Config_Loader parses it; read-only, shared between tests."""
    loader = UniqueKeySafeLoader(BASELINE.read_text(encoding="utf-8"))
    try:
        data = loader.get_single_data()
        duplicates = list(loader.duplicates)
    finally:
        loader.dispose()
    assert duplicates == []
    assert isinstance(data, dict)
    return data


def overlay(base: Mapping[str, Any], top: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of ``base`` with ``top`` merged in, mapping by mapping; ``top`` wins elsewhere."""
    merged = dict(base)
    for key, value in top.items():
        below = merged.get(key)
        if isinstance(below, Mapping) and isinstance(value, Mapping):
            merged[key] = overlay(below, value)
        else:
            merged[key] = value
    return merged


def dig(data: object, path: str) -> object:
    """The value at dotted ``path``; fails when the file does not write it."""
    for key in path.split("."):
        assert isinstance(data, dict), f"{path}: {key!r} is under a non-mapping"
        assert key in data, f"{path} is not written in {BASELINE.name}"
        data = data[key]
    return data


def test_shipped_file_fails_with_exactly_the_operator_required_keys() -> None:
    result = load(BASELINE)
    assert isinstance(result, LoadErr), "the shipped baseline must not carry Operator values"
    found = sorted((e.key_path, e.kind) for e in result.errors)
    assert found == sorted((path, KIND_MISSING_KEY) for path in OPERATOR_REQUIRED)


def test_operator_required_keys_are_marked_in_the_file() -> None:
    lines = BASELINE.read_text(encoding="utf-8").splitlines()
    marked = [line for line in lines if OPERATOR_MARK in line]
    for needle in ("regime.min_abs_value", "fills.costs.<instrument>", "MES: {}", "MNQ: {}"):
        assert any(needle in line for line in marked), needle


def test_with_fake_operator_values_it_loads_in_paper_mode() -> None:
    data = overlay(parsed_baseline(), FAKE_OPERATOR_VALUES)
    result = validate_data(data, source=str(BASELINE))
    assert isinstance(result, LoadOk), result
    cfg = result.config
    assert cfg.order_mode == "paper"
    assert cfg.config_id == "playbook_baseline"
    assert result.warnings == ()
    assert set(GATE_IDS) - set(cfg.gates.enabled_ids()) == DISABLED_GATES
    assert len(cfg.gates.enabled_ids()) == 25
    assert cfg.regime.min_abs_value == FAKE_OPERATOR_VALUES["regime"]["min_abs_value"]


@pytest.mark.parametrize(("path", "value"), list(CODIFIED.items()), ids=list(CODIFIED))
def test_codified_value_is_written_explicitly(path: str, value: object) -> None:
    found = dig(parsed_baseline(), path)
    assert (type(found), found) == (type(value), value)
