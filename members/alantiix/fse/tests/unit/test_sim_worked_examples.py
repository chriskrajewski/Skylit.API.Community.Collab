"""Worked examples of Req 13 and Req 15, driven end to end (task 18.10).

Each example places real brackets in a ``SimBook``, runs 1-minute bars
through ``fse.sim.fills.on_bar`` and books every fill in an ``AccountSim`` the
way the Backtester does: the bar's fills first, then the account checks on
that bar. Costs and slippage are zero, so the dollar amounts are the ones in
the requirement text. Sessions are synthetic, in March 2026 (Monday 2 March
onward), under the default 50K account rules. Prices are ticks: 23200 is
5800.00 points.

**Validates: Requirements 13.9, 13.11, 15.7, 15.12, 15.13**
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal

import pytest

from fse.config.schema.account import AccountConfig
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
from fse.sim.account import (
    AccountEvent,
    AccountRejection,
    AccountSim,
    AttemptEnded,
    AttemptResult,
    Liquidation,
)
from fse.sim.fills import SimBook, close_all, on_bar
from fse.timekit import NS_PER_MINUTE, Instant, SessionCalendar, SessionTimes, ny_instant

D = Decimal
INSTRUMENTS = ("MES", "MNQ", "ES", "NQ")
POINT_VALUE = {"MES": D(5), "MNQ": D(2), "ES": D(50), "NQ": D(20)}
"""Dollars per point per contract, as Req 13.9 states them."""
PRICE = {"MES": 23200, "MNQ": 80000, "ES": 23200, "NQ": 80000}
"""The entry price in ticks per instrument (5800.00 and 20000.00 points)."""
FAR = 2000
"""Ticks from the entry to the bracket leg a round trip does not exit at."""

MON, TUE, WED, THU = (date(2026, 3, d) for d in (2, 3, 4, 5))
ACCOUNT = AccountConfig()
FILLS = FillsConfig.model_validate(
    {
        "trade_through_ticks": 1,
        "slippage_ticks": 0,
        "costs": {name: {"commission": "0", "exchange_fee": "0"} for name in INSTRUMENTS},
    }
)


def calendar(cfg: AccountConfig) -> SessionCalendar:
    return SessionCalendar(
        date(2026, 3, 1), date(2026, 3, 31), times=cfg.session_times(SessionTimes())
    )


def at(session: date, hh: int, mm: int) -> Instant:
    return ny_instant(session, time(hh, mm))


def bar(t: Instant, instrument: str, o: Ticks, extreme: Ticks, c: Ticks) -> Bar:
    """A 1-minute bar that opens at ``o``, reaches ``extreme`` and closes at ``c``."""
    h, low = max(o, extreme, c), min(o, extreme, c)
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


@dataclass
class Desk:
    """A Fill_Simulator book and an Account_Simulator stepped together, bar by bar."""

    sim: AccountSim
    calendar: SessionCalendar
    book: SimBook = field(default_factory=SimBook)
    closed: list[Trade] = field(default_factory=list)
    seq: int = 0

    def place(
        self,
        instrument: str,
        direction: Direction,
        qty: int,
        *,
        entry: Ticks,
        stop: Ticks,
        target: Ticks,
        placed_at: Instant,
    ) -> None:
        """Submit a limit entry with its protective stop and one target for all contracts."""
        session = self.sim.session
        assert session is not None
        self.seq += 1
        key = SetupKey(instrument, "floor_ceiling_bounce", entry / 4, direction, session, self.seq)
        entry_side: Side = "buy" if direction == "long" else "sell"
        exit_side: Side = "sell" if direction == "long" else "buy"

        def leg(role: OrderRole, kind: OrderKind, side: Side, price: Ticks) -> Order:
            cid = f"{self.seq}-{role}"
            return Order(cid, key, instrument, side, kind, qty, price, placed_at, role)

        self.book = self.book.submit_bracket(
            leg("entry", "limit", entry_side, entry),
            leg("stop", "stop", exit_side, stop),
            [leg("tp1", "limit", exit_side, target)],
        )

    def step(self, b: Bar) -> list[AccountEvent]:
        """Fill the book on ``b``, book each fill in the account, then run the bar checks."""
        session = self.sim.session
        assert session is not None
        rth = (self.calendar.rth_open(session), self.calendar.rth_close(session))
        self.book, fills = on_bar(self.book, b, FILLS, rth=rth)
        for event in fills:
            self.sim.on_fill(event.fill, event.order)
            if event.closed is not None:
                self.closed.append(event.closed)
        return self.sim.on_bar({b.instrument: b})


def make_desk(cfg: AccountConfig = ACCOUNT) -> Desk:
    cal = calendar(cfg)
    return Desk(AccountSim(cfg, cal, {name: D("0") for name in INSTRUMENTS}), cal)


def round_trip(
    desk: Desk, instrument: str, direction: Direction, qty: int, exit_ticks: int
) -> Trade:
    """One trade in the current session: entry fill at 10:00, exit at 10:01.

    ``exit_ticks > 0`` exits at the target that many ticks in the trade's
    favor; ``exit_ticks < 0`` exits at the initial stop that many ticks
    against it.
    """
    session = desk.sim.session
    assert session is not None
    s = 1 if direction == "long" else -1
    entry = PRICE[instrument]
    if exit_ticks > 0:
        target, stop = entry + s * exit_ticks, entry - s * FAR
    else:
        target, stop = entry + s * FAR, entry + s * exit_ticks
    desk.place(
        instrument,
        direction,
        qty,
        entry=entry,
        stop=stop,
        target=target,
        placed_at=at(session, 10, 0),
    )
    # The entry bar trades through the limit by 1 tick and stays clear of the stop.
    events = desk.step(bar(at(session, 10, 0), instrument, entry + 2 * s, entry - s, entry))
    if exit_ticks > 0:  # trades through the target by 1 tick
        exit_bar = bar(at(session, 10, 1), instrument, target, target + s, target)
    else:  # reaches the stop without opening past it
        exit_bar = bar(at(session, 10, 1), instrument, stop + 2 * s, stop, stop)
    events += desk.step(exit_bar)
    assert events == []  # no loss limit fired
    trade = desk.closed[-1]
    assert [f.price for f in trade.exits] == [target if exit_ticks > 0 else stop]
    assert desk.book.trades == {}
    return trade


def day(
    desk: Desk, session: date, instrument: str, direction: Direction, qty: int, exit_ticks: int
) -> list[AccountEvent]:
    """One trading day with one round trip; every account event of the day."""
    events = desk.sim.start_trading_day(session)
    round_trip(desk, instrument, direction, qty, exit_ticks)
    return events + desk.sim.end_trading_day({})


def ended(events: list[AccountEvent]) -> AttemptResult:
    [result] = [e.result for e in events if isinstance(e, AttemptEnded)]
    return result


# ---------------------------------------------------------------- Req 13.9 contract values


@pytest.mark.parametrize("ticks", [1, 4])
@pytest.mark.parametrize("instrument", INSTRUMENTS)
def test_contract_values_price_a_one_contract_trade(instrument: str, ticks: int) -> None:
    """A 0.25-point tick and a 1-point gain on 1 contract earn the Req 13.9 values."""
    desk = make_desk()
    desk.sim.start_trading_day(MON)
    trade = round_trip(desk, instrument, "long", 1, ticks)
    expected = POINT_VALUE[instrument] * ticks / 4  # MES 1.25 / 5, MNQ 0.50 / 2, ...
    assert trade.net == expected
    assert desk.sim.balance == D("50000.00") + expected


# ---------------------------------------------------------------- Req 13.11 R_Multiple


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("instrument", INSTRUMENTS)
def test_a_full_exit_at_the_initial_stop_with_zero_costs_is_minus_one_r(
    instrument: str, direction: Direction
) -> None:
    desk = make_desk()
    desk.sim.start_trading_day(MON)
    trade = round_trip(desk, instrument, direction, 3, -16)  # 4 points against, 3 contracts
    risk = 4 * POINT_VALUE[instrument] * 3
    assert trade.exits[0].price == trade.initial_stop
    assert trade.exits[0].qty == trade.qty_at_entry == 3
    assert (trade.net, trade.r) == (-risk, risk)
    assert trade.r_multiple == D(-1)
    assert desk.sim.balance == D("50000.00") - risk


# ---------------------------------------------------------------- Req 15.7 MLL_Floor


def test_an_end_of_day_balance_of_51200_sets_the_mll_floor_to_49200() -> None:
    desk = make_desk()
    desk.sim.start_trading_day(MON)
    round_trip(desk, "ES", "long", 1, 96)  # 24 points x $50 = +1,200
    # The floor moves only when the trading day ends.
    assert (desk.sim.balance, desk.sim.mll_floor) == (D("51200.00"), D("48000.00"))
    desk.sim.end_trading_day({})
    assert (desk.sim.balance, desk.sim.mll_floor) == (D("51200.00"), D("49200.00"))


def test_an_end_of_day_balance_of_52000_or_more_holds_the_mll_floor_at_50000() -> None:
    desk = make_desk()
    days: list[tuple[date, str, Direction, int, int]] = [
        (MON, "ES", "long", 1, 96),  # +1,200: 51,200
        (TUE, "NQ", "short", 1, 160),  # 40 points x $20 = +800: 52,000
        (WED, "MNQ", "long", 10, 100),  # 10 x 25 points x $2 = +500: 52,500
        (THU, "MES", "short", 4, -180),  # 4 x 45 points x $5 = -900: 51,600
    ]
    floors: list[tuple[Decimal, Decimal | None]] = []
    for session, instrument, direction, qty, exit_ticks in days:
        day(desk, session, instrument, direction, qty, exit_ticks)
        floors.append((desk.sim.balance, desk.sim.mll_floor))
    assert floors == [
        (D("51200.00"), D("49200.00")),
        (D("52000.00"), D("50000.00")),
        (D("52500.00"), D("50000.00")),
        (D("51600.00"), D("50000.00")),
    ]
    [result] = desk.sim.finish()
    assert (result.outcome, result.mll_floor) == ("incomplete", D("50000.00"))


# ---------------------------------------------------------------- Req 15.13 consistency target


def test_a_best_day_of_1650_leaves_the_profit_target_at_3000() -> None:
    desk = make_desk()
    day(desk, MON, "ES", "long", 1, 132)  # 33 points x $50 = +1,650
    assert desk.sim.profit_target == D("3000.00")
    events = day(desk, TUE, "NQ", "long", 1, 270)  # 67.5 points x $20 = +1,350: 53,000
    result = ended(events)
    assert (result.outcome, result.final_balance, result.best_day, result.profit_target) == (
        "passed",
        D("53000.00"),
        D("1650.00"),
        D("3000.00"),
    )


def test_a_best_day_of_2000_raises_the_profit_target_to_3636_37() -> None:
    desk = make_desk()
    day(desk, MON, "NQ", "long", 1, 400)  # 100 points x $20 = +2,000
    assert desk.sim.profit_target == D("3636.37")
    events = day(desk, TUE, "ES", "long", 1, 80)  # +1,000: 53,000 is short of 53,636.37
    assert not any(isinstance(e, AttemptEnded) for e in events)
    assert desk.sim.active
    events = day(desk, WED, "MES", "long", 1, 510)  # 127.5 points x $5 = +637.50: 53,637.50
    result = ended(events)
    assert (result.outcome, result.final_balance, result.best_day, result.profit_target) == (
        "passed",
        D("53637.50"),
        D("2000.00"),
        D("3636.37"),
    )


# ---------------------------------------------------------------- Req 15.12 MLL and DLL on one bar


def both_limits_bar(desk: Desk) -> tuple[Bar, list[AccountEvent]]:
    """Monday +1,200; Tuesday 1 NQ long, then a bar whose low is 100 points down.

    At that low the day is at -2,000 (past the -1,000 DLL) and equity is
    51,200 - 2,000 = 49,200, at the default MLL_Floor. The stop, 150 points
    down, is not reached, so the position is open on the bar.
    """
    day(desk, MON, "ES", "long", 1, 96)
    desk.sim.start_trading_day(TUE)
    entry = PRICE["NQ"]
    desk.place(
        "NQ",
        "long",
        1,
        entry=entry,
        stop=entry - 600,
        target=entry + 600,
        placed_at=at(TUE, 10, 0),
    )
    assert desk.step(bar(at(TUE, 10, 0), "NQ", entry + 2, entry - 1, entry)) == []
    breach = bar(at(TUE, 10, 1), "NQ", entry - 100, entry - 400, entry - 300)
    return breach, desk.step(breach)


def test_the_bar_meets_the_dll_condition_on_its_own() -> None:
    """With the MLL disabled, the same bar fires the DLL: both conditions hold on it."""
    desk = make_desk(AccountConfig.model_validate({"maximum_loss_limit": {"enabled": False}}))
    _, events = both_limits_bar(desk)
    [liquidation] = events
    assert isinstance(liquidation, Liquidation)
    assert liquidation.rule == "daily_loss_limit"
    assert (liquidation.day_pnl, liquidation.balance) == (D("-1000.00"), D("50200.00"))
    assert desk.sim.active


def test_mll_wins_when_one_bar_meets_both_the_mll_and_the_dll() -> None:
    desk = make_desk()
    breach, events = both_limits_bar(desk)
    [liquidation, end] = events
    assert isinstance(liquidation, Liquidation)
    assert liquidation.rule == "maximum_loss_limit"
    assert (liquidation.balance, liquidation.day_pnl) == (D("49200.00"), D("-2000.00"))
    assert isinstance(end, AttemptEnded)
    result = end.result
    assert (result.outcome, result.final_balance, result.trading_days, result.ended_ns) == (
        "failed",
        D("49200.00"),
        2,
        breach.open_ns,
    )
    assert not desk.sim.active
    assert desk.sim.positions == {}
    # The Backtester mirrors the forced exit in its book at the breach price.
    [forced] = liquidation.exits
    assert (forced.instrument, forced.side, forced.qty) == ("NQ", "sell", 1)
    desk.book, closes = close_all(
        desk.book, breach.open_ns, {forced.instrument: forced.price}, FILLS, reason="mll"
    )
    [close] = closes
    assert close.closed is not None
    assert close.closed.net == D("-2000.00")
    assert (desk.book.trades, desk.book.orders) == ({}, {})
    late = Order("late", None, "MES", "buy", "market", 1, None, at(TUE, 10, 2), "entry")
    rejection = desk.sim.check_order(late)
    assert isinstance(rejection, AccountRejection)
    assert rejection.rule == "maximum_loss_limit"
