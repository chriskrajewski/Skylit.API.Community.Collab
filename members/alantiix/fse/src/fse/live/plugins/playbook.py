"""``PlaybookPlugin``: the Skylit Playbook under ``fse run``, no behavior change (design 9).

The plugin wraps :class:`~fse.live.runner.LiveSession` (engine,
``SessionLoop``, ``PaperBroker``, ``AccountSim``, guards, recorder, cards,
state store) and drives it with :class:`~fse.live.runner.PlaybookScheduler`,
the same input rule and timing code ``LiveRunner`` (``fse paper``) uses. It is
self-brokered (``brokerage="self"``): its orders go to the session's own
paper broker through ``paper_routing``, exactly as under ``fse paper``.

**Paper only.** The session is always built with ``mode=None, safety=None``;
after ``open()`` the plugin asserts ``session.safety is None`` and
``session.routing.mode == "paper"``. A failure raises
:class:`PaperOnlyViolation`, which faults the plugin before any decision.

**Per FSE session** ``s`` of a Globex day ``d`` with an exchange session:

- ``on_day_open(d)``: ``prep_day`` (own calendars with the account's
  ``SessionTimes`` and ``session_window``) runs through
  ``services.run_blocking``; its wake arms ``open_at = min(run_start,
  premarket) - open_lead_s``. A session whose Decision_Time window is over is
  skipped (``session_over``).
- At ``open_at - prep_lead_s``: ``prep_session`` (``session_inputs``: Data_Cache,
  Regime medians, VIX, events, feed contracts) through ``run_blocking``.
- At ``open_at`` (once prepared; else on the preparation's wake,
  ``session_open_late``): the Notifier (``services.spawn``), the session
  (``open(now)``), the paper-only assertion, the scheduler and the start log
  (``start_log_entry``). Bars and dark-pool prints dispatched since the day
  opened are buffered until then and delivered as one call per instrument (in
  the engine's instrument order) and one per ticker, received at the open,
  as ``BarFeed``'s and ``DarkPoolFeed``'s first polls would. After a restart
  the day's bars come from the bar store; a resumed session gets only bars
  closing after the newest bar of its own recording.
- Inputs after the open: bars until ``bars_until``; dark-pool prints received
  before ``map_end``; snapshots received in ``[run_start, map_end)``. At the
  first ``DecisionStep(done=True)`` map and print forwarding stop and the
  LevelsCompare task is cancelled, as ``LiveRunner`` cancels its feeds. In
  stream mode the latest snapshot per (symbol, metric) received before
  ``run_start`` is forwarded once at ``run_start`` (the stream seed).
- At ``bars_until``: ``session.finish()`` (the Flat_Deadline close) and the
  Notifier closes.

**Per-day errors skip the day, never fault:** ``LiveStartError``,
``CalendarError``, ``ValueError`` from ``session_window`` (``NotASessionError``)
and ``StateDecodeError`` give a ``session_skipped`` error log; the plugin waits
for the next ``on_day_open``. Anything else faults the plugin.

``on_stop`` closes the session without ``finish()``, so a restart resumes it.
"""

from __future__ import annotations

import asyncio
import secrets
import time as _time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fse.backtest.manifest import describe_code_version
from fse.backtest.runner import trading_fees
from fse.calendars import CalendarError, Calendars, load_calendars
from fse.commands._config import load_config
from fse.commands.paper import SessionInputs, session_inputs
from fse.config.schema import StrategyConfig
from fse.data.path_guard import check_output_dirs
from fse.engine.state import StateDecodeError
from fse.engine.step import EngineParams
from fse.engine.types import Bar, DarkPoolPrint, Snapshot
from fse.live.darkpool_feed import feed_tickers
from fse.live.levels_compare import LevelsCompare, compare_active
from fse.live.plugins.api import (
    BarNeed,
    DataNeeds,
    GlobexDay,
    Intent,
    PluginContext,
    PluginServices,
    SkylitNeed,
    StrategyPlugin,
)
from fse.live.recorder import RecordingError, decode_bar, iter_recording
from fse.live.runner import (
    LiveSession,
    LiveStartError,
    PlaybookScheduler,
    SessionWindow,
    check_refresh_interval,
    map_wait_ns,
    session_window,
    start_log_entry,
)
from fse.notify.notifier import Notifier
from fse.pit.market_view import heatmap_view
from fse.settings import default_paths
from fse.settings import project_dir as default_project_dir
from fse.timekit import NS_PER_SECOND, Instant, SessionTimes, ny_instant

__all__ = [
    "PaperOnlyViolation",
    "PlaybookPlugin",
    "PlaybookPluginConfig",
    "PlaybookPluginError",
]

SKIP_ERRORS: Final = (LiveStartError, CalendarError, StateDecodeError, ValueError)
"""Per-day conditions that skip the session (``StateDecodeError`` is a ``ValueError`` too)."""
MAX_STEPS_PER_WAKE: Final = 64
"""Bound on the steps one ``on_wake`` takes at one instant (a guard, never reached)."""


class PlaybookPluginError(ValueError):
    """A bad plugin config or a failed start check (the host exits 2)."""


class PaperOnlyViolation(RuntimeError):
    """The opened session is not paper-routed: the plugin faults before any decision."""


class PlaybookPluginConfig(BaseModel):
    """The plugin's ``config`` block in the runner YAML (design 9.2)."""

    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, use_attribute_docstrings=True
    )
    strategy_config: str
    """The Strategy_Config file (relative paths are under the Project folder)."""
    out_root: str = "~/.skylit-fse/runs"
    """Run directories go to ``<out_root>/live-<session>-<hex8>/``."""
    open_lead_s: int = Field(300, ge=0, le=3600)
    """How long before ``min(run_start, premarket)`` the session opens."""
    prep_lead_s: int = Field(120, ge=0, le=3600)
    """How long before the open the session inputs are prepared (off the dispatcher)."""


@dataclass(frozen=True, slots=True)
class _DayPlan:
    """The result of ``prep_day``: the session's own calendars and windows."""

    calendars: Calendars
    window: SessionWindow


def _prep_day(
    calendar_dir: Path, session: date, times: SessionTimes, cfg: StrategyConfig
) -> _DayPlan:
    calendars = load_calendars(calendar_dir, session, session, times=times)
    return _DayPlan(calendars, session_window(cfg, calendars.exchange.sessions, session))


class PlaybookPlugin(StrategyPlugin):
    """The Skylit Playbook (``LiveSession``) as a self-brokered ``fse run`` plugin."""

    label = "Skylit Playbook"
    description = (
        "The Playbook engine (a Strategy_Config) as a plugin; it books its own paper orders."
    )
    brokerage = "self"
    config_model = PlaybookPluginConfig

    perf_ns: Callable[[], int] = staticmethod(_time.perf_counter_ns)
    """The session's latency timer (tests pin it)."""

    def __init__(
        self, plugin_id: str, config: Mapping[str, object], services: PluginServices
    ) -> None:
        super().__init__(plugin_id, config, services)
        try:
            opts = PlaybookPluginConfig.model_validate(dict(config))
        except ValidationError as exc:
            raise PlaybookPluginError(f"playbook plugin config: {exc}") from exc
        self.opts = opts
        project = services.project_dir or default_project_dir()
        path = Path(opts.strategy_config).expanduser()
        if not path.is_absolute():
            if project is None:
                raise PlaybookPluginError(
                    f"strategy_config {opts.strategy_config} is relative and there is no "
                    "Project folder"
                )
            path = project / path
        cfg = load_config(path, services.writer)
        check_refresh_interval(cfg)
        params = EngineParams.from_sections(cfg)
        trading_fees(cfg, params.instruments)  # exit 2 at start when a fee is unset
        self.cfg = cfg
        self._instruments: tuple[str, ...] = tuple(params.instruments)
        self._view_id = heatmap_view(cfg.data.heatmap_view).view_id()
        self._tickers = feed_tickers(cfg.gates.dark_pool_confluence)
        self._times = cfg.account.session_times(SessionTimes())
        self._window_start = self._check_window(cfg, services.skylit_window)
        out = check_output_dirs({"playbook out_root": Path(opts.out_root).expanduser()})
        self._out_root = out["playbook out_root"]
        self._recordings_root = services.recordings_root or default_paths().root
        self._code_version = self.code_version()
        self._notes: list[str] = []
        if cfg.order_mode != "paper":
            self._notes.append(
                f"fse run runs the Playbook in Paper Order_Mode; the config's order_mode "
                f"{cfg.order_mode} is not used"
            )
        self._started_ns: Instant | None = None
        self._reset_day()

    # ------------------------------------------------------------ overridable (tests)

    def code_version(self) -> str:
        """The start log's code version (computed once, at construction)."""
        return describe_code_version(self.services.project_dir)

    def build_session(self, **kwargs: Any) -> LiveSession:
        """The session object (tests substitute a stub here)."""
        return LiveSession(**kwargs)

    # ------------------------------------------------------------ construction checks

    @staticmethod
    def _check_window(cfg: StrategyConfig, plane: tuple[time, time] | None) -> time:
        start = cfg.live.run_window.start
        if plane is None:
            return start
        plane_start = plane[0]
        day = date(2026, 1, 5)  # any date: wall-clock arithmetic on one day only
        gap = ny_instant(day, start) - ny_instant(day, plane_start)
        if gap < 0:
            raise PlaybookPluginError(
                f"data_plane.skylit.window.start {plane_start:%H:%M} is after "
                f"live.run_window.start {start:%H:%M}"
            )
        if cfg.live.mode == "polling":
            # The data plane's poll ticks must fall on MapFeed.run(run_start, ...)'s.
            step = cfg.live.refresh_interval_s * NS_PER_SECOND
            if gap % step:
                raise PlaybookPluginError(
                    f"data_plane.skylit.window.start {plane_start:%H:%M} must be a whole number "
                    f"of refresh intervals ({cfg.live.refresh_interval_s} s) before "
                    f"live.run_window.start {start:%H:%M}"
                )
        return plane_start

    # ------------------------------------------------------------ per-day state

    def _reset_day(self) -> None:
        self._day: GlobexDay | None = None
        self._prep_day_task: asyncio.Task[_DayPlan] | None = None
        self._plan: _DayPlan | None = None
        self._open_at: Instant | None = None
        self._prep_task: asyncio.Task[SessionInputs] | None = None
        self._inputs: SessionInputs | None = None
        self._session: LiveSession | None = None
        self._sched: PlaybookScheduler | None = None
        self._notifier: Notifier | None = None
        self._levels: asyncio.Task[None] | None = None
        self._run_dir: Path | None = None
        self._done = False
        self._done_at: Instant | None = None
        self._finished = False
        self._seed: dict[tuple[str, str], Snapshot] = {}
        self._seed_sent = False
        self._bars: dict[str, list[tuple[Bar, Instant]]] = {}
        self._prints: list[tuple[str, tuple[DarkPoolPrint, ...]]] = []
        self._snaps: list[tuple[tuple[Snapshot, ...], Instant]] = []
        self._early: list[tuple[tuple[Snapshot, ...], Instant]] = []
        self._fed: dict[str, Instant] = {}

    @property
    def session(self) -> LiveSession | None:
        """Today's open session (``None`` before the open and after a skip)."""
        return self._session

    @property
    def scheduler(self) -> PlaybookScheduler | None:
        return self._sched

    @property
    def run_dir(self) -> Path | None:
        """Today's run directory, once the session is open."""
        return self._run_dir

    # ------------------------------------------------------------ needs

    def needs(self) -> DataNeeds:
        cfg = self.cfg
        return DataNeeds(
            bars=tuple(BarNeed(i) for i in self._instruments),
            hours="rth",
            skylit=SkylitNeed(
                symbols=tuple(cfg.data.symbols),
                view_id=self._view_id,
                mode=cfg.live.mode,
                refresh_interval_s=cfg.live.refresh_interval_s,
                window=(self._window_start, cfg.live.run_window.end),
                dark_pool_tickers=self._tickers,
            ),
            brokerage="self",
            hold_through_break=False,
        )

    # ------------------------------------------------------------ hooks

    def on_start(self, ctx: PluginContext) -> Sequence[Intent]:
        self._started_ns = ctx.now
        return ()

    def on_day_open(self, day: GlobexDay, ctx: PluginContext) -> Sequence[Intent]:
        if self._day is not None and self._day.day == day.day:
            return ()  # already open (12.1 step 8 after a start inside the day)
        self._close_session(finish=True)
        self._reset_day()
        if not day.exchange_session:
            return ()
        self._day = day
        self._prep_day_task = self.services.run_blocking(
            "prep_day", _prep_day, self.services.calendar_dir, day.day, self._times, self.cfg
        )
        self._advance_all(ctx)
        return ()

    def on_day_close(self, day: GlobexDay, ctx: PluginContext) -> Sequence[Intent]:
        if self._day is not None and self._day.day == day.day:
            self._close_session(finish=True)
            self._reset_day()
        return ()

    def on_bar(
        self, bar: Bar, received_ns: Instant, catch_up: bool, ctx: PluginContext
    ) -> Sequence[Intent]:
        day = self._day
        if day is None or bar.open_ns < day.open_ns or bar.instrument not in self._instruments:
            return ()
        sched = self._sched
        if sched is None:
            if self._session is None and not self._finished:
                self._bars.setdefault(bar.instrument, []).append((bar, received_ns))
            return ()
        w = sched.session.window
        if self._finished or received_ns >= w.bars_until:
            return ()
        if bar.open_ns <= self._fed.get(bar.instrument, -1):
            return ()
        self._fed[bar.instrument] = bar.open_ns
        sched.on_bars((bar,), received_ns)
        return ()

    def on_snapshots(
        self, snaps: Sequence[Snapshot], received_ns: Instant, ctx: PluginContext
    ) -> Sequence[Intent]:
        if self._day is None or self._cut(received_ns) or self._finished:
            return ()
        if self._plan is None:  # the window is not known yet (prep_day is running)
            self._early.append((tuple(snaps), received_ns))
            return ()
        self._route_snapshots(snaps, received_ns)
        return ()

    def _route_snapshots(self, snaps: Sequence[Snapshot], received_ns: Instant) -> None:
        plan = self._plan
        assert plan is not None
        w = plan.window
        if received_ns < w.run_start:
            if self.cfg.live.mode == "stream":  # the latest per key, for the stream seed
                for snap in snaps:
                    self._seed[(snap.symbol, snap.metric)] = snap
            return
        if received_ns >= w.map_end:
            return
        sched = self._sched
        if sched is None:
            self._snaps.append((tuple(snaps), received_ns))
            return
        self._send_seed(sched, w)
        sched.on_snapshots(snaps, received_ns)

    def on_dark_pool(
        self,
        ticker: str,
        prints: Sequence[DarkPoolPrint],
        received_ns: Instant,
        ctx: PluginContext,
    ) -> Sequence[Intent]:
        if (
            self._day is None
            or self._cut(received_ns)
            or self._finished
            or ticker not in self._tickers
        ):
            return ()
        sched = self._sched
        if sched is None:
            if self._session is None:
                self._prints.append((ticker, tuple(prints)))
            return ()
        if received_ns >= sched.session.window.map_end:
            return ()
        sched.on_dark_pool(ticker, prints, received_ns)
        return ()

    def next_wake(self, now: Instant) -> Instant | None:
        if self._day is None or self._finished:
            return None
        sched = self._sched
        if sched is None:
            if self._plan is None or self._open_at is None:
                return None  # prep_day is running: its task_done wake follows
            if self._prep_task is None:
                return self._open_at - self.opts.prep_lead_s * NS_PER_SECOND
            if self._prep_task.done():
                return self._open_at
            return None  # prep_session is running
        w = sched.session.window
        wakes: list[Instant] = []
        if self._seed_pending(w):
            wakes.append(w.run_start)
        if not sched.premarket_sent:
            wake = sched.premarket_wake(now)
            if wake is not None:
                wakes.append(wake)
        elif not self._done:
            # NIT 3: a pending input wakes the plugin at once while a decision is possible.
            wakes.append(now if sched.input_pending else sched.decision_wake(now))
        else:
            wakes.append(w.bars_until)
        return min(wakes)

    def on_wake(self, now: Instant, ctx: PluginContext) -> Sequence[Intent]:
        self._advance_all(ctx)
        return ()

    def on_stop(self, ctx: PluginContext) -> None:
        self._close_session(finish=False)

    # ------------------------------------------------------------ the day's steps

    def _advance_all(self, ctx: PluginContext) -> None:
        """Every step due at ``now`` (``LiveRunner``'s loops run without waiting between them)."""
        now = ctx.now
        for _ in range(MAX_STEPS_PER_WAKE):
            if not self._advance(now, ctx):
                return

    def _skip(self, ctx: PluginContext, exc: BaseException) -> None:
        day = self._day
        cause = self.services.writer.redact(f"{type(exc).__name__}: {exc}")
        ctx.log(
            {
                "kind": "session_skipped",
                "level": "error",
                "session": None if day is None else day.day.isoformat(),
                "cause": cause,
            }
        )
        self.services.writer.error(f"error: playbook session skipped: {cause}")
        self._close_session(finish=False)
        self._reset_day()

    def _advance(self, now: Instant, ctx: PluginContext) -> bool:
        """One step at ``now``; whether anything changed."""
        day = self._day
        if day is None or self._finished:
            return False
        if self._plan is None:
            task = self._prep_day_task
            if task is None or not task.done():
                return False
            self._prep_day_task = None
            try:
                plan = task.result()
            except SKIP_ERRORS as exc:
                self._skip(ctx, exc)
                return False
            self._plan = plan
            w = plan.window
            if now >= w.dt_end:
                ctx.log({"kind": "session_over", "level": "info", "session": day.day.isoformat()})
                self._reset_day()
                return False
            self._open_at = min(w.run_start, w.premarket) - self.opts.open_lead_s * NS_PER_SECOND
            early, self._early = self._early, []
            for snaps, received in early:
                self._route_snapshots(snaps, received)
            return True
        if self._sched is None:
            return self._advance_open(now, ctx)
        return self._advance_session(now)

    def _advance_open(self, now: Instant, ctx: PluginContext) -> bool:
        plan, open_at = self._plan, self._open_at
        assert plan is not None
        assert open_at is not None
        if self._prep_task is None:
            if now < open_at - self.opts.prep_lead_s * NS_PER_SECOND:
                return False
            self._prep_task = self.services.run_blocking(
                "prep_session",
                session_inputs,
                self.cfg,
                plan.calendars,
                plan.window.session,
                self.services.cache_dir,
            )
            return True
        if not self._prep_task.done() or now < open_at:
            return False
        try:
            self._inputs = self._prep_task.result()
        except SKIP_ERRORS as exc:
            self._skip(ctx, exc)
            return False
        if now > open_at:
            ctx.log(
                {
                    "kind": "session_open_late",
                    "level": "warning",
                    "session": plan.window.session.isoformat(),
                    "delay_ms": (now - open_at) // 1_000_000,
                }
            )
        if now >= plan.window.dt_end:
            ctx.log(
                {
                    "kind": "session_over",
                    "level": "info",
                    "session": plan.window.session.isoformat(),
                }
            )
            self._reset_day()
            return False
        return self._open(now, ctx)

    def _open(self, now: Instant, ctx: PluginContext) -> bool:
        """Open the session (fse paper started at ``now``); ``False`` when it is skipped."""
        plan, inputs, day = self._plan, self._inputs, self._day
        assert plan is not None
        assert inputs is not None
        assert day is not None
        cfg = self.cfg
        services = self.services
        session = plan.window.session
        run_dir = self._out_root / f"live-{session.isoformat()}-{secrets.token_hex(4)}"
        notifier = Notifier(
            cfg.notify, services.env, services.writer, services.clock, run_dir, http=services.http
        )
        try:
            live = self.build_session(
                cfg=cfg,
                engine=inputs.engine,
                calendar=plan.calendars.exchange.sessions,
                session=session,
                writer=services.writer,
                run_dir=run_dir,
                state_dir=services.state_dir,
                recordings_root=self._recordings_root,
                outbox=notifier,
                events=inputs.events,
                vix_daily=inputs.vix,
                notes=list(self._notes),
                perf_ns=self.perf_ns,
                mode=None,
                safety=None,
                bar_store=services.bar_store,
            )
            resumed = self._recorded_closes(live)
            live.open(now)
        except SKIP_ERRORS as exc:
            self._skip(ctx, exc)
            return False
        # Paper only (design 10.1): checked after open(), before any decision.
        if live.safety is not None or live.routing.mode != "paper":
            live.close()
            raise PaperOnlyViolation(
                f"the Playbook session is not paper-routed (mode {live.routing.mode!r}, "
                f"safety {'set' if live.safety is not None else 'none'})"
            )
        self._session = live
        self._run_dir = run_dir
        self._notifier = notifier
        services.spawn("notifier", notifier.run())
        sched = PlaybookScheduler(live, cfg.time.decision_cadence_s, map_wait_ns(cfg))
        self._sched = sched
        start = start_log_entry(live, cfg, self._code_version)
        live.log(start)
        credits = start["credit_projection"]
        total = credits.get("total_credits") if isinstance(credits, dict) else None
        services.writer.echo(
            f"Playbook {session}: Order_Mode {live.routing.mode}, config {start['config_hash']}, "
            f"code {self._code_version}, projected Skylit credits per session {total}, "
            f"run directory {run_dir}"
        )
        self._catch_up(sched, now, resumed)
        if services.skylit_levels is not None and compare_active(live.routing.mode):
            view = heatmap_view(cfg.data.heatmap_view)
            levels = LevelsCompare(
                services.skylit_levels,
                services.clock,
                symbols=cfg.data.symbols,
                inputs=live.inputs,
                node_params=live.engine.params.node_params,
                sink=live.feed_log(),
                interval_s=cfg.live.levels_compare_interval_s,
            )
            w = live.window
            self._levels = services.spawn("levels", levels.run(view, w.run_start, w.map_end))
        return True

    def _recorded_closes(self, live: LiveSession) -> dict[str, Instant]:
        """The newest bar close per instrument in the session's recording (a resumed session)."""
        path = live.recording
        if not path.is_file():
            return {}
        out: dict[str, Instant] = {}
        try:
            for entry in iter_recording(path, kinds=("bar",)):  # streamed, bounded memory
                try:
                    bar = decode_bar(entry.payload)
                except RecordingError, ValueError:
                    continue
                out[bar.instrument] = max(out.get(bar.instrument, 0), bar.close_ns)
        except RecordingError, OSError, ValueError:
            return {}
        return out

    def _catch_up(
        self, sched: PlaybookScheduler, now: Instant, resumed: Mapping[str, Instant]
    ) -> None:
        """The inputs dispatched before the open, as the feeds' first polls would deliver them."""
        day = self._day
        assert day is not None
        late_start = self._started_ns is not None and self._started_ns > day.open_ns
        for instrument in self._instruments:
            got: dict[Instant, Bar] = {b.open_ns: b for b, _ in self._bars.get(instrument, [])}
            if late_start:  # after a restart the day's earlier bars are in the bar store only
                for b in self.services.bar_store.bars(instrument, day.day):
                    got.setdefault(b.open_ns, b)
            floor = resumed.get(instrument)
            bars = tuple(
                got[k]
                for k in sorted(got)
                if got[k].close_ns <= now and (floor is None or got[k].close_ns > floor)
            )
            if bars:
                self._fed[instrument] = bars[-1].open_ns
                sched.on_bars(bars, now)
        self._bars = {}
        merged: dict[str, list[DarkPoolPrint]] = {}
        for ticker, prints in self._prints:
            merged.setdefault(ticker, []).extend(prints)
        for ticker in self._tickers:
            if ticker in merged:
                sched.on_dark_pool(ticker, tuple(merged[ticker]), now)
        self._prints = []
        w = sched.session.window
        if self._snaps:
            self._send_seed(sched, w)
        for snaps, received in self._snaps:
            sched.on_snapshots(snaps, received)
        self._snaps = []

    def _seed_pending(self, w: SessionWindow) -> bool:
        return (
            self.cfg.live.mode == "stream"
            and not self._seed_sent
            and bool(self._seed)
            and not self._done
        )

    def _send_seed(self, sched: PlaybookScheduler, w: SessionWindow) -> None:
        """The stream seed at ``run_start``: the latest pre-``run_start`` snapshot per key."""
        if not self._seed_pending(w):
            self._seed_sent = True
            return
        self._seed_sent = True
        seed = tuple(self._seed[k] for k in sorted(self._seed))
        self._seed = {}
        sched.on_snapshots(seed, w.run_start)

    def _advance_session(self, now: Instant) -> bool:
        sched, live = self._sched, self._session
        assert sched is not None
        assert live is not None
        w = live.window
        if not self._seed_sent and now >= w.run_start:
            self._send_seed(sched, w)
            return True
        if not sched.premarket_sent:
            due = sched.premarket_due(now)
            if due is None:
                return False
            live.premarket(due)
            return True
        if not self._done:
            wake = sched.decision_wake(now)
            if not sched.input_pending and wake > now:
                return False
            step = sched.decision_step(now)
            if step.done:
                self._cut_off(now)
                return True
            if step.t is not None:
                live.decide(step.t)
                return True
            return step.clear_input
        if now >= w.bars_until:
            self._close_session(finish=True)
            return False
        return False

    def _cut(self, received_ns: Instant) -> bool:
        """Whether a map or print input received at ``received_ns`` is past the cut-off."""
        return self._done_at is not None and received_ns > self._done_at

    def _cut_off(self, now: Instant) -> None:
        """Deciding is over: no more map or print forwarding, LevelsCompare cancelled.

        Inputs received at the instant of the last step still go in: ``LiveRunner``'s
        feeds deliver what they received at that instant before its loop cancels them.
        """
        self._done = True
        self._done_at = now
        self._snaps = []
        self._prints = []
        if self._levels is not None:
            self._levels.cancel()
            self._levels = None

    def _close_session(self, *, finish: bool) -> None:
        live = self._session
        if live is not None and not self._finished:
            if finish:
                live.finish()
            else:
                live.close()
        if self._levels is not None:
            self._levels.cancel()
            self._levels = None
        if self._notifier is not None:
            self._notifier.close()
            self._notifier = None
        if live is not None:
            self._finished = True
