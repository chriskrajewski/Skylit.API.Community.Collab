"""Property 72: Stale-map guard.

*For any* sequence of Snapshot_Age values over time, resting entries are
cancelled and new entries blocked from the first Decision_Time where the age
exceeds the live maximum until the first Decision_Time where it is back at or
below the maximum, and stop and exit orders of open positions are untouched
throughout.

Each example drives :class:`~fse.live.runner.LiveSession` over generated
sessions with drawn receipt delays, a drawn Map_State refresh interval and an
optional refresh outage, and a drawn live maximum, so the Snapshot_Age
crosses the maximum both ways. The pinned ``funnel_case`` example has an
entry resting when an outage makes the map stale, and a withheld entry. The
engine is wrapped: at every Decision_Time it also runs the same step without
the blocks (the engine is pure).

Checked at every Decision_Time:

- the ``stale_map`` block is in force exactly when the Snapshot_Age of the
  Map_State at that instant is above the maximum (or undefined, with no
  Snapshot);
- with the block: no entry is placed and no entry is left resting; the
  step's intents are those of the unblocked step up to where they differ,
  then only cancels of resting entry orders, while the unblocked step only
  adds placements; the open positions (stops and targets included) equal
  the unblocked step's.

**Validates: Requirements 23.7**
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from hypothesis import event, example, given
from hypothesis import strategies as st

from fse.config.schema import StrategyConfig
from fse.engine.planner import CancelOrder, PlaceBracket
from fse.engine.state import EngineState
from fse.engine.step import Engine, ExternalBlock, StepResult
from fse.engine.types import Unavailable
from fse.live.guards import STALE_MAP_BLOCK
from fse.pit.protocols import MarketView
from fse.timekit import NS_PER_SECOND, Instant
from tests.strategies.backtest_inputs import (
    MarketInputs,
    funnel_case,
    market_inputs,
    strategy_configs,
)
from tests.strategies.live_sessions import Driver, LivePlan, live_plans


@dataclass(frozen=True, slots=True)
class Checked:
    t: Instant
    age: int | Unavailable
    blocks: tuple[ExternalBlock, ...]
    before: EngineState
    blocked: StepResult
    free: StepResult


class DiffEngine:
    """An :class:`Engine` that also runs each step without its external blocks."""

    def __init__(self, engine: Engine, out: list[Checked]) -> None:
        self._engine = engine
        self._out = out

    def __getattr__(self, name: str) -> Any:
        return getattr(self._engine, name)

    def step(
        self,
        state: EngineState,
        view: MarketView,
        t: Instant,
        events: Any = (),
        external_blocks: Any = (),
    ) -> StepResult:
        evs = tuple(events)
        blocks = tuple(external_blocks)
        result = self._engine.step(state, view, t, evs, blocks)
        free = self._engine.step(state, view, t, evs, ()) if blocks else result
        age = view.map_state().snapshot_age_ns()
        self._out.append(Checked(t, age, blocks, state, result, free))
        return result


@st.composite
def configs(draw: st.DrawFn) -> StrategyConfig:
    data = draw(strategy_configs()).model_dump(mode="python", by_alias=True)
    data["live"]["live_max_snapshot_age_s"] = draw(st.sampled_from((30, 60, 120, 600)), label="max")
    return StrategyConfig.model_validate(data)


@st.composite
def plans(draw: st.DrawFn) -> LivePlan:
    plan = draw(live_plans(halts=False))
    return LivePlan(
        plan.seed,
        draw(st.sampled_from((60, 300)), label="refresh"),
        plan.max_delay_s,
        plan.outage,
        draw(st.sampled_from((0.0, 0.05)), label="extra"),
        (),
    )


def _max_age(cfg: StrategyConfig, seconds: int) -> StrategyConfig:
    data = cfg.model_dump(mode="python", by_alias=True)
    data["live"]["live_max_snapshot_age_s"] = seconds
    return StrategyConfig.model_validate(data)


_FUNNEL, _FUNNEL_CFG = funnel_case()


# Feature: skylit-futures-strategy-engine, Property 72: Stale-map guard
@given(market=market_inputs(), cfg=configs(), plan=plans())
@example(  # an entry placed at 09:32:01 rests when the refresh outage makes the map stale
    market=_FUNNEL,
    cfg=_max_age(_FUNNEL_CFG, 120),
    plan=LivePlan(seed=1, refresh_s=60, max_delay_s=5, outage=(125, 900), extra_dt_share=0.3),
)
def test_a_stale_map_cancels_resting_entries_and_blocks_new_ones_only_while_stale(
    market: MarketInputs, cfg: StrategyConfig, plan: LivePlan
) -> None:
    checked: list[Checked] = []
    max_ns = cfg.live.live_max_snapshot_age_s * NS_PER_SECOND
    with TemporaryDirectory(prefix="fse-p72-") as tmp:
        driver = Driver(Path(tmp), market, cfg)
        driver.run_market([plan], engine=DiffEngine(driver.engine, checked))
    stale_seen = cancelled_seen = withheld_seen = False
    for c in checked:
        stale = isinstance(c.age, Unavailable) or c.age > max_ns
        kinds = [b.kind for b in c.blocks]
        assert kinds == ([STALE_MAP_BLOCK] if stale else []), (c.t, c.age, kinds)
        if not stale:
            continue
        stale_seen = True
        blocked, free = c.blocked, c.free
        assert not any(isinstance(i, PlaceBracket) for i in blocked.intents)
        assert blocked.state.book.resting == ()
        k = 0
        while (
            k < min(len(blocked.intents), len(free.intents))
            and blocked.intents[k] == free.intents[k]
        ):
            k += 1
        resting_entries = {p.entry.client_id for p in free.state.book.resting} | {
            p.entry.client_id for p in c.before.book.resting
        }
        for intent in blocked.intents[k:]:
            assert isinstance(intent, CancelOrder), intent
            assert intent.client_id in resting_entries, intent
            cancelled_seen = True
        for intent in free.intents[k:]:
            assert isinstance(intent, PlaceBracket), intent
            withheld_seen = True
        opened = [p for p in blocked.state.book.plans if p.status == "open"]
        assert opened == [p for p in free.state.book.plans if p.status == "open"]
    event(f"stale Decision_Times: {'some' if stale_seen else 'none'}")
    event(f"resting entries cancelled by the guard: {'some' if cancelled_seen else 'none'}")
    event(f"entries withheld by the guard: {'some' if withheld_seen else 'none'}")
