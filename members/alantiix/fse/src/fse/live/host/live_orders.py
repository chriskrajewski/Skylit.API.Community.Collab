"""Opt-in ProjectX orders for one host-brokered plugin under ``fse run`` (design D2, D4).

This is the only module under ``fse.live.host`` (or ``fse.commands.run``)
that imports ``fse.projectx.broker``, ``fse.live.broker_safety`` or
``resolve_order_mode``; ``host.py`` and ``run.py`` reach them only through the
names re-exported here (``tests/unit/test_runner_paper_only.py``).

**Gates** (:func:`resolve_plugin_order_mode`). Paper is the default. The one
plugin whose ``execution.order_mode`` is not ``paper`` sends orders only when
the start command has :data:`PLUGIN_ORDERS_FLAG` **and** the environment has
``FSE_RUN_PLUGIN_ORDERS=enabled``, both checked before any Broker_Adapter is
built; then the existing practice/combine rules of
:func:`~fse.live.order_router.resolve_order_mode` apply unchanged (combine
also needs :data:`LIVE_ORDERS_FLAG`). Every failed condition is named, by
flag or variable only, on the plugin's first Finding_Card; the run is paper.
The AWS runner service's fixed ``fse run`` command never has the flag.

**Wiring.** The plugin keeps its paper book (:class:`BrokeredPaperBroker`);
that book is also the expected book a per-day
:class:`~fse.live.broker_safety.BrokerSafety` reconciles against
(:class:`OrderLink`). Every other plugin keeps the plain paper broker.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any, Final, Literal, Protocol

from fse.clock import Clock
from fse.config.schema.live import LiveConfig
from fse.engine.types import Bar, Order, SetupKey, Side
from fse.live.bar_feed import FeedInstrument
from fse.live.broker_safety import (
    _LOOKUP_MARGIN_NS,
    BrokerSafety,
    ClientIds,
    Expectation,
    JournalError,
    OrderJournal,
    _Pending,
    new_run_uuid8,
)
from fse.live.host.broker import (
    REASON_RE,
    AccountCheck,
    PluginPaperBroker,
    client_id,
    tag_of,
)
from fse.live.host.config import RunnerConfig
from fse.live.host.dayclock import GlobexDay
from fse.live.host.gate import plugin_dir
from fse.live.order_router import (
    BROKER_MODES,
    COMBINE,
    LIVE_ORDERS_FLAG,
    PAPER,
    PRACTICE,
    BrokerAction,
    CancelAction,
    ExitAction,
    ModeDecision,
    ModifyAction,
    PlaceAction,
    resolve_order_mode,
)
from fse.live.plugins.api import (
    Cancel,
    EnterMarket,
    EnterResting,
    Flatten,
    Intent,
    ModifyStop,
    OrderUpdate,
    Quote,
)
from fse.live.state_store import BlockStore
from fse.logio import LogWriter
from fse.logio.canonical_json import JsonValue
from fse.projectx.broker import (
    ACCOUNT_VARIABLES,
    BrokerAdapter,
    BrokerModeError,
    EntryIntent,
    OrderRequest,
)
from fse.projectx.session import ProjectXSession
from fse.secrets.env import EnvView
from fse.sim.fills import OpenTrade
from fse.timekit import NS_PER_SECOND, Instant

__all__ = [
    "ACCOUNT_VARIABLES",
    "BROKER_DRAIN_TIMEOUT_NS",
    "BROKER_MODES",
    "LINK_BLOCK_KINDS",
    "LIVE_ORDERS_FLAG",
    "PLUGIN_ORDERS_ENV",
    "PLUGIN_ORDERS_FLAG",
    "BrokerEvent",
    "BrokeredPaperBroker",
    "JournalError",
    "ModeDecision",
    "OrderLink",
    "PluginOrderSetup",
    "SafetyFactory",
    "make_safety_factory",
    "new_run_uuid8",
    "resolve_plugin_order_mode",
]

PLUGIN_ORDERS_FLAG: Final = "--plugin-orders"
"""The ``fse run`` flag without which no plugin sends a broker order."""
PLUGIN_ORDERS_ENV: Final = "FSE_RUN_PLUGIN_ORDERS"
"""The environment variable that must equal :data:`PLUGIN_ORDERS_ENABLED` as well."""
PLUGIN_ORDERS_ENABLED: Final = "enabled"
BROKER_DRAIN_TIMEOUT_NS: Final = 10 * NS_PER_SECOND
"""How long a stop waits for queued exits (each exit costs two to four HTTP calls)."""
LINK_BLOCK_KINDS: Final[frozenset[str]] = frozenset(
    {"broker_not_synced", "broker_outage", "contract_mismatch", "broker_task_stopped"}
)
"""Transient broker blocks: they refuse new entries but never cancel working ones."""

type SafetyFactory = Callable[[GlobexDay, Instant], BrokerSafety]
"""Builds one Globex day's BrokerSafety; the instant is its order-search start."""


class ContractSource(Protocol):
    def contracts(self, day: date) -> tuple[tuple[FeedInstrument, ...], list[str]]: ...


@dataclass(frozen=True, slots=True)
class PluginOrderSetup:
    """The order-mode decision for the one non-paper plugin, and its broker objects."""

    plugin_id: str
    decision: ModeDecision
    adapter: BrokerAdapter | None = None
    """Bound to the mode's account; only for a broker decision."""
    journal: OrderJournal | None = None
    ids: ClientIds | None = None
    blocks: BlockStore | None = None


def _local_failures(
    requested: str, env: EnvView, *, plugin_orders: bool, live_orders: bool
) -> list[str]:
    """Every condition checkable from the flags and the environment alone (design D2.1)."""
    out: list[str] = []
    if not plugin_orders:
        out.append(f"the start command has no {PLUGIN_ORDERS_FLAG} flag")
    if env.get(PLUGIN_ORDERS_ENV) != PLUGIN_ORDERS_ENABLED:
        out.append(f"{PLUGIN_ORDERS_ENV} is not set to {PLUGIN_ORDERS_ENABLED}")
    if requested == PRACTICE:
        name = ACCOUNT_VARIABLES[PRACTICE]
        practice = env.get(name)
        if practice is None:
            out.append(f"{name} is unset")
        elif practice == env.get(ACCOUNT_VARIABLES[COMBINE]):
            out.append(f"{name} equals {ACCOUNT_VARIABLES[COMBINE]}")
    elif requested == COMBINE:
        name = ACCOUNT_VARIABLES[COMBINE]
        if env.get(name) is None:
            out.append(f"{name} is unset")
        if not live_orders:
            out.append(f"the start command has no {LIVE_ORDERS_FLAG} flag")
    return out


async def resolve_plugin_order_mode(
    cfg: RunnerConfig,
    env: EnvView,
    session: ProjectXSession,
    clock: Clock,
    writer: LogWriter,
    state_root: Path,
    *,
    plugin_orders: bool,
    live_orders: bool,
) -> PluginOrderSetup | None:
    """The decision for the enabled non-paper plugin, or ``None`` when there is none.

    No Broker_Adapter is built unless the local opt-in (flag and variable)
    passes. A :class:`JournalError` from the plugin's order journal propagates
    (``fse run`` exits 4); a paper fallback opens no journal.
    """
    entry = next((p for p in cfg.enabled_plugins if p.execution.order_mode != PAPER), None)
    if entry is None:
        return None
    requested = entry.execution.order_mode
    failures = _local_failures(requested, env, plugin_orders=plugin_orders, live_orders=live_orders)
    if failures:
        return PluginOrderSetup(entry.id, ModeDecision(requested, PAPER, tuple(failures)))
    adapter = BrokerAdapter(session, env, clock)
    accounts = await adapter.resolve_accounts()
    decision = resolve_order_mode(requested, env, accounts, live_orders=live_orders)
    if decision.mode not in BROKER_MODES:
        return PluginOrderSetup(entry.id, decision)
    try:
        bound = adapter.for_mode(decision.mode)
        bound.set_caller(entry.id)
    except BrokerModeError as exc:
        return PluginOrderSetup(entry.id, ModeDecision(requested, PAPER, (str(exc),)))
    state = plugin_dir(state_root, entry.id)
    journal = OrderJournal(writer, state)
    return PluginOrderSetup(
        entry.id,
        decision,
        adapter=bound,
        journal=journal,
        ids=ClientIds(writer, state, journal),
        blocks=BlockStore(writer, state),
    )


def make_safety_factory(
    setup: PluginOrderSetup,
    plane: ContractSource,
    clock: Clock,
    entry_gate: Callable[[str], str | None],
    instruments: frozenset[str],
) -> SafetyFactory:
    """One BrokerSafety per Globex day for the plugin (design D7), with ``LiveConfig()``."""
    adapter, journal, ids, blocks = setup.adapter, setup.journal, setup.ids, setup.blocks
    if adapter is None or journal is None or ids is None or blocks is None:
        raise ValueError("a safety factory needs a broker decision's adapter and state")

    def factory(day: GlobexDay, start: Instant) -> BrokerSafety:
        feeds, _problems = plane.contracts(day.day)
        expected: Mapping[str, str | None] = {f.instrument: f.contract_id for f in feeds}
        return BrokerSafety(
            adapter=adapter,
            live=LiveConfig(),
            instruments=sorted(instruments),
            expected_contracts=expected,
            blocks=blocks,
            journal=journal,
            ids=ids,
            clock=clock,
            session_start_ns=start,
            entry_gate=entry_gate,
            exit_read=True,
            adopt_since_ns=day.open_ns,
        )

    return factory


# ---------------------------------------------------------------- the link


type BrokerEventKind = Literal["withdraw", "note", "stale_broker", "restart"]


@dataclass(frozen=True, slots=True)
class BrokerEvent:
    """A broker-task event the host handles in a dispatch step (design D4 host change 7)."""

    plugin_id: str
    kind: BrokerEventKind
    tag: str | None = None
    """``withdraw``: the tag. ``stale_broker``: the instrument."""
    text: str = ""
    """``withdraw``: the cancel reason code. ``note``/``restart``: the detail."""


@dataclass(frozen=True, slots=True)
class _Closing:
    """A close paper has settled while the broker's exit ``exit`` is still on its way."""

    key: SetupKey
    trade: OpenTrade | None
    entry: Order | None
    legs: tuple[Order, ...]
    exit: Order


_LEGS: Final = ("stop", "tp1", "tp2")
WITHHELD: Final = "broker_withheld"
NO_SESSION: Final = "broker_no_session"


def _unpriced(order: Order) -> Order:
    """A tick-bracket leg as the broker places it: no engine price (``kind`` market)."""
    if order.price is None:
        return order
    return replace(order, kind="market", price=None)


class OrderLink:
    """One broker plugin's link from its paper book to a per-day BrokerSafety (design D4).

    It is also the :class:`~fse.live.broker_safety.ExpectedBook` the safety
    reconciles against: the paper book, with the synthetic legs of pending
    market entries and the closes paper settled before the broker did.
    """

    def __init__(
        self,
        setup: PluginOrderSetup,
        book: BrokeredPaperBroker,
        *,
        instruments: frozenset[str],
        factory: SafetyFactory,
        post: Callable[[BrokerEvent], None],
        log: Callable[[Mapping[str, JsonValue]], None],
        clock: Clock,
    ) -> None:
        if setup.journal is None:
            raise ValueError("an OrderLink needs a broker decision's journal")
        self.setup = setup
        self.journal: OrderJournal = setup.journal
        self.book = book
        self.instruments = instruments
        self._factory = factory
        self._post = post
        self._host_log = log
        self._clock = clock
        self._actions: list[BrokerAction] = []
        self._carry: list[BrokerAction] = []
        self._known: dict[str, Order] = {}
        self._pinned: set[str] = set()
        self._market_keys: set[SetupKey] = set()
        self.market_tags: set[str] = set()
        self.exits_open: dict[str, str] = {}
        self._exit_qty: dict[str, int] = {}
        self._closing: dict[str, _Closing] = {}
        self._remainder_seq = 0
        self._cancel_logged: set[str] = set()
        self.safety: BrokerSafety | None = None
        self.day: date | None = None
        self.until_ns: Instant | None = None
        self.task: asyncio.Task[None] | None = None
        self.catching_up = False
        self.stopping = False
        self.restarted_day: date | None = None

    @property
    def plugin_id(self) -> str:
        return self.setup.plugin_id

    # ------------------------------------------------------------ ExpectedBook

    def _view(self, order: Order) -> Order:
        if (
            order.role in _LEGS
            and order.setup_key in self._market_keys
            and order.client_id not in self._pinned
        ):
            return _unpriced(order)
        return order

    def _synthetic_legs(self, key: SetupKey, entry: Order) -> list[Order]:
        sim = self.book.book
        mb = sim.market_brackets.get(key)
        if mb is None:
            return []
        side = _exit_side(mb.direction)
        legs = [
            Order(
                mb.stop_id,
                key,
                entry.instrument,
                side,
                "market",
                entry.qty,
                None,
                entry.placed_at,
                "stop",
            )
        ]
        legs.extend(
            Order(cid, key, entry.instrument, side, "market", qty, None, entry.placed_at, role)
            for role, cid, qty, _ticks in mb.targets
        )
        return legs

    def working_orders(self) -> tuple[Order, ...]:
        hidden = {a.order.client_id for a in self._carry if isinstance(a, ExitAction)}
        out: list[Order] = []
        for order in self.book.book.working_orders():
            out.append(self._view(order))
            if order.role == "entry" and order.setup_key is not None:
                out.extend(self._synthetic_legs(order.setup_key, order))
        for c in self._closing.values():
            if c.entry is not None:
                out.append(c.entry)
            out.extend(self._view(leg) for leg in c.legs)
            out.append(c.exit)
        return tuple(o for o in out if o.client_id not in hidden)

    @property
    def trades(self) -> Mapping[SetupKey, OpenTrade]:
        out = dict(self.book.book.trades)
        for c in self._closing.values():
            if c.trade is not None:
                out[c.key] = c.trade
        return out

    def known_order(self, client_id: str) -> Order | None:
        order = self.book.book.order(client_id)
        if order is None:
            for c in self._closing.values():
                for o in (c.entry, *c.legs, c.exit):
                    if o is not None and o.client_id == client_id:
                        order = o
        if order is None:
            order = self._known.get(client_id)
        return None if order is None else self._view(order)

    # ------------------------------------------------------------ state for the host

    def active(self, now: Instant) -> bool:
        return (
            self.safety is not None
            and self.task is not None
            and not self.task.done()
            and self.until_ns is not None
            and now < self.until_ns
        )

    def entry_blocks(self) -> tuple[str, ...]:
        if self.safety is None or not self.active(self._clock.now()):
            return ("broker_not_synced",)
        return tuple(dict.fromkeys(b.kind for b in self.safety.external_blocks()))

    def broker_positions(self) -> Mapping[str, int] | None:
        return None if self.safety is None else self.safety.broker_positions()

    def recheck(
        self, entry: Order, positions: Mapping[str, int] | None, working: Mapping[str, int]
    ) -> str | None:
        """The plugin's caps on the broker's current state, right before a send."""
        if positions is None:
            return "the broker state has not been read yet"
        limit = self.book.caps.max_contracts
        measured = sum(abs(q) for q in positions.values()) + sum(working.values()) + entry.qty
        if measured > limit:
            return f"max_contracts measured {measured}, limit {limit}"
        if "max_daily_loss" in self.book.cap_blocks():
            return "max_daily_loss"
        # Per-trade stop risk vs remaining daily-loss budget (combine / practice send).
        loss_cap = self.book.caps.max_daily_loss
        if loss_cap is not None and entry.setup_key is not None:
            mb = self.book.book.market_brackets.get(entry.setup_key)
            if mb is not None and mb.stop_ticks > 0:
                risk = self.book._stop_risk_dollars(
                    entry.instrument, mb.stop_ticks, entry.qty
                )
                remaining = loss_cap + self.book.state.day_net
                if risk is not None and risk > remaining:
                    return "max_daily_loss"
        return None

    # ------------------------------------------------------------ logs and events

    def log(self, entry: Mapping[str, JsonValue]) -> None:
        self._host_log({"plugin": self.plugin_id, **entry})

    def _note(self, text: str) -> None:
        self._post(BrokerEvent(self.plugin_id, "note", None, text))

    def _withdraw(self, engine_id: str, reason: str) -> bool:
        known = self._known.get(engine_id)
        if known is None or known.role != "entry" or known.setup_key is None:
            return False
        self._post(BrokerEvent(self.plugin_id, "withdraw", tag_of(known.setup_key), reason))
        return True

    def _skip(self, a: PlaceAction, reason: str) -> None:
        self.log(
            {
                "kind": "broker_skip",
                "level": "warning",
                "reason": reason,
                "engine_id": a.entry.client_id,
            }
        )

    def _final(self, engine_id: str) -> _Closing | None:
        """Drop the open exit and the closing record whose exit is ``engine_id``."""
        for tag, eid in list(self.exits_open.items()):
            if eid == engine_id:
                del self.exits_open[tag]
        self._exit_qty.pop(engine_id, None)
        for tag, c in list(self._closing.items()):
            if c.exit.client_id == engine_id:
                del self._closing[tag]
                return c
        return None

    def safety_log(self, entry: dict[str, JsonValue]) -> None:
        """The BrokerSafety's log sink: the host log, plus the withdraw and drop rules."""
        self._host_log({"kind": "broker", "plugin": self.plugin_id, **entry})
        event, eid = entry.get("event"), entry.get("engine_id")
        if not isinstance(eid, str):
            return
        if event == "broker_skip":
            self._withdraw(eid, WITHHELD)
            exit_order = self._exit_order(eid)
            if exit_order is not None:
                self._final(eid)
                if entry.get("reason") == "no checked contract":
                    self._note(f"exit for {exit_order.instrument} not sent: no contract id")
        elif event == "resubmit_dropped" or (
            event == "order_sent" and entry.get("outcome") not in ("accepted", "no_response")
        ):
            self._final(eid)

    def _exit_order(self, engine_id: str) -> Order | None:
        for c in self._closing.values():
            if c.exit.client_id == engine_id:
                return c.exit
        if engine_id in self._exit_qty:
            return self._known.get(engine_id)
        return None

    # ------------------------------------------------------------ routing hand-over

    def note_place(self, action: PlaceAction, *, market: bool) -> None:
        for o in (action.entry, action.stop, *action.targets):
            self._known[o.client_id] = o
        if market and action.entry.setup_key is not None:
            self._market_keys.add(action.entry.setup_key)
            self.market_tags.add(tag_of(action.entry.setup_key))
        self._actions.append(action)

    def note_exit(self, tag: str | None, action: ExitAction) -> None:
        self._known[action.order.client_id] = action.order
        if tag is not None:
            self.exits_open[tag] = action.order.client_id
            self._exit_qty[action.order.client_id] = action.qty
        self._actions.append(action)

    def retract(self, tag: str) -> bool:
        """Take an unflushed market ``PlaceAction`` of ``tag`` back (never sent)."""
        for a in self._actions:
            if (
                isinstance(a, PlaceAction)
                and a.entry.setup_key is not None
                and tag_of(a.entry.setup_key) == tag
            ):
                self._actions.remove(a)
                return True
        return False

    def next_remainder_id(self) -> str:
        n = self._remainder_seq
        self._remainder_seq += 1
        return f"{self.plugin_id}:*{n}:exit"

    def flush(self, now: Instant) -> None:
        """Hand the routed actions and the book after them to the day's safety."""
        actions, self._actions = self._actions, []
        self._drop_filled()
        safety = self.safety
        if safety is not None and self.active(now):
            safety.submit(now, actions, Expectation.of(self))
            return
        for a in actions:
            if isinstance(a, PlaceAction):
                self._skip(a, "no broker session")
                self._withdraw(a.entry.client_id, NO_SESSION)
            else:
                self._carry.append(a)
                self.log({"kind": "broker_carried", "level": "info", "action": type(a).__name__})

    def _drop_filled(self) -> None:
        safety = self.safety
        if safety is None:
            return
        for eid, qty in list(self._exit_qty.items()):
            if safety.filled(eid) >= qty:
                self._final(eid)
        for c in list(self._closing.values()):
            if safety.filled(c.exit.client_id) >= c.exit.qty:
                self._final(c.exit.client_id)

    def take_unsent(self) -> list[BrokerAction]:
        """At stop: the carried and routed actions without their entries (each logged)."""
        out = list(self._carry)
        self._carry = []
        for a in self._actions:
            if isinstance(a, PlaceAction):
                self._skip(a, "runner stopping")
            else:
                out.append(a)
        self._actions = []
        return out

    def submit_unsent(self, now: Instant) -> int:
        """At stop: give the safety every carried and routed exit, cancel and modify."""
        actions = self.take_unsent()
        if self.safety is not None and actions:
            self.safety.submit(now, actions, Expectation.of(self))
        return len(actions)

    def take_left(self) -> int:
        """At stop, after the drain bound: how many actions were never dispatched."""
        return 0 if self.safety is None else len(self.safety.take_queued())

    def exit_sent_at(self, c: _Closing) -> Instant | None:
        cid = self.journal.client_for_engine(c.exit.client_id)
        return None if cid is None else self.journal.sent_at(cid)

    # ------------------------------------------------------------ days

    def open_day(self, day: GlobexDay, now: Instant, *, force: bool = False) -> BrokerSafety | None:
        """Build the BrokerSafety of ``day`` (idempotent unless ``force``; design D4)."""
        if self.day == day.day and not force:
            return None
        old = self.safety
        kept: dict[str, _Pending] = {}
        counts: dict[str, JsonValue] = {
            "carried_pending": 0,
            "withdrawn": 0,
            "dropped": 0,
            "open_positions": 0,
            "forced": force,
        }

        def bump(name: str) -> None:
            value = counts[name]
            assert isinstance(value, int)
            counts[name] = value + 1

        if old is not None:
            if not force:
                for tag, c in list(self._closing.items()):
                    if old.filled(c.exit.client_id) >= c.exit.qty:
                        self._final(c.exit.client_id)
                    elif c.entry is not None:
                        del self._closing[tag]
                        self.log(
                            {
                                "kind": "broker_rollover",
                                "level": "warning",
                                "tag": tag,
                                "detail": "entry-form close dropped at the day rollover",
                            }
                        )
            else:
                for a in old.take_queued():
                    if isinstance(a, PlaceAction):
                        self._skip(a, "broker task restarted")
                        self._withdraw(a.entry.client_id, WITHHELD)
                        bump("withdrawn")
                    else:
                        self._carry.append(a)
                carried = {a.order.client_id for a in self._carry if isinstance(a, ExitAction)}
                pending = {p.engine_id for p in old.pending.values()}
                for c in self._closing.values():
                    eid = c.exit.client_id
                    if (
                        self.journal.order_for_engine(eid) is not None
                        or eid in pending
                        or eid in carried
                    ):
                        continue
                    cid = self.journal.client_for_engine(eid)
                    states = old.states or {}
                    legs = tuple(leg.client_id for leg in c.legs)
                    inst = c.exit.instrument
                    sent_at = None if cid is None else self.journal.sent_at(cid)
                    if cid is not None and sent_at is not None and inst in states:
                        request = OrderRequest(
                            cid, states[inst].contract_id, c.exit.side, c.exit.qty, "market"
                        )
                        kept[cid] = _Pending(cid, eid, inst, request, sent_at, legs=legs)
                    else:
                        entry_id = None if c.entry is None else c.entry.client_id
                        self._carry.append(
                            ExitAction(
                                c.exit, c.exit.side, c.exit.qty, legs=legs, entry_id=entry_id
                            )
                        )
            for cid, p in old.pending.items():
                if isinstance(p.request, EntryIntent):
                    reason = "broker task restarted" if force else "entry unanswered at rollover"
                    self.log(
                        {
                            "kind": "broker_skip",
                            "level": "warning",
                            "reason": reason,
                            "engine_id": p.engine_id,
                        }
                    )
                    self._withdraw(p.engine_id, WITHHELD)
                    bump("withdrawn")
                elif p.request.kind == "market":
                    kept[cid] = p
                    bump("carried_pending")
                else:
                    self.log(
                        {
                            "kind": "broker_skip",
                            "level": "warning",
                            "reason": "tp2 unanswered at rollover",
                            "engine_id": p.engine_id,
                        }
                    )
                    bump("dropped")
            positions = old.broker_positions() or {}
            counts["open_positions"] = sum(1 for q in positions.values() if q)
            if (
                any(
                    counts[k] for k in ("carried_pending", "withdrawn", "dropped", "open_positions")
                )
                or kept
            ):
                self.log({"kind": "broker_rollover", "level": "warning", **counts})
        start = day.open_ns
        carried_at = [p.sent_at for p in kept.values()]
        carried_at.extend(t for t in map(self.exit_sent_at, self._closing.values()) if t)
        if carried_at:
            start = min(start, min(carried_at) - _LOOKUP_MARGIN_NS)
        new = self._factory(day, start)
        new.bind(self, self._note, self.safety_log, recheck=self.recheck)
        new.pending.update(kept)
        self.safety = new
        self.day = day.day
        self.until_ns = day.rotate_ns
        return new

    def release_carry(self, safety: BrokerSafety, now: Instant) -> None:
        """Send the exits, cancels and modifies held while no safety accepted sends."""
        if not self._carry:
            return
        actions, self._carry = self._carry, []
        safety.submit(now, actions, Expectation.of(self))

    def foreign_check(self, safety: BrokerSafety) -> None:
        """After a day's first sync: foreign orders, and broker-only positions (design D4)."""
        states = safety.states or {}
        if any(
            o.working and self.journal.engine_for_order(o) is None
            for s in states.values()
            for o in s.orders
        ):
            self._note(
                "the account holds orders or positions this plugin did not send; "
                "the account must be dedicated to this plugin"
            )
        positions = safety.broker_positions() or {}
        sim = self.book.book
        for inst in sorted(self.instruments):
            if not positions.get(inst):
                continue
            if sim.positions().get(inst) or sim.working_entry_contracts().get(inst):
                continue
            if any(c.exit.instrument == inst for c in self._closing.values()):
                continue
            if any(isinstance(a, ExitAction) and a.order.instrument == inst for a in self._carry):
                continue
            if any(
                p.instrument == inst and isinstance(p.request, OrderRequest)
                for p in safety.pending.values()
            ):
                continue
            self._post(BrokerEvent(self.plugin_id, "stale_broker", inst))


# ---------------------------------------------------------------- the paper book


class BrokeredPaperBroker(PluginPaperBroker):
    """A plugin paper book whose routed intents also go to ProjectX (design D4).

    With ``link`` unset it behaves exactly like :class:`PluginPaperBroker`.
    """

    __slots__ = ("link",)

    link: OrderLink | None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.link = None

    def route(
        self,
        intent: Intent,
        now: Instant,
        *,
        blocks: Sequence[str] = (),
        quote: Quote | None = None,
        account_check: AccountCheck | None = None,
    ) -> list[OrderUpdate]:
        link = self.link
        if link is None:
            return super().route(
                intent, now, blocks=blocks, quote=quote, account_check=account_check
            )
        if isinstance(intent, EnterMarket | EnterResting):
            if link.stopping:
                return self._reject(intent.tag, "broker_stopping", now)
            if link.catching_up:
                return self._reject(intent.tag, "broker_catch_up", now)
        if isinstance(intent, EnterMarket):
            if len(intent.targets) != 1 or intent.targets[0].qty != intent.qty:
                return self._reject(intent.tag, "broker_one_target", now)
            if intent.reference_t is not None:
                return self._reject(intent.tag, "broker_reference_t", now)
        if isinstance(intent, Cancel) and intent.tag in link.market_tags:
            return self._reject(intent.tag, "broker_entry_sent", now)
        if isinstance(intent, Flatten):
            return self._route_flatten(link, intent, now)
        resting = None
        if isinstance(intent, Cancel):
            resting = self.book.order(client_id(self._id, intent.tag, "entry"))
        updates = super().route(
            intent, now, blocks=blocks, quote=quote, account_check=account_check
        )
        accepted = any(u.status == "accepted" for u in updates)
        if isinstance(intent, EnterMarket) and accepted:
            link.note_place(self._market_place(intent), market=True)
        elif isinstance(intent, EnterResting) and accepted:
            link.note_place(self._resting_place(intent.tag), market=False)
        elif isinstance(intent, Cancel) and resting is not None and resting.setup_key is not None:
            link._actions.append(
                CancelAction(resting.client_id, resting.instrument, "entry", resting.setup_key)
            )
        elif isinstance(intent, ModifyStop) and accepted:
            detail = next((u.detail for u in updates if u.status == "accepted"), None)
            if detail is not None and detail.get("unchanged") is False:
                stop = client_id(self._id, intent.tag, "stop")
                key = self._key(intent.tag)
                if key is not None:
                    link._pinned.add(stop)
                    link._actions.append(
                        ModifyAction(stop, key.instrument, "stop", intent.stop_price)
                    )
        return updates

    def _market_place(self, intent: EnterMarket) -> PlaceAction:
        entry = self.book.order(client_id(self._id, intent.tag, "entry"))
        assert entry is not None
        assert entry.setup_key is not None
        side = _exit_side(entry.setup_key.direction)
        stop = Order(
            client_id(self._id, intent.tag, "stop"),
            entry.setup_key,
            entry.instrument,
            side,
            "market",
            entry.qty,
            None,
            entry.placed_at,
            "stop",
        )
        leg = intent.targets[0]
        tp = Order(
            client_id(self._id, intent.tag, leg.role),
            entry.setup_key,
            entry.instrument,
            side,
            "market",
            leg.qty,
            None,
            entry.placed_at,
            leg.role,
        )
        return PlaceAction(entry, stop, (tp,), stop_ticks=intent.stop_ticks, target_ticks=leg.ticks)

    def _resting_place(self, tag: str) -> PlaceAction:
        entry = self.book.order(client_id(self._id, tag, "entry"))
        stop = self.book.order(client_id(self._id, tag, "stop"))
        assert entry is not None
        assert stop is not None
        targets = tuple(
            o
            for o in (self.book.order(client_id(self._id, tag, r)) for r in ("tp1", "tp2"))
            if o is not None
        )
        return PlaceAction(entry, stop, targets)

    def _legs_of(self, link: OrderLink, tag: str) -> tuple[Order, ...]:
        out: list[Order] = []
        for role in _LEGS:
            cid = client_id(self._id, tag, role)
            order = self.book.order(cid) or link._known.get(cid)
            if order is not None:
                out.append(link._view(order))
        return tuple(out)

    def _route_flatten(self, link: OrderLink, intent: Flatten, now: Instant) -> list[OrderUpdate]:
        if REASON_RE.fullmatch(intent.reason) is None:
            return super().route(intent, now)  # paper rejects it (bad_reason)
        updates: list[OrderUpdate] = []
        tags = (
            [intent.tag]
            if intent.tag is not None
            else list(dict.fromkeys(tag_of(k) for k in self._keys()))
        )
        retracted: set[str] = set()
        for tag in tags:
            if tag in link.market_tags and link.retract(tag):
                updates += self.withdraw(tag, now, intent.reason)
                retracted.add(tag)
        snaps: dict[str, tuple[SetupKey, Side, int, str | None, tuple[Order, ...]]] = {}
        resting: list[CancelAction] = []
        closing: dict[str, _Closing] = {}
        for tag in tags:
            if tag in retracted:
                continue
            key = self._key(tag)
            if key is None:
                continue
            trade = self.book.trades.get(key)
            entry_cid = client_id(self._id, tag, "entry")
            entry = self.book.order(entry_cid)
            in_market = tag in link.market_tags
            qty = (0 if trade is None else trade.open_qty) + (
                entry.qty if in_market and entry is not None else 0
            )
            entry_id = entry_cid if entry_cid in link._known else None
            legs = self._legs_of(link, tag)
            exit_order = Order(
                client_id(self._id, tag, "exit"),
                key,
                key.instrument,
                _exit_side(key.direction),
                "market",
                max(qty, 1),
                None,
                now,
                "exit",
            )
            snaps[tag] = (key, _exit_side(key.direction), qty, entry_id, legs)
            if entry is not None and not in_market and entry.kind != "market":
                resting.append(CancelAction(entry_cid, key.instrument, "entry", key))
            if in_market:
                known_entry = link._known.get(entry_cid, entry)
                updates += self.withdraw(tag, now, intent.reason)
                closing[tag] = _Closing(key, None, known_entry, legs, exit_order)
            elif trade is not None and intent.price == "last_close":
                closing[tag] = _Closing(key, replace(trade, open_qty=qty), None, legs, exit_order)
        link._closing.update(closing)
        paper = super().route(intent, now)
        updates += paper
        rejected = {u.tag for u in paper if u.status == "rejected"}
        for tag in sorted(rejected & set(snaps)):
            link._closing.pop(tag, None)
            reason = next(u.reason for u in paper if u.tag == tag and u.status == "rejected")
            self._log(
                {"kind": "broker_flatten_skipped", "level": "warning", "tag": tag, "reason": reason}
            )
        per_inst: dict[str, int] = {}
        for tag, (key, side, qty, entry_id, legs) in snaps.items():
            if tag in rejected or qty <= 0:
                continue
            if tag in link.exits_open:
                self._log({"kind": "broker_exit_dup_skipped", "level": "info", "tag": tag})
                continue
            exit_order = Order(
                client_id(self._id, tag, "exit"),
                key,
                key.instrument,
                side,
                "market",
                qty,
                None,
                now,
                "exit",
            )
            if tag in link._closing:
                link._closing[tag] = replace(link._closing[tag], exit=exit_order)
            link.note_exit(
                tag,
                ExitAction(
                    exit_order, side, qty, legs=tuple(o.client_id for o in legs), entry_id=entry_id
                ),
            )
        for a in resting:
            if a.setup_key is None or tag_of(a.setup_key) not in rejected:
                link._actions.append(a)
        for eid in link.exits_open.values():
            order = link._known.get(eid)
            if order is not None:
                per_inst[order.instrument] = per_inst.get(order.instrument, 0) + _signed(
                    order.side, link._exit_qty.get(eid, 0)
                )
        if intent.tag is None:
            self._remainders(link, per_inst, now)
        return updates

    def _remainders(self, link: OrderLink, open_exits: Mapping[str, int], now: Instant) -> None:
        positions = link.broker_positions() or {}
        for inst in sorted(link.instruments):
            b = positions.get(inst, 0)
            if not b:
                continue
            side: Side = "sell" if b > 0 else "buy"
            covering = open_exits.get(inst, 0)
            closes = abs(covering) if (covering < 0) == (b > 0) else 0
            r = abs(b) - closes
            if r <= 0:
                continue
            listed = {leg for a in link._actions if isinstance(a, ExitAction) for leg in a.legs}
            legs = tuple(
                cid
                for cid, o in link._known.items()
                if o.instrument == inst and o.role in _LEGS and cid not in listed
            )
            order = Order(
                link.next_remainder_id(), None, inst, side, "market", r, None, now, "exit"
            )
            link.note_exit(None, ExitAction(order, side, r, legs=legs))

    def cancel_entries(self, now: Instant, reason: str) -> list[OrderUpdate]:
        link = self.link
        if link is None:
            return super().cancel_entries(now, reason)
        out: list[OrderUpdate] = []
        for tag in self.working_tags():
            if tag in link.market_tags:
                if tag not in link._cancel_logged:
                    link._cancel_logged.add(tag)
                    self._log({"kind": "broker_cancel_ignored", "level": "info", "tag": tag})
                continue
            entry = self.book.order(client_id(self._id, tag, "entry"))
            out.extend(self._cancel_tag(tag, now, reason))
            if entry is not None and entry.setup_key is not None:
                link._actions.append(
                    CancelAction(entry.client_id, entry.instrument, "entry", entry.setup_key)
                )
        return out

    def has_exposure(self) -> bool:
        if super().has_exposure():
            return True
        link = self.link
        if link is None:
            return False
        positions = link.broker_positions() or {}
        return any(q != 0 for i, q in positions.items() if i in link.instruments)

    def on_bar(self, bar: Bar) -> list[OrderUpdate]:
        updates = super().on_bar(bar)
        link = self.link
        if link is not None:
            for u in updates:
                if u.role == "entry" and u.status in ("filled_entry", "expired", "cancelled"):
                    link.market_tags.discard(u.tag)
        return updates

    def withdraw(self, tag: str, now: Instant, reason: str) -> list[OrderUpdate]:
        """Cancel the paper entry the broker never got (at its placement time if unchanged)."""
        if self.link is not None:
            self.link.market_tags.discard(tag)
        cid = client_id(self._id, tag, "entry")
        working = self.book.orders.get(cid)
        if working is None or working.latest is None:
            return []
        before = self.book
        if len(working.versions) == 1:
            at = working.versions[0][0]  # placement time: no bar can fill it
        else:
            at = max(now, working.versions[-1][0])
        after = before.cancel(cid, at)
        cancels = dict(self._state.cancels)
        cancels.setdefault(tag, reason)
        self._state = replace(self._state, cancels=cancels)
        return self._apply(before, after, [], None, now)


def _exit_side(direction: str) -> Side:
    return "sell" if direction == "long" else "buy"


def _signed(side: Side, qty: int) -> int:
    return qty if side == "buy" else -qty
