"""Unit tests for EngineState and its canonical-JSON round trip (design "Engine state").

The rich state comes from the ``test_engine_step`` scenario after the bar phase:
an open plan and a resting one, a Lockout, Decimal P&L, Taps, lifecycle peaks,
chart state (with infinite signed-space bounds) and Finding_Card memory.

**Validates: Requirements 16.7, 16.10**
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction

import pytest

from fse.engine.planner import OrderBook
from fse.engine.risk import Lockout, RiskFill, RiskState
from fse.engine.sizing import PriorSession
from fse.engine.state import (
    TRADE_SESSIONS_KEPT,
    CardMemory,
    EngineState,
    SessionTrades,
    StateDecodeError,
    decode_value,
    encode_value,
)
from fse.engine.types import Fill, SetupKey, Trade
from fse.logio import canonical_json
from tests.unit.test_engine_step import (
    BAR_UP,
    DT1,
    DT2,
    GK_KEY,
    SESSION,
    brackets,
    engine,
    entry_fill,
    inputs,
)


def rich_state() -> EngineState:
    eng = engine(orders={"max_open": 2}, kill_switches={"max_trades": {"max": 1}})
    data = inputs(BAR_UP)
    first = eng.step(eng.initial_state(), data.view(DT1), DT1)
    gk = brackets(first)[0]
    fill = entry_fill(gk.entry)
    state = eng.count_fill(first.state, RiskFill(fill.fill, "entry"))
    stops = eng.update_stops(state, BAR_UP, [fill])
    state = eng.consume_bar(stops.state, BAR_UP, data.view(DT2))
    state = eng.step(state, data.view(DT2), DT2, stops.events).state
    return state.with_trade(trade(SESSION, Decimal("-37.50"), Decimal("-0.75")))


def trade(session: date, net: Decimal, r_multiple: Decimal, *, shadow: bool = False) -> Trade:
    key = SetupKey("MES", "floor_ceiling_bounce", 5750.0, "long", session, 1)
    entry = Fill("e", 0, 23000, 1, Decimal("0.62"))
    out = Fill("x", 60, 22990, 1, Decimal("0.62"))
    return Trade(
        key, entry, 22980, 1, (out,), net, Decimal("25.00"), r_multiple, False,
        Decimal("1"), Decimal("0"), 0, shadow,
    )  # fmt: skip


def round_trip(state: EngineState) -> tuple[str, EngineState]:
    text = canonical_json.dumps(state.to_jsonable())
    return text, EngineState.from_jsonable(json.loads(text))


# ---------------------------------------------------------------- round trip


def test_the_initial_state_round_trips() -> None:
    state = EngineState.initial()
    text, back = round_trip(state)
    assert back == state
    assert text == state.to_canonical_json()
    assert EngineState.from_json(text) == state


def test_a_rich_state_round_trips_exactly_through_canonical_json() -> None:
    state = rich_state()
    plan = state.book.plan(GK_KEY)
    assert plan is not None
    assert plan.status == "open"
    assert state.risk.lockouts
    assert state.chart.instruments
    assert state.lifecycle.peaks
    assert state.cards.grades
    text, back = round_trip(state)
    assert back == state
    for name in ("taps", "lifecycle", "chart", "book", "risk", "trade_totals", "cards"):
        assert getattr(back, name) == getattr(state, name)
    assert canonical_json.dumps(back.to_jsonable()) == text
    assert back.to_canonical_json() == text
    assert '{"$float":"inf"}' in text
    assert '"$decimal":"-37.50"' in text


def test_a_restored_state_has_exact_money_and_instants() -> None:
    _, back = round_trip(rich_state())
    totals = back.trades_of(SESSION)
    assert str(totals.net) == "-37.50"
    assert str(totals.r_sum) == "-0.75"
    assert back.t == DT2
    assert type(back.t) is int
    assert isinstance(back.book.placed, frozenset)


# ---------------------------------------------------------------- the codec


def test_values_keep_their_exact_types() -> None:
    value = (
        Decimal("1.50"),
        Fraction(1, 3),
        2**70,
        -0.1,
        math.inf,
        -math.inf,
        date(2026, 3, 5),
        ((1, 2), ()),
        None,
        True,
        "s",
    )
    back = decode_value(json.loads(canonical_json.dumps(encode_value(value))))
    assert back == value
    assert isinstance(back, tuple)
    assert str(back[0]) == "1.50"
    assert type(back[1]) is Fraction
    assert type(back[2]) is int


def test_frozensets_encode_in_one_order_and_maps_keep_theirs() -> None:
    keys = [SetupKey("MES", "rug", float(k), "short", date(2026, 3, 5), 1) for k in range(5)]
    a = canonical_json.dumps(encode_value(frozenset(keys)))
    b = canonical_json.dumps(encode_value(frozenset(reversed(keys))))
    assert a == b
    mapping = {"b": 1, "a": 2}
    back = decode_value(json.loads(canonical_json.dumps(encode_value(mapping))))
    assert back == mapping
    assert isinstance(back, dict)
    assert list(back) == ["b", "a"]


@pytest.mark.parametrize(
    ("value", "error"),
    [
        (math.nan, ValueError),
        ([1, 2], TypeError),
        (datetime(2026, 3, 5, 10, 0), TypeError),
        (Decimal("NaN"), ValueError),
        (object(), TypeError),
    ],
)
def test_unsupported_values_are_refused(value: object, error: type[Exception]) -> None:
    with pytest.raises(error):
        encode_value(value)


@pytest.mark.parametrize(
    "obj",
    [
        {"$type": "os.system"},
        {"$type": "risk.Lockout", "rule": "red_day", "started_at": 1, "nope": 2},
        {"$type": "risk.Lockout", "rule": "not_a_rule", "started_at": 1,
         "last_session": {"$date": "2026-03-05"}},
        {"$model": "SizingConfig", "data": {"$map": []}},
        {"$float": "nan"},
        {"$unknown": 1},
        {"a": 1, "b": 2},
    ],
)  # fmt: skip
def test_malformed_values_do_not_decode(obj: object) -> None:
    with pytest.raises(StateDecodeError):
        decode_value(obj)


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        '{"format":"fse.engine_state","version":2,"state":null}',
        '{"format":"other","version":1,"state":null}',
        '{"format":"fse.engine_state","version":1,"state":{"$date":"2026-03-05"}}',
        '{"format":"fse.engine_state","version":1,"state":NaN}',
    ],
)
def test_unreadable_state_text_raises_a_decode_error(text: str) -> None:
    with pytest.raises(StateDecodeError):
        EngineState.from_json(text)


def test_a_decoded_lockout_equals_the_original() -> None:
    lockout = Lockout("consecutive_losers", DT1, date(2026, 3, 6))
    rs = RiskState(session=SESSION, session_net=Decimal("-12.40"), lockouts=(lockout,))
    state = EngineState(risk=rs, book=OrderBook(next_seq=7))
    _, back = round_trip(state)
    assert back.risk == rs
    assert back.book.next_seq == 7


# ---------------------------------------------------------------- trade totals and memory


def test_trade_totals_sum_per_session_and_keep_the_latest_sessions() -> None:
    days = [date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4)]
    state = EngineState()
    for d in days:
        state = state.with_trade(trade(d, Decimal("100.00"), Decimal("1.5")))
    state = state.with_trade(trade(days[-1], Decimal("50.25"), Decimal("0.5")))
    assert len(state.trade_totals) == TRADE_SESSIONS_KEPT
    assert state.trades_of(days[-1]) == PriorSession(Decimal("150.25"), Decimal("2.0"))
    assert state.trades_of(days[0]) == PriorSession()
    older = SessionTrades(days[1], PriorSession(Decimal("100.00"), Decimal("1.5")))
    assert state.trade_totals[0] == older
    with pytest.raises(ValueError, match="Shadow_Trade"):
        state.with_trade(trade(days[-1], Decimal("1"), Decimal("0.1"), shadow=True))


def test_the_prior_session_skips_weekends() -> None:
    eng = engine()
    friday = date(2026, 3, 6)
    monday = date(2026, 3, 9)
    state = EngineState().with_trade(trade(friday, Decimal("1300.00"), Decimal("2")))
    assert eng.prior_session(state, monday) == PriorSession(Decimal("1300.00"), Decimal("2"))
    assert eng.prior_session(state, date(2026, 3, 10)) == PriorSession()
    assert eng.prior_session(state, date(2026, 1, 1)) == PriorSession()


def test_card_memory_round_trips() -> None:
    memory = CardMemory(session=SESSION, grades=((GK_KEY, "A_Plus"),))
    _, back = round_trip(EngineState(cards=memory))
    assert back.cards == memory
