"""Monte_Carlo_Simulator edge cases (task 24.4; Req 21.7, 21.10, 21.11, 21.12).

- No passing path: days to pass are not available (``None``), the counts
  still sum to the path count.
- Fewer sessions than ``min_sessions``: the estimate is flagged
  "insufficient sample" and still reports every metric.
- Out-of-range inputs: each failed check is named and no estimate (and, for
  a run, no file) is produced.
- A run: the generated seed is recorded in the Run_Manifest and reproduces
  the estimate.
"""

from __future__ import annotations

import io
import json
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, Final

import pytest

from fse.analytics.montecarlo import (
    MONTECARLO_KIND,
    PASS_ESTIMATE_FILE_NAME,
    MonteCarloError,
    MonteCarloRunError,
    SessionOutcome,
    estimate,
    run_pass_estimate,
)
from fse.backtest.manifest import MANIFEST_FILE_NAME
from fse.backtest.runner import BACKTEST_KIND, SESSION_OUTCOMES_FILE_NAME
from fse.config.hashing import config_hash
from fse.config.schema import StrategyConfig
from fse.config.schema.account import AccountConfig
from fse.logio import LogWriter, Redactor
from tests.fakes.configs import minimal_config_data

DEFAULTS: Final = AccountConfig()
DAY0: Final = date(2026, 3, 2)


def outcome(i: int, net: str, low: str, truncated: bool = False) -> SessionOutcome:
    return SessionOutcome(DAY0 + timedelta(days=i), Decimal(net), Decimal(low), truncated)


def run(outcomes: tuple[SessionOutcome, ...], *, acct: AccountConfig = DEFAULTS, **kw: int) -> Any:
    settings = {"paths": 1_000, "max_days": 60, "seed": 11, "min_sessions": 40} | kw
    return estimate(outcomes, acct, **settings)


# ---------------------------------------------------------------- no passing path


def test_no_passing_path_reports_days_to_pass_as_not_available() -> None:
    losing = (outcome(0, "-150.00", "-300.00"), outcome(1, "0.00", "0.00"))
    est = run(losing)
    assert est.passed == 0
    assert est.median_days_to_pass is None
    assert est.p90_days_to_pass is None
    assert est.pass_probability == 0
    assert est.failed + est.unresolved == est.paths == 1_000
    assert est.failed > 0  # $150 a day breaches the $2,000 MLL distance within 60 days


def test_a_disabled_profit_target_never_passes() -> None:
    acct = AccountConfig.model_validate({"profit_target": {"enabled": False}})
    est = run((outcome(0, "3000.00", "0.00"),), acct=acct)
    assert (est.passed, est.failed, est.unresolved) == (0, 0, 1_000)
    assert est.unresolved_share == 1
    assert est.median_days_to_pass is None


# ---------------------------------------------------------------- insufficient sample


def test_fewer_sessions_than_the_minimum_is_labelled_and_still_reported() -> None:
    # One session of +$3,000: the 55% consistency target raises the profit target to
    # $5,454.55 after day 1, so every path passes on day 2 (Req 15.13, 21.4).
    est = run((outcome(0, "3000.00", "0.00", truncated=True),), min_sessions=40)
    assert est.insufficient_sample
    assert (est.sessions, est.min_sessions) == (1, 40)
    assert (est.passed, est.failed, est.unresolved) == (1_000, 0, 0)
    assert est.pass_probability == 1
    assert est.median_days_to_pass == Fraction(2)
    assert est.p90_days_to_pass == 2
    assert est.truncated_by_run_account == 1


def test_enough_sessions_is_not_insufficient() -> None:
    est = run((outcome(0, "0.00", "0.00"),), min_sessions=1)
    assert not est.insufficient_sample


# ---------------------------------------------------------------- out-of-range inputs


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("paths", 999),
        ("paths", 1_000_001),
        ("paths", True),
        ("max_days", 0),
        ("max_days", 251),
        ("min_sessions", 0),
        ("min_sessions", 1_001),
        ("seed", -1),
        ("seed", 2**63),
    ],
)
def test_an_out_of_range_setting_is_named(setting: str, value: int) -> None:
    settings = {"paths": 1_000, "max_days": 60, "seed": 11, "min_sessions": 40}
    settings[setting] = value
    with pytest.raises(MonteCarloError, match=rf"^{setting} must be a whole number"):
        estimate((outcome(0, "0.00", "0.00"),), DEFAULTS, **settings)


def test_every_failed_check_is_named_together() -> None:
    with pytest.raises(MonteCarloError) as info:
        run((), paths=10, max_days=300, min_sessions=0)
    message = str(info.value)
    for part in ("paths", "max_days", "min_sessions", "zero sessions"):
        assert part in message


def test_zero_sessions_is_rejected() -> None:
    with pytest.raises(MonteCarloError, match="zero sessions"):
        run(())


# ---------------------------------------------------------------- a run


def config() -> StrategyConfig:
    return StrategyConfig.model_validate(minimal_config_data())


def writer() -> LogWriter:
    return LogWriter(Redactor(), stdout=io.StringIO(), stderr=io.StringIO())


def backtest_dir(
    root: Path,
    cfg: StrategyConfig,
    outcomes: list[dict[str, Any]] | None = None,
    status: str = "completed",
) -> Path:
    run_dir = root / "bt"
    run_dir.mkdir()
    manifest = {
        "run_id": "bt-0123456789abcdef",
        "kind": BACKTEST_KIND,
        "config_hash": config_hash(cfg),
        "data_range": {"start": "2026-03-02", "end": "2026-03-03"},
        "status": status,
    }
    (run_dir / MANIFEST_FILE_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    if outcomes is None:
        outcomes = [
            {
                "session": "2026-03-02",
                "net": "700.00",
                "intraday_low": "-200.00",
                "truncated_by_run_account": False,
            },
            {
                "session": "2026-03-03",
                "net": "-400.00",
                "intraday_low": "-900.00",
                "truncated_by_run_account": False,
            },
        ]
    (run_dir / SESSION_OUTCOMES_FILE_NAME).write_text(json.dumps(outcomes), encoding="utf-8")
    return run_dir


def test_a_generated_seed_is_recorded_and_reproduces_the_estimate(tmp_path: Path) -> None:
    cfg = config()
    source = backtest_dir(tmp_path, cfg)
    first = run_pass_estimate(source, cfg, out_dir=tmp_path / "mc1", writer=writer())
    manifest = json.loads((first.run_dir / MANIFEST_FILE_NAME).read_text(encoding="utf-8"))
    assert manifest["kind"] == MONTECARLO_KIND
    assert manifest["status"] == "completed"
    assert manifest["seed"] == first.estimate.seed
    assert manifest["sessions_evaluated"] == ["2026-03-02", "2026-03-03"]
    assert PASS_ESTIMATE_FILE_NAME in manifest["outputs"]
    written = json.loads((first.run_dir / PASS_ESTIMATE_FILE_NAME).read_text(encoding="utf-8"))
    assert written["source_run_id"] == "bt-0123456789abcdef"
    assert written["insufficient_sample"] is True

    again = run_pass_estimate(
        source, cfg, out_dir=tmp_path / "mc2", writer=writer(), seed=manifest["seed"]
    )
    assert again.estimate == first.estimate
    assert again.run_id == first.run_id


@pytest.mark.parametrize("case", ["no_manifest", "aborted", "zero_sessions", "other_config"])
def test_a_bad_run_is_rejected_before_anything_is_written(tmp_path: Path, case: str) -> None:
    cfg = config()
    source = tmp_path / "missing"
    expected: type[MonteCarloError] = MonteCarloRunError
    if case == "aborted":
        source = backtest_dir(tmp_path, cfg, status="aborted")
    elif case == "zero_sessions":
        source = backtest_dir(tmp_path, cfg, outcomes=[])
        expected = MonteCarloError
    elif case == "other_config":
        other = minimal_config_data() | {"config_id": "another"}
        source = backtest_dir(tmp_path, StrategyConfig.model_validate(other))
        expected = MonteCarloError
    out = tmp_path / "mc"
    with pytest.raises(expected) as info:
        run_pass_estimate(source, cfg, out_dir=out, writer=writer(), seed=1)
    assert info.type is expected
    assert info.value.exit_code == (4 if expected is MonteCarloRunError else 2)
    assert not out.exists()
