"""Unit tests for the ``fills`` Config_Schema section (design §13, "Strategy_Config shape").

Defaults from the sketch, the Req 13 ranges, required commission and exchange
fee per instrument, exact Decimal money, strict types and unknown keys
rejected. Inputs are plain dicts, as a YAML safe loader produces them.

**Validates: Requirements 13.1, 13.3, 13.7, 13.8**
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, get_args

import pytest
import yaml
from pydantic import ValidationError

from fse.config.schema.data import EsInstrument, NqInstrument
from fse.config.schema.fills import (
    FILL_INSTRUMENTS,
    CostsConfig,
    FillInstrument,
    FillsConfig,
    InstrumentCosts,
)

DESIGN_FILLS_YAML = """
trade_through_ticks: 1
slippage_ticks: 1
costs:
  MES: {commission: "0.37", exchange_fee: "0.35"}
  MNQ: {commission: "0.37", exchange_fee: "0.35"}
"""


def error_types(value: dict[str, Any]) -> list[tuple[Any, ...]]:
    with pytest.raises(ValidationError) as info:
        FillsConfig.model_validate(value)
    return [(e["loc"], e["type"]) for e in info.value.errors()]


def test_defaults_and_design_sketch() -> None:
    cfg = FillsConfig()
    assert (cfg.trade_through_ticks, cfg.slippage_ticks) == (1, 1)
    assert cfg.costs == CostsConfig()
    assert all(cfg.costs_for(i) is None for i in FILL_INSTRUMENTS)

    sketch = FillsConfig.model_validate(yaml.safe_load(DESIGN_FILLS_YAML))
    mes = sketch.costs_for("MES")
    assert mes == InstrumentCosts(commission=Decimal("0.37"), exchange_fee=Decimal("0.35"))
    assert mes is not None
    assert mes.per_contract == Decimal("0.72")
    assert sketch.costs_for("ES") is None
    assert sketch.costs_for("MGC") is None


def test_instruments_cover_the_data_section_choices() -> None:
    assert set(get_args(FillInstrument.__value__)) == set(FILL_INSTRUMENTS)
    traded = set(get_args(EsInstrument.__value__)) | set(get_args(NqInstrument.__value__))
    assert traded == set(FILL_INSTRUMENTS)


def test_money_round_trips_through_yaml_as_strings() -> None:
    cfg = FillsConfig.model_validate(
        {
            "trade_through_ticks": 0,
            "slippage_ticks": 20,
            "costs": {"ES": {"commission": "25.00", "exchange_fee": "0"}},
        }
    )
    dumped = cfg.model_dump(mode="python")
    assert dumped["costs"]["ES"] == {"commission": "25.00", "exchange_fee": "0"}
    text = yaml.safe_dump(dumped, sort_keys=False)
    assert FillsConfig.model_validate(yaml.safe_load(text)) == cfg


def test_commission_and_exchange_fee_are_required() -> None:
    assert error_types({"costs": {"MES": {}}}) == [
        (("costs", "MES", "commission"), "missing"),
        (("costs", "MES", "exchange_fee"), "missing"),
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"trade_through_ticks": -1}, [(("trade_through_ticks",), "greater_than_equal")]),
        ({"trade_through_ticks": 21}, [(("trade_through_ticks",), "less_than_equal")]),
        ({"slippage_ticks": 21}, [(("slippage_ticks",), "less_than_equal")]),
        ({"slippage_ticks": 1.0}, [(("slippage_ticks",), "int_type")]),
        ({"slippage_ticks": True}, [(("slippage_ticks",), "int_type")]),
        (
            {"costs": {"MES": {"commission": "-0.01", "exchange_fee": "0"}}},
            [(("costs", "MES", "commission"), "greater_than_equal")],
        ),
        (
            {"costs": {"MNQ": {"commission": "0", "exchange_fee": "25.01"}}},
            [(("costs", "MNQ", "exchange_fee"), "less_than_equal")],
        ),
        (
            {"costs": {"MES": {"commission": 0.37, "exchange_fee": "0.35"}}},
            [(("costs", "MES", "commission"), "decimal_type")],
        ),
        (
            {"costs": {"MES": {"commission": "abc", "exchange_fee": "0.35"}}},
            [(("costs", "MES", "commission"), "decimal_parsing")],
        ),
        (
            {"costs": {"MGC": {"commission": "0.37", "exchange_fee": "0.35"}}},
            [(("costs", "MGC"), "extra_forbidden")],
        ),
        (
            {"costs": {"MES": {"commission": "0", "exchange_fee": "0", "nfa": "0"}}},
            [(("costs", "MES", "nfa"), "extra_forbidden")],
        ),
        ({"partial_fills": True}, [(("partial_fills",), "extra_forbidden")]),
    ],
)
def test_out_of_range_wrong_type_and_unknown_keys_are_rejected(
    value: dict[str, Any], expected: list[tuple[Any, ...]]
) -> None:
    assert error_types(value) == expected


def test_section_is_frozen() -> None:
    cfg = FillsConfig()
    with pytest.raises(ValidationError):
        cfg.slippage_ticks = 2  # type: ignore[misc]
