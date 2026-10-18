"""Unit tests for the shipped Playbook_Baseline, ``configs/playbook_baseline.yaml``.

As shipped, the file is the Config_Schema defaults and leaves out the values that have no
default: ``regime.min_abs_value`` (Req 7.4) and the commission and exchange fee of each
traded instrument (Req 13.8); the Operator enters them in the file. So the Config_Loader
rejects it with exactly the missing keys the Operator has not entered yet, and loads it
once all are entered. With a test-only overlay of fake values for them, it loads with no
error in Paper Order_Mode (Req 17.9).

The fake values live in this module only and are never written to the file.

**Validates: Requirements 7.4, 13.8, 17.9**
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any, Final

from fse.config.loader import (
    KIND_MISSING_KEY,
    LoadErr,
    LoadOk,
    UniqueKeySafeLoader,
    load,
    validate_data,
)
from fse.config.schema import StrategyConfig

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
"""The key paths the Operator enters before the first backtest."""

FAKE_OPERATOR_VALUES: Final[Mapping[str, Any]] = {
    "regime": {"min_abs_value": 1234.5},
    "fills": {
        "costs": {
            "MES": {"commission": "1.11", "exchange_fee": "2.22"},
            "MNQ": {"commission": "3.33", "exchange_fee": "4.44"},
        }
    },
}
"""Fake values for :data:`OPERATOR_REQUIRED`; test numbers, not any broker's fees."""


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


def written(data: object, path: str) -> bool:
    """Whether the file writes a value at dotted ``path``."""
    for key in path.split("."):
        if not isinstance(data, dict) or key not in data:
            return False
        data = data[key]
    return True


def outstanding() -> frozenset[str]:
    """The :data:`OPERATOR_REQUIRED` keys the Operator has not entered in the file yet."""
    return frozenset(p for p in OPERATOR_REQUIRED if not written(parsed_baseline(), p))


def test_shipped_file_fails_with_exactly_the_operator_keys_not_yet_entered() -> None:
    # The Operator enters the values in the file over time; every entered value must
    # validate, and each one still missing must be the only error.
    missing = outstanding()
    result = load(BASELINE)
    if not missing:
        assert isinstance(result, LoadOk), result
        return
    assert isinstance(result, LoadErr), f"{sorted(missing)} are missing, yet the file loads"
    found = sorted((e.key_path, e.kind) for e in result.errors)
    assert found == sorted((path, KIND_MISSING_KEY) for path in missing)


def test_operator_required_keys_not_yet_entered_are_marked_in_the_file() -> None:
    lines = BASELINE.read_text(encoding="utf-8").splitlines()
    marked = [line for line in lines if OPERATOR_MARK in line]
    missing = outstanding()
    needles = ["fills.costs.<instrument>"]  # the instructions stay beside the costs
    if "regime.min_abs_value" in missing:
        needles.append("regime.min_abs_value")
    for instrument in ("MES", "MNQ"):
        if {f"fills.costs.{instrument}.{k}" for k in ("commission", "exchange_fee")} <= missing:
            needles.append(f"{instrument}: {{}}")
    for needle in needles:
        assert any(needle in line for line in marked), needle


def test_with_fake_operator_values_it_loads_in_paper_mode() -> None:
    data = overlay(parsed_baseline(), FAKE_OPERATOR_VALUES)
    result = validate_data(data, source=str(BASELINE))
    assert isinstance(result, LoadOk), result
    cfg = result.config
    assert cfg.order_mode == "paper"
    assert cfg.config_id == "playbook_baseline"
    assert result.warnings == ()
    assert cfg.regime.min_abs_value == FAKE_OPERATOR_VALUES["regime"]["min_abs_value"]


def test_the_shipped_file_writes_the_schema_defaults() -> None:
    # The baseline is generated from the Config_Schema defaults: nothing but the Operator
    # values differs from ``StrategyConfig`` built with them.
    data = overlay(parsed_baseline(), FAKE_OPERATOR_VALUES)
    result = validate_data(data, source=str(BASELINE))
    assert isinstance(result, LoadOk), result
    expected = StrategyConfig.model_validate(
        {**FAKE_OPERATOR_VALUES, "config_id": "playbook_baseline"}
    )
    assert result.config.model_dump(exclude={"fills"}) == expected.model_dump(exclude={"fills"})
