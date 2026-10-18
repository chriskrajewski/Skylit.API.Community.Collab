"""The Risk_Manager: Kill_Switches, Lockouts and the order pre-check (design §16, Req 16, 24.23).

**State.** :class:`RiskState` is a frozen value held in ``EngineState``. Only
:func:`on_fill` and :func:`on_minute_close` change it, from the ordered fills,
1-minute closes and config. Nothing else goes in, so the Backtester, an
uninterrupted Live_Runner run and a run restarted from recorded state produce
the same Lockouts (Req 16.7).

**Sessions.** A fill or minute close belongs to the session whose trading day
contains its instant (``SessionCalendar.trading_day_of``). The first event of a
later session resets the session counters and the loss-stop record, and drops
the Lockouts that ended before it. The Loss_Streak carries over.

**Session net.** ``session_net`` adds each exit fill's gross P&L and subtracts
the fees of every fill, entries included, so a closed trade adds exactly its
``Trade.net``.

**Kill_Switches** (each only while enabled, Req 16.1-16.6):

- ``max_trades``: the first fill of an entry order counts it; the count
  reaching exactly ``max`` starts a Lockout.
- ``max_losers``: a Losing_Trade close counts it; reaching exactly ``limit``
  starts a Lockout.
- ``consecutive_losers``: a Losing_Trade close adds 1 to the Loss_Streak and any
  other close resets it to 0 (whether or not the rule is enabled). Reaching
  exactly ``limit`` starts a Lockout through the next non-holiday weekday and
  resets the Loss_Streak to 0.
- ``red_day``: each trade close that leaves ``session_net`` below the threshold
  starts a Lockout.
- ``daily_profit_cap``: an exit fill, partial or last, that takes
  ``session_net`` from below the cap to at or above it starts a Lockout.
- ``internal_daily_loss_stop``: at a 1-minute close, ``session_net`` plus the
  unrealized P&L at the bar's worst prices at or below ``-multiple x risk_usd``
  gives a market close for every open position, then a Lockout. It fires at
  most once per session.

A fill's Lockout starts at ``fill.bar_open_ns``; the loss stop's at the bar's
close. A Lockout is active from its start through the end of ``last_session``.
Every trigger starts its own Lockout; repeats are not merged. While any is
active, new entries are withheld and resting entries cancelled
(:func:`entries_blocked`, :func:`entries_to_cancel`); exits are untouched
(Req 16.8).

**Pre-check** (Req 24.23-24.25). :func:`precheck` passes an order that only
reduces or closes a position: the opposite side of a held position, for at most
its size. Any other order is withheld when it would lift open plus working-entry
Micro_Equivalents above the account position cap, or after the internal daily
loss stop fired this session. Each failed check carries its measured value and
limit.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import MappingProxyType
from typing import Final, Literal

from fse.config.schema.kill_switches import KILL_SWITCH_IDS, KillSwitchesConfig
from fse.engine.types import ORDER_ROLES, Fill, Money, Order, OrderRole, Side, Trade
from fse.timekit import Instant, SessionCalendar

__all__ = [
    "LOCKOUT_RULES",
    "MICRO_EQUIVALENTS",
    "CloseIntent",
    "FailedCheck",
    "Lockout",
    "LockoutRule",
    "LossStopHit",
    "PrecheckName",
    "RiskFill",
    "RiskState",
    "Withheld",
    "active_lockouts",
    "entries_blocked",
    "entries_to_cancel",
    "is_losing_trade",
    "is_reducing",
    "loss_stop_hit",
    "loss_stop_limit",
    "micro_equivalents",
    "next_weekday_session",
    "on_fill",
    "on_minute_close",
    "precheck",
    "roll_session",
]

type LockoutRule = Literal[
    "max_trades",
    "max_losers",
    "consecutive_losers",
    "red_day",
    "daily_profit_cap",
    "internal_daily_loss_stop",
]
"""The rule that started a Lockout: a Kill_Switch id or the internal daily loss stop."""

type PrecheckName = Literal["position_cap", "internal_daily_loss_stop"]
"""A Risk_Manager order pre-check (Req 24.23)."""

LOCKOUT_RULES: Final[frozenset[str]] = frozenset(KILL_SWITCH_IDS)

MICRO_EQUIVALENTS: Final[Mapping[str, int]] = MappingProxyType(
    {"MES": 1, "MNQ": 1, "ES": 10, "NQ": 10}
)
"""Micro_Equivalents per contract (Glossary)."""

_ZERO: Final = Decimal(0)
_ONE_DAY: Final = timedelta(days=1)


def _require_int(name: str, value: object, minimum: int | None = None) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer, got {value!r}")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}, got {value}")


def _require_money(name: str, value: object) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal, got {value!r}")


def _require_date(name: str, value: object) -> None:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise ValueError(f"{name} must be a date (not a datetime), got {value!r}")


def _require_positions(name: str, positions: Mapping[str, int]) -> None:
    for instrument, qty in positions.items():
        _require_int(f"{name}[{instrument!r}]", qty)


# ---------------------------------------------------------------- state


@dataclass(frozen=True, slots=True)
class Lockout:
    """Blocks new entries from ``started_at`` through the end of ``last_session``."""

    rule: LockoutRule
    started_at: Instant
    last_session: date

    def __post_init__(self) -> None:
        if self.rule not in LOCKOUT_RULES:
            raise ValueError(f"Lockout.rule must be one of {sorted(LOCKOUT_RULES)}: {self.rule!r}")
        _require_int("Lockout.started_at", self.started_at)
        _require_date("Lockout.last_session", self.last_session)


@dataclass(frozen=True, slots=True)
class LossStopHit:
    """The internal daily loss stop firing: when, the measured mark and the limit (Req 24.24)."""

    at: Instant
    measured: Money
    limit: Money

    def __post_init__(self) -> None:
        _require_int("LossStopHit.at", self.at)
        _require_money("LossStopHit.measured", self.measured)
        _require_money("LossStopHit.limit", self.limit)
        if self.measured > self.limit:
            raise ValueError("LossStopHit.measured must be at or below its limit")


@dataclass(frozen=True, slots=True)
class RiskState:
    """Session counters, the Loss_Streak and the Lockouts (design §16).

    - ``session``: the session the counters belong to; ``None`` before the
      first event.
    - ``filled_entries``: client ids of the session's entry orders with a fill,
      in fill order; ``session_entries`` is their count (Req 16.1).
    - ``session_losers``: Losing_Trades closed this session (Req 16.2).
    - ``loss_streak``: the Loss_Streak, across sessions (Req 16.3).
    - ``session_net``: net realized P&L this session, after fees.
    - ``lockouts``: started Lockouts that cover ``session`` or later, in start
      order.
    - ``loss_stop``: the internal daily loss stop firing this session, if any.
    """

    session: date | None = None
    filled_entries: tuple[str, ...] = ()
    session_losers: int = 0
    loss_streak: int = 0
    session_net: Money = _ZERO
    lockouts: tuple[Lockout, ...] = ()
    loss_stop: LossStopHit | None = None

    def __post_init__(self) -> None:
        _require_int("RiskState.session_losers", self.session_losers, minimum=0)
        _require_int("RiskState.loss_streak", self.loss_streak, minimum=0)
        _require_money("RiskState.session_net", self.session_net)
        if len(set(self.filled_entries)) != len(self.filled_entries):
            raise ValueError("RiskState.filled_entries must not repeat a client id")
        for client_id in self.filled_entries:
            if not client_id.strip():
                raise ValueError("RiskState.filled_entries must not hold a blank client id")
        if self.session is not None:
            _require_date("RiskState.session", self.session)
        elif (
            self.filled_entries
            or self.session_losers
            or self.session_net != 0
            or self.loss_stop is not None
        ):
            raise ValueError("a RiskState with no session has no session counters")

    @property
    def session_entries(self) -> int:
        """Filled entry orders this session (Req 16.1)."""
        return len(self.filled_entries)


@dataclass(frozen=True, slots=True)
class RiskFill:
    """One fill as the Risk_Manager sees it.

    ``role`` is the filled order's role. ``gross`` is the fill's P&L before
    fees, ``(exit - entry)`` ticks x direction sign x tick value x qty, and is
    0 for an entry fill.
    """

    fill: Fill
    role: OrderRole
    gross: Money = _ZERO

    def __post_init__(self) -> None:
        if self.role not in ORDER_ROLES:
            raise ValueError(f"RiskFill.role must be one of {sorted(ORDER_ROLES)}: {self.role!r}")
        _require_money("RiskFill.gross", self.gross)
        if self.role == "entry" and self.gross != 0:
            raise ValueError("an entry RiskFill has no gross P&L")

    @property
    def net(self) -> Money:
        """The fill's realized P&L after its fees."""
        return self.gross - self.fill.fees


@dataclass(frozen=True, slots=True)
class CloseIntent:
    """Close the whole open position in ``instrument`` at market (Req 16.6)."""

    instrument: str
    side: Side
    qty: int

    def __post_init__(self) -> None:
        if not self.instrument.strip():
            raise ValueError("CloseIntent.instrument must not be blank")
        if self.side not in ("buy", "sell"):
            raise ValueError(f"CloseIntent.side must be buy or sell: {self.side!r}")
        _require_int("CloseIntent.qty", self.qty, minimum=1)


@dataclass(frozen=True, slots=True)
class FailedCheck:
    """One failed pre-check with its measured value and limit (Req 24.24).

    ``position_cap`` values are Micro_Equivalents; ``internal_daily_loss_stop``
    values are dollars.
    """

    check: PrecheckName
    measured: int | Money
    limit: int | Money


@dataclass(frozen=True, slots=True)
class Withheld:
    """An order the pre-check kept from the Broker_Adapter, with every failed check."""

    order: Order
    failures: tuple[FailedCheck, ...]

    def __post_init__(self) -> None:
        if not self.failures:
            raise ValueError("a Withheld order needs at least one failed check")


# ---------------------------------------------------------------- sessions


def next_weekday_session(session: date, cal: SessionCalendar) -> date:
    """The first weekday after ``session`` that the calendar does not list as a holiday.

    Reads ``cal.holidays`` only, so it works past the calendar's last date.
    """
    d = session + _ONE_DAY
    while d.weekday() >= 5 or d in cal.holidays:
        d += _ONE_DAY
    return d


def roll_session(rs: RiskState, session: date) -> RiskState:
    """``rs`` moved to ``session``: counters reset, ended Lockouts dropped, streak kept.

    Raises ``ValueError`` for a session before ``rs.session``: events must
    arrive in order.
    """
    _require_date("session", session)
    if rs.session == session:
        return rs
    if rs.session is not None and session < rs.session:
        raise ValueError(f"session {session} is before the Risk_Manager's session {rs.session}")
    kept = tuple(lo for lo in rs.lockouts if lo.last_session >= session)
    return RiskState(session=session, loss_streak=rs.loss_streak, lockouts=kept)


def _fill_session(fill: Fill, cal: SessionCalendar) -> date:
    session = cal.trading_day_of(fill.bar_open_ns)
    if session is None:
        raise ValueError(
            f"fill of {fill.client_id!r} at {fill.bar_open_ns} is outside every trading day"
        )
    return session


# ---------------------------------------------------------------- kill switches


def is_losing_trade(trade: Trade, cfg: KillSwitchesConfig) -> bool:
    """A closed trade with net P&L below minus the losing-trade tolerance (Glossary)."""
    return trade.net < -cfg.losing_trade_tolerance_usd


def _check_closed_trade(trade: Trade, fill: Fill) -> None:
    if trade.shadow:
        raise ValueError("a Shadow_Trade never reaches the Risk_Manager (Req 19.9)")
    if trade.exits[-1] != fill:
        raise ValueError(f"trade_closed must end with the fill of {fill.client_id!r}")


def on_fill(
    rs: RiskState,
    rf: RiskFill,
    trade_closed: Trade | None,
    cfg: KillSwitchesConfig,
    cal: SessionCalendar,
) -> RiskState:
    """Count one fill and start the Lockouts it triggers (Req 16.1-16.5).

    ``trade_closed`` is the closed Trade when ``rf`` is its last exit, else
    ``None``. Raises ``ValueError`` for a fill outside every trading day, a
    fill from an earlier session, a Shadow_Trade, or a ``trade_closed`` that
    does not end with this fill.
    """
    fill = rf.fill
    session = _fill_session(fill, cal)
    rs = roll_session(rs, session)
    t = fill.bar_open_ns
    net = rs.session_net + rf.net
    entries, losers, streak = rs.filled_entries, rs.session_losers, rs.loss_streak
    started: list[Lockout] = []

    if rf.role == "entry":
        if trade_closed is not None:
            raise ValueError("an entry fill cannot close a trade")
        if fill.client_id not in entries:
            entries = (*entries, fill.client_id)
            if cfg.max_trades.enabled and len(entries) == cfg.max_trades.max:
                started.append(Lockout("max_trades", t, session))
    elif trade_closed is not None:
        _check_closed_trade(trade_closed, fill)
        if is_losing_trade(trade_closed, cfg):
            losers += 1
            streak += 1
            if cfg.max_losers.enabled and losers == cfg.max_losers.limit:
                started.append(Lockout("max_losers", t, session))
            if cfg.consecutive_losers.enabled and streak == cfg.consecutive_losers.limit:
                started.append(Lockout("consecutive_losers", t, next_weekday_session(session, cal)))
                streak = 0
        else:
            streak = 0
        if cfg.red_day.enabled and net < cfg.red_day.threshold_usd:
            started.append(Lockout("red_day", t, session))

    cap = cfg.daily_profit_cap
    if rf.role != "entry" and cap.enabled and rs.session_net < cap.cap_usd <= net:
        started.append(Lockout("daily_profit_cap", t, session))

    return replace(
        rs,
        filled_entries=entries,
        session_losers=losers,
        loss_streak=streak,
        session_net=net,
        lockouts=(*rs.lockouts, *started),
    )


def loss_stop_limit(risk_usd: Money, cfg: KillSwitchesConfig) -> Money:
    """``-multiple x risk_usd``: -$750 with the defaults (Req 16.6)."""
    _require_money("risk_usd", risk_usd)
    if risk_usd <= 0:
        raise ValueError(f"risk_usd must be above 0, got {risk_usd}")
    return -(Decimal(repr(cfg.internal_daily_loss_stop.multiple)) * risk_usd)


def on_minute_close(
    rs: RiskState,
    t: Instant,
    positions: Mapping[str, int],
    unrealized_worst: Money,
    risk_usd: Money,
    cfg: KillSwitchesConfig,
    cal: SessionCalendar,
) -> tuple[RiskState, tuple[CloseIntent, ...]]:
    """Check the internal daily loss stop at the close ``t`` of a 1-minute bar (Req 16.6).

    ``positions`` holds the signed open contracts (long above 0) of each
    configured instrument; ``unrealized_worst`` is their unrealized P&L at the
    bar's worst prices (the low for a long, the high for a short), and
    ``risk_usd`` is ``sizing.risk_usd``. When the stop fires, the result holds
    one market close per open position, by instrument, and a Lockout from
    ``t``. A close outside every trading day changes nothing.
    """
    _require_int("t", t)
    _require_positions("positions", positions)
    _require_money("unrealized_worst", unrealized_worst)
    limit = loss_stop_limit(risk_usd, cfg)
    session = cal.trading_day_of(t)
    if session is None:
        return rs, ()
    rs = roll_session(rs, session)
    if not cfg.internal_daily_loss_stop.enabled or rs.loss_stop is not None:
        return rs, ()
    measured = rs.session_net + unrealized_worst
    if measured > limit:
        return rs, ()
    closes = tuple(
        CloseIntent(instrument, "sell" if qty > 0 else "buy", abs(qty))
        for instrument, qty in sorted(positions.items())
        if qty != 0
    )
    lockout = Lockout("internal_daily_loss_stop", t, session)
    rs = replace(rs, loss_stop=LossStopHit(t, measured, limit), lockouts=(*rs.lockouts, lockout))
    return rs, closes


# ---------------------------------------------------------------- lockout queries


def active_lockouts(rs: RiskState, session: date) -> tuple[Lockout, ...]:
    """The started Lockouts that cover ``session``, in start order (Req 16.9)."""
    return tuple(lo for lo in rs.lockouts if lo.last_session >= session)


def entries_blocked(rs: RiskState, session: date) -> bool:
    """Whether a Lockout withholds new entry orders in ``session`` (Req 16.8)."""
    return bool(active_lockouts(rs, session))


def entries_to_cancel(rs: RiskState, session: date, working: Iterable[Order]) -> tuple[Order, ...]:
    """The resting entry orders a Lockout cancels; stop and target orders stay (Req 16.8)."""
    if not entries_blocked(rs, session):
        return ()
    return tuple(order for order in working if order.role == "entry")


def loss_stop_hit(rs: RiskState, session: date) -> LossStopHit | None:
    """The internal daily loss stop firing in ``session``, if it fired."""
    if rs.session is None or rs.session < session:
        return None
    if rs.session > session:
        raise ValueError(f"session {session} is before the Risk_Manager's session {rs.session}")
    return rs.loss_stop


# ---------------------------------------------------------------- pre-check


def micro_equivalents(instrument: str, qty: int) -> int:
    """Micro_Equivalents of ``qty`` contracts of ``instrument`` (Glossary)."""
    per_contract = MICRO_EQUIVALENTS.get(instrument)
    if per_contract is None:
        raise ValueError(
            f"instrument {instrument!r} has no Micro_Equivalent size; "
            f"known: {sorted(MICRO_EQUIVALENTS)}"
        )
    return per_contract * abs(qty)


def is_reducing(order: Order, positions: Mapping[str, int]) -> bool:
    """Whether ``order`` only reduces or closes the held position in its instrument."""
    held = positions.get(order.instrument, 0)
    if held == 0:
        return False
    closing_side: Side = "sell" if held > 0 else "buy"
    return order.side == closing_side and order.qty <= abs(held)


def precheck(
    rs: RiskState,
    order: Order,
    session: date,
    positions: Mapping[str, int],
    working_entries: Mapping[str, int],
    position_cap: int | None,
) -> Order | Withheld:
    """Check ``order`` before it reaches the Broker_Adapter (Req 24.23-24.25).

    ``positions`` holds signed open contracts per configured instrument and
    ``working_entries`` the unfilled contracts of working entry orders, not
    counting ``order``. ``position_cap`` is the account cap in
    Micro_Equivalents, ``None`` when that rule is disabled.

    An order that only reduces or closes a position comes back unchanged. Any
    other order fails ``position_cap`` when open plus working-entry plus its own
    Micro_Equivalents, counted without regard to direction, exceed the cap, and
    fails ``internal_daily_loss_stop`` after that stop fired in ``session``.
    The order comes back unchanged when no check fails, else :class:`Withheld`.
    """
    _require_date("session", session)
    _require_positions("positions", positions)
    _require_positions("working_entries", working_entries)
    for instrument, qty in working_entries.items():
        if qty < 0:
            raise ValueError(f"working_entries[{instrument!r}] must not be negative, got {qty}")
    if position_cap is not None:
        _require_int("position_cap", position_cap, minimum=1)
    if is_reducing(order, positions):
        return order

    failures: list[FailedCheck] = []
    if position_cap is not None:
        total = (
            sum(micro_equivalents(i, q) for i, q in positions.items())
            + sum(micro_equivalents(i, q) for i, q in working_entries.items())
            + micro_equivalents(order.instrument, order.qty)
        )
        if total > position_cap:
            failures.append(FailedCheck("position_cap", total, position_cap))
    hit = loss_stop_hit(rs, session)
    if hit is not None:
        failures.append(FailedCheck("internal_daily_loss_stop", hit.measured, hit.limit))
    return Withheld(order, tuple(failures)) if failures else order
