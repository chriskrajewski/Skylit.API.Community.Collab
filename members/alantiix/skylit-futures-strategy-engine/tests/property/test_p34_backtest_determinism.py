"""Property 34: Backtest determinism.

*For any* synthetic Data_Cache, calendar files, Strategy_Config, date range
and seed, two runs produce field-equal Candidate_Setups, Detection_Skips, Gate
results and orders in the same order, and byte-identical trade lists,
Gate_Funnels and decision logs, including when sessions are precomputed in
parallel worker processes.

Each example writes one synthetic Data_Cache and the test calendar files
(``tests/strategies/backtest_inputs``, with skipped sessions possible) and runs
:func:`run_backtest` twice with the same Strategy_Config, range and seed:

1. on one thread (``workers=1``), which also builds the trailing-median file;
2. on the unchanged cache with 1 to 3 loader workers, so later sessions are
   read on worker threads while one runs, and the median file is loaded.

The two runs' output files are byte-identical: the decision log (every
Candidate_Setup, Detection_Skip, Gate result, Grade and order in order), the
trade list as CSV and JSON, the Combine_Attempts, the session outcomes, the
Setup_Key records with their Shadow_Trades, the Shadow_Trade list, the
Gate_Funnel, the report inputs and the Run_Manifest (the manifest clock is
constant). The returned trades, Combine_Attempts, session outcomes, Setup_Key
records, Gate_Funnel, gaps and skipped sessions are equal field by field.

Pinned example: :func:`~tests.strategies.backtest_inputs.funnel_case`, a run
with every final status and nine Shadow_Trades, with two loader workers.

**Validates: Requirements 10.15, 18.5**
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from hypothesis import event, example, given
from hypothesis import strategies as st

from fse.backtest.decision_log import DECISION_LOG_FILE_NAME
from fse.backtest.runner import BACKTEST_OUTPUT_FILES
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

PINNED = funnel_case()


# Feature: skylit-futures-strategy-engine, Property 34: Backtest determinism
@example(market=PINNED[0], cfg=PINNED[1], workers=2)
@given(
    market=market_inputs(gaps=True),
    cfg=strategy_configs(),
    workers=st.integers(1, 3),
)
def test_equal_inputs_give_byte_identical_outputs(
    market: MarketInputs, cfg: StrategyConfig, workers: int
) -> None:
    with tempfile.TemporaryDirectory(prefix="fse-p34-") as tmp:
        root = Path(tmp)
        calendars = write_calendars(root / "calendars")
        cache = write_cache(root / "cache", market)
        first = run(cache, calendars, cfg, market.data_range, root / "first")
        second = run(cache, calendars, cfg, market.data_range, root / "second", workers=workers)

        for name in BACKTEST_OUTPUT_FILES:
            a = (first.run_dir / name).read_bytes()
            b = (second.run_dir / name).read_bytes()
            assert a == b, name

        assert second.manifest == first.manifest
        assert (second.seed, second.decision_times) == (first.seed, first.decision_times)
        assert second.trades == first.trades
        assert second.attempts == first.attempts
        assert second.outcomes == first.outcomes
        assert (second.setups, second.funnel) == (first.setups, first.funnel)
        assert (second.gaps, second.skipped) == (first.gaps, first.skipped)

        log = (first.run_dir / DECISION_LOG_FILE_NAME).read_text(encoding="utf-8")
        setups = sum(len(json.loads(line)["setups"]) for line in log.splitlines())
        event(f"workers: {workers}")
        event(f"setups: {'some' if setups else 'none'}")
        event(f"trades: {'some' if first.trades else 'none'}")
