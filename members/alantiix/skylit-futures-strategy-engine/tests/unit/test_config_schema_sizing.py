"""Unit tests for the ``sizing`` Config_Schema section.

Design §14 "Position_Sizer" and "Strategy_Config shape": defaults from the
sketch, the Req 14 ranges, money as quoted decimal strings, strict types and
unknown keys rejected. Inputs are plain dicts, as a YAML safe loader produces.

**Validates: Requirements 14.1, 14.2, 14.4, 14.5, 14.6, 14.7**
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, get_args

import pytest
import yaml
from pydantic import ValidationError

from fse.config.schema.sizing import (
    SIZING_INSTRUMENTS,
    SIZING_MODES,
    FixedContracts,
    ReducedContracts,
    SizingConfig,
    SizingInstrument,
    SizingMode,
)

DESIGN_SIZING_YAML = """
mode: fixed_contracts
fixed: {MES: 5, MNQ: 3}
risk_usd: "375"
big_win: {usd: "1200", r: 3, reduced: {MES: 3, MNQ: 2}}
trinity_size_down: {enabled: true, fraction: 0.5}
vix_gap: {enabled: true, pct: 15.0}
micro_equivalent_limit: 50
"""


def error_types(value: dict[str, Any]) -> list[tuple[Any, ...]]:
    with pytest.raises(ValidationError) as info:
        SizingConfig.model_validate(value)
    return [(e["loc"], e["type"]) for e in info.value.errors()]


def test_defaults_match_the_design_sketch() -> None:
    cfg = SizingConfig()
    assert cfg == SizingConfig.model_validate(yaml.safe_load(DESIGN_SIZING_YAML))
    assert cfg.mode == "fixed_contracts"
    assert (cfg.fixed.MES, cfg.fixed.MNQ, cfg.fixed.ES, cfg.fixed.NQ) == (5, 3, 1, 1)
    assert cfg.risk_usd == Decimal("375")
    assert (cfg.big_win.usd, cfg.big_win.r) == (Decimal("1200"), 3.0)
    assert cfg.big_win.reduced == ReducedContracts(MES=3, MNQ=2, ES=1, NQ=1)
    assert (cfg.trinity_size_down.enabled, cfg.trinity_size_down.fraction) == (True, 0.5)
    assert (cfg.vix_gap.enabled, cfg.vix_gap.pct) == (True, 15.0)
    assert cfg.micro_equivalent_limit == 50


def test_literals_match_their_constants() -> None:
    assert set(get_args(SizingMode.__value__)) == SIZING_MODES
    assert get_args(SizingInstrument.__value__) == SIZING_INSTRUMENTS


def test_configured_values_and_yaml_round_trip() -> None:
    cfg = SizingConfig.model_validate(
        {
            "mode": "fixed_dollar_risk",
            "fixed": {"MES": 2, "ES": 3},
            "risk_usd": " 412.50 ",
            "big_win": {"usd": "999.99", "r": 2.5, "reduced": {"MNQ": 1}},
            "trinity_size_down": {"enabled": False, "fraction": 1},  # a whole float is valid
            "vix_gap": {"pct": 20},
            "micro_equivalent_limit": 1,
        }
    )
    assert cfg.fixed == FixedContracts(MES=2, MNQ=3, ES=3, NQ=1)
    assert cfg.risk_usd == Decimal("412.50")
    assert cfg.big_win.reduced.for_instrument("MNQ") == 1
    assert cfg.trinity_size_down.fraction == 1.0
    dumped = cfg.model_dump(mode="python")
    assert (dumped["risk_usd"], dumped["big_win"]["usd"]) == ("412.50", "999.99")
    text = yaml.safe_dump(dumped, sort_keys=False)
    assert SizingConfig.model_validate(yaml.safe_load(text)) == cfg


def test_for_instrument_reads_each_instrument_and_refuses_others() -> None:
    fixed = FixedContracts(MES=5, MNQ=3, ES=2, NQ=4)
    assert [fixed.for_instrument(i) for i in SIZING_INSTRUMENTS] == [5, 3, 2, 4]
    with pytest.raises(ValueError, match="MGC"):
        fixed.for_instrument("MGC")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"mode": "fixed_risk"}, [(("mode",), "literal_error")]),
        ({"fixed": {"MES": 0}}, [(("fixed", "MES"), "greater_than_equal")]),
        ({"fixed": {"MNQ": 2.0}}, [(("fixed", "MNQ"), "int_type")]),
        ({"fixed": {"MGC": 1}}, [(("fixed", "MGC"), "extra_forbidden")]),
        ({"risk_usd": "0"}, [(("risk_usd",), "greater_than")]),
        ({"risk_usd": "-5"}, [(("risk_usd",), "greater_than")]),
        # Money is a quoted string: a float would not be exact, and YAML ints are refused too.
        ({"risk_usd": 375.0}, [(("risk_usd",), "decimal_type")]),
        ({"risk_usd": 375}, [(("risk_usd",), "decimal_type")]),
        ({"risk_usd": "$375"}, [(("risk_usd",), "decimal_parsing")]),
        ({"risk_usd": "NaN"}, [(("risk_usd",), "finite_number")]),
        ({"big_win": {"usd": "0"}}, [(("big_win", "usd"), "greater_than")]),
        ({"big_win": {"r": 0}}, [(("big_win", "r"), "greater_than")]),
        ({"big_win": {"r": float("inf")}}, [(("big_win", "r"), "finite_number")]),
        (
            {"big_win": {"reduced": {"NQ": 0}}},
            [(("big_win", "reduced", "NQ"), "greater_than_equal")],
        ),
        (
            {"trinity_size_down": {"fraction": 0}},
            [(("trinity_size_down", "fraction"), "greater_than")],
        ),
        (
            {"trinity_size_down": {"fraction": 1.01}},
            [(("trinity_size_down", "fraction"), "less_than_equal")],
        ),
        (
            {"trinity_size_down": {"enabled": "yes"}},
            [(("trinity_size_down", "enabled"), "bool_type")],
        ),
        ({"vix_gap": {"pct": 0.0}}, [(("vix_gap", "pct"), "greater_than")]),
        ({"vix_gap": {"pct": "15"}}, [(("vix_gap", "pct"), "float_type")]),
        ({"micro_equivalent_limit": 0}, [(("micro_equivalent_limit",), "greater_than_equal")]),
        ({"micro_equivalent_limit": 50.0}, [(("micro_equivalent_limit",), "int_type")]),
        ({"big_win": {"enabled": False}}, [(("big_win", "enabled"), "extra_forbidden")]),
        ({"max_contracts": 10}, [(("max_contracts",), "extra_forbidden")]),
    ],
)
def test_out_of_range_wrong_type_and_unknown_keys_are_rejected(
    value: dict[str, Any], expected: list[tuple[Any, ...]]
) -> None:
    assert error_types(value) == expected


def test_section_is_frozen() -> None:
    cfg = SizingConfig()
    with pytest.raises(ValidationError):
        cfg.micro_equivalent_limit = 10  # type: ignore[misc]
