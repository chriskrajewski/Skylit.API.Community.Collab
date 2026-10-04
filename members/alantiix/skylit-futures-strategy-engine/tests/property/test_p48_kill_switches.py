"""Property 48: Kill switches match the reference model.

*For any* sequence of fills, trade closes, minute marks and session dates
(holidays included), the Lockouts started (rule, start time, last session
covered), the Loss_Streak and the internal-loss-stop closes equal a reference
implementation of Requirement 16 criteria 1-6; while a Lockout is active no
entry is placed, resting entries are cancelled, exits keep working, and
``kill_switch_lockout`` fails with each active rule and last session.

**Inputs.** A random ``KillSwitchesConfig`` (each rule on three times in four,
small limits, threshold, cap and tolerance in cents, a loss-stop multiple), a
``sizing.risk_usd`` and a five-week calendar. One to four sessions are drawn
from its weekdays, mostly back to back, and up to four holidays fall on unused
weekdays between and just after them, often on the weekday right after a
session, so a consecutive_losers Lockout often skips a holiday as well as a
weekend. Each session gets up to four trades (MES or MNQ, long or short, one or
two fills of one entry order, one to three exit fills, fees on every fill) and
up to four 1-minute marks, many on bars with an open position. Times are
minutes after the trading-day start ``(d - 1) 18:00`` up to the 16:10
Flat_Deadline, so evening fills belong to the next session.

Some exit fills aim the session net exactly at the red_day threshold or the
profit cap, or the trade net exactly at the losing line, and some marks aim at
the loss-stop limit, each offset by -1, 0 or +1 cent, so every boundary of
Req 16.2-16.6 is hit.

**Reference model.** ``Reference`` applies Req 16.1-16.6 and the Glossary
(Losing_Trade, Loss_Streak) as written, one event at a time, and keeps every
Lockout it starts. A Lockout is active in session ``s`` when it covers ``s`` or
a later session (every kept Lockout started at or before the current event).

**Checks.** After every event, the Risk_Manager's held and active Lockouts
equal the model's active ones, so each started Lockout is compared the moment
it starts; its Loss_Streak equals the model's; a mark's market closes equal the
model's. On every weekday session from the first one to a week past the last,
with or without events, ``entries_blocked`` and ``entries_to_cancel`` withhold
and cancel entries exactly while a Lockout is active and never touch stop,
target or exit orders (Req 16.8). The Gate_Evaluator's ``kill_switch_lockout``
is not built yet, so Req 16.9 is checked on what it reports from:
``active_lockouts`` names each active rule and its last session.

Several Lockouts can start on one fill and Req 16 gives them no order, so
Lockouts are compared sorted.

**Validates: Requirements 16.1, 16.2, 16.3, 16.4, 16.5, 16.6, 16.8, 16.9**
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Final, Literal

from hypothesis import event, given
from hypothesis import strategies as st

from fse.config.schema.kill_switches import KillSwitchesConfig
from fse.engine.risk import (
    CloseIntent,
    Lockout,
    LockoutRule,
    RiskFill,
    RiskState,
    active_lockouts,
    entries_blocked,
    entries_to_cancel,
    on_fill,
    on_minute_close,
    roll_session,
)
from fse.engine.types import Direction, Fill, Order, OrderRole, SetupKey, Trade, direction_sign
from fse.timekit import NS_PER_MINUTE, Instant, SessionCalendar

ONE_DAY: Final = timedelta(days=1)
ZERO: Final = Decimal(0)
CENT: Final = Decimal("0.01")
R_USD: Final = Decimal(100)
PRICE: Final = 20_000

FIRST_MONDAY: Final = date(2024, 1, 1)
CALENDAR_DAYS: Final = 35  # five weeks from a Monday
LAST_MINUTE: Final = 1329  # the bar opening at 16:09 closes at the 16:10 Flat_Deadline
INSTRUMENTS: Final[tuple[str, ...]] = ("MES", "MNQ")
DIRECTIONS: Final[tuple[Direction, ...]] = ("long", "short")
EXIT_ROLES: Final[tuple[OrderRole, ...]] = ("stop", "tp1", "tp2", "exit")
MULTIPLES: Final[tuple[float, ...]] = (0.1, 0.5, 1.0, 1.5, 2.0, 2.5)
OFFSETS: Final[tuple[Decimal, ...]] = (-CENT, ZERO, CENT)

# Resting orders a Lockout must sort: the entries go, the exits stay (Req 16.8).
WORKING: Final[tuple[Order, ...]] = (
    Order("w-entry-1", None, "MES", "buy", "limit", 1, PRICE - 10, 0, "entry"),
    Order("w-stop", None, "MES", "sell", "stop", 1, PRICE - 20, 0, "stop"),
    Order("w-tp1", None, "MES", "sell", "limit", 1, PRICE + 10, 0, "tp1"),
    Order("w-tp2", None, "MNQ", "buy", "limit", 1, PRICE - 30, 0, "tp2"),
    Order("w-exit", None, "MNQ", "buy", "market", 1, None, 0, "exit"),
    Order("w-entry-2", None, "MNQ", "sell", "stop", 2, PRICE - 40, 0, "entry"),
)
WORKING_ENTRIES: Final = tuple(order for order in WORKING if order.role == "entry")

type AimTarget = Literal["red_day", "daily_profit_cap", "losing_line"]


def lockout_key(lo: Lockout) -> tuple[Instant, str, date]:
    return lo.started_at, lo.rule, lo.last_session


# ---------------------------------------------------------------- plans


@dataclass(frozen=True, slots=True)
class Aim:
    """Choose an exit's gross so a running net lands on ``target`` plus ``offset``.

    ``red_day`` and ``daily_profit_cap`` aim the session net at the threshold or
    the cap; ``losing_line`` (last exits only) aims the trade net at minus the
    losing-trade tolerance.
    """

    target: AimTarget
    offset: Decimal


@dataclass(frozen=True, slots=True)
class EntryPlan:
    minute: int  # the fill bar's open, in minutes after the trading-day start
    qty: int
    fees: Decimal


@dataclass(frozen=True, slots=True)
class ExitPlan:
    minute: int
    qty: int
    role: OrderRole
    fees: Decimal
    gross: Decimal  # used when ``aim`` is None
    aim: Aim | None


@dataclass(frozen=True, slots=True)
class TradePlan:
    """One trade: fills of one entry order, then exit fills that close it."""

    instrument: str
    direction: Direction
    entries: tuple[EntryPlan, ...]
    exits: tuple[ExitPlan, ...]

    def minutes(self) -> tuple[int, ...]:
        """The fill minutes, entries first."""
        return (*(ep.minute for ep in self.entries), *(xp.minute for xp in self.exits))


@dataclass(frozen=True, slots=True)
class MarkPlan:
    """A 1-minute close: of the bar opening at ``minute``.

    ``unrealized`` is the worst-price mark of the open positions, unless ``aim``
    is set: then the session net plus the mark is the loss-stop limit plus
    ``aim``. With no open position the mark is 0.
    """

    minute: int
    unrealized: Decimal
    aim: Decimal | None


@dataclass(frozen=True, slots=True)
class SessionPlan:
    session: date
    trades: tuple[TradePlan, ...]
    marks: tuple[MarkPlan, ...]

    def steps(self) -> list[tuple[int, int, int, int]]:
        """``(minute, phase, index, j)`` in event order.

        Phase 0 is fill ``j`` of trade ``index`` (entries first); phase 1 is
        mark ``index``. A bar's fills come before its close, which comes before
        the next bar's fills.
        """
        out: list[tuple[int, int, int, int]] = []
        for k, plan in enumerate(self.trades):
            out.extend((minute, 0, k, j) for j, minute in enumerate(plan.minutes()))
        out.extend((mark.minute, 1, m, 0) for m, mark in enumerate(self.marks))
        return sorted(out)


@dataclass(frozen=True, slots=True)
class Case:
    cfg: KillSwitchesConfig
    risk_usd: Decimal
    first_monday: date
    holidays: tuple[date, ...]
    plans: tuple[SessionPlan, ...]  # in session order

    def calendar(self) -> SessionCalendar:
        last = self.first_monday + timedelta(days=CALENDAR_DAYS - 1)
        return SessionCalendar(self.first_monday, last, holidays=self.holidays)


# ---------------------------------------------------------------- strategies


def cents(lo: int, hi: int) -> st.SearchStrategy[Decimal]:
    return st.integers(lo, hi).map(lambda c: Decimal(c) * CENT)


FEES = st.sampled_from(("0", "0.37", "0.62", "1.24", "2.80")).map(Decimal)
# Typical stop-outs and targets keep losers and winners common; the integers fill the gaps.
GROSS = st.one_of(
    st.sampled_from(("-150", "-75", "-37.50", "-12.50", "0", "25", "62.50", "150")).map(Decimal),
    st.integers(-240, 240).map(lambda k: Decimal(k) * Decimal("1.25")),
)


@st.composite
def configs(draw: st.DrawFn) -> KillSwitchesConfig:
    def on() -> bool:  # each rule is on three times in four
        return draw(st.sampled_from((True, True, True, False)))

    return KillSwitchesConfig.model_validate(
        {
            "max_trades": {"enabled": on(), "max": draw(st.integers(1, 4))},
            "max_losers": {"enabled": on(), "limit": draw(st.integers(1, 3))},
            "consecutive_losers": {"enabled": on(), "limit": draw(st.integers(1, 4))},
            "red_day": {"enabled": on(), "threshold_usd": draw(cents(-20_000, 5_000))},
            "daily_profit_cap": {"enabled": on(), "cap_usd": draw(cents(1, 60_000))},
            "internal_daily_loss_stop": {
                "enabled": on(),
                "multiple": draw(st.sampled_from(MULTIPLES)),
            },
            "losing_trade_tolerance_usd": draw(st.one_of(st.just(ZERO), cents(0, 3_000))),
        }
    )


def aims(targets: tuple[AimTarget, ...]) -> st.SearchStrategy[Aim | None]:
    aimed = st.builds(Aim, st.sampled_from(targets), st.sampled_from(OFFSETS))
    return st.one_of(st.none(), aimed)


@st.composite
def trade_plans(draw: st.DrawFn) -> TradePlan:
    entry_qty = draw(st.lists(st.integers(1, 2), min_size=1, max_size=2))
    total = sum(entry_qty)
    n_exits = draw(st.integers(1, min(total, 3)))
    cuts: list[int] = []
    if n_exits > 1:
        parts = st.sets(st.integers(1, total - 1), min_size=n_exits - 1, max_size=n_exits - 1)
        cuts = sorted(draw(parts))
    exit_qty = [b - a for a, b in zip((0, *cuts), (*cuts, total), strict=True)]
    # Fills of one trade stay within 90 minutes of its first fill.
    start = draw(st.integers(0, LAST_MINUTE))
    n = len(entry_qty) + n_exits
    offsets = draw(st.lists(st.integers(0, 90), min_size=n, max_size=n))
    minutes = sorted(min(start + off, LAST_MINUTE) for off in offsets)
    entries = tuple(EntryPlan(m, q, draw(FEES)) for m, q in zip(minutes, entry_qty, strict=False))
    exits: list[ExitPlan] = []
    for j, q in enumerate(exit_qty):
        targets: tuple[AimTarget, ...] = ("red_day", "daily_profit_cap")
        if j == n_exits - 1:
            targets = (*targets, "losing_line")
        exits.append(
            ExitPlan(
                minute=minutes[len(entry_qty) + j],
                qty=q,
                role=draw(st.sampled_from(EXIT_ROLES)),
                fees=draw(FEES),
                gross=draw(GROSS),
                aim=draw(aims(targets)),
            )
        )
    return TradePlan(
        instrument=draw(st.sampled_from(INSTRUMENTS)),
        direction=draw(st.sampled_from(DIRECTIONS)),
        entries=entries,
        exits=tuple(exits),
    )


@st.composite
def session_plans(draw: st.DrawFn, session: date) -> SessionPlan:
    # Counts drawn as integers spread evenly over 0 to 4; list sizes would stay small.
    trades = tuple(draw(trade_plans()) for _ in range(draw(st.integers(0, 4))))
    fill_minutes = sorted({m for plan in trades for m in plan.minutes()})
    minute = st.integers(0, LAST_MINUTE)
    if fill_minutes:
        minute = st.one_of(minute, st.sampled_from(fill_minutes))
    mark = st.builds(
        MarkPlan,
        minute=minute,
        unrealized=cents(-100_000, 10_000),
        aim=st.one_of(st.none(), st.sampled_from(OFFSETS)),
    )
    marks = tuple(draw(mark) for _ in range(draw(st.integers(0, 4))))
    return SessionPlan(session, trades, marks)


@st.composite
def cases(draw: st.DrawFn) -> Case:
    monday = FIRST_MONDAY + timedelta(weeks=draw(st.integers(0, 520)))
    days = (monday + timedelta(days=i) for i in range(CALENDAR_DAYS))
    weekdays = [d for d in days if d.weekday() < 5]
    idx = [draw(st.integers(0, 9))]
    for _ in range(draw(st.integers(0, 3))):
        idx.append(idx[-1] + draw(st.sampled_from((1, 1, 2, 3))))
    # Holidays fall on unused weekdays between the sessions and just after them,
    # often on the weekday right after a session, where a consecutive_losers
    # Lockout would otherwise end.
    candidates = [weekdays[i] for i in range(idx[0], idx[-1] + 5) if i not in idx]
    right_after = [weekdays[i + 1] for i in idx if i + 1 not in idx]
    picked = draw(st.sets(st.sampled_from(right_after), max_size=2))
    picked |= draw(st.sets(st.sampled_from(candidates), max_size=2))
    holidays = tuple(sorted(picked))
    return Case(
        cfg=draw(configs()),
        risk_usd=Decimal(draw(st.integers(25, 400))),
        first_monday=monday,
        holidays=holidays,
        plans=tuple(draw(session_plans(weekdays[i])) for i in idx),
    )


# ---------------------------------------------------------------- reference model


@dataclass
class Reference:
    """Requirement 16.1-16.6 as written, fed one event at a time."""

    cfg: KillSwitchesConfig
    holidays: frozenset[date]
    stop_limit: Decimal  # minus multiple x risk_usd (Req 16.6)
    session: date | None = None
    entries: set[str] = field(default_factory=set)  # entry orders with a fill this session
    losers: int = 0  # Losing_Trades closed this session
    net: Decimal = ZERO  # net realized P&L this session, every fee included
    stopped: bool = False  # the internal daily loss stop fired this session
    streak: int = 0  # the Loss_Streak, across sessions
    started: list[Lockout] = field(default_factory=list)

    def session_net(self, s: date) -> Decimal:
        return self.net if self.session == s else ZERO

    def _enter(self, s: date) -> None:
        """A new session resets the session counters; the Loss_Streak carries over."""
        if s != self.session:
            self.session = s
            self.entries = set()
            self.losers = 0
            self.net = ZERO
            self.stopped = False

    def _next_weekday_session(self, s: date) -> date:
        d = s + ONE_DAY
        while d.weekday() >= 5 or d in self.holidays:
            d += ONE_DAY
        return d

    def _lock(self, rule: LockoutRule, t: Instant, last_session: date) -> None:
        self.started.append(Lockout(rule, t, last_session))

    def fill(
        self,
        s: date,
        t: Instant,
        client_id: str,
        role: OrderRole,
        gross: Decimal,
        fees: Decimal,
        trade_net: Decimal | None,
    ) -> None:
        """One fill in session ``s``; ``trade_net`` is set when it is a trade's last exit."""
        cfg = self.cfg
        self._enter(s)
        net_before = self.net
        self.net += gross - fees
        if role == "entry":
            first_fill = client_id not in self.entries
            self.entries.add(client_id)
            # 16.1: an entry order's first fill brings the count to the maximum.
            if first_fill and cfg.max_trades.enabled and len(self.entries) == cfg.max_trades.max:
                self._lock("max_trades", t, s)
            return
        if trade_net is not None:
            if trade_net < -cfg.losing_trade_tolerance_usd:  # a Losing_Trade
                self.losers += 1
                self.streak += 1
                # 16.2: the session's Losing_Trades reach the limit.
                if cfg.max_losers.enabled and self.losers == cfg.max_losers.limit:
                    self._lock("max_losers", t, s)
                # 16.3: the Loss_Streak reaches the limit; the Lockout covers the next
                # non-holiday weekday too, and resets the streak (Glossary).
                if cfg.consecutive_losers.enabled and self.streak == cfg.consecutive_losers.limit:
                    self._lock("consecutive_losers", t, self._next_weekday_session(s))
                    self.streak = 0
            else:
                self.streak = 0
            # 16.4: the last exit leaves the session net below the threshold.
            if cfg.red_day.enabled and self.net < cfg.red_day.threshold_usd:
                self._lock("red_day", t, s)
        # 16.5: any exit fill brings the session net to or above the cap.
        cap = cfg.daily_profit_cap
        if cap.enabled and net_before < cap.cap_usd <= self.net:
            self._lock("daily_profit_cap", t, s)

    def mark(
        self, s: date, t: Instant, positions: Mapping[str, int], unrealized: Decimal
    ) -> tuple[CloseIntent, ...]:
        """16.6: the 1-minute close at ``t``; returns the market closes it orders."""
        self._enter(s)
        if not self.cfg.internal_daily_loss_stop.enabled or self.stopped:
            return ()
        if self.net + unrealized > self.stop_limit:
            return ()
        self.stopped = True
        self._lock("internal_daily_loss_stop", t, s)
        return tuple(
            CloseIntent(name, "sell" if qty > 0 else "buy", abs(qty))
            for name, qty in sorted(positions.items())
            if qty != 0
        )

    def active(self, s: date) -> list[Lockout]:
        return sorted((lo for lo in self.started if lo.last_session >= s), key=lockout_key)


# ---------------------------------------------------------------- run


@dataclass
class Book:
    """A trade in progress: its fills so far."""

    entry_t: Instant | None = None
    entry_fees: Decimal = ZERO
    gross: Decimal = ZERO
    fees: Decimal = ZERO
    exits: list[Fill] = field(default_factory=list)


def check_blocking(rs: RiskState, s: date, want: list[Lockout]) -> None:
    """Req 16.8 and the data of Req 16.9 in session ``s``."""
    where = f"session {s}"
    assert sorted(active_lockouts(rs, s), key=lockout_key) == want, where
    assert entries_blocked(rs, s) is bool(want), where
    assert entries_to_cancel(rs, s, WORKING) == (WORKING_ENTRIES if want else ()), where


class Run:
    """Feeds one case to the Risk_Manager and the reference model side by side."""

    def __init__(self, case: Case) -> None:
        self.case = case
        self.cfg = case.cfg
        self.cal = case.calendar()
        multiple = Decimal(str(case.cfg.internal_daily_loss_stop.multiple))
        self.model = Reference(case.cfg, frozenset(case.holidays), -(multiple * case.risk_usd))
        self.rs = RiskState()
        self.positions = dict.fromkeys(INSTRUMENTS, 0)

    def check(self, s: date, what: str) -> None:
        want = self.model.active(s)
        assert sorted(self.rs.lockouts, key=lockout_key) == want, what
        assert self.rs.loss_streak == self.model.streak, what
        check_blocking(self.rs, s, want)

    def idle(self, s: date) -> None:
        """A weekday session with no events: only the Lockouts carried into it."""
        want = self.model.active(s)
        rolled = roll_session(self.rs, s)
        assert sorted(rolled.lockouts, key=lockout_key) == want, f"idle {s}"
        assert rolled.loss_streak == self.model.streak, f"idle {s}"
        check_blocking(rolled, s, want)
        check_blocking(self.rs, s, want)

    def session(self, plan: SessionPlan) -> None:
        s = plan.session
        start = self.cal.trading_day_start(s)
        books = [Book() for _ in plan.trades]
        for minute, phase, k, j in plan.steps():
            t = start + minute * NS_PER_MINUTE
            if phase == 0:
                self.fill(s, k, j, t, plan.trades[k], books[k])
                self.check(s, f"{s} trade {k} fill {j} at {t}")
            else:
                self.mark(s, t + NS_PER_MINUTE, plan.marks[k])
                self.check(s, f"{s} mark {k} at {t + NS_PER_MINUTE}")

    def fill(self, s: date, k: int, j: int, t: Instant, plan: TradePlan, book: Book) -> None:
        sign = direction_sign(plan.direction)
        if j < len(plan.entries):
            ep = plan.entries[j]
            entry = Fill(f"{s}-t{k}-entry", t, PRICE, ep.qty, ep.fees)
            if book.entry_t is None:
                book.entry_t = t
            book.entry_fees += ep.fees
            book.fees += ep.fees
            self.positions[plan.instrument] += sign * ep.qty
            self.rs = on_fill(self.rs, RiskFill(entry, "entry"), None, self.cfg, self.cal)
            self.model.fill(s, t, entry.client_id, "entry", ZERO, ep.fees, None)
            return
        xp = plan.exits[j - len(plan.entries)]
        gross = self.gross(s, xp, book)
        exit_ = Fill(f"{s}-t{k}-x{j}", t, PRICE, xp.qty, xp.fees)
        book.gross += gross
        book.fees += xp.fees
        book.exits.append(exit_)
        self.positions[plan.instrument] -= sign * xp.qty
        closed = self.trade(s, k, plan, book) if len(book.exits) == len(plan.exits) else None
        self.rs = on_fill(self.rs, RiskFill(exit_, xp.role, gross), closed, self.cfg, self.cal)
        trade_net = book.gross - book.fees if closed is not None else None
        self.model.fill(s, t, exit_.client_id, xp.role, gross, xp.fees, trade_net)

    def gross(self, s: date, xp: ExitPlan, book: Book) -> Decimal:
        if xp.aim is None:
            return xp.gross
        if xp.aim.target == "losing_line":
            line = -self.cfg.losing_trade_tolerance_usd + xp.aim.offset
            return line - book.gross + book.fees + xp.fees
        if xp.aim.target == "red_day":
            level = self.cfg.red_day.threshold_usd
        else:
            level = self.cfg.daily_profit_cap.cap_usd
        return level + xp.aim.offset - self.model.session_net(s) + xp.fees

    def trade(self, s: date, k: int, plan: TradePlan, book: Book) -> Trade:
        assert book.entry_t is not None
        qty = sum(ep.qty for ep in plan.entries)
        entry = Fill(f"{s}-t{k}-entry", book.entry_t, PRICE, qty, book.entry_fees)
        net = book.gross - book.fees
        key = SetupKey(plan.instrument, "gatekeeper_fade", 5800.0, plan.direction, s, k + 1)
        stop = PRICE - 20 * direction_sign(plan.direction)
        return Trade(
            setup_key=key,
            entry_fill=entry,
            initial_stop=stop,
            qty_at_entry=qty,
            exits=tuple(book.exits),
            net=net,
            r=R_USD,
            r_multiple=net / R_USD,
            reached_tp1=False,
            mae_r=ZERO,
            mfe_r=ZERO,
            missing_bars=0,
            shadow=False,
        )

    def mark(self, s: date, t: Instant, plan: MarkPlan) -> None:
        held = dict(self.positions)
        if not any(held.values()):
            unrealized = ZERO
        elif plan.aim is not None:
            unrealized = self.model.stop_limit - self.model.session_net(s) + plan.aim
        else:
            unrealized = plan.unrealized
        self.rs, closes = on_minute_close(
            self.rs, t, held, unrealized, self.case.risk_usd, self.cfg, self.cal
        )
        assert closes == self.model.mark(s, t, held, unrealized), f"{s} mark at {t}"


# ---------------------------------------------------------------- property


# Feature: skylit-futures-strategy-engine, Property 48: Kill switches match the reference model
@given(case=cases())
def test_kill_switches_match_the_reference_model(case: Case) -> None:
    run = Run(case)
    plans = {plan.session: plan for plan in case.plans}
    holidays = frozenset(case.holidays)
    d, end = case.plans[0].session, case.plans[-1].session + timedelta(days=8)
    while d <= end:
        if d.weekday() < 5 and d not in holidays:
            plan = plans.get(d)
            if plan is not None and plan.steps():
                run.session(plan)
            else:
                run.idle(d)
        d += ONE_DAY
    for lo in run.model.started:
        event(f"lockout {lo.rule}")
