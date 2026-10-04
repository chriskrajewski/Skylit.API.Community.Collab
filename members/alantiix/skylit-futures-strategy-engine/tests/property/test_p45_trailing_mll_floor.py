"""Property 45: Trailing MLL_Floor.

*For any* sequence of end-of-day balances in a Combine_Attempt, the MLL_Floor
after each trading day equals min(starting balance, max(previous floor,
end-of-day balance - MLL)), never decreases, never exceeds the starting
balance, and changes only at trading-day ends.

**Inputs.** A random ``AccountConfig``: a starting balance of $1,000.00 to
$300,000.00 (often the $50,000.00 default), a Maximum Loss Limit from one cent
to the starting balance less a cent (often $2,000.00), and random profit
target, consistency target and Daily Loss Limit settings, so attempts also end
by passes and days end early on DLL blocks. Random fees per contract for the
Flat_Deadline close. One to twelve consecutive sessions, each with up to three
sequential round trips (any contract, long or short, fees on both fills); the
last one may stay open for the Flat_Deadline close. A round trip may get a
mark bar with up to 400 ticks of favourable excursion, so intraday equity runs
above the day-end balance.

The day's last round trip may be aimed: its move and entry fee are chosen at
run time so the end-of-day balance lands on a boundary, offset by -1, 0 or +1
cent: ``eod - MLL`` at the current floor (``trail``), halfway from the floor to
the starting balance (``midway``) or at the starting balance (``cap``), or
``eod`` at the floor (``floor``, a breach at 0 and -1 cent).

**Reference model.** Exact ``Decimal`` dollars. A Combine_Attempt starts at
the starting balance with the floor at ``start - MLL`` (Req 15.6); each fill
moves the balance by ``move x tick value x qty - fees``; at each trading-day
end the floor becomes ``min(start, max(floor, eod - MLL))`` (Req 15.7). No bar
goes against an open position (entry and exit bars are flat at the fill price,
mark bars open at the entry price), so worst-price equity equals the balance:
the model expects an MLL breach exactly when the balance is at or below the
floor after a fill or after the flat close, and a DLL liquidation, which then
leaves the balance unchanged, when the day's net is at or below minus the DLL.
Those are the attempt and day boundaries the floor depends on; Property 46
covers the liquidations themselves. Passes are taken from the simulator
(Property 47 covers them): a pass must follow the floor update and needs at
least the configured target. After a pass or a fail the next session starts a
new attempt.

**Checks.** The floor equals the model's after every day start, fill, bar and
day end, so it changes only at day ends; each day-end update is at least the
previous floor and at most the starting balance; the end-of-day balance the
rule reads equals the model's; every attempt result records the model's final
floor.

**Validates: Requirements 15.6, 15.7**
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
from typing import Final, Literal

from hypothesis import event, example, given
from hypothesis import strategies as st

from fse.config.schema.account import AccountConfig
from fse.engine.types import Bar, Fill, Order, Side, Ticks
from fse.sim.account import (
    AccountEvent,
    AccountSim,
    AttemptEnded,
    AttemptStarted,
    FlatDeadlineClose,
    Liquidation,
)
from fse.timekit import NS_PER_MINUTE, Instant, SessionCalendar, SessionTimes, ny_instant

ZERO: Final = Decimal("0.00")
CENT: Final = Decimal("0.01")

# Dollars per tick per contract (Req 13.9), kept apart from the simulator's table.
TICK_USD: Final[Mapping[str, Decimal]] = {
    "MES": Decimal("1.25"),
    "MNQ": Decimal("0.50"),
    "ES": Decimal("12.50"),
    "NQ": Decimal("5.00"),
}
INSTRUMENTS: Final[tuple[str, ...]] = ("MES", "MNQ", "ES", "NQ")
# One position at a time, at most the default cap of 50 Micro_Equivalents.
MAX_QTY: Final[Mapping[str, int]] = {"MES": 10, "MNQ": 10, "ES": 5, "NQ": 5}
BASE_PRICE: Final[Mapping[str, Ticks]] = {"MES": 23_200, "MNQ": 84_000, "ES": 23_200, "NQ": 84_000}
MIN_PRICE: Final = 1_000  # an aimed move shifts the entry so the exit stays at or above this
SIDES: Final[tuple[Side, ...]] = ("buy", "sell")

CAL_FIRST: Final = date(2026, 3, 1)
CAL_LAST: Final = date(2026, 4, 30)
FIRST_SESSION: Final = date(2026, 3, 2)  # a Monday
MAX_DAYS: Final = 12

type AimTarget = Literal["trail", "midway", "cap", "floor"]
AIM_TARGETS: Final[tuple[AimTarget, ...]] = ("trail", "midway", "cap", "floor")
OFFSETS: Final[tuple[Decimal, ...]] = (-CENT, ZERO, CENT)


def reference_floor(floor: Decimal, eod: Decimal, mll: Decimal, start: Decimal) -> Decimal:
    """Req 15.7: the lesser of the start and the greater of the floor and ``eod - MLL``."""
    return min(start, max(floor, eod - mll))


# ---------------------------------------------------------------- plans


@dataclass(frozen=True, slots=True)
class Aim:
    """Land the day-end balance on ``target`` plus ``offset`` (see the module docstring)."""

    target: AimTarget
    offset: Decimal


@dataclass(frozen=True, slots=True)
class TradePlan:
    """One round trip: a market entry, an optional mark bar, then an exit.

    ``move`` is the exit minus the entry in ticks, in the trade's favour (a
    negative move is a loss). With ``at_deadline`` there is no exit fill: the
    Flat_Deadline close exits at ``entry + move`` with the per-contract fee.
    """

    instrument: str
    side: Side
    qty: int
    move: int
    entry_fees: Decimal
    exit_fees: Decimal
    swing: int  # favourable excursion of the mark bar, ticks; 0 for no mark bar
    at_deadline: bool


@dataclass(frozen=True, slots=True)
class DayPlan:
    trades: tuple[TradePlan, ...]
    aim: Aim | None  # applies to the last round trip


@dataclass(frozen=True, slots=True)
class Case:
    cfg: AccountConfig
    fee_per_contract: Mapping[str, Decimal]
    days: tuple[DayPlan, ...]


# ---------------------------------------------------------------- strategies


def cents(lo: int, hi: int) -> st.SearchStrategy[Decimal]:
    return st.integers(lo, hi).map(lambda c: Decimal(c) * CENT)


FEES = st.sampled_from(("0", "0.37", "0.62", "1.24", "2.80", "9.99")).map(Decimal)


@st.composite
def configs(draw: st.DrawFn) -> AccountConfig:
    start = draw(st.one_of(st.just(5_000_000), st.integers(100_000, 30_000_000)))
    top = min(start - 1, 2_000_000)
    mll = draw(st.one_of(st.just(min(200_000, top)), st.integers(1, top)))
    return AccountConfig.model_validate(
        {
            "starting_balance": {"value": str(Decimal(start) * CENT)},
            "maximum_loss_limit": {"enabled": True, "value": str(Decimal(mll) * CENT)},
            "profit_target": {
                "enabled": draw(st.sampled_from((True, True, True, False))),
                "value": str(draw(st.one_of(st.just(Decimal("3000.00")), cents(1, 3_000_000)))),
            },
            "consistency_target": {
                "enabled": draw(st.booleans()),
                "pct": draw(st.sampled_from((55.0, 30.0, 100.0))),
            },
            "daily_loss_limit": {
                "enabled": draw(st.sampled_from((True, True, False))),
                "value": str(draw(st.one_of(st.just(Decimal("1000.00")), cents(1, 2_000_000)))),
            },
        }
    )


@st.composite
def trade_plans(draw: st.DrawFn, last: bool) -> TradePlan:
    instrument = draw(st.sampled_from(INSTRUMENTS))
    return TradePlan(
        instrument=instrument,
        side=draw(st.sampled_from(SIDES)),
        qty=draw(st.integers(1, MAX_QTY[instrument])),
        move=draw(st.integers(-400, 400)),
        entry_fees=draw(FEES),
        exit_fees=draw(FEES),
        swing=draw(st.sampled_from((0, 0, 8, 400))),
        at_deadline=last and draw(st.booleans()),
    )


@st.composite
def day_plans(draw: st.DrawFn) -> DayPlan:
    n = draw(st.integers(0, 3))
    trades = tuple(draw(trade_plans(last=k == n - 1)) for k in range(n))
    aimed = st.builds(Aim, st.sampled_from(AIM_TARGETS), st.sampled_from(OFFSETS))
    aim = draw(st.one_of(st.none(), aimed)) if trades else None
    return DayPlan(trades, aim)


@st.composite
def cases(draw: st.DrawFn) -> Case:
    cfg = draw(configs())
    fee_per_contract = {i: draw(cents(0, 300)) for i in INSTRUMENTS}
    days = tuple(draw(day_plans()) for _ in range(draw(st.integers(1, MAX_DAYS))))
    return Case(cfg, fee_per_contract, days)


# With defaults: 51,200 at the first day end sets 49,200; 52,000 sets 50,000 (Req 15.7).
WORKED: Final = Case(
    cfg=AccountConfig(),
    fee_per_contract=dict.fromkeys(INSTRUMENTS, ZERO),
    days=(
        DayPlan((TradePlan("MES", "buy", 4, 240, ZERO, ZERO, 0, False),), None),  # +1,200
        DayPlan((TradePlan("MES", "buy", 4, 160, ZERO, ZERO, 400, False),), None),  # +800
    ),
)


# ---------------------------------------------------------------- bars and events


def at(session: date, minute: int) -> Instant:
    return ny_instant(session, time(10, minute))


def make_bar(instrument: str, t: Instant, o: Ticks, h: Ticks, low: Ticks, c: Ticks) -> Bar:
    return Bar(
        instrument=instrument,
        contract=f"{instrument}M6",
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


def flat_bar(instrument: str, t: Instant, price: Ticks) -> Bar:
    return make_bar(instrument, t, price, price, price, price)


def describe(events: Sequence[AccountEvent]) -> list[str]:
    """Each event as a word: the liquidation rule, the attempt outcome, or its kind."""
    out: list[str] = []
    for e in events:
        if isinstance(e, Liquidation):
            out.append(e.rule)
        elif isinstance(e, AttemptEnded):
            out.append(e.result.outcome)
        elif isinstance(e, FlatDeadlineClose):
            out.append("flat_deadline")
        else:
            out.append("started")
    return out


def floor_label(before: Decimal, after: Decimal, start: Decimal) -> str:
    if after == before:
        return "floor held"
    if after == start:
        return "floor reached the starting balance"
    return "floor trailed"


# ---------------------------------------------------------------- run


@dataclass(frozen=True, slots=True)
class Held:
    """A round trip left open for the Flat_Deadline close."""

    instrument: str
    exit_price: Ticks
    net: Decimal  # its P&L at the close, the close fee included


class Run:
    """Feeds one case to the Account_Simulator and the reference model side by side."""

    def __init__(self, case: Case) -> None:
        cfg = case.cfg
        self.cfg = cfg
        self.fee_per_contract = case.fee_per_contract
        self.start = cfg.starting_balance.value
        self.mll = cfg.maximum_loss_limit.value
        self.dll = cfg.daily_loss_limit.value if cfg.daily_loss_limit.enabled else None
        self.cal = SessionCalendar(CAL_FIRST, CAL_LAST, times=cfg.session_times(SessionTimes()))
        self.sim = AccountSim(cfg, self.cal, case.fee_per_contract)
        self.attempts = 0
        self.active = False
        self.balance = self.start
        self.floor = self.start - self.mll
        self.day_pnl = ZERO
        self.dll_hit = False
        self.final_floors: list[Decimal] = []  # per ended attempt, in order

    # ------------------------------------------------------------ model

    def apply(self, net: Decimal) -> None:
        self.balance += net
        self.day_pnl += net

    def bar_events(self) -> list[str]:
        """What one bar's checks must report; worst-price equity is the balance."""
        if self.balance <= self.floor:
            return ["maximum_loss_limit", "failed"]
        if self.dll is not None and not self.dll_hit and self.day_pnl <= -self.dll:
            return ["daily_loss_limit"]
        return []

    def resolve(self, tp: TradePlan, aim: Aim | None) -> tuple[int, Decimal]:
        """The move and entry fee; an aimed round trip lands the day-end balance on its aim."""
        if aim is None:
            return tp.move, tp.entry_fees
        halfway = ((self.start - self.floor) / 2).quantize(CENT)
        levels = {
            "trail": self.floor + self.mll,
            "midway": self.floor + halfway + self.mll,
            "cap": self.start + self.mll,
            "floor": self.floor,
        }
        need = levels[aim.target] + aim.offset - self.balance  # the round trip's net
        cost = self.fee_per_contract[tp.instrument] * tp.qty if tp.at_deadline else tp.exit_fees
        unit = TICK_USD[tp.instrument] * tp.qty
        n, u = int((need + cost) * 100), int(unit * 100)  # whole cents
        move = -(-n // u)  # the fewest ticks whose gross covers the net plus the exit cost
        entry_fees = move * unit - cost - need
        assert ZERO <= entry_fees < unit
        return move, entry_fees

    # ------------------------------------------------------------ checks

    def check_floor(self, where: str) -> None:
        got = self.sim.mll_floor
        assert got == self.floor, f"{where}: MLL_Floor {got}, expected {self.floor}"
        assert self.floor <= self.start, where

    def end_attempt(self, events: Sequence[AccountEvent], where: str) -> None:
        [result] = [e.result for e in events if isinstance(e, AttemptEnded)]
        assert result.number == self.attempts, where
        assert result.mll_floor == self.floor, f"{where}: result floor {result.mll_floor}"
        self.final_floors.append(self.floor)
        self.active = False

    def step(self, bar: Bar, fill: tuple[Fill, Order] | None, where: str) -> bool:
        """Feed a fill (already in the model) and its bar; return whether trading goes on."""
        if fill is not None:
            self.sim.on_fill(*fill)
        events = self.sim.on_bar({bar.instrument: bar})
        want = self.bar_events()
        assert describe(events) == want, f"{where}: {describe(events)}, expected {want}"
        self.check_floor(where)  # no change within the trading day
        if want[:1] == ["maximum_loss_limit"]:
            self.end_attempt(events, where)
            event("MLL breach on a bar")
            return False
        if want:
            self.dll_hit = True
            event("DLL liquidation")
            return False
        return True

    # ------------------------------------------------------------ driving

    def day(self, s: date, plan: DayPlan) -> None:
        events = self.sim.start_trading_day(s)
        if self.active:
            assert events == [], f"{s} start"
        else:  # Req 15.6: a new attempt
            self.attempts += 1
            self.active = True
            self.balance = self.start
            self.floor = self.start - self.mll
            assert events == [AttemptStarted(self.attempts, s, self.start, self.floor)], f"{s}"
        self.day_pnl = ZERO
        self.dll_hit = False
        self.check_floor(f"{s} start")
        held: Held | None = None
        for k, tp in enumerate(plan.trades):
            aim = plan.aim if k == len(plan.trades) - 1 else None
            going, held = self.trade(s, k, tp, aim)
            if not going:
                break
        self.end_day(s, held)

    def trade(self, s: date, k: int, tp: TradePlan, aim: Aim | None) -> tuple[bool, Held | None]:
        move, entry_fees = self.resolve(tp, aim)
        sign = 1 if tp.side == "buy" else -1
        entry = max(BASE_PRICE[tp.instrument], MIN_PRICE - sign * move)
        exit_price = entry + sign * move
        gross = move * TICK_USD[tp.instrument] * tp.qty
        where = f"{s} trade {k}"

        t0 = at(s, 5 * k)
        entry_order = Order(
            f"{s}-{k}-in", None, tp.instrument, tp.side, "market", tp.qty, None, t0, "entry"
        )
        assert self.sim.check_order(entry_order) is entry_order, where
        self.apply(-entry_fees)
        entry_fill = Fill(entry_order.client_id, t0, entry, tp.qty, entry_fees)
        if not self.step(flat_bar(tp.instrument, t0, entry), (entry_fill, entry_order), where):
            return False, None

        if tp.swing:  # a favourable excursion only: worst price stays at the entry
            high, low = (entry + tp.swing, entry) if sign > 0 else (entry, entry - tp.swing)
            mark = make_bar(tp.instrument, t0 + NS_PER_MINUTE, entry, high, low, entry)
            if not self.step(mark, None, f"{where} mark"):
                return False, None

        if tp.at_deadline:
            close_fee = self.fee_per_contract[tp.instrument] * tp.qty
            return True, Held(tp.instrument, exit_price, gross - close_fee)

        t2 = t0 + 2 * NS_PER_MINUTE
        exit_side: Side = "sell" if sign > 0 else "buy"
        exit_order = Order(
            f"{s}-{k}-out", None, tp.instrument, exit_side, "market", tp.qty, None, t2, "exit"
        )
        self.apply(gross - tp.exit_fees)
        exit_fill = Fill(exit_order.client_id, t2, exit_price, tp.qty, tp.exit_fees)
        exit_bar = flat_bar(tp.instrument, t2, exit_price)
        return self.step(exit_bar, (exit_fill, exit_order), f"{where} exit"), None

    def end_day(self, s: date, held: Held | None) -> None:
        where = f"{s} day end"
        deadline = self.cal.flat_deadline(s)
        closing: dict[str, Bar] = {}
        if held is not None:
            closing[held.instrument] = flat_bar(held.instrument, deadline, held.exit_price)
        events = self.sim.end_trading_day(closing)
        if not self.active:  # the attempt failed on a bar today
            assert events == [], where
            self.check_floor(where)
            return
        if held is not None:
            self.apply(held.net)
        assert self.sim.balance == self.balance, f"{where}: balance {self.sim.balance}"
        kinds = describe(events)
        if self.balance <= self.floor:  # the flat close breached the floor: no update
            assert kinds == ["flat_deadline", "failed"], f"{where}: {kinds}"
            self.check_floor(where)
            self.end_attempt(events, where)
            event("MLL breach at the flat close")
            return
        before = self.floor
        self.floor = reference_floor(before, self.balance, self.mll, self.start)
        self.check_floor(where)
        assert before <= self.floor <= self.start, where
        event(floor_label(before, self.floor, self.start))
        if kinds == ["flat_deadline", "passed"]:
            target = self.cfg.profit_target
            assert target.enabled, where
            assert self.balance >= self.start + target.value, where
            self.end_attempt(events, where)
            event("pass")
        else:
            assert kinds == ["flat_deadline"], f"{where}: {kinds}"

    def finish(self) -> None:
        results = self.sim.finish()
        if self.active:  # recorded as incomplete
            self.final_floors.append(self.floor)
        assert [r.number for r in results] == list(range(1, self.attempts + 1))
        assert [r.mll_floor for r in results] == self.final_floors


# ---------------------------------------------------------------- tests


def test_reference_floor_matches_the_worked_examples() -> None:
    start, mll = Decimal("50000.00"), Decimal("2000.00")
    first = start - mll
    assert first == Decimal("48000.00")
    second = reference_floor(first, Decimal("51200.00"), mll, start)
    assert second == Decimal("49200.00")
    assert reference_floor(second, Decimal("52000.00"), mll, start) == Decimal("50000.00")
    assert reference_floor(start, Decimal("60000.00"), mll, start) == start


# Feature: skylit-futures-strategy-engine, Property 45: Trailing MLL_Floor
@given(case=cases())
@example(case=WORKED)
def test_trailing_mll_floor(case: Case) -> None:
    run = Run(case)
    assert run.sim.mll_floor is None  # no attempt before the first trading day
    sessions = run.cal.sessions(FIRST_SESSION)
    for s, plan in zip(sessions, case.days, strict=False):
        run.day(s, plan)
    run.finish()
