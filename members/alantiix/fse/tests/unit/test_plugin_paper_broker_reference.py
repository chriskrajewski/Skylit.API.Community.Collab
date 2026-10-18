"""``PluginPaperBroker`` with ``EnterMarket.reference_t`` (plugin-api-r1 R1).

Legs from the reference, ``expired``/``reference_gap`` when the fill bar gaps to a
leg, ``fill_bar_missing`` still wins for ``exact_bar_open``, ``invalid_order`` for a
non-integer reference, and ``reference_t=None`` behaves exactly like today.
"""

from __future__ import annotations

from typing import Any

import pytest

from fse.engine.types import Direction, Ticks
from fse.live.plugins import OrderUpdate, TargetLeg
from fse.timekit import NS_PER_SECOND
from tests.unit.test_plugin_paper_broker import T0, bar, broker, market, statuses

R = 80_000


def ref_market(direction: Direction = "long", **kw: Any) -> Any:
    args: dict[str, Any] = {"direction": direction, "exact_bar_open": False, "reference_t": R}
    args.update(kw)
    return market(**args)


def test_reference_bracket_fills_at_open_plus_slip_with_legs_from_the_reference() -> None:
    b, _ = broker()
    assert statuses(b.route(ref_market(), T0)) == [("t1", "accepted", None)]
    ups = b.on_bar(bar(0, R + 2, R + 4, R, R + 3))
    assert statuses(ups) == [("t1", "filled_entry", None)]
    assert ups[0].fill is not None
    assert ups[0].fill.price == R + 3
    orders = {o.client_id: o.price for o in b.book.working_orders()}
    assert orders == {"mx:t1:stop": R - 8, "mx:t1:tp1": R + 12}


@pytest.mark.parametrize(
    ("direction", "o"),
    [
        ("long", R - 8),  # open at the stop
        ("long", R - 20),  # open past the stop
        ("long", R + 12),  # open at the target
        ("long", R + 11),  # open + slip at the target
        ("short", R + 8),
        ("short", R + 20),
        ("short", R - 12),
        ("short", R - 11),  # open - slip at the target
    ],
)
def test_a_gap_to_a_leg_expires_with_reference_gap(direction: Direction, o: Ticks) -> None:
    b, _ = broker()
    b.route(ref_market(direction), T0)
    ups = b.on_bar(bar(0, o))
    assert statuses(ups) == [("t1", "expired", "reference_gap")]
    assert b.book.orders == {}
    assert b.book.market_brackets == {}
    assert b.book.trades == {}
    assert b.working_tags() == ()


def test_exact_bar_open_still_reports_fill_bar_missing() -> None:
    b, _ = broker()
    b.route(ref_market(exact_bar_open=True), T0)
    ups = b.on_bar(bar(1, R))  # the 10:00 bar never came
    assert statuses(ups) == [("t1", "expired", "fill_bar_missing")]


@pytest.mark.parametrize("bad", ["x", True])
def test_a_non_int_reference_is_invalid_order(bad: object) -> None:
    b, _ = broker()
    assert statuses(b.route(ref_market(reference_t=bad), T0)) == [
        ("t1", "rejected", "invalid_order")
    ]
    assert b.book.orders == {}


def _default_run(**kw: Any) -> list[OrderUpdate]:
    b, _ = broker()
    out = list(b.route(market(**kw), T0 + 5 * NS_PER_SECOND))
    out += b.on_bar(bar(0, 80_000, 80_004, 79_996, 80_002))
    out += b.on_bar(bar(1, 80_002, 80_014, 80_000, 80_010))
    return out


def test_reference_none_is_todays_market_bracket() -> None:
    explicit = _default_run(reference_t=None)
    assert explicit == _default_run()
    assert statuses(explicit) == [
        ("t1", "accepted", None),
        ("t1", "filled_entry", None),
        ("t1", "filled_exit", None),
        ("t1", "closed", None),
    ]
    fills = [u.fill.price for u in explicit if u.fill is not None and u.status != "closed"]
    assert fills == [80_001, 80_012]
    assert market(reference_t=None).targets == (TargetLeg("tp1", 2, 12),)
