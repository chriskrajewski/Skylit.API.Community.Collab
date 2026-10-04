"""Examples for the core value types (design "Data Models").

**Validates: Requirements 4.7**
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from fse.engine.types import (
    Bar,
    DarkPoolPrint,
    MissingInput,
    MissingPrice,
    NotApplicable,
    NoTarget,
    Snapshot,
    Unavailable,
    VixState,
)
from fse.timekit import NS_PER_MINUTE, parse_rfc3339

OPEN_1000 = parse_rfc3339("2026-03-05T10:00:00-05:00")


def snapshot(**overrides: Any) -> Snapshot:
    fields: dict[str, Any] = {
        "symbol": "SPX",
        "metric": "gamma",
        "view_id": "view-default",
        "as_of_ns": OPEN_1000,
        "as_of_raw": "2026-03-05T15:00:00Z",
        "spot": 5800.0,
        "previous_close": 5790.5,
        "strikes": (5780.0, 5800.0, 5825.0),
        "values": (-1.5e9, 3.0e9, 2.0e9),
        "node_types": None,
        "expirations": ("2026-03-05",),
        "resolution": "1s",
        "source_endpoint": "range",
        "extra_json": "{}",
    }
    fields.update(overrides)
    return Snapshot(**fields)


def bar(**overrides: Any) -> Bar:
    fields: dict[str, Any] = {
        "instrument": "MES",
        "contract": "MESH6",
        "interval_s": 60,
        "open_ns": OPEN_1000,
        "close_ns": OPEN_1000 + NS_PER_MINUTE,
        "o": 5800.25,
        "h": 5801.0,
        "l": 5799.5,
        "c": 5800.75,
        "v": 1200.0,
        "o_t": 23201,
        "h_t": 23204,
        "l_t": 23198,
        "c_t": 23203,
        "source": "atlas",
    }
    fields.update(overrides)
    return Bar(**fields)


# ---------------------------------------------------------------- Snapshot


def test_snapshot_keeps_parallel_arrays() -> None:
    s = snapshot(node_types=("floor", "king", None))
    assert len(s.strikes) == len(s.values) == 3
    assert s.node_types == ("floor", "king", None)


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"values": (1.0, 2.0)}, "3 strikes but 2 values"),
        ({"node_types": ("king",)}, "3 strikes but 1 node types"),
        ({"metric": "delta"}, "metric"),
        ({"resolution": "5s"}, "resolution"),
        ({"source_endpoint": "cache"}, "source_endpoint"),
    ],
)
def test_snapshot_rejects_malformed_input(overrides: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        snapshot(**overrides)


def test_value_types_are_frozen_and_slotted() -> None:
    s = snapshot()
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.spot = 1.0  # type: ignore[misc]
    assert not hasattr(s, "__dict__")
    assert not hasattr(bar(), "__dict__")


# ---------------------------------------------------------------- Bar


def test_bar_is_stamped_with_open_and_close() -> None:
    b = bar()
    assert b.close_ns - b.open_ns == NS_PER_MINUTE
    assert b.open_ns == parse_rfc3339("2026-03-05T15:00:00Z")


def test_index_bar_has_no_ticks() -> None:
    vix = bar(instrument="VIX", contract="VIX", o_t=None, h_t=None, l_t=None, c_t=None)
    assert vix.c_t is None


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"close_ns": OPEN_1000 + NS_PER_MINUTE - 1}, "close_ns must equal"),
        ({"interval_s": 0}, "positive integer"),
        ({"interval_s": True, "close_ns": OPEN_1000 + 1_000_000_000}, "positive integer"),
        ({"c_t": None}, "all set"),
        ({"source": "csv"}, "source"),
    ],
)
def test_bar_rejects_malformed_input(overrides: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        bar(**overrides)


# ---------------------------------------------------------------- dark pool and VIX


def test_dark_pool_print_and_vix_state() -> None:
    p = DarkPoolPrint("SPY", OPEN_1000, 580.12, 25_000, 14_503_000.0, "TRF")
    assert p.ts_ns == OPEN_1000
    gap = Unavailable("no prior session close")
    vix = VixState(daily_open=18.4, prior_close=gap, last_1m_close=18.9)
    assert isinstance(vix.prior_close, Unavailable)
    assert vix.daily_open == 18.4


# ---------------------------------------------------------------- sentinels


def test_sentinels_are_distinct_values_not_none() -> None:
    values = [
        Unavailable("no snapshot at or before t"),
        MissingInput(("vanna",)),
        MissingPrice("MESH6"),
        NotApplicable(),
        NoTarget(),
    ]
    assert len({type(v) for v in values}) == 5
    assert all(v is not None for v in values)
    assert NotApplicable() == NotApplicable()
    assert NoTarget() == NoTarget()
    not_applicable: object = NotApplicable()
    assert not_applicable != NoTarget()
    assert MissingInput(("gamma", "vanna")).names == ("gamma", "vanna")
    assert Unavailable("x") == Unavailable("x") != Unavailable("y")


@pytest.mark.parametrize(
    ("build", "match"),
    [
        (lambda: Unavailable(" "), "reason"),
        (lambda: MissingInput(()), "at least one"),
        (lambda: MissingInput(("vix_open", "")), "entry"),
        (lambda: MissingPrice(""), "symbol_or_contract"),
    ],
)
def test_sentinels_reject_blank_content(build: Any, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        build()
