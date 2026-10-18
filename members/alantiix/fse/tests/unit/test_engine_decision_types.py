"""Examples for the decision value types (design "Data Models").

**Validates: Requirements 8.11, 10.11, 10.12, 10.13, 13.10, 13.11**
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest

from fse.engine.types import (
    CandidateSetup,
    ChartLevelRef,
    DetectionSkip,
    Fill,
    MissingInput,
    MissingPrice,
    Order,
    SetupInputs,
    SetupKey,
    SnapshotAsOf,
    SourceNodeRef,
    Trade,
    Unavailable,
    direction_sign,
)
from fse.logio.canonical_json import dumps, to_jsonable
from fse.timekit import NS_PER_MINUTE, parse_rfc3339

SESSION = date(2026, 3, 5)
T_1000 = parse_rfc3339("2026-03-05T10:00:00-05:00")
MES_TICK_VALUE = Decimal("1.25")  # Req 13.9, used here only to check the Trade fields
FEE_PER_CONTRACT = Decimal("0.37") + Decimal("0.35")  # commission + exchange fee, per side


def key(**overrides: Any) -> SetupKey:
    fields: dict[str, Any] = {
        "instrument": "MES",
        "pattern": "floor_ceiling_bounce",
        "source_strike": 5780.0,
        "direction": "long",
        "session": SESSION,
        "tap_seq": 1,
    }
    fields.update(overrides)
    return SetupKey(**fields)


def source(**overrides: Any) -> SourceNodeRef:
    # SPX Floor 5780 -> MES 5780.00 (23120 ticks), ES band half-width 5 points.
    fields: dict[str, Any] = {
        "symbol": "SPX",
        "metric": "gamma",
        "strike": 5780.0,
        "value": 2.5e9,
        "level": 23120,
        "band": (5775.0, 5785.0),
    }
    fields.update(overrides)
    return SourceNodeRef(**fields)


def inputs(**overrides: Any) -> SetupInputs:
    fields: dict[str, Any] = {
        "map_as_of": (
            SnapshotAsOf("QQQ", "gamma", T_1000 - 2 * 10**9),
            SnapshotAsOf("SPX", "gamma", T_1000 - 10**9),
            SnapshotAsOf("SPX", "vanna", T_1000 - 10**9),
        ),
        "source_spot": 5791.25,
        "futures_price": 23122,
        "conversion_method": "offset",
        "conversion_factor": 0.5,
        "band_half_width_pts": 5.0,
        "regime": "Positive_Gamma",
        "map_grade": "Neutral_Map",
        "stop_rule": "fixed_ticks",
        "chart_levels": (ChartLevelRef("prior_rth_low", 5779.75),),
    }
    fields.update(overrides)
    return SetupInputs(**fields)


def long_setup(**overrides: Any) -> CandidateSetup:
    # Entry at the level, stop 8 ticks below the lower band edge, 3R Fixed_R target.
    fields: dict[str, Any] = {
        "key": key(),
        "detector_id": "floor_ceiling_bounce",
        "t": T_1000,
        "entry": 23120,
        "stop": 23092,
        "targets": (23204,),
        "exit_mode": "fixed_r",
        "source": source(),
        "inputs": inputs(),
    }
    fields.update(overrides)
    return CandidateSetup(**fields)


def short_setup(**overrides: Any) -> CandidateSetup:
    fields: dict[str, Any] = {
        "key": key(direction="short", source_strike=5825.0),
        "detector_id": "floor_ceiling_bounce",
        "t": T_1000,
        "entry": 23300,
        "stop": 23328,
        "targets": (23216,),
        "exit_mode": "fixed_r",
        "source": source(strike=5825.0, level=23300, band=(5820.0, 5830.0)),
        "inputs": inputs(),
    }
    fields.update(overrides)
    return CandidateSetup(**fields)


def fill(client_id: str, price: int, qty: int, minute: int = 0) -> Fill:
    return Fill(client_id, T_1000 + minute * NS_PER_MINUTE, price, qty, qty * FEE_PER_CONTRACT)


def trade(**overrides: Any) -> Trade:
    fields: dict[str, Any] = {
        "setup_key": key(),
        "entry_fill": fill("c1", 23120, 2),
        "initial_stop": 23092,
        "qty_at_entry": 2,
        "exits": (fill("c2", 23204, 2, minute=5),),
        # 84 ticks x 1.25 x 2 contracts - 4 x 0.72 fees; R = 28 ticks x 1.25 x 2
        "net": Decimal("207.12"),
        "r": Decimal("70.00"),
        "r_multiple": Decimal("207.12") / Decimal("70.00"),
        "reached_tp1": True,
        "mae_r": Decimal("0.25"),
        "mfe_r": Decimal("3.1"),
        "missing_bars": 0,
        "shadow": False,
    }
    fields.update(overrides)
    return Trade(**fields)


# ---------------------------------------------------------------- SetupKey


def test_setup_key_is_a_hashable_identity() -> None:
    assert key() == key()
    assert hash(key()) == hash(key())
    assert key(tap_seq=2) != key()  # a new Tap gives a new Setup_Key (Req 10.11)
    placed = {key()}
    assert key() in placed
    assert key(tap_seq=2) not in placed


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"tap_seq": 0}, "at least 1"),
        ({"tap_seq": True}, "integer"),
        ({"instrument": " "}, "instrument"),
        ({"pattern": ""}, "pattern"),
        ({"direction": "flat"}, "direction"),
        ({"source_strike": float("nan")}, "finite"),
        ({"session": datetime(2026, 3, 5, 9, 30)}, "not a datetime"),
    ],
)
def test_setup_key_rejects_malformed_input(overrides: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        key(**overrides)


def test_direction_sign() -> None:
    assert direction_sign("long") == 1
    assert direction_sign("short") == -1


# ---------------------------------------------------------------- CandidateSetup


def test_priced_setups_keep_the_price_order() -> None:
    long = long_setup()
    assert long.priced
    assert (long.stop, long.entry, long.targets[0]) == (23092, 23120, 23204)
    short = short_setup()
    assert (short.targets[0], short.entry, short.stop) == (23216, 23300, 23328)
    two_targets = long_setup(exit_mode="tp1_partial_be", targets=(23176, 23232))
    assert two_targets.targets == (23176, 23232)


@pytest.mark.parametrize(
    ("build", "match"),
    [
        (lambda: long_setup(stop=23120), "price order"),
        (lambda: long_setup(targets=(23120,)), "price order"),
        (lambda: long_setup(targets=(23000,)), "price order"),
        (lambda: short_setup(stop=23280), "price order"),
        (lambda: short_setup(targets=(23310,)), "price order"),
        (lambda: long_setup(targets=()), "at least one target"),
        (lambda: long_setup(stop=None), "needs an entry, a stop"),
        (lambda: long_setup(inputs=inputs(stop_rule=None)), "stop rule"),
        (lambda: long_setup(entry=23120.0), "entry must be an integer"),
        (lambda: long_setup(source=source(strike=5785.0, value=1.0)), "source_strike"),
        (lambda: long_setup(detector_id=""), "detector_id"),
    ],
)
def test_candidate_setup_rejects_malformed_input(build: Any, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        build()


def unpriced_inputs() -> SetupInputs:
    gap = MissingPrice("MESH6")
    return inputs(futures_price=gap, conversion_factor=gap, band_half_width_pts=gap, stop_rule=None)


def test_unpriced_setup_is_still_emitted_for_no_conversion_price() -> None:
    c = long_setup(
        entry=None,
        stop=None,
        targets=(),
        source=source(level=None, band=None),
        inputs=unpriced_inputs(),
    )
    assert not c.priced
    assert c.source.level is None
    assert c.inputs.conversion_factor == MissingPrice("MESH6")


def test_unpriced_setup_has_no_prices() -> None:
    with pytest.raises(ValueError, match="unpriced"):
        long_setup(source=source(level=None, band=None), inputs=unpriced_inputs())


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"band": None}, "both be set or both be None"),
        ({"level": None}, "both be set or both be None"),
        ({"band": (5781.0, 5785.0)}, "must contain level"),
        ({"band": (5785.0, 5775.0)}, "must contain level"),
        ({"metric": "delta"}, "metric"),
        ({"value": float("inf")}, "finite"),
    ],
)
def test_source_node_ref_rejects_malformed_input(overrides: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        source(**overrides)


# ---------------------------------------------------------------- SetupInputs


def test_setup_inputs_hold_typed_missing_values() -> None:
    i = inputs(
        regime=MissingInput(("vanna",)),
        map_grade=MissingInput(("gamma",)),
        chart_levels=(ChartLevelRef("ib30_low", Unavailable("before 10:00")),),
    )
    assert isinstance(i.regime, MissingInput)
    assert isinstance(i.chart_levels[0].value, Unavailable)


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"map_as_of": tuple(reversed(inputs().map_as_of))}, "sorted"),
        ({"map_as_of": (SnapshotAsOf("SPX", "gamma", 1),) * 2}, "no repeats"),
        ({"regime": "Rainbow_Road"}, "regime"),
        ({"map_grade": "B_Map"}, "map_grade"),
        ({"stop_rule": "trailing"}, "stop_rule"),
        ({"conversion_method": "spread"}, "conversion_method"),
        ({"futures_price": 5800.25}, "futures_price must be an integer"),
        ({"band_half_width_pts": -0.25}, "negative"),
        ({"chart_levels": (ChartLevelRef("pdl", 1.0), ChartLevelRef("pdl", 2.0))}, "distinct"),
    ],
)
def test_setup_inputs_reject_malformed_input(overrides: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        inputs(**overrides)


def test_detection_skip_names_key_time_and_condition() -> None:
    skip = DetectionSkip(key(), T_1000, "no_target")
    assert (skip.key, skip.t, skip.condition) == (key(), T_1000, "no_target")
    with pytest.raises(ValueError, match="condition"):
        DetectionSkip(key(), T_1000, "no_target_node")  # type: ignore[arg-type]


# ---------------------------------------------------------------- orders and fills


@pytest.mark.parametrize(
    ("kind", "price", "match"),
    [
        ("market", 23120, "has no price"),
        ("limit", None, "needs a price"),
        ("stop", None, "needs a price"),
    ],
)
def test_order_price_matches_its_kind(kind: str, price: int | None, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        Order("c1", key(), "MES", "buy", kind, 1, price, T_1000, "entry")  # type: ignore[arg-type]


def test_order_examples() -> None:
    entry = Order("c1", key(), "MES", "buy", "limit", 2, 23120, T_1000, "entry")
    flatten = Order("c9", None, "MES", "sell", "market", 2, None, T_1000, "exit")
    assert entry.price == 23120
    assert flatten.setup_key is None
    with pytest.raises(ValueError, match="at least 1"):
        Order("c1", key(), "MES", "buy", "limit", 0, 23120, T_1000, "entry")
    with pytest.raises(ValueError, match="role"):
        Order("c1", key(), "MES", "buy", "limit", 1, 23120, T_1000, "tp3")  # type: ignore[arg-type]


def test_fill_fees_are_exact_decimals() -> None:
    assert fill("c1", 23120, 3).fees == Decimal("2.16")
    with pytest.raises(ValueError, match="finite Decimal"):
        Fill("c1", T_1000, 23120, 1, 0.72)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="negative"):
        Fill("c1", T_1000, 23120, 1, Decimal("-0.01"))
    with pytest.raises(ValueError, match="finite Decimal"):
        Fill("c1", T_1000, 23120, 1, Decimal("NaN"))


# ---------------------------------------------------------------- Trade


def _net_and_r(t: Trade) -> tuple[Decimal, Decimal]:
    """Req 13.10 and 13.11 from the Trade's own fields."""
    sign = direction_sign(t.setup_key.direction)
    gross = sum(
        ((x.price - t.entry_fill.price) * sign * MES_TICK_VALUE * x.qty for x in t.exits),
        Decimal(0),
    )
    fees = t.entry_fill.fees + sum((x.fees for x in t.exits), Decimal(0))
    r = abs(t.entry_fill.price - t.initial_stop) * MES_TICK_VALUE * t.qty_at_entry
    return gross - fees, r


def test_full_exit_at_the_initial_stop_with_zero_costs_is_minus_one_r() -> None:
    entry = Fill("c1", T_1000, 23120, 2, Decimal("0.00"))
    stopped = Fill("c2", T_1000 + NS_PER_MINUTE, 23092, 2, Decimal("0.00"))
    t = trade(
        entry_fill=entry,
        exits=(stopped,),
        net=Decimal("-70.00"),
        r=Decimal("70.00"),
        r_multiple=Decimal(-1),
        reached_tp1=False,
    )
    net, r = _net_and_r(t)
    assert (net, r) == (t.net, t.r)
    assert net / r == t.r_multiple == Decimal(-1)


def test_partial_exits_net_is_exact() -> None:
    # TP1 1 contract at +56 ticks, TP2 1 contract at +112 ticks, 0.72 per contract per side.
    t = trade(exits=(fill("c3", 23176, 1, minute=3), fill("c4", 23232, 1, minute=9)))
    net, r = _net_and_r(t)
    assert _net_and_r(trade()) == (t.net, t.r)  # one full exit at +84 ticks nets the same
    assert net == t.net == Decimal("207.12")  # 168 ticks x 1.25 - 4 x 0.72
    assert net / r == t.r_multiple


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"exits": ()}, "at least one exit"),
        ({"exits": (fill("c2", 23204, 1),)}, "total 1 contracts"),
        ({"r": Decimal(0)}, "above 0"),
        ({"net": 206.12}, "finite Decimal"),
        ({"mae_r": Decimal("-0.1")}, "must not be negative"),
        ({"missing_bars": -1}, "at least 0"),
        ({"qty_at_entry": 0}, "at least 1"),
    ],
)
def test_trade_rejects_malformed_input(overrides: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        trade(**overrides)


# ---------------------------------------------------------------- encoding and shape


def test_decision_types_encode_as_canonical_json() -> None:
    c = to_jsonable(long_setup())
    assert isinstance(c, dict)
    assert c["key"] == {
        "direction": "long",
        "instrument": "MES",
        "pattern": "floor_ceiling_bounce",
        "session": "2026-03-05",
        "source_strike": 5780.0,
        "tap_seq": 1,
    }
    assert c["source"] == {
        "band": [5775.0, 5785.0],
        "level": 23120,
        "metric": "gamma",
        "strike": 5780.0,
        "symbol": "SPX",
        "value": 2.5e9,
    }
    unpriced = long_setup(
        entry=None,
        stop=None,
        targets=(),
        source=source(level=None, band=None),
        inputs=unpriced_inputs(),
    )
    encoded = to_jsonable(unpriced)
    assert isinstance(encoded, dict)
    assert encoded["entry"] is None
    unpriced_inputs_json = encoded["inputs"]
    assert isinstance(unpriced_inputs_json, dict)
    assert unpriced_inputs_json["futures_price"] == {"symbol_or_contract": "MESH6"}
    assert unpriced_inputs_json["stop_rule"] is None
    t = to_jsonable(trade())
    assert isinstance(t, dict)
    assert (t["net"], t["r"], t["mfe_r"]) == ("207.12", "70.00", "3.1")
    assert t["entry_fill"] == {
        "bar_open_ns": T_1000,
        "client_id": "c1",
        "fees": "1.44",
        "price": 23120,
        "qty": 2,
    }
    builders: tuple[Any, ...] = (
        long_setup,
        short_setup,
        lambda: DetectionSkip(key(), T_1000, "price_order"),
        lambda: Order("c1", key(), "MES", "buy", "limit", 2, 23120, T_1000, "entry"),
        trade,
    )
    for build in builders:
        assert dumps(build()) == dumps(build())  # equal values, identical bytes


def test_decision_types_are_frozen_and_slotted() -> None:
    values = (
        key(),
        source(),
        inputs(),
        long_setup(),
        DetectionSkip(key(), T_1000, "price_order"),
        Order("c1", key(), "MES", "buy", "limit", 2, 23120, T_1000, "entry"),
        fill("c1", 23120, 2),
        trade(),
    )
    for value in values:
        assert not hasattr(value, "__dict__")
    with pytest.raises(dataclasses.FrozenInstanceError):
        key().tap_seq = 2  # type: ignore[misc]
