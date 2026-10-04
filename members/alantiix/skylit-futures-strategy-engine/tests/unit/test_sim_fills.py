"""Unit tests for the Fill_Simulator (``fse.sim.fills``, design §13).

Worked examples with exact Decimal arithmetic: the contract table, R_Multiple
of exactly -1 at the initial stop with zero costs, partial exits with fees,
fill timing, trade-through, gapped stops, market exits, the stop-before-target
rule, missing bars and the account close-out.

**Validates: Requirements 5.5, 5.6, 13.1, 13.3, 13.4, 13.5, 13.6, 13.7, 13.9,
13.10, 13.11, 13.12**
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, time
from decimal import Decimal

import pytest

from fse.config.schema.fills import FillsConfig
from fse.engine import sizing
from fse.engine.types import Bar, Direction, Order, OrderKind, OrderRole, SetupKey, Side, Ticks
from fse.sim.fills import (
    POINT_VALUE_USD,
    TICK_VALUE_USD,
    FillEvent,
    SimBook,
    close_all,
    on_bar,
)
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, Instant, ny_instant

SESSION = date(2026, 3, 5)
T0 = ny_instant(SESSION, time(9, 30))
RTH = (T0, ny_instant(SESSION, time(16, 0)))


def at(minute: int, second: int = 0) -> Instant:
    return T0 + minute * NS_PER_MINUTE + second * NS_PER_SECOND


def bar(minute: int, o: Ticks, h: Ticks, low: Ticks, c: Ticks, instrument: str = "MES") -> Bar:
    start = at(minute)
    return Bar(
        instrument=instrument,
        contract=f"{instrument}H6",
        interval_s=60,
        open_ns=start,
        close_ns=start + NS_PER_MINUTE,
        o=o / 4,
        h=h / 4,
        l=low / 4,
        c=c / 4,
        v=100.0,
        o_t=o,
        h_t=h,
        l_t=low,
        c_t=c,
        source="atlas",
    )


def fills_cfg(
    through: int = 1, slippage: int = 1, commission: str = "0", fee: str = "0"
) -> FillsConfig:
    costs = {"commission": commission, "exchange_fee": fee}
    return FillsConfig.model_validate(
        {
            "trade_through_ticks": through,
            "slippage_ticks": slippage,
            "costs": {name: costs for name in ("MES", "MNQ", "ES", "NQ")},
        }
    )


def setup_key(direction: Direction = "long", instrument: str = "MES", tap: int = 1) -> SetupKey:
    return SetupKey(instrument, "floor_ceiling_bounce", 5800.0, direction, SESSION, tap)


def order(
    cid: str,
    key: SetupKey,
    role: OrderRole,
    kind: OrderKind,
    qty: int,
    price: Ticks | None,
    placed_at: Instant,
) -> Order:
    entry_side: Side = "buy" if key.direction == "long" else "sell"
    exit_side: Side = "sell" if entry_side == "buy" else "buy"
    side = entry_side if role == "entry" else exit_side
    return Order(cid, key, key.instrument, side, kind, qty, price, placed_at, role)


def place(
    book: SimBook,
    key: SetupKey,
    *,
    entry: Ticks,
    stop: Ticks,
    targets: Iterable[tuple[OrderRole, Ticks, int]] = (),
    qty: int = 5,
    placed_at: Instant = T0,
    kind: OrderKind = "limit",
    prefix: str = "",
) -> SimBook:
    """A bracket with client ids ``{prefix}entry``, ``{prefix}stop`` and ``{prefix}tp1``..."""
    return book.submit_bracket(
        order(f"{prefix}entry", key, "entry", kind, qty, entry, placed_at),
        order(f"{prefix}stop", key, "stop", "stop", qty, stop, placed_at),
        [
            order(f"{prefix}{role}", key, role, "limit", n, price, placed_at)
            for role, price, n in targets
        ],
    )


def run(book: SimBook, bars: Iterable[Bar], cfg: FillsConfig) -> tuple[SimBook, list[FillEvent]]:
    events: list[FillEvent] = []
    for b in bars:
        book, new = on_bar(book, b, cfg, rth=RTH)
        events += new
    return book, events


def summary(events: Iterable[FillEvent]) -> list[tuple[str, Instant, Ticks, int]]:
    return [(e.fill.client_id, e.fill.bar_open_ns, e.fill.price, e.fill.qty) for e in events]


LONG = setup_key("long")
SHORT = setup_key("short")


# ---------------------------------------------------------------- contract values


def test_contract_values() -> None:
    assert dict(TICK_VALUE_USD) == {
        "MES": Decimal("1.25"),
        "MNQ": Decimal("0.50"),
        "ES": Decimal("12.50"),
        "NQ": Decimal("5.00"),
    }
    assert dict(POINT_VALUE_USD) == {
        "MES": Decimal(5),
        "MNQ": Decimal(2),
        "ES": Decimal(50),
        "NQ": Decimal(20),
    }
    # The Position_Sizer (engine, which cannot import fse.sim) keeps the same table.
    assert dict(sizing.TICK_VALUE_USD) == dict(TICK_VALUE_USD)


# ---------------------------------------------------------------- accounting


def test_full_exit_at_the_initial_stop_with_zero_costs_is_exactly_minus_one_r() -> None:
    cfg = fills_cfg(through=0, slippage=0)
    book = place(SimBook(), LONG, entry=20000, stop=19984, targets=[("tp1", 20032, 5)])
    book, events = run(
        book,
        [bar(0, 20004, 20006, 19999, 20002), bar(1, 19990, 19995, 19980, 19982)],
        cfg,
    )
    assert summary(events) == [("entry", at(0), 20000, 5), ("stop", at(1), 19984, 5)]
    trade = events[-1].closed
    assert trade is not None
    assert trade.net == Decimal("-100.00")  # -16 ticks x $1.25 x 5
    assert trade.r == Decimal("100.00")
    assert trade.r_multiple == Decimal(-1)
    assert str(trade.r_multiple) == "-1"
    assert (trade.mae_r, trade.mfe_r) == (Decimal("1.25"), Decimal("0.375"))  # 20 and 6 of 16
    assert (trade.initial_stop, trade.qty_at_entry, trade.reached_tp1) == (19984, 5, False)
    assert trade.missing_bars == 0
    assert not trade.shadow
    # OCO: the target went with the trade.
    assert book.orders == {}
    assert book.brackets == {}
    assert book.trades == {}


def test_partial_exits_charge_fees_per_contract_per_fill() -> None:
    key = setup_key("long", "MNQ")
    cfg = fills_cfg(commission="0.37", fee="0.35")
    book = place(
        SimBook(),
        key,
        entry=80000,
        stop=79960,
        targets=[("tp1", 80080, 2), ("tp2", 80160, 3)],
    )
    bars = [
        bar(0, 80010, 80012, 79998, 80004, "MNQ"),
        bar(1, 80050, 80081, 80040, 80070, "MNQ"),
        bar(2, 80100, 80161, 80090, 80150, "MNQ"),
    ]
    book, events = run(book, bars, cfg)
    assert summary(events) == [
        ("entry", at(0), 80000, 5),
        ("tp1", at(1), 80080, 2),
        ("tp2", at(2), 80160, 3),
    ]
    assert [e.fill.fees for e in events] == [Decimal("3.60"), Decimal("1.44"), Decimal("2.16")]
    assert [e.gross for e in events] == [Decimal(0), Decimal("80.00"), Decimal("240.00")]
    trade = events[-1].closed
    assert trade is not None
    assert events[1].closed is None
    # Round trip of 5 contracts: 2 x 5 x ($0.37 + $0.35) = $7.20.
    assert sum((e.fill.fees for e in events), Decimal(0)) == Decimal("7.20")
    assert trade.net == Decimal("312.80") == sum((e.net for e in events), Decimal(0))
    assert trade.r == Decimal("100.00")  # 40 ticks x $0.50 x 5
    assert trade.r_multiple == Decimal("3.128")
    assert trade.reached_tp1
    assert (trade.mae_r, trade.mfe_r) == (Decimal("0.05"), Decimal("4.025"))


def test_short_trade_pnl_has_the_direction_sign() -> None:
    cfg = fills_cfg(commission="0.50", fee="0.25")
    book = place(SimBook(), SHORT, entry=20000, stop=20016, targets=[("tp1", 19968, 2)], qty=2)
    _, events = run(
        book,
        [bar(0, 19996, 20001, 19990, 19998), bar(1, 19980, 19985, 19967, 19970)],
        cfg,
    )
    assert summary(events) == [("entry", at(0), 20000, 2), ("tp1", at(1), 19968, 2)]
    trade = events[-1].closed
    assert trade is not None
    assert events[-1].gross == Decimal("80.00")  # 32 ticks x $1.25 x 2
    assert trade.net == Decimal("77.00")  # less 2 fills x 2 contracts x $0.75
    assert trade.r_multiple == Decimal("1.925")  # 77 / 40


# ---------------------------------------------------------------- timing (Req 5.5-5.6)


def test_an_order_fills_only_on_bars_opening_at_or_after_placement() -> None:
    book = place(SimBook(), LONG, entry=20000, stop=19984, placed_at=at(1))
    book, events = run(book, [bar(0, 19990, 19995, 19980, 19992)], fills_cfg())
    assert events == []
    assert "entry" in book.orders
    _, events = run(book, [bar(1, 20004, 20006, 19999, 20002)], fills_cfg())
    assert summary(events) == [("entry", at(1), 20000, 5)]


def test_a_cancel_applies_only_to_bars_opening_at_or_after_it() -> None:
    placed = place(SimBook(), LONG, entry=20000, stop=19984, targets=[("tp1", 20032, 5)])
    # Cancelled at 09:30:30: the 09:30 bar opened earlier, so its fill stands.
    book = placed.cancel("entry", at(0, 30))
    book, events = run(book, [bar(0, 20004, 20006, 19999, 20002)], fills_cfg())
    assert summary(events) == [("entry", at(0), 20000, 5)]
    assert set(book.orders) == {"stop", "tp1"}

    # Cancelled at 09:31: the 09:31 bar trades through but nothing fills, and the
    # stop and target go with the entry.
    book = placed.cancel("entry", at(1))
    assert book.working_entry_contracts() == {}
    book, events = run(
        book,
        [bar(0, 20010, 20012, 20004, 20008), bar(1, 20004, 20006, 19990, 19995)],
        fills_cfg(),
    )
    assert events == []
    assert book.orders == {}
    assert book.brackets == {}


def test_a_price_change_applies_only_to_bars_opening_at_or_after_it() -> None:
    book = place(SimBook(), LONG, entry=20000, stop=19984).modify("entry", 19990, at(1))
    bars = [
        bar(0, 20010, 20012, 20005, 20008),  # above both prices
        bar(1, 20000, 20002, 19995, 19996),  # through the old price only
        bar(2, 19995, 19996, 19989, 19992),  # through the new price
    ]
    _, events = run(book, bars, fills_cfg())
    assert summary(events) == [("entry", at(2), 19990, 5)]


# ---------------------------------------------------------------- fill rules


def test_limit_needs_the_trade_through_distance_and_fills_at_its_price() -> None:
    cfg = fills_cfg(through=2)
    book = place(SimBook(), LONG, entry=20000, stop=19960)
    book, events = run(book, [bar(0, 20004, 20006, 19999, 20002)], cfg)  # 1 tick through
    assert events == []
    _, events = run(book, [bar(1, 20004, 20006, 19998, 20002)], cfg)  # 2 ticks through
    assert summary(events) == [("entry", at(1), 20000, 5)]
    # A gap open past the limit still fills at the limit price, not the open.
    gapped = place(SimBook(), LONG, entry=20000, stop=19960)
    _, events = run(gapped, [bar(0, 19980, 19985, 19975, 19982)], cfg)
    assert summary(events) == [("entry", at(0), 20000, 5)]


def test_stop_fills_at_the_stop_or_the_gapped_open_less_slippage() -> None:
    cfg = fills_cfg(slippage=1)
    long_open = run(
        place(SimBook(), LONG, entry=20000, stop=19984), [bar(0, 20004, 20006, 19999, 20002)], cfg
    )[0]
    _, touched = run(long_open, [bar(1, 19990, 19992, 19984, 19986)], cfg)
    _, gapped = run(long_open, [bar(1, 19980, 19983, 19976, 19978)], cfg)
    assert summary(touched)[-1] == ("stop", at(1), 19983, 5)
    assert summary(gapped)[-1] == ("stop", at(1), 19979, 5)

    short_open = run(
        place(SimBook(), SHORT, entry=20000, stop=20016), [bar(0, 19996, 20001, 19990, 19998)], cfg
    )[0]
    _, gapped = run(short_open, [bar(1, 20020, 20024, 20018, 20022)], cfg)
    assert summary(gapped)[-1] == ("stop", at(1), 20021, 5)


def test_market_exit_fills_at_the_next_open_plus_slippage_for_the_open_contracts() -> None:
    cfg = fills_cfg(slippage=2)
    book = place(SimBook(), LONG, entry=20000, stop=19960)
    book, _ = run(book, [bar(0, 20004, 20006, 19999, 20002)], cfg)
    book = book.submit_exit(order("exit", LONG, "exit", "market", 9, None, at(1, 30)))
    book, events = run(book, [bar(1, 20010, 20012, 20006, 20008)], cfg)  # opened before the exit
    assert events == []
    book, events = run(book, [bar(2, 20020, 20022, 20012, 20016)], cfg)
    assert summary(events) == [("exit", at(2), 20018, 5)]
    assert events[0].gross == Decimal("112.50")  # 18 ticks x $1.25 x 5
    assert book.trades == {}


def test_stop_is_checked_on_the_entry_bar_and_targets_from_the_next_bar() -> None:
    cfg = fills_cfg()
    # The entry bar reaches the stop: stopped out on the entry bar.
    book = place(SimBook(), LONG, entry=20000, stop=19984, targets=[("tp1", 20032, 5)])
    _, events = run(book, [bar(0, 20004, 20040, 19980, 19990)], cfg)
    assert summary(events) == [("entry", at(0), 20000, 5), ("stop", at(0), 19983, 5)]

    # The entry bar reaches the target: it waits for the next bar.
    book = place(SimBook(), LONG, entry=20000, stop=19984, targets=[("tp1", 20032, 5)])
    book, events = run(book, [bar(0, 20004, 20040, 19999, 20030)], cfg)
    assert summary(events) == [("entry", at(0), 20000, 5)]
    _, events = run(book, [bar(1, 20030, 20036, 20028, 20034)], cfg)
    assert summary(events) == [("tp1", at(1), 20032, 5)]


def test_a_bar_meeting_stop_and_target_fills_only_the_stop_for_all_contracts() -> None:
    cfg = fills_cfg()
    targets: list[tuple[OrderRole, Ticks, int]] = [("tp1", 20032, 2), ("tp2", 20064, 3)]
    book = place(SimBook(), LONG, entry=20000, stop=19984, targets=targets)
    book, _ = run(book, [bar(0, 20004, 20006, 19999, 20002)], cfg)
    book, events = run(book, [bar(1, 20010, 20070, 19980, 20000)], cfg)
    assert summary(events) == [("stop", at(1), 19983, 5)]
    trade = events[0].closed
    assert trade is not None
    assert not trade.reached_tp1
    assert book.orders == {}


# ---------------------------------------------------------------- missing bars (Req 13.12)


def test_missing_rth_bars_while_open_are_counted_on_the_trade() -> None:
    book = place(SimBook(), LONG, entry=20000, stop=19984, targets=[("tp1", 20032, 5)])
    _, events = run(
        book,
        [bar(0, 20004, 20006, 19999, 20002), bar(4, 20030, 20040, 20020, 20035)],
        fills_cfg(),
    )
    trade = events[-1].closed
    assert trade is not None
    assert trade.exits[0].bar_open_ns == at(4)
    assert trade.missing_bars == 3


def test_gaps_outside_rth_are_not_counted() -> None:
    book = place(SimBook(), LONG, entry=20000, stop=19984, targets=[("tp1", 20032, 5)])
    _, events = run(
        book,
        # 15:59 entry; the next bar opens at 16:05, after the RTH close.
        [bar(389, 20004, 20006, 19999, 20002), bar(395, 20030, 20040, 20020, 20035)],
        fills_cfg(),
    )
    trade = events[-1].closed
    assert trade is not None
    assert trade.missing_bars == 0


# ---------------------------------------------------------------- close-out and queries


def test_close_all_closes_every_trade_at_the_given_price_and_clears_the_book() -> None:
    cfg = fills_cfg(commission="0.37", fee="0.35")
    book = place(SimBook(), LONG, entry=20000, stop=19984, targets=[("tp1", 20032, 5)])
    book, _ = run(book, [bar(0, 20004, 20006, 19999, 20002)], cfg)
    pending = setup_key("short", "MNQ")
    book = place(book, pending, entry=80100, stop=80140, qty=1, prefix="nq-")
    trade = book.trades[LONG]
    assert book.positions() == {"MES": 5}
    assert book.working_entry_contracts() == {"MNQ": 1}
    assert trade.unrealized(19990) == Decimal("-62.50")
    assert trade.worst_price(bar(1, 19995, 19996, 19988, 19990)) == 19988

    book, events = close_all(book, at(1), {"MES": 19990}, cfg, reason="mll")
    assert summary(events) == [("mll:entry", at(1), 19990, 5)]
    assert (events[0].order.role, events[0].order.kind) == ("exit", "market")
    closed = events[0].closed
    assert closed is not None
    assert closed.net == Decimal("-69.70")  # -62.50 less 2 x 5 x $0.72
    assert book.orders == {}
    assert book.brackets == {}
    assert book.trades == {}


# ---------------------------------------------------------------- rejected input


def test_invalid_orders_and_bars_are_rejected() -> None:
    with pytest.raises(ValueError, match="stop beyond the entry on the loss side"):
        place(SimBook(), LONG, entry=20000, stop=20000)
    with pytest.raises(ValueError, match="limit or stop order with role 'entry'"):
        SimBook().submit_bracket(
            order("entry", LONG, "entry", "market", 5, None, T0),
            order("stop", LONG, "stop", "stop", 5, 19984, T0),
        )
    book = place(SimBook(), LONG, entry=20000, stop=19984)
    with pytest.raises(ValueError, match="already placed"):
        place(book, LONG, entry=20000, stop=19984)
    with pytest.raises(ValueError, match="stop beyond the entry"):
        book.modify("stop", 20004, at(1))
    book, _ = on_bar(book, bar(1, 20010, 20012, 20004, 20008), fills_cfg(), rth=RTH)
    with pytest.raises(ValueError, match="is not after the last one"):
        on_bar(book, bar(1, 20010, 20012, 20004, 20008), fills_cfg(), rth=RTH)
    with pytest.raises(ValueError, match=r"fills\.costs\.MES is not set"):
        on_bar(book, bar(2, 20004, 20006, 19990, 20002), FillsConfig(), rth=RTH)
