"""Account_Simulator: the Topstep 50K Trading Combine rules (design §15, Req 15).

One :class:`AccountSim` runs sequential Combine_Attempts over a backtest. The
Backtester drives it once per trading day, in this order:

1. ``start_trading_day(session)``. Starts a new Combine_Attempt when none is
   active (the first day, or the day after a pass or a fail), so strategy
   metrics cover every session (Req 15.6, 15.9, 15.17).
2. For each bar, in close-time order: ``on_fill(fill, order)`` for every
   Fill_Simulator fill on the bar, then ``on_bar(bars)`` with that interval's
   bar of each instrument. ``on_bar`` applies the Maximum Loss Limit and the
   Daily Loss Limit on each bar's worst price (Req 15.8-15.12).
3. ``check_order(order, working_entries)`` before any new order joins the
   book: the position cap, the DLL and Flat_Deadline blocks, and the end of
   the attempt (Req 15.5, 15.9, 15.11, 15.16, 15.17).
4. ``end_trading_day(closing_bars)`` at the Flat_Deadline, with the first bar
   of each instrument that opens at or after it: the flat close, then the
   MLL_Floor, consistency target and pass checks (Req 15.7, 15.13, 15.16-15.17).
5. ``finish()`` when the data range ends (Req 15.18).

A :class:`Liquidation` or :class:`FlatDeadlineClose` event means: cancel every
working order and close the book's open positions with the listed
:class:`ForcedExit` records. The simulator has already booked those exits, so
they are not passed back to ``on_fill``. A liquidation sets the balance by the
rule (the MLL_Floor or minus the DLL, or the open-price value when that is
worse), not from exit prices, so its exits carry no fees and their price is
the bar's worst price, the price the breach was measured at.

Accounting is exact (``Decimal`` dollars, integer ticks). Positions are net
per instrument, as on a broker account; P&L is linear in price, so balance
plus unrealized P&L does not depend on how exits are matched to entries.
Every fill and P&L change belongs to the trading day from 18:00 on the day
before the session to the session's Flat_Deadline (Req 15.14-15.15), which the
:class:`~fse.timekit.SessionCalendar` defines; a fill outside the current
trading day raises ``ValueError``.

Disabled rules (Req 15.3) are skipped, and every :class:`AttemptResult` lists
their ids. With the Maximum Loss Limit disabled there is no MLL_Floor
(``None``) and no breach; with the profit target disabled there is no pass.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from fractions import Fraction
from types import MappingProxyType
from typing import Final, Literal

from fse.config.schema.account import AccountConfig, AccountRuleId
from fse.engine.types import Bar, Fill, Money, Order, Side, Ticks
from fse.timekit import Instant, SessionCalendar

__all__ = [
    "MICRO_EQUIVALENTS",
    "TICK_VALUE_USD",
    "AccountEvent",
    "AccountRejection",
    "AccountSim",
    "AccountSnapshot",
    "AttemptEnded",
    "AttemptOutcome",
    "AttemptResult",
    "AttemptSnapshot",
    "AttemptStarted",
    "FlatDeadlineClose",
    "ForcedExit",
    "Liquidation",
    "RejectionRule",
    "consistency_profit_target",
    "next_mll_floor",
]

TICK_VALUE_USD: Final[Mapping[str, Money]] = MappingProxyType(
    {"MES": Decimal("1.25"), "MNQ": Decimal("0.50"), "ES": Decimal("12.50"), "NQ": Decimal("5.00")}
)
"""Dollars per 0.25-point tick per contract (Req 13.9)."""

MICRO_EQUIVALENTS: Final[Mapping[str, int]] = MappingProxyType(
    {"MES": 1, "MNQ": 1, "ES": 10, "NQ": 10}
)
"""Position-cap units per contract (Glossary: Micro_Equivalent)."""

_CENT: Final = Fraction(1, 100)
_ZERO: Final = Decimal("0.00")

type AttemptOutcome = Literal["passed", "failed", "incomplete"]
type RejectionRule = Literal[
    "position_cap",
    "daily_loss_limit",
    "flat_deadline",
    "maximum_loss_limit",
    "profit_target",
    "no_session",
]
"""Why ``check_order`` refused an order.

``maximum_loss_limit`` and ``profit_target`` mean the Combine_Attempt has
ended (failed or passed); ``flat_deadline`` covers the time from a
Flat_Deadline to the next ``start_trading_day``; ``no_session`` means no
trading day has started yet.
"""


# ---------------------------------------------------------------- rule arithmetic


def next_mll_floor(floor: Money, eod_balance: Money, mll: Money, start: Money) -> Money:
    """The MLL_Floor after a trading day: ``min(start, max(floor, eod - mll))`` (Req 15.7)."""
    return min(start, max(floor, eod_balance - mll))


def consistency_profit_target(configured: Money, best_day: Money | None, pct: float) -> Money:
    """``max(configured, best_day / share)`` with the quotient rounded up to a cent (Req 15.13).

    ``pct`` is the consistency target in percent; it is read through its
    shortest ``repr`` (55.0 means exactly 55/100). The division and the
    rounding are exact rationals, so no quotient rounds the wrong way.
    """
    if best_day is None:
        return configured
    share = Fraction(Decimal(repr(pct))) / 100
    cents = math.ceil(Fraction(best_day) / share / _CENT)
    raised = (Decimal(cents) / 100).quantize(Decimal("0.01"))
    return max(configured, raised)


# ---------------------------------------------------------------- results and events


@dataclass(frozen=True, slots=True)
class AttemptResult:
    """The outcome of one Combine_Attempt (Req 15.9, 15.17, 15.18).

    - ``number``: 1 for the run's first attempt, then 2, 3, ...
    - ``last_session``: the pass date for a pass, the breach session for a
      fail, the last trading day processed for an incomplete attempt.
    - ``trading_days``: calendar sessions from ``first_session`` through
      ``last_session``, inclusive.
    - ``ended_ns``: for a fail, the open instant of the bar on which the MLL
      was breached (the ``Fill.bar_open_ns`` convention), or the Flat_Deadline
      when the flat close breached it; for a pass, the Flat_Deadline; ``None``
      for an incomplete attempt.
    - ``mll_floor``: ``None`` when the Maximum Loss Limit is disabled.
    - ``profit_target``: the current profit target after any consistency raise.
    - ``best_day``: the largest net P&L of a completed trading day, or ``None``.
    - ``disabled_rules``: the run's disabled rule ids (Req 15.3).
    """

    number: int
    outcome: AttemptOutcome
    first_session: date
    last_session: date
    trading_days: int
    ended_ns: Instant | None
    final_balance: Money
    mll_floor: Money | None
    profit_target: Money
    best_day: Money | None
    disabled_rules: tuple[AccountRuleId, ...]


@dataclass(frozen=True, slots=True)
class ForcedExit:
    """One position close the simulator booked; the Backtester mirrors it in its book."""

    instrument: str
    side: Side
    qty: int
    price: Ticks
    fees: Money


@dataclass(frozen=True, slots=True)
class AttemptStarted:
    number: int
    session: date
    balance: Money
    mll_floor: Money | None


@dataclass(frozen=True, slots=True)
class Liquidation:
    """An MLL or DLL liquidation on the bar opening at ``bar_open_ns`` (Req 15.8, 15.10).

    ``balance`` and ``day_pnl`` are the values after the liquidation.
    """

    rule: Literal["maximum_loss_limit", "daily_loss_limit"]
    session: date
    bar_open_ns: Instant
    exits: tuple[ForcedExit, ...]
    balance: Money
    day_pnl: Money


@dataclass(frozen=True, slots=True)
class FlatDeadlineClose:
    """The Flat_Deadline was reached: positions closed at the next bar's open (Req 15.16)."""

    session: date
    deadline_ns: Instant
    exits: tuple[ForcedExit, ...]


@dataclass(frozen=True, slots=True)
class AttemptEnded:
    result: AttemptResult


type AccountEvent = AttemptStarted | Liquidation | FlatDeadlineClose | AttemptEnded


@dataclass(frozen=True, slots=True)
class AccountRejection:
    """An order ``check_order`` refused (Req 15.5, 15.9, 15.11, 15.16, 15.17).

    ``total`` and ``cap`` are set for a position-cap rejection: the
    Micro_Equivalents the order would have made, and the cap.
    """

    rule: RejectionRule
    client_id: str
    message: str
    total: int | None = None
    cap: int | None = None


# ---------------------------------------------------------------- the simulator


@dataclass(slots=True)
class _Lot:
    sign: int  # +1 long, -1 short
    qty: int
    price: Ticks


@dataclass(slots=True)
class _Attempt:
    number: int
    first_session: date
    balance: Money
    mll_floor: Money | None
    profit_target: Money
    best_day: Money | None = None
    outcome: AttemptOutcome | None = None  # None while active
    ended_ns: Instant | None = None


@dataclass(frozen=True, slots=True)
class AttemptSnapshot:
    """A frozen copy of the running or last Combine_Attempt (for :class:`AccountSnapshot`)."""

    number: int
    first_session: date
    balance: Money
    mll_floor: Money | None
    profit_target: Money
    best_day: Money | None
    outcome: AttemptOutcome | None
    ended_ns: Instant | None


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    """Everything an :class:`AccountSim` holds, frozen, so a live Paper_Broker can resume.

    ``lots`` lists each instrument's open lots as ``(sign, qty, price)`` and
    ``marks`` the last seen close per instrument, both in insertion order.
    """

    results: tuple[AttemptResult, ...]
    attempt: AttemptSnapshot | None
    session: date | None
    last_session: date | None
    finished: bool
    lots: tuple[tuple[str, tuple[tuple[int, int, Ticks], ...]], ...]
    marks: tuple[tuple[str, Ticks], ...]
    day_pnl: Money
    dll_hit: bool


def _require_instrument(instrument: str) -> None:
    if instrument not in TICK_VALUE_USD:
        raise ValueError(
            f"instrument {instrument!r} is not one of the contracts {sorted(TICK_VALUE_USD)}"
        )


class AccountSim:
    """Sequential Combine_Attempts under one validated ``AccountConfig``.

    ``calendar`` must use this config's Flat_Deadline and early-close offset;
    build its times with ``cfg.session_times(...)``. ``fee_per_contract`` is
    commission plus exchange fee per contract and instrument (the Fill_Simulator
    costs, Req 13.7), charged on the Flat_Deadline close.
    """

    def __init__(
        self,
        cfg: AccountConfig,
        calendar: SessionCalendar,
        fee_per_contract: Mapping[str, Money],
    ) -> None:
        times = calendar.times
        if (
            times.flat_deadline != cfg.flat_deadline
            or times.flat_deadline_early_close_offset_min != cfg.early_close_offset_min
        ):
            raise ValueError(
                "the calendar's Flat_Deadline settings differ from the account config; "
                "build the calendar times with AccountConfig.session_times()"
            )
        for instrument, fee in fee_per_contract.items():
            _require_instrument(instrument)
            if not isinstance(fee, Decimal) or not fee.is_finite() or fee < 0:
                raise ValueError(f"fee for {instrument} must be a Decimal of at least 0: {fee!r}")
        self._cfg = cfg
        self._calendar = calendar
        self._fees: Mapping[str, Money] = MappingProxyType(dict(fee_per_contract))
        self._disabled = cfg.disabled_rules()
        self._results: list[AttemptResult] = []
        self._attempt: _Attempt | None = None
        self._session: date | None = None  # the trading day in progress
        self._last_session: date | None = None
        self._finished = False
        self._lots: dict[str, list[_Lot]] = {}
        self._marks: dict[str, Ticks] = {}
        self._day_pnl: Money = _ZERO
        self._dll_hit = False

    # ---------------------------------------------------------------- state

    @property
    def disabled_rules(self) -> tuple[AccountRuleId, ...]:
        return self._disabled

    @property
    def results(self) -> tuple[AttemptResult, ...]:
        """Every ended Combine_Attempt so far, in order (incomplete ones after ``finish``)."""
        return tuple(self._results)

    @property
    def session(self) -> date | None:
        """The trading day in progress, or ``None`` between Flat_Deadline and the next start."""
        return self._session

    @property
    def active(self) -> bool:
        """Whether a Combine_Attempt is running (started, neither passed nor failed)."""
        return self._attempt is not None and self._attempt.outcome is None

    @property
    def attempt_number(self) -> int:
        """The current or last attempt's number; 0 before the first trading day."""
        return 0 if self._attempt is None else self._attempt.number

    @property
    def balance(self) -> Money:
        """Starting balance plus the attempt's net realized P&L."""
        return self._cfg.starting_balance.value if self._attempt is None else self._attempt.balance

    @property
    def mll_floor(self) -> Money | None:
        return None if self._attempt is None else self._attempt.mll_floor

    @property
    def profit_target(self) -> Money:
        """The current profit target, after any consistency raise."""
        if self._attempt is None:
            return self._cfg.profit_target.value
        return self._attempt.profit_target

    @property
    def day_pnl(self) -> Money:
        """The trading day's net realized P&L, after any DLL adjustment."""
        return self._day_pnl

    @property
    def blocked_by(self) -> RejectionRule | None:
        """The block on entry orders right now, or ``None`` when entries may be placed."""
        if self._attempt is not None and self._attempt.outcome is not None:
            return "maximum_loss_limit" if self._attempt.outcome == "failed" else "profit_target"
        if self._session is None:
            return "no_session" if self._last_session is None else "flat_deadline"
        return "daily_loss_limit" if self._dll_hit else None

    @property
    def positions(self) -> Mapping[str, int]:
        """Net open contracts per instrument: positive long, negative short."""
        return MappingProxyType(
            {i: sum(lot.sign * lot.qty for lot in lots) for i, lots in self._lots.items()}
        )

    def open_micro_equivalents(self) -> int:
        """Micro_Equivalents of the open contracts, without regard to direction."""
        return sum(
            MICRO_EQUIVALENTS[i] * sum(lot.qty for lot in lots) for i, lots in self._lots.items()
        )

    # ---------------------------------------------------------------- snapshots

    def snapshot(self) -> AccountSnapshot:
        """The simulator's whole state, frozen (the config, calendar and fees excluded)."""
        a = self._attempt
        attempt = (
            None
            if a is None
            else AttemptSnapshot(
                a.number,
                a.first_session,
                a.balance,
                a.mll_floor,
                a.profit_target,
                a.best_day,
                a.outcome,
                a.ended_ns,
            )
        )
        return AccountSnapshot(
            results=tuple(self._results),
            attempt=attempt,
            session=self._session,
            last_session=self._last_session,
            finished=self._finished,
            lots=tuple(
                (i, tuple((lot.sign, lot.qty, lot.price) for lot in lots))
                for i, lots in self._lots.items()
            ),
            marks=tuple(self._marks.items()),
            day_pnl=self._day_pnl,
            dll_hit=self._dll_hit,
        )

    @classmethod
    def restore(
        cls,
        cfg: AccountConfig,
        calendar: SessionCalendar,
        fee_per_contract: Mapping[str, Money],
        snap: AccountSnapshot,
    ) -> AccountSim:
        """A simulator in the state ``snap`` recorded, under the same config, calendar and fees."""
        sim = cls(cfg, calendar, fee_per_contract)
        a = snap.attempt
        sim._results = list(snap.results)
        sim._attempt = (
            None
            if a is None
            else _Attempt(
                a.number,
                a.first_session,
                a.balance,
                a.mll_floor,
                a.profit_target,
                a.best_day,
                a.outcome,
                a.ended_ns,
            )
        )
        sim._session = snap.session
        sim._last_session = snap.last_session
        sim._finished = snap.finished
        for instrument, lots in snap.lots:
            _require_instrument(instrument)
            sim._lots[instrument] = [_Lot(sign, qty, price) for sign, qty, price in lots]
        sim._marks = dict(snap.marks)
        sim._day_pnl = snap.day_pnl
        sim._dll_hit = snap.dll_hit
        return sim

    # ---------------------------------------------------------------- trading days

    def start_trading_day(self, session: date) -> list[AccountEvent]:
        """Open trading day ``session``; start a Combine_Attempt if none is active."""
        self._require_open_run()
        if self._session is not None:
            raise ValueError(f"trading day {self._session} has not ended")
        if not self._calendar.is_session(session):
            raise ValueError(f"{session} is not a session in the exchange calendar")
        if self._last_session is not None and session <= self._last_session:
            raise ValueError(f"trading day {session} is not after {self._last_session}")
        events: list[AccountEvent] = []
        if not self.active:
            start = self._cfg.starting_balance.value
            mll = self._cfg.maximum_loss_limit
            self._attempt = _Attempt(
                number=self.attempt_number + 1,
                first_session=session,
                balance=start,
                mll_floor=start - mll.value if mll.enabled else None,
                profit_target=self._cfg.profit_target.value,
            )
            events.append(
                AttemptStarted(self._attempt.number, session, start, self._attempt.mll_floor)
            )
        self._session = session
        self._day_pnl = _ZERO
        self._dll_hit = False
        return events

    def end_trading_day(self, closing_bars: Mapping[str, Bar]) -> list[AccountEvent]:
        """Apply the Flat_Deadline, then the day-end rules (Req 15.7, 15.13, 15.16, 15.17).

        ``closing_bars`` holds, per instrument with an open position, the first
        bar that opens at or after the Flat_Deadline; positions close at its
        open. Other entries are ignored.
        """
        session = self._require_session()
        deadline = self._calendar.flat_deadline(session)
        attempt = self._attempt
        assert attempt is not None  # a started trading day always has an attempt
        events: list[AccountEvent] = []
        if attempt.outcome is None:
            exits: list[ForcedExit] = []
            for instrument in sorted(self._lots):
                bar = closing_bars.get(instrument)
                if bar is None or bar.instrument != instrument:
                    raise ValueError(f"no closing bar for open {instrument} at the Flat_Deadline")
                if bar.open_ns < deadline:
                    raise ValueError(f"the {instrument} closing bar opens before the Flat_Deadline")
                exits.append(self._close_position(instrument, _tick_price(bar, "o_t")))
            events.append(FlatDeadlineClose(session, deadline, tuple(exits)))
            events.extend(self._day_end_rules(attempt, session, deadline))
        self._last_session = session
        self._session = None
        return events

    def finish(self) -> tuple[AttemptResult, ...]:
        """End the run: record an active attempt as incomplete (Req 15.18); return every result."""
        self._require_open_run()
        if self._session is not None:
            raise ValueError(f"trading day {self._session} has not ended")
        if self.active and self._last_session is not None:
            assert self._attempt is not None
            self._attempt.outcome = "incomplete"
            self._results.append(self._result(self._attempt, self._last_session))
        self._finished = True
        return tuple(self._results)

    # ---------------------------------------------------------------- fills and bars

    def on_fill(self, fill: Fill, order: Order) -> None:
        """Book one Fill_Simulator fill of ``order`` (Req 15.14)."""
        session = self._require_session()
        attempt = self._attempt
        if attempt is None or attempt.outcome is not None:
            raise ValueError(f"fill of {fill.client_id} after the Combine_Attempt ended")
        if fill.client_id != order.client_id:
            raise ValueError(f"fill of {fill.client_id} does not belong to order {order.client_id}")
        _require_instrument(order.instrument)
        self._require_in_day(session, fill.bar_open_ns, f"fill of {fill.client_id}")
        sign = 1 if order.side == "buy" else -1
        net = self._apply(order.instrument, sign, fill.qty, fill.price) - fill.fees
        attempt.balance += net
        self._day_pnl += net
        self._marks.setdefault(order.instrument, fill.price)

    def on_bar(self, bars: Mapping[str, Bar]) -> list[AccountEvent]:
        """Check the MLL, then the DLL, on one interval's bars (Req 15.8-15.12).

        ``bars`` maps each instrument to its bar for one interval; every bar
        opens at the same instant, inside the trading day and before the
        Flat_Deadline. A position whose instrument has no bar in ``bars`` is
        valued at its last seen close.
        """
        session = self._require_session()
        if not bars:
            raise ValueError("on_bar needs at least one bar")
        opens = {bar.open_ns for bar in bars.values()}
        if len(opens) != 1:
            raise ValueError("every bar passed to on_bar must open at the same instant")
        [open_ns] = opens
        self._require_in_day(session, open_ns, "bar")
        if open_ns >= self._calendar.flat_deadline(session):
            raise ValueError("bars at or after the Flat_Deadline go to end_trading_day")
        for instrument, bar in bars.items():
            if bar.instrument != instrument:
                raise ValueError(f"bar for {bar.instrument} is keyed as {instrument}")
        attempt = self._attempt
        if attempt is None or attempt.outcome is not None:
            self._update_marks(bars)
            return []
        worst, at_open = self._unrealized(bars)
        self._update_marks(bars)
        floor = attempt.mll_floor
        if floor is not None and attempt.balance + worst <= floor:
            balance = min(floor, attempt.balance + at_open)
            exits = self._liquidate(bars)
            self._day_pnl += balance - attempt.balance
            attempt.balance = balance
            attempt.outcome = "failed"
            attempt.ended_ns = open_ns
            result = self._result(attempt, session)
            self._results.append(result)
            return [
                Liquidation("maximum_loss_limit", session, open_ns, exits, balance, self._day_pnl),
                AttemptEnded(result),
            ]
        dll = self._cfg.daily_loss_limit
        if dll.enabled and not self._dll_hit and self._day_pnl + worst <= -dll.value:
            day_pnl = min(-dll.value, self._day_pnl + at_open)
            exits = self._liquidate(bars)
            attempt.balance += day_pnl - self._day_pnl
            self._day_pnl = day_pnl
            self._dll_hit = True
            return [
                Liquidation("daily_loss_limit", session, open_ns, exits, attempt.balance, day_pnl)
            ]
        return []

    # ---------------------------------------------------------------- orders

    def check_order(
        self, order: Order, working_entries: Iterable[Order] = ()
    ) -> Order | AccountRejection:
        """Accept ``order`` or refuse it (Req 15.5, 15.9, 15.11, 15.16, 15.17).

        After a pass or a fail every order is refused until the next attempt
        starts. While a trading day runs, exit orders are always accepted, and
        an entry order is refused during a DLL block or when the open plus
        working-entry Micro_Equivalents (``working_entries``, the book's
        resting entry orders) plus this order would exceed the position cap.
        """
        _require_instrument(order.instrument)
        blocked = self.blocked_by
        if blocked in ("maximum_loss_limit", "profit_target", "no_session", "flat_deadline"):
            return AccountRejection(blocked, order.client_id, _BLOCK_MESSAGES[blocked])
        if order.role != "entry":
            return order
        if blocked == "daily_loss_limit":
            return AccountRejection(blocked, order.client_id, _BLOCK_MESSAGES[blocked])
        cap = self._cfg.position_cap
        if cap.enabled:
            working = 0
            for other in working_entries:
                if other.role == "entry" and other.client_id != order.client_id:
                    _require_instrument(other.instrument)
                    working += MICRO_EQUIVALENTS[other.instrument] * other.qty
            total = (
                self.open_micro_equivalents()
                + working
                + MICRO_EQUIVALENTS[order.instrument] * order.qty
            )
            if total > cap.micro_equivalents:
                return AccountRejection(
                    "position_cap",
                    order.client_id,
                    f"position_cap: {total} Micro_Equivalents would exceed the cap of "
                    f"{cap.micro_equivalents}",
                    total=total,
                    cap=cap.micro_equivalents,
                )
        return order

    # ---------------------------------------------------------------- internals

    def _require_open_run(self) -> None:
        if self._finished:
            raise ValueError("the account simulation has finished")

    def _require_session(self) -> date:
        self._require_open_run()
        if self._session is None:
            raise ValueError("no trading day is in progress; call start_trading_day")
        return self._session

    def _require_in_day(self, session: date, t: Instant, what: str) -> None:
        day = self._calendar.trading_day_of(t)
        if day != session:
            raise ValueError(
                f"{what} at {t} is outside trading day {session} (it belongs to {day})"
            )

    def _apply(self, instrument: str, sign: int, qty: int, price: Ticks) -> Money:
        """Net ``qty`` at ``price`` into the instrument's lots; return the realized gross P&L."""
        lots = self._lots.setdefault(instrument, [])
        ticks = 0
        remaining = qty
        while remaining and lots and lots[0].sign != sign:
            lot = lots[0]
            take = min(lot.qty, remaining)
            ticks += (price - lot.price) * lot.sign * take
            lot.qty -= take
            remaining -= take
            if lot.qty == 0:
                lots.pop(0)
        if remaining:
            lots.append(_Lot(sign, remaining, price))
        if not lots:
            del self._lots[instrument]
        return ticks * TICK_VALUE_USD[instrument]

    def _mark(self, instrument: str, bars: Mapping[str, Bar], field: str) -> Ticks:
        bar = bars.get(instrument)
        return self._marks[instrument] if bar is None else _tick_price(bar, field)

    def _unrealized(self, bars: Mapping[str, Bar]) -> tuple[Money, Money]:
        """Unrealized P&L at each position's worst price and at the bar open."""
        worst = _ZERO
        at_open = _ZERO
        for instrument, lots in self._lots.items():
            sign = lots[0].sign
            low_or_high = self._mark(instrument, bars, "l_t" if sign > 0 else "h_t")
            open_ = self._mark(instrument, bars, "o_t")
            value = TICK_VALUE_USD[instrument]
            for lot in lots:
                worst += (low_or_high - lot.price) * lot.sign * lot.qty * value
                at_open += (open_ - lot.price) * lot.sign * lot.qty * value
        return worst, at_open

    def _update_marks(self, bars: Mapping[str, Bar]) -> None:
        for instrument, bar in bars.items():
            if bar.c_t is not None:
                self._marks[instrument] = bar.c_t

    def _liquidate(self, bars: Mapping[str, Bar]) -> tuple[ForcedExit, ...]:
        """Drop every position (the rule sets the balance); record the exits at worst price."""
        exits: list[ForcedExit] = []
        for instrument in sorted(self._lots):
            lots = self._lots[instrument]
            sign = lots[0].sign
            price = self._mark(instrument, bars, "l_t" if sign > 0 else "h_t")
            qty = sum(lot.qty for lot in lots)
            exits.append(ForcedExit(instrument, "sell" if sign > 0 else "buy", qty, price, _ZERO))
        self._lots.clear()
        return tuple(exits)

    def _close_position(self, instrument: str, price: Ticks) -> ForcedExit:
        """Close one instrument at ``price``, with fees; book the realized P&L."""
        attempt = self._attempt
        assert attempt is not None
        fee = self._fees.get(instrument)
        if fee is None:
            raise ValueError(f"no fee_per_contract for {instrument}")
        lots = self._lots[instrument]
        sign = lots[0].sign
        qty = sum(lot.qty for lot in lots)
        fees = fee * qty
        net = self._apply(instrument, -sign, qty, price) - fees
        attempt.balance += net
        self._day_pnl += net
        return ForcedExit(instrument, "sell" if sign > 0 else "buy", qty, price, fees)

    def _day_end_rules(
        self, attempt: _Attempt, session: date, deadline: Instant
    ) -> list[AccountEvent]:
        cfg = self._cfg
        if attempt.mll_floor is not None and attempt.balance <= attempt.mll_floor:
            # The flat close itself breached the MLL_Floor (Req 15.8 at the open price).
            return self._end(attempt, session, "failed", deadline)
        attempt.best_day = (
            self._day_pnl if attempt.best_day is None else max(attempt.best_day, self._day_pnl)
        )
        start = cfg.starting_balance.value
        if attempt.mll_floor is not None:
            attempt.mll_floor = next_mll_floor(
                attempt.mll_floor, attempt.balance, cfg.maximum_loss_limit.value, start
            )
        if cfg.consistency_target.enabled:
            attempt.profit_target = consistency_profit_target(
                cfg.profit_target.value, attempt.best_day, cfg.consistency_target.pct
            )
        if cfg.profit_target.enabled and attempt.balance >= start + attempt.profit_target:
            return self._end(attempt, session, "passed", deadline)
        return []

    def _end(
        self, attempt: _Attempt, session: date, outcome: AttemptOutcome, at: Instant
    ) -> list[AccountEvent]:
        attempt.outcome = outcome
        attempt.ended_ns = at
        result = self._result(attempt, session)
        self._results.append(result)
        return [AttemptEnded(result)]

    def _result(self, attempt: _Attempt, last_session: date) -> AttemptResult:
        assert attempt.outcome is not None
        days = len(self._calendar.sessions(attempt.first_session, last_session))
        return AttemptResult(
            number=attempt.number,
            outcome=attempt.outcome,
            first_session=attempt.first_session,
            last_session=last_session,
            trading_days=days,
            ended_ns=attempt.ended_ns,
            final_balance=attempt.balance,
            mll_floor=attempt.mll_floor,
            profit_target=attempt.profit_target,
            best_day=attempt.best_day,
            disabled_rules=self._disabled,
        )


_BLOCK_MESSAGES: Final[Mapping[RejectionRule, str]] = MappingProxyType(
    {
        "maximum_loss_limit": "maximum_loss_limit: the Combine_Attempt failed; no more orders",
        "profit_target": "profit_target: the Combine_Attempt passed; no more orders",
        "daily_loss_limit": "daily_loss_limit: entries blocked until the next trading day",
        "flat_deadline": "flat_deadline: orders blocked until the next trading day at 18:00",
        "no_session": "no_session: no trading day has started",
    }
)


def _tick_price(bar: Bar, field: str) -> Ticks:
    value: Ticks | None = getattr(bar, field)
    if value is None:
        raise ValueError(f"the {bar.instrument} bar has no tick prices")
    return value
