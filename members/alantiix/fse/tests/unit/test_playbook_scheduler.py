"""``PlaybookScheduler``: LiveRunner's input rule and Decision_Time timing (design 9.1).

The scheduler drives a stub session (no files), so each rule is checked on
its own: dark-pool prints never set ``input_pending``; a snapshot batch only
when the session used one of its Snapshots; an empty bar batch never; inputs
before ``dt_start`` are cleared without a decision; and the premarket and
decision timing follow ``LiveRunner._premarket``/``_decisions``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, time
from typing import Any, Final, cast

from fse.config.schema import StrategyConfig
from fse.engine.types import Bar, DarkPoolPrint, Snapshot
from fse.live.runner import (
    DecisionStep,
    LiveSession,
    PlaybookScheduler,
    SessionWindow,
    map_wait_ns,
    session_window,
)
from fse.timekit import NS_PER_SECOND, Instant, SessionCalendar, ny_instant
from tests.strategies.backtest_inputs import CALENDAR, funnel_case, make_bar

S: Final = NS_PER_SECOND
_, BASE_CFG = funnel_case()
DAY: Final = date(2026, 3, 2)


def at(hh: int, mm: int, ss: int = 0) -> Instant:
    return ny_instant(DAY, time(hh, mm, ss))


def config(start: str = "09:28", end: str = "09:40", premarket: str = "09:25") -> StrategyConfig:
    data = BASE_CFG.model_dump(mode="python", by_alias=True)
    data["time"]["decision_cadence_s"] = 60
    data["live"]["run_window"] = {"start": start, "end": end}
    data["notify"]["premarket"] = premarket
    return StrategyConfig.model_validate(data)


@dataclass
class StubSession:
    """The parts of ``LiveSession`` the scheduler reads and calls."""

    window: SessionWindow
    calendar: SessionCalendar = CALENDAR
    session: date = DAY
    used: bool = True
    last_t: Instant | None = None
    stale: Instant | None = None
    calls: list[tuple[str, Any]] = field(default_factory=list)

    def on_snapshots(self, snaps: Sequence[Snapshot], received: Instant) -> bool:
        self.calls.append(("snapshots", received))
        return self.used

    def on_bars(self, bars: Sequence[Bar], received: Instant) -> None:
        self.calls.append(("bars", len(bars)))

    def on_dark_pool(self, ticker: str, prints: Sequence[DarkPoolPrint], received: Instant) -> None:
        self.calls.append(("dark_pool", ticker))

    def stale_at(self, now: Instant) -> Instant | None:
        return self.stale


def make(cfg: StrategyConfig | None = None) -> tuple[PlaybookScheduler, StubSession]:
    cfg = cfg or config()
    stub = StubSession(session_window(cfg, CALENDAR, DAY))
    sched = PlaybookScheduler(
        cast(LiveSession, stub), cfg.time.decision_cadence_s, map_wait_ns(cfg)
    )
    return sched, stub


def bar(open_ns: Instant) -> Bar:
    return make_bar("MES", open_ns, 23200, 23204, 23196, 23202)


# ---------------------------------------------------------------- the input rule


def test_dark_pool_prints_never_set_input_pending() -> None:
    sched, stub = make()
    sched.on_dark_pool("SPY", (), at(9, 31))
    assert stub.calls == [("dark_pool", "SPY")]
    assert not sched.input_pending
    assert not sched.map_seen


def test_a_snapshot_batch_sets_input_only_when_the_session_used_it() -> None:
    sched, stub = make()
    stub.used = False
    sched.on_snapshots((), at(9, 31))
    assert not sched.input_pending
    assert not sched.map_seen
    stub.used = True
    sched.on_snapshots((), at(9, 31, 5))
    assert sched.input_pending
    assert sched.map_seen


def test_an_empty_bar_batch_does_not_set_input_pending() -> None:
    sched, stub = make()
    sched.on_bars((), at(9, 31))
    assert stub.calls == [("bars", 0)]
    assert not sched.input_pending
    sched.on_bars((bar(at(9, 30)),), at(9, 31))
    assert sched.input_pending


def test_inputs_before_dt_start_are_cleared_without_a_decision() -> None:
    sched, stub = make()
    w = stub.window
    assert w.dt_start == at(9, 30)
    sched.on_bars((bar(at(9, 28)),), at(9, 29))
    assert sched.input_pending
    step = sched.decision_step(at(9, 29))
    assert step == DecisionStep(None, clear_input=True, done=False)
    assert not sched.input_pending
    # The first Decision_Time is the first grid tick at dt_start, not an earlier input.
    assert sched.decision_wake(at(9, 29)) == at(9, 30)
    assert sched.decision_step(at(9, 30)) == DecisionStep(at(9, 30), True, False)


# ---------------------------------------------------------------- decisions


def test_decision_steps_follow_ticks_inputs_stale_instants_and_dt_end() -> None:
    sched, stub = make()
    stub.last_t = at(9, 30)
    assert sched.decision_wake(at(9, 30)) == at(9, 31)
    # Nothing new at 09:30:10: no decision.
    assert sched.decision_step(at(9, 30, 10)) == DecisionStep(None, False, False)
    # An input decides at once.
    sched.on_bars((bar(at(9, 30)),), at(9, 31, 2))
    assert sched.decision_step(at(9, 31, 2)) == DecisionStep(at(9, 31, 2), True, False)
    assert not sched.input_pending
    # An input at the instant of the previous Decision_Time decides 1 ns later.
    stub.last_t = at(9, 31, 2)
    sched.decision_wake(at(9, 31, 2))
    sched.on_bars((bar(at(9, 31)),), at(9, 31, 2))
    assert sched.decision_step(at(9, 31, 2)) == DecisionStep(at(9, 31, 2) + 1, True, False)
    # The stale instant wakes the loop before the next tick.
    stub.last_t = at(9, 31, 2) + 1
    stub.stale = at(9, 31, 30)
    assert sched.decision_wake(at(9, 31, 3)) == at(9, 31, 30)
    assert sched.decision_step(at(9, 31, 30)) == DecisionStep(at(9, 31, 30), True, False)
    # dt_end: done, also when the next instant would be at or after it.
    w = stub.window
    assert sched.decision_step(w.dt_end) == DecisionStep(None, False, True)
    stub.last_t = w.dt_end - 1
    stub.stale = None
    sched.decision_wake(w.dt_end - 1)
    sched.on_bars((bar(at(9, 38)),), w.dt_end - 1)
    assert sched.decision_step(w.dt_end - 1) == DecisionStep(None, True, True)


# ---------------------------------------------------------------- premarket


def test_premarket_waits_for_its_time_and_a_first_map() -> None:
    cfg = config(premarket="09:29")
    sched, stub = make(cfg)
    assert sched.premarket_wake(at(9, 28)) == at(9, 29)
    assert sched.premarket_due(at(9, 28)) is None
    # At its time without a map: wait refresh + timeout for one.
    wait_until = at(9, 29) + map_wait_ns(cfg)
    assert sched.premarket_wake(at(9, 29)) == wait_until
    assert sched.premarket_due(at(9, 29)) is None
    stub.used = True
    sched.on_snapshots((), at(9, 29, 3))
    assert sched.premarket_wake(at(9, 29, 3)) == at(9, 29, 3)
    assert sched.premarket_due(at(9, 29, 3)) == at(9, 29, 3)
    assert sched.premarket_sent
    assert sched.premarket_wake(at(9, 29, 4)) is None
    assert sched.premarket_due(at(9, 29, 4)) is None


def test_premarket_goes_out_after_the_map_wait_without_a_map() -> None:
    cfg = config(premarket="09:29")
    sched, _ = make(cfg)
    wait_until = sched.premarket_wake(at(9, 29, 1))
    assert wait_until == at(9, 29, 1) + map_wait_ns(cfg)
    assert sched.premarket_due(wait_until - 1) is None
    assert sched.premarket_due(wait_until) == wait_until


def test_premarket_with_a_map_seen_early_goes_out_at_its_time() -> None:
    cfg = config(premarket="09:29")
    sched, _ = make(cfg)
    sched.on_snapshots((), at(9, 28, 30))
    assert sched.premarket_due(at(9, 28, 30)) is None
    assert sched.premarket_wake(at(9, 28, 30)) == at(9, 29)
    assert sched.premarket_due(at(9, 29)) == at(9, 29)
