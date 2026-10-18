"""``PluginHost``: the single dispatcher of ``fse run`` (design 2.2, 3.3, 5, 12, 13).

One asyncio task (:meth:`PluginHost.run`) is the only mutator of plugin books
and plugin state, so a replay of the same inputs gives the same plugin calls.

**Events.** The data plane submits :data:`MarketEvent` values
(:meth:`PluginHost.submit`). Per event the dispatcher records it
(``HostDataPlane.record``), stores bars (``store_bars``), and then, per bar and
per plugin in config order: the plugin's book (``PluginPaperBroker.on_bar``),
``on_order_update`` for the book's updates, the plugin hook, and the routing
of the returned intents. Bars a plugin's book already processed
(``open_ns <= last_bar_open``) are not dispatched again. The dispatch rule
(design 3.3) decides which plugin sees which event.

**Intent rounds.** Intents are routed in order; their updates queue up and are
delivered by ``on_order_update``, whose intents are routed the same way. One
round delivers every update the previous round queued. A 17th round in one
chain (one bar, one hook call) faults the plugin (``intent_loop``). Only
entries (``EnterMarket``, ``EnterResting``) get the halt file and blocks;
``Flatten``, ``Cancel`` and ``ModifyStop`` are never gated. Rejections other
than the blocks and caps (e.g. ``stop_loosen``, ``not_open``,
``invalid_order``) are logged as ``intent_rejected`` warnings.

**Services.** A host-brokered plugin is constructed with ``services.fills``
(:class:`PaperFills`), ``services.risk`` (:class:`PaperRisk`) and
``services.broker_terms`` (:class:`BrokerTerms`), all built from its runner
``fills``/``risk`` blocks (``FillsBlock`` defaults when ``fills`` is omitted);
a self-brokered plugin gets ``None`` for all three.

**Wakes.** After draining the queue the dispatcher evaluates ``next_wake``
for every plugin and calls ``on_wake`` once for each plugin whose wake is
``<= now`` (NIT 3), then sleeps until the earliest of: a later wake, the next
Globex day boundary, the once-per-second gate tick (only while a host-brokered
plugin has a working entry), a calendar retry, ``until``, or the next event.
A task started through ``services.run_blocking`` or ``services.spawn`` gets
exactly one ``task_done`` wake when it finishes: an ``on_wake`` call on the
dispatcher.

**Days** (design 5.3). At ``rotate_ns``: ``on_day_close``; the break backstop
(``Flatten(None, "host_break_backstop", "last_close")`` for every
host-brokered plugin whose effective ``hold_through_break`` is false and that
still has exposure, Friday 18:00 included); day counters and ``used_tags``
reset; ``HostDataPlane.rotate`` (recorder rotation and the next day's
contracts); calendar reload for ``[d+1, d+7]`` (on failure the previous
calendars stay and every plugin's entries are blocked, ``calendar_stale``,
retried hourly). At ``open_ns``: ``on_day_open`` and the urgent bar closes.

**Persistence** (design 12.2). At the end of every dispatch step that called a
hook of a host-brokered plugin or changed its book, ``<state_dir>/plugin.json``
= ``{"version": 1, "seq": n, "book": PluginBrokerState, "plugin":
plugin.snapshot()}`` is written atomically, unless its bytes (ignoring
``seq``) equal the last write. Three ``OSError`` in a row block entries
(``state_write_failed``) until a write succeeds. Just before it,
``<state_dir>/exit_reasons.json`` (tag -> reason of each pending flatten) is
written the same way; its failures only warn. A replay
(``coalesce_state=True``) does not rewrite ``plugin.json`` on each bar: the
bar ring changes every minute, so the byte compare never hits, and that
encode is most of a rules replay. Shutdown still writes the final book.
Hook calls and fills are the same either way.

**Journal** (paper-analytics §4). Every fill and closed trade of a
host-brokered plugin is appended to ``<run>/plugins/<id>/trades.jsonl``
(:mod:`fse.live.host.journal`) before the update reaches the plugin. A
journal failure logs ``journal_write_failed`` once and never blocks trading.

**Startup** (design 12.1 steps 5-8): restore both halves or discard both
(fresh book and plugin, a ``restore_failed`` block in the plugin's own
``blocks.json``, the file renamed ``plugin.json.bad-<UTC stamp>``);
``on_start``; repair replay of today's recorded bars after the book's
``last_bar_open`` with ``catch_up=True``; stale-position flatten
(``restart_stale``); ``on_day_open(today)``; then the feeds and the
dispatcher.

**Faults** (design 13). An exception a hook does not catch faults its plugin
for the life of the process (not persisted): no further calls, spawned tasks
cancelled, working entries cancelled (stops and targets stay), the break
backstop still applies, and :meth:`PluginHost.run` returns 1.

**Marks.** Every quote and bar updates ``<live-state>/marks.json``
(:class:`~fse.live.host.marks.MarksWriter`): the latest quote and 1-minute close
per instrument, rewritten at most once per second and only on a change, so the
console can value open positions between bars. A replay passes
``write_marks=False``.

**Decision cards.** A plugin log line with a ``"card"`` object is also cleaned
(:func:`~fse.live.host.cards.clean_card`) and appended to
``<out_dir>/decision_cards.jsonl`` as ``{"t_ns", "plugin", "kind", "card"}``,
unless the plugin's runner entry has ``cards: false``. An invalid card is
logged once per plugin (``card_invalid``); ``plugin_log.jsonl`` is unchanged.

**Graceful stop** (design 12.3): stop the feeds, drain the queue,
``on_stop``, write ``plugin.json``, close the data plane. Open paper positions
are not flattened.

**Paper accounts** (paper-analytics design 3). A host-brokered plugin with
``account: <id>`` shares that runner ``accounts[]`` entry with every other
plugin naming it. The account's status adds gate-level entry blocks
(``account_breached``, ``account_daily_loss``, ``account_flat_by``,
``account_state_write_failed``) before the plugin's caps; after the caps,
``account_check`` refuses entries in a no-trade window
(``account_no_trade_window``) or past the shared position cap
(``account_max_position``). Every closed trade of a member books to the
account (:meth:`PluginHost._record`); after each bar the account is valued
over every member book and its rules checked (one ``account_status`` and one
``account_flatten`` line per transition). An MLL or DLL breach and the flat
deadline flatten every member at the next open. ``<live-state>/accounts/<id>/
account.json`` is written before the members' ``plugin.json``. A plugin
without an account never reaches any of this.

**Nexus mirror** (paper-analytics §5). An account with ``nexus.enabled``
gets its members' fills enqueued (:mod:`fse.live.host.nexus`) after the
journal and account steps of ``_record``; a separate asyncio task sends
them. The host never awaits Nexus: :meth:`PluginHost.run` sends the broker
exits first, then drains the mirror for at most 2 s at shutdown and cancels
it. A Practice/Combine plugin's account may mirror too: Nexus gets the paper
book's fills, never the ProjectX fills, and nothing in the mirror touches a
ProjectX order. Only ``fse run`` hands the host a transport
(``HostResources.nexus``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time as _time
from collections import deque
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from functools import partial
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Protocol

from fse.calendars import CalendarError, Calendars
from fse.clock import Clock
from fse.config.schema.account import in_no_trade_window
from fse.engine.types import Bar, DarkPoolPrint, Money, SetupKey, Snapshot, Ticks, Trade
from fse.live.host.accounts import (
    ACCOUNT_CODEC,
    ACCOUNT_REASONS,
    ACCOUNT_STATE_FILE_NAME,
    ACCOUNT_STATE_VERSION,
    AccountAction,
    FlattenMembers,
    PaperAccount,
    PaperAccountState,
    StatusChanged,
    account_dir,
)
from fse.live.host.broker import (
    BROKER_CODEC,
    REASON_RE,
    PluginBrokerState,
    PluginPaperBroker,
    tag_of,
)
from fse.live.host.cards import CARDS_FILE_NAME, clean_card
from fse.live.host.config import (
    AccountEntry,
    FillsBlock,
    PluginEntry,
    RunnerConfig,
    account_brokerage_problem,
    execution_brokerage_problem,
)
from fse.live.host.day_net_reset import take_day_net_reset
from fse.live.host.dayclock import (
    GlobexDay,
    globex_day,
    globex_day_at,
    next_globex_day,
    next_open,
    next_open_ns,
)
from fse.live.host.gate import EntryGate, plugin_dir
from fse.live.host.journal import (
    JOURNAL_FILE_NAME,
    PENDING_EXIT_RESTART,
    JournalSlot,
    journal_day,
    journal_lines,
)
from fse.live.host.live_orders import (
    ACCOUNT_VARIABLES,
    BROKER_DRAIN_TIMEOUT_NS,
    BROKER_MODES,
    LINK_BLOCK_KINDS,
    BrokeredPaperBroker,
    BrokerEvent,
    ModeDecision,
    OrderLink,
    PluginOrderSetup,
    SafetyFactory,
    new_run_uuid8,
)
from fse.live.host.marks import MARKS_FILE_NAME, MarksWriter
from fse.live.host.nexus import (
    DRAIN_TIMEOUT_NS,
    MirrorFill,
    NexusMirror,
    NexusTransport,
    bounded,
)
from fse.live.host.status import (
    STATUS_FILE_NAME,
    Activity,
    build_status,
    clean_plugin_status,
    describe_update,
)
from fse.live.levels_compare import LevelsClient
from fse.live.plugins.api import (
    BarStoreView,
    BrokerTerms,
    Cancel,
    DataNeeds,
    EnterMarket,
    EnterResting,
    FeedName,
    Flatten,
    Intent,
    ModifyStop,
    OrderUpdate,
    PaperFills,
    PaperRisk,
    PluginServices,
    Quote,
    StrategyPlugin,
)
from fse.live.plugins.loader import PluginLoadError, needs_problems
from fse.live.state_store import BlockRecord
from fse.logio import LogWriter, canonical_json
from fse.logio.canonical_json import JsonValue
from fse.notify.finding_trade import (
    TRADE_BAR_LIMIT,
    odds_for_pattern,
    session_day_range_pct,
    trade_bars,
)
from fse.secrets.env import EnvView
from fse.sim.account import MICRO_EQUIVALENTS
from fse.sim.fills import OpenTrade
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, Instant, ny_datetime, ny_instant

__all__ = [
    "CALENDAR_RETRY_NS",
    "EXIT_REASONS_FILE_NAME",
    "EXIT_REASONS_VERSION",
    "GATE_TICK_NS",
    "HOOK_LATENCY_WARN_NS",
    "HOST_LOG_FILE_NAME",
    "MAX_INTENT_ROUNDS",
    "PLUGIN_LOG_FILE_NAME",
    "PLUGIN_STATE_FILE_NAME",
    "PLUGIN_STATE_VERSION",
    "STATE_WRITE_FAILURE_LIMIT",
    "BarsEvent",
    "BlockingRunner",
    "DarkPoolEvent",
    "FeedStatusEvent",
    "HostDataPlane",
    "HostPaths",
    "HostResources",
    "InlineBlockingRunner",
    "MarketEvent",
    "PluginHost",
    "QuoteEvent",
    "SnapshotsEvent",
    "ThreadBlockingRunner",
]

HOOK_LATENCY_WARN_NS: Final = 1_000_000_000
MAX_INTENT_ROUNDS: Final = 16
GATE_TICK_NS: Final = NS_PER_SECOND
CALENDAR_RETRY_NS: Final = 3600 * NS_PER_SECOND
CALENDAR_DAYS_AHEAD: Final = 7
STATE_WRITE_FAILURE_LIMIT: Final = 3
PLUGIN_STATE_FILE_NAME: Final = "plugin.json"
PLUGIN_STATE_VERSION: Final = 1
HOST_LOG_FILE_NAME: Final = "host_log.jsonl"
PLUGIN_LOG_FILE_NAME: Final = "plugin_log.jsonl"
PLUGINS_OUT_DIR_NAME: Final = "plugins"
EXIT_REASONS_FILE_NAME: Final = "exit_reasons.json"
EXIT_REASONS_VERSION: Final = 1
EXIT_OK: Final = 0
EXIT_FAULT: Final = 1

_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_STATE_KEYS: Final = frozenset({"version", "seq", "book", "plugin"})
_ACCOUNT_KEYS: Final = frozenset({"version", "seq", "account", "view"})
_BROKER_STATE_REASONS: Final = frozenset(
    {"broker_catch_up", "broker_stopping", "broker_entry_sent", *LINK_BLOCK_KINDS}
)
"""Broker-path refusals that are the runner's state, not a bad intent (logged at info)."""
_CAP_REASONS: Final = frozenset(
    {"max_contracts", "max_open_trades", "max_trades_per_day", "max_daily_loss"}
)
_ZERO: Final = Decimal("0.00")


# ---------------------------------------------------------------- events


@dataclass(frozen=True, slots=True)
class BarsEvent:
    """Closed bars from one fetch, oldest first per instrument."""

    bars: tuple[Bar, ...]
    received_ns: Instant


@dataclass(frozen=True, slots=True)
class SnapshotsEvent:
    snaps: tuple[Snapshot, ...]
    received_ns: Instant


@dataclass(frozen=True, slots=True)
class DarkPoolEvent:
    ticker: str
    prints: tuple[DarkPoolPrint, ...]
    received_ns: Instant


@dataclass(frozen=True, slots=True)
class QuoteEvent:
    quote: Quote


@dataclass(frozen=True, slots=True)
class FeedStatusEvent:
    feed: FeedName
    up: bool
    detail: str


type MarketEvent = BarsEvent | SnapshotsEvent | DarkPoolEvent | QuoteEvent | FeedStatusEvent


@dataclass(frozen=True, slots=True)
class _TaskDone:
    plugin_id: str
    name: str


@dataclass(frozen=True, slots=True)
class _Stop:
    pass


# ---------------------------------------------------------------- collaborators


class HostDataPlane(Protocol):
    """What the host needs from the data plane (``fse.live.host.dataplane``, or a fake)."""

    @property
    def bar_store(self) -> BarStoreView: ...

    def record(self, event: MarketEvent) -> None:
        """Write the event's recording line(s); ``OSError`` stops the run."""
        ...

    def store_bars(self, bars: Sequence[Bar], received_ns: Instant) -> None:
        """Add closed bars to the bar store; ``OSError`` stops the run."""
        ...

    def recorded_bars(self, day: GlobexDay) -> Sequence[tuple[Bar, Instant]]:
        """``(bar, received_ns)`` recorded so far on ``day``, in dispatch order (repair)."""
        ...

    def set_urgent(self, closes: Sequence[tuple[str, Instant]]) -> None:
        """The bar closes plugins want as early as possible today."""
        ...

    def rotate(self, closing: GlobexDay, opening: GlobexDay | None) -> None:
        """Close ``closing``'s recordings and prepare ``opening`` (contracts, files)."""
        ...

    async def run(self, submit: Callable[[MarketEvent], None]) -> None:
        """Run the feeds, calling ``submit`` for each event, until cancelled."""
        ...

    def close(self) -> None:
        """Flush and close recordings (graceful stop)."""
        ...


class BlockingRunner(Protocol):
    """Runs a blocking job for ``services.run_blocking``."""

    def start[T](self, fn: Callable[..., T], *args: object) -> asyncio.Task[T]: ...


class InlineBlockingRunner:
    """Tests: runs ``fn`` at once and returns an already-finished task (deterministic)."""

    __slots__ = ()

    def start[T](self, fn: Callable[..., T], *args: object) -> asyncio.Task[T]:
        async def value() -> T:
            return fn(*args)

        return asyncio.Task(value(), loop=asyncio.get_running_loop(), eager_start=True)


class ThreadBlockingRunner:
    """Production: runs ``fn`` in a worker thread (``asyncio.to_thread``)."""

    __slots__ = ()

    def start[T](self, fn: Callable[..., T], *args: object) -> asyncio.Task[T]:
        return asyncio.get_running_loop().create_task(asyncio.to_thread(fn, *args))


@dataclass(frozen=True, slots=True)
class HostPaths:
    """Resolved directories of one run."""

    state_root: Path
    """The live-state root (``<live-state>``); plugins get ``<live-state>/plugins/<id>``."""
    run_dir: Path
    """The run directory: ``host_log.jsonl`` and ``plugins/<id>/`` (plugin out dirs)."""
    cache_dir: Path
    calendar_dir: Path
    project_dir: Path | None = None
    recordings_root: Path | None = None
    """``<recordings_root>/recordings/``; handed to plugins (the Playbook records there)."""


@dataclass(frozen=True, slots=True)
class HostResources:
    """Process resources handed to plugins through their services."""

    env: EnvView
    http: Any = None
    """The process ``httpx.AsyncClient`` (or ``None``)."""
    skylit_levels: LevelsClient | None = None
    nexus: NexusTransport | None = None
    """The Nexus transport; only ``fse run`` sets it (DP-12), and only with a key."""
    nexus_resolved: Mapping[str, str] = field(default_factory=dict)
    """Paper account id -> resolved Nexus account (memory only, never written)."""
    nexus_disabled: Mapping[str, str] = field(default_factory=dict)
    """Paper account id -> why its mirror is off (``env var X not set``, …)."""


# ---------------------------------------------------------------- per plugin


@dataclass(slots=True)
class _Slot:
    entry: PluginEntry
    cls: type[StrategyPlugin]
    services: PluginServices
    plugin: StrategyPlugin
    needs: DataNeeds
    broker: PluginPaperBroker | None
    hold: bool
    instruments: frozenset[str]
    state_path: Path
    out_dir: Path
    faulted: str | None = None
    tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    last_seen: dict[str, Instant] = field(default_factory=dict)
    touched: bool = False
    rounds: int = 0
    last_intents: list[str] = field(default_factory=list)
    seq: int = 0
    last_body: str | None = None
    write_failures: int = 0
    host_blocks: list[str] = field(default_factory=list)
    status_path: Path | None = None
    activity: Activity = field(default_factory=Activity)
    next_wake: Instant | None = None
    last_log: dict[str, JsonValue] | None = None
    status_body: str | None = None
    status_failed: bool = False
    card_invalid: bool = False
    keys: dict[str, SetupKey] = field(default_factory=dict)
    account: str | None = None
    """The paper account id (runner ``account:``) of a host-brokered plugin."""
    exit_reasons: dict[str, str] = field(default_factory=dict)
    """Tag -> reason of the flatten routed for it and not yet closed."""
    journal_failed: bool = False
    """``journal_write_failed`` was logged and no line has been written since."""
    reasons_last_body: str | None = None
    reasons_write_failed: bool = False
    link: OrderLink | None = None
    """The ProjectX order link of the one broker plugin (``execution.order_mode``)."""

    @property
    def id(self) -> str:
        return self.entry.id

    @property
    def journal_path(self) -> Path:
        """``<run>/plugins/<id>/trades.jsonl``."""
        return self.out_dir / JOURNAL_FILE_NAME

    @property
    def reasons_path(self) -> Path:
        """``<state_dir>/exit_reasons.json``, next to ``plugin.json``."""
        return self.state_path.with_name(EXIT_REASONS_FILE_NAME)


@dataclass(slots=True)
class _AccountSlot:
    """One paper account in the host: its rule engine, members and ``account.json``."""

    entry: AccountEntry
    account: PaperAccount
    members: list[_Slot]
    state_path: Path
    seq: int = 0
    last_body: str | None = None
    write_failures: int = 0
    write_blocked: bool = False
    """``account_state_write_failed``: the last writes failed, entries refused."""
    touched: bool = False

    @property
    def id(self) -> str:
        return self.entry.id


class _SlotTasks:
    """The :class:`~fse.live.plugins.api.TaskStarter` of one plugin."""

    __slots__ = ("_host", "_id")

    def __init__(self, host: PluginHost, plugin_id: str) -> None:
        self._host = host
        self._id = plugin_id

    def run_blocking[T](self, name: str, fn: Callable[..., T], *args: object) -> asyncio.Task[T]:
        task = self._host._blocking.start(fn, *args)
        self._host._track(self._id, name, task, spawned=False)
        return task

    def spawn[T](self, name: str, coro: Coroutine[Any, Any, T]) -> asyncio.Task[T]:
        task = asyncio.get_running_loop().create_task(coro, name=f"{self._id}:{name}")
        self._host._track(self._id, name, task, spawned=True)
        return task


class _Context:
    """The :class:`~fse.live.plugins.api.PluginContext` of one hook call."""

    __slots__ = ("_host", "_slot", "now")

    def __init__(self, host: PluginHost, slot: _Slot, now: Instant) -> None:
        self._host = host
        self._slot = slot
        self.now = now

    def position(self, instrument: str) -> int:
        b = self._slot.broker
        return 0 if b is None else b.position(instrument)

    def open_tags(self) -> tuple[str, ...]:
        b = self._slot.broker
        return () if b is None else b.open_tags()

    def working_tags(self) -> tuple[str, ...]:
        b = self._slot.broker
        return () if b is None else b.working_tags()

    def entries_blocked(self) -> tuple[str, ...]:
        return self._host._blocked_reasons(self._slot, caps=True)

    def day_net(self) -> Decimal:
        b = self._slot.broker
        return Decimal(0) if b is None else b.state.day_net

    def log(self, entry: Mapping[str, JsonValue]) -> None:
        self._host._plugin_log(self._slot, entry)


class _NullBarStore:
    """A bar store with nothing in it (when the data plane has none)."""

    @property
    def root(self) -> Path:
        return Path()

    def bars(self, instrument: str, day: date) -> tuple[Bar, ...]:
        return ()


def _is_faulted(slot: _Slot) -> bool:
    return slot.faulted is not None


def _utc_stamp(now: Instant) -> str:
    return (_EPOCH + timedelta(microseconds=now // 1000)).strftime("%Y%m%dT%H%M%SZ")


def _is_json(value: object, depth: int = 0) -> bool:
    if depth > 64:
        return False
    if value is None or isinstance(value, bool | int | str):
        return True
    if isinstance(value, float):
        return value == value and value not in (float("inf"), float("-inf"))
    if isinstance(value, list):
        return all(_is_json(v, depth + 1) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_json(v, depth + 1) for k, v in value.items())
    return False


# ---------------------------------------------------------------- the host


class PluginHost:
    """Loads plugins, dispatches data plane events to them, brokers and persists them."""

    def __init__(
        self,
        cfg: RunnerConfig,
        classes: Mapping[str, type[StrategyPlugin]],
        *,
        dataplane: HostDataPlane,
        clock: Clock,
        writer: LogWriter,
        paths: HostPaths,
        calendars: Calendars,
        resources: HostResources,
        calendar_loader: Callable[[date, date], Calendars] | None = None,
        blocking: BlockingRunner | None = None,
        perf_ns: Callable[[], int] = _time.perf_counter_ns,
        trade_sink: Callable[[str, Trade], None] | None = None,
        write_marks: bool = True,
        coalesce_state: bool = False,
    ) -> None:
        self._cfg = cfg
        self._trade_sink = trade_sink
        self._dataplane = dataplane
        self._clock = clock
        self._writer = writer
        self._paths = paths
        self._calendars = calendars
        self._calendar_loader = calendar_loader
        self._blocking: BlockingRunner = (
            blocking if blocking is not None else ThreadBlockingRunner()
        )
        self._perf_ns = perf_ns
        self._coalesce_state = coalesce_state
        self._queue: asyncio.Queue[MarketEvent | _TaskDone | _Stop | BrokerEvent] = asyncio.Queue()
        self._order_decisions: dict[str, ModeDecision] = {}
        self._order_flags: tuple[bool, bool] = (False, False)
        self._order_run_uuid8: str | None = None
        self._broker_notes = 0
        self._quotes: dict[str, Quote] = {}
        self._gate = EntryGate(writer, paths.state_root, self._host_log)
        self._marks: MarksWriter | None = (
            MarksWriter(writer, paths.state_root / MARKS_FILE_NAME, log=self._host_log)
            if write_marks
            else None
        )
        self._day: GlobexDay | None = None
        self._next_open: GlobexDay | None = None
        self._calendar_stale_since: Instant | None = None
        self._stop = False
        self._exit_code = EXIT_OK
        self._started = False
        self._now: Instant = clock.now()
        self._resources = resources
        self._slots: list[_Slot] = []
        self._by_id: dict[str, _Slot] = {}
        problems: list[str] = []
        for entry in cfg.enabled_plugins:
            cls = classes.get(entry.id)
            if cls is None:
                problems.append(f"plugin {entry.id!r}: no class was loaded for it")
                continue
            try:
                slot = self._build_slot(entry, cls)
            except PluginLoadError as exc:
                problems.extend(exc.problems)
                continue
            problems.extend(needs_problems(entry, slot.needs, cfg.data_plane))
            if isinstance(slot.needs, DataNeeds):
                brokerage = account_brokerage_problem(cfg, entry, slot.needs.brokerage)
                if brokerage is not None:
                    problems.append(brokerage)
                execution = execution_brokerage_problem(
                    cfg,
                    entry,
                    brokerage=slot.needs.brokerage,
                    broker_orders=slot.needs.broker_orders,
                    instruments=slot.instruments,
                )
                if execution is not None:
                    problems.append(execution)
            self._slots.append(slot)
            self._by_id[slot.id] = slot
        if problems:
            raise PluginLoadError(problems)
        self._accounts: dict[str, _AccountSlot] = self._build_accounts(cfg)
        self._mirror: NexusMirror | None = self._build_mirror()

    # ------------------------------------------------------------ construction

    def _build_accounts(self, cfg: RunnerConfig) -> dict[str, _AccountSlot]:
        """Every account an enabled host-brokered plugin names, with its member slots."""
        entries = {a.id: a for a in cfg.accounts}
        members: dict[str, list[_Slot]] = {}
        for slot in self._slots:
            acct_id = slot.entry.account
            if acct_id is None or slot.broker is None:
                continue
            slot.account = acct_id
            members.setdefault(acct_id, []).append(slot)
        out: dict[str, _AccountSlot] = {}
        for acct_id, slots in members.items():
            entry = entries[acct_id]
            out[acct_id] = _AccountSlot(
                entry=entry,
                account=PaperAccount.fresh(acct_id, entry.rules, (s.id for s in slots)),
                members=slots,
                state_path=account_dir(self._paths.state_root, acct_id) / ACCOUNT_STATE_FILE_NAME,
            )
        return out

    def _build_mirror(self) -> NexusMirror | None:
        """The Nexus mirror of the accounts in use with ``nexus.enabled`` (else ``None``)."""
        mirrored = [a.entry for a in self._accounts.values() if a.entry.nexus.enabled]
        if not mirrored:
            return None
        res = self._resources

        async def offload(fn: Callable[[], object]) -> object:
            return await self._blocking.start(fn)

        return NexusMirror(
            mirrored,
            state_root=self._paths.state_root,
            transport=res.nexus,
            resolved=res.nexus_resolved,
            disabled=res.nexus_disabled,
            writer=self._writer,
            clock=self._clock,
            log=self._host_log,
            offload=offload,
            next_open_ns=lambda now: next_open_ns(now, self._calendars.exchange.sessions),
        )

    def _services(self, entry: PluginEntry, *, self_brokered: bool) -> PluginServices:
        sky = self._cfg.data_plane.skylit
        out_dir = self._paths.run_dir / PLUGINS_OUT_DIR_NAME / entry.id
        state_dir = (
            self._paths.state_root
            if self_brokered
            else plugin_dir(self._paths.state_root, entry.id)
        )
        store = getattr(self._dataplane, "bar_store", None)
        fills: PaperFills | None = None
        risk: PaperRisk | None = None
        terms: BrokerTerms | None = None
        if not self_brokered:
            block = entry.fills if entry.fills is not None else FillsBlock()
            fills = PaperFills(
                trade_through_ticks=block.trade_through_ticks,
                slippage_ticks=block.slippage_ticks,
                stop_fill=block.stop_fill,
                commission_rt=MappingProxyType({str(k): v for k, v in block.commission_rt.items()}),
            )
            caps = entry.risk
            assert caps is not None  # self_brokered is entry.risk is None (_build_slot)
            risk = PaperRisk(
                max_contracts=caps.max_contracts,
                max_open_trades=caps.max_open_trades,
                max_trades_per_day=caps.max_trades_per_day,
                max_daily_loss=caps.max_daily_loss,
            )
            terms = BrokerTerms(
                trade_through_ticks=fills.trade_through_ticks,
                slippage_ticks=fills.slippage_ticks,
                stop_fill=fills.stop_fill,
                commission_rt=fills.commission_rt,
                max_contracts=risk.max_contracts,
                max_open_trades=risk.max_open_trades,
                max_trades_per_day=risk.max_trades_per_day,
                max_daily_loss=risk.max_daily_loss,
            )
        return PluginServices(
            tasks=_SlotTasks(self, entry.id),
            calendars=lambda: self._calendars,
            writer=self._writer,
            clock=self._clock,
            calendar_dir=self._paths.calendar_dir,
            state_dir=state_dir,
            out_dir=out_dir,
            project_dir=self._paths.project_dir,
            bar_store=store if store is not None else _NullBarStore(),
            cache_dir=self._paths.cache_dir,
            env=self._resources.env,
            http=self._resources.http,
            skylit_levels=self._resources.skylit_levels if sky.enabled else None,
            skylit_window=sky.window.pair() if sky.enabled else None,
            fills=fills,
            recordings_root=self._paths.recordings_root,
            risk=risk,
            broker_terms=terms,
        )

    def _construct(
        self, entry: PluginEntry, cls: type[StrategyPlugin], services: PluginServices
    ) -> StrategyPlugin:
        try:
            return cls(entry.id, dict(entry.config), services)
        except Exception as exc:
            raise PluginLoadError(
                [f"plugin {entry.id!r}: cannot start: {type(exc).__name__}: {exc}"]
            ) from exc

    def _build_slot(self, entry: PluginEntry, cls: type[StrategyPlugin]) -> _Slot:
        # A plugin without a runner ``risk`` block is expected to be self-brokered
        # (needs validation reports any mismatch); it gets the live-state root.
        services = self._services(entry, self_brokered=entry.risk is None)
        plugin = self._construct(entry, cls, services)
        try:
            needs = plugin.needs()
        except Exception as exc:
            raise PluginLoadError(
                [f"plugin {entry.id!r}: needs() failed: {type(exc).__name__}: {exc}"]
            ) from exc
        instruments = needs.instruments if isinstance(needs, DataNeeds) else frozenset()
        broker: PluginPaperBroker | None = None
        hold = False
        if isinstance(needs, DataNeeds):
            hold = needs.hold_through_break and entry.hold_through_break
            if needs.brokerage == "host" and entry.risk is not None:
                kind = (
                    BrokeredPaperBroker
                    if entry.execution.order_mode != "paper"
                    else PluginPaperBroker
                )
                broker = kind.from_blocks(
                    entry.id,
                    entry.fills if entry.fills is not None else FillsBlock(),
                    entry.risk,
                    declared=instruments,
                    rth_for=self._rth_for,
                    log=partial(self._broker_log, entry.id),
                )
        return _Slot(
            entry=entry,
            cls=cls,
            services=services,
            plugin=plugin,
            needs=needs,
            broker=broker,
            hold=hold,
            instruments=instruments,
            state_path=services.state_dir / PLUGIN_STATE_FILE_NAME,
            out_dir=services.out_dir,
            status_path=plugin_dir(self._paths.state_root, entry.id) / STATUS_FILE_NAME,
        )

    # ------------------------------------------------------------ public

    @property
    def plugin_ids(self) -> tuple[str, ...]:
        return tuple(s.id for s in self._slots)

    def plugin(self, plugin_id: str) -> StrategyPlugin:
        return self._by_id[plugin_id].plugin

    def needs_of(self, plugin_id: str) -> DataNeeds:
        """The plugin's ``needs()`` (asked once, at construction)."""
        return self._by_id[plugin_id].needs

    def broker(self, plugin_id: str) -> PluginPaperBroker | None:
        return self._by_id[plugin_id].broker

    @property
    def account_ids(self) -> tuple[str, ...]:
        """The paper accounts in use (named by an enabled host-brokered plugin)."""
        return tuple(self._accounts)

    def account(self, account_id: str) -> PaperAccount:
        return self._accounts[account_id].account

    def faults(self) -> dict[str, str]:
        """``{plugin id: fault reason}`` of every faulted plugin."""
        return {s.id: s.faulted for s in self._slots if s.faulted is not None}

    def hold_through_break(self, plugin_id: str) -> bool:
        """The effective ``hold_through_break`` (needs AND YAML)."""
        return self._by_id[plugin_id].hold

    @property
    def exit_code(self) -> int:
        return self._exit_code

    @property
    def day(self) -> GlobexDay | None:
        return self._day

    def submit(self, event: MarketEvent) -> None:
        """Queue one data plane event (call on the event loop thread)."""
        self._queue.put_nowait(event)

    def attach_order_mode(
        self,
        setup: PluginOrderSetup | None,
        safety_factory: SafetyFactory | None,
        *,
        plugin_orders: bool = False,
        live_orders: bool = False,
    ) -> None:
        """Record ``fse run``'s order-mode decision; a broker decision gets an OrderLink.

        Call before :meth:`run`. ``setup`` ``None``: no plugin is non-paper.
        """
        if self._started:
            raise RuntimeError("attach_order_mode must be called before run()")
        self._order_flags = (plugin_orders, live_orders)
        if setup is None:
            return
        slot = self._by_id.get(setup.plugin_id)
        if slot is None or not isinstance(slot.broker, BrokeredPaperBroker):
            raise ValueError(f"plugin {setup.plugin_id!r} is not a non-paper host-brokered plugin")
        decision = setup.decision
        self._order_decisions[slot.id] = decision
        if decision.mode not in BROKER_MODES:
            return
        if setup.adapter is None or safety_factory is None or setup.ids is None:
            raise ValueError("a broker decision needs its adapter and a safety factory")
        self._order_run_uuid8 = setup.ids.run_uuid8
        link = OrderLink(
            setup,
            slot.broker,
            instruments=slot.instruments,
            factory=safety_factory,
            post=self._queue.put_nowait,
            log=self._host_log,
            clock=self._clock,
        )
        slot.link = link
        slot.broker.link = link

    def broker_instruments(self, plugin_id: str) -> frozenset[str]:
        """The plugin's declared instruments (``KeyError`` for an unknown id)."""
        return self._by_id[plugin_id].instruments

    def broker_entry_gate(self, plugin_id: str) -> Callable[[str], str | None]:
        """The host's reasons that also stop a broker send or resend (design D4 change 12)."""
        slot = self._by_id[plugin_id]

        def gate(_instrument: str) -> str | None:
            for reason in self._blocked_reasons(slot, caps=False):
                if reason not in LINK_BLOCK_KINDS:
                    return reason
            acct = self._account_of(slot)
            if acct is not None and in_no_trade_window(
                acct.account.rules.no_trade_windows, ny_datetime(self._clock.now()).time()
            ):
                return "account_no_trade_window"
            return None

        return gate

    def request_stop(self) -> None:
        """Graceful stop (SIGINT/SIGTERM): the dispatcher finishes and shuts down."""
        self._stop = True
        self._queue.put_nowait(_Stop())

    def fail(self, kind: str, detail: str) -> None:
        """Record a run-level error (e.g. a feed stop): logged, and the run exits 1."""
        self._host_log({"kind": kind, "level": "error", "detail": detail})
        self._exit_code = EXIT_FAULT

    async def run(self, until_ns: Instant | None = None) -> int:
        """Start up, dispatch until stopped (or ``until_ns``), shut down; the exit status."""
        if self._started:
            raise RuntimeError("a PluginHost runs once")
        self._started = True
        self._startup()
        loop = asyncio.get_running_loop()
        feeds = loop.create_task(self._dataplane.run(self.submit))
        mirror: asyncio.Task[None] | None = None
        if self._mirror is not None and self._mirror.active:
            mirror = loop.create_task(self._mirror.run())
        try:
            await self._loop(until_ns)
        except BaseException:
            for slot in self._slots:
                if slot.link is not None:
                    slot.link.stopping = True
            feeds.cancel()
            with contextlib.suppress(BaseException):
                await feeds
            await self._stop_brokers()
            await self._stop_mirror(mirror)
            raise
        feeds.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await feeds
        self._shutdown()
        await self._stop_brokers()
        await self._stop_mirror(mirror)
        return self._exit_code

    async def _stop_mirror(self, task: asyncio.Task[None] | None) -> None:
        """Let the mirror send what is queued (≤ 2 s on the host clock), then cancel it.

        Whatever is not sent stays pending in the outbox for the next start.
        """
        if task is None or self._mirror is None:
            return
        with contextlib.suppress(Exception):
            await bounded(self._mirror.drain(), self._clock, DRAIN_TIMEOUT_NS)
        task.cancel()
        with contextlib.suppress(BaseException):
            await task

    # ------------------------------------------------------------ broker orders

    def _order_mode_card(self, slot: _Slot, now: Instant) -> None:
        """The non-paper plugin's first card: the order mode in use, or why not (D6)."""
        requested = slot.entry.execution.order_mode
        decision = self._order_decisions.get(slot.id) or ModeDecision(
            requested, "paper", ("the Broker_Adapter did not resolve the accounts",)
        )
        if self._order_run_uuid8 is None:
            self._order_run_uuid8 = new_run_uuid8()
        used = decision.mode in BROKER_MODES
        title = (
            f"Order_Mode {decision.mode} in use"
            if used
            else f"Order_Mode {requested} was not used, running Paper"
        )
        reason = decision.note or (
            f"orders go to the account named by {ACCOUNT_VARIABLES[decision.mode]}"
            if used
            else "paper"
        )
        plugin_orders, live_orders = self._order_flags
        card: dict[str, JsonValue] = {
            "id": f"order-mode-{self._order_run_uuid8}",
            "title": title,
            "as_of_ns": now,
            "action": "info",
            "reason": reason,
            "fields": [
                ["requested", requested],
                ["mode", decision.mode],
                ["plugin_orders_flag", "yes" if plugin_orders else "no"],
                ["live_orders_flag", "yes" if live_orders else "no"],
            ],
        }
        for failure in decision.failures:
            self._host_log(
                {"kind": "order_mode", "level": "warning", "plugin": slot.id, "detail": failure}
            )
        line: dict[str, JsonValue] = {
            "t_ns": now,
            "kind": "order_mode",
            "level": "warning" if decision.failures else "info",
            "plugin": slot.id,
            "requested": requested,
            "mode": decision.mode,
            "failures": list(decision.failures),
        }
        try:
            self._writer.append_json(slot.out_dir / PLUGIN_LOG_FILE_NAME, line)
        except OSError:
            self._host_log({"kind": "plugin_log_failed", "level": "error", "plugin": slot.id})
        self._decision_card(slot, line, card)  # a safety notice: not gated by ``cards``

    def _broker_note(self, slot: _Slot, text: str) -> None:
        detail = self._writer.redact(text)
        now = self._clock.now()
        self._host_log(
            {"kind": "broker_note", "level": "warning", "plugin": slot.id, "detail": detail}
        )
        if self._order_run_uuid8 is None:
            self._order_run_uuid8 = new_run_uuid8()
        self._broker_notes += 1
        card: dict[str, JsonValue] = {
            "id": f"broker-note-{self._order_run_uuid8}-{self._broker_notes}",
            "title": f"Broker: {detail[:60]}",
            "as_of_ns": now,
            "action": "info",
            "reason": detail,
        }
        self._decision_card(slot, {"t_ns": now, "kind": "broker_note"}, card)

    def _open_broker_day(
        self, slot: _Slot, day: GlobexDay, now: Instant, *, force: bool = False
    ) -> None:
        link = slot.link
        if link is None:
            return
        previous = link.task
        safety = link.open_day(day, now, force=force)
        if safety is None:
            return
        if not force and "broker_task_stopped" in slot.host_blocks:
            slot.host_blocks.remove("broker_task_stopped")
        task = asyncio.get_running_loop().create_task(self._broker_task(link, safety, previous))
        link.task = task
        task.add_done_callback(partial(self._broker_task_done, slot.id))

    async def _broker_task(
        self, link: OrderLink, safety: Any, previous: asyncio.Task[None] | None
    ) -> None:
        if previous is not None and not previous.done():
            with contextlib.suppress(BaseException):
                await previous  # the old day's drain finishes first
        await safety.start()
        link.foreign_check(safety)
        link.release_carry(safety, self._clock.now())
        assert link.until_ns is not None
        await safety.run(link.until_ns)
        await safety.drain()

    def _broker_task_done(self, plugin_id: str, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return
        slot = self._by_id[plugin_id]
        name = type(exc).__name__
        self.fail("broker_task_stopped", f"plugin {plugin_id}: the broker task raised {name}")
        self._host_log(
            {"kind": "broker_task_failed", "level": "error", "plugin": plugin_id, "error": name}
        )
        if "broker_task_stopped" not in slot.host_blocks:
            slot.host_blocks.append("broker_task_stopped")
        self._queue.put_nowait(BrokerEvent(plugin_id, "restart", None, name))

    def _broker_event(self, event: BrokerEvent) -> None:
        slot = self._by_id.get(event.plugin_id)
        if slot is None or slot.link is None or not isinstance(slot.broker, BrokeredPaperBroker):
            return
        link, broker = slot.link, slot.broker
        if event.kind == "note":
            self._broker_note(slot, event.text)
        elif event.kind == "withdraw" and event.tag is not None:
            slot.touched = True
            updates = broker.withdraw(event.tag, self._now, event.text)
            if updates:
                self._deliver(slot, updates)
            elif event.tag in broker.open_tags():
                self._host_log(
                    {
                        "kind": "broker_withdraw_late",
                        "level": "warning",
                        "plugin": slot.id,
                        "tag": event.tag,
                    }
                )
        elif event.kind == "stale_broker":
            self._host_log(
                {
                    "kind": "restart_stale",
                    "level": "error",
                    "plugin": slot.id,
                    "source": "broker",
                    "instrument": event.tag,
                }
            )
            self._broker_note(
                slot, "the broker holds a position this plugin's paper book does not; closing it"
            )
            slot.touched = True
            updates = broker.route(Flatten(None, "restart_stale", "last_close"), self._now)
            self._deliver(slot, updates)
        elif event.kind == "restart":
            day = self._day
            if link.stopping or day is None or link.day != day.day:
                self._host_log(
                    {"kind": "broker_restart_skipped", "level": "warning", "plugin": slot.id}
                )
            elif link.restarted_day != link.day:
                link.restarted_day = link.day
                self._open_broker_day(slot, day, self._now, force=True)
            else:
                self._broker_note(slot, "broker exits cannot be sent; flatten the account manually")
                self._host_log(
                    {
                        "kind": "broker_task_failed",
                        "level": "error",
                        "plugin": slot.id,
                        "restart": False,
                    }
                )

    async def _stop_brokers(self) -> None:
        """Send what is routed or carried (never an entry), bounded, then stop (D4 change 10)."""
        links = [s for s in self._slots if s.link is not None]
        for slot in links:
            link = slot.link
            assert link is not None
            link.stopping = True
            task = link.task
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
            if link.safety is None:
                continue
            link.submit_unsent(self._clock.now())
            with contextlib.suppress(Exception):
                await bounded(
                    link.safety.drain(entries=False), self._clock, BROKER_DRAIN_TIMEOUT_NS
                )
            left = link.take_left()
            if left:
                self._host_log(
                    {
                        "kind": "broker_stop_unsent",
                        "level": "warning",
                        "plugin": slot.id,
                        "count": left,
                    }
                )
        if not links:
            return
        while not self._queue.empty():
            event = self._queue.get_nowait()
            if not isinstance(event, BrokerEvent):
                continue
            target = self._by_id.get(event.plugin_id)
            if target is None:
                continue
            if event.kind == "note":
                self._broker_note(target, event.text)
            else:
                kind = "broker_withdraw_stop" if event.kind == "withdraw" else "broker_event_stop"
                self._host_log(
                    {
                        "kind": kind,
                        "level": "info",
                        "plugin": target.id,
                        "event": event.kind,
                        "tag": event.tag,
                    }
                )

    # ------------------------------------------------------------ logs

    def _host_log(self, entry: Mapping[str, JsonValue]) -> None:
        line: dict[str, JsonValue] = {"t_ns": self._clock.now(), **entry}
        try:
            self._writer.append_json(self._paths.run_dir / HOST_LOG_FILE_NAME, line)
        except OSError:
            self._writer.error(f"cannot write the host log: {entry.get('kind')}")

    def _broker_log(self, plugin_id: str, entry: Mapping[str, JsonValue]) -> None:
        self._host_log({"plugin": plugin_id, **entry})

    def _plugin_log(self, slot: _Slot, entry: Mapping[str, JsonValue]) -> None:
        line: dict[str, JsonValue] = {"t_ns": self._clock.now(), **entry, "plugin": slot.id}
        kind = entry.get("kind")
        slot.last_log = {"at_ns": line["t_ns"], "kind": kind if isinstance(kind, str) else None}
        try:
            self._writer.append_json(slot.out_dir / PLUGIN_LOG_FILE_NAME, line)
        except OSError:
            self._host_log({"kind": "plugin_log_failed", "level": "error", "plugin": slot.id})
        card = entry.get("card")
        if slot.entry.cards and isinstance(card, Mapping):
            self._decision_card(slot, line, card)

    def _decision_card(
        self, slot: _Slot, line: Mapping[str, JsonValue], card: Mapping[str, JsonValue]
    ) -> None:
        """Append a plugin log line's card to ``decision_cards.jsonl`` (module notes)."""
        cleaned = clean_card(card)
        if cleaned is None:
            if not slot.card_invalid:
                slot.card_invalid = True
                self._host_log(
                    {
                        "kind": "card_invalid",
                        "level": "warning",
                        "plugin": slot.id,
                        "detail": "a plugin log card lacks a valid id, title, as_of_ns or action",
                    }
                )
            return
        cleaned = self._enrich_decision_card(cleaned)
        out: dict[str, JsonValue] = {
            "t_ns": line["t_ns"],
            "plugin": slot.id,
            "kind": line.get("kind"),
            "card": cleaned,
        }
        try:
            self._writer.append_json(slot.out_dir / CARDS_FILE_NAME, out)
        except OSError:
            self._host_log({"kind": "card_write_failed", "level": "error", "plugin": slot.id})

    def _enrich_decision_card(self, card: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Attach session bars / dayPct / odds when the store or pattern table has them."""
        out = dict(card)
        instrument = out.get("instrument")
        as_of = out.get("as_of_ns")
        bars_json = out.get("bars")
        if (
            isinstance(instrument, str)
            and instrument
            and isinstance(as_of, int)
            and (not isinstance(bars_json, list) or not bars_json)
        ):
            day = globex_day_at(as_of)
            if day is not None:
                try:
                    store = self._dataplane.bar_store
                    found = store.bars(instrument, day.day)
                except Exception:
                    found = ()
                # Prefer bars closed by as_of; fall back to bars that have opened
                # (decision cards often stamp as_of at bar open / slot time).
                session = tuple(b for b in found if b.close_ns <= as_of)
                if not session:
                    session = tuple(b for b in found if b.open_ns <= as_of)
                ohlc = trade_bars(session, limit=TRADE_BAR_LIMIT)
                if ohlc:
                    out["bars"] = ohlc
                    spot = out.get("spot")
                    if not isinstance(spot, (int, float)):
                        entry = out.get("entry")
                        spot = entry if isinstance(entry, (int, float)) else ohlc[-1]["c"]
                        out["spot"] = float(spot)
                    if out.get("dayPct") is None or out.get("rangePct") is None:
                        day_pct, range_pct = session_day_range_pct(session, float(spot))
                        if day_pct is not None and out.get("dayPct") is None:
                            out["dayPct"] = day_pct
                        if range_pct is not None and out.get("rangePct") is None:
                            out["rangePct"] = range_pct
        if out.get("odds") is None:
            tag = out.get("tag")
            if isinstance(tag, str) and tag:
                odds = odds_for_pattern(tag)
                if odds is not None:
                    out["odds"] = odds
        return out

    # ------------------------------------------------------------ calendars

    def _rth_for(self, bar: Bar) -> tuple[Instant, Instant]:
        cal = self._calendars.exchange.sessions
        day = globex_day_at(bar.open_ns)
        try:
            if day is not None and cal.covers(day.day) and cal.is_session(day.day):
                return cal.rth_open(day.day), cal.rth_close(day.day)
        except ValueError:
            pass
        return bar.open_ns, bar.close_ns

    def _reload_calendars(self, first: date, now: Instant) -> None:
        if self._calendar_loader is None:
            return
        try:
            self._calendars = self._calendar_loader(first, first + timedelta(CALENDAR_DAYS_AHEAD))
        except CalendarError as exc:
            if self._calendar_stale_since is None:
                self._calendar_stale_since = now
            self._host_log(
                {
                    "kind": "calendar_stale",
                    "level": "error",
                    "detail": self._writer.redact(str(exc)),
                }
            )
            return
        if self._calendar_stale_since is not None:
            self._host_log({"kind": "calendar_reloaded", "level": "info"})
        self._calendar_stale_since = None

    # ------------------------------------------------------------ blocks

    def _apply_day_net_reset(self, slot: _Slot) -> None:
        """Consume ``day_net_reset.json``: zero paper day_net; leave max_daily_loss alone."""
        if slot.broker is None or slot.faulted is not None:
            return
        payload = take_day_net_reset(self._gate.plugin_dir(slot.id))
        if payload is None:
            return
        changed = slot.broker.reset_day_net()
        reason = payload.get("reason") if isinstance(payload.get("reason"), str) else ""
        self._host_log(
            {
                "kind": "day_net_reset",
                "level": "info",
                "plugin": slot.id,
                "changed": changed,
                "reason": reason[:200],
            }
        )
        slot.touched = True
        self._persist(slot, force=True)
        self._write_status(slot)

    def _blocked_reasons(self, slot: _Slot, *, caps: bool) -> tuple[str, ...]:
        self._apply_day_net_reset(slot)
        out: list[str] = []
        if slot.faulted is not None:
            out.append("faulted")
        if slot.broker is not None:
            out.extend(self._gate.blocks(slot.id, None if self._day is None else self._day.day))
        if self._calendar_stale_since is not None:
            out.append("calendar_stale")
        out.extend(slot.host_blocks)
        if slot.link is not None:
            out.extend(slot.link.entry_blocks())
        acct = self._account_of(slot)
        if acct is not None:
            out.extend(acct.account.entry_blocks())
            if acct.write_blocked:
                out.append("account_state_write_failed")
        if caps and slot.broker is not None:
            out.extend(slot.broker.cap_blocks())
        if (
            caps
            and acct is not None
            and in_no_trade_window(
                acct.account.rules.no_trade_windows, ny_datetime(self._now).time()
            )
        ):
            out.append("account_no_trade_window")
        seen: dict[str, None] = dict.fromkeys(out)
        return tuple(seen)

    # ------------------------------------------------------------ paper accounts

    def _account_of(self, slot: _Slot) -> _AccountSlot | None:
        return None if slot.account is None else self._accounts.get(slot.account)

    def _account_check(self, slot: _Slot, instrument: str, qty: int) -> str | None:
        """``account_check`` of a member's entry: no-trade window, then the shared cap."""
        acct = self._account_of(slot)
        if acct is None:
            return None
        return acct.account.check_entry(
            instrument, qty, self._exposure(acct), ny_datetime(self._now).time()
        )

    def _exposure(self, acct: _AccountSlot) -> int:
        """Micro_Equivalents over every member book: open plus working-entry contracts."""
        total = 0
        for slot in acct.members:
            broker = slot.broker
            if broker is None:
                continue
            book = broker.book
            for instrument, qty in book.positions().items():
                total += MICRO_EQUIVALENTS[instrument] * abs(qty)
            for instrument, qty in book.working_entry_contracts().items():
                total += MICRO_EQUIVALENTS[instrument] * qty
        return total

    @staticmethod
    def _mark_for(state: PaperAccountState, broker: PluginPaperBroker, trade: OpenTrade) -> Ticks:
        """The account's mark of the trade's instrument, unless the member book's ring
        holds a newer bar (a fresh or lagging account replaying older bars); else the
        book's last ring close; else the entry fill price (open P&L 0)."""
        ring = broker.state.ring.get(trade.instrument, ())
        newest = ring[-1] if ring and ring[-1].c_t is not None else None
        mark = state.marks.get(trade.instrument)
        if mark is not None:
            seen = state.last_bar_open.get(trade.instrument)
            if newest is None or (seen is not None and seen >= newest.open_ns):
                return mark
        if newest is not None and newest.c_t is not None:
            return newest.c_t
        return trade.entry_fill.price

    def _open_pnl(
        self, acct: _AccountSlot, bar: Bar | None, took: set[str] | None = None
    ) -> tuple[Money, Money, Money]:
        """``(worst, best, at_close)`` open P&L over every member book's open trades.

        Trades in ``bar.instrument`` of members in ``took`` (the slots whose book
        processed the bar) are valued at the bar's worst/best price and close;
        every other trade (all of them without a bar) at its mark. A member that
        did not take the bar is already past it, so its trades are never judged
        at that bar's prices.
        """
        worst = best = close = _ZERO
        state = acct.account.state
        for slot in acct.members:
            broker = slot.broker
            if broker is None:
                continue
            at_bar = bar is not None and (took is None or slot.id in took)
            for trade in broker.book.trades.values():
                base = trade.realized
                if (
                    at_bar
                    and bar is not None
                    and bar.c_t is not None
                    and trade.instrument == bar.instrument
                ):
                    worst += base + trade.unrealized(trade.worst_price(bar))
                    best += base + trade.unrealized(trade.best_price(bar))
                    close += base + trade.unrealized(bar.c_t)
                else:
                    at_mark = base + trade.unrealized(self._mark_for(state, broker, trade))
                    worst += at_mark
                    best += at_mark
                    close += at_mark
        return worst, best, close

    def _account_bar(self, bar: Bar, took: set[str]) -> None:
        """Value and check every account a member of which took ``bar`` (after the slots).

        ``took`` holds the ids of the slots whose book processed the bar in this
        pass. A bar no member took (a startup replay bar the books already had)
        is not evaluated: the books' open trades are today's, not that bar's,
        so a fresh or lagging account would judge them at pre-entry prices.
        """
        if not self._accounts or bar.h_t is None or bar.l_t is None or bar.c_t is None:
            return
        for acct in self._accounts.values():
            if not any(m.id in took and bar.instrument in m.instruments for m in acct.members):
                continue
            last = acct.account.state.last_bar_open.get(bar.instrument)
            if last is not None and bar.open_ns <= last:
                continue
            worst, best, close = self._open_pnl(acct, bar, took)
            actions = acct.account.on_bar(
                bar.instrument,
                bar.open_ns,
                worst,
                best,
                close,
                self._now,
                close_t=bar.c_t,
                close_ns=bar.close_ns,
            )
            acct.touched = True
            self._apply_account_actions(acct, actions)

    def _apply_account_actions(self, acct: _AccountSlot, actions: Sequence[AccountAction]) -> None:
        """The one consumer of account actions: log lines and member flattens."""
        for action in actions:
            acct.touched = True
            if isinstance(action, StatusChanged):
                s = acct.account.state
                self._host_log(
                    {
                        "kind": "account_status",
                        "level": "warning" if action.new in ("breached", "flat_locked") else "info",
                        "account": acct.id,
                        "old": action.old,
                        "new": action.new,
                        "reason": action.reason,
                        "balance": str(s.balance),
                        "equity": str(s.balance + s.unrealized),
                        "mll_floor": None if s.mll_floor is None else str(s.mll_floor),
                    }
                )
            elif isinstance(action, FlattenMembers):
                self._flatten_account(acct, action.reason)
            else:
                self._host_log({"kind": action.kind, "level": action.level, **dict(action.detail)})

    def _flatten_account(self, acct: _AccountSlot, reason: str) -> None:
        """Flatten every member at the next open (design 3.5); call inside ``_begin/_end``."""
        plugins = [m.id for m in acct.members if m.broker is not None]
        self._host_log(
            {
                "kind": "account_flatten",
                "level": "warning",
                "account": acct.id,
                "reason": reason,
                "plugins": list(plugins),
            }
        )
        for slot in acct.members:
            broker = slot.broker
            if broker is None:
                continue
            for tag in (*broker.open_tags(), *broker.working_tags()):
                self._note_exit_reason(slot, tag, reason)
            slot.touched = True
            slot.rounds = 0
            updates = broker.route(Flatten(None, reason, "next_open"), self._now)
            self._deliver(slot, updates)

    def _note_exit_reason(self, slot: _Slot, tag: str, reason: str) -> None:
        """The reason of the flatten routed for ``tag`` (the last one routed wins)."""
        slot.exit_reasons[tag] = reason

    def _account_deadline_ns(self, acct: _AccountSlot, day: GlobexDay) -> Instant:
        """``flat_deadline`` NY on ``day``; before an early close, ``min`` with close - offset."""
        rules = acct.account.rules
        deadline = ny_instant(day.day, rules.flat_deadline)
        if day.early_close is not None:
            early = ny_instant(day.day, day.early_close)
            deadline = min(deadline, early - rules.early_close_offset_min * NS_PER_MINUTE)
        return deadline

    def _account_tick(self, now: Instant) -> None:
        """Flatten the members of every account whose flat deadline passed today (once a day)."""
        day = self._day
        if day is None or not self._accounts:
            return
        for acct in self._accounts.values():
            pa = acct.account
            if not pa.rules.flat_deadline_enabled or pa.state.flat_by_done == day.day:
                continue
            if now < self._account_deadline_ns(acct, day):
                continue
            self._begin(now)
            try:
                acct.touched = True
                self._apply_account_actions(acct, pa.flat_by(day.day))
            finally:
                self._end()

    def _account_view(self, acct: _AccountSlot) -> dict[str, JsonValue]:
        flat_by_ns = None if self._day is None else self._account_deadline_ns(acct, self._day)
        return acct.account.view(exposure_micros=self._exposure(acct), flat_by_ns=flat_by_ns)

    def _persist_account(self, acct: _AccountSlot) -> None:
        """Write ``account.json`` unless its bytes (ignoring ``seq``) equal the last write."""
        acct.touched = False
        doc: dict[str, JsonValue] = {
            "version": ACCOUNT_STATE_VERSION,
            "account": ACCOUNT_CODEC.encode(acct.account.state),
            "view": self._account_view(acct),
        }
        body = canonical_json.dumps(doc)
        if body == acct.last_body:
            return
        text = canonical_json.dumps({**doc, "seq": acct.seq + 1}) + "\n"
        try:
            self._writer.write_text(acct.state_path, text)
        except OSError as exc:
            acct.write_failures += 1
            self._host_log(
                {
                    "kind": "account_state_write_failed",
                    "level": "error",
                    "account": acct.id,
                    "failures": acct.write_failures,
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            )
            if acct.write_failures >= STATE_WRITE_FAILURE_LIMIT:
                acct.write_blocked = True
            return
        acct.seq += 1
        acct.last_body = body
        acct.write_failures = 0
        acct.write_blocked = False

    def _restore_account(self, acct: _AccountSlot, now: Instant) -> None:
        """``account.json`` back into the rule engine; missing: fresh; invalid: fail closed."""
        path = acct.state_path
        plugins = tuple(m.id for m in acct.members)
        rules = acct.entry.rules
        acct.touched = True
        if not path.exists():
            acct.account = PaperAccount.fresh(acct.id, rules, plugins)
            self._host_log({"kind": "account_fresh_start", "level": "info", "account": acct.id})
            return
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(obj, dict) or set(obj) != _ACCOUNT_KEYS:
                raise ValueError(f"{path.name} is not an account state document")
            if obj["version"] != ACCOUNT_STATE_VERSION:
                raise ValueError(f"{path.name} has version {obj['version']!r}")
            seq = obj["seq"]
            if isinstance(seq, bool) or not isinstance(seq, int):
                raise ValueError(f"{path.name} has no integer seq")
            state = ACCOUNT_CODEC.decode(obj["account"])
            if not isinstance(state, PaperAccountState):
                raise ValueError(f"{path.name} holds a {type(state).__name__}, not an account")
            if state.id != acct.id:
                raise ValueError(f"{path.name} holds account {state.id!r}")
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            bad = path.with_name(f"{path.name}.bad-{_utc_stamp(now)}")
            with contextlib.suppress(OSError):
                path.replace(bad)
            acct.account = PaperAccount.restore_failed(acct.id, rules, plugins, now)
            self._host_log(
                {
                    "kind": "account_restore_failed",
                    "level": "error",
                    "account": acct.id,
                    "detail": self._writer.redact(f"{type(exc).__name__}: {exc}"),
                    "renamed_to": str(bad),
                }
            )
            return
        acct.account, actions = PaperAccount.restore(state, rules, plugins, now)
        acct.seq = seq
        self._host_log(
            {
                "kind": "account_restored",
                "level": "info",
                "account": acct.id,
                "seq": seq,
                "status": acct.account.state.status,
            }
        )
        self._apply_account_actions(acct, actions)

    # ------------------------------------------------------------ the loop

    async def _loop(self, until_ns: Instant | None) -> None:
        while True:
            while not self._queue.empty():
                self._event(self._queue.get_nowait())
            now = self._clock.now()
            if self._stop or (until_ns is not None and now >= until_ns):
                return
            if self._boundary(now):
                continue
            self._gate_tick(now)
            self._account_tick(now)
            next_wake = self._wake_pass(now)
            if not self._queue.empty():
                continue
            deadline = self._deadline(now, next_wake, until_ns)
            event = await self._wait(deadline - now)
            if event is not None:
                self._event(event)

    async def _wait(self, delay_ns: int) -> MarketEvent | _TaskDone | _Stop | BrokerEvent | None:
        getter = asyncio.ensure_future(self._queue.get())
        sleeper = asyncio.ensure_future(self._clock.sleep(max(0, delay_ns)))
        try:
            await asyncio.wait({getter, sleeper}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (getter, sleeper):
                if not task.done():
                    task.cancel()
            for task in (getter, sleeper):
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        if getter.done() and not getter.cancelled():
            return getter.result()
        return None

    def _deadline(
        self, now: Instant, next_wake: Instant | None, until_ns: Instant | None
    ) -> Instant:
        candidates: list[Instant] = []
        if next_wake is not None:
            candidates.append(next_wake)
        if self._day is not None:
            candidates.append(self._day.rotate_ns)
        elif self._next_open is not None:
            candidates.append(self._next_open.open_ns)
        if any(s.broker is not None and s.broker.has_working_entries() for s in self._slots):
            candidates.append(now + GATE_TICK_NS)
        day = self._day
        if day is not None:
            for acct in self._accounts.values():
                pa = acct.account
                if pa.rules.flat_deadline_enabled and pa.state.flat_by_done != day.day:
                    candidates.append(self._account_deadline_ns(acct, day))
        if self._calendar_stale_since is not None:
            candidates.append(self._calendar_stale_since + CALENDAR_RETRY_NS)
        if until_ns is not None:
            candidates.append(until_ns)
        return max(now + 1, min(candidates)) if candidates else now + CALENDAR_RETRY_NS

    # ------------------------------------------------------------ dispatch steps

    def _begin(self, now: Instant) -> None:
        self._now = now
        for slot in self._slots:
            slot.touched = False
            slot.rounds = 0

    def _end(self) -> None:
        # Accounts before plugins: a crash between the two leaves a book without
        # a close the account already booked, which the replay re-closes and the
        # account's ``applied`` skips (design 3.6).
        for acct in self._accounts.values():
            if acct.touched or any(m.touched for m in acct.members):
                self._persist_account(acct)
        for slot in self._slots:
            if not slot.touched:
                continue
            if slot.broker is not None:
                # the sidecar before plugin.json: a crash between them leaves a
                # reason the restore drops when the book has no such exit
                if slot.faulted is None:
                    self._persist_exit_reasons(slot)
                self._persist(slot)
            self._write_status(slot)
            if slot.link is not None:
                slot.link.flush(self._now)

    # ------------------------------------------------------------ status

    def _note_update(self, slot: _Slot, update: OrderUpdate) -> None:
        """Remember an order update for the plugin's ``status.json`` ``recent`` list."""
        key: SetupKey | None = None
        if update.trade is not None:
            key = update.trade.setup_key
        elif slot.broker is not None:
            key = slot.broker.key_of(update.tag)
        if key is None:
            key = slot.keys.get(update.tag)
        elif update.tag not in slot.keys:
            if len(slot.keys) >= 256:
                slot.keys.clear()
            slot.keys[update.tag] = key
        item = describe_update(update, key)
        if item is not None:
            slot.activity.add(item)
        if update.status == "closed" and update.trade is not None and self._trade_sink:
            self._trade_sink(slot.id, update.trade)

    def _plugin_status(self, slot: _Slot) -> dict[str, JsonValue] | None:
        if slot.faulted is not None:
            return None
        try:
            raw = slot.plugin.status(self._now)
        except Exception as exc:
            self._status_failed(slot, f"{type(exc).__name__}: {exc}")
            return None
        if raw is None:
            return None
        if not isinstance(raw, Mapping) or not _is_json(dict(raw)):
            self._status_failed(slot, f"status() returned {type(raw).__name__}, not a JSON object")
            return None
        return clean_plugin_status(raw)

    def _status_failed(self, slot: _Slot, detail: str) -> None:
        if slot.status_failed:
            return
        slot.status_failed = True
        self._host_log(
            {
                "kind": "status_failed",
                "level": "warning",
                "plugin": slot.id,
                "detail": self._writer.redact(detail),
            }
        )

    def _write_status(self, slot: _Slot) -> None:
        """Write ``status.json`` when anything but its ``updated_ns`` changed."""
        if slot.status_path is None:
            return
        blocks = tuple(r for r in self._blocked_reasons(slot, caps=True) if r != "faulted")
        doc = build_status(
            plugin_id=slot.id,
            label=slot.cls.display_label(),
            module=slot.entry.module,
            brokerage=slot.needs.brokerage if isinstance(slot.needs, DataNeeds) else "host",
            now=self._now,
            faulted=slot.faulted,
            blocks=blocks,
            state=None if slot.broker is None else slot.broker.state,
            day=None if self._day is None else self._day.day.isoformat(),
            next_wake_ns=slot.next_wake,
            recent=slot.activity.items,
            last_log=slot.last_log,
            plugin_status=self._plugin_status(slot),
            exit_fee=None if slot.broker is None else slot.broker.exit_fee_per_contract,
        )
        body = canonical_json.dumps({k: v for k, v in doc.items() if k != "updated_ns"})
        if body == slot.status_body:
            return
        try:
            self._writer.write_text(slot.status_path, canonical_json.dumps(doc) + "\n")
        except OSError as exc:
            self._status_failed(slot, f"cannot write status.json: {type(exc).__name__}")
            return
        slot.status_body = body

    def _refresh_status(self) -> None:
        for slot in self._slots:
            self._apply_day_net_reset(slot)
            self._write_status(slot)

    def _load_activity(self, slot: _Slot, today: date | None) -> None:
        """Keep today's ``recent`` list across a restart (and skip an identical rewrite)."""
        if slot.status_path is None:
            return
        with contextlib.suppress(OSError, ValueError, TypeError):
            obj = json.loads(slot.status_path.read_text(encoding="utf-8"))
            if not isinstance(obj, dict):
                return
            slot.status_body = canonical_json.dumps(
                {k: v for k, v in obj.items() if k != "updated_ns"}
            )
            if today is not None and obj.get("day") == today.isoformat():
                recent = obj.get("recent")
                if isinstance(recent, list):
                    for item in recent:
                        if isinstance(item, dict) and isinstance(item.get("text"), str):
                            slot.activity.add(item)

    def _ctx(self, slot: _Slot) -> _Context:
        return _Context(self, slot, self._now)

    def _event(self, event: MarketEvent | _TaskDone | _Stop | BrokerEvent) -> None:
        if isinstance(event, _Stop):
            self._stop = True
            return
        self._begin(self._clock.now())
        try:
            if isinstance(event, BrokerEvent):
                self._broker_event(event)
                return
            if isinstance(event, _TaskDone):
                slot = self._by_id.get(event.plugin_id)
                if slot is not None and slot.faulted is None:
                    self._chain(slot, "on_wake", slot.plugin.on_wake, self._now, self._ctx(slot))
                return
            try:
                self._dataplane.record(event)
                if isinstance(event, BarsEvent):
                    self._dataplane.store_bars(event.bars, event.received_ns)
            except OSError as exc:
                self.fail("recording_failed", f"{type(exc).__name__}: {exc}")
                self._stop = True
                return
            if isinstance(event, BarsEvent | QuoteEvent):
                self._mark(event)
            if isinstance(event, BarsEvent):
                for bar in event.bars:
                    took = {
                        slot.id
                        for slot in self._slots
                        if self._deliver_bar(slot, bar, event.received_ns, catch_up=False)
                    }
                    self._account_bar(bar, took)
            elif isinstance(event, SnapshotsEvent):
                self._snapshots(event)
            elif isinstance(event, DarkPoolEvent):
                for slot in self._slots:
                    sky = slot.needs.skylit
                    if sky is not None and event.ticker in sky.dark_pool_tickers:
                        self._chain(
                            slot,
                            "on_dark_pool",
                            slot.plugin.on_dark_pool,
                            event.ticker,
                            event.prints,
                            event.received_ns,
                            self._ctx(slot),
                        )
            elif isinstance(event, QuoteEvent):
                quote = event.quote
                self._quotes[quote.instrument] = quote
                for slot in self._slots:
                    if slot.needs.quotes and quote.instrument in slot.instruments:
                        self._chain(slot, "on_quote", slot.plugin.on_quote, quote, self._ctx(slot))
            else:
                self._feed_status(event)
        finally:
            self._end()

    def _mark(self, event: BarsEvent | QuoteEvent) -> None:
        """Update the live marks (``marks.json``); never faults anything."""
        marks = self._marks
        if marks is None:
            return
        if isinstance(event, QuoteEvent):
            marks.quote(event.quote)
        else:
            for bar in event.bars:
                marks.bar(bar)
        marks.maybe_write(self._now)

    def _snapshots(self, event: SnapshotsEvent) -> None:
        wall = ny_datetime(event.received_ns).time()
        for slot in self._slots:
            sky = slot.needs.skylit
            if sky is None:
                continue
            start, end = sky.window
            if not start <= wall < end:
                continue
            batch = tuple(s for s in event.snaps if s.symbol in sky.symbols)
            if batch:
                self._chain(
                    slot,
                    "on_snapshots",
                    slot.plugin.on_snapshots,
                    batch,
                    event.received_ns,
                    self._ctx(slot),
                )

    def _feed_status(self, event: FeedStatusEvent) -> None:
        for slot in self._slots:
            n = slot.needs
            wanted = (
                event.feed == "bars"
                or (event.feed == "quotes" and n.quotes)
                or (event.feed == "skylit" and n.skylit is not None)
                or (
                    event.feed == "dark_pool"
                    and n.skylit is not None
                    and bool(n.skylit.dark_pool_tickers)
                )
            )
            if wanted:
                self._chain(
                    slot,
                    "on_feed_status",
                    slot.plugin.on_feed_status,
                    event.feed,
                    event.up,
                    event.detail,
                    self._ctx(slot),
                )

    def _last_bar_open(self, slot: _Slot, instrument: str) -> Instant | None:
        if slot.broker is not None:
            return slot.broker.book.last_bar_open.get(instrument)
        return slot.last_seen.get(instrument)

    def _deliver_bar(self, slot: _Slot, bar: Bar, received_ns: Instant, *, catch_up: bool) -> bool:
        """Dispatch ``bar`` to ``slot``; True when the slot took it (not a replay duplicate)."""
        if bar.instrument not in slot.instruments:
            return False
        last = self._last_bar_open(slot, bar.instrument)
        if last is not None and bar.open_ns <= last:
            return False
        slot.rounds = 0
        link = slot.link
        if link is not None and catch_up:
            link.catching_up = True
        try:
            if slot.broker is not None:
                slot.touched = True
                updates = slot.broker.on_bar(bar)
                self._deliver(slot, updates)
            else:
                slot.last_seen[bar.instrument] = bar.open_ns
            self._chain(
                slot, "on_bar", slot.plugin.on_bar, bar, received_ns, catch_up, self._ctx(slot)
            )
        finally:
            if link is not None:
                link.catching_up = False
        return True

    def _call(self, slot: _Slot, hook: str, fn: Callable[..., object], *args: object) -> object:
        """Call one hook with the latency warning; an exception faults the plugin."""
        if slot.faulted is not None:
            return None
        slot.touched = True
        start = self._perf_ns()
        try:
            return fn(*args)
        except Exception as exc:
            self._fault(slot, hook, exc=exc)
            return None
        finally:
            elapsed = self._perf_ns() - start
            if elapsed > HOOK_LATENCY_WARN_NS:
                self._host_log(
                    {
                        "kind": "hook_latency",
                        "level": "warning",
                        "plugin": slot.id,
                        "hook": hook,
                        "elapsed_ms": elapsed // 1_000_000,
                    }
                )

    def _intents(self, slot: _Slot, hook: str, out: object) -> tuple[Intent, ...]:
        if slot.faulted is not None or out is None:
            return ()
        if not isinstance(out, Sequence) or isinstance(out, str | bytes):
            self._fault(slot, hook, reason="bad_intents", detail=f"{type(out).__name__}")
            return ()
        items = tuple(out)
        for item in items:
            if not isinstance(item, EnterMarket | EnterResting | Flatten | Cancel | ModifyStop):
                self._fault(slot, hook, reason="bad_intents", detail=type(item).__name__)
                return ()
        return items

    def _chain(self, slot: _Slot, hook: str, fn: Callable[..., object], *args: object) -> None:
        """Call a hook, route its intents and deliver every resulting update (intent rounds)."""
        if slot.faulted is not None:
            return
        slot.rounds = 0
        intents = self._intents(slot, hook, self._call(slot, hook, fn, *args))
        if intents:
            self._route(slot, intents)

    def _route(self, slot: _Slot, intents: Sequence[Intent]) -> None:
        if slot.broker is None:
            self._host_log(
                {
                    "kind": "intents_ignored",
                    "level": "warning",
                    "plugin": slot.id,
                    "detail": "a self-brokered plugin's intents are not routed",
                    "intents": [type(i).__name__ for i in intents],
                }
            )
            return
        slot.last_intents = [type(i).__name__ for i in intents]
        updates: list[OrderUpdate] = []
        for intent in intents:
            updates.extend(self._route_one(slot, intent))
        self._deliver(slot, updates)

    def _route_one(self, slot: _Slot, intent: Intent) -> list[OrderUpdate]:
        broker = slot.broker
        assert broker is not None
        slot.touched = True
        entry = isinstance(intent, EnterMarket | EnterResting)
        blocks = self._blocked_reasons(slot, caps=False) if entry else ()
        quote = self._quotes.get(intent.instrument) if isinstance(intent, EnterMarket) else None
        if isinstance(intent, Flatten) and REASON_RE.fullmatch(intent.reason) is not None:
            tags = (
                (intent.tag,)
                if intent.tag is not None
                else (*broker.open_tags(), *broker.working_tags())
            )
            for tag in tags:
                self._note_exit_reason(slot, tag, intent.reason)
        if entry and slot.account is not None:
            check = partial(self._account_check, slot)
            updates = broker.route(
                intent, self._now, blocks=blocks, quote=quote, account_check=check
            )
        else:
            updates = broker.route(intent, self._now, blocks=blocks, quote=quote)
        for u in updates:
            if u.status == "rejected":
                invalid = (
                    u.reason not in blocks
                    and u.reason not in _CAP_REASONS
                    and u.reason not in ACCOUNT_REASONS
                    and u.reason not in _BROKER_STATE_REASONS
                )
                self._plugin_log(
                    slot,
                    {
                        "kind": "intent_rejected",
                        "level": "warning" if invalid else "info",
                        "tag": u.tag,
                        "reason": u.reason,
                        "intent": type(intent).__name__,
                    },
                )
            elif u.status == "accepted":
                self._plugin_log(
                    slot,
                    {
                        "kind": "intent_accepted",
                        "level": "info",
                        "tag": u.tag,
                        "intent": type(intent).__name__,
                        "detail": dict(u.detail) if u.detail is not None else None,
                    },
                )
        return updates

    def _record(self, slot: _Slot, updates: Sequence[OrderUpdate]) -> None:
        """Book every update the host produced, before any reaches the plugin.

        Called first in :meth:`_deliver` and on each routed intent's updates in
        its loop, so every ``OrderUpdate`` passes here exactly once. A close
        books to the plugin's account; the open P&L is then revalued at the
        marks, so equity never counts a closed trade twice.
        """
        acct = self._account_of(slot)
        booked = False
        for u in updates:
            self._journal(slot, u, updates)
            if u.status != "closed":
                continue
            if acct is not None and u.trade is not None:
                self._apply_account_actions(acct, acct.account.on_closed(u.trade, u.at))
                booked = True
            slot.exit_reasons.pop(u.tag, None)
        if booked and acct is not None:
            acct.account.revalue(self._open_pnl(acct, None)[2])
            acct.touched = True
        if acct is not None and self._mirror is not None and self._mirror.mirrors(acct.id):
            # Nexus never gates the plugin or its broker orders: a raise here
            # would skip the routed intent's ``link.flush`` (ProjectX order).
            try:
                self._mirror_batch(slot, acct.id, self._mirror, updates)
            except Exception as exc:
                with contextlib.suppress(Exception):
                    self._mirror.enqueue_failed(acct.id, exc)

    def _mirror_batch(
        self, slot: _Slot, acct_id: str, mirror: NexusMirror, updates: Sequence[OrderUpdate]
    ) -> None:
        """Enqueue the batch's fills for Nexus; a build failure is logged, never raised."""
        broker = slot.broker
        if broker is None:
            return
        try:
            sessions = self._calendars.exchange.sessions
            fills: list[MirrorFill] = []
            for u in updates:
                if u.status not in ("filled_entry", "filled_exit") or u.fill is None:
                    continue
                key = self._setup_key(broker, u.tag, updates)
                if key is None:
                    raise ValueError(f"no Setup_Key for the fill of {u.tag!r}")
                fills.append(
                    MirrorFill(
                        plugin=slot.id,
                        tag=u.tag,
                        leg=u.role if u.role is not None else "exit",
                        entry=u.status == "filled_entry",
                        instrument=key.instrument,
                        direction=key.direction,
                        qty=u.fill.qty,
                        price_t=u.fill.price,
                        fill_ns=u.fill.bar_open_ns,
                        day=journal_day(broker.state.day, u.at, sessions).day,
                    )
                )
            positions = broker.book.positions()
        except Exception as exc:
            mirror.enqueue_failed(acct_id, exc)
            return
        mirror.enqueue_fills(acct_id, fills, positions)

    # ------------------------------------------------------------ trade journal

    @staticmethod
    def _setup_key(
        broker: PluginPaperBroker, tag: str, batch: Sequence[OrderUpdate]
    ) -> SetupKey | None:
        """The Setup_Key of ``tag``: its close in this batch, else the book."""
        for u in batch:
            if u.tag == tag and u.trade is not None:
                return u.trade.setup_key
        book = broker.book
        for key in (*book.trades, *book.brackets, *book.market_brackets):
            if tag_of(key) == tag:
                return key
        for working in book.orders.values():
            placed = working.placed.setup_key
            if placed is not None and tag_of(placed) == tag:
                return placed
        return None

    def _journal(self, slot: _Slot, u: OrderUpdate, batch: Sequence[OrderUpdate]) -> None:
        """Append the update's journal lines; any failure is logged once, never raised."""
        broker = slot.broker
        if broker is None or u.status not in ("filled_entry", "filled_exit", "closed"):
            return
        try:
            day = journal_day(broker.state.day, u.at, self._calendars.exchange.sessions)
            view = JournalSlot(slot.id, slot.account, slot.exit_reasons)
            key = None if u.status == "closed" else self._setup_key(broker, u.tag, batch)
            for line in journal_lines(view, u, day, key):
                self._writer.append_json(slot.journal_path, line)
        except Exception as exc:
            self._journal_failed(slot, exc)
            return
        self._journal_ok(slot)

    def _journal_failed(self, slot: _Slot, exc: Exception) -> None:
        if slot.journal_failed:
            return
        slot.journal_failed = True
        self._host_log(
            {
                "kind": "journal_write_failed",
                "level": "error",
                "plugin": slot.id,
                "detail": type(exc).__name__,
            }
        )

    def _journal_ok(self, slot: _Slot) -> None:
        if not slot.journal_failed:
            return
        slot.journal_failed = False
        self._host_log({"kind": "journal_write_recovered", "level": "info", "plugin": slot.id})

    def _touch_journal(self, slot: _Slot) -> None:
        """Create ``trades.jsonl`` at startup: its presence marks a journal-enabled run."""
        try:
            self._writer.touch(slot.journal_path)
        except Exception as exc:
            self._journal_failed(slot, exc)

    # ------------------------------------------------------------ exit reasons sidecar

    def _persist_exit_reasons(self, slot: _Slot) -> None:
        """Write ``exit_reasons.json`` unless its bytes equal the last write (never blocks)."""
        doc: dict[str, JsonValue] = {
            "version": EXIT_REASONS_VERSION,
            "exit_reasons": dict(slot.exit_reasons),
        }
        body = canonical_json.dumps(doc)
        if body == slot.reasons_last_body:
            return
        try:
            self._writer.write_text(slot.reasons_path, body + "\n")
        except OSError as exc:
            if not slot.reasons_write_failed:
                slot.reasons_write_failed = True
                self._host_log(
                    {
                        "kind": "exit_reasons_write_failed",
                        "level": "warning",
                        "plugin": slot.id,
                        "detail": f"{type(exc).__name__}: {exc}",
                    }
                )
            return
        slot.reasons_last_body = body
        slot.reasons_write_failed = False

    def _read_exit_reasons(self, path: Path) -> dict[str, str]:
        obj = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(obj, dict) or set(obj) != {"version", "exit_reasons"}:
            raise ValueError(f"{path.name} is not an exit reasons document")
        if obj["version"] != EXIT_REASONS_VERSION:
            raise ValueError(f"{path.name} has version {obj['version']!r}")
        reasons = obj["exit_reasons"]
        if not isinstance(reasons, dict):
            raise ValueError(f"{path.name}: exit_reasons is not an object")
        out: dict[str, str] = {}
        for tag, reason in reasons.items():
            if not isinstance(reason, str) or not REASON_RE.match(reason):
                raise ValueError(f"{path.name}: bad reason for tag {tag!r}")
            out[str(tag)] = reason
        return out

    def _restore_exit_reasons(self, slot: _Slot, now: Instant) -> None:
        """Reasons of the restored book's pending exits; ``pending_exit_restart`` when lost.

        Kept: tags with a working ``exit`` order or an open trade. A working exit
        with no kept reason (no sidecar, an unreadable one, a pre-feature state
        dir) gets ``pending_exit_restart``.
        """
        broker = slot.broker
        assert broker is not None
        path = slot.reasons_path
        loaded: dict[str, str] = {}
        if path.exists():
            try:
                loaded = self._read_exit_reasons(path)
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                with contextlib.suppress(OSError):
                    path.replace(path.with_name(f"{path.name}.bad-{_utc_stamp(now)}"))
                self._host_log(
                    {
                        "kind": "exit_reasons_restore_failed",
                        "level": "warning",
                        "plugin": slot.id,
                        "detail": self._writer.redact(f"{type(exc).__name__}: {exc}"),
                    }
                )
        exiting = [
            tag_of(order.setup_key)
            for working in broker.book.orders.values()
            if (order := working.latest) is not None
            and order.role == "exit"
            and order.setup_key is not None
        ]
        live = set(broker.open_tags()) | set(exiting)
        kept = {t: r for t, r in loaded.items() if t in live}
        for tag in exiting:
            kept.setdefault(tag, PENDING_EXIT_RESTART)
        slot.exit_reasons = kept

    def _deliver(self, slot: _Slot, updates: Sequence[OrderUpdate]) -> None:
        self._record(slot, updates)
        fifo: deque[OrderUpdate] = deque(updates)
        while fifo and slot.faulted is None:
            slot.rounds += 1
            if slot.rounds > MAX_INTENT_ROUNDS:
                self._fault(
                    slot,
                    "on_order_update",
                    reason="intent_loop",
                    detail=",".join(slot.last_intents),
                )
                return
            batch = list(fifo)
            fifo.clear()
            for update in batch:
                self._note_update(slot, update)
                out = self._call(
                    slot, "on_order_update", slot.plugin.on_order_update, update, self._ctx(slot)
                )
                intents = self._intents(slot, "on_order_update", out)
                if _is_faulted(slot):
                    return
                if intents:
                    slot.last_intents = [type(i).__name__ for i in intents]
                for intent in intents:
                    routed = self._route_one(slot, intent)
                    self._record(slot, routed)
                    fifo.extend(routed)

    # ------------------------------------------------------------ wakes and gate

    def _next_wake(self, slot: _Slot, now: Instant) -> Instant | None:
        out = self._call(slot, "next_wake", slot.plugin.next_wake, now)
        if out is None or slot.faulted is not None:
            return None
        if isinstance(out, bool) or not isinstance(out, int):
            self._fault(slot, "next_wake", reason="bad_wake", detail=type(out).__name__)
            return None
        return out

    def _wake_pass(self, now: Instant) -> Instant | None:
        """``on_wake`` for each plugin due now (one call each); the earliest later wake."""
        later: list[Instant] = []
        for slot in self._slots:
            if slot.faulted is not None:
                continue
            self._now = now
            wake = self._next_wake(slot, now)
            if wake is not None and wake <= now:
                self._begin(now)
                try:
                    self._chain(slot, "on_wake", slot.plugin.on_wake, now, self._ctx(slot))
                finally:
                    self._end()
                wake = self._next_wake(slot, now) if slot.faulted is None else None
            planned = wake if wake is not None and wake > now else None
            if planned != slot.next_wake:
                slot.next_wake = planned
                self._write_status(slot)
            if wake is not None and wake > now:
                later.append(wake)
        return min(later) if later else None

    def _gate_tick(self, now: Instant) -> None:
        """Cancel working entries of host-brokered plugins while a block is in force."""
        for slot in self._slots:
            broker = slot.broker
            if broker is None or not broker.has_working_entries():
                continue
            reasons = self._blocked_reasons(slot, caps=False)
            # broker link kinds are transient: they refuse new entries, never cancel
            reasons = tuple(r for r in reasons if r != "faulted" and r not in LINK_BLOCK_KINDS)
            if not reasons:
                continue
            self._begin(now)
            try:
                slot.touched = True
                updates = broker.cancel_entries(now, reasons[0])
                self._deliver(slot, updates)
            finally:
                self._end()

    # ------------------------------------------------------------ tasks

    def _track(self, plugin_id: str, name: str, task: asyncio.Task[Any], *, spawned: bool) -> None:
        slot = self._by_id.get(plugin_id)
        if slot is not None:
            slot.tasks.add(task)

        def done(t: asyncio.Task[Any]) -> None:
            if slot is not None:
                slot.tasks.discard(t)
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None and spawned:
                self._host_log(
                    {
                        "kind": "spawned_task_failed",
                        "level": "error",
                        "plugin": plugin_id,
                        "task": name,
                        "traceback": self._writer.format_exception(exc),
                    }
                )
                self._exit_code = EXIT_FAULT
            self._queue.put_nowait(_TaskDone(plugin_id, name))

        task.add_done_callback(done)

    def _cancel_tasks(self, slot: _Slot) -> None:
        for task in list(slot.tasks):
            task.cancel()
        slot.tasks.clear()

    # ------------------------------------------------------------ faults

    def _fault(
        self,
        slot: _Slot,
        hook: str,
        *,
        exc: BaseException | None = None,
        reason: str = "exception",
        detail: str | None = None,
    ) -> None:
        if slot.faulted is not None:
            return
        slot.faulted = reason
        self._exit_code = EXIT_FAULT
        entry: dict[str, JsonValue] = {
            "kind": "plugin_faulted",
            "level": "error",
            "plugin": slot.id,
            "hook": hook,
            "reason": reason,
        }
        if detail is not None:
            entry["detail"] = detail
        if exc is not None:
            entry["error"] = self._writer.redact(f"{type(exc).__name__}: {exc}")
            entry["traceback"] = self._writer.format_exception(exc)
        self._host_log(entry)
        self._cancel_tasks(slot)
        slot.next_wake = None
        if slot.broker is not None:
            slot.touched = True
            slot.broker.cancel_entries(self._now, "faulted")
        self._write_status(slot)

    # ------------------------------------------------------------ persistence

    def _document(self, slot: _Slot, snapshot: JsonValue) -> dict[str, JsonValue]:
        broker = slot.broker
        assert broker is not None
        return {
            "version": PLUGIN_STATE_VERSION,
            "book": BROKER_CODEC.encode(broker.state),
            "plugin": snapshot,
        }

    def _persist(self, slot: _Slot, *, force: bool = False) -> None:
        if slot.faulted is not None or slot.broker is None:
            return
        try:
            snapshot = slot.plugin.snapshot()
        except Exception as exc:
            self._fault(slot, "snapshot", exc=exc)
            return
        if not _is_json(snapshot):
            self._fault(
                slot, "snapshot", reason="snapshot_not_json", detail=type(snapshot).__name__
            )
            return
        # The bar ring and last_bar_open change on every 1-minute bar, so a
        # byte compare never hits. A replay still fills and calls hooks the
        # same way; it writes the final book at shutdown (force=True).
        if self._coalesce_state and not force:
            return
        doc = self._document(slot, snapshot)
        body = canonical_json.dumps(doc)
        if body == slot.last_body:
            return
        text = canonical_json.dumps({**doc, "seq": slot.seq + 1}) + "\n"
        try:
            self._writer.write_text(slot.state_path, text)
        except OSError as exc:
            slot.write_failures += 1
            self._host_log(
                {
                    "kind": "state_write_failed",
                    "level": "error",
                    "plugin": slot.id,
                    "failures": slot.write_failures,
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            )
            if (
                slot.write_failures >= STATE_WRITE_FAILURE_LIMIT
                and "state_write_failed" not in slot.host_blocks
            ):
                slot.host_blocks.append("state_write_failed")
            return
        slot.seq += 1
        slot.last_body = body
        slot.write_failures = 0
        if "state_write_failed" in slot.host_blocks:
            slot.host_blocks.remove("state_write_failed")

    def _restore(self, slot: _Slot, now: Instant) -> None:
        broker = slot.broker
        assert broker is not None
        path = slot.state_path
        if not path.exists():
            self._host_log({"kind": "plugin_fresh_start", "level": "info", "plugin": slot.id})
            return
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(obj, dict) or set(obj) != _STATE_KEYS:
                raise ValueError(f"{path.name} is not a plugin state document")
            if obj["version"] != PLUGIN_STATE_VERSION:
                raise ValueError(f"{path.name} has version {obj['version']!r}")
            seq = obj["seq"]
            if isinstance(seq, bool) or not isinstance(seq, int):
                raise ValueError(f"{path.name} has no integer seq")
            state = BROKER_CODEC.decode(obj["book"])
            if not isinstance(state, PluginBrokerState):
                raise ValueError(f"{path.name} holds a {type(state).__name__}, not a book")
            plugin_half: JsonValue = obj["plugin"]
            slot.plugin.restore(plugin_half)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            self._restore_failed(slot, now, f"{type(exc).__name__}: {exc}")
            return
        except Exception as exc:
            self._fault(slot, "restore", exc=exc)
            return
        broker.restore(state)
        slot.seq = seq
        slot.last_body = canonical_json.dumps(
            {"version": PLUGIN_STATE_VERSION, "book": obj["book"], "plugin": plugin_half}
        )
        self._host_log({"kind": "plugin_restored", "level": "info", "plugin": slot.id, "seq": seq})

    def _restore_failed(self, slot: _Slot, now: Instant, reason: str) -> None:
        path = slot.state_path
        bad = path.with_name(f"{path.name}.bad-{_utc_stamp(now)}")
        with contextlib.suppress(OSError):
            path.replace(bad)
        slot.plugin = self._construct(slot.entry, slot.cls, slot.services)
        assert slot.broker is not None
        slot.broker.restore(PluginBrokerState())
        slot.seq = 0
        slot.last_body = None
        reason = self._writer.redact(reason)
        self._gate.plugin_blocks(slot.id).add(BlockRecord("restore_failed", None, reason, now))
        self._host_log(
            {
                "kind": "restore_failed",
                "level": "error",
                "plugin": slot.id,
                "detail": reason,
                "renamed_to": str(bad),
            }
        )

    # ------------------------------------------------------------ startup and days

    def _startup(self) -> None:
        now = self._clock.now()
        self._now = now
        cal = self._calendars.exchange.sessions
        self._day = globex_day_at(now, cal)
        self._next_open = next_open(now, cal) if self._day is None else None
        for slot in self._slots:
            self._host_log(
                {
                    "kind": "plugin_loaded",
                    "level": "info",
                    "plugin": slot.id,
                    "module": slot.entry.module,
                    "brokerage": slot.needs.brokerage,
                    "hold_through_break": slot.hold,
                }
            )
        today = None if self._day is None else self._day.day
        used = set(self._accounts)
        for entry in self._cfg.accounts:
            if entry.id not in used:
                self._host_log({"kind": "account_unused", "level": "info", "account": entry.id})
        # 5. restore (accounts first), then on_start
        for acct in self._accounts.values():
            self._restore_account(acct, now)
        if self._mirror is not None:
            self._mirror.load()  # before any replayed fill is enqueued
        for slot in self._slots:
            self._load_activity(slot, today)
            if slot.broker is not None:
                self._touch_journal(slot)
                self._restore(slot, now)
                self._restore_exit_reasons(slot, now)
                if slot.broker.state.day != today:
                    slot.broker.start_day(today)
                # Operator request written while stopped (or mid-run before restart).
                self._apply_day_net_reset(slot)
        for acct in self._accounts.values():
            # Value the restored books (a fresh account has no marks yet: ring
            # close, else entry price). Then a day closed while the runner was
            # down closes now; a restart in the 17:00-18:00 break is still on
            # that day (a no-op).
            acct.account.revalue(self._open_pnl(acct, None)[2])
            self._apply_account_actions(acct, acct.account.roll(today, now))
            if today is not None:
                acct.account.open_day(today)
            acct.touched = True
        for slot in self._slots:
            if slot.entry.execution.order_mode != "paper" and slot.broker is not None:
                self._order_mode_card(slot, now)
                if self._day is not None:
                    self._open_broker_day(slot, self._day, now)
        for slot in self._slots:
            self._begin(now)
            try:
                self._chain(slot, "on_start", slot.plugin.on_start, self._ctx(slot))
            finally:
                self._end()
        # 6. repair replay of today's recorded bars; bar-major with accounts, so
        # a shared account sees every member's book at the same bar
        if self._day is not None and self._accounts:
            for bar, received in tuple(self._dataplane.recorded_bars(self._day)):
                self._begin(now)
                try:
                    took = {
                        slot.id
                        for slot in self._slots
                        if slot.broker is not None
                        and self._deliver_bar(slot, bar, received, catch_up=True)
                    }
                    self._account_bar(bar, took)
                finally:
                    self._end()
        elif self._day is not None:
            recorded = tuple(self._dataplane.recorded_bars(self._day))
            for slot in self._slots:
                if slot.broker is None:
                    continue
                for bar, received in recorded:
                    self._begin(now)
                    try:
                        self._deliver_bar(slot, bar, received, catch_up=True)
                    finally:
                        self._end()
        # 7. stale positions
        current = (
            today
            if today is not None
            else (self._next_open.day if self._next_open is not None else None)
        )
        for slot in self._slots:
            broker = slot.broker
            oldest = None if broker is None else broker.oldest_trade_session()
            if broker is None or oldest is None or current is None or oldest >= current:
                continue
            self._host_log(
                {
                    "kind": "restart_stale",
                    "level": "error",
                    "plugin": slot.id,
                    "trade_session": oldest.isoformat(),
                }
            )
            self._begin(now)
            try:
                slot.touched = True
                for tag in (*broker.open_tags(), *broker.working_tags()):
                    self._note_exit_reason(slot, tag, "restart_stale")
                updates = broker.route(Flatten(None, "restart_stale", "last_close"), now)
                self._deliver(slot, updates)
            finally:
                self._end()
        # 8. on_day_open(today)
        if self._day is not None:
            self._open_day(self._day, now)

    def _boundary(self, now: Instant) -> bool:
        """Rotate or open a Globex day when due; whether anything happened."""
        day = self._day
        if day is not None and now >= day.rotate_ns:
            nxt = next_globex_day(day)
            self._rotate(day, nxt if nxt.open_ns == day.rotate_ns else None, now)
            self._day = None
            self._next_open = nxt
            self._refresh_status()
            return True
        if day is None and self._next_open is not None and now >= self._next_open.open_ns:
            cal = self._calendars.exchange.sessions
            found = globex_day_at(now, cal)
            if found is None:
                self._next_open = next_open(now, cal)
                return True
            self._next_open = None
            self._day = found
            self._open_day(found, now)
            return True
        if (
            self._calendar_stale_since is not None
            and now >= self._calendar_stale_since + CALENDAR_RETRY_NS
        ):
            first = (self._day or self._next_open or globex_day_at(now) or next_open(now)).day
            self._calendar_stale_since = now
            self._reload_calendars(first, now)
            return True
        return False

    def _rotate(self, closing: GlobexDay, opening: GlobexDay | None, now: Instant) -> None:
        # 1. on_day_close
        for slot in self._slots:
            self._begin(now)
            try:
                self._chain(
                    slot, "on_day_close", slot.plugin.on_day_close, closing, self._ctx(slot)
                )
            finally:
                self._end()
        # 2. backstop, then the day resets
        next_day = None if opening is None else opening.day
        for slot in self._slots:
            broker = slot.broker
            if broker is None:
                continue
            self._begin(now)
            try:
                slot.touched = True
                if not slot.hold and broker.has_exposure():
                    self._host_log(
                        {
                            "kind": "host_break_backstop",
                            "level": "error",
                            "plugin": slot.id,
                            "open_tags": list(broker.open_tags()),
                            "working_tags": list(broker.working_tags()),
                        }
                    )
                    for tag in (*broker.open_tags(), *broker.working_tags()):
                        self._note_exit_reason(slot, tag, "host_break_backstop")
                    updates = broker.route(Flatten(None, "host_break_backstop", "last_close"), now)
                    self._deliver(slot, updates)
                broker.start_day(next_day)
            finally:
                self._end()
        # 2b. every member's backstop close booked to the closing day: roll accounts
        if self._accounts:
            self._begin(now)
            try:
                for acct in self._accounts.values():
                    acct.touched = True
                    self._apply_account_actions(acct, acct.account.roll(next_day, now))
            finally:
                self._end()
        # 2c. lift Nexus account_locked blocks, then archive each fully acked
        #     outbox (synchronous: no enqueue interleaves)
        if self._mirror is not None:
            self._mirror.new_day(closing.day)
            self._mirror.archive(closing.day)
        # 3 and 5. recordings and the next day's contracts
        self._dataplane.rotate(closing, opening)
        # 4. calendars for the next week
        self._reload_calendars(closing.day + timedelta(days=1), now)

    def _open_day(self, day: GlobexDay, now: Instant) -> None:
        day = globex_day(day.day, self._calendars.exchange.sessions)
        self._day = day
        self._host_log(
            {
                "kind": "day_open",
                "level": "info",
                "day": day.day.isoformat(),
                "exchange_session": day.exchange_session,
            }
        )
        for slot in self._slots:
            if slot.broker is not None and slot.broker.state.day != day.day:
                self._begin(now)
                try:
                    slot.touched = True
                    slot.broker.start_day(day.day)
                finally:
                    self._end()
            if slot.link is not None:
                self._open_broker_day(slot, day, now)
        if self._accounts:
            self._begin(now)
            try:
                for acct in self._accounts.values():
                    acct.account.open_day(day.day)
                    acct.touched = True
            finally:
                self._end()
        for slot in self._slots:
            self._begin(now)
            try:
                self._chain(slot, "on_day_open", slot.plugin.on_day_open, day, self._ctx(slot))
            finally:
                self._end()
        urgent: list[tuple[str, Instant]] = []
        for slot in self._slots:
            out = self._call(slot, "urgent_bar_closes", slot.plugin.urgent_bar_closes, day)
            if not isinstance(out, Sequence):
                continue
            for item in out:
                if (
                    isinstance(item, tuple)
                    and len(item) == 2
                    and isinstance(item[0], str)
                    and isinstance(item[1], int)
                ):
                    urgent.append((item[0], item[1]))
        self._dataplane.set_urgent(sorted(set(urgent), key=lambda x: (x[1], x[0])))

    # ------------------------------------------------------------ shutdown

    def _shutdown(self) -> None:
        for slot in self._slots:
            if slot.link is not None:
                slot.link.stopping = True  # a replayed bar never creates a broker entry
        while not self._queue.empty():
            event = self._queue.get_nowait()
            if not isinstance(event, _Stop):
                self._event(event)
        now = self._clock.now()
        self._now = now
        for slot in self._slots:
            if slot.faulted is None:
                self._call(slot, "on_stop", slot.plugin.on_stop, self._ctx(slot))
            self._cancel_tasks(slot)
        for slot in self._slots:
            if slot.link is not None:
                slot.link.flush(now)
        for acct in self._accounts.values():
            self._persist_account(acct)
        for slot in self._slots:
            if slot.broker is not None:
                if slot.faulted is None:
                    self._persist_exit_reasons(slot)
                self._persist(slot, force=True)
        self._dataplane.close()
        self._host_log({"kind": "host_stopped", "level": "info", "exit_code": self._exit_code})
