"""Property 42: Fill rules match the reference model.

*For any* order book and bar (gap opens included) and any trade-through and
slippage settings, fills equal a reference implementation: limits fill in full
at the limit price only when traded through by the configured distance; stops
fill at the stop price or the gapped open, moved against the order by the
slippage; market orders fill at the next bar open plus slippage; targets are
not checked on the entry fill bar; a bar that meets both stop and target fills
only the stop for all open contracts; and a trade open across missing bars is
flagged with the missing-bar count.

Each case is one instrument's stream of 1-minute bars (about 1 in 5 missing,
some opening up to 30 ticks away from the last close), 1 to 4 brackets (limit
or stop entry, protective stop, 0 to 2 targets) with any trade-through and
slippage from 0 to 20 ticks, and an RTH window that is either the whole
session or a few minutes of the stream, so missing bars occur both inside and
outside it. A bracket may get a market exit, placed just before a chosen bar
while its trade is open, the way the Order_Planner would.

The reference model plays each bracket alone, bar by bar, straight from the
wording of Requirement 13. The ``SimBook`` processes every bracket together;
its fills per Setup_Key, the trades it closes, and the trades it leaves open
must equal the reference.

**Validates: Requirements 13.1, 13.3, 13.4, 13.5, 13.6, 13.12**
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from typing import Final

from hypothesis import event, example, given
from hypothesis import strategies as st

from fse.config.schema.fills import FILL_INSTRUMENTS, FILL_TICKS_MAX, FillsConfig
from fse.engine.types import (
    Bar,
    Direction,
    Order,
    OrderKind,
    OrderRole,
    SetupKey,
    Side,
    Ticks,
)
from fse.sim.fills import FillEvent, SimBook, on_bar
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, Instant, ny_instant

SESSION: Final = date(2026, 3, 5)
T0: Final = ny_instant(SESSION, time(9, 30))
FULL_RTH: Final = (0, 390)
"""The RTH window in minutes after ``T0``: 09:30 to 16:00."""

BASE: Final[Ticks] = 23_000
"""Opening price of the bar walk, in ticks."""
MAX_MINUTES: Final = 30
DIRECTIONS: Final[tuple[Direction, ...]] = ("long", "short")
ENTRY_KINDS: Final[tuple[OrderKind, ...]] = ("limit", "stop")
TARGET_ROLES: Final[tuple[OrderRole, ...]] = ("tp1", "tp2")
PLACE_OFFSETS_NS: Final = (0, 1, 30 * NS_PER_SECOND, NS_PER_MINUTE - 1)
"""Bracket placement times: at a bar open, 1 ns after, mid-bar, 1 ns before the next open."""
EXIT_LEADS_NS: Final = (0, 1, 30 * NS_PER_SECOND)
"""How long before the open of its bar a market exit is placed."""
BAR_KINDS: Final = ("normal", "normal", "normal", "gap", "spike", "spike")
STOP_DISTANCES: Final = st.integers(1, 24)
"""Stop distances from the entry, in ticks."""
TARGET_DISTANCES: Final = st.one_of(st.integers(1, 3), st.integers(1, 16))
"""Target distances from the entry, in ticks."""

type Row = tuple[str, Instant, Ticks, int]
"""One fill: client id, open of the fill bar, price in ticks, contracts."""

# ---------------------------------------------------------------- case


@dataclass(frozen=True, slots=True)
class Setup:
    """One bracket and its optional market exit.

    ``exit_bar`` is an index into the case's bars: if the trade is open just
    before that bar, a market exit for ``exit_qty`` contracts is placed
    ``exit_lead_ns`` before the bar's open.
    """

    setup: int
    instrument: str
    direction: Direction
    entry_kind: OrderKind
    qty: int
    entry: Ticks
    stop: Ticks
    targets: tuple[tuple[OrderRole, Ticks, int], ...]
    placed_at: Instant
    exit_bar: int | None
    exit_qty: int
    exit_lead_ns: int

    @property
    def key(self) -> SetupKey:
        return SetupKey(
            self.instrument, "floor_ceiling_bounce", 5800.0, self.direction, SESSION, self.setup + 1
        )

    @property
    def entry_side(self) -> Side:
        return "buy" if self.direction == "long" else "sell"

    @property
    def exit_side(self) -> Side:
        return "sell" if self.direction == "long" else "buy"

    def cid(self, role: OrderRole) -> str:
        return f"s{self.setup}:{role}"

    def leg(self, role: OrderRole, kind: OrderKind, qty: int, price: Ticks) -> Order:
        side = self.entry_side if role == "entry" else self.exit_side
        return Order(
            self.cid(role), self.key, self.instrument, side, kind, qty, price, self.placed_at, role
        )

    def orders(self) -> tuple[Order, Order, list[Order]]:
        entry = self.leg("entry", self.entry_kind, self.qty, self.entry)
        stop = self.leg("stop", "stop", self.qty, self.stop)
        targets = [self.leg(role, "limit", n, price) for role, price, n in self.targets]
        return entry, stop, targets

    def exit_order(self, placed_at: Instant) -> Order:
        return Order(
            self.cid("exit"),
            self.key,
            self.instrument,
            self.exit_side,
            "market",
            self.exit_qty,
            None,
            placed_at,
            "exit",
        )


@dataclass(frozen=True, slots=True)
class Case:
    bars: tuple[Bar, ...]
    setups: tuple[Setup, ...]
    cfg: FillsConfig
    rth_minutes: tuple[int, int]

    @property
    def rth(self) -> tuple[Instant, Instant]:
        lo, hi = self.rth_minutes
        return T0 + lo * NS_PER_MINUTE, T0 + hi * NS_PER_MINUTE


def minute_of(bar: Bar) -> int:
    return (bar.open_ns - T0) // NS_PER_MINUTE


def ohl(bar: Bar) -> tuple[Ticks, Ticks, Ticks]:
    """The bar's open, high and low in ticks."""
    o, h, low = bar.o_t, bar.h_t, bar.l_t
    assert o is not None
    assert h is not None
    assert low is not None
    return o, h, low


# ---------------------------------------------------------------- generators


def make_bar(instrument: str, minute: int, o: Ticks, h: Ticks, low: Ticks, c: Ticks) -> Bar:
    start = T0 + minute * NS_PER_MINUTE
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


@st.composite
def bar_streams(draw: st.DrawFn, instrument: str, n_minutes: int) -> tuple[Bar, ...]:
    bars: list[Bar] = []
    close = BASE
    for minute in range(n_minutes):
        # A gap bar opens up to 30 ticks from the last close, so it can open past
        # a level; a spike bar has wicks of up to 40 ticks, so it can reach both a
        # stop and a target. Other bars move a few ticks.
        kind = draw(st.sampled_from(BAR_KINDS))
        gap = 30 if kind == "gap" else 3
        wick = 40 if kind == "spike" else 3
        o = close + draw(st.integers(-gap, gap))
        c = o + draw(st.integers(-3, 3))
        h = max(o, c) + draw(st.integers(0, wick))
        low = min(o, c) - draw(st.integers(0, wick))
        close = c
        if draw(st.integers(0, 4)):  # about 1 bar in 5 is missing
            bars.append(make_bar(instrument, minute, o, h, low, c))
    return tuple(bars)


@st.composite
def setup_specs(draw: st.DrawFn, setup: int, instrument: str, n_minutes: int, n_bars: int) -> Setup:
    direction = draw(st.sampled_from(DIRECTIONS))
    sign = 1 if direction == "long" else -1
    qty = draw(st.integers(1, 5))
    entry = BASE + draw(st.integers(-12, 12))
    n_targets = draw(st.integers(0, min(2, qty)))
    left = qty
    target_qty: list[int] = []
    for i in range(n_targets):
        n = draw(st.integers(1, left - (n_targets - 1 - i)))
        target_qty.append(n)
        left -= n
    targets = tuple(
        (role, entry + sign * draw(TARGET_DISTANCES), n)
        for role, n in zip(TARGET_ROLES, target_qty, strict=False)
    )
    placed_at = (
        T0
        + draw(st.integers(0, n_minutes // 2)) * NS_PER_MINUTE
        + draw(st.sampled_from(PLACE_OFFSETS_NS))
    )
    exit_bar = draw(st.one_of(st.none(), st.integers(0, n_bars - 1))) if n_bars else None
    return Setup(
        setup=setup,
        instrument=instrument,
        direction=direction,
        entry_kind=draw(st.sampled_from(ENTRY_KINDS)),
        qty=qty,
        entry=entry,
        stop=entry - sign * draw(STOP_DISTANCES),
        targets=targets,
        placed_at=placed_at,
        exit_bar=exit_bar,
        exit_qty=draw(st.integers(1, qty)),
        exit_lead_ns=draw(st.sampled_from(EXIT_LEADS_NS)),
    )


@st.composite
def rth_windows(draw: st.DrawFn, n_minutes: int) -> tuple[int, int]:
    lo = draw(st.integers(0, n_minutes))
    return lo, draw(st.integers(lo + 1, n_minutes + 1))


def tick_settings() -> st.SearchStrategy[int]:
    return st.one_of(st.integers(0, 3), st.integers(0, FILL_TICKS_MAX))


@st.composite
def cases(draw: st.DrawFn) -> Case:
    instrument = draw(st.sampled_from(FILL_INSTRUMENTS))
    n_minutes = draw(st.integers(1, MAX_MINUTES))
    bars = draw(bar_streams(instrument, n_minutes))
    setups = tuple(
        draw(setup_specs(i, instrument, n_minutes, len(bars)))
        for i in range(draw(st.integers(1, 4)))
    )
    cfg = fills_config(draw(tick_settings()), draw(tick_settings()))
    rth = draw(st.one_of(st.just(FULL_RTH), rth_windows(n_minutes)))
    return Case(bars, setups, cfg, rth)


def fills_config(through: int, slippage: int) -> FillsConfig:
    return FillsConfig.model_validate(
        {
            "trade_through_ticks": through,
            "slippage_ticks": slippage,
            "costs": {
                name: {"commission": "0.37", "exchange_fee": "0.35"} for name in FILL_INSTRUMENTS
            },
        }
    )


def stop_and_target_case() -> Case:
    """A 2-lot long whose second bar, after a missing minute, meets its stop and tp1 (Req 13.6).

    The reference fills the buy limit at ``BASE`` on minute 0, then on minute 2
    only the stop, for both contracts, at ``BASE - 8 - 1``, flagged with 1
    missing bar. The random cases reach this branch less often, so it is pinned.
    """
    bars = (
        make_bar("MES", 0, BASE + 2, BASE + 3, BASE - 2, BASE - 1),
        make_bar("MES", 2, BASE, BASE + 20, BASE - 20, BASE),
    )
    setup = Setup(
        setup=0,
        instrument="MES",
        direction="long",
        entry_kind="limit",
        qty=2,
        entry=BASE,
        stop=BASE - 8,
        targets=(("tp1", BASE + 8, 1),),
        placed_at=T0,
        exit_bar=None,
        exit_qty=1,
        exit_lead_ns=0,
    )
    return Case(bars, (setup,), fills_config(1, 1), FULL_RTH)


# ---------------------------------------------------------------- reference model


def reaches_stop(side: Side, stop: Ticks, high: Ticks, low: Ticks) -> bool:
    """Req 13.3: a bar reaches a buy stop when high >= stop, a sell stop when low <= stop."""
    return high >= stop if side == "buy" else low <= stop


def stop_fill_price(side: Side, stop: Ticks, open_: Ticks, slip: int) -> Ticks:
    """Req 13.3: the stop price, or the open if the bar opens past it, moved against the order."""
    opened_past = open_ > stop if side == "buy" else open_ < stop
    base = open_ if opened_past else stop
    return base + slip if side == "buy" else base - slip


def trades_through(side: Side, limit: Ticks, high: Ticks, low: Ticks, through: int) -> bool:
    """Req 13.1: low <= limit - through for a buy limit, high >= limit + through for a sell."""
    return low <= limit - through if side == "buy" else high >= limit + through


def market_fill_price(side: Side, open_: Ticks, slip: int) -> Ticks:
    """Req 13.4: the bar's open, moved against the order."""
    return open_ + slip if side == "buy" else open_ - slip


@dataclass(slots=True)
class Expected:
    """What the reference model says happens to one bracket."""

    targets: list[tuple[OrderRole, Ticks, int]]
    fills: list[Row] = field(default_factory=list)
    entered_on: int | None = None
    closed_on: int | None = None
    open_qty: int = 0
    missing_bars: int = 0
    tags: set[str] = field(default_factory=set)

    def exit(self, cid: str, k: int, bar: Bar, price: Ticks, qty: int) -> bool:
        """Record an exit fill; True when it closes the trade."""
        self.fills.append((cid, bar.open_ns, price, qty))
        self.open_qty -= qty
        if self.open_qty == 0:
            self.closed_on = k
        return self.open_qty == 0


def reference_step(exp: Expected, cfg: FillsConfig, s: Setup, k: int, bar: Bar) -> bool:
    """Apply bar ``k`` to one working bracket; True once its trade has closed."""
    through, slip = cfg.trade_through_ticks, cfg.slippage_ticks
    o, h, low = ohl(bar)
    was_open = exp.entered_on is not None

    # A market exit placed before this bar's open, while the trade is open (Req 13.4).
    if was_open and s.exit_bar == k:
        exp.tags.add("market exit")
        price = market_fill_price(s.exit_side, o, slip)
        if exp.exit(s.cid("exit"), k, bar, price, min(s.exit_qty, exp.open_qty)):
            return True

    if not was_open:
        if s.entry_kind == "stop" and reaches_stop(s.entry_side, s.entry, h, low):
            price = stop_fill_price(s.entry_side, s.entry, o, slip)  # Req 13.3
            if (o > s.entry) if s.entry_side == "buy" else (o < s.entry):
                exp.tags.add("stop entry on a gap open")
        elif s.entry_kind == "limit" and trades_through(s.entry_side, s.entry, h, low, through):
            price = s.entry  # in full, at the limit price, gap opens included (Req 13.1)
            if (o < s.entry) if s.entry_side == "buy" else (o > s.entry):
                exp.tags.add("limit entry on a gap open")
        else:
            return False
        exp.fills.append((s.cid("entry"), bar.open_ns, price, s.qty))
        exp.entered_on = k
        exp.open_qty = s.qty

    # The stop works from the entry fill bar on and takes every open contract;
    # on a bar that also meets a target, only the stop fills (Req 13.5, 13.6).
    if reaches_stop(s.exit_side, s.stop, h, low):
        if not was_open:
            exp.tags.add("stop on the entry fill bar")
        if was_open and any(
            trades_through(s.exit_side, p, h, low, through) for _, p, _ in exp.targets
        ):
            exp.tags.add("stop and target on one bar")
        if (o > s.stop) if s.exit_side == "buy" else (o < s.stop):
            exp.tags.add("stop on a gap open")
        price = stop_fill_price(s.exit_side, s.stop, o, slip)
        return exp.exit(s.cid("stop"), k, bar, price, exp.open_qty)

    # Targets work from the bar after the entry fill (Req 13.5), in full at the
    # limit price when traded through (Req 13.1), never for more than is open.
    if not was_open:
        return False
    for target in list(exp.targets):
        role, price, qty = target
        if trades_through(s.exit_side, price, h, low, through):
            exp.tags.add("target fill")
            exp.targets.remove(target)
            if exp.exit(s.cid(role), k, bar, price, min(qty, exp.open_qty)):
                return True
    return False


def reference(case: Case, s: Setup) -> Expected:
    """Play one bracket alone through the bars by the rules of Requirement 13."""
    exp = Expected(targets=list(s.targets))
    for k, bar in enumerate(case.bars):
        # A bracket works on bars opening at or after its placement (Req 5.5).
        if bar.open_ns >= s.placed_at and reference_step(exp, case.cfg, s, k, bar):
            break
    if exp.entered_on is not None:
        # RTH minutes with no bar strictly between the entry fill bar and the
        # closing bar, or the last bar seen while open (Req 13.12).
        last = exp.closed_on if exp.closed_on is not None else len(case.bars) - 1
        first_m, last_m = minute_of(case.bars[exp.entered_on]), minute_of(case.bars[last])
        present = {minute_of(b) for b in case.bars}
        lo, hi = case.rth_minutes
        exp.missing_bars = sum(
            1 for m in range(first_m + 1, last_m) if m not in present and lo <= m < hi
        )
        if exp.missing_bars:
            exp.tags.add("missing bars flagged")
    return exp


# ---------------------------------------------------------------- system under test


def run_book(case: Case) -> tuple[SimBook, list[FillEvent]]:
    """Place every bracket, then process the bars, placing market exits as they come due."""
    book = SimBook()
    for s in case.setups:
        entry, stop, targets = s.orders()
        book = book.submit_bracket(entry, stop, targets)
    events: list[FillEvent] = []
    for k, bar in enumerate(case.bars):
        for s in case.setups:
            if s.exit_bar == k and s.key in book.trades:
                book = book.submit_exit(s.exit_order(bar.open_ns - s.exit_lead_ns))
        book, new = on_bar(book, bar, case.cfg, rth=case.rth)
        events.extend(new)
    return book, events


def row(e: FillEvent) -> Row:
    return e.fill.client_id, e.fill.bar_open_ns, e.fill.price, e.fill.qty


# ---------------------------------------------------------------- property


# Feature: skylit-futures-strategy-engine, Property 42: Fill rules match the reference model
@given(case=cases())
@example(case=stop_and_target_case())
def test_fill_rules_match_reference(case: Case) -> None:
    book, events = run_book(case)

    by_key: dict[SetupKey, list[FillEvent]] = {s.key: [] for s in case.setups}
    for e in events:
        assert e.order.client_id == e.fill.client_id
        assert e.order.setup_key is not None
        by_key[e.order.setup_key].append(e)

    for s in case.setups:
        exp = reference(case, s)
        for tag in sorted(exp.tags):
            event(tag)
        got = by_key[s.key]
        assert [row(e) for e in got] == exp.fills, f"setup {s.setup}: fills differ"

        closed = [e.closed for e in got if e.closed is not None]
        if exp.closed_on is not None:
            # Closed on its last fill, flagged with the missing RTH bars (Req 13.12).
            trade = got[-1].closed
            assert trade is not None, f"setup {s.setup}: last fill did not close the trade"
            assert len(closed) == 1
            assert trade.missing_bars == exp.missing_bars
            assert trade.initial_stop == s.stop
            assert s.key not in book.brackets
            assert s.key not in book.trades
            assert not any(w.placed.setup_key == s.key for w in book.orders.values())
        elif exp.entered_on is not None:
            assert not closed
            open_trade = book.trades[s.key]
            assert open_trade.open_qty == exp.open_qty
            assert open_trade.missing_bars == exp.missing_bars
        else:
            assert not got
            assert s.key in book.brackets
            assert s.key not in book.trades


def test_reference_fills_only_the_stop_on_a_stop_and_target_bar() -> None:
    """The pinned case means what its docstring says, so the example exercises Req 13.6."""
    case = stop_and_target_case()
    exp = reference(case, case.setups[0])
    entry_bar, both_bar = (b.open_ns for b in case.bars)
    assert exp.fills == [("s0:entry", entry_bar, BASE, 2), ("s0:stop", both_bar, BASE - 9, 2)]
    assert exp.closed_on == 1
    assert exp.missing_bars == 1
    assert "stop and target on one bar" in exp.tags
