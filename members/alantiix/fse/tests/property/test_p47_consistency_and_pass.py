"""Property 47: Consistency target and pass timing.

*For any* sequence of trading-day results, the current profit target equals
max(configured target, best day / consistency share) rounded up to the next
$0.01, and a Combine_Pass is recorded only at a trading-day end whose balance
is at or above starting balance + current target, before any MLL breach, after
which every order in that attempt is rejected.

**Inputs.** A random ``AccountConfig``: a starting balance of $1,000.00 to
$300,000.00 (often $50,000.00), a profit target from one cent to $20,000.00
(often $3,000.00), a consistency target in thousandths of a percent from
0.001% to 100% (often 55%), and the profit target, consistency target, MLL and
DLL rules each sometimes disabled. Random fees. One to twelve consecutive
sessions, each with up to three sequential round trips (any contract, long or
short); the last may stay open for the Flat_Deadline close, and a mark bar may
carry up to 4,000 ticks of favourable excursion, so intraday equity runs past
the pass threshold without a pass.

The day's last round trip may be aimed, offset by -1, 0 or +1 cent: the day's
net at the consistency boundary, configured target x share (``best``); the
end-of-day balance at starting balance + current target (``pass``); or the
day's net at the largest value that still passes once it raises the target,
prior gain x share / (1 - share) (``raised``; ``best`` while that value would
not raise the target). A quarter of the cases are steady: both target rules
on, default loss limits and every day aimed at ``raised``, so attempts climb to
a gain and then pass, or miss by a cent, on a raised target.

**Reference model.** Exact ``Decimal`` dollars; quotients go through exact
integer ratios. The target after a trading day is max(configured, best day x
100 / pct) with the quotient rounded up to a whole cent, where the best day is
the largest net of a completed trading day in the attempt and pct is the
Operator's decimal (Req 15.13); with the consistency rule disabled it stays the
configured target. The Flat_Deadline closes an open position at the open of
the first bar at or after it, with the per-contract fee (Req 15.16). No bar
goes against a position, so an MLL breach is expected exactly when the balance
is at or below the floor after a fill or the flat close, and a DLL block when
the day's net is at or below minus the DLL (Property 46 covers both); the floor
trails per Req 15.7. A day the attempt fails on is not a completed day. A pass
is expected exactly at a completed day end with the profit target rule enabled
and balance >= start + the target after that day's update (Req 15.17).

**Checks.** The target and balance equal the model's after every day start,
fill, bar and day end, so the target changes only at day ends; the flat close
books the expected exits; a pass is reported exactly when the model expects
one, and only by ``end_trading_day``; each attempt result records the outcome,
pass date, trading days, end instant, balance, target and best day; after a
pass or a fail every order is rejected, and after any other day end every
entry order is rejected, until the next trading day starts a new attempt.

**Validates: Requirements 15.13, 15.16, 15.17**
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
from fse.engine.types import Bar, Fill, Order, OrderRole, Side, Ticks
from fse.sim.account import (
    AccountEvent,
    AccountRejection,
    AccountSim,
    AttemptEnded,
    AttemptOutcome,
    AttemptResult,
    AttemptStarted,
    FlatDeadlineClose,
    ForcedExit,
    Liquidation,
    RejectionRule,
)
from fse.timekit import NS_PER_MINUTE, Instant, SessionCalendar, SessionTimes, ny_instant

ZERO: Final = Decimal("0.00")
CENT: Final = Decimal("0.01")
HUNDRED: Final = Decimal(100)

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
ALL_ROLES: Final[tuple[OrderRole, ...]] = ("entry", "stop", "tp1", "exit")

CAL_FIRST: Final = date(2026, 3, 1)
CAL_LAST: Final = date(2026, 4, 30)
FIRST_SESSION: Final = date(2026, 3, 2)  # a Monday
MAX_DAYS: Final = 12

type AimTarget = Literal["best", "pass", "raised"]
AIM_TARGETS: Final[tuple[AimTarget, ...]] = ("best", "pass", "raised")
OFFSETS: Final[tuple[Decimal, ...]] = (-CENT, ZERO, CENT)


# ---------------------------------------------------------------- reference arithmetic


def _cents_ratio(num: Decimal, den: Decimal) -> tuple[int, int]:
    """``num / den`` in cents as an exact integer ratio ``n / m`` with ``m > 0`` (``den > 0``)."""
    a, b = num.as_integer_ratio()
    c, d = den.as_integer_ratio()
    return 100 * a * d, b * c


def ceil_cents(num: Decimal, den: Decimal) -> Decimal:
    n, m = _cents_ratio(num, den)
    return Decimal(-(-n // m)).scaleb(-2)


def floor_cents(num: Decimal, den: Decimal) -> Decimal:
    n, m = _cents_ratio(num, den)
    return Decimal(n // m).scaleb(-2)


def reference_target(configured: Decimal, best: Decimal | None, pct: Decimal | None) -> Decimal:
    """Req 15.13: max(configured, best / (pct / 100)), the quotient rounded up to a cent.

    ``pct`` is ``None`` when the consistency rule is disabled.
    """
    if pct is None or best is None:
        return configured
    return max(configured, ceil_cents(best * HUNDRED, pct))


def reference_floor(floor: Decimal, eod: Decimal, mll: Decimal, start: Decimal) -> Decimal:
    """Req 15.7: the lesser of the start and the greater of the floor and ``eod - MLL``."""
    return min(start, max(floor, eod - mll))


# ---------------------------------------------------------------- plans


@dataclass(frozen=True, slots=True)
class Aim:
    """Land the day on ``target`` plus ``offset`` (see the module docstring)."""

    target: AimTarget
    offset: Decimal


@dataclass(frozen=True, slots=True)
class TradePlan:
    """One round trip: a market entry, an optional mark bar, then an exit.

    ``move`` is the exit minus the entry in ticks, in the trade's favour. With
    ``at_deadline`` there is no exit fill: the Flat_Deadline close exits at
    ``entry + move`` with the per-contract fee.
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
    pct: Decimal  # the consistency target as the Operator wrote it; cfg holds float(pct)
    fee_per_contract: Mapping[str, Decimal]
    days: tuple[DayPlan, ...]


# ---------------------------------------------------------------- strategies


def cents(lo: int, hi: int) -> st.SearchStrategy[Decimal]:
    return st.integers(lo, hi).map(lambda c: Decimal(c) * CENT)


FEES = st.sampled_from(("0", "0.37", "0.62", "1.24", "2.80", "9.99")).map(Decimal)
PCTS = st.one_of(
    st.sampled_from(("55", "30", "50", "100", "33.333", "0.001")).map(Decimal),
    st.integers(1, 100_000).map(lambda k: Decimal(k).scaleb(-3)),
)


@st.composite
def cases(draw: st.DrawFn) -> Case:
    # A steady case aims every day at "raised", with both target rules on and the
    # default loss limits, so the climb is not cut short by a tiny MLL or DLL.
    steady = draw(st.integers(0, 3)) == 0
    start = draw(st.one_of(st.just(5_000_000), st.integers(100_000, 30_000_000)))
    top = min(start - 1, 2_000_000)
    default_mll = st.just(min(200_000, top))
    mll = draw(default_mll if steady else st.one_of(default_mll, st.integers(1, top)))
    default_dll = st.just(Decimal("1000.00"))
    dll = draw(default_dll if steady else st.one_of(default_dll, cents(1, 2_000_000)))
    on = st.just(True)
    pct = draw(PCTS)
    cfg = AccountConfig.model_validate(
        {
            "starting_balance": {"value": str(Decimal(start) * CENT)},
            "profit_target": {
                "enabled": draw(on if steady else st.sampled_from((True, True, True, False))),
                "value": str(draw(st.one_of(st.just(Decimal("3000.00")), cents(1, 2_000_000)))),
            },
            "consistency_target": {
                "enabled": draw(on if steady else st.sampled_from((True, True, False))),
                "pct": float(pct),
            },
            "maximum_loss_limit": {
                "enabled": draw(st.sampled_from((True, True, True, False))),
                "value": str(Decimal(mll) * CENT),
            },
            "daily_loss_limit": {
                "enabled": draw(st.sampled_from((True, True, False))),
                "value": str(dll),
            },
        }
    )
    fee_per_contract = {i: draw(cents(0, 300)) for i in INSTRUMENTS}
    plans = steady_day_plans() if steady else day_plans()
    days = tuple(draw(plans) for _ in range(draw(st.integers(1, MAX_DAYS))))
    return Case(cfg, pct, fee_per_contract, days)


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
        swing=draw(st.sampled_from((0, 0, 8, 4_000))),
        at_deadline=last and draw(st.booleans()),
    )


@st.composite
def day_plans(draw: st.DrawFn) -> DayPlan:
    n = draw(st.integers(0, 3))
    trades = tuple(draw(trade_plans(last=k == n - 1)) for k in range(n))
    aimed = st.builds(Aim, st.sampled_from(AIM_TARGETS), st.sampled_from(OFFSETS))
    aim = draw(st.one_of(st.none(), aimed, aimed)) if trades else None
    return DayPlan(trades, aim)


@st.composite
def steady_day_plans(draw: st.DrawFn) -> DayPlan:
    """One ``raised``-aimed round trip: climb to the gain, then pass on a raised target."""
    return DayPlan((draw(trade_plans(last=True)),), Aim("raised", draw(st.sampled_from(OFFSETS))))


# ---------------------------------------------------------------- worked examples

NO_FEES: Final[Mapping[str, Decimal]] = dict.fromkeys(INSTRUMENTS, ZERO)


def plain(instrument: str, side: Side, qty: int, move: int, *, held: bool = False) -> TradePlan:
    return TradePlan(instrument, side, qty, move, ZERO, ZERO, 0, held)


# With defaults a best day of $1,650.00 leaves the target at $3,000.00 (Req 15.13), so
# +1,650.00 then +1,350.00 is a pass on day 2; day 3 starts attempt 2.
WORKED_KEEP: Final = Case(
    cfg=AccountConfig(),
    pct=Decimal(55),
    fee_per_contract=NO_FEES,
    days=(
        DayPlan((plain("MES", "buy", 4, 330),), None),
        DayPlan((plain("MES", "sell", 4, 270),), None),
        DayPlan((plain("MES", "buy", 1, 4),), None),
    ),
)
# A best day of $2,000.00 raises it to $3,636.37: 53,636.36 is no pass, 53,636.37 is.
WORKED_RAISE: Final = Case(
    cfg=AccountConfig(),
    pct=Decimal(55),
    fee_per_contract=NO_FEES,
    days=(
        DayPlan((plain("MES", "buy", 4, 400),), None),
        DayPlan((plain("MES", "buy", 4, 0),), Aim("pass", -CENT)),
        DayPlan((plain("MES", "sell", 4, 0, held=True),), Aim("pass", ZERO)),
    ),
)
# +3,000.00 in one day reaches start + configured target, but raises the target first.
WORKED_BLOCKED: Final = Case(
    cfg=AccountConfig(),
    pct=Decimal(55),
    fee_per_contract=NO_FEES,
    days=(DayPlan((plain("MES", "buy", 4, 600),), None),),
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


# ---------------------------------------------------------------- run


@dataclass(frozen=True, slots=True)
class Held:
    """A round trip left open for the Flat_Deadline close."""

    instrument: str
    side: Side  # the entry side
    qty: int
    exit_price: Ticks
    net: Decimal  # its P&L at the close, the close fee included


class Run:
    """Feeds one case to the Account_Simulator and the reference model side by side."""

    def __init__(self, case: Case) -> None:
        cfg = case.cfg
        self.case = case
        self.fees = case.fee_per_contract
        self.start = cfg.starting_balance.value
        self.configured = cfg.profit_target.value
        self.pass_enabled = cfg.profit_target.enabled
        self.pct = case.pct if cfg.consistency_target.enabled else None
        self.mll = cfg.maximum_loss_limit.value if cfg.maximum_loss_limit.enabled else None
        self.dll = cfg.daily_loss_limit.value if cfg.daily_loss_limit.enabled else None
        self.cal = SessionCalendar(CAL_FIRST, CAL_LAST, times=cfg.session_times(SessionTimes()))
        self.sim = AccountSim(cfg, self.cal, case.fee_per_contract)
        self.attempts = 0
        self.active = False
        self.session = FIRST_SESSION  # the trading day in progress
        self.first_session = FIRST_SESSION
        self.days = 0  # trading days in the current attempt
        self.balance = self.start
        self.day_start = self.start
        self.floor: Decimal | None = None
        self.day_pnl = ZERO
        self.dll_hit = False
        self.best: Decimal | None = None
        self.target = self.configured
        self.ended: list[tuple[AttemptOutcome, Decimal, Decimal | None]] = []

    # ------------------------------------------------------------ model

    def apply(self, net: Decimal) -> None:
        self.balance += net
        self.day_pnl += net

    def bar_events(self) -> list[str]:
        """What one bar's checks must report; worst-price equity is the balance."""
        if self.floor is not None and self.balance <= self.floor:
            return ["maximum_loss_limit", "failed"]
        if self.dll is not None and not self.dll_hit and self.day_pnl <= -self.dll:
            return ["daily_loss_limit"]
        return []

    def aim_net(self, aim: Aim) -> Decimal:
        """The net the aimed round trip needs to land the day on its aim."""
        pct = self.case.pct
        if aim.target == "pass":
            return self.start + self.target + aim.offset - self.balance
        gain = self.day_start - self.start
        if aim.target == "raised" and pct < HUNDRED and gain > 0:
            # gain + day >= ceil(day / share) exactly when day <= gain x share / (1 - share)
            day = floor_cents(gain * pct, HUNDRED - pct)
            if day * HUNDRED > self.configured * pct:  # that day raises the target
                return day + aim.offset - self.day_pnl
        # "best", or "raised" while the gain is too small: the day at configured x share
        return floor_cents(self.configured * pct, HUNDRED) + aim.offset - self.day_pnl

    def resolve(self, tp: TradePlan, aim: Aim | None) -> tuple[int, Decimal]:
        """The move and entry fee; an aimed round trip nets exactly what its aim needs."""
        if aim is None:
            return tp.move, tp.entry_fees
        need = self.aim_net(aim)
        cost = self.fees[tp.instrument] * tp.qty if tp.at_deadline else tp.exit_fees
        unit = TICK_USD[tp.instrument] * tp.qty
        n, u = int((need + cost) * 100), int(unit * 100)  # whole cents
        move = -(-n // u)  # the fewest ticks whose gross covers the net plus the exit cost
        entry_fees = move * unit - cost - need
        assert ZERO <= entry_fees < unit
        return move, entry_fees

    # ------------------------------------------------------------ checks

    def check_state(self, where: str) -> None:
        got = self.sim.profit_target
        assert got == self.target, f"{where}: profit target {got}, expected {self.target}"
        assert self.sim.balance == self.balance, f"{where}: balance {self.sim.balance}"
        assert self.sim.mll_floor == self.floor, f"{where}: MLL_Floor {self.sim.mll_floor}"

    def check_blocked(
        self, s: date, t: Instant, rule: RejectionRule, roles: tuple[OrderRole, ...]
    ) -> None:
        """Every order with one of ``roles`` is refused for ``rule``."""
        assert self.sim.blocked_by == rule, f"{s}: blocked by {self.sim.blocked_by}, not {rule}"
        for k, role in enumerate(roles):
            instrument = INSTRUMENTS[k % len(INSTRUMENTS)]
            kind: Literal["market", "stop"] = "stop" if role == "stop" else "market"
            price = BASE_PRICE[instrument] if kind == "stop" else None
            order = Order(f"{s}-after-{k}", None, instrument, SIDES[k % 2], kind, 1, price, t, role)
            got = self.sim.check_order(order)
            assert isinstance(got, AccountRejection), f"{s}: {role} order accepted after {rule}"
            assert (got.rule, got.client_id) == (rule, order.client_id), f"{s}: {got}"

    def end_attempt(
        self,
        events: Sequence[AccountEvent],
        outcome: AttemptOutcome,
        s: date,
        ended_ns: Instant,
        where: str,
    ) -> AttemptResult:
        [result] = [e.result for e in events if isinstance(e, AttemptEnded)]
        expected = AttemptResult(
            number=self.attempts,
            outcome=outcome,
            first_session=self.first_session,
            last_session=s,
            trading_days=self.days,
            ended_ns=ended_ns,
            final_balance=self.balance,
            mll_floor=self.floor,
            profit_target=self.target,
            best_day=self.best,
            disabled_rules=self.case.cfg.disabled_rules(),
        )
        assert result == expected, f"{where}: {result}, expected {expected}"
        self.ended.append((outcome, self.target, self.best))
        self.active = False
        return result

    def step(self, bar: Bar, fill: tuple[Fill, Order] | None, where: str) -> bool:
        """Feed a fill (already in the model) and its bar; return whether trading goes on."""
        if fill is not None:
            self.sim.on_fill(*fill)
        events = self.sim.on_bar({bar.instrument: bar})
        want = self.bar_events()
        assert describe(events) == want, f"{where}: {describe(events)}, expected {want}"
        self.check_state(where)  # no target change within the trading day
        if want[:1] == ["maximum_loss_limit"]:
            self.end_attempt(events, "failed", self.session, bar.open_ns, where)
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
        self.session = s
        if self.active:
            assert events == [], f"{s} start"
        else:  # the first day, or the day after a pass or a fail
            self.attempts += 1
            self.active = True
            self.first_session = s
            self.days = 0
            self.balance = self.start
            self.floor = None if self.mll is None else self.start - self.mll
            self.best = None
            self.target = self.configured
            assert events == [AttemptStarted(self.attempts, s, self.start, self.floor)], f"{s}"
        self.days += 1
        self.day_start = self.balance
        self.day_pnl = ZERO
        self.dll_hit = False
        assert self.sim.blocked_by is None, f"{s} start: blocked by {self.sim.blocked_by}"
        self.check_state(f"{s} start")
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
            close_fee = self.fees[tp.instrument] * tp.qty
            return True, Held(tp.instrument, tp.side, tp.qty, exit_price, gross - close_fee)

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
        if held is not None:  # not flat: the close must use the open (Req 15.16)
            p = held.exit_price
            closing[held.instrument] = make_bar(held.instrument, deadline, p, p + 5, p - 7, p + 3)
        events = self.sim.end_trading_day(closing)
        if not self.active:  # the attempt failed on a bar today: no day-end rules
            assert events == [], where
            self.check_state(where)
            self.check_blocked(s, deadline, "maximum_loss_limit", ALL_ROLES)
            return

        exits: tuple[ForcedExit, ...] = ()
        if held is not None:
            self.apply(held.net)
            side: Side = "sell" if held.side == "buy" else "buy"
            fees = self.fees[held.instrument] * held.qty
            exits = (ForcedExit(held.instrument, side, held.qty, held.exit_price, fees),)
        assert events[:1] == [FlatDeadlineClose(s, deadline, exits)], f"{where}: {events[:1]}"
        assert len(self.sim.positions) == 0, f"{where}: {dict(self.sim.positions)}"
        assert self.sim.balance == self.balance, f"{where}: balance {self.sim.balance}"
        kinds = describe(events)

        if self.floor is not None and self.balance <= self.floor:
            # The flat close breached the MLL_Floor: the day is not completed.
            assert kinds == ["flat_deadline", "failed"], f"{where}: {kinds}"
            self.check_state(where)
            self.end_attempt(events, "failed", s, deadline, where)
            self.check_blocked(s, deadline, "maximum_loss_limit", ALL_ROLES)
            event("MLL breach at the flat close")
            return

        if self.mll is not None and self.floor is not None:
            self.floor = reference_floor(self.floor, self.balance, self.mll, self.start)
        self.best = self.day_pnl if self.best is None else max(self.best, self.day_pnl)
        before = self.target
        self.target = reference_target(self.configured, self.best, self.pct)
        self.check_state(where)
        assert self.target >= self.configured, where
        threshold = self.start + self.target
        label(before, self.target, self.balance, threshold, self.start + self.configured)

        if self.pass_enabled and self.balance >= threshold:
            assert kinds == ["flat_deadline", "passed"], f"{where}: {kinds}, expected a pass"
            self.end_attempt(events, "passed", s, deadline, where)
            assert not self.sim.active, where
            self.check_blocked(s, deadline, "profit_target", ALL_ROLES)
            event("pass with a raised target" if self.target > self.configured else "pass")
        else:
            assert kinds == ["flat_deadline"], f"{where}: {kinds}, expected no pass"
            assert self.sim.active, where
            self.check_blocked(s, deadline, "flat_deadline", ("entry",))

    def finish(self) -> None:
        results = self.sim.finish()
        if self.active:  # recorded as incomplete (Req 15.18)
            self.ended.append(("incomplete", self.target, self.best))
        assert [r.number for r in results] == list(range(1, self.attempts + 1))
        assert [(r.outcome, r.profit_target, r.best_day) for r in results] == self.ended


def label(
    before: Decimal, after: Decimal, eod: Decimal, threshold: Decimal, unraised: Decimal
) -> None:
    """Record how a completed day end met the consistency and pass boundaries."""
    if after != before:
        event("target raised")
    if eod == threshold:
        event("balance exactly at the threshold")
    elif eod == threshold - CENT:
        event("balance one cent short of the threshold")
    if unraised <= eod < threshold:
        event("raised target held off a pass")


# ---------------------------------------------------------------- tests


def test_reference_target_matches_the_worked_examples() -> None:
    target, pct = Decimal("3000.00"), Decimal(55)
    assert reference_target(target, Decimal("1650.00"), pct) == target
    assert reference_target(target, Decimal("1649.99"), pct) == target
    assert reference_target(target, Decimal("1650.01"), pct) == Decimal("3000.02")
    assert reference_target(target, Decimal("2000.00"), pct) == Decimal("3636.37")
    assert reference_target(target, Decimal("-250.00"), pct) == target
    assert reference_target(target, None, pct) == target
    assert reference_target(target, Decimal("2000.00"), None) == target


# Feature: skylit-futures-strategy-engine, Property 47: Consistency target and pass timing
@given(case=cases())
@example(case=WORKED_KEEP)
@example(case=WORKED_RAISE)
@example(case=WORKED_BLOCKED)
def test_consistency_target_and_pass_timing(case: Case) -> None:
    run = Run(case)
    assert run.sim.profit_target == run.configured  # no attempt before the first trading day
    sessions = run.cal.sessions(FIRST_SESSION)
    for s, plan in zip(sessions, case.days, strict=False):
        run.day(s, plan)
    run.finish()
