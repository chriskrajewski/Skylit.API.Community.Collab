"""Unit tests for the Config_Printer (design §17 step 6).

Every key is written, defaults included, in schema order; the output loads
back into an equal config with exactly equal numbers. The round-trip property
over generated configs is Property 50 (task 20.5).

**Validates: Requirements 17.7, 17.8**
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from fse.config.loader import LoadOk, UniqueKeySafeLoader, load
from fse.config.printer import dump
from fse.config.schema import StrategyConfig
from tests.fakes.configs import minimal_config_data


def key_paths(model: type[BaseModel], prefix: str = "") -> set[str]:
    """Every key path the schema defines below ``model``, aliases as written in YAML."""
    paths: set[str] = set()
    for name, info in model.model_fields.items():
        path = f"{prefix}{info.alias or name}"
        paths.add(path)
        annotation = info.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            paths |= key_paths(annotation, f"{path}.")
    return paths


def dumped_paths(value: object, prefix: str = "") -> set[str]:
    paths: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}{key}"
            paths.add(path)
            paths |= dumped_paths(item, f"{path}.")
    return paths


def non_default_config() -> StrategyConfig:
    data: dict[str, Any] = minimal_config_data()
    data["config_id"] = "printer-test.v1"
    data["order_mode"] = "practice"
    data["regime"] = {"min_abs_value": 0.1 + 0.2, "whipsaw_pct": 1e-05}
    data["exits"] = {
        "per_regime": {
            "Positive_Gamma": None,
            "Whipsaw": {"mode": "fixed_r", "stop_rule": "fixed_ticks"},
        },
        "modes": {"fixed_r": {"r_multiple": 2.75}},
    }
    data["gates"] = {
        "stdev_fib_zone": {"enabled": True, "zones": [{"near": -1.0 / 3, "far": -4.5}]}
    }
    data["orders"] = {"flatten_time": "15:45", "max_age_min": 30}
    data["account"] = {"profit_target": {"value": "3000.50"}, "flat_deadline": "16:05"}
    data["sizing"] = {"risk_usd": "412.5"}
    data["live"] = {
        "run_window": {"start": "08:30", "end": "15:59"},
        "expected_contracts": {"ES": "CON.F.US.EP.H27"},
    }
    data["notify"] = {
        "narrator": {"enabled": True, "base_url": "http://localhost:11434", "model": "llama3.1:8b"}
    }
    data["reporting"] = {"primary_win_rate": "b", "scratch_tolerance_r": 0.05}
    return StrategyConfig.model_validate(data)


def test_every_schema_key_is_written_in_schema_order() -> None:
    cfg = StrategyConfig.model_validate(minimal_config_data())
    text = dump(cfg)
    written = yaml.safe_load(text)
    assert list(written) == list(StrategyConfig.model_fields)
    assert key_paths(StrategyConfig) <= dumped_paths(written)
    # Defaults are written, including null ones.
    assert written["notify"]["narrator"]["base_url"] is None
    assert written["fills"]["costs"]["ES"] is None
    assert written["exits"]["global"] == {
        "mode": "opposition_or_fixed_r",
        "stop_rule": "one_node_beyond",
    }


def test_money_and_times_are_quoted_strings() -> None:
    text = dump(non_default_config())
    written = yaml.safe_load(text)
    assert written["account"]["profit_target"]["value"] == "3000.50"
    assert written["sizing"]["risk_usd"] == "412.5"
    assert written["orders"]["flatten_time"] == "15:45"
    assert written["live"]["run_window"] == {"start": "08:30", "end": "15:59"}
    assert "flatten_time: '15:45'" in text


def test_round_trip_is_exact(tmp_path: Path) -> None:
    cfg = non_default_config()
    path = tmp_path / "printed.yaml"
    path.write_text(dump(cfg), encoding="utf-8")
    result = load(path)
    assert isinstance(result, LoadOk), result
    assert result.config == cfg
    assert result.config.regime.min_abs_value == 0.1 + 0.2
    assert result.config.regime.whipsaw_pct == 1e-05
    assert result.config.gates.stdev_fib_zone.zones[0].near == -1.0 / 3
    assert result.config.account.profit_target.value == Decimal("3000.50")
    assert dump(result.config) == dump(cfg)


def test_output_has_no_repeated_keys() -> None:
    loader = UniqueKeySafeLoader(dump(non_default_config()))
    try:
        loader.get_single_data()
    finally:
        loader.dispose()
    assert loader.duplicates == []
