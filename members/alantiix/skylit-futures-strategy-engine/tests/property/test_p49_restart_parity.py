"""Property 49: Restart parity.

*For any* input sequence and any restart point, serializing EngineState
(including Risk_Manager state) and deserializing it yields an equal state, and
continuing from the restored state produces the same Lockouts, orders and
decision-log entries as an uninterrupted run and as the Backtester on the same
inputs.

Each example writes a synthetic Data_Cache (``tests/strategies/backtest_inputs``)
and runs :func:`run_backtest` twice with one Strategy_Config:

1. **Uninterrupted**: the Backtester as shipped.
2. **Restarted**: the same run with :class:`RestartingEngine` in place of the
   engine. After ``Engine.step`` at each drawn restart Decision_Time (one to
   three of them) it writes the returned state with
   :meth:`EngineState.to_canonical_json`, reads it back with
   :meth:`EngineState.from_json`, checks the two states are equal and the text
   round-trips exactly, then builds a new :class:`~fse.engine.step.Engine`
   from the same parameters, calendar and medians, as a restarted process
   would, and continues from the restored state. The Fill_Simulator and
   Account_Simulator stand in for the broker, so they carry over.

The restart runs through the Backtester's decision loop; the Live_Runner's
own restart from its state file is built in task 33.

Checked: every drawn restart happened, and every output file of the two runs
is byte-identical: the decision log (Lockouts, orders, setups and fills of
every Decision_Time), the trade list, the Combine_Attempts, the session
outcomes and the Run_Manifest.

**Validates: Requirements 16.7**
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest
from hypothesis import event, given
from hypothesis import strategies as st

from fse.backtest import runner
from fse.backtest.decision_log import DECISION_LOG_FILE_NAME
from fse.backtest.runner import BACKTEST_OUTPUT_FILES
from fse.config.schema import StrategyConfig
from fse.engine.state import EngineState
from fse.engine.step import Engine, ExternalBlock, StepEvent, StepResult
from fse.pit.protocols import MarketView
from fse.timekit import Instant
from tests.strategies.backtest_inputs import (
    CALENDAR,
    MarketInputs,
    market_inputs,
    run,
    strategy_configs,
    write_cache,
    write_calendars,
)


class RestartingEngine:
    """An :class:`Engine` that restarts from its serialized state after chosen Decision_Times."""

    def __init__(self, args: tuple[Any, ...], at: frozenset[Instant], done: list[Instant]) -> None:
        self._args = args
        self._engine = Engine(*args)
        self._at = at
        self._done = done

    def __getattr__(self, name: str) -> Any:
        return getattr(self._engine, name)

    def step(
        self,
        state: EngineState,
        view: MarketView,
        t: Instant,
        events: Iterable[StepEvent] = (),
        external_blocks: Iterable[ExternalBlock] = (),
    ) -> StepResult:
        result = self._engine.step(state, view, t, events, external_blocks)
        if t not in self._at:
            return result
        text = result.state.to_canonical_json()
        restored = EngineState.from_json(text)
        assert restored == result.state
        assert restored.to_canonical_json() == text
        if result.state.risk.lockouts:
            event("restart with a Lockout in the state")
        if result.state.book.plans:
            event("restart with orders in the book")
        self._engine = Engine(*self._args)
        self._done.append(t)
        return replace(result, state=restored)


def restarting(at: frozenset[Instant], done: list[Instant]) -> Callable[..., RestartingEngine]:
    """A stand-in for the ``Engine`` constructor the Backtester calls."""

    def build(*args: Any) -> RestartingEngine:
        return RestartingEngine(args, at, done)

    return build


@dataclass(frozen=True, slots=True)
class Case:
    market: MarketInputs
    cfg: StrategyConfig
    restarts: frozenset[Instant]


@st.composite
def cases(draw: st.DrawFn) -> Case:
    market = draw(market_inputs())
    cfg = draw(strategy_configs())
    cadence = cfg.time.decision_cadence_s
    grid = [t for s in market.evaluated for t in CALENDAR.decision_times(s.session, cadence)]
    points = draw(st.lists(st.sampled_from(grid), min_size=1, max_size=3), label="restarts")
    return Case(market, cfg, frozenset(points))


# Feature: skylit-futures-strategy-engine, Property 49: Restart parity
@given(case=cases())
def test_a_restart_from_serialized_state_changes_nothing(case: Case) -> None:
    with TemporaryDirectory(prefix="fse-p49-") as tmp:
        root = Path(tmp)
        calendars = write_calendars(root / "calendars")
        cache = write_cache(root / "cache", case.market)
        span = case.market.data_range
        reference = run(cache, calendars, case.cfg, span, root / "uninterrupted")
        done: list[Instant] = []
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(runner, "Engine", restarting(case.restarts, done))
            restarted = run(cache, calendars, case.cfg, span, root / "restarted")
        assert sorted(done) == sorted(case.restarts)
        for name in BACKTEST_OUTPUT_FILES:
            a = (reference.run_dir / name).read_bytes()
            assert (restarted.run_dir / name).read_bytes() == a, name
        log = (reference.run_dir / DECISION_LOG_FILE_NAME).read_text(encoding="utf-8")
        lockouts = any(json.loads(line)["lockouts"] for line in log.splitlines())
        event(f"lockouts logged: {'some' if lockouts else 'none'}")
        event(f"restarts: {len(case.restarts)}")
