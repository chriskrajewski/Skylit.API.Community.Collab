"""Property 57: Shadow trades do not leak.

*For any* run, removing every Shadow_Trade (or simulating additional ones)
leaves the account balances, Combine_Attempt outcomes, Kill_Switch state,
sizing and every accepted-trade metric unchanged.

Each example runs one synthetic Data_Cache (``tests/strategies/backtest_inputs``)
and Strategy_Config twice: with the ShadowBook's normal mode (a Shadow_Trade
per rejected Setup_Key) and with either no Shadow_Trade at all (``off``) or a
shadow for every emission, accepted ones included (``every_setup``). These
files must be byte-identical:

- ``decision_log.jsonl``: every Gate result, Grade, sizing step, order,
  fill, Kill_Switch Lockout and loss-stop event;
- ``trades.csv`` and ``trades.json``: the accepted trades;
- ``combine_attempts.json`` and ``session_outcomes.json``: account balances
  and Combine_Attempt outcomes.

The accepted-trade metrics (``fse.analytics.metrics.summarize``) are equal,
and the normal mode's Shadow_Trades are all flagged ``shadow`` and never in
the accepted trade list. Pinned examples: the
:func:`~tests.strategies.backtest_inputs.funnel_case` run, which has nine
Shadow_Trades, in both other modes.

**Validates: Requirements 19.9, 14.5**
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from hypothesis import event, example, given
from hypothesis import strategies as st

from fse.analytics.metrics import MetricsCfg, summarize
from fse.backtest.decision_log import DECISION_LOG_FILE_NAME
from fse.backtest.runner import (
    ATTEMPTS_FILE_NAME,
    SESSION_OUTCOMES_FILE_NAME,
    TRADES_CSV_FILE_NAME,
    TRADES_JSON_FILE_NAME,
)
from fse.backtest.shadow import ShadowMode
from fse.config.schema import StrategyConfig
from tests.strategies.backtest_inputs import (
    MarketInputs,
    funnel_case,
    market_inputs,
    run,
    strategy_configs,
    write_cache,
    write_calendars,
)

ACCOUNT_FILES = (
    DECISION_LOG_FILE_NAME,
    TRADES_CSV_FILE_NAME,
    TRADES_JSON_FILE_NAME,
    ATTEMPTS_FILE_NAME,
    SESSION_OUTCOMES_FILE_NAME,
)


PINNED = funnel_case()


# Feature: skylit-futures-strategy-engine, Property 57: Shadow trades do not leak
@example(market=PINNED[0], cfg=PINNED[1], other="off")
@example(market=PINNED[0], cfg=PINNED[1], other="every_setup")
@given(
    market=market_inputs(gaps=True),
    cfg=strategy_configs(),
    other=st.sampled_from(("off", "every_setup")),
)
def test_shadow_trades_change_nothing_on_the_account(
    market: MarketInputs, cfg: StrategyConfig, other: ShadowMode
) -> None:
    with tempfile.TemporaryDirectory(prefix="fse-p57-") as tmp:
        root = Path(tmp)
        calendars = write_calendars(root / "calendars")
        cache = write_cache(root / "cache", market)
        base = run(cache, calendars, cfg, market.data_range, root / "base")
        alt = run(cache, calendars, cfg, market.data_range, root / other, shadow_mode=other)
        for name in ACCOUNT_FILES:
            assert (base.run_dir / name).read_bytes() == (alt.run_dir / name).read_bytes(), name

    assert alt.trades == base.trades
    assert alt.attempts == base.attempts
    assert alt.outcomes == base.outcomes
    assert alt.decision_times == base.decision_times
    sessions = base.manifest.sessions_evaluated
    assert summarize(alt.trades, sessions, MetricsCfg()) == summarize(
        base.trades, sessions, MetricsCfg()
    )
    assert all(t.shadow for t in base.shadow_trades)
    assert not any(t.shadow for t in base.trades)
    if other == "off":
        assert alt.shadow_trades == ()
    event(f"other mode: {other}")
    event(f"shadow trades: {'some' if base.shadow_trades else 'none'}")
    event(f"accepted trades: {'some' if base.trades else 'none'}")
