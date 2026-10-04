"""Property 43: Accounting invariant.

*For any* closed trade (any partial exits, instruments and fee settings), the
net P&L equals the sum over exit fills of the side-adjusted price difference x
point value x contracts minus all fees, fees equal contracts x (commission +
exchange fee) per fill so a round trip costs 2 x N x (commission + fee), and
R_Multiple equals net P&L / R with R from the entry fill, initial stop and
entry contracts, so a full exit at the initial stop with zero costs gives
exactly -1.

Two tests:

1. ``test_accounting_invariant``: brackets on MES, MNQ, ES and NQ (limit or
   stop entry, protective stop, 0 to 2 partial targets) against random
   1-minute bars, with partial market exits, protective-stop moves once the
   entry has filled, and a final ``close_all`` that closes whatever is still
   open. Every closed trade is checked against an oracle in exact ``Fraction``
   arithmetic built only from the contract values of Req 13.9, the drawn
   commission and exchange fee, and the trade's fill prices and quantities.
2. ``test_full_exit_at_initial_stop``: a trade that exits in full at its
   initial stop, by its protective stop (no slippage, no gap past the stop)
   or by ``close_all`` at the stop price, with zero costs has an R_Multiple
   of exactly -1.

**Validates: Requirements 13.7, 13.10, 13.11**
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, time
from decimal import Decimal
from fractions import Fraction
from types import MappingProxyType
from typing import Final, Literal

from hypothesis import event, given
from hypothesis import strategies as st

from fse.config.schema.fills import FillsConfig
from fse.engine.types import (
    Bar,
    Direction,
    Order,
    OrderKind,
    OrderRole,
    SetupKey,
    Side,
    Ticks,
    Trade,
)
from fse.sim.fills import FillEvent, SimBook, close_all, on_bar
from fse.timekit import NS_PER_MINUTE, Instant, ny_instant

SESSION: Final = date(2026, 3, 5)
T0: Final = ny_instant(SESSION, time(9, 30))
RTH: Final = (T0, ny_instant(SESSION, time(16, 0)))

INSTRUMENTS: Final[tuple[str, ...]] = ("MES", "MNQ", "ES", "NQ")
POINT_VALUE_USD: Final[Mapping[str, Fraction]] = MappingProxyType(
    {"MES": Fraction(5), "MNQ": Fraction(2), "ES": Fraction(50), "NQ": Fraction(20)}
)
"""Dollars per point per contract, straight from Req 13.9."""
TICKS_PER_POINT: Final = 4
"""A 0.25-point tick for all four instruments (Req 13.9)."""
BASE: Final[Mapping[str, Ticks]] = MappingProxyType(
    {"MES": 23_000, "MNQ": 84_000, "ES": 23_000, "NQ": 84_000}
)
"""Opening price of each instrument's walk, in ticks."""
SIGN: Final[Mapping[Direction, int]] = MappingProxyType({"long": 1, "short": -1})
MAX_MINUTES: Final = 20
DIRECTIONS: Final[tuple[Direction, ...]] = ("long", "short")
ENTRY_KINDS: Final[tuple[OrderKind, ...]] = ("limit", "stop")
TARGET_ROLES: Final[tuple[OrderRole, ...]] = ("tp1", "tp2")
MIN_DIGITS: Final = 28
"""Fewest significant digits a non-terminating R_Multiple may carry (Python's default)."""

# ---------------------------------------------------------------- operations


@dataclass(frozen=True, slots=True)
class Costs:
    """The drawn commission and exchange fee of one instrument, per contract per side."""

    commission: Decimal
    exchange_fee: Decimal

    @property
    def per_contract(self) -> Fraction:
        return Fraction(self.commission) + Fraction(self.exchange_fee)


ZERO_COSTS: Final = Costs(Decimal("0.00"), Decimal("0.00"))


def minute_ns(minute: int) -> Instant:
    return T0 + minute * NS_PER_MINUTE


def leg_id(setup: int, role: OrderRole) -> str:
    return f"s{setup}:{role}"


@dataclass(frozen=True, slots=True)
class Place:
    """Submit a bracket for setup ``setup`` at the open of ``minute``."""

    minute: int
    setup: int
    instrument: str
    direction: Direction
    entry_kind: OrderKind
    qty: int
    entry: Ticks
    stop: Ticks
    targets: tuple[tuple[OrderRole, Ticks, int], ...] = ()

    @property
    def key(self) -> SetupKey:
        return SetupKey(
            self.instrument, "floor_ceiling_bounce", 5800.0, self.direction, SESSION, self.setup + 1
        )

    @property
    def sign(self) -> int:
        return SIGN[self.direction]

    @property
    def exit_side(self) -> Side:
        return "sell" if self.direction == "long" else "buy"

    def leg(self, role: OrderRole, kind: OrderKind, qty: int, price: Ticks) -> Order:
        entry_side: Side = "buy" if self.direction == "long" else "sell"
        side = entry_side if role == "entry" else self.exit_side
        return Order(
            leg_id(self.setup, role),
            self.key,
            self.instrument,
            side,
            kind,
            qty,
            price,
            minute_ns(self.minute),
            role,
        )

    def orders(self) -> tuple[Order, Order, list[Order]]:
        entry = self.leg("entry", self.entry_kind, self.qty, self.entry)
        stop = self.leg("stop", "stop", self.qty, self.stop)
        targets = [self.leg(role, "limit", n, price) for role, price, n in self.targets]
        return entry, stop, targets


@dataclass(frozen=True, slots=True)
class MoveStop:
    """Move setup ``setup``'s protective stop to ``offset`` profit-side ticks from its entry."""

    minute: int
    setup: int
    offset: int


@dataclass(frozen=True, slots=True)
class Exit:
    """Submit market exit number ``seq`` of setup ``setup`` for ``qty`` contracts."""

    minute: int
    setup: int
    seq: int
    qty: int


type Op = Place | MoveStop | Exit


def exit_order(op: Exit, place: Place) -> Order:
    return Order(
        f"s{op.setup}:exit{op.seq}",
        place.key,
        place.instrument,
        place.exit_side,
        "market",
        op.qty,
        None,
        minute_ns(op.minute),
        "exit",
    )


@dataclass(frozen=True, slots=True)
class Case:
    """Bars in processing order, operations in time order, costs and the close-out prices."""

    bars: tuple[Bar, ...]
    ops: tuple[Op, ...]
    costs: Mapping[str, Costs]
    cfg: FillsConfig
    final: Mapping[str, Ticks]

    @property
    def places(self) -> dict[SetupKey, Place]:
        return {op.key: op for op in self.ops if isinstance(op, Place)}


# ---------------------------------------------------------------- generators


def make_bar(instrument: str, minute: int, o: Ticks, h: Ticks, low: Ticks, c: Ticks) -> Bar:
    start = minute_ns(minute)
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


def fills_config(through: int, slippage: int, costs: Mapping[str, Costs]) -> FillsConfig:
    return FillsConfig.model_validate(
        {
            "trade_through_ticks": through,
            "slippage_ticks": slippage,
            "costs": {
                name: {"commission": str(c.commission), "exchange_fee": str(c.exchange_fee)}
                for name, c in costs.items()
            },
        }
    )


def _cents(n: int) -> Decimal:
    return Decimal(n).scaleb(-2)


# $0.00 to $25.00 per contract per side (Req 13.8), with $0.00 drawn often.
CENTS: Final = st.one_of(st.just(0), st.integers(0, 2500)).map(_cents)
COSTS: Final = st.one_of(st.just(ZERO_COSTS), st.builds(Costs, CENTS, CENTS))


@st.composite
def bar_streams(draw: st.DrawFn, n_minutes: int) -> tuple[Bar, ...]:
    bars: list[Bar] = []
    for instrument in INSTRUMENTS:
        close = BASE[instrument]
        for minute in range(n_minutes):
            o = close + draw(st.integers(-4, 4))
            c = o + draw(st.integers(-10, 10))
            h = max(o, c) + draw(st.integers(0, 6))
            low = min(o, c) - draw(st.integers(0, 6))
            close = c
            bars.append(make_bar(instrument, minute, o, h, low, c))
    bars.sort(key=lambda b: (b.open_ns, INSTRUMENTS.index(b.instrument)))
    return tuple(bars)


@st.composite
def place_ops(draw: st.DrawFn, setup: int, n_minutes: int) -> Place:
    instrument = draw(st.sampled_from(INSTRUMENTS))
    direction = draw(st.sampled_from(DIRECTIONS))
    sign = SIGN[direction]
    qty = draw(st.integers(1, 10))
    n_targets = min(qty, draw(st.integers(0, 2)))
    left = qty
    target_qty: list[int] = []
    for i in range(n_targets):
        n = draw(st.integers(1, left - (n_targets - 1 - i)))
        target_qty.append(n)
        left -= n
    entry = BASE[instrument] + draw(st.integers(-10, 10))
    return Place(
        minute=draw(st.integers(0, n_minutes - 1)),
        setup=setup,
        instrument=instrument,
        direction=direction,
        entry_kind=draw(st.sampled_from(ENTRY_KINDS)),
        qty=qty,
        entry=entry,
        stop=entry - sign * draw(st.integers(1, 16)),
        targets=tuple(
            (role, entry + sign * draw(st.integers(1, 20)), n)
            for role, n in zip(TARGET_ROLES, target_qty, strict=False)
        ),
    )


@st.composite
def change_ops(draw: st.DrawFn, place: Place, seq: int, n_minutes: int) -> Op:
    """A market exit or a stop move for ``place``, never before its placement."""
    minute = draw(st.integers(place.minute, n_minutes - 1))
    if draw(st.booleans()):
        return Exit(minute, place.setup, seq, draw(st.integers(1, place.qty)))
    return MoveStop(minute, place.setup, draw(st.integers(-20, 12)))


def _op_order(op: Op) -> tuple[int, int]:
    """Time order; within a minute a placement comes before the changes to it."""
    return op.minute, 0 if isinstance(op, Place) else 1


@st.composite
def cases(draw: st.DrawFn) -> Case:
    n_minutes = draw(st.integers(1, MAX_MINUTES))
    bars = draw(bar_streams(n_minutes))
    setups = [draw(place_ops(i, n_minutes)) for i in range(draw(st.integers(1, 5)))]
    others = [
        draw(change_ops(draw(st.sampled_from(setups)), seq, n_minutes))
        for seq in range(draw(st.integers(0, 10)))
    ]
    ops = tuple(sorted([*setups, *others], key=_op_order))  # stable: ties keep draw order
    costs = {name: draw(COSTS) for name in INSTRUMENTS}
    cfg = fills_config(draw(st.integers(0, 2)), draw(st.integers(0, 2)), costs)
    final = {name: BASE[name] + draw(st.integers(-30, 30)) for name in INSTRUMENTS}
    return Case(bars, ops, MappingProxyType(costs), cfg, MappingProxyType(final))


# ---------------------------------------------------------------- simulation


def apply(book: SimBook, op: Op, places: Mapping[int, Place]) -> SimBook:
    """Apply one operation; exits and stop moves only act on an open trade."""
    if isinstance(op, Place):
        entry, stop, targets = op.orders()
        return book.submit_bracket(entry, stop, targets)
    place = places[op.setup]
    if place.key not in book.trades:
        return book
    if isinstance(op, Exit):
        return book.submit_exit(exit_order(op, place))
    price = place.entry + place.sign * op.offset
    return book.modify(leg_id(op.setup, "stop"), price, minute_ns(op.minute))


def simulate(case: Case) -> tuple[SimBook, list[FillEvent]]:
    """Process every bar, each operation before the bars of its minute, then close all."""
    places = {op.setup: op for op in case.ops if isinstance(op, Place)}
    book = SimBook()
    events: list[FillEvent] = []
    pending = list(case.ops)
    for bar in case.bars:
        while pending and minute_ns(pending[0].minute) <= bar.open_ns:
            book = apply(book, pending.pop(0), places)
        book, new = on_bar(book, bar, case.cfg, rth=RTH)
        events.extend(new)
    book, new = close_all(book, case.bars[-1].close_ns, case.final, case.cfg, reason="flat")
    events.extend(new)
    return book, events


# ---------------------------------------------------------------- oracle


def _terminates(q: Fraction) -> bool:
    """Whether ``q`` has a finite decimal expansion."""
    den = q.denominator
    for p in (2, 5):
        while den % p == 0:
            den //= p
    return den == 1


def assert_quotient(value: Decimal, exact: Fraction) -> None:
    """``value`` is ``exact`` when that terminates, else ``exact`` correctly rounded.

    A non-terminating quotient must carry at least ``MIN_DIGITS`` significant
    digits and lie within half a unit in its last place of the exact value.
    """
    if _terminates(exact):
        assert Fraction(value) == exact, f"R_Multiple {value} is not exactly {exact}"
        return
    _, digits, exponent = value.as_tuple()
    assert isinstance(exponent, int), f"R_Multiple {value} is not finite"
    assert len(digits) >= MIN_DIGITS, f"R_Multiple {value} keeps too few digits"
    ulp = Fraction(10) ** exponent
    assert abs(Fraction(value) - exact) <= ulp / 2, f"R_Multiple {value} is not {exact} rounded"


def check_trade(place: Place, costs: Costs, events: Sequence[FillEvent]) -> Trade:
    """Check the fills of one Setup_Key, in order, against the exact oracle."""
    entry_event, *exit_events = events
    assert entry_event.order.role == "entry"
    assert entry_event.closed is None
    assert exit_events, f"{place.key} entered but never closed"
    assert all(e.order.role != "entry" for e in exit_events)
    assert all(e.closed is None for e in exit_events[:-1]), "only the last exit closes"
    trade = exit_events[-1].closed
    assert trade is not None
    entry = entry_event.fill
    assert trade.setup_key == place.key
    assert trade.entry_fill == entry
    assert trade.exits == tuple(e.fill for e in exit_events)
    assert entry.qty == trade.qty_at_entry == place.qty
    assert sum(f.qty for f in trade.exits) == place.qty
    # No stop move comes before the entry fills, so the initial stop is the placed one.
    assert trade.initial_stop == place.stop

    # Req 13.7: every fill, partial exits included, costs its contracts x
    # (commission + exchange fee), so the round trip costs 2 x N x that.
    per_contract = costs.per_contract
    for e in events:
        assert Fraction(e.fill.fees) == e.fill.qty * per_contract, f"fees of {e.fill.client_id}"
    fees = sum((Fraction(e.fill.fees) for e in events), Fraction(0))
    assert fees == 2 * place.qty * per_contract

    # Req 13.10: net = sum over exits of (exit - entry) x sign x point value x
    # contracts, minus every fee. Prices are ticks, so ticks / 4 are points.
    tick_usd = POINT_VALUE_USD[place.instrument] / TICKS_PER_POINT
    assert Fraction(entry_event.gross) == 0
    gross = [(f.price - entry.price) * place.sign * tick_usd * f.qty for f in trade.exits]
    for e, g in zip(exit_events, gross, strict=True):
        assert Fraction(e.gross) == g, f"gross of {e.fill.client_id}"
        assert Fraction(e.net) == g - Fraction(e.fill.fees)
    net = sum(gross, Fraction(0)) - fees
    assert Fraction(trade.net) == net
    assert sum((Fraction(e.net) for e in events), Fraction(0)) == net

    # Req 13.11: R from the entry fill, the initial stop and the entry contracts.
    r = abs(entry.price - place.stop) * tick_usd * place.qty
    assert Fraction(trade.r) == r
    assert_quotient(trade.r_multiple, net / r)
    if per_contract == 0 and all(f.price == place.stop for f in trade.exits):
        assert trade.r_multiple == -1
    return trade


# ---------------------------------------------------------------- properties


# Feature: skylit-futures-strategy-engine, Property 43: Accounting invariant
@given(case=cases())
def test_accounting_invariant(case: Case) -> None:
    book, events = simulate(case)
    assert not book.trades, "close_all leaves no open trade"

    by_key: dict[SetupKey, list[FillEvent]] = {}
    for e in events:
        assert e.order.setup_key is not None
        by_key.setdefault(e.order.setup_key, []).append(e)
    closed = [e.closed for e in events if e.closed is not None]
    assert len(closed) == len(by_key), "every entered trade closes exactly once"

    places = case.places
    trades = [
        check_trade(places[key], case.costs[key.instrument], evs) for key, evs in by_key.items()
    ]
    event(f"closed trades: {min(len(trades), 3)}")
    if any(len(t.exits) > 1 for t in trades):
        event("partial exits")
    if any(case.costs[t.setup_key.instrument].per_contract == 0 for t in trades):
        event("zero-cost trade")


type Via = Literal["stop", "close_all"]
VIAS: Final[tuple[Via, ...]] = ("stop", "close_all")


@dataclass(frozen=True, slots=True)
class StopCase:
    """One bracket that fills on bar 0, then exits in full at its initial stop."""

    place: Place
    bars: tuple[Bar, ...]
    cfg: FillsConfig
    costs: Costs
    via: Via


def profit_bar(place: Place, minute: int, o: int, h: int, low: int, c: int) -> Bar:
    """A bar given in profit-side ticks from the entry (positive is profit for the direction)."""
    o_t, h_t, low_t, c_t = (place.entry + place.sign * x for x in (o, h, low, c))
    return make_bar(place.instrument, minute, o_t, max(h_t, low_t), min(h_t, low_t), c_t)


@st.composite
def stop_cases(draw: st.DrawFn) -> StopCase:
    instrument = draw(st.sampled_from(INSTRUMENTS))
    direction = draw(st.sampled_from(DIRECTIONS))
    risk = draw(st.integers(1, 40))
    through = draw(st.integers(0, min(risk - 1, 20)))
    entry = BASE[instrument] + draw(st.integers(-20, 20))
    draft = Place(
        minute=0,
        setup=0,
        instrument=instrument,
        direction=direction,
        entry_kind=draw(st.sampled_from(ENTRY_KINDS)),
        qty=draw(st.integers(1, 50)),
        entry=entry,
        stop=0,
    )
    place = replace(draft, stop=entry - draft.sign * risk)
    # Bar 0 fills either entry kind (it trades through the limit and touches the
    # stop entry) and stays short of the protective stop.
    low = draw(st.integers(-(risk - 1), -through))
    high = draw(st.integers(0, 10))
    o, c = draw(st.integers(low, high)), draw(st.integers(low, high))
    bars = [profit_bar(place, 0, o, high, low, c)]
    via = draw(st.sampled_from(VIAS))
    if via == "stop":
        # Bar 1 opens at or on the profit side of the stop and trades to or
        # through it: with no slippage the stop fills at its own price.
        o = draw(st.integers(-risk, -risk + 10))
        low = draw(st.integers(-risk - 10, -risk))
        c = draw(st.integers(low, o + 10))
        bars.append(profit_bar(place, 1, o, max(o, c) + draw(st.integers(0, 5)), low, c))
    zero = Decimal(draw(st.sampled_from(("0", "0.00", "0.000"))))
    costs = Costs(zero, zero)
    cfg = fills_config(through, 0, dict.fromkeys(INSTRUMENTS, costs))
    return StopCase(place, tuple(bars), cfg, costs, via)


# Feature: skylit-futures-strategy-engine, Property 43: Accounting invariant
@given(case=stop_cases())
def test_full_exit_at_initial_stop(case: StopCase) -> None:
    place = case.place
    entry, stop, _ = place.orders()
    book = SimBook().submit_bracket(entry, stop)
    events: list[FillEvent] = []
    for bar in case.bars:
        book, new = on_bar(book, bar, case.cfg, rth=RTH)
        events.extend(new)
    if case.via == "close_all":
        assert place.key in book.trades, "bar 0 fills the entry and leaves the stop"
        prices = {place.instrument: place.stop}
        book, new = close_all(book, case.bars[-1].close_ns, prices, case.cfg, reason="flat")
        events.extend(new)
    assert not book.trades
    event(f"via {case.via}, {place.entry_kind} entry")

    trade = check_trade(place, case.costs, events)
    assert all(f.price == place.stop for f in trade.exits)
    assert trade.net == -trade.r
    assert trade.r_multiple == Decimal(-1)
