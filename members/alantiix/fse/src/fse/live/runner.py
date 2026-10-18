"""The Live_Runner: the backtested engine on live data (design §23, Req 23, 24.2, 24.26-24.27, 25).

One run covers one session. :class:`LiveSession` is the synchronous core and
:class:`LiveRunner` the asyncio orchestration around it.

**Start** (Req 23.12-23.13, 24.2). A refresh interval under 5 s is refused
(:class:`LiveStartError`, exit 2). The Order_Mode is fixed for the run
(:mod:`fse.live.order_router`); ``fse paper`` always runs Paper Order_Mode.
The start log names the Order_Mode, the Strategy_Config hash, the code
version and the projected Skylit credits per session
(:func:`credit_projection`).

**Windows** (:func:`session_window`). The map feed refreshes from
``live.run_window.start`` to the run window end (the close on an early-close
day). Decision_Times run from the later of the run window start and the RTH
open to the earliest of the run window end, the close and the Flat_Deadline,
so no live Decision_Time falls at or after the Flat_Deadline: an exit is
never sent after the account's flat close. The bar feed polls until the bar
that opens at the Flat_Deadline has closed (plus :data:`BAR_SLACK_S`); then
the session ends with the Paper_Broker's Flat_Deadline close.

**Inputs.** Every Snapshot, bar, dark-pool fetch and VIX record goes to the
recorder and to :class:`~fse.pit.market_view.LiveInputs` with its receipt
time (Req 23.8), so it is available at ``max(Observation_Time,
receipt_time)``. A receipt time is never at or before the latest completed
Decision_Time (it is moved to 1 ns after it), so an input that arrives after a
Decision_Time is never visible to it, live or in replay (Req 23.9).

**Decision_Times** (design "Time model", Req 23.6). Each Decision_Cadence
tick of the Backtester's grid, each arrival of a new Map_State or closed bar,
and the instant the current Map_State becomes too old (the stale-map guard,
Req 23.7), inside the Decision_Time window. Inputs that arrive together are
handled by one Decision_Time. At Decision_Time ``t`` (:meth:`LiveSession.decide`):

1. the guards (:mod:`fse.live.guards`): stale map, halt file, persistent
   blocks; recorded as a ``guard_event`` when any is in force;
2. the closed bars that became available since the previous Decision_Time,
   in (open, instrument) order, go through the bar phase;
3. ``Engine.step`` through the Backtester's :class:`~fse.backtest.runner.SessionLoop`
   (Req 23.5), whose intents go to the Paper_Broker before anything else
   (Req 25.13); the step's latency is logged, with a warning above 1 s;
4. the ``decision_time`` is recorded and the state saved (temp file, fsync,
   rename; :mod:`fse.live.state_store`);
5. the Finding_Card and 2R alerts are built and queued for the Notifier when
   due (:class:`~fse.notify.finding_card.CardSchedule`); the Notifier delivers
   in its own task, so a pending delivery never delays a Decision_Time
   (Req 23.11, 25.13-25.14).

**Practice and Combine** (Req 24, :mod:`fse.live.broker_safety`). With a
:class:`~fse.live.order_router.ModeDecision` for a broker mode and a
:class:`~fse.live.broker_safety.BrokerSafety`, the session's broker is a
:class:`~fse.live.order_router.MirrorBroker` bound to the mode's account:
the Risk_Manager pre-check and the ignored-instrument rule run as each intent
is routed, and after each Decision_Time the session queues the routed broker
actions and its book for the safety task, which sends them, reads the broker,
confirms stops and reconciles. Its blocks join the guards; its notes go in
the next Finding_Card, which is then sent whatever the schedule. A failed
Order_Mode condition is named in the first Finding_Card (Req 24.3, 24.6).

**Restart** (Req 16.7, 16.10). The saved EngineState and paper state are
restored. In the same session, the session's recording is read back into
the ring buffers, and the bars received after the saved Decision_Time go
through the next bar phase. A state that cannot be read, a recording that
cannot be read or a paper state from another Decision_Time sets a
``restore_failed`` block (persistent until ``fse clear``), the run starts from
a fresh state, and the first Finding_Card reports it.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
from collections.abc import Callable, Coroutine, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, ClassVar, Final, Protocol

from fse.backtest.decision_log import DECISION_LOG_FILE_NAME, DecisionLog
from fse.backtest.runner import SessionLoop, bar_groups, trading_fees
from fse.calendars import ContractPeriod
from fse.clock import Clock
from fse.config.hashing import config_hash
from fse.config.schema import StrategyConfig
from fse.config.schema.live import REFRESH_INTERVAL_MIN_S
from fse.data.aux_stores import VixDailyRecord
from fse.data.darkpool import DarkPoolTradesClient
from fse.engine.state import EngineState
from fse.engine.step import Engine, ExternalBlock, StepResult
from fse.engine.taps import BASE_INTERVAL_S
from fse.engine.types import Bar, DarkPoolPrint, EconomicEvent, Snapshot
from fse.live._wait import FeedLog, within
from fse.live.bar_feed import DEFAULT_POLL_INTERVAL_S, BarFeed, BarRetriever, FeedInstrument
from fse.live.broker_safety import BrokerSafety, Expectation
from fse.live.darkpool_feed import DEFAULT_INTERVAL_S as DARK_POOL_INTERVAL_S
from fse.live.darkpool_feed import DarkPoolFeed, feed_tickers
from fse.live.guards import STALE_MAP_BLOCK, Guards, StaleMapGuard
from fse.live.levels_compare import LevelsClient, LevelsCompare, compare_active
from fse.live.map_feed import REFRESH_TIMEOUT_S, MapClient, MapFeed
from fse.live.order_router import (
    BROKER_MODES,
    MirrorBroker,
    ModeDecision,
    Routing,
    broker_routing,
    paper_routing,
)
from fse.live.recorder import (
    Recorder,
    RecordingError,
    decode_bar,
    decode_dark_pool,
    decode_snapshot,
    decode_vix,
    iter_recording,
    recording_path,
)
from fse.live.state_store import (
    BlockRecord,
    BlockStore,
    BlocksUnreadable,
    PaperState,
    Restored,
    RestoreFailure,
    StateStore,
)
from fse.logio import LogWriter
from fse.logio.canonical_json import JsonValue, ny_iso
from fse.notify.finding_card import (
    CardLedger,
    CardSchedule,
    FindingCard,
    alerts_for,
    build_card,
    premarket_card,
)
from fse.notify.notifier import Message, Notifier
from fse.pit.asof import available_at
from fse.pit.market_view import MAP_METRICS, LiveInputs, heatmap_view
from fse.sim.account import AccountSim
from fse.sim.paper_broker import PaperBroker
from fse.skylit.endpoints import DARK_POOL_TRADES, GEX_LEVELS, HEATMAP, STREAM
from fse.timekit import NS_PER_SECOND, Instant, SessionCalendar, ny_instant

__all__ = [
    "BAR_SLACK_S",
    "LIVE_LOG_FILE_NAME",
    "STEP_LATENCY_LIMIT_NS",
    "DecisionStep",
    "LiveClients",
    "LiveDecision",
    "LiveRunner",
    "LiveSession",
    "LiveStartError",
    "Outbox",
    "PlaybookScheduler",
    "SessionWindow",
    "check_refresh_interval",
    "credit_projection",
    "feed_instruments",
    "feed_instruments_for",
    "map_wait_ns",
    "session_window",
    "start_log_entry",
]

LIVE_LOG_FILE_NAME: Final = "live_log.jsonl"
STEP_LATENCY_LIMIT_NS: Final = NS_PER_SECOND
"""A step slower than this is logged as a warning (Req 23.6)."""
BAR_SLACK_S: Final = 30
"""How long after the Flat_Deadline bar's close the bar feed keeps polling for it."""


class LiveStartError(Exception):
    """The Live_Runner refuses to start: exit 2 (invalid input)."""

    exit_code: ClassVar[int] = 2


def check_refresh_interval(cfg: StrategyConfig) -> None:
    """Refuse a refresh interval under 5 s, naming the setting (Req 23.13)."""
    interval = cfg.live.refresh_interval_s
    if interval < REFRESH_INTERVAL_MIN_S:
        raise LiveStartError(
            f"live.refresh_interval_s is {interval} s; the minimum is {REFRESH_INTERVAL_MIN_S} s"
        )


# ---------------------------------------------------------------- windows and credits


@dataclass(frozen=True, slots=True)
class SessionWindow:
    """The instants that bound one live session (see the module notes)."""

    session: date
    run_start: Instant
    map_end: Instant
    dt_start: Instant
    dt_end: Instant
    deadline: Instant
    bars_until: Instant
    premarket: Instant


def session_window(cfg: StrategyConfig, calendar: SessionCalendar, session: date) -> SessionWindow:
    """The live windows of ``session`` (``ValueError`` when it is not a session)."""
    rw = cfg.live.run_window
    run_start = ny_instant(session, rw.start)
    run_end = ny_instant(session, rw.end)
    close = calendar.rth_close(session)
    deadline = calendar.flat_deadline(session)
    map_end = min(run_end, close)
    dt_start = max(run_start, calendar.rth_open(session))
    dt_end = min(run_end, close, deadline)
    bars_until = deadline + (BASE_INTERVAL_S + BAR_SLACK_S) * NS_PER_SECOND
    return SessionWindow(
        session=session,
        run_start=run_start,
        map_end=max(map_end, run_start),
        dt_start=dt_start,
        dt_end=max(dt_end, dt_start),
        deadline=deadline,
        bars_until=bars_until,
        premarket=ny_instant(session, cfg.notify.premarket),
    )


def credit_projection(
    cfg: StrategyConfig, window: SessionWindow, order_mode: str
) -> dict[str, JsonValue]:
    """The projected Skylit credits of one session (Req 23.12, design §23)."""
    seconds = max(0, window.map_end - window.run_start) / NS_PER_SECOND
    live = cfg.live
    symbols = len(cfg.data.symbols)
    metrics = len(MAP_METRICS)
    if live.mode == "polling":
        refreshes = math.ceil(seconds / live.refresh_interval_s)
        map_credits = refreshes * metrics * HEATMAP.credits
    else:
        minutes = math.ceil(seconds / 60)
        map_credits = symbols * metrics * STREAM.credits * (minutes + 1)
    levels = 0
    if compare_active(order_mode):
        levels = math.ceil(seconds / live.levels_compare_interval_s) * GEX_LEVELS.credits
    dark_pool = 0
    if cfg.gates.dark_pool_confluence.enabled:
        tickers = len(feed_tickers(cfg.gates.dark_pool_confluence))
        polls = math.ceil(seconds / DARK_POOL_INTERVAL_S)
        dark_pool = polls * DARK_POOL_TRADES.credits * max(1, tickers)
    return {
        "refresh_mode": live.mode,
        "refresh_interval_s": live.refresh_interval_s,
        "window_start_ny": ny_iso(window.run_start),
        "window_end_ny": ny_iso(window.map_end),
        "symbols": list(cfg.data.symbols),
        "metrics": list(MAP_METRICS),
        "levels_compare_interval_s": live.levels_compare_interval_s if levels else None,
        "map_credits": map_credits,
        "levels_credits": levels,
        "dark_pool_credits": dark_pool,
        "total_credits": map_credits + levels + dark_pool,
    }


def feed_instruments(
    cfg: StrategyConfig, periods: Iterable[tuple[str, ContractPeriod | None]]
) -> tuple[FeedInstrument, ...]:
    """The bar feed's contracts: the roll calendar's, its ProjectX id or the expected id.

    :class:`LiveStartError` names each instrument without a contract or id.
    """
    out, missing = feed_instruments_for(periods, cfg.live.expected_contracts.for_instrument)
    if missing:
        raise LiveStartError("the bar feed cannot start: " + "; ".join(missing))
    return out


def feed_instruments_for(
    periods: Iterable[tuple[str, ContractPeriod | None]],
    fallback: Callable[[str], str | None],
) -> tuple[tuple[FeedInstrument, ...], list[str]]:
    """The feed instruments with a contract and id, and one problem line per other instrument.

    The id is the roll calendar's ProjectX id, else ``fallback(instrument)``
    (the expected contract id).
    """
    out: list[FeedInstrument] = []
    missing: list[str] = []
    for instrument, period in periods:
        if period is None:
            missing.append(f"{instrument}: no contract in the roll calendar")
            continue
        contract_id = period.projectx_contract_id or fallback(instrument)
        if contract_id is None:
            missing.append(f"{instrument}: no ProjectX contract id")
            continue
        out.append(FeedInstrument(instrument, period.contract, contract_id))
    return tuple(out), missing


# ---------------------------------------------------------------- the session core


class Outbox(Protocol):
    """Where the session queues Finding_Cards and alerts (the Notifier)."""

    def submit(self, message: Message) -> None: ...


@dataclass(frozen=True, slots=True)
class LiveDecision:
    """One completed live Decision_Time."""

    result: StepResult
    blocks: tuple[ExternalBlock, ...]
    latency_ns: int
    card: FindingCard | None
    alerts: int


@dataclass(slots=True)
class _Restore:
    engine: EngineState | None = None
    paper: PaperState | None = None
    failure: str | None = None
    notes: list[str] = field(default_factory=list)


class LiveSession:
    """One live session: inputs, recorder, guards, the decision loop and the cards."""

    def __init__(
        self,
        *,
        cfg: StrategyConfig,
        engine: Engine,
        calendar: SessionCalendar,
        session: date,
        writer: LogWriter,
        run_dir: Path,
        state_dir: Path,
        recordings_root: Path,
        outbox: Outbox,
        events: Iterable[EconomicEvent] = (),
        vix_daily: tuple[VixDailyRecord | None, VixDailyRecord | None] = (None, None),
        adapter: object | None = None,
        notes: Iterable[str] = (),
        perf_ns: Callable[[], int] = time.perf_counter_ns,
        mode: ModeDecision | None = None,
        safety: BrokerSafety | None = None,
        bar_store: object | None = None,
    ) -> None:
        self._cfg = cfg
        self._engine = engine
        self._calendar = calendar
        self._session = session
        self._writer = writer
        self._run_dir = Path(run_dir)
        self._state_dir = Path(state_dir)
        self._recordings_root = Path(recordings_root)
        self._outbox = outbox
        self._events = tuple(events)
        self._vix_daily = vix_daily
        self._adapter = adapter
        self._start_notes = list(notes)
        self._perf_ns = perf_ns
        self._mode = mode
        self._safety = safety if mode is not None and mode.mode in BROKER_MODES else None
        self._bar_store = bar_store
        if mode is not None and mode.mode in BROKER_MODES and safety is None:
            raise ValueError(f"Order_Mode {mode.mode} needs a BrokerSafety")
        self.window = session_window(cfg, calendar, session)
        self._instruments = engine.params.instruments
        self._symbols = tuple(cfg.data.symbols)
        self._view_id = heatmap_view(cfg.data.heatmap_view).view_id()
        self.store = StateStore(writer, self._state_dir)
        self.blocks = BlockStore(writer, self._state_dir)
        self.guards = Guards(
            StaleMapGuard(cfg.live.live_max_snapshot_age_s), self.blocks, state_dir
        )
        self.schedule = CardSchedule(cfg.notify.interval_min, cfg.notify.alerts_2r)
        self.ledger = CardLedger()
        self.inputs = LiveInputs(symbols=self._symbols, view_id=self._view_id, events=self._events)
        self._pending_bars: list[tuple[Bar, Instant]] = []
        self._last_t: Instant | None = None
        self._stale = False
        self._notes: list[str] = []
        self._opened = False
        self._finished = False
        self.latencies: list[int] = []
        self.decisions = 0
        self._log_path = self._run_dir / LIVE_LOG_FILE_NAME
        self._recorder: Recorder | None = None
        self._dlog: DecisionLog | None = None
        self._loop: SessionLoop | None = None
        self._routing: Routing | None = None

    # ------------------------------------------------------------ properties

    @property
    def session(self) -> date:
        return self._session

    @property
    def writer(self) -> LogWriter:
        return self._writer

    @property
    def calendar(self) -> SessionCalendar:
        return self._calendar

    @property
    def engine(self) -> Engine:
        return self._engine

    @property
    def loop(self) -> SessionLoop:
        if self._loop is None:
            raise ValueError("the live session is not open")
        return self._loop

    @property
    def routing(self) -> Routing:
        if self._routing is None:
            raise ValueError("the live session is not open")
        return self._routing

    @property
    def last_t(self) -> Instant | None:
        return self._last_t

    @property
    def recording(self) -> Path:
        return recording_path(self._recordings_root, self._session)

    @property
    def decision_log(self) -> Path:
        return self._run_dir / DECISION_LOG_FILE_NAME

    def log(self, entry: dict[str, JsonValue]) -> None:
        """One live-log line through the Log_Writer (feed failures, latency, start)."""
        self._writer.append_json(self._log_path, entry)

    def feed_log(self) -> FeedLog:
        def write(entry: object) -> None:
            self._writer.append_json(self._log_path, entry)

        return write

    # ------------------------------------------------------------ open

    def open(self, now: Instant) -> None:
        """Restore the saved state, start or resume the session, open the files."""
        if self._opened:
            raise ValueError("the live session is already open")
        restore = self._restore()
        cfg = self._cfg
        fees = trading_fees(cfg, self._instruments)
        kind = PaperBroker if self._safety is None else MirrorBroker
        account = AccountSim(cfg.account, self._calendar, fees)
        broker = kind(cfg.fills, account)
        resume_bars: list[Bar] | None = None
        if restore.failure is None and restore.paper is not None:
            account = AccountSim.restore(
                cfg.account, self._calendar, fees, restore.paper.broker.account
            )
            broker = kind.restore(cfg.fills, account, restore.paper.broker)
            if restore.paper.session == self._session:
                resume_bars = self._refill(restore)
        if restore.failure is not None:
            account = AccountSim(cfg.account, self._calendar, fees)
            broker = kind(cfg.fills, account)
            resume_bars = None
            self.blocks.add(BlockRecord("restore_failed", None, restore.failure, now))
            self._notes.append(
                f"restore failed: {restore.failure}; new entries are blocked until fse clear"
            )
        if self._mode is not None and self._mode.note is not None:
            self._notes.append(self._mode.note)
        self._routing = self._route(broker)
        self._run_dir.mkdir(parents=True, exist_ok=True)
        self._dlog = DecisionLog(self._writer, self.decision_log)
        loop = SessionLoop(cfg, self._engine, broker, self._calendar, self._dlog, "off")
        if restore.failure is None and restore.engine is not None:
            loop.state = restore.engine
            self._last_t = restore.engine.t
            if restore.paper is not None:
                loop.pending = list(restore.paper.pending)
                if resume_bars is not None:
                    loop.day_low = restore.paper.day_low
                    loop.truncated = restore.paper.truncated
        self._loop = loop
        self._recorder = Recorder(self._writer, self.recording)
        if resume_bars is not None:
            loop.resume_session(self._session, resume_bars)
            self._notes.append(
                f"resumed {self._session} from the state saved at {ny_iso(self._last_t or now)}"
            )
        else:
            loop.begin_session(self._session)
        self._notes.extend(restore.notes)
        found = self.blocks.read()
        if isinstance(found, BlocksUnreadable):
            self._notes.append(
                f"the blocks file is unreadable ({found.reason}): entries are blocked"
            )
        elif found:
            kinds = ", ".join(r.kind for r in found)
            self._notes.append(f"persistent blocks in force: {kinds} (lifted by fse clear)")
        self._notes.extend(self._start_notes)
        today, prior = self._vix_daily
        self.on_vix_daily(today, prior, now)
        self._opened = True

    def _route(self, broker: PaperBroker) -> Routing:
        safety, mode = self._safety, self._mode
        if safety is None or mode is None:
            return paper_routing(broker, self._adapter)
        assert isinstance(broker, MirrorBroker)
        cap = self._cfg.account.position_cap
        broker.configure(
            gate=safety,
            ignored=self._cfg.live.ignored_instruments,
            position_cap=cap.micro_equivalents if cap.enabled else None,
            risk=lambda: self.loop.state.risk,
            session=lambda: self.loop.session,
            note=self.note,
        )
        safety.bind(broker, self.note, self.broker_event, recheck=broker.recheck_entry)
        self.guards.add_source(safety.external_blocks)
        return broker_routing(mode.mode, broker, safety.adapter)

    @property
    def safety(self) -> BrokerSafety | None:
        return self._safety

    def note(self, text: str) -> None:
        """A line for the next Finding_Card, which is then sent whatever the schedule."""
        self._notes.append(text)

    def broker_event(self, entry: dict[str, JsonValue]) -> None:
        """A broker event: to the live log and, while open, to the recording (Req 23.8)."""
        self.log({"source": "broker", **entry})
        at = entry.get("t")
        if self._recorder is not None and isinstance(at, int):
            self._recorder.broker_event(entry, self._received(at))

    def _restore(self) -> _Restore:
        out = _Restore()
        loaded = self.store.load()
        if loaded is None:
            return out
        if isinstance(loaded, RestoreFailure):
            out.failure = loaded.reason
            return out
        assert isinstance(loaded, Restored)
        engine, paper = loaded.engine, loaded.paper
        if paper is None:
            out.failure = "the paper state file is missing"
        elif paper.t != engine.t:
            out.failure = (
                "the EngineState and the paper state were saved at different Decision_Times"
            )
        elif engine.session is not None and engine.session > self._session:
            out.failure = f"the saved state is from a later session ({engine.session})"
        elif paper.session is not None and paper.session != self._session:
            out.failure = f"the saved session {paper.session} did not finish"
        elif paper.session is None and engine.session == self._session:
            raise LiveStartError(f"the session {self._session} has already finished")
        else:
            out.engine, out.paper = engine, paper
        return out

    def _refill(self, restore: _Restore) -> list[Bar] | None:
        """Read the session's recording back into the ring buffers (a mid-session restart)."""
        assert restore.engine is not None
        saved_t = restore.engine.t
        path = self.recording
        try:
            rec = iter_recording(path)  # streamed: no whole-file copy in memory
            bars: list[Bar] = []
            for entry in rec:
                at = entry.received_ns
                match entry.kind:
                    case "snapshot":
                        self.inputs.add_snapshot(decode_snapshot(entry.payload), at)
                    case "bar":
                        bar = decode_bar(entry.payload)
                        self.inputs.add_bar(bar, at)
                        if self._is_traded(bar):
                            bars.append(bar)
                            avail = available_at(bar.close_ns, at)
                            if saved_t is None or avail > saved_t:
                                self._pending_bars.append((bar, avail))
                    case "vix":
                        value = decode_vix(entry.payload)
                        if isinstance(value, Bar):
                            self.inputs.add_vix_bar(value, at)
                        else:
                            self.inputs.set_vix_daily(value[0], value[1], at)
                    case "dark_pool":
                        ticker, prints = decode_dark_pool(entry.payload)
                        self.inputs.add_dark_pool(ticker, prints, at)
                    case _:
                        pass
        except (RecordingError, ValueError, OSError) as exc:
            restore.failure = f"the recording {path.name} cannot be read back: {exc}"
            self.inputs = LiveInputs(
                symbols=self._symbols, view_id=self._view_id, events=self._events
            )
            self._pending_bars = []
            return None
        if rec.truncated:
            restore.notes.append(
                f"the recording {path.name} was cut off; read to its last whole line"
            )
        return bars

    def _is_traded(self, bar: Bar) -> bool:
        return bar.instrument in self._instruments and bar.interval_s == BASE_INTERVAL_S

    # ------------------------------------------------------------ inputs

    def _received(self, received: Instant) -> Instant:
        last = self._last_t
        return received if last is None or received > last else last + 1

    def _require_recorder(self) -> Recorder:
        if self._recorder is None:
            raise ValueError("the live session is not open")
        return self._recorder

    def on_snapshots(self, snapshots: Sequence[Snapshot], received: Instant) -> bool:
        """Record and keep each Snapshot; whether any joined the Map_State inputs."""
        rec = self._require_recorder()
        at = self._received(received)
        kept = False
        for snap in snapshots:
            rec.snapshot(snap, at)
            kept = self.inputs.add_snapshot(snap, at) or kept
        return kept

    def on_bars(self, bars: Sequence[Bar], received: Instant) -> None:
        """Record and keep closed futures bars; traded 1-minute bars wait for the bar phase."""
        rec = self._require_recorder()
        at = self._received(received)
        for bar in bars:
            rec.bar(bar, at)
            self.inputs.add_bar(bar, at)
            if self._is_traded(bar):
                self._pending_bars.append((bar, available_at(bar.close_ns, at)))
                self.loop.add_bars((bar,))

    def on_dark_pool(self, ticker: str, prints: Sequence[DarkPoolPrint], received: Instant) -> None:
        rec = self._require_recorder()
        at = self._received(received)
        rec.dark_pool(ticker, prints, at)
        self.inputs.add_dark_pool(ticker, prints, at)

    def on_vix_daily(
        self, today: VixDailyRecord | None, prior: VixDailyRecord | None, received: Instant
    ) -> None:
        rec = self._require_recorder()
        at = self._received(received)
        rec.vix_daily(today, prior, at)
        self.inputs.set_vix_daily(today, prior, at)

    def on_vix_bar(self, bar: Bar, received: Instant) -> None:
        rec = self._require_recorder()
        at = self._received(received)
        rec.vix_bar(bar, at)
        self.inputs.add_vix_bar(bar, at)

    # ------------------------------------------------------------ Decision_Times

    def stale_at(self, now: Instant) -> Instant | None:
        """When the current Map_State becomes too old; ``None`` once a block is in force."""
        if self._stale:
            return None
        return self.guards.stale.stale_at(self.inputs.view(now).map_state())

    def decide(self, t: Instant) -> LiveDecision:
        """Run Decision_Time ``t`` (see the module notes)."""
        loop = self.loop
        rec = self._require_recorder()
        if self._last_t is not None and t <= self._last_t:
            raise ValueError(f"Decision_Time {t} is not after the previous one {self._last_t}")
        view = self.inputs.view(t)
        blocks = self.guards.at(view.map_state())
        self._stale = any(b.kind == STALE_MAP_BLOCK for b in blocks)
        if blocks:
            rec.guard_event(t, blocks, t)
        due = [bar for bar, avail in self._pending_bars if avail <= t]
        self._pending_bars = [(bar, avail) for bar, avail in self._pending_bars if avail > t]
        due.sort(key=lambda b: (b.open_ns, b.instrument))
        cal = self._calendar
        groups = bar_groups(due, cal.trading_day_start(self._session), self.window.deadline)
        paper = self.routing.paper
        if isinstance(paper, MirrorBroker):  # entries the BrokerSafety withheld at send time
            loop.pending.extend(paper.take_rejected())
        started = self._perf_ns()
        result = loop.advance(t, view, groups, blocks)
        latency = self._perf_ns() - started
        self._last_t = t
        if self._safety is not None and isinstance(paper, MirrorBroker):
            self._safety.submit(t, paper.take_outbound(), Expectation.of(paper))
        rec.decision_time(t)
        self._save(t)
        self.latencies.append(latency)
        self.decisions += 1
        entry: dict[str, JsonValue] = {
            "event": "decision",
            "t": t,
            "t_ny": ny_iso(t),
            "latency_ms": round(latency / 1_000_000, 3),
            "intents": len(result.intents),
            "blocks": [b.kind for b in blocks],
        }
        if latency > STEP_LATENCY_LIMIT_NS:
            entry["warning"] = "Engine.step took more than 1 s"
            self._writer.error(f"warning: Engine.step at {ny_iso(t)} took {latency / 1e9:.3f} s")
        self.log(entry)
        card, alerts = self._cards(result)
        return LiveDecision(result, blocks, latency, card, alerts)

    def _save(self, t: Instant | None) -> None:
        loop = self.loop
        paper = PaperState(
            broker=self.routing.paper.snapshot(),
            session=loop.session,
            pending=tuple(loop.pending),
            day_low=loop.day_low,
            truncated=loop.truncated,
            t=t,
        )
        self.store.save(loop.state, paper)

    def _session_bars(self) -> dict[str, tuple]:
        """Recent 1-min bars per instrument for the rich Finding_Card trade chart."""
        store = self._bar_store
        if store is None:
            return {}
        bars_fn = getattr(store, "bars", None)
        if bars_fn is None:
            return {}
        out: dict[str, tuple] = {}
        for instrument in self._instruments:
            try:
                found = bars_fn(instrument, self._session)
            except Exception:
                continue
            if found:
                out[instrument] = tuple(found)
        return out

    def _cards(self, result: StepResult) -> tuple[FindingCard | None, int]:
        payload = result.payload
        self.ledger.record(payload)
        card: FindingCard | None = None
        if self._notes or self.schedule.due(payload.t, payload.cards):
            p = self._engine.params
            card = build_card(
                payload,
                self.inputs.view(payload.t),
                self.loop.state,
                instruments=self._instruments,
                node_params=p.node_params,
                level_params=p.level_params,
                working=self.routing.paper.working_orders(),
                order_mode=self.routing.mode,
                since=self.ledger,
                notes=self._take_notes(),
                bars_by_instrument=self._session_bars(),
            )
            self._outbox.submit(Message.card(card))
            self.schedule.sent(payload.t)
            self.ledger.reset()
        keys = self.schedule.alerts(payload.session, payload.cards)
        alerts = alerts_for(payload, keys)
        for alert in alerts:
            self._outbox.submit(Message.alert(alert))
        return card, len(alerts)

    def _take_notes(self) -> list[str]:
        notes, self._notes = self._notes, []
        return notes

    def premarket(self, t: Instant) -> FindingCard:
        """Queue the opening card at ``t`` (Req 25.7)."""
        p = self._engine.params
        card = premarket_card(
            self.inputs.view(t),
            instruments=self._instruments,
            node_params=p.node_params,
            level_params=p.level_params,
            regime_params=p.regime_params,
            medians=self._engine.trailing_medians(self._session),
            order_mode=self.routing.mode,
            notes=self._take_notes(),
        )
        self._outbox.submit(Message.card(card))
        self.schedule.sent(t)
        return card

    # ------------------------------------------------------------ the end

    def finish(self) -> None:
        """The bars left, the Flat_Deadline close, the saved state, the files closed."""
        if self._finished:
            return
        loop = self.loop
        rest = sorted(
            (bar for bar, _ in self._pending_bars), key=lambda b: (b.open_ns, b.instrument)
        )
        self._pending_bars = []
        deadline = self.window.deadline
        groups = bar_groups(rest, self._calendar.trading_day_start(self._session), deadline)
        loop.finish_session(self.inputs.view(deadline), groups)
        self._save(self._last_t)
        account = self.routing.paper.account
        self.log(
            {
                "event": "session_end",
                "session": self._session.isoformat(),
                "decision_times": self.decisions,
                "trades": len(loop.trades),
                "day_pnl": str(account.day_pnl),
                "balance": str(account.balance),
            }
        )
        self._finished = True
        self.close()

    def close(self) -> None:
        """Close the recorder and the decision log (safe to call more than once)."""
        if self._recorder is not None:
            self._recorder.close()
            self._recorder = None
        if self._dlog is not None:
            self._dlog.close()
            self._dlog = None


# ---------------------------------------------------------------- start log and scheduler


def start_log_entry(
    session: LiveSession, cfg: StrategyConfig, code_version: str
) -> dict[str, JsonValue]:
    """The start log entry of an open session (Req 23.12)."""
    return {
        "event": "start",
        "session": session.session.isoformat(),
        "order_mode": session.routing.mode,
        "config_hash": config_hash(cfg),
        "code_version": code_version,
        "credit_projection": credit_projection(cfg, session.window, session.routing.mode),
    }


def map_wait_ns(cfg: StrategyConfig) -> int:
    """How long the premarket card waits for a first map after its time."""
    return (cfg.live.refresh_interval_s + REFRESH_TIMEOUT_S) * NS_PER_SECOND


@dataclass(frozen=True, slots=True)
class DecisionStep:
    """What one pass of the decision loop does at ``now``."""

    t: Instant | None
    """Call ``session.decide(t)`` when set."""
    clear_input: bool
    """The caller clears its input flag (the scheduler's own is already cleared)."""
    done: bool
    """Deciding is over (``now >= dt_end``, or the next Decision_Time would be)."""


class PlaybookScheduler:
    """The input rule and Decision_Time timing of one session.

    Shared by :class:`LiveRunner` (``fse paper``) and the Playbook plugin of
    ``fse run``, so both feed :class:`LiveSession` by the same rule:

    - **Input rule.** A snapshot batch sets :attr:`input_pending` (and
      :attr:`map_seen`) only when ``LiveSession.on_snapshots`` used one of its
      Snapshots; a bar batch only when it is non-empty; dark-pool prints
      never.
    - **Premarket.** :meth:`premarket_wake` is the card time, then (no map
      yet) the end of the map wait; :meth:`premarket_due` gives the card's
      instant once it is due (a map seen, or the wait over), once.
    - **Decisions.** :meth:`decision_wake` is the next grid tick, the stale
      instant or ``dt_end``; :meth:`decision_step` decides what happens at
      ``now`` after that wait (or after an input).
    """

    __slots__ = (
        "_grid",
        "_k",
        "_last",
        "_map_wait_ns",
        "_map_wait_until",
        "_premarket_sent",
        "_s",
        "_stale",
        "input_pending",
        "map_seen",
    )

    def __init__(self, session: LiveSession, cadence_s: int, map_wait_ns: int) -> None:
        self._s = session
        self._map_wait_ns = map_wait_ns
        w = session.window
        self._grid = [
            t
            for t in session.calendar.decision_times(session.session, cadence_s)
            if w.dt_start <= t < w.dt_end
        ]
        self._k = 0
        self._last: Instant | None = None
        self._stale: Instant | None = None
        self._map_wait_until: Instant | None = None
        self._premarket_sent = False
        self.input_pending = False
        """A new input arrived since the last Decision_Time (``LiveRunner._input``)."""
        self.map_seen = False
        """A used Snapshot arrived (``LiveRunner._map_seen``)."""

    @property
    def session(self) -> LiveSession:
        return self._s

    @property
    def premarket_sent(self) -> bool:
        return self._premarket_sent

    # ------------------------------------------------------------ input rule

    def on_snapshots(self, snapshots: Sequence[Snapshot], received: Instant) -> None:
        if self._s.on_snapshots(snapshots, received):
            self.map_seen = True
            self.input_pending = True

    def on_bars(self, bars: Sequence[Bar], received: Instant) -> None:
        self._s.on_bars(bars, received)
        if bars:
            self.input_pending = True

    def on_dark_pool(self, ticker: str, prints: Sequence[DarkPoolPrint], received: Instant) -> None:
        self._s.on_dark_pool(ticker, prints, received)

    # ------------------------------------------------------------ premarket

    def premarket_wake(self, now: Instant) -> Instant | None:
        """When to look at the premarket card again; ``None`` once it was sent."""
        if self._premarket_sent:
            return None
        premarket = self._s.window.premarket
        if now < premarket:
            return premarket
        if self.map_seen:
            return now
        if self._map_wait_until is None:
            self._map_wait_until = now + self._map_wait_ns
        return self._map_wait_until

    def premarket_due(self, now: Instant) -> Instant | None:
        """The card's instant when it is due at ``now`` (marks it sent); else ``None``."""
        if self._premarket_sent:
            return None
        premarket = self._s.window.premarket
        if now < premarket:
            return None
        if not self.map_seen:
            if self._map_wait_until is None:
                self._map_wait_until = now + self._map_wait_ns
            if now < self._map_wait_until:
                return None
        self._premarket_sent = True
        return max(now, premarket)

    # ------------------------------------------------------------ Decision_Times

    def decision_wake(self, now: Instant) -> Instant:
        """The next grid tick, the stale instant or ``dt_end`` (the decision loop's wait)."""
        s = self._s
        w = s.window
        last = s.last_t
        self._last = last
        grid = self._grid
        while self._k < len(grid) and last is not None and grid[self._k] <= last:
            self._k += 1
        wake = grid[self._k] if self._k < len(grid) else w.dt_end
        self._stale = s.stale_at(now) if now >= w.dt_start else None
        if self._stale is not None:
            wake = min(wake, self._stale)
        return wake

    def decision_step(self, now: Instant) -> DecisionStep:
        """The decision loop's step at ``now``, after :meth:`decision_wake`'s wait."""
        w = self._s.window
        if now >= w.dt_end:
            return DecisionStep(None, clear_input=False, done=True)
        if now < w.dt_start:
            self.input_pending = False
            return DecisionStep(None, clear_input=True, done=False)
        grid = self._grid
        tick = self._k < len(grid) and grid[self._k] <= now
        stale = self._stale
        if not (tick or self.input_pending or (stale is not None and stale <= now)):
            return DecisionStep(None, clear_input=False, done=False)
        self.input_pending = False
        last = self._last
        t = now if last is None or now > last else last + 1
        if t >= w.dt_end:
            return DecisionStep(None, clear_input=True, done=True)
        return DecisionStep(t, clear_input=True, done=False)


# ---------------------------------------------------------------- the async runner


@dataclass(frozen=True, slots=True)
class LiveClients:
    """The network clients the feeds use (fakes in tests)."""

    map: MapClient
    bars: BarRetriever | None = None
    instruments: tuple[FeedInstrument, ...] = ()
    levels: LevelsClient | None = None
    dark_pool: DarkPoolTradesClient | None = None
    bar_poll_s: int = DEFAULT_POLL_INTERVAL_S


class LiveRunner:
    """Feeds, the decision loop, the premarket card and the Notifier for one session."""

    def __init__(
        self,
        session: LiveSession,
        clients: LiveClients,
        clock: Clock,
        notifier: Notifier,
        *,
        code_version: str,
    ) -> None:
        self._s = session
        self._clients = clients
        self._clock = clock
        self._notifier = notifier
        self._code_version = code_version
        self._input = asyncio.Event()
        self._map_seen = asyncio.Event()
        self._sched: PlaybookScheduler | None = None
        self.feed_errors: list[str] = []

    @property
    def session(self) -> LiveSession:
        return self._s

    def _scheduler(self) -> PlaybookScheduler:
        if self._sched is None:
            raise ValueError("the live runner has not started")
        return self._sched

    def _mirror(self) -> None:
        """Copy the scheduler's input flags into the events the loops wait on."""
        sched = self._scheduler()
        if sched.map_seen:
            self._map_seen.set()
        if sched.input_pending:
            self._input.set()

    def _on_snapshots(self, snapshots: Sequence[Snapshot], received: Instant) -> None:
        self._scheduler().on_snapshots(snapshots, received)
        self._mirror()

    def _on_bars(self, bars: Sequence[Bar], received: Instant) -> None:
        self._scheduler().on_bars(bars, received)
        self._mirror()

    def _on_prints(self, ticker: str, prints: Sequence[DarkPoolPrint], received: Instant) -> None:
        self._scheduler().on_dark_pool(ticker, prints, received)

    def start_log(self, cfg: StrategyConfig) -> dict[str, JsonValue]:
        """The start log entry (Req 23.12)."""
        return start_log_entry(self._s, cfg, self._code_version)

    async def run(self, cfg: StrategyConfig) -> None:
        """Run the session: feeds, Decision_Times, the Flat_Deadline close, the Notifier."""
        check_refresh_interval(cfg)
        s = self._s
        s.open(self._clock.now())
        self._sched = PlaybookScheduler(s, cfg.time.decision_cadence_s, map_wait_ns(cfg))
        start = self.start_log(cfg)
        s.log(start)
        credits = start["credit_projection"]
        total = credits.get("total_credits") if isinstance(credits, dict) else None
        s.writer.echo(
            f"Live_Runner {s.session}: Order_Mode {s.routing.mode}, config {start['config_hash']}, "
            f"code {self._code_version}, projected Skylit credits per session {total}"
        )
        notifier_task = asyncio.ensure_future(self._notifier.run())
        safety = s.safety
        if safety is not None:
            await safety.start()  # contracts and the first comparison before any order
        tasks = {
            name: asyncio.ensure_future(self._guarded(name, work))
            for name, work in self._feeds(cfg)
        }
        if safety is not None:
            tasks["broker"] = asyncio.ensure_future(
                self._guarded("broker", safety.run(s.window.dt_end))
            )
        try:
            await self._premarket()
            await self._decisions()
            for name, task in tasks.items():
                if name != "bars":
                    task.cancel()
            bars = tasks.get("bars")
            if bars is not None:
                await asyncio.gather(bars, return_exceptions=True)
            s.finish()
        finally:
            for task in tasks.values():
                task.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)
            s.close()
            self._notifier.close()
            with contextlib.suppress(asyncio.CancelledError):
                await notifier_task

    def _feeds(self, cfg: StrategyConfig) -> list[tuple[str, Coroutine[Any, Any, None]]]:
        s = self._s
        w = s.window
        c = self._clients
        clock = self._clock
        view = heatmap_view(cfg.data.heatmap_view)
        map_feed = MapFeed(
            c.map,
            clock,
            symbols=cfg.data.symbols,
            view=view,
            view_id=view.view_id(),
            mode=cfg.live.mode,
            refresh_interval_s=cfg.live.refresh_interval_s,
            on_snapshots=self._on_snapshots,
            log=s.feed_log(),
        )
        out: list[tuple[str, Coroutine[Any, Any, None]]] = [
            ("map", map_feed.run(w.run_start, w.map_end))
        ]
        if c.bars is not None and c.instruments:
            bar_feed = BarFeed(
                c.bars,
                clock,
                instruments=c.instruments,
                start_ns=s.calendar.trading_day_start(s.session),
                on_bars=self._on_bars,
                log=s.feed_log(),
                poll_interval_s=c.bar_poll_s,
            )
            out.append(("bars", bar_feed.run(w.bars_until)))
        if c.levels is not None and compare_active(s.routing.mode):
            levels = LevelsCompare(
                c.levels,
                clock,
                symbols=cfg.data.symbols,
                inputs=s.inputs,
                node_params=s.engine.params.node_params,
                sink=s.feed_log(),
                interval_s=cfg.live.levels_compare_interval_s,
            )
            out.append(("levels", levels.run(view, w.run_start, w.map_end)))
        gate = cfg.gates.dark_pool_confluence
        if c.dark_pool is not None and gate.enabled:
            dark = DarkPoolFeed(
                c.dark_pool,
                clock,
                tickers=feed_tickers(gate),
                session=s.session,
                on_prints=self._on_prints,
                log=s.feed_log(),
            )
            out.append(("dark_pool", dark.run(w.map_end)))
        return out

    async def _guarded(self, name: str, work: Coroutine[Any, Any, None]) -> None:
        """Run one feed; a failure is logged and the others keep running (guards fail closed)."""
        try:
            await work
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            text = self._s.writer.redact(f"{name} feed stopped: {type(exc).__name__}: {exc}")
            self.feed_errors.append(text)
            self._s.log({"event": "feed_stopped", "feed": name, "cause": text})
            self._s.writer.error(f"error: {text}")

    async def _premarket(self) -> None:
        """The premarket card at its time (at once when it has passed), after the first map."""
        sched = self._scheduler()
        premarket = self._s.window.premarket
        clock = self._clock
        while True:
            now = clock.now()
            wake = sched.premarket_wake(now)
            if wake is None:
                return
            due = sched.premarket_due(now)
            if due is not None:
                self._s.premarket(due)
                return
            if now < premarket:
                await clock.sleep(wake - now)
            else:
                await within(clock, self._map_seen.wait(), wake - now)

    async def _decisions(self) -> None:
        """Decision_Times on each tick, new input and stale instant (see the module notes)."""
        s = self._s
        w = s.window
        clock = self._clock
        sched = self._scheduler()
        while True:
            now = clock.now()
            if now >= w.dt_end:
                return
            wake = sched.decision_wake(now)
            if not self._input.is_set() and wake > now:
                await within(clock, self._input.wait(), wake - now)
            step = sched.decision_step(clock.now())
            if step.clear_input:
                self._input.clear()
            if step.done:
                return
            if step.t is not None:
                s.decide(step.t)
