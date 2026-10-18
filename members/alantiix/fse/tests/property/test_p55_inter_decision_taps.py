"""Property 55: Inter-decision Tap count.

*For any* session with stored bars shorter than 300 s, the reported count of
Taps that start and end strictly between two consecutive 300 s Decision_Times
equals the reference count over the stored bars, regardless of the run's
cadence.

Each example writes a synthetic Data_Cache (``tests/strategies/backtest_inputs``,
1-minute bars, skipped sessions possible) and runs the same Strategy_Config at
two Decision_Cadences: a fine one (60 or 300 s) and a coarse one (600 to
3600 s). For every evaluated session, the ``tap_counts`` of
``report_inputs.json`` (what ``fse report`` shows) must give a 60 s bar
interval, the reference Tap count and the reference inter-decision count
(``tests/reference/taps``), at both cadences. A skipped session has no entry,
so the report marks it not measurable.

**Validates: Requirements 18.11**
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from hypothesis import event, given
from hypothesis import strategies as st

from fse.backtest.runner import REPORT_INPUTS_FILE_NAME
from fse.config.schema import StrategyConfig
from tests.reference.taps import reference_inter_decision, reference_taps
from tests.strategies.backtest_inputs import (
    CALENDAR,
    MarketInputs,
    market_inputs,
    run,
    strategy_configs,
    with_cadence,
    write_cache,
    write_calendars,
)


# Feature: skylit-futures-strategy-engine, Property 55: Inter-decision Tap count
@given(
    market=market_inputs(gaps=True, max_sessions=2),
    cfg=strategy_configs(),
    fine=st.sampled_from((60, 300)),
    coarse=st.sampled_from((600, 900, 1800, 3600)),
)
def test_inter_decision_tap_count_matches_the_reference_at_any_cadence(
    market: MarketInputs, cfg: StrategyConfig, fine: int, coarse: int
) -> None:
    with tempfile.TemporaryDirectory(prefix="fse-p55-") as tmp:
        root = Path(tmp)
        calendars = write_calendars(root / "calendars")
        cache = write_cache(root / "cache", market)
        reported: list[list[dict[str, Any]]] = []
        for cadence in (fine, coarse):
            result = run(
                cache, calendars, with_cadence(cfg, cadence), market.data_range, root / str(cadence)
            )
            text = (result.run_dir / REPORT_INPUTS_FILE_NAME).read_text(encoding="utf-8")
            reported.append(json.loads(text)["tap_counts"])

    expected: list[dict[str, Any]] = []
    for s in market.evaluated:
        taps = reference_taps(s, cfg, CALENDAR)
        expected.append(
            {
                "session": s.session.isoformat(),
                "bar_interval_s": 60,
                "taps": len(taps),
                "inter_decision": reference_inter_decision(taps, s.session),
            }
        )
    for counts in reported:
        assert counts == expected
    inter = sum(e["inter_decision"] for e in expected)
    event(f"inter-decision Taps: {'some' if inter else 'none'}")
    event(f"skipped sessions: {len(market.sessions) - len(market.evaluated)}")
