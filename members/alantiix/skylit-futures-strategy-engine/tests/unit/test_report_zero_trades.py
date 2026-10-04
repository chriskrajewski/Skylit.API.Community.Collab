"""Unit test for a zero-trade report (task 22.11).

A backtest whose Strategy_Config enables no Setup_Detector produces no
Candidate_Setup and so no trade. ``fse report`` must still finish: trade
count and trades per day 0, every metric with a zero denominator shown as
"not applicable" (never 0 or infinity), an empty Gate_Funnel, and a per-trade
MAE and MFE file with only its header.

**Validates: Requirements 20.16**
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from fse import cli
from fse.backtest.runner import BacktestResult
from fse.commands.report import (
    EXCURSIONS_FILE_NAME,
    REPORT_DIR_NAME,
    REPORT_JSON_FILE_NAME,
    REPORT_MD_FILE_NAME,
)
from fse.config.schema import StrategyConfig
from fse.config.schema.patterns import DETECTOR_IDS
from fse.reports.markdown import NOT_APPLICABLE
from fse.reports.tables import EXCURSION_COLUMNS
from tests.strategies.backtest_inputs import (
    SESSIONS,
    MarketInputs,
    bar_opens,
    build_session,
    fixed_config,
    run,
    write_cache,
    write_calendars,
)

UNDEFINED = (
    "avg_win_r",
    "avg_loss_r",
    "expectancy_r",
    "expectancy_usd",
    "profit_factor",
    "max_drawdown_usd",
    "max_drawdown_r",
    "win_rate_a_pct",
    "win_rate_b_pct",
    "win_rate_c_pct",
    "primary_win_rate_pct",
    "break_even_win_rate_pct",
    "mae_r",
    "mfe_r",
)
"""The metrics with a zero denominator in a zero-trade run (Req 20.16)."""


def no_detector_config() -> StrategyConfig:
    data = fixed_config(1800).model_dump(mode="python", by_alias=True)
    for detector in DETECTOR_IDS:
        data["patterns"][detector]["enabled"] = False
    return StrategyConfig.model_validate(data)


def zero_trade_run(root: Path) -> BacktestResult:
    days = SESSIONS[:2]
    market = MarketInputs(
        tuple(build_session(d, 100 + i, bar_opens(d, 40, (90, 30))) for i, d in enumerate(days))
    )
    cache = write_cache(root / "cache", market)
    calendars = write_calendars(root / "calendars")
    return run(cache, calendars, no_detector_config(), market.data_range, root / "runs" / "zero")


def test_a_zero_trade_run_reports_not_applicable_metrics(tmp_path: Path) -> None:
    result = zero_trade_run(tmp_path)
    assert result.trades == ()
    assert result.setups == ()

    out, err = io.StringIO(), io.StringIO()
    code = cli.run(
        ["report", "--run", "zero", "--runs-dir", str(tmp_path / "runs")],
        project_dir=None,
        environ={},
        stdout=out,
        stderr=err,
    )

    assert (code, err.getvalue()) == (0, "")
    assert "2 sessions, 0 trades" in out.getvalue()
    folder = result.run_dir / REPORT_DIR_NAME
    report = json.loads((folder / REPORT_JSON_FILE_NAME).read_text(encoding="utf-8"))
    metrics = report["metrics"]
    assert metrics["trade_count"] == 0
    assert metrics["session_count"] == 2
    assert metrics["trades_per_day"] == "0.000000"
    for name in UNDEFINED:
        assert metrics[name] == {}, name  # NotApplicable, never 0 or infinity
    assert report["header"]["trade_count"] == 0
    assert report["gate_funnel"]["setup_keys"] == 0

    md = (folder / REPORT_MD_FILE_NAME).read_text(encoding="utf-8")
    assert "| Trade count | 0 |" in md
    assert "| Trades per day | 0.00 |" in md
    for label in ("Average win", "Expectancy (R)", "Profit factor", "Break-even win rate"):
        line = next(x for x in md.splitlines() if x.startswith(f"| {label}"))
        assert NOT_APPLICABLE in line, line
    assert "inf" not in md.lower().replace("information", "")
    assert md.index("## Run") < md.index("## Metrics")  # the header comes first

    excursions = (folder / EXCURSIONS_FILE_NAME).read_text(encoding="utf-8")
    assert excursions == ",".join(EXCURSION_COLUMNS) + "\n"
