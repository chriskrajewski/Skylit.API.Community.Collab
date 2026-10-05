"""Simulated live sessions for the Live_Runner properties (70, 71, 72, 80) and tests.

:class:`Driver` runs :class:`~fse.live.runner.LiveSession` synchronously over
the synthetic sessions of :mod:`tests.strategies.backtest_inputs`, the way
:class:`~fse.live.runner.LiveRunner` would, without asyncio:

- **Inputs** (:func:`live_events`): with ``refresh_s > 0`` every configured
  (symbol, metric) gets a fresh Snapshot each ``refresh_s`` from 09:00 (the
  session's latest Snapshot re-stamped with the refresh instant as ``asOf``),
  so the map stays fresh except in the drawn outage; with ``refresh_s == 0``
  only the session's own Snapshots. Bars, VIX bars and dark-pool prints come
  from the session. Each input arrives a drawn 0 to ``max_delay_s`` seconds
  after its Observation_Time (the closed bars at least at their close).
- **Decision_Times**: the Decision_Cadence grid inside the Decision_Time
  window, plus a drawn share of the input arrivals in it. An input that
  arrives at the instant of a Decision_Time comes before or after it (drawn),
  so the receipt-time rule is exercised.
- **Guards**: drawn halt-file windows (the file exists from ``on`` to ``off``).
- After the window, the remaining inputs arrive and the session finishes
  (the Paper_Broker's Flat_Deadline close).

Every session of a :class:`~tests.strategies.backtest_inputs.MarketInputs`
that the Backtester evaluates runs in turn with one live-state directory, so
each session after the first starts from the saved state of the one before.
:meth:`Driver.replay` runs the Backtester's replay over the recordings.
"""

from __future__ import annotations

import io
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, time
from pathlib import Path
from typing import Any, Final

from hypothesis import strategies as st

from fse.backtest.decision_log import DECISION_LOG_FILE_NAME
from fse.backtest.runner import BacktestResult, run_backtest
from fse.calendars import load_calendars
from fse.config.schema import StrategyConfig
from fse.data.cache import DataCache
from fse.data.medians import MedianParams, RegimeMedianStore
from fse.data.vix import prior_session
from fse.engine.step import Engine, EngineParams
from fse.engine.types import DarkPoolPrint, Snapshot
from fse.live.runner import LiveDecision, LiveSession, session_window
from fse.live.state_store import halt_file
from fse.logio import LogWriter, Redactor
from fse.logio.canonical_json import ny_iso
from fse.notify.notifier import Message
from fse.pit.market_view import heatmap_view
from fse.timekit import NS_PER_SECOND, Instant, SessionCalendar, SessionTimes, ny_instant
from tests.strategies.backtest_inputs import (
    CODE_VERSION,
    FAKE_SECRET,
    SEED,
    STARTED,
    MarketInputs,
    SessionInputs,
    write_cache,
    write_calendars,
)

__all__ = ["Driver", "LivePlan", "Outbox", "live_events", "live_plans"]

_REFRESH_START: Final = time(9, 0)


@dataclass(frozen=True, slots=True)
class LivePlan:
    """How one simulated live session delivers its inputs (see the module notes)."""

    seed: int = 0
    refresh_s: int = 60
    max_delay_s: int = 3
    outage: tuple[int, int] | None = None
    """A refresh outage: seconds after 09:30 and its length; no Snapshot arrives in it."""
    extra_dt_share: float = 0.2
    halts: tuple[tuple[int, int], ...] = ()
    """Halt-file windows, seconds after 09:30: the file exists from ``on`` to ``off``."""


@st.composite
def live_plans(draw: st.DrawFn, *, halts: bool = True, refresh: bool = True) -> LivePlan:
    """A drawn :class:`LivePlan`."""
    outage = None
    if draw(st.booleans(), label="outage"):
        outage = (draw(st.integers(0, 20_000)), draw(st.integers(1, 3_000)))
    windows: tuple[tuple[int, int], ...] = ()
    if halts and draw(st.booleans(), label="halt"):
        on = draw(st.integers(0, 22_000))
        windows = ((on, on + draw(st.integers(1, 4_000))),)
    return LivePlan(
        seed=draw(st.integers(0, 2**32 - 1), label="seed"),
        refresh_s=draw(st.sampled_from((30, 60, 300))) if refresh else 0,
        max_delay_s=draw(st.integers(0, 40), label="max delay"),
        outage=outage,
        extra_dt_share=draw(st.sampled_from((0.0, 0.1, 0.5))),
        halts=windows,
    )


# ---------------------------------------------------------------- inputs


@dataclass(frozen=True, slots=True)
class _Input:
    at: Instant
    seq: int
    kind: str
    item: Any


def _restamp(snap: Snapshot, as_of: Instant) -> Snapshot:
    return replace(snap, as_of_ns=as_of, as_of_raw=ny_iso(as_of))


def live_events(
    s: SessionInputs, plan: LivePlan, cal: SessionCalendar, rng: random.Random
) -> list[_Input]:
    """The session's inputs with their receipt times, in arrival order."""
    out: list[_Input] = []
    seq = 0

    def add(at: Instant, kind: str, item: object) -> None:
        nonlocal seq
        out.append(_Input(at, seq, kind, item))
        seq += 1

    delay = plan.max_delay_s * NS_PER_SECOND
    rth = cal.rth_open(s.session)

    def blocked(at: Instant) -> bool:
        if plan.outage is None:
            return False
        lo = rth + plan.outage[0] * NS_PER_SECOND
        return lo <= at < lo + plan.outage[1] * NS_PER_SECOND

    snaps = sorted(s.snapshots, key=lambda x: x.as_of_ns)
    for snap in snaps:
        if not blocked(snap.as_of_ns):
            add(snap.as_of_ns + rng.randint(0, delay), "snapshot", snap)
    if plan.refresh_s > 0:
        start = ny_instant(s.session, _REFRESH_START)
        end = cal.pull_window(s.session)[1]
        latest: dict[tuple[str, str], Snapshot] = {}
        i = 0
        for t in range(start, end, plan.refresh_s * NS_PER_SECOND):
            while i < len(snaps) and snaps[i].as_of_ns <= t:
                latest[(snaps[i].symbol, snaps[i].metric)] = snaps[i]
                i += 1
            if blocked(t):
                continue
            for snap in latest.values():
                if snap.as_of_ns < t:
                    add(t + rng.randint(0, delay), "snapshot", _restamp(snap, t))
    for bar in s.bars:
        add(bar.close_ns + rng.randint(0, delay), "bar", bar)
    for bar in s.vix_bars:
        add(bar.close_ns + rng.randint(0, delay), "vix_bar", bar)
    for p in s.prints:
        add(p.ts_ns + rng.randint(0, delay), "print", p)
    out.sort(key=lambda e: (e.at, e.seq))
    return out


# ---------------------------------------------------------------- the driver


@dataclass(slots=True)
class Outbox:
    """Collects the messages a LiveSession queues."""

    messages: list[Message] = field(default_factory=list)

    def submit(self, message: Message) -> None:
        self.messages.append(message)


type Hook = Callable[[LiveSession, LiveDecision], None]


class Driver:
    """Runs simulated live sessions and their replay (see the module notes)."""

    def __init__(self, root: Path, market: MarketInputs, cfg: StrategyConfig) -> None:
        self.root = root
        self.market = market
        self.cfg = cfg
        self.calendar_dir = write_calendars(root / "calendars")
        self.cache_root = write_cache(root / "cache", market)
        times = cfg.account.session_times(SessionTimes())
        span = market.data_range
        calendars = load_calendars(self.calendar_dir, span.start, span.end, times=times)
        self.calendar = calendars.exchange.sessions
        self.events = calendars.events.events
        params = EngineParams.from_sections(cfg)
        view_id = heatmap_view(cfg.data.heatmap_view).view_id()
        with DataCache(self.cache_root, calendar=self.calendar) as cache:
            medians = RegimeMedianStore(cache).load_or_build(
                self.calendar,
                view_id=view_id,
                params=MedianParams.from_config(cfg.regime, cfg.data),
            )
            self.daily = cache.vix.read_daily()
        self.engine = Engine(params, self.calendar, medians.by_session)
        self.state_dir = root / "live-state"
        self.recordings_root = root / "live"
        self.outbox = Outbox()
        self.writer = LogWriter(Redactor([FAKE_SECRET]), stdout=io.StringIO(), stderr=io.StringIO())
        self.sessions: list[LiveSession] = []
        self.decisions: list[LiveDecision] = []
        self.crash_at = 0

    def new_session(self, day: date, *, run: str | None = None, **kwargs: Any) -> LiveSession:
        prior = prior_session(self.calendar, day)
        return LiveSession(
            cfg=self.cfg,
            engine=kwargs.pop("engine", self.engine),
            calendar=self.calendar,
            session=day,
            writer=self.writer,
            run_dir=self.root / "runs" / (run or f"live-{day.isoformat()}-{len(self.sessions)}"),
            state_dir=self.state_dir,
            recordings_root=self.recordings_root,
            outbox=kwargs.pop("outbox", self.outbox),
            events=[e for e in self.events if abs((e.release_date - day).days) <= 1],
            vix_daily=(self.daily.get(day), None if prior is None else self.daily.get(prior)),
            **kwargs,
        )

    def run_market(
        self,
        plans: Sequence[LivePlan],
        hook: Hook | None = None,
        *,
        every_session: bool = False,
        **kwargs: Any,
    ) -> None:
        """Each evaluated session (or every session) in order, each with its plan.

        The last plan repeats for the sessions after it.
        """
        chosen = self.market.sessions if every_session else self.market.evaluated
        for i, s in enumerate(chosen):
            self.run_session(s, plans[min(i, len(plans) - 1)], hook, **kwargs)

    def schedule(self, s: SessionInputs, plan: LivePlan) -> list[tuple[str, Any]]:
        """The session's actions in order: ``("input", _Input)`` and ``("decide", t)``."""
        cal = self.calendar
        rng = random.Random(plan.seed)
        w = session_window(self.cfg, cal, s.session)
        events = live_events(s, plan, cal, rng)
        grid = [
            t
            for t in cal.decision_times(s.session, self.cfg.time.decision_cadence_s)
            if w.dt_start <= t < w.dt_end
        ]
        extra = [
            e.at
            for e in events
            if w.dt_start <= e.at < w.dt_end and rng.random() < plan.extra_dt_share
        ]
        actions: list[tuple[str, Any]] = []
        j = 0
        for t in sorted(set(grid) | set(extra)):
            while j < len(events) and (
                events[j].at < t or (events[j].at == t and rng.random() < 0.5)
            ):
                actions.append(("input", events[j]))
                j += 1
            actions.append(("decide", t))
        actions.extend(("input", e) for e in events[j:])
        return actions

    def run_session(
        self,
        s: SessionInputs,
        plan: LivePlan,
        hook: Hook | None = None,
        *,
        stop_after: int | None = None,
        resume_at: int = 0,
        **kwargs: Any,
    ) -> LiveSession:
        """One live session (see the module notes).

        ``stop_after`` Decision_Times, then a simulated crash: the session is
        closed and :attr:`crash_at` holds the number of actions done.
        ``resume_at`` skips that many actions (a restart after such a crash).
        """
        live = self.new_session(s.session, **kwargs)
        self.sessions.append(live)
        try:
            return self._run(live, s, plan, hook, stop_after, resume_at)
        except BaseException:
            live.close()
            raise

    def _run(
        self,
        live: LiveSession,
        s: SessionInputs,
        plan: LivePlan,
        hook: Hook | None,
        stop_after: int | None,
        resume_at: int,
    ) -> LiveSession:
        cal = self.calendar
        live.open(cal.trading_day_start(s.session))
        rth = cal.rth_open(s.session)
        halts = [(rth + on * NS_PER_SECOND, rth + off * NS_PER_SECOND) for on, off in plan.halts]
        path = halt_file(self.state_dir)
        done = 0
        for i, (kind, item) in enumerate(self.schedule(s, plan)):
            if i < resume_at:
                continue
            if kind == "input":
                self._deliver(live, item)
                continue
            t: Instant = item
            on = any(lo <= t < hi for lo, hi in halts)
            if on and not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("halt\n", encoding="utf-8")
            elif not on and path.exists():
                path.unlink()
            last = live.last_t
            decision = live.decide(t if last is None or t > last else last + 1)
            self.decisions.append(decision)
            if hook is not None:
                hook(live, decision)
            done += 1
            if stop_after is not None and done >= stop_after:
                self.crash_at = i + 1
                live.close()  # a crash: nothing after this Decision_Time is kept
                return live
        if path.exists():
            path.unlink()
        live.finish()
        return live

    @staticmethod
    def _deliver(live: LiveSession, e: _Input) -> None:
        match e.kind:
            case "snapshot":
                live.on_snapshots((e.item,), e.at)
            case "bar":
                live.on_bars((e.item,), e.at)
            case "vix_bar":
                live.on_vix_bar(e.item, e.at)
            case _:
                p: DarkPoolPrint = e.item
                live.on_dark_pool(p.ticker, (p,), e.at)

    def live_log(self) -> bytes:
        """Every live session's decision log, in run order."""
        return b"".join(s.decision_log.read_bytes() for s in self.sessions)

    def replay(self, out: str = "replay") -> BacktestResult:
        """The Backtester's replay of every recording."""
        with DataCache(self.cache_root, calendar=self.calendar) as cache:
            return run_backtest(
                self.cfg,
                self.market.data_range,
                cache,
                self.root / out,
                SEED,
                "replay",
                writer=self.writer,
                calendar_dir=self.calendar_dir,
                clock=lambda: STARTED,
                code_version=CODE_VERSION,
                recordings=self.recordings_root / "recordings",
            )

    def replay_log(self, out: str = "replay") -> bytes:
        return (self.root / out / DECISION_LOG_FILE_NAME).read_bytes()
