"""Unit tests for market-entry brackets in ``fse.sim.fills`` (plugin-runner design 7.1).

Fill at ``open ± slip``, legs from the unslipped open, targets on the entry bar,
stop-first, ``exact_bar_open`` expiry, cancellation, the ``stop_fill`` rule,
``close_setup``, old-state decoding and the ``_BarRun`` plumbing.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, time
from decimal import Decimal
from typing import Any

import pytest

from fse.config.schema.fills import FillsConfig
from fse.engine.state import ENGINE_CODEC, dataclass_registry
from fse.engine.types import Bar, Direction, Order, OrderRole, SetupKey, Side, Ticks
from fse.sim import fills as fills_module
from fse.sim.fills import (
    FillEvent,
    MarketBracket,
    OpenTrade,
    SimBook,
    StopFill,
    close_setup,
    on_bar,
)
from fse.timekit import NS_PER_MINUTE, Instant, ny_instant

SESSION = date(2026, 3, 5)
T0 = ny_instant(SESSION, time(9, 30))
RTH = (T0, ny_instant(SESSION, time(16, 0)))
CODEC = ENGINE_CODEC.extended(dataclass_registry(fills_module))


def at(minute: int) -> Instant:
    return T0 + minute * NS_PER_MINUTE


def bar(minute: int, o: Ticks, h: Ticks, low: Ticks, c: Ticks) -> Bar:
    start = at(minute)
    return Bar(
        instrument="MNQ", contract="MNQH6", interval_s=60, open_ns=start,
        close_ns=start + NS_PER_MINUTE, o=o / 4, h=h / 4, l=low / 4, c=c / 4, v=1.0,
        o_t=o, h_t=h, l_t=low, c_t=c, source="atlas",
    )


def cfg(slippage: int = 0, through: int = 1) -> FillsConfig:
    costs = {"commission": "0.62", "exchange_fee": "0"}
    return FillsConfig.model_validate(
        {"trade_through_ticks": through, "slippage_ticks": slippage, "costs": {"MNQ": costs}}
    )


def key(direction: Direction = "long", tap: int = 1) -> SetupKey:
    return SetupKey("MNQ", "alpha", 0.0, direction, SESSION, tap)


def entry(k: SetupKey, qty: int = 2, placed_at: Instant = T0, cid: str = "e") -> Order:
    side: Side = "buy" if k.direction == "long" else "sell"
    return Order(cid, k, "MNQ", side, "market", qty, None, placed_at, "entry")


def place(
    book: SimBook,
    k: SetupKey,
    *,
    stop_ticks: int = 40,
    targets: Sequence[tuple[OrderRole, str, int, int]] = (("tp1", "t", 2, 80),),
    qty: int = 2,
    placed_at: Instant = T0,
    exact: bool = False,
    prefix: str = "",
) -> SimBook:
    return book.submit_market_bracket(
        entry(k, qty, placed_at, f"{prefix}e"),
        stop_id=f"{prefix}s",
        stop_ticks=stop_ticks,
        targets=[(r, f"{prefix}{cid}", q, t) for r, cid, q, t in targets],
        exact_bar_open=exact,
    )


def run(
    book: SimBook, bars: Iterable[Bar], c: FillsConfig, stop_fill: StopFill = "gap_open"
) -> tuple[SimBook, list[FillEvent]]:
    events: list[FillEvent] = []
    for b in bars:
        book, new = on_bar(book, b, c, rth=RTH, stop_fill=stop_fill)
        events += new
    return book, events


def summary(events: Iterable[FillEvent]) -> list[tuple[str, Instant, Ticks, int]]:
    return [(e.fill.client_id, e.fill.bar_open_ns, e.fill.price, e.fill.qty) for e in events]


@pytest.mark.parametrize(("direction", "price"), [("long", 20001), ("short", 19999)])
def test_fill_at_open_plus_minus_slip(direction: Direction, price: Ticks) -> None:
    book, events = run(place(SimBook(), key(direction)), [bar(0, 20000, 20002, 19998, 20000)],
                       cfg(slippage=1))
    assert summary(events) == [("e", at(0), price, 2)]
    trade = book.trades[key(direction)]
    assert trade.entry_bar_targets is True
    assert not book.market_brackets


@pytest.mark.parametrize(("direction", "stop", "tp"), [("long", 19960, 20080),
                                                      ("short", 20040, 19920)])
def test_legs_from_unslipped_open(direction: Direction, stop: Ticks, tp: Ticks) -> None:
    book, _ = run(place(SimBook(), key(direction)), [bar(0, 20000, 20002, 19998, 20000)],
                  cfg(slippage=2))
    s, t = book.order("s"), book.order("t")
    assert s is not None
    assert t is not None
    assert (s.kind, s.role, s.price, s.qty, s.placed_at) == ("stop", "stop", stop, 2, at(0))
    assert (t.kind, t.role, t.price, t.qty, t.placed_at) == ("limit", "tp1", tp, 2, at(0))
    assert book.trades[key(direction)].initial_stop == stop
    assert key(direction) in book.brackets


def test_target_fills_on_entry_bar() -> None:
    _, events = run(place(SimBook(), key()), [bar(0, 20000, 20081, 19990, 20070)], cfg())
    assert summary(events) == [("e", at(0), 20000, 2), ("t", at(0), 20080, 2)]
    assert events[-1].closed is not None


def test_target_needs_trade_through_on_entry_bar() -> None:
    book, events = run(place(SimBook(), key()), [bar(0, 20000, 20080, 19990, 20070)], cfg())
    assert summary(events) == [("e", at(0), 20000, 2)]
    assert key() in book.trades


def test_stop_first_on_bar_hitting_both() -> None:
    _, events = run(place(SimBook(), key()), [bar(0, 20000, 20090, 19950, 20000)], cfg())
    assert summary(events) == [("e", at(0), 20000, 2), ("s", at(0), 19960, 2)]


def test_exit_on_later_bar() -> None:
    bars = [bar(0, 20000, 20010, 19990, 20005), bar(1, 20005, 20100, 20000, 20090)]
    _, events = run(place(SimBook(), key()), bars, cfg())
    assert summary(events) == [("e", at(0), 20000, 2), ("t", at(1), 20080, 2)]


def test_exact_bar_open_expires_without_fill() -> None:
    book = place(SimBook(), key(), placed_at=at(1), exact=True)
    book, events = run(book, [bar(2, 20000, 20002, 19998, 20000)], cfg())
    assert events == []
    assert not book.market_brackets
    assert not book.orders
    assert not book.brackets


def test_exact_bar_open_fills_on_its_bar() -> None:
    book = place(SimBook(), key(), placed_at=at(1), exact=True)
    _, events = run(book, [bar(1, 20000, 20002, 19998, 20000)], cfg())
    assert summary(events) == [("e", at(1), 20000, 2)]


def test_not_exact_fills_on_next_available_bar() -> None:
    book = place(SimBook(), key(), placed_at=at(1))
    _, events = run(book, [bar(3, 20010, 20012, 20008, 20010)], cfg())
    assert summary(events) == [("e", at(3), 20010, 2)]


def test_pending_bracket_survives_earlier_bar() -> None:
    book = place(SimBook(), key(), placed_at=at(5))
    book, events = run(book, [bar(1, 20000, 20002, 19998, 20000)], cfg())
    assert events == []
    assert book.market_brackets == {key(): MarketBracket(
        "MNQ", "long", "s", 40, (("tp1", "t", 2, 80),), False)}
    assert book.working_entry_contracts() == {"MNQ": 2}
    _, events = run(book, [bar(5, 20000, 20002, 19998, 20000)], cfg())
    assert summary(events) == [("e", at(5), 20000, 2)]


def test_cancel_at_placement_removes_setup() -> None:
    book = place(SimBook(), key()).cancel("e", T0)
    assert not book.market_brackets
    assert not book.orders


def test_cancel_before_bar_drops_setup() -> None:
    book = place(SimBook(), key()).cancel("e", at(1))
    book, events = run(book, [bar(1, 20000, 20002, 19998, 20000)], cfg())
    assert events == []
    assert not book.market_brackets
    assert not book.orders
    assert not book.brackets


def test_cancel_after_bar_keeps_fill() -> None:
    book = place(SimBook(), key()).cancel("e", at(1))
    book, events = run(book, [bar(0, 20000, 20002, 19998, 20000)], cfg())
    assert summary(events) == [("e", at(0), 20000, 2)]
    assert key() in book.trades


@pytest.mark.parametrize(("stop_fill", "price"), [("gap_open", 19949), ("at_stop", 19959)])
def test_stop_fill_rule_on_gap(stop_fill: StopFill, price: Ticks) -> None:
    bars = [bar(0, 20000, 20002, 19998, 20000), bar(1, 19950, 19955, 19940, 19945)]
    _, events = run(place(SimBook(), key()), bars, cfg(slippage=1), stop_fill)
    assert summary(events)[-1] == ("s", at(1), price, 2)


def test_stop_fill_at_stop_keeps_gap_rule_for_stop_entries() -> None:
    k = key()
    stop_entry = Order("se", k, "MNQ", "buy", "stop", 1, 20010, T0, "entry")
    stop = Order("ss", k, "MNQ", "sell", "stop", 1, 19990, T0, "stop")
    book = SimBook().submit_bracket(stop_entry, stop)
    _, events = run(book, [bar(0, 20020, 20025, 20015, 20020)], cfg(), "at_stop")
    assert summary(events) == [("se", at(0), 20020, 1)]


def test_on_bar_rejects_unknown_stop_fill() -> None:
    with pytest.raises(ValueError, match="stop_fill"):
        on_bar(SimBook(), bar(0, 1, 1, 1, 1), cfg(), rth=RTH, stop_fill="nope")  # type: ignore[arg-type]


def test_close_setup_closes_one_key() -> None:
    a, b = key(tap=1), key(tap=2)
    book = place(place(SimBook(), a), b, prefix="b", qty=1,
                 targets=(("tp1", "t", 1, 80),))
    book, _ = run(book, [bar(0, 20000, 20002, 19998, 20000)], cfg())
    book, events = close_setup(book, a, at(0), 20010, cfg(), reason="flat")
    assert summary(events) == [("flat:e", at(0), 20010, 2)]
    closed = events[0].closed
    # 10 ticks x 2 contracts x $0.50, minus 4 contract-fills x $0.62
    assert closed is not None
    assert closed.net == Decimal("7.52")
    assert a not in book.trades
    assert a not in book.brackets
    assert {o.client_id for o in book.working_orders()} == {"bs", "bt"}
    assert b in book.trades


def test_close_setup_drops_pending_market_bracket() -> None:
    book = place(SimBook(), key(), placed_at=at(5))
    book, events = close_setup(book, key(), at(1), 20000, cfg(), reason="flat")
    assert events == []
    assert not book.market_brackets
    assert not book.orders


def test_close_setup_unknown_key_is_noop() -> None:
    book = SimBook(last_bar_open={"MNQ": at(0)})
    out, events = close_setup(book, key(), at(1), 20000, cfg(), reason="flat")
    assert out == book
    assert events == []


def test_close_setup_needs_reason() -> None:
    with pytest.raises(ValueError, match="reason"):
        close_setup(SimBook(), key(), at(1), 20000, cfg(), reason=" ")


def _strip(obj: Any, names: frozenset[str]) -> Any:
    if isinstance(obj, dict):
        return {k: _strip(v, names) for k, v in obj.items() if k not in names}
    if isinstance(obj, list):
        return [_strip(v, names) for v in obj]
    return obj


def test_old_state_without_new_fields_decodes() -> None:
    k = SetupKey("MNQ", "floor_ceiling_bounce", 5800.0, "long", SESSION, 1)
    limit = Order("le", k, "MNQ", "buy", "limit", 1, 20000, T0, "entry")
    stop = Order("ls", k, "MNQ", "sell", "stop", 1, 19960, T0, "stop")
    book, _ = run(SimBook().submit_bracket(limit, stop), [bar(0, 20000, 20002, 19998, 20000)],
                  cfg())
    assert isinstance(book.trades[k], OpenTrade)
    encoded = CODEC.encode(book)
    old = _strip(encoded, frozenset({"market_brackets", "entry_bar_targets"}))
    assert old != encoded
    decoded = CODEC.decode(old)
    assert decoded == book
    assert isinstance(decoded, SimBook)
    assert decoded.market_brackets == {}
    assert decoded.trades[k].entry_bar_targets is False


def test_state_with_market_bracket_round_trips() -> None:
    book = place(SimBook(), key(), placed_at=at(5))
    assert CODEC.decode(CODEC.encode(book)) == book


def test_submit_bracket_rejects_key_held_by_market_bracket() -> None:
    book = place(SimBook(), key())
    stop = Order("x2", key(), "MNQ", "sell", "stop", 2, 19960, T0, "stop")
    limit = Order("x1", key(), "MNQ", "buy", "limit", 2, 20000, T0, "entry")
    with pytest.raises(ValueError, match="already placed"):
        book.submit_bracket(limit, stop)


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"entry": Order("e", None, "MNQ", "buy", "market", 2, None, T0, "entry")}, "setup_key"),
        ({"entry": Order("e", key(), "MNQ", "buy", "limit", 2, 20000, T0, "entry")}, "market"),
        ({"entry": Order("e", key(), "MNQ", "sell", "market", 2, None, T0, "exit")}, "market"),
        ({"entry": Order("e", SetupKey("XYZ", "m", 0.0, "long", SESSION, 1), "XYZ", "buy",
                         "market", 2, None, T0, "entry")}, "contract value"),
        ({"stop_ticks": 0}, "stop_ticks"),
        ({"targets": [("tp1", "t", 1, 8), ("tp1", "u", 1, 9)]}, "distinct roles"),
        ({"targets": [("exit", "t", 1, 8)]}, "distinct roles"),
        ({"targets": [("tp1", "t", 0, 8)]}, "qty >= 1"),
        ({"targets": [("tp1", "t", 1, 0)]}, "ticks >= 1"),
        ({"targets": [("tp1", "t", 2, 8), ("tp2", "u", 1, 9)]}, "more contracts"),
        ({"stop_id": "e"}, "new and distinct"),
        ({"targets": [("tp1", "s", 1, 8)]}, "new and distinct"),
        ({"stop_id": " "}, "non-blank"),
    ],
)
def test_submit_market_bracket_validation(change: dict[str, Any], match: str) -> None:
    kwargs: dict[str, Any] = {"entry": entry(key()), "stop_id": "s", "stop_ticks": 40,
                              "targets": [("tp1", "t", 2, 80)]}
    kwargs.update(change)
    with pytest.raises(ValueError, match=match):
        SimBook().submit_market_bracket(**kwargs)


def test_submit_market_bracket_rejects_duplicate_key_and_used_ids() -> None:
    book = place(SimBook(), key())
    with pytest.raises(ValueError, match="already placed"):
        place(book, key(), prefix="x")
    with pytest.raises(ValueError, match="new and distinct"):
        place(book, key(tap=2))
