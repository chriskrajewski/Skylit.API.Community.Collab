"""``PluginPaperBroker`` and ``ModifyStop`` (plugin-api-r1, plan decision 3, test items 8-15).

The new stop is a ``SimBook.modify`` version effective from the next bar the book
has not processed (last bar open + 60 s), not from ``now``: here ``now`` is two
seconds after that bar's open, as for a live bar received after its close.
"""

from __future__ import annotations

from typing import Any

import pytest

from fse.engine.types import Direction, Fill
from fse.live.host.broker import PluginPaperBroker
from fse.live.plugins import ModifyStop, OrderUpdate, TargetLeg
from fse.timekit import NS_PER_SECOND
from tests.unit.test_plugin_paper_broker import T0, M, bar, broker, market, statuses

STOP = "mx:t1:stop"


def open_trade(direction: Direction = "long", **kw: Any) -> tuple[PluginPaperBroker, list[Any]]:
    """``t1`` filled on the 10:00 bar: long at 80_001 (stop 79_992, tp1 80_012), short mirror."""
    b, logs = broker(**kw.pop("broker", {}))
    b.route(market(direction=direction, **kw), T0)
    ups = b.on_bar(bar(0, 80_000))
    assert statuses(ups) == [("t1", "filled_entry", None)]
    return b, logs


def now_after(minute: int) -> int:
    """Two seconds after the open of the bar at ``minute`` (it has not been processed)."""
    return T0 + minute * M + 2 * NS_PER_SECOND


def stop_price(b: PluginPaperBroker) -> int | None:
    latest = b.book.orders[STOP].latest
    assert latest is not None
    return latest.price


def exit_fills(ups: list[OrderUpdate]) -> list[tuple[str | None, int]]:
    out: list[tuple[str | None, int]] = []
    for u in ups:
        if u.status == "filled_exit":
            assert isinstance(u.fill, Fill)
            out.append((u.role, u.fill.price))
    return out


# ---------------------------------------------------------------- 8: tighten


def test_tighten_a_long_applies_from_the_next_bar_open() -> None:
    b, _ = open_trade()
    (u,) = b.route(ModifyStop("t1", 79_996), now_after(1))
    assert (u.tag, u.status, u.reason, u.role) == ("t1", "accepted", None, "stop")
    assert u.detail == {
        "stop_price": 79_996,
        "previous_stop": 79_992,
        "effective_ns": T0 + M,
        "unchanged": False,
    }
    assert b.book.orders[STOP].versions[-1][0] == T0 + M
    # the next bar touches the new level only: it exits there (at_stop - slip 1)
    ups = b.on_bar(bar(1, 80_000, 80_004, 79_996, 80_000))
    assert exit_fills(ups) == [("stop", 79_995)]
    assert statuses(ups)[-1] == ("t1", "closed", None)


def test_without_the_modify_the_same_bar_does_not_exit() -> None:
    b, _ = open_trade()
    assert b.on_bar(bar(1, 80_000, 80_004, 79_996, 80_000)) == []
    assert b.open_tags() == ("t1",)


def test_a_bar_above_the_new_stop_does_not_exit() -> None:
    b, _ = open_trade()
    b.route(ModifyStop("t1", 79_996), now_after(1))
    assert b.on_bar(bar(1, 80_000, 80_004, 79_997, 80_000)) == []
    assert stop_price(b) == 79_996


def test_tighten_a_short() -> None:
    b, _ = open_trade("short")
    assert stop_price(b) == 80_008
    (u,) = b.route(ModifyStop("t1", 80_004), now_after(1))
    assert (u.status, u.role, u.detail) == (
        "accepted",
        "stop",
        {"stop_price": 80_004, "previous_stop": 80_008, "effective_ns": T0 + M, "unchanged": False},
    )
    assert b.on_bar(bar(1, 80_000, 80_003, 79_996, 80_000)) == []
    ups = b.on_bar(bar(2, 80_000, 80_004, 79_996, 80_000))
    assert exit_fills(ups) == [("stop", 80_005)]


# ---------------------------------------------------------------- 9: loosen


@pytest.mark.parametrize(
    ("direction", "price", "kept"), [("long", 79_990, 79_992), ("short", 80_010, 80_008)]
)
def test_loosen_is_rejected(direction: Direction, price: int, kept: int) -> None:
    b, _ = open_trade(direction)
    (u,) = b.route(ModifyStop("t1", price), now_after(1))
    assert (u.status, u.reason, u.role) == ("rejected", "stop_loosen", "stop")
    assert stop_price(b) == kept
    assert len(b.book.orders[STOP].versions) == 1


# ---------------------------------------------------------------- 10: not open, invalid


def test_unknown_and_pending_tags_are_not_open() -> None:
    b, _ = broker()
    assert statuses(b.route(ModifyStop("nope", 1), T0)) == [("nope", "rejected", "not_open")]
    b.route(market(), T0)  # working, not filled yet
    (u,) = b.route(ModifyStop("t1", 80_000), T0)
    assert (u.status, u.reason, u.role) == ("rejected", "not_open", "stop")


def test_a_closed_trade_is_not_open() -> None:
    b, _ = open_trade()
    ups = b.on_bar(bar(1, 80_002, 80_014, 80_000, 80_010))  # tp1 trades through
    assert statuses(ups)[-1] == ("t1", "closed", None)
    assert statuses(b.route(ModifyStop("t1", 80_000), now_after(2))) == [
        ("t1", "rejected", "not_open")
    ]


@pytest.mark.parametrize("bad", ["79996", True, 79_996.0, None])
def test_a_non_int_stop_is_invalid_order(bad: object) -> None:
    b, _ = open_trade()
    (u,) = b.route(ModifyStop("t1", bad), now_after(1))  # type: ignore[arg-type]
    assert (u.status, u.reason, u.role) == ("rejected", "invalid_order", "stop")
    assert u.detail is not None
    assert "error" in u.detail
    assert len(b.book.orders[STOP].versions) == 1


# ---------------------------------------------------------------- 11: equal price


def test_the_current_price_is_an_accepted_no_op() -> None:
    b, _ = open_trade()
    before = b.book
    (u,) = b.route(ModifyStop("t1", 79_992), now_after(1))
    assert (u.status, u.reason, u.role) == ("accepted", None, "stop")
    assert u.detail == {
        "stop_price": 79_992,
        "previous_stop": 79_992,
        "effective_ns": None,
        "unchanged": True,
    }
    assert b.book is before
    assert len(b.book.orders[STOP].versions) == 1


# ---------------------------------------------------------------- 12: through the market


@pytest.mark.parametrize(("stop_fill", "price"), [("at_stop", 80_001), ("gap_open", 79_999)])
def test_a_stop_through_the_market_fills_by_the_stop_fill_rule(stop_fill: str, price: int) -> None:
    b, _ = open_trade(broker={"stop_fill": stop_fill})
    # a long stop above the next bar's open (80_000)
    assert b.route(ModifyStop("t1", 80_002), now_after(1))[0].status == "accepted"
    ups = b.on_bar(bar(1, 80_000, 80_004, 79_998, 80_000))
    assert exit_fills(ups) == [("stop", price)]


# ---------------------------------------------------------------- 13: two legs


def test_two_leg_targets_are_untouched_and_the_stop_closes_the_rest() -> None:
    legs = (TargetLeg("tp1", 1, 12), TargetLeg("tp2", 1, 40))
    b, _ = open_trade(targets=legs)
    targets = {c: b.book.orders[c] for c in ("mx:t1:tp1", "mx:t1:tp2")}
    assert b.route(ModifyStop("t1", 80_001), now_after(1))[0].status == "accepted"
    assert {c: b.book.orders[c] for c in targets} == targets
    ups = b.on_bar(bar(1, 80_005, 80_013, 80_004, 80_010))
    assert exit_fills(ups) == [("tp1", 80_012)]
    assert b.open_tags() == ("t1",)
    assert b.book.orders["mx:t1:tp2"] == targets["mx:t1:tp2"]
    ups = b.on_bar(bar(2, 80_005, 80_006, 80_001, 80_002))
    assert exit_fills(ups) == [("stop", 80_000)]
    closed = ups[-1].trade
    assert closed is not None
    assert [(f.price, f.qty) for f in closed.exits] == [(80_012, 1), (80_000, 1)]


# ---------------------------------------------------------------- 14: trailing


def test_a_trailing_sequence_exits_at_the_last_level_and_keeps_the_initial_stop() -> None:
    b, _ = open_trade(targets=(TargetLeg("tp1", 2, 200),))
    levels: list[int] = []
    for i in range(1, 5):
        base = 80_000 + 4 * i
        assert b.on_bar(bar(i, base, base + 3, base - 1, base + 2)) == []
        level = base - 2
        levels.append(level)
        (u,) = b.route(ModifyStop("t1", level), now_after(i + 1))
        assert u.status == "accepted"
        assert u.detail is not None
        assert u.detail["effective_ns"] == T0 + (i + 1) * M
    assert stop_price(b) == levels[-1] == 80_014
    ups = b.on_bar(bar(5, 80_016, 80_017, 80_000, 80_001))
    assert exit_fills(ups) == [("stop", 80_013)]
    closed = ups[-1].trade
    assert closed is not None
    assert closed.initial_stop == 79_992
    assert closed.r_multiple > 0


# ---------------------------------------------------------------- 15: two modifies


def test_two_modifies_before_the_next_bar_the_second_wins() -> None:
    b, _ = open_trade()
    first = b.route(ModifyStop("t1", 79_994), now_after(1))
    second = b.route(ModifyStop("t1", 79_996), now_after(1))
    assert [u.detail for u in (*first, *second)] == [
        {"stop_price": 79_994, "previous_stop": 79_992, "effective_ns": T0 + M, "unchanged": False},
        {"stop_price": 79_996, "previous_stop": 79_994, "effective_ns": T0 + M, "unchanged": False},
    ]
    versions = b.book.orders[STOP].versions
    assert [(at, o.price if o else None) for at, o in versions] == [(T0, 79_992), (T0 + M, 79_996)]
    ups = b.on_bar(bar(1, 80_000, 80_004, 79_995, 80_000))
    assert exit_fills(ups) == [("stop", 79_995)]
