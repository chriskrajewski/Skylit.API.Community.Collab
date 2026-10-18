"""The strategy plugin interface of ``fse run`` (design 3).

A plugin is a :class:`StrategyPlugin` subclass named in the runner YAML. The
host constructs it with its id, its opaque ``config`` block and its
:class:`PluginServices`, asks for its :meth:`~StrategyPlugin.needs` once, and
then calls its hooks from one dispatcher task. Hooks **return** intents
(:data:`Intent`) instead of calling a submit method, so a plugin is a plain
function of its inputs and can be unit tested without a host. Intents: enter
(:class:`EnterMarket`, :class:`EnterResting`), :class:`Flatten`,
:class:`Cancel` an entry, and :class:`ModifyStop` (tighten an open trade's
stop from the next bar on). A host-brokered plugin reads its runner blocks
from ``services.fills`` (:class:`PaperFills`), ``services.risk``
(:class:`PaperRisk`) and ``services.broker_terms`` (:class:`BrokerTerms`, both
flattened), available in ``__init__``.

**Rules every plugin follows** (the host can only detect violations):

- Hooks are synchronous and must return within 1 s; slower calls are logged
  as ``hook_latency`` warnings. No network I/O, no CPU-heavy preparation, and
  no file I/O beyond the plugin's own small state/output files and local bar
  reads covered by a perf test. Anything else goes through
  :meth:`PluginServices.run_blocking`; async work through
  :meth:`PluginServices.spawn`. When such a task finishes the host calls
  :meth:`StrategyPlugin.on_wake` once on the dispatcher, where the plugin
  reads ``task.result()``. A plugin never awaits and never calls ``result()``
  on an unfinished task.
- **Thread confinement.** Objects wrapping a sqlite connection
  (``fse.data.cache.DataCache``, ``fse.data.catalog.Catalog``) are confined to
  the thread that created them. A ``run_blocking`` job opens and closes its
  own instance inside ``fn`` and returns plain data only; an instance the
  plugin keeps for its dispatcher-side reads is opened in a hook (e.g.
  ``on_start``) and closed in ``on_stop``.
- ``needs()`` is constant for the plugin's lifetime.

Market types (``Bar``, ``Fill``, ``Trade``, ``Snapshot``, ``DarkPoolPrint``)
are re-exported from :mod:`fse.live.plugins`, so a plugin never names a
vendor client module.

**Decision cards.** A plugin publishes a decision (an entry, a skip, a hold,
an exit, a trade result, or plain info) to the console's Finding Cards page by
logging a line with a ``"card"`` object built by :func:`decision_card`::

    ctx.log({"kind": kind, "level": "info", "card": decision_card(
        id="t1", title="Long MNQ at the open", as_of_ns=ctx.now, action="enter",
        instrument="MNQ", side="long", qty=1, entry=21000.25, stop=20990.0,
        targets=(21010.0,), reason="...", tag="t1")})

Prices are futures points. ``id`` is stable per decision; a ``result`` card
names the card it closes in ``ref``. The host cleans the card (known keys
only, strings capped, at most 4 targets and 24 ``fields`` pairs) and appends
it to ``<out_dir>/decision_cards.jsonl`` unless the runner entry has
``cards: false``; an invalid card is dropped with one ``card_invalid`` host
warning. ``plugin_log.jsonl`` keeps the line either way, and a card never
changes trading.
"""

from __future__ import annotations

import asyncio
import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal, Protocol

from pydantic import BaseModel, ValidationError

from fse.engine.types import Bar, DarkPoolPrint, Direction, Fill, OrderRole, Snapshot, Ticks, Trade
from fse.live.host.dayclock import GlobexDay
from fse.logio.canonical_json import JsonValue
from fse.timekit import Instant

if TYPE_CHECKING:
    import httpx

    from fse.calendars import Calendars
    from fse.clock import Clock
    from fse.live.levels_compare import LevelsClient
    from fse.logio import LogWriter
    from fse.secrets.env import EnvView

__all__ = [
    "BAR_INTERVAL_S",
    "PLUGIN_API_VERSION",
    "TAG_RE",
    "BarNeed",
    "BarStoreView",
    "BrokerTerms",
    "Cancel",
    "CardAction",
    "DataNeeds",
    "EnterMarket",
    "EnterResting",
    "FeedName",
    "Flatten",
    "GlobexDay",
    "Intent",
    "ModifyStop",
    "OrderStatus",
    "OrderUpdate",
    "PaperFills",
    "PaperRisk",
    "PluginContext",
    "PluginServices",
    "Quote",
    "SkylitNeed",
    "StrategyPlugin",
    "TargetLeg",
    "TaskStarter",
    "decision_card",
]

PLUGIN_API_VERSION: Final = 1
"""The plugin API version; a plugin class with another ``api_version`` does not load."""

BAR_INTERVAL_S: Final = 60
"""The only bar interval in v1."""

TAG_RE: Final = re.compile(r"^[A-Za-z0-9_.-]{1,48}$")
"""A plugin's order tag: no ``:``, so ``f"{plugin_id}:{tag}:{leg}"`` splits unambiguously."""

type FeedName = Literal["bars", "quotes", "skylit", "dark_pool"]
type OrderStatus = Literal[
    "accepted", "rejected", "filled_entry", "filled_exit", "closed", "expired", "cancelled"
]


# ---------------------------------------------------------------- needs


@dataclass(frozen=True, slots=True)
class BarNeed:
    """Closed bars of one data-plane instrument."""

    instrument: str
    interval_s: int = BAR_INTERVAL_S


@dataclass(frozen=True, slots=True)
class SkylitNeed:
    """Skylit map (and optionally dark-pool) data, matching the data plane's settings."""

    symbols: tuple[str, ...]
    view_id: str
    mode: Literal["polling", "stream"]
    refresh_interval_s: int
    window: tuple[time, time]
    """NY wall times; must lie inside the data plane's Skylit window."""
    dark_pool_tickers: tuple[str, ...] = ()
    layout: Literal["net", "matrix"] = "net"
    """``matrix``: needs the per-expiration grid, so the data plane must run that layout."""


@dataclass(frozen=True, slots=True)
class DataNeeds:
    """What a plugin is dispatched (design 3.3 dispatch rule) and how it is brokered."""

    bars: tuple[BarNeed, ...]
    hours: Literal["eth", "rth"]
    """Informational in v1: the data plane always runs ETH."""
    skylit: SkylitNeed | None = None
    quotes: bool = False
    brokerage: Literal["host", "self"] = "host"
    hold_through_break: bool = False
    """May hold a position across the 18:00 NY rotation (the YAML can only restrict it)."""
    broker_orders: bool = False
    """May be configured with a non-paper ``execution.order_mode`` (the YAML can only keep
    it paper)."""

    @property
    def instruments(self) -> frozenset[str]:
        return frozenset(b.instrument for b in self.bars)


# ---------------------------------------------------------------- market types


@dataclass(frozen=True, slots=True)
class Quote:
    """One realtime quote of an instrument's contract, in ticks."""

    instrument: str
    contract_id: str
    t_ns: Instant
    received_ns: Instant
    bid_t: Ticks | None
    ask_t: Ticks | None
    last_t: Ticks | None


# ---------------------------------------------------------------- intents (plugin -> host)


@dataclass(frozen=True, slots=True)
class TargetLeg:
    """A target ``ticks`` from the reference (``EnterMarket.reference_t``, else the
    fill bar's unslipped open)."""

    role: Literal["tp1", "tp2"]
    qty: int
    ticks: int


@dataclass(frozen=True, slots=True)
class EnterMarket:
    """A market entry with a tick-distance bracket (design 7.2).

    The stop and targets are measured from the reference: ``reference_t`` when
    set, else the fill bar's unslipped open. The entry fills at the fill bar's
    ``open ± slippage`` either way. With ``reference_t`` set, a fill bar whose
    open or slipped fill is at or past the stop or a target drops the entry with
    no fill (``expired``/``reference_gap``).

    ``placed_at`` must satisfy ``now - 60 s <= placed_at <= now`` and precede
    every bar the plugin's book has processed for the instrument; with
    ``exact_bar_open`` only the bar opening at ``placed_at`` may fill it. With
    ``freeze_from_ns`` (``>= placed_at + 60 s``) the broker feeds the setup no
    bar opening at or after it.
    """

    tag: str
    instrument: str
    direction: Direction
    qty: int
    stop_ticks: int
    targets: tuple[TargetLeg, ...]
    placed_at: Instant
    exact_bar_open: bool = False
    freeze_from_ns: Instant | None = None
    reference_t: Ticks | None = None
    """``None``: the reference is the fill bar's unslipped open. Set: this price, in
    raw ticks of the instrument's current contract; a fill bar whose open or slipped
    fill is at or past the stop or a target drops the entry (``reference_gap``)."""


@dataclass(frozen=True, slots=True)
class EnterResting:
    """A limit or stop entry with absolute prices (``SimBook.submit_bracket``)."""

    tag: str
    instrument: str
    direction: Direction
    qty: int
    kind: Literal["limit", "stop"]
    price: Ticks
    stop: Ticks
    targets: tuple[tuple[Literal["tp1", "tp2"], int, Ticks], ...]
    """``(role, qty, price)``."""
    setup_through: bool = False
    """The setup bar already traded to ``price``. Fill at that price on this bar."""


@dataclass(frozen=True, slots=True)
class Flatten:
    """Close ``tag`` (``None``: every open trade and working order of the plugin)."""

    tag: str | None
    reason: str
    """``[a-z_]{1,32}``."""
    price: Literal["last_close", "next_open"]
    stamp_ns: Instant | None = None
    """``last_close``: use the newest bar closing at or before it (e.g. a session close)."""


@dataclass(frozen=True, slots=True)
class Cancel:
    """Cancel the working entry of ``tag`` (not its stop or targets)."""

    tag: str


@dataclass(frozen=True, slots=True)
class ModifyStop:
    """Tighten the resting protective stop of ``tag``'s open trade (breakeven, trailing).

    The new price applies from the next bar the plugin's book has not processed
    (``last bar open + 60 s``, ``detail["effective_ns"]``), not from ``ctx.now``:
    live bars arrive after their close, so a version at ``now`` would skip that
    bar. Two modifies before that bar: the last one wins.

    ``accepted`` (role ``stop``) with ``detail = {"stop_price", "previous_stop",
    "effective_ns", "unchanged"}``. The same price as the current stop is an
    accepted no-op (``unchanged: True``, ``effective_ns: None``). Rejections
    (role ``stop``): ``not_open`` (no open trade has the tag: unknown, pending
    entry, closed or flattened), ``invalid_order`` (``stop_price`` not an
    ``int``), ``stop_loosen`` (below the current stop for a long, above for a
    short). Not gated by the halt file, blocks or caps. A stop at or through the
    market fills on the next bar by the usual stop rules (``at_stop``: at the
    stop price, ``gap_open``: at the worse of stop and open, then slippage).
    Targets and ``Trade.initial_stop`` (so R) do not change.
    """

    tag: str
    stop_price: Ticks
    """Raw ticks of the instrument's current contract."""


type Intent = EnterMarket | EnterResting | Flatten | Cancel | ModifyStop


# ---------------------------------------------------------------- events (host -> plugin)


@dataclass(frozen=True, slots=True)
class OrderUpdate:
    """What happened to one of the plugin's tags."""

    tag: str
    status: OrderStatus
    reason: str | None
    """Rejection / expiry / cancellation reason only, e.g. ``"halt_file"``, ``"max_contracts"``."""
    fill: Fill | None
    role: OrderRole | None
    trade: Trade | None
    """The closed trade when ``status == "closed"``."""
    bar: Bar | None
    """The bar the fill happened on (``None`` for a ``last_close`` flatten)."""
    at: Instant
    detail: Mapping[str, JsonValue] | None = None
    """Diagnostics, e.g. ``{"submit_quote_t": 85012, "quote_age_ms": 420}`` on ``accepted``."""


# ---------------------------------------------------------------- services and context


class BarStoreView(Protocol):
    """A read-only view of the data plane's bar store."""

    @property
    def root(self) -> Path:
        """The store root (for a plugin's own ``--extra-bars`` option)."""
        ...

    def bars(self, instrument: str, day: date) -> tuple[Bar, ...]:
        """The stored closed bars of ``instrument`` for Globex day ``day``, oldest first."""
        ...


class TaskStarter(Protocol):
    """How :class:`PluginServices` starts the plugin's background tasks (the host)."""

    def run_blocking[T](
        self, name: str, fn: Callable[..., T], *args: object
    ) -> asyncio.Task[T]: ...

    def spawn[T](self, name: str, coro: Coroutine[Any, Any, T]) -> asyncio.Task[T]: ...


@dataclass(frozen=True, slots=True)
class PaperFills:
    """A host-brokered plugin's runner ``fills`` block, read-only (design 7.4).

    A plugin checks its fill semantics against it at start (for example it
    needs ``trade_through_ticks == 1`` and ``stop_fill == "at_stop"``) and books its
    own outputs with the same costs the paper broker charges.
    """

    trade_through_ticks: int
    slippage_ticks: int
    stop_fill: Literal["gap_open", "at_stop"]
    commission_rt: Mapping[str, Decimal]
    """Round-trip commission per contract in dollars, per instrument."""


@dataclass(frozen=True, slots=True)
class PaperRisk:
    """A host-brokered plugin's runner ``risk`` block, read-only (design 7.4).

    The caps the paper broker enforces; a plugin reads it next to :class:`PaperFills`.
    ``max_daily_loss`` also caps per-trade stop dollar risk: a plugin that sizes by risk can
    size qty so ``stop_ticks * tick_value * qty <= remaining budget`` (``max_daily_loss +
    day_net``), else skips; the host rejects an oversized ``EnterMarket``.
    """

    max_contracts: int
    max_open_trades: int
    max_trades_per_day: int | None
    max_daily_loss: Decimal | None
    """Dollars; ``None``: no daily loss / per-trade stop-risk cap."""


@dataclass(frozen=True, slots=True)
class BrokerTerms:
    """The runner ``fills`` and ``risk`` blocks as one flat, read-only record.

    The same values as :class:`PaperFills` plus :class:`PaperRisk` (Range reads
    this shape; ``commission_rt`` is the same read-only mapping as
    ``services.fills.commission_rt``).
    """

    trade_through_ticks: int
    slippage_ticks: int
    stop_fill: Literal["gap_open", "at_stop"]
    commission_rt: Mapping[str, Decimal]
    """Round-trip commission per contract in dollars, per instrument."""
    max_contracts: int
    max_open_trades: int
    max_trades_per_day: int | None
    max_daily_loss: Decimal | None


class PluginServices:
    """Read-only services the host gives a plugin at construction (design 3.3)."""

    __slots__ = (
        "_calendars",
        "_tasks",
        "bar_store",
        "broker_terms",
        "cache_dir",
        "calendar_dir",
        "clock",
        "env",
        "fills",
        "http",
        "out_dir",
        "project_dir",
        "recordings_root",
        "risk",
        "skylit_levels",
        "skylit_window",
        "state_dir",
        "writer",
    )

    def __init__(
        self,
        *,
        tasks: TaskStarter,
        calendars: Callable[[], Calendars],
        writer: LogWriter,
        clock: Clock,
        calendar_dir: Path,
        state_dir: Path,
        out_dir: Path,
        project_dir: Path | None,
        bar_store: BarStoreView,
        cache_dir: Path,
        env: EnvView,
        http: httpx.AsyncClient | None = None,
        skylit_levels: LevelsClient | None = None,
        skylit_window: tuple[time, time] | None = None,
        fills: PaperFills | None = None,
        recordings_root: Path | None = None,
        risk: PaperRisk | None = None,
        broker_terms: BrokerTerms | None = None,
    ) -> None:
        self._tasks = tasks
        self._calendars = calendars
        self.writer = writer
        """The redacting Log_Writer."""
        self.clock = clock
        self.calendar_dir = calendar_dir
        """The resolved calendar directory (for a plugin loading its own calendars)."""
        self.state_dir = state_dir
        """``<live-state>/plugins/<id>`` (a self-brokered plugin gets the live-state root)."""
        self.out_dir = out_dir
        self.project_dir = project_dir
        self.bar_store = bar_store
        self.cache_dir = cache_dir
        self.env = env
        self.http = http
        """The process HTTP client (the Playbook's Notifier only)."""
        self.skylit_levels = skylit_levels
        """The data plane's Skylit levels client; ``None`` when Skylit is disabled."""
        self.skylit_window = skylit_window
        """The data plane's Skylit window (NY wall times); ``None`` when Skylit is disabled."""
        self.fills = fills
        """The runner ``fills`` block of a host-brokered plugin; ``None`` when self-brokered."""
        self.recordings_root = recordings_root
        """The run's recordings root (``<root>/recordings/``); ``None`` = the default root."""
        self.risk = risk
        """The runner ``risk`` block of a host-brokered plugin; ``None`` when self-brokered."""
        self.broker_terms = broker_terms
        """``fills`` + ``risk`` as one flat record (host-brokered); ``None`` when self-brokered."""

    @property
    def calendars(self) -> Calendars:
        """The host's calendars with default ``SessionTimes``, reloaded at each rotation."""
        return self._calendars()

    def run_blocking[T](self, name: str, fn: Callable[..., T], *args: object) -> asyncio.Task[T]:
        """Run ``fn(*args)`` off the dispatcher (production: a worker thread).

        The finished task gets one ``task_done`` wake (an ``on_wake`` call on
        the dispatcher). Thread confinement: ``fn`` opens and closes its own
        sqlite-backed objects (``DataCache``, ``Catalog``) and returns plain
        data only.
        """
        return self._tasks.run_blocking(name, fn, *args)

    def spawn[T](self, name: str, coro: Coroutine[Any, Any, T]) -> asyncio.Task[T]:
        """Run ``coro`` as a cancellable task; its end gets one ``task_done`` wake.

        ``on_stop`` and a fault cancel every task the plugin spawned.
        """
        return self._tasks.spawn(name, coro)


class PluginContext(Protocol):
    """Per-call context of a hook."""

    @property
    def now(self) -> Instant: ...

    def position(self, instrument: str) -> int:
        """Signed open contracts of ``instrument`` in this plugin's book."""
        ...

    def open_tags(self) -> tuple[str, ...]:
        """Tags with an open trade."""
        ...

    def working_tags(self) -> tuple[str, ...]:
        """Tags with a working (unfilled) entry."""
        ...

    def entries_blocked(self) -> tuple[str, ...]:
        """Reasons entries are refused now (halt file, blocks, caps, faulted); ``()`` if none."""
        ...

    def day_net(self) -> Decimal:
        """This plugin's paper-book realized net for the current Globex day (dollars).

        ``0`` when there is no broker yet. Used with ``services.risk.max_daily_loss``
        to size stop risk against the remaining daily-loss budget.
        """
        ...

    def log(self, entry: Mapping[str, JsonValue]) -> None:
        """Append ``entry`` (plugin id added) to ``<out_dir>/plugin_log.jsonl``.

        An entry with a ``"card"`` (:func:`decision_card`) is also a decision
        card for the console (module notes).
        """
        ...


# ---------------------------------------------------------------- decision cards


type CardAction = Literal["enter", "skip", "hold", "exit", "result", "info"]


def decision_card(
    *,
    id: str,
    title: str,
    as_of_ns: int,
    action: CardAction,
    instrument: str | None = None,
    symbol: str | None = None,
    side: Literal["long", "short"] | None = None,
    qty: int | None = None,
    entry: float | None = None,
    stop: float | None = None,
    targets: Sequence[float] = (),
    reason: str | None = None,
    ref: str | None = None,
    tag: str | None = None,
    result: Mapping[str, JsonValue] | None = None,
    fields: Sequence[tuple[str, JsonValue]] = (),
    spot: float | None = None,
    dayPct: float | None = None,
    rangePct: float | None = None,
    gates: str | None = None,
    bars: Sequence[Mapping[str, float]] = (),
    odds: Mapping[str, JsonValue] | None = None,
    ladder: Sequence[Mapping[str, JsonValue]] = (),
    narration: str | None = None,
) -> dict[str, JsonValue]:
    """The ``"card"`` object of a plugin log line (module notes); prices in futures points.

    ``result`` keys: ``net`` (dollars, a decimal string), ``exit_reason``,
    ``r`` and ``ticks``. ``fields`` are extra ``(label, value)`` rows shown
    as-is. Optional ``bars`` / ``ladder`` / ``odds`` enrich the console rich
    FindingCard. Pure: the host does the validation and size caps.
    """
    return {
        "id": id,
        "title": title,
        "as_of_ns": as_of_ns,
        "action": action,
        "instrument": instrument,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "entry": entry,
        "stop": stop,
        "targets": list(targets),
        "reason": reason,
        "ref": ref,
        "tag": tag,
        "result": None if result is None else dict(result),
        "fields": [[label, value] for label, value in fields],
        "spot": spot,
        "dayPct": dayPct,
        "rangePct": rangePct,
        "gates": gates,
        "bars": [dict(b) for b in bars],
        "odds": None if odds is None else dict(odds),
        "ladder": [dict(row) for row in ladder],
        "narration": narration,
    }


# ---------------------------------------------------------------- the plugin


class StrategyPlugin(ABC):
    """Base class of every strategy plugin: every hook but :meth:`needs` is a no-op.

    Hooks run on the host's single dispatcher task, synchronously, and must
    not block (see the module notes, including the thread-confinement rule
    for sqlite-backed objects). Any exception a hook does not catch faults
    the plugin: it gets no further calls until the process restarts.

    **Self-description.** The class attributes :attr:`label`,
    :attr:`description`, :attr:`brokerage` and :attr:`config_model` let the
    console and every fse command show the plugin with no plugin-specific
    code: its name, what it does, and a form for its ``config`` block built
    from the model's JSON schema (field docstrings become the help text).
    """

    api_version: ClassVar[int] = PLUGIN_API_VERSION
    label: ClassVar[str | None] = None
    """A short display name, e.g. ``"Template"``; ``None``: the class name."""
    description: ClassVar[str] = ""
    """One or two sentences on what the plugin trades and when."""
    brokerage: ClassVar[Literal["host", "self"]] = "host"
    """Who books its orders; must equal ``needs().brokerage`` (shown before construction)."""
    config_model: ClassVar[type[BaseModel] | None] = None
    """The pydantic model of the runner YAML ``config`` block, or ``None`` (free-form)."""

    @classmethod
    def display_label(cls) -> str:
        """:attr:`label`, or the class name."""
        return cls.label or cls.__name__

    @classmethod
    def validate_config(cls, config: Mapping[str, object]) -> list[str]:
        """Problems with a ``config`` block, one line each; ``[]`` when it is valid.

        The default validates against :attr:`config_model` (no model: always
        valid). Override when the plugin reads its config another way.
        """
        if cls.config_model is None:
            return []
        try:
            cls.config_model.model_validate(dict(config))
        except ValidationError as exc:
            return [
                f"config.{'.'.join(str(x) for x in e['loc'])}: {e['msg']}"
                if e["loc"]
                else f"config: {e['msg']}"
                for e in exc.errors()
            ]
        return []

    def __init__(
        self, plugin_id: str, config: Mapping[str, object], services: PluginServices
    ) -> None:
        self.plugin_id = plugin_id
        self.config = config
        self.services = services

    @abstractmethod
    def needs(self) -> DataNeeds:
        """The plugin's data needs and brokerage; constant for its lifetime."""

    def on_start(self, ctx: PluginContext) -> Sequence[Intent]:
        """After restore (or a fresh start), before the repair replay: warm-up."""
        return ()

    def on_day_open(self, day: GlobexDay, ctx: PluginContext) -> Sequence[Intent]:
        """At ``day.open_ns`` (and at start when started inside ``day``)."""
        return ()

    def on_bar(
        self, bar: Bar, received_ns: Instant, catch_up: bool, ctx: PluginContext
    ) -> Sequence[Intent]:
        """A closed bar of a needed instrument, after the plugin's book processed it."""
        return ()

    def on_snapshots(
        self, snaps: Sequence[Snapshot], received_ns: Instant, ctx: PluginContext
    ) -> Sequence[Intent]:
        """Map snapshots of the needed symbols received inside the Skylit window."""
        return ()

    def on_dark_pool(
        self,
        ticker: str,
        prints: Sequence[DarkPoolPrint],
        received_ns: Instant,
        ctx: PluginContext,
    ) -> Sequence[Intent]:
        """Dark-pool prints of a needed ticker."""
        return ()

    def on_quote(self, quote: Quote, ctx: PluginContext) -> Sequence[Intent]:
        """A quote of a needed instrument (only with ``needs().quotes``)."""
        return ()

    def on_order_update(self, update: OrderUpdate, ctx: PluginContext) -> Sequence[Intent]:
        """An update of one of the plugin's own tags."""
        return ()

    def on_feed_status(
        self, feed: FeedName, up: bool, detail: str, ctx: PluginContext
    ) -> Sequence[Intent]:
        """A feed went up or down (only feeds the plugin needs; ``bars`` always)."""
        return ()

    def next_wake(self, now: Instant) -> Instant | None:
        """The earliest instant the plugin wants :meth:`on_wake`; ``None`` for none."""
        return None

    def on_wake(self, now: Instant, ctx: PluginContext) -> Sequence[Intent]:
        """At a wanted wake, and once after each finished background task."""
        return ()

    def urgent_bar_closes(self, day: GlobexDay) -> Sequence[tuple[str, Instant]]:
        """``(instrument, close_ns)`` bars wanted as early as possible on ``day``."""
        return ()

    def on_day_close(self, day: GlobexDay, ctx: PluginContext) -> Sequence[Intent]:
        """At ``day.rotate_ns``, before the host's break backstop."""
        return ()

    def snapshot(self) -> JsonValue:
        """The plugin's state for a restart (stored with its book in ``plugin.json``)."""
        return None

    def restore(self, state: JsonValue) -> None:
        """Restore :meth:`snapshot`'s value; raise ``ValueError`` if it is unusable."""
        return None

    def status(self, now: Instant) -> Mapping[str, JsonValue] | None:
        """Optional plain-English status for the console; ``None`` (the default) for none.

        The host already writes a status for every plugin (phase, positions,
        working orders, today's P&L, recent order events, next wake); this
        only adds the plugin's own words. Known keys, all optional:

        - ``summary``: one line, e.g. ``"Waiting for the 11:40 slot"``;
        - ``now``: what it is doing right now;
        - ``next``: ``{"at_ns": int | None, "text": str}``, the next planned action;
        - ``last``: ``{"at_ns": int | None, "text": str}``, the last thing it did;
        - ``schedule``: up to 64 ``{"at_ns": int, "label": str, "state": str}`` rows
          for today (``state``: ``planned``, ``done``, ``skipped``, ``entered`` ...);
        - ``facts``: up to 16 ``[label, value]`` string pairs.

        Called on the dispatcher after state changes; it must be cheap, must
        not change state, and must not depend on ``now`` beyond picking what
        is next (clock-relative text such as "in 5 min" belongs in the UI).
        An exception or a non-JSON value is logged once as ``status_failed``
        and ignored: it never faults the plugin.
        """
        return None

    def on_stop(self, ctx: PluginContext) -> None:
        """Graceful shutdown."""
        return None
