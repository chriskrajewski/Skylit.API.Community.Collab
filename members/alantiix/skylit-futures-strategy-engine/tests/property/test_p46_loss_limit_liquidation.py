"""Property 46: Loss-limit liquidation.

*For any* bar sequence, positions and balances, when worst-price equity reaches
the MLL_Floor the simulator liquidates, sets the balance to the floor (or the
open-price value if already below), ends the attempt as failed and rejects
every later order; when worst-price day P&L reaches -DLL without an MLL
breach, it liquidates, sets day P&L to -DLL (or the open-price value), blocks
entries until 18:00 and keeps the attempt active; when both hold on one bar,
the MLL outcome applies.

**Inputs.** A random ``AccountConfig``: the starting balance, the Maximum Loss
Limit and the Daily Loss Limit are the defaults or random dollar amounts
(mostly on a $0.25 grid, so worst-price equity can land exactly on a limit),
with both loss limits on five times in eight and each other on/off pair once.
The profit target is $10,000,000.00 and the position cap 1,000
Micro_Equivalents, so no pass and no cap rejection interferes. One to four
back-to-back sessions from Monday 2 March 2026, each with 1 to 12 1-minute
bars from 10:00 that carry an MES and an MNQ bar. On a bar the driver places
an entry (adding to the instrument's position or opening one), places an exit
(part or all of a position, any exit role), or holds; every order goes
through ``check_order`` and fills on that bar inside its range, with a drawn
per-contract fee. Before the Flat_Deadline the driver closes what is still
open with exit fills, so ``end_trading_day`` has nothing to close.

Holding bars are mostly aimed: for the floor, minus the DLL from the day's
start balance, or the lower of the two, the driver solves (exactly, in
``Fraction``) for the worst price of one open instrument that puts
worst-price equity on that level (shifting the other held instrument's bar a
few ticks when that makes the solution a whole tick), then moves it -1, 0, 1,
2, 10 or 100 ticks past the first tick that meets the limit, with the open 0
to 300 ticks back from the worst price. So both sides of every boundary,
exact equality, both limits on one bar and gaps through a limit (the
open-price value) all occur.

**Reference model.** ``Model`` keeps exact ``Decimal`` dollars: cash plus,
per instrument, net contracts and signed tick cost, so balance plus
unrealized P&L at any prices is ``cash + sum(tick value x (qty x price +
cost))`` whatever the lot matching. On each bar it values each position at
its worst price (low for a long, high for a short) and at the open, then
applies Req 15.8-15.12 as written: the MLL check first, then the DLL check
(once per trading day; after it the book is flat and entries are refused).
Attempts start at the starting balance with the floor at start minus the MLL,
and the floor trails day-end balances (Req 15.6-15.7), so later days use the
right floor.

**Checks.** On every bar the events equal the model's: none, a
``maximum_loss_limit`` ``Liquidation`` (every open position closed at its
worst price, post balance ``min(floor, open-price value)``) followed by a
failed ``AttemptResult`` with the breach bar's open instant, final balance,
floor and trading days elapsed, or a ``daily_loss_limit`` ``Liquidation``
(day P&L ``min(-DLL, open-price value)``) with the attempt still active.
After a fail every entry and exit order of the attempt is refused; during a
DLL block every entry is refused; at the next trading day's start entries are
accepted again. The simulator has no working orders of its own, so "cancel
every working order" is checked as the ``Liquidation`` event that tells the
Backtester to cancel them.

**Validates: Requirements 15.8, 15.9, 15.10, 15.11, 15.12**
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal
from fractions import Fraction
from typing import Final, Literal

from hypothesis import event, given
from hypothesis import strategies as st

from fse.config.schema.account import AccountConfig, AccountRuleId
from fse.engine.types import Bar, Fill, Money, Order, OrderRole, Side
from fse.sim.account import (
    AccountRejection,
    AccountSim,
    AttemptEnded,
    AttemptStarted,
    FlatDeadlineClose,
    ForcedExit,
    Liquidation,
    RejectionRule,
)
from fse.timekit import NS_PER_MINUTE, Instant, SessionCalendar, SessionTimes, ny_instant

D = Decimal
ZERO: Final = D("0.00")
CENT: Final = D("0.01")

INSTRUMENTS: Final[tuple[str, ...]] = ("MES", "MNQ")
TICK_USD: Final[Mapping[str, Money]] = {"MES": D("1.25"), "MNQ": D("0.50")}  # Req 13.9
SESSIONS: Final[tuple[date, ...]] = tuple(date(2026, 3, d) for d in (2, 3, 4, 5))  # Mon-Thu
FIRST_BAR: Final = time(10, 0)
FLAT_DEADLINE: Final = time(16, 10)
START_PRICE: Final = 20_000  # ticks: 5000.00 points

FEES_PER_CONTRACT: Final[tuple[Money, ...]] = tuple(
    D(x) for x in ("0.00", "0.25", "0.50", "1.00", "0.74", "1.37")
)
DEPTHS: Final[tuple[int, ...]] = (0, -1, 0, 1, 0, 2, 10, 100)  # ticks past the first breaching tick
GAPS: Final[tuple[int, ...]] = (0, 0, 1, 3, 20, 300)  # ticks from the worst price to the open
SIDES: Final[tuple[Side, ...]] = ("buy", "sell")
EXIT_ROLES: Final[tuple[OrderRole, ...]] = ("stop", "tp1", "tp2", "exit")

ENABLED_PAIRS: Final[tuple[tuple[bool, bool], ...]] = ((True, False), (False, True), (False, False))

type Ohlc = tuple[int, int, int, int]
type Aim = Literal["random", "mll", "dll", "both"]
AIMS: Final[tuple[Aim, ...]] = ("dll", "mll", "dll", "both", "mll", "dll", "random")


# ---------------------------------------------------------------- strategies


def _quarters(lo: str, hi: str) -> st.SearchStrategy[Money]:
    """Dollars on a $0.25 grid, so equity can land exactly on a limit."""
    return st.integers(int(D(lo) * 4), int(D(hi) * 4)).map(lambda n: (D(n) / 4).quantize(CENT))


def _cents(lo: str, hi: str) -> st.SearchStrategy[Money]:
    return st.integers(int(D(lo) * 100), int(D(hi) * 100)).map(lambda n: D(n).scaleb(-2))


def _money(lo: str, hi: str) -> st.SearchStrategy[Money]:
    return st.one_of(_quarters(lo, hi), _quarters(lo, hi), _quarters(lo, hi), _cents(lo, hi))


@st.composite
def account_configs(draw: st.DrawFn) -> AccountConfig:
    start = draw(st.one_of(st.just(D("50000.00")), _money("6000", "100000")))
    mll = draw(st.one_of(st.just(D("2000.00")), _money("1", "5000")))
    dll = draw(st.one_of(st.just(D("1000.00")), _money("1", "3000")))
    mll_on, dll_on = draw(st.sampled_from(((True, True),) * 5 + ENABLED_PAIRS))
    return AccountConfig.model_validate(
        {
            "starting_balance": {"value": str(start)},
            "profit_target": {"value": "10000000.00"},
            "maximum_loss_limit": {"enabled": mll_on, "value": str(mll)},
            "daily_loss_limit": {"enabled": dll_on, "value": str(dll)},
            "position_cap": {"micro_equivalents": 1000},
        }
    )


@st.composite
def random_ohlc(draw: st.DrawFn, last: int) -> Ohlc:
    o = last + draw(st.integers(-8, 8))
    c = o + draw(st.integers(-60, 60))
    h = max(o, c) + draw(st.integers(0, 40))
    low = min(o, c) - draw(st.integers(0, 40))
    return o, h, low, c


# ---------------------------------------------------------------- reference model


def worst_price(qty: int, ohlc: Ohlc) -> int:
    """The low for a long, the high for a short (Req 15.8); any price when flat."""
    o, h, low, _ = ohlc
    return low if qty > 0 else h if qty < 0 else o


@dataclass(slots=True)
class Model:
    """Req 15.6-15.12 in exact dollars, for one run of sequential attempts."""

    start: Money
    mll: Money | None  # None when the rule is disabled
    dll: Money | None
    number: int = 0
    first_index: int = 0
    failed: bool = True  # no attempt is active before the first trading day
    floor: Money | None = None
    cash: Money = ZERO
    qty: dict[str, int] = field(default_factory=dict)
    cost: dict[str, int] = field(default_factory=dict)  # minus the signed qty x price, ticks
    day_start: Money = ZERO
    dll_hit: bool = False

    def equity(self, prices: Mapping[str, int]) -> Money:
        """Balance plus unrealized P&L with each instrument valued at ``prices``."""
        total = self.cash
        for instrument, qty in self.qty.items():
            total += TICK_USD[instrument] * (qty * prices[instrument] + self.cost[instrument])
        return total

    def open(self) -> dict[str, int]:
        return {i: q for i, q in self.qty.items() if q}

    def balance(self) -> Money:
        assert not self.open(), "the balance is read on a flat book only"
        return self.equity(dict.fromkeys(INSTRUMENTS, 0))

    def fill(self, instrument: str, side: Side, qty: int, price: int, fees: Money) -> None:
        signed = qty if side == "buy" else -qty
        self.qty[instrument] = self.qty.get(instrument, 0) + signed
        self.cost[instrument] = self.cost.get(instrument, 0) - signed * price
        self.cash -= fees

    def settle(self, balance: Money) -> None:
        """Every position closed with the balance set by a rule."""
        self.cash = balance
        self.qty.clear()
        self.cost.clear()

    def start_day(self, index: int) -> None:
        if self.failed:
            self.number += 1
            self.first_index = index
            self.failed = False
            self.settle(self.start)
            self.floor = None if self.mll is None else self.start - self.mll
        self.day_start = self.balance()
        self.dll_hit = False

    def end_day(self) -> None:
        if self.floor is not None and self.mll is not None:
            self.floor = min(self.start, max(self.floor, self.balance() - self.mll))

    def dll_level(self) -> Money | None:
        """The equity at which day P&L is minus the DLL, while the DLL can still fire."""
        if self.dll is None or self.dll_hit:
            return None
        return self.day_start - self.dll


# ---------------------------------------------------------------- driver


def make_bar(instrument: str, t: Instant, ohlc: Ohlc) -> Bar:
    o, h, low, c = ohlc
    return Bar(
        instrument=instrument,
        contract=f"{instrument}H6",
        interval_s=60,
        open_ns=t,
        close_ns=t + NS_PER_MINUTE,
        o=o / 4,
        h=h / 4,
        l=low / 4,
        c=c / 4,
        v=100.0,
        o_t=o,
        h_t=h,
        l_t=low,
        c_t=c,
        source="projectx",
    )


class Driver:
    """Drives ``AccountSim`` the way the Backtester does and checks it against ``Model``."""

    def __init__(self, cfg: AccountConfig, fee: Money, data: st.DataObject) -> None:
        calendar = SessionCalendar(
            date(2026, 3, 1), date(2026, 3, 31), times=cfg.session_times(SessionTimes())
        )
        self.sim = AccountSim(cfg, calendar, dict.fromkeys(INSTRUMENTS, fee))
        mll, dll = cfg.maximum_loss_limit, cfg.daily_loss_limit
        self.model = Model(
            start=cfg.starting_balance.value,
            mll=mll.value if mll.enabled else None,
            dll=dll.value if dll.enabled else None,
        )
        rules: tuple[tuple[AccountRuleId, bool], ...] = (
            ("maximum_loss_limit", mll.enabled),
            ("daily_loss_limit", dll.enabled),
        )
        self.disabled = tuple(rule for rule, enabled in rules if not enabled)
        self.fee = fee
        self.data = data
        self.last = dict.fromkeys(INSTRUMENTS, START_PRICE)
        self.index = 0
        self.session = SESSIONS[0]
        self.orders = 0

    # ---------------------------------------------------------------- helpers

    def draw[T](self, strategy: st.SearchStrategy[T], label: str) -> T:
        return self.data.draw(strategy, label=label)

    def order(self, t: Instant, instrument: str, side: Side, qty: int, role: OrderRole) -> Order:
        self.orders += 1
        return Order(f"o{self.orders}", None, instrument, side, "market", qty, None, t, role)

    def probe(self, t: Instant, rule: RejectionRule, *, exits: bool) -> None:
        """Every entry (and, with ``exits``, every exit) order is refused for ``rule``."""
        roles: list[OrderRole] = ["entry"]
        if exits:
            roles.append(self.draw(st.sampled_from(EXIT_ROLES), "probe exit role"))
        for role in roles:
            order = self.order(
                t,
                self.draw(st.sampled_from(INSTRUMENTS), "probe instrument"),
                self.draw(st.sampled_from(SIDES), "probe side"),
                self.draw(st.integers(1, 5), "probe qty"),
                role,
            )
            rejection = self.sim.check_order(order)
            assert isinstance(rejection, AccountRejection), (role, rule, rejection)
            assert (rejection.rule, rejection.client_id) == (rule, order.client_id)

    def bars(self, t: Instant, prices: Mapping[str, Ohlc]) -> dict[str, Bar]:
        for instrument in INSTRUMENTS:
            self.last[instrument] = prices[instrument][3]
        return {i: make_bar(i, t, prices[i]) for i in INSTRUMENTS}

    def aim_level(self, aim: Aim) -> Money | None:
        m = self.model
        floor, dll = m.floor, m.dll_level()
        if aim == "mll":
            return floor
        if aim == "dll":
            return dll
        if aim == "both" and floor is not None and dll is not None:
            return min(floor, dll)
        return None

    def draw_prices(self, aim: Aim) -> dict[str, Ohlc]:
        """One bar per instrument; an aimed bar puts one position's worst price on a limit."""
        level = self.aim_level(aim)
        held = sorted(self.model.open())
        target: str | None = None
        if level is not None and held:
            target = self.draw(st.sampled_from(held), "aimed instrument")
        prices = {
            i: self.draw(random_ohlc(self.last[i]), f"{i} bar") for i in INSTRUMENTS if i != target
        }
        if target is not None and level is not None:
            aimed = self.aimed_ohlc(target, level, prices)
            prices[target] = (
                aimed
                if aimed is not None
                else self.draw(random_ohlc(self.last[target]), f"{target} bar")
            )
        return prices

    def solve(self, target: str, level: Money, others: Mapping[str, Ohlc]) -> Fraction:
        """The worst ``target`` price that puts worst-price equity exactly on ``level``."""
        m = self.model
        rest = m.cash
        for instrument, qty in m.qty.items():
            if instrument != target:
                worst = worst_price(qty, others[instrument])
                rest += TICK_USD[instrument] * (qty * worst + m.cost[instrument])
        # level = rest + tick value x (qty x w + cost), solved for w.
        return (
            (Fraction(level) - Fraction(rest)) / Fraction(TICK_USD[target]) - m.cost[target]
        ) / m.qty[target]

    def aimed_ohlc(self, target: str, level: Money, others: dict[str, Ohlc]) -> Ohlc | None:
        """A ``target`` bar whose worst price is ``depth`` ticks past the first one at ``level``.

        When the solution is not a whole tick and the other instrument is held
        too, its bar is shifted by up to 60 ticks, if that makes it whole, so
        equity can land exactly on the limit.
        """
        exact = self.solve(target, level, others)
        partners = [i for i in sorted(self.model.open()) if i != target]
        if exact.denominator != 1 and partners:
            partner = partners[0]
            for shift in sorted(range(-60, 61), key=abs):
                o, h, low, c = others[partner]
                shifted = {**others, partner: (o + shift, h + shift, low + shift, c + shift)}
                if self.solve(target, level, shifted).denominator == 1:
                    others.update(shifted)
                    exact = self.solve(target, level, others)
                    break
        depth = self.draw(st.sampled_from(DEPTHS), "depth")
        gap = self.draw(st.sampled_from(GAPS), "gap")
        wick = self.draw(st.integers(0, 30), "wick")
        qty = self.model.qty[target]
        if qty > 0:  # equity rises with the low: the limit holds for w <= exact
            low = math.floor(exact) - depth
            o = low + gap
            h = o + wick
        else:  # equity falls as the high rises: the limit holds for w >= exact
            h = math.ceil(exact) + depth
            o = h - gap
            low = o - wick
        if low < 1:
            return None
        return o, h, low, self.draw(st.integers(low, h), "close")

    # ---------------------------------------------------------------- the run

    def run_session(self, index: int, session: date) -> None:
        self.start_day(index, session)
        t = ny_instant(session, FIRST_BAR)
        for _ in range(self.draw(st.integers(1, 12), "bars")):
            self.step(t)
            t += NS_PER_MINUTE
        self.end_day(t)

    def start_day(self, index: int, session: date) -> None:
        m = self.model
        self.index, self.session = index, session
        new_attempt = m.failed
        events = self.sim.start_trading_day(session)
        m.start_day(index)
        expected = [AttemptStarted(m.number, session, m.start, m.floor)] if new_attempt else []
        assert events == expected
        assert self.sim.active
        assert (self.sim.attempt_number, self.sim.balance) == (m.number, m.balance())
        # Req 15.11: a DLL block lasts only until the next trading day starts.
        probe = self.order(ny_instant(session, FIRST_BAR), "MES", "buy", 1, "entry")
        assert self.sim.check_order(probe) is probe

    def step(self, t: Instant) -> None:
        m = self.model
        if m.failed:  # Req 15.9: every later order in the attempt is refused
            self.probe(t, "maximum_loss_limit", exits=True)
            prices = self.draw_prices("random")
            assert self.sim.on_bar(self.bars(t, prices)) == []
            assert not self.sim.active
            return
        if m.dll_hit:  # Req 15.11: entries refused, the attempt stays active
            self.probe(t, "daily_loss_limit", exits=False)
            assert self.sim.active
            action = "hold"
        else:  # with a position open, mostly hold, so aimed bars meet it
            actions = ("hold", "hold", "entry", "exit") if m.open() else ("entry", "entry", "hold")
            action = self.draw(st.sampled_from(actions), "action")
        if action == "hold":
            self.check_bar(t, self.draw_prices(self.draw(st.sampled_from(AIMS), "aim")))
            return
        order = self.entry(t) if action == "entry" else self.exit(t)
        assert self.sim.check_order(order) is order
        prices = self.draw_prices("random")
        self.fill(t, order, prices)
        self.check_bar(t, prices)

    def entry(self, t: Instant) -> Order:
        instrument = self.draw(st.sampled_from(INSTRUMENTS), "entry instrument")
        held = self.model.qty.get(instrument, 0)
        side: Side = (
            "buy"
            if held > 0
            else "sell"
            if held < 0
            else self.draw(st.sampled_from(SIDES), "entry side")
        )
        return self.order(t, instrument, side, self.draw(st.integers(1, 5), "entry qty"), "entry")

    def exit(self, t: Instant) -> Order:
        held = self.model.open()
        instrument = self.draw(st.sampled_from(sorted(held)), "exit instrument")
        qty = held[instrument]
        return self.order(
            t,
            instrument,
            "sell" if qty > 0 else "buy",
            self.draw(st.integers(1, abs(qty)), "exit qty"),
            self.draw(st.sampled_from(EXIT_ROLES), "exit role"),
        )

    def fill(self, t: Instant, order: Order, prices: Mapping[str, Ohlc]) -> None:
        _, high, low, _ = prices[order.instrument]
        price = self.draw(st.integers(low, high), "fill price")
        fees = self.fee * order.qty
        self.sim.on_fill(Fill(order.client_id, t, price, order.qty, fees), order)
        self.model.fill(order.instrument, order.side, order.qty, price, fees)

    def end_day(self, t: Instant) -> None:
        m = self.model
        held = m.open()
        if not m.failed and held:  # close what is open before the Flat_Deadline
            orders = [
                self.order(t, i, "sell" if q > 0 else "buy", abs(q), "exit")
                for i, q in sorted(held.items())
            ]
            prices = self.draw_prices("random")
            for order in orders:
                assert self.sim.check_order(order) is order
                self.fill(t, order, prices)
            self.check_bar(t, prices)
        failed = m.failed
        events = self.sim.end_trading_day({})
        if failed:
            assert events == []
            return
        deadline = ny_instant(self.session, FLAT_DEADLINE)
        assert events == [FlatDeadlineClose(self.session, deadline, ())]
        m.end_day()
        assert (self.sim.balance, self.sim.mll_floor) == (m.balance(), m.floor)

    # ---------------------------------------------------------------- the property

    def check_bar(self, t: Instant, prices: Mapping[str, Ohlc]) -> None:
        m = self.model
        events = self.sim.on_bar(self.bars(t, prices))
        held = m.open()
        worst = {i: worst_price(m.qty.get(i, 0), prices[i]) for i in INSTRUMENTS}
        worst_equity = m.equity(worst)
        open_equity = m.equity({i: prices[i][0] for i in INSTRUMENTS})
        exits = tuple(
            ForcedExit(i, "sell" if q > 0 else "buy", abs(q), worst[i], ZERO)
            for i, q in sorted(held.items())
        )
        floor, dll = m.floor, m.dll
        dll_met = dll is not None and not m.dll_hit and worst_equity - m.day_start <= -dll

        if floor is not None and worst_equity <= floor:  # Req 15.8, 15.9, 15.12
            balance = min(floor, open_equity)
            event("MLL liquidation")
            if dll_met:
                event("MLL and DLL on one bar")
            if worst_equity == floor:
                event("MLL: worst-price equity exactly at the floor")
            if open_equity < floor:
                event("MLL: open-price value below the floor")
            assert len(events) == 2, events
            liquidation, ended = events
            assert liquidation == Liquidation(
                "maximum_loss_limit",
                self.session,
                t,
                exits,
                balance,
                balance - m.day_start,
            )
            assert isinstance(ended, AttemptEnded)
            r = ended.result
            assert (
                r.number,
                r.outcome,
                r.first_session,
                r.last_session,
                r.trading_days,
                r.ended_ns,
                r.final_balance,
                r.mll_floor,
                r.disabled_rules,
            ) == (
                m.number,
                "failed",
                SESSIONS[m.first_index],
                self.session,
                self.index - m.first_index + 1,
                t,
                balance,
                floor,
                self.disabled,
            )
            assert self.sim.results[-1] == r
            assert not self.sim.active
            assert self.sim.positions == {}
            assert self.sim.balance == balance
            m.settle(balance)
            m.failed = True
        elif dll_met:  # Req 15.10, 15.11
            assert dll is not None
            day_pnl = min(-dll, open_equity - m.day_start)
            balance = m.day_start + day_pnl
            event("DLL liquidation")
            if worst_equity - m.day_start == -dll:
                event("DLL: worst-price day P&L exactly at -DLL")
            if open_equity - m.day_start < -dll:
                event("DLL: open-price value below -DLL")
            assert events == [
                Liquidation("daily_loss_limit", self.session, t, exits, balance, day_pnl)
            ]
            assert self.sim.active
            assert self.sim.attempt_number == m.number
            assert (self.sim.balance, self.sim.day_pnl) == (balance, day_pnl)
            assert self.sim.positions == {}
            m.settle(balance)
            m.dll_hit = True
        else:
            assert events == []
            assert self.sim.active
            assert self.sim.positions == held


@given(cfg=account_configs(), fee=st.sampled_from(FEES_PER_CONTRACT), data=st.data())
def test_loss_limit_liquidation(cfg: AccountConfig, fee: Money, data: st.DataObject) -> None:
    driver = Driver(cfg, fee, data)
    sessions = data.draw(st.integers(1, len(SESSIONS)), label="sessions")
    for index, session in enumerate(SESSIONS[:sessions]):
        driver.run_session(index, session)
