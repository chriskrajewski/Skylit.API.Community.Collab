"""Named paper accounts of ``fse run``: state, codec and the pure rule engine.

A paper account sits between its plugins' own ``risk`` caps and their
``SimBook``s. Several plugins may share one account: balance, P&L, every rule
and the position cap are evaluated on the sum over the account's plugins.

:class:`PaperAccount` is pure (no I/O, no clock): the host hands it closed
trades (:meth:`~PaperAccount.on_closed`), per-bar open-P&L sums computed from
the member books (:meth:`~PaperAccount.on_bar`), the Globex day boundaries
(:meth:`~PaperAccount.roll`, :meth:`~PaperAccount.open_day`) and the flat
deadline (:meth:`~PaperAccount.flat_by`). Each returns the
:data:`AccountAction` list the host turns into log lines and flattens.

Status transitions (one ``StatusChanged`` and one ``FlattenMembers`` per rule
breach, however many bars follow past the limit):

=============== ========= ===================== ============ ============
Status before   MLL check DLL check             On MLL hit   On DLL hit
=============== ========= ===================== ============ ============
``active``      yes       yes, with a day set   ``breached`` ``flat_locked``
``passed``      yes       yes, with a day set   ``breached`` ``flat_locked``
``flat_locked`` yes       no                    ``breached`` --
``breached``    no        no                    --           --
=============== ========= ===================== ============ ============

Money is exact ``Decimal``. ``account.json`` stores the state through
:data:`ACCOUNT_CODEC` (money as ``{"$decimal": "50000.00"}``).
"""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, time
from decimal import Decimal
from pathlib import Path
from typing import Final, Literal

from fse.config.schema.account import PaperAccountRules, in_no_trade_window
from fse.engine.state import ENGINE_CODEC, StateCodec, dataclass_registry
from fse.engine.types import Money, Ticks, Trade
from fse.logio import canonical_json
from fse.logio.canonical_json import JsonValue
from fse.sim.account import MICRO_EQUIVALENTS, consistency_profit_target, mll_floor_for
from fse.timekit import Instant

__all__ = [
    "ACCOUNTS_DIR_NAME",
    "ACCOUNT_CODEC",
    "ACCOUNT_FLATTEN_REASONS",
    "ACCOUNT_REASONS",
    "ACCOUNT_STATE_FILE_NAME",
    "ACCOUNT_STATE_VERSION",
    "FORWARD_TEST_START",
    "AccountAction",
    "AccountStatus",
    "FlattenMembers",
    "LogLevel",
    "Logged",
    "PaperAccount",
    "PaperAccountState",
    "StatusChanged",
    "account_dir",
    "mll_fingerprint",
    "rules_fingerprint",
    "trade_id",
]

FORWARD_TEST_START: Final = date(2000, 1, 1)
"""The first Globex day counted for the paper forward test P&L; set it to your own start day."""

ACCOUNTS_DIR_NAME: Final = "accounts"
ACCOUNT_STATE_FILE_NAME: Final = "account.json"
ACCOUNT_STATE_VERSION: Final = 1

ACCOUNT_REASONS: Final[frozenset[str]] = frozenset(
    {
        "account_breached",
        "account_daily_loss",
        "account_flat_by",
        "account_state_write_failed",
        "account_no_trade_window",
        "account_max_position",
    }
)
"""The ``intent_rejected`` reasons of an entry refused by its account."""

ACCOUNT_FLATTEN_REASONS: Final[frozenset[str]] = frozenset(
    {"account_mll", "account_dll", "account_flat_by"}
)
"""The ``Flatten.reason`` of the host flattens an account orders."""

REASON_PASSED: Final = "account_profit_target"
REASON_DAY_END: Final = "account_day_end"
REASON_RESTORE_FAILED: Final = "restore_failed"

_ZERO: Final = Decimal("0.00")
_FINGERPRINT_CHARS: Final = 16

type AccountStatus = Literal["active", "passed", "breached", "flat_locked"]
type LogLevel = Literal["info", "warning", "error"]


def account_dir(state_root: Path, account_id: str) -> Path:
    """``<live-state>/accounts/<id>``: a paper account's state directory."""
    return Path(state_root) / ACCOUNTS_DIR_NAME / account_id


def trade_id(trade: Trade) -> str:
    """A closed trade's id: ``<plugin>:<tag>:entry@<entry bar_open_ns>``."""
    return f"{trade.entry_fill.client_id}@{trade.entry_fill.bar_open_ns}"


def _digest(obj: object) -> str:
    text = canonical_json.dumps(obj)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:_FINGERPRINT_CHARS]


def rules_fingerprint(rules: PaperAccountRules) -> str:
    """sha256 of the canonical JSON of every rule, first 16 hex digits."""
    return _digest(rules.model_dump(mode="json"))


def mll_fingerprint(rules: PaperAccountRules) -> str:
    """:func:`rules_fingerprint` over ``maximum_loss_limit`` only."""
    return _digest(rules.maximum_loss_limit.model_dump(mode="json"))


# ---------------------------------------------------------------- state


@dataclass(frozen=True, slots=True)
class PaperAccountState:
    """Everything a paper account persists in ``account.json`` (``account``)."""

    id: str
    plugins: tuple[str, ...]
    """Enabled plugins on the account at the last start."""
    starting_balance: Money
    """Fixed at creation; changed only by ``fse account reset``."""
    balance: Money
    """``starting_balance`` + cumulative realized net."""
    realized: Money
    unrealized: Money
    """Open P&L at the last mark: realized partials plus the open qty at the mark."""
    marks: Mapping[str, Ticks]
    """Last bar close per instrument."""
    mark_ns: Instant | None
    """Close of the newest marked bar."""
    last_bar_open: Mapping[str, Instant]
    """Newest bar evaluated per instrument (replay dedupe)."""
    open_pnl_day_start: Money
    """Open P&L at the day's start of the trades held over the break."""
    hwm: Money
    """Intraday high-water equity (best intrabar price); starts at ``starting_balance``."""
    eod_high: Money
    """Highest equity at a day end; starts at ``starting_balance``."""
    mll_floor: Money | None
    """``mll_floor_for(rules, start, eod_high, hwm)``; ``None`` while the MLL is disabled."""
    day: date | None
    """The Globex day in progress; ``None`` in the weekend gap or before the first day."""
    day_net: Money
    day_open_pnl_worst: Money
    trading_days: int
    traded_today: bool
    best_day: Money | None
    profit_target: Money
    """After the consistency raise."""
    status: AccountStatus
    passed_at: Instant | None
    breach_reason: str | None
    breached_at: Instant | None
    flat_by_done: date | None
    """The Globex day whose ``flat_by`` flatten already ran."""
    applied: frozenset[str]
    """Trade ids applied on the current day."""
    applied_prev: frozenset[str]
    """Trade ids applied on the previous day (rotated by ``roll``)."""
    fingerprint: str
    mll_fingerprint: str
    rules_changed_at: Instant | None


# ---------------------------------------------------------------- actions


@dataclass(frozen=True, slots=True)
class StatusChanged:
    """One status transition: one ``account_status`` host-log line."""

    old: AccountStatus
    new: AccountStatus
    reason: str


@dataclass(frozen=True, slots=True)
class FlattenMembers:
    """Flatten every member plugin with ``Flatten(None, reason, "next_open")``."""

    reason: str


@dataclass(frozen=True, slots=True)
class Logged:
    """A host-log line of kind ``kind``."""

    kind: str
    level: LogLevel
    detail: Mapping[str, JsonValue]


type AccountAction = StatusChanged | FlattenMembers | Logged


ACCOUNT_CODEC: Final[StateCodec] = ENGINE_CODEC.extended(dataclass_registry(sys.modules[__name__]))
"""The engine state types plus this module's dataclasses."""


def _money(value: Money | None) -> JsonValue:
    return None if value is None else str(value)


# ---------------------------------------------------------------- the rule engine


class PaperAccount:
    """One paper account's rules and state; pure, every change returns its actions."""

    __slots__ = ("_rules", "_state")

    def __init__(self, state: PaperAccountState, rules: PaperAccountRules) -> None:
        self._state = state
        self._rules = rules

    @property
    def state(self) -> PaperAccountState:
        return self._state

    @property
    def rules(self) -> PaperAccountRules:
        return self._rules

    @property
    def id(self) -> str:
        return self._state.id

    # ------------------------------------------------------------ construction

    @classmethod
    def fresh(
        cls, account_id: str, rules: PaperAccountRules, plugins: Iterable[str]
    ) -> PaperAccount:
        """A new account at the rules' starting balance, no Globex day yet."""
        start = rules.starting_balance.value
        state = PaperAccountState(
            id=account_id,
            plugins=tuple(plugins),
            starting_balance=start,
            balance=start,
            realized=_ZERO,
            unrealized=_ZERO,
            marks={},
            mark_ns=None,
            last_bar_open={},
            open_pnl_day_start=_ZERO,
            hwm=start,
            eod_high=start,
            mll_floor=mll_floor_for(rules.maximum_loss_limit, start, start, start),
            day=None,
            day_net=_ZERO,
            day_open_pnl_worst=_ZERO,
            trading_days=0,
            traded_today=False,
            best_day=None,
            profit_target=rules.profit_target.value,
            status="active",
            passed_at=None,
            breach_reason=None,
            breached_at=None,
            flat_by_done=None,
            applied=frozenset(),
            applied_prev=frozenset(),
            fingerprint=rules_fingerprint(rules),
            mll_fingerprint=mll_fingerprint(rules),
            rules_changed_at=None,
        )
        return cls(state, rules)

    @classmethod
    def restore_failed(
        cls, account_id: str, rules: PaperAccountRules, plugins: Iterable[str], now: Instant
    ) -> PaperAccount:
        """A fresh account that starts ``breached`` (``restore_failed``): fail closed."""
        acct = cls.fresh(account_id, rules, plugins)
        acct._state = replace(
            acct._state,
            status="breached",
            breach_reason=REASON_RESTORE_FAILED,
            breached_at=now,
        )
        return acct

    @classmethod
    def restore(
        cls,
        state: PaperAccountState,
        rules: PaperAccountRules,
        plugins: Iterable[str],
        now: Instant,
    ) -> tuple[PaperAccount, list[AccountAction]]:
        """The persisted ``state`` under the current ``rules``.

        Balance, realized, peaks, status and day fields are kept. Changed rules
        log ``account_rules_changed``; a changed MLL recomputes the floor from
        the peaks (old and new floor in the line). A different starting balance
        logs ``account_start_changed`` and takes effect only through a reset.
        """
        actions: list[AccountAction] = []
        s = replace(state, plugins=tuple(plugins))
        fp, mfp = rules_fingerprint(rules), mll_fingerprint(rules)
        if s.fingerprint != fp or s.mll_fingerprint != mfp:
            detail: dict[str, JsonValue] = {
                "account": s.id,
                "old_fingerprint": s.fingerprint,
                "new_fingerprint": fp,
            }
            s = replace(
                s,
                fingerprint=fp,
                rules_changed_at=now,
                profit_target=_profit_target(rules, s.best_day),
            )
            if state.mll_fingerprint != mfp:
                floor = mll_floor_for(
                    rules.maximum_loss_limit, s.starting_balance, s.eod_high, s.hwm
                )
                detail["old_floor"] = _money(s.mll_floor)
                detail["new_floor"] = _money(floor)
                s = replace(s, mll_floor=floor, mll_fingerprint=mfp)
            actions.append(Logged("account_rules_changed", "warning", detail))
            configured = rules.starting_balance.value
            if configured != s.starting_balance:
                actions.append(
                    Logged(
                        "account_start_changed",
                        "warning",
                        {
                            "account": s.id,
                            "starting_balance": str(s.starting_balance),
                            "configured": str(configured),
                            "message": "takes effect on fse account reset",
                        },
                    )
                )
        return cls(s, rules), actions

    # ------------------------------------------------------------ events

    def on_closed(self, trade: Trade, now: Instant) -> list[AccountAction]:
        """Book one closed trade of a member plugin (deduped by :func:`trade_id`).

        With a Globex day in progress the net counts toward the day; outside a
        day (weekend gap, before the first day) it books to the ledger only.
        """
        del now  # part of the event signature; a close changes no status
        s = self._state
        tid = trade_id(trade)
        if tid in s.applied or tid in s.applied_prev:
            return []
        net = trade.net
        s = replace(
            s, realized=s.realized + net, balance=s.balance + net, applied=s.applied | {tid}
        )
        if s.day is not None:
            self._state = replace(s, day_net=s.day_net + net, traded_today=True)
            return []
        self._state = s
        return [
            Logged(
                "account_close_outside_day",
                "info",
                {"account": s.id, "trade_id": tid, "net": str(net)},
            )
        ]

    def revalue(self, at_close: Money) -> None:
        """Set the open P&L at the last marks (after a close changed the member books)."""
        self._state = replace(self._state, unrealized=at_close)

    def on_bar(
        self,
        instrument: str,
        bar_open_ns: Instant,
        worst: Money,
        best: Money,
        at_close: Money,
        now: Instant,
        *,
        close_t: Ticks,
        close_ns: Instant,
    ) -> list[AccountAction]:
        """Evaluate one bar of ``instrument``; ``worst``/``best``/``at_close`` are open P&L sums.

        Each sum is over every member book's open trades (realized partials plus
        the open qty), valued at this bar's worst/best price/close for trades in
        ``instrument`` and at the last mark for the others. A bar at or before the
        last one evaluated for ``instrument`` is ignored (replay dedupe).
        """
        s = self._state
        last = s.last_bar_open.get(instrument)
        if last is not None and bar_open_ns <= last:
            return []
        actions: list[AccountAction] = []
        rules = self._rules
        dll = rules.daily_loss_limit
        if (
            s.status in ("active", "passed", "flat_locked")
            and s.mll_floor is not None
            and s.balance + worst <= s.mll_floor
        ):
            actions += self._transition("breached", "account_mll", now)
        elif (
            s.status in ("active", "passed")
            and s.day is not None
            and dll.enabled
            and s.day_net + (worst - s.open_pnl_day_start) <= -dll.value
        ):
            actions += self._transition("flat_locked", "account_dll", now)
        s = self._state
        hwm, floor = s.hwm, s.mll_floor
        if not any(isinstance(a, StatusChanged) and a.new == "breached" for a in actions):
            # Breach is judged before the peak moves: a bar never meets a floor
            # raised by its own high.
            hwm = max(s.hwm, s.balance + best)
            floor = mll_floor_for(rules.maximum_loss_limit, s.starting_balance, s.eod_high, hwm)
        self._state = replace(
            s,
            hwm=hwm,
            mll_floor=floor,
            marks={**s.marks, instrument: close_t},
            mark_ns=close_ns if s.mark_ns is None else max(s.mark_ns, close_ns),
            last_bar_open={**s.last_bar_open, instrument: bar_open_ns},
            unrealized=at_close,
            day_open_pnl_worst=min(s.day_open_pnl_worst, worst),
        )
        return actions

    def flat_by(self, day: date) -> list[AccountAction]:
        """The flat deadline of Globex day ``day`` was reached: flatten once per day."""
        s = self._state
        if not self._rules.flat_deadline_enabled or s.flat_by_done == day:
            return []
        self._state = replace(s, flat_by_done=day)
        return [FlattenMembers("account_flat_by")]

    def roll(self, new_day: date | None, now: Instant) -> list[AccountAction]:
        """Close ``state.day`` and open ``new_day``; a no-op unless a different day is open.

        Returns only ``StatusChanged`` actions (pass, DLL unlock); never flattens.
        """
        s = self._state
        if s.day is None or s.day == new_day:
            return []
        rules = self._rules
        eod_high = max(s.eod_high, s.balance + s.unrealized)
        floor = mll_floor_for(rules.maximum_loss_limit, s.starting_balance, eod_high, s.hwm)
        best_day, trading_days = s.best_day, s.trading_days
        if s.traded_today:
            best_day = s.day_net if best_day is None else max(best_day, s.day_net)
            trading_days += 1
        profit_target = _profit_target(rules, best_day)
        status, passed_at = s.status, s.passed_at
        reason = REASON_DAY_END
        if (
            passed_at is None
            and status != "breached"
            and rules.profit_target.enabled
            and s.balance >= s.starting_balance + profit_target
            and trading_days >= (rules.min_trading_days or 0)
        ):
            passed_at = now
            status = "passed"
            reason = REASON_PASSED
        if status == "flat_locked":
            status = "passed" if passed_at is not None else "active"
        actions: list[AccountAction] = []
        if status != s.status:
            actions.append(StatusChanged(s.status, status, reason))
        self._state = replace(
            s,
            eod_high=eod_high,
            mll_floor=floor,
            best_day=best_day,
            trading_days=trading_days,
            profit_target=profit_target,
            status=status,
            passed_at=passed_at,
            day=new_day,
            day_net=_ZERO,
            traded_today=False,
            flat_by_done=None,
            open_pnl_day_start=s.unrealized,
            day_open_pnl_worst=s.unrealized,
            applied_prev=s.applied,
            applied=frozenset(),
        )
        return actions

    def open_day(self, day: date) -> None:
        """Open ``day`` when no day is open (weekend gap, fresh account); else nothing."""
        s = self._state
        if s.day is not None:
            return
        self._state = replace(
            s,
            day=day,
            day_net=_ZERO,
            traded_today=False,
            flat_by_done=None,
            open_pnl_day_start=s.unrealized,
            day_open_pnl_worst=s.unrealized,
        )

    # ------------------------------------------------------------ entry checks

    def entry_blocks(self) -> tuple[str, ...]:
        """The gate-level entry blocks of the account's status (before the plugin caps)."""
        s = self._state
        out: list[str] = []
        if s.status == "breached":
            out.append("account_breached")
        elif s.status == "flat_locked":
            out.append("account_daily_loss")
        if s.flat_by_done is not None and s.flat_by_done == s.day:
            out.append("account_flat_by")
        return tuple(out)

    def check_entry(self, instrument: str, qty: int, exposure: int, ny_time: time) -> str | None:
        """The account refusal of an entry, after the plugin caps; ``None`` to accept.

        ``exposure`` is the account's Micro_Equivalents now (open plus working
        entries over every member book); the order adds ``qty`` contracts.
        """
        if in_no_trade_window(self._rules.no_trade_windows, ny_time):
            return "account_no_trade_window"
        cap = self._rules.position_cap
        if cap.enabled and exposure + MICRO_EQUIVALENTS[instrument] * qty > cap.micro_equivalents:
            return "account_max_position"
        return None

    # ------------------------------------------------------------ view

    def view(
        self, *, exposure_micros: int = 0, flat_by_ns: Instant | None = None
    ) -> dict[str, JsonValue]:
        """The derived, non-authoritative numbers: equity, drawdown, distance to each limit."""
        s, rules = self._state, self._rules
        equity = s.balance + s.unrealized
        dll, target, cap = rules.daily_loss_limit, rules.profit_target, rules.position_cap
        distance: dict[str, JsonValue] = {
            "mll": None if s.mll_floor is None else str(equity - s.mll_floor),
            "dll": (
                str(dll.value + s.day_net + (s.unrealized - s.open_pnl_day_start))
                if dll.enabled
                else None
            ),
            "profit_target": (
                str(s.starting_balance + s.profit_target - s.balance) if target.enabled else None
            ),
            "position": cap.micro_equivalents - exposure_micros if cap.enabled else None,
            "flat_by_ns": flat_by_ns if rules.flat_deadline_enabled else None,
        }
        return {
            "equity": str(equity),
            "drawdown": str(max(_ZERO, s.hwm - equity)),
            "distance": distance,
            "blocks": list(self.entry_blocks()),
            "exposure_micros": exposure_micros,
            "disabled_rules": list(rules.disabled_rules()),
        }

    # ------------------------------------------------------------ internals

    def _transition(self, new: AccountStatus, reason: str, now: Instant) -> list[AccountAction]:
        """Set the status; exactly ``[StatusChanged(old, new, reason), FlattenMembers(reason)]``."""
        s = self._state
        old = s.status
        if new == "breached":
            self._state = replace(s, status=new, breach_reason=reason, breached_at=now)
        else:
            self._state = replace(s, status=new)
        return [StatusChanged(old, new, reason), FlattenMembers(reason)]


def _profit_target(rules: PaperAccountRules, best_day: Money | None) -> Money:
    configured = rules.profit_target.value
    if not rules.consistency_target.enabled:
        return configured
    return consistency_profit_target(configured, best_day, rules.consistency_target.pct)
