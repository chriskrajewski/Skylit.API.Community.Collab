"""Property 70: Replay parity.

*For any* simulated live session (generated Snapshots, bars, broker events,
guard events and receipt times fed through fake feeds), replaying the
recording in the Backtester yields, at every recorded Decision_Time, the same
Candidate_Setups (Setup_Key, entry, stop, targets), Grades, Rejection_Reasons
and order decisions the Live_Runner logged.

Each example writes a synthetic Data_Cache and drives
:class:`~fse.live.runner.LiveSession` over every evaluated session
(:class:`tests.strategies.live_sessions.Driver`): drawn receipt delays, a
drawn Map_State refresh with an optional outage (so the stale-map guard
fires), Decision_Times on the cadence grid and at a drawn share of input
arrivals (an input at a Decision_Time's instant comes before or after it),
and a drawn halt-file window. Each session after the first starts from the
state the previous one saved. Then the Backtester replays the recordings
(``run_backtest(mode="replay")``).

Checked: the replay's decision log is byte-identical to the live decision
logs in order (every Decision_Time's setups with entry, stop and targets,
Gate results, Grades, Rejection_Reasons, intents, fills and blocks), and the
replay's trade list equals the trades the Paper_Broker closed live.

Pinned ``@example`` cases: ``funnel_case`` with a 120 s live maximum, so
trades, halts and stale blocks all occur; and the early-close session
``early_close_hold`` at a 1800 s cadence, where the Backtester's first grid
time after Flatten_Time is the Flat_Deadline itself (the task 19 follow-up).
Live Decision_Times stop before the Flat_Deadline, and the Paper_Broker drops
an exit for a position it no longer holds, so no reverse position can open.

**Validates: Requirements 23.5, 23.9**
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import event, example, given
from hypothesis import strategies as st

from fse.config.schema import StrategyConfig
from tests.strategies.backtest_inputs import (
    MarketInputs,
    early_close_hold,
    fixed_config,
    funnel_case,
    market_inputs,
    strategy_configs,
)
from tests.strategies.live_sessions import Driver, LivePlan, live_plans


def _with_max_age(cfg: StrategyConfig, max_age_s: int) -> StrategyConfig:
    data = cfg.model_dump(mode="python", by_alias=True)
    data["live"]["live_max_snapshot_age_s"] = max_age_s
    return StrategyConfig.model_validate(data)


@st.composite
def configs(draw: st.DrawFn) -> StrategyConfig:
    cfg = draw(strategy_configs())
    return _with_max_age(cfg, draw(st.sampled_from((30, 120, 900)), label="live max age"))


@st.composite
def sparse_plans(draw: st.DrawFn) -> LivePlan:
    plan = draw(live_plans())
    return LivePlan(
        seed=plan.seed,
        refresh_s=draw(st.sampled_from((60, 300)), label="refresh"),
        max_delay_s=plan.max_delay_s,
        outage=plan.outage,
        extra_dt_share=draw(st.sampled_from((0.0, 0.02, 0.05)), label="extra"),
        halts=plan.halts,
    )


_FUNNEL, _FUNNEL_CFG = funnel_case()


# Feature: skylit-futures-strategy-engine, Property 70: Replay parity
@given(
    market=market_inputs(), cfg=configs(), plans=st.lists(sparse_plans(), min_size=1, max_size=3)
)
@example(
    market=_FUNNEL,
    cfg=_with_max_age(_FUNNEL_CFG, 120),
    plans=[LivePlan(seed=2, refresh_s=60, max_delay_s=40, extra_dt_share=0.05,
                    outage=(3000, 900), halts=((6000, 9000),))],
)  # fmt: skip
@example(  # task 19 follow-up: at 1800 s the first grid time after Flatten_Time is the deadline
    market=early_close_hold(),
    cfg=_with_max_age(fixed_config(1800), 900),
    plans=[LivePlan(seed=5, refresh_s=60, max_delay_s=2, extra_dt_share=0.0)],
)
def test_replaying_a_live_recording_gives_the_live_decisions(
    market: MarketInputs, cfg: StrategyConfig, plans: list[LivePlan]
) -> None:
    with TemporaryDirectory(prefix="fse-p70-") as tmp:
        driver = Driver(Path(tmp), market, cfg)
        driver.run_market(plans)
        if not driver.sessions:
            event("no evaluated session")
            return
        replayed = driver.replay()
        live = driver.live_log()
        assert driver.replay_log() == live
        live_trades = [t for s in driver.sessions for t in s.loop.trades]
        assert list(replayed.trades) == live_trades
        assert replayed.decision_times == sum(s.decisions for s in driver.sessions)
        kinds = {b.kind for d in driver.decisions for b in d.blocks}
        event(f"blocks: {', '.join(sorted(kinds)) or 'none'}")
        event(f"live trades: {'some' if live_trades else 'none'}")
        event(f"sessions: {len(driver.sessions)}")
