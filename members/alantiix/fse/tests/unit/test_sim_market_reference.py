"""Market-entry brackets measured from ``reference_t`` (plugin-api-r1 R1), book level.

Legs come from the reference, the fill stays ``open ± slip``, a fill bar whose open
or slipped fill is at or past a leg drops the setup with no fill, ``reference_t``
must be an integer, and ``BROKER_CODEC`` round-trips a pending reference bracket
(an older document without the key decodes with ``reference_t=None``).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date, time

import pytest

from fse.config.schema.fills import FillsConfig
from fse.engine.types import Bar, Direction, Order, SetupKey, Side, Ticks
from fse.live.host.broker import BROKER_CODEC, PluginBrokerState
from fse.logio.canonical_json import JsonValue, dumps
from fse.sim.fills import FillEvent, MarketBracket, SimBook, on_bar
from fse.timekit import NS_PER_MINUTE, Instant, ny_instant

SESSION = date(2026, 3, 5)
T0 = ny_instant(SESSION, time(9, 30))
RTH = (T0, ny_instant(SESSION, time(16, 0)))
R = 20_000
STOP_TICKS = 8
TARGET_TICKS = 12


def cfg(slippage: int = 1, through: int = 0) -> FillsConfig:
    costs = {"commission": "0", "exchange_fee": "0"}
    return FillsConfig.model_validate(
        {"trade_through_ticks": through, "slippage_ticks": slippage, "costs": {"MNQ": costs}}
    )


def bar(minute: int, o: Ticks, h: Ticks, low: Ticks, c: Ticks) -> Bar:
    start = T0 + minute * NS_PER_MINUTE
    return Bar(
        instrument="MNQ",
        contract="MNQH6",
        interval_s=60,
        open_ns=start,
        close_ns=start + NS_PER_MINUTE,
        o=o / 4,
        h=h / 4,
        l=low / 4,
        c=c / 4,
        v=1.0,
        o_t=o,
        h_t=h,
        l_t=low,
        c_t=c,
        source="atlas",
    )


def key(direction: Direction = "long") -> SetupKey:
    return SetupKey("MNQ", "range", 0.0, direction, SESSION, 1)


def place(direction: Direction = "long", reference_t: object = R) -> SimBook:
    side: Side = "buy" if direction == "long" else "sell"
    return SimBook().submit_market_bracket(
        Order("e", key(direction), "MNQ", side, "market", 1, None, T0, "entry"),
        stop_id="s",
        stop_ticks=STOP_TICKS,
        targets=[("tp1", "t", 1, TARGET_TICKS)],
        reference_t=reference_t,  # type: ignore[arg-type]
    )


def run(book: SimBook, bars: Iterable[Bar], c: FillsConfig) -> tuple[SimBook, list[FillEvent]]:
    events: list[FillEvent] = []
    for b in bars:
        book, new = on_bar(book, b, c, rth=RTH, stop_fill="at_stop")
        events += new
    return book, events


def summary(events: Iterable[FillEvent]) -> list[tuple[str, Instant, Ticks]]:
    return [(e.fill.client_id, e.fill.bar_open_ns, e.fill.price) for e in events]


def test_long_legs_from_the_reference_and_fill_from_the_open() -> None:
    # open R+2: stop R-8, target R+12, entry R+2+1; the fill bar reaches the target
    book, events = run(place("long"), [bar(0, R + 2, R + 12, R, R + 10)], cfg())
    assert summary(events) == [("e", T0, R + 3), ("t", T0, R + TARGET_TICKS)]
    assert events[-1].closed is not None
    assert events[-1].closed.initial_stop == R - STOP_TICKS
    assert not book.trades
    assert not book.orders


def test_long_legs_are_placed_at_the_reference() -> None:
    book, events = run(place("long"), [bar(0, R + 2, R + 4, R, R + 1)], cfg())
    assert summary(events) == [("e", T0, R + 3)]
    s, t = book.order("s"), book.order("t")
    assert s is not None
    assert t is not None
    assert (s.price, t.price) == (R - STOP_TICKS, R + TARGET_TICKS)
    assert book.trades[key("long")].initial_stop == R - STOP_TICKS


def test_short_mirror() -> None:
    book, events = run(place("short"), [bar(0, R - 2, R, R - 12, R - 10)], cfg())
    assert summary(events) == [("e", T0, R - 3), ("t", T0, R - TARGET_TICKS)]
    assert events[-1].closed is not None
    assert events[-1].closed.initial_stop == R + STOP_TICKS
    assert not book.trades


@pytest.mark.parametrize(
    ("direction", "o"),
    [
        ("long", R - STOP_TICKS),  # open at the stop
        ("long", R - STOP_TICKS - 5),  # open past the stop
        ("long", R + TARGET_TICKS),  # open at the target
        ("long", R + TARGET_TICKS + 5),  # open past the target
        ("long", R + TARGET_TICKS - 1),  # open inside, open + slip at the target
        ("short", R + STOP_TICKS),
        ("short", R + STOP_TICKS + 5),
        ("short", R - TARGET_TICKS),
        ("short", R - TARGET_TICKS - 5),
        ("short", R - TARGET_TICKS + 1),  # open inside, open - slip at the target
    ],
)
def test_a_fill_bar_at_or_past_a_leg_drops_the_setup(direction: Direction, o: Ticks) -> None:
    book, events = run(place(direction), [bar(0, o, o + 1, o - 1, o)], cfg())
    assert events == []
    assert book.orders == {}
    assert book.market_brackets == {}
    assert book.trades == {}


@pytest.mark.parametrize(
    ("direction", "o"), [("long", R - STOP_TICKS + 1), ("short", R + STOP_TICKS - 1)]
)
def test_an_open_one_tick_inside_the_stop_fills(direction: Direction, o: Ticks) -> None:
    book, events = run(place(direction), [bar(0, o, o, o, o)], cfg())
    assert [e.fill.client_id for e in events] == ["e"]
    assert key(direction) in book.trades


@pytest.mark.parametrize("bad", ["x", 1.5, True, False])
def test_submit_rejects_a_non_int_reference(bad: object) -> None:
    with pytest.raises(ValueError, match="reference_t"):
        place("long", bad)


def test_none_reference_keeps_the_legs_from_the_open() -> None:
    book, events = run(place("long", None), [bar(0, R + 2, R + 4, R, R + 1)], cfg())
    assert summary(events) == [("e", T0, R + 3)]
    s, t = book.order("s"), book.order("t")
    assert s is not None
    assert t is not None
    assert (s.price, t.price) == (R + 2 - STOP_TICKS, R + 2 + TARGET_TICKS)


def _drop_reference(obj: JsonValue) -> int:
    """Delete ``reference_t`` from every encoded MarketBracket; how many were changed."""
    n = 0
    if isinstance(obj, dict):
        tag = obj.get("$type")
        if isinstance(tag, str) and tag.endswith("MarketBracket") and "reference_t" in obj:
            del obj["reference_t"]
            n += 1
        for v in obj.values():
            n += _drop_reference(v)
    elif isinstance(obj, list):
        for v in obj:
            n += _drop_reference(v)
    return n


def test_codec_round_trips_a_pending_reference_bracket() -> None:
    state = PluginBrokerState(book=place("long"))
    assert state.book.market_brackets[key("long")].reference_t == R
    text = dumps(BROKER_CODEC.encode(state))
    assert BROKER_CODEC.decode(json.loads(text)) == state


def test_codec_decodes_an_old_bracket_without_the_key_as_none() -> None:
    state = PluginBrokerState(book=place("long"))
    encoded = json.loads(dumps(BROKER_CODEC.encode(state)))
    assert _drop_reference(encoded) == 1
    decoded = BROKER_CODEC.decode(encoded)
    assert isinstance(decoded, PluginBrokerState)
    mb = decoded.book.market_brackets[key("long")]
    assert isinstance(mb, MarketBracket)
    assert mb.reference_t is None
    assert decoded == PluginBrokerState(book=place("long", None))
