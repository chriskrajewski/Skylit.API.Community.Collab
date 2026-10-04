"""Examples for the Position_Sizer pipeline (design §14, Req 14).

The setups come from ``test_engine_decision_types``: a long and a short MES
setup with a 28-tick (7-point) stop, so 1 MES contract risks $35 and 1 MNQ
contract $14.

**Validates: Requirements 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7, 14.8, 14.9**
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from fse.config.schema.sizing import SizingConfig
from fse.engine.sizing import (
    MICRO_EQUIVALENTS_PER_CONTRACT,
    POINT_VALUE_USD,
    SIZING_STEPS,
    TICK_VALUE_USD,
    PriorSession,
    SizedSetup,
    SizingContext,
    SizingRejection,
    StepOutcome,
    base_contracts,
    micro_equivalents,
    size,
    vix_gap_pct,
)
from fse.engine.types import CandidateSetup, MissingInput, Unavailable, VixState
from fse.logio.canonical_json import to_jsonable
from tests.unit.test_engine_decision_types import (
    inputs,
    key,
    long_setup,
    short_setup,
    source,
    trade,
)

VIX_FLAT = VixState(daily_open=16.0, prior_close=16.0, last_1m_close=16.0)
VIX_GAP_15 = VixState(daily_open=18.4, prior_close=16.0, last_1m_close=18.0)  # exactly 15%


def context(**overrides: Any) -> SizingContext:
    fields: dict[str, Any] = {
        "prior_session": PriorSession(),
        "trinity_agreement": 3,
        "vix": VIX_FLAT,
        "used_micro_equivalents": 0,
    }
    fields.update(overrides)
    return SizingContext(**fields)


def config(**sections: Any) -> SizingConfig:
    return SizingConfig.model_validate(sections)


def setup_for(instrument: str) -> CandidateSetup:
    return long_setup(key=key(instrument=instrument))


def unpriced() -> CandidateSetup:
    return long_setup(
        entry=None,
        stop=None,
        targets=(),
        source=source(level=None, band=None),
        inputs=inputs(stop_rule=None),
    )


def sized(result: SizedSetup | SizingRejection) -> SizedSetup:
    assert isinstance(result, SizedSetup), result
    return result


def rejected(result: SizedSetup | SizingRejection) -> SizingRejection:
    assert isinstance(result, SizingRejection), result
    return result


def contracts_by_step(result: SizedSetup | SizingRejection) -> tuple[int, ...]:
    return tuple(s.contracts for s in result.steps)


# ---------------------------------------------------------------- tables


def test_contract_tables() -> None:
    assert dict(TICK_VALUE_USD) == {
        "MES": Decimal("1.25"),
        "MNQ": Decimal("0.50"),
        "ES": Decimal("12.50"),
        "NQ": Decimal("5.00"),
    }
    assert dict(POINT_VALUE_USD) == {"MES": 5, "MNQ": 2, "ES": 50, "NQ": 20}
    assert dict(MICRO_EQUIVALENTS_PER_CONTRACT) == {"MES": 1, "MNQ": 1, "ES": 10, "NQ": 10}
    assert micro_equivalents("ES", 3) + micro_equivalents("MNQ", 4) == 34
    with pytest.raises(ValueError, match="MGC"):
        micro_equivalents("MGC", 1)
    with pytest.raises(ValueError, match="contracts"):
        micro_equivalents("MES", -1)


# ---------------------------------------------------------------- base contracts


@pytest.mark.parametrize(("instrument", "expected"), [("MES", 5), ("MNQ", 3), ("ES", 1), ("NQ", 1)])
def test_fixed_contracts_pass_through_untriggered_steps(instrument: str, expected: int) -> None:
    result = sized(size(setup_for(instrument), context(), SizingConfig()))
    assert result.contracts == result.base_contracts == expected
    assert tuple(s.step for s in result.steps) == SIZING_STEPS
    assert [s.triggered for s in result.steps] == [True, False, False, False, False]


@pytest.mark.parametrize(
    ("setup", "risk", "expected"),
    [
        (long_setup(), "375", 10),  # 375 / 35 = 10.71
        (short_setup(), "375", 10),
        (setup_for("MNQ"), "375", 26),  # 375 / 14 = 26.79
        (setup_for("ES"), "375", 1),  # 375 / 350
        (long_setup(), "35", 1),  # exactly 1, no rounding error
        (long_setup(), "70.00", 2),
        (long_setup(), "34.99", 0),
    ],
)
def test_fixed_dollar_risk_base(setup: CandidateSetup, risk: str, expected: int) -> None:
    cfg = config(mode="fixed_dollar_risk", risk_usd=risk)
    assert base_contracts(setup, cfg) == expected


def test_zero_base_is_size_zero_naming_the_base_step() -> None:
    cfg = config(mode="fixed_dollar_risk", risk_usd="34.99")
    # The base step comes first, so a missing Trinity input is never reached.
    result = rejected(size(long_setup(), context(trinity_agreement=Unavailable("no map")), cfg))
    assert (result.reason, result.step, result.missing) == ("size_zero", "base_contracts", None)
    assert result.key == long_setup().key
    assert result.steps == (StepOutcome("base_contracts", 0, True),)


def test_unpriced_setup() -> None:
    assert sized(size(unpriced(), context(), SizingConfig())).contracts == 5
    result = rejected(size(unpriced(), context(), config(mode="fixed_dollar_risk")))
    assert (result.reason, result.step) == ("data_unavailable", "base_contracts")
    assert result.missing == MissingInput(("entry", "stop"))
    assert result.steps == ()


def test_unknown_instrument_is_refused() -> None:
    with pytest.raises(ValueError, match="MGC"):
        size(setup_for("MGC"), context(), SizingConfig())
    with pytest.raises(ValueError, match="MGC"):
        size(setup_for("MGC"), context(), config(mode="fixed_dollar_risk"))


# ---------------------------------------------------------------- big win


@pytest.mark.parametrize(
    ("net", "r_sum", "expected"),
    [
        ("1200", "0", 3),  # the dollar threshold, inclusive
        ("0", "3", 3),  # the R threshold, inclusive
        ("1199.99", "2.99", 5),
        ("-500", "-1", 5),
    ],
)
def test_big_win_reduces_mes(net: str, r_sum: str, expected: int) -> None:
    prior = PriorSession(Decimal(net), Decimal(r_sum))
    result = sized(size(long_setup(), context(prior_session=prior), SizingConfig()))
    assert result.contracts == expected
    assert result.steps[1].triggered is (expected == 3)


def test_big_win_takes_the_lesser_of_base_and_reduced() -> None:
    big = context(prior_session=PriorSession(Decimal(1500)))
    assert sized(size(setup_for("MNQ"), big, SizingConfig())).contracts == 2
    result = sized(size(long_setup(), big, config(fixed={"MES": 2})))
    assert (result.contracts, result.steps[1].triggered) == (2, True)


def test_big_win_r_threshold_is_the_decimal_written() -> None:
    # 2.1 as a binary float is above 2.1, so a float comparison would miss this.
    prior = PriorSession(r_sum=Decimal("2.1"))
    cfg = config(big_win={"r": 2.1})
    assert sized(size(long_setup(), context(prior_session=prior), cfg)).contracts == 3


def test_prior_session_sums_filled_trades_only() -> None:
    trades = [
        trade(net=Decimal("800.00"), r_multiple=Decimal("2.0")),
        trade(net=Decimal("450.00"), r_multiple=Decimal("1.5")),
        trade(net=Decimal("5000.00"), r_multiple=Decimal("9"), shadow=True),
    ]
    assert PriorSession.from_trades(trades) == PriorSession(Decimal("1250.00"), Decimal("3.5"))
    assert PriorSession.from_trades([trades[2]]) == PriorSession() == PriorSession.from_trades([])
    with pytest.raises(ValueError, match="net"):
        PriorSession(net=1200.0)  # type: ignore[arg-type]


# ---------------------------------------------------------------- Trinity size-down


@pytest.mark.parametrize(("agreeing", "expected"), [(2, 2), (3, 5), (1, 5), (0, 5)])
def test_trinity_size_down_at_exactly_two_of_three(agreeing: int, expected: int) -> None:
    result = sized(size(long_setup(), context(trinity_agreement=agreeing), SizingConfig()))
    assert result.contracts == expected  # floor(5 x 0.5) = 2


def test_trinity_size_down_minimum_one_and_exact_fraction() -> None:
    two = context(trinity_agreement=2)
    assert sized(size(long_setup(), two, config(fixed={"MES": 1}))).contracts == 1
    # 100 x 0.29 is 28.999... in floats; the configured decimal gives 29.
    cfg = config(
        fixed={"MES": 100}, trinity_size_down={"fraction": 0.29}, micro_equivalent_limit=500
    )
    assert sized(size(long_setup(), two, cfg)).contracts == 29


def test_trinity_unavailable() -> None:
    missing = context(trinity_agreement=Unavailable("no SPY Snapshot"))
    result = rejected(size(long_setup(), missing, SizingConfig()))
    assert (result.reason, result.step) == ("data_unavailable", "trinity_size_down")
    assert result.missing == MissingInput(("trinity_agreement",))
    assert tuple(s.step for s in result.steps) == ("base_contracts", "big_win_reduce")
    off = config(trinity_size_down={"enabled": False})
    assert sized(size(long_setup(), missing, off)).contracts == 5


# ---------------------------------------------------------------- VIX gap


def test_vix_gap_pct_is_exact() -> None:
    assert vix_gap_pct(VIX_GAP_15) == 15
    assert vix_gap_pct(VixState(15.0, 20.0, Unavailable("none"))) == -25
    assert vix_gap_pct(VixState(Unavailable("09:31"), Unavailable("no bar"), 1.0)) == MissingInput(
        ("vix_daily_open", "vix_prior_close")
    )
    assert vix_gap_pct(VixState(18.0, 0.0, 18.0)) == MissingInput(("vix_prior_close",))
    assert vix_gap_pct(VixState(float("nan"), 16.0, 18.0)) == MissingInput(("vix_daily_open",))


@pytest.mark.parametrize(
    ("vix", "fixed", "expected"),
    [
        (VIX_GAP_15, 5, 2),  # exactly 15% halves: floor(5 / 2)
        (VixState(18.39, 16.0, 18.0), 5, 5),  # 14.94%
        (VIX_GAP_15, 1, 1),  # minimum 1
    ],
)
def test_vix_gap_halving(vix: VixState, fixed: int, expected: int) -> None:
    result = sized(size(long_setup(), context(vix=vix), config(fixed={"MES": fixed})))
    assert result.contracts == expected


def test_vix_gap_threshold_and_disabled_rule() -> None:
    assert (
        sized(size(long_setup(), context(vix=VIX_GAP_15), config(vix_gap={"pct": 20}))).contracts
        == 5
    )
    missing = context(vix=VixState(Unavailable("09:31"), 16.0, Unavailable("none")))
    result = rejected(size(long_setup(), missing, SizingConfig()))
    assert (result.reason, result.step) == ("data_unavailable", "vix_gap_halve")
    assert result.missing == MissingInput(("vix_daily_open",))
    assert len(result.steps) == 3
    assert sized(size(long_setup(), missing, config(vix_gap={"enabled": False}))).contracts == 5


def test_trinity_input_is_named_before_vix_inputs() -> None:
    both = context(
        trinity_agreement=Unavailable("no QQQ"),
        vix=VixState(Unavailable("a"), Unavailable("b"), Unavailable("c")),
    )
    assert rejected(size(long_setup(), both, SizingConfig())).step == "trinity_size_down"


# ---------------------------------------------------------------- Micro_Equivalent cap


@pytest.mark.parametrize(
    ("instrument", "used", "expected"),
    [
        ("MES", 47, 3),  # 50 - 47 = 3 left
        ("MES", 45, 5),  # cap 5 does not bind
        ("ES", 30, 2),  # 20 left = 2 ES (fixed ES 3)
        ("NQ", 39, 1),  # 11 left = 1 NQ
    ],
)
def test_micro_equivalent_cap(instrument: str, used: int, expected: int) -> None:
    cfg = config(fixed={"ES": 3})
    result = sized(size(setup_for(instrument), context(used_micro_equivalents=used), cfg))
    assert result.contracts == expected
    assert result.steps[-1].triggered is (result.steps[-2].contracts > expected)


@pytest.mark.parametrize(("instrument", "used"), [("MES", 50), ("MES", 75), ("ES", 41), ("NQ", 49)])
def test_no_capacity_is_size_zero_naming_the_cap(instrument: str, used: int) -> None:
    result = rejected(
        size(setup_for(instrument), context(used_micro_equivalents=used), SizingConfig())
    )
    assert (result.reason, result.step, result.missing) == ("size_zero", "micro_cap", None)
    assert len(result.steps) == 5
    assert result.steps[-1] == StepOutcome("micro_cap", 0, True)


# ---------------------------------------------------------------- order


def test_steps_run_in_order_each_taking_the_previous_result() -> None:
    cfg = config(fixed={"MES": 10}, big_win={"reduced": {"MES": 6}})
    ctx = context(
        prior_session=PriorSession(Decimal(2000)),
        trinity_agreement=2,
        vix=VIX_GAP_15,
        used_micro_equivalents=49,
    )
    # 10 -> min(10, 6) = 6 -> floor(6 x 0.5) = 3 -> floor(3 / 2) = 1 -> cap 1 = 1.
    result = sized(size(long_setup(), ctx, cfg))
    assert contracts_by_step(result) == (10, 6, 3, 1, 1)
    assert [s.triggered for s in result.steps] == [True, True, True, True, False]
    assert result.base_contracts == 10
    encoded = to_jsonable(result)
    assert isinstance(encoded, dict)
    assert encoded["contracts"] == 1


# ---------------------------------------------------------------- value checks


def test_result_and_context_types_check_their_shape() -> None:
    base = StepOutcome("base_contracts", 5, True)
    steps = (base, *(StepOutcome(s, 5, False) for s in SIZING_STEPS[1:]))
    assert SizedSetup(long_setup(), 5, steps).base_contracts == 5
    with pytest.raises(ValueError, match="all 5 steps"):
        SizedSetup(long_setup(), 5, steps[:4])
    with pytest.raises(ValueError, match="sizing order"):
        SizedSetup(long_setup(), 5, (steps[1], steps[0], *steps[2:]))
    with pytest.raises(ValueError, match="last step"):
        SizedSetup(long_setup(), 4, steps)
    with pytest.raises(ValueError, match="names no missing"):
        SizingRejection(key(), "size_zero", "base_contracts", MissingInput(("x",)), (base,))
    with pytest.raises(ValueError, match="0 contracts"):
        SizingRejection(key(), "size_zero", "base_contracts", None, (base,))
    with pytest.raises(ValueError, match="steps before"):
        SizingRejection(key(), "data_unavailable", "vix_gap_halve", MissingInput(("x",)), (base,))
    with pytest.raises(ValueError, match="names the missing"):
        SizingRejection(key(), "data_unavailable", "big_win_reduce", None, (base,))
    with pytest.raises(ValueError, match="one of"):
        StepOutcome("cap", 1, True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="at most 3"):
        context(trinity_agreement=4)
    with pytest.raises(ValueError, match="integer"):
        context(trinity_agreement=True)
    with pytest.raises(ValueError, match="used_micro_equivalents"):
        context(used_micro_equivalents=-1)
