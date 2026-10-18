"""Mixed roll sessions are skipped like sessions with missing data.

The roll calendar's ``mixed_sessions`` lists sessions whose overnight bars mix
the old and the new contract. A backtest skips such a session for any
configured instrument, records it in the Run_Manifest as
``mixed_roll_session`` with the mixed instruments as names, and otherwise
treats it exactly like a missing-data skip: no decision-log line, no
SessionOutcome, and the Combine_Attempt runs on across it.

The cache is synthetic (``tests/strategies/backtest_inputs``) and holds full
data for 2026-03-03 to 03-05. The test roll calendar lists 03-04 as mixed for
MES and ES, and 03-05 as mixed for ES only (not configured, so it runs).
"""

from __future__ import annotations

import io
import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from fse.backtest.decision_log import DECISION_LOG_FILE_NAME
from fse.backtest.manifest import MANIFEST_FILE_NAME, DataRange
from fse.backtest.runner import SESSION_OUTCOMES_FILE_NAME, BacktestResult, check_sessions
from fse.calendars import ROLL_CALENDAR_FILE, load_roll_calendar
from fse.commands.backtest import summary_lines
from fse.config.schema import StrategyConfig
from fse.data.cache import DataCache
from tests.strategies.backtest_inputs import (
    CALENDAR,
    INSTRUMENTS,
    SYMBOLS,
    VIEW,
    SessionInputs,
    bar_opens,
    build_session,
    fixed_config,
    run,
    write_cache,
    write_calendars,
)

TUE, WED, THU = date(2026, 3, 3), date(2026, 3, 4), date(2026, 3, 5)
RANGE = DataRange(TUE, THU)

ROLL = """\
covers: {first: 2026-02-02, last: 2026-03-13}
instruments:
  ES:
    - {first: 2026-02-02, last: 2026-03-13, contract: ESH6}
  NQ:
    - {first: 2026-02-02, last: 2026-03-13, contract: NQH6}
  MES:
    - {first: 2026-02-02, last: 2026-03-13, contract: MESH6}
  MNQ:
    - {first: 2026-02-02, last: 2026-03-13, contract: MNQH6}
"""
MIXED = """\
mixed_sessions:
  - {session: 2026-03-04, instruments: [MES, ES], switch: "20:00"}
  - {session: 2026-03-05, instruments: [ES], switch: "20:00"}
"""


def _sessions() -> list[SessionInputs]:
    return [build_session(d, 8000 + d.day, bar_opens(d, 12, None)) for d in (TUE, WED, THU)]


def _config() -> StrategyConfig:
    """``fixed_config`` with limits no run reaches, so one Combine_Attempt spans the range."""
    data = fixed_config(900).model_dump(mode="python", by_alias=True)
    limits = {"profit_target": "100000.00", "maximum_loss_limit": "40000.00"}
    limits["daily_loss_limit"] = "30000.00"
    for rule, value in limits.items():
        data["account"][rule]["value"] = value
    return StrategyConfig.model_validate(data)


def _calendars(directory: Path, roll: str) -> Path:
    write_calendars(directory)
    (directory / ROLL_CALENDAR_FILE).write_text(roll, encoding="utf-8")
    return directory


def _log_lines(result: BacktestResult) -> list[dict[str, Any]]:
    text = (result.run_dir / DECISION_LOG_FILE_NAME).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def _json(result: BacktestResult, name: str) -> Any:
    return json.loads((result.run_dir / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> tuple[BacktestResult, BacktestResult, str]:
    """The mixed run, the control run (03-04 has no MES bars) and the mixed offline printout."""
    tmp = tmp_path_factory.mktemp("mixed")
    cfg = _config()
    mixed_cache = write_cache(tmp / "cache-mixed", _sessions())
    mixed_cal = _calendars(tmp / "cal-mixed", ROLL + MIXED)
    stdout = io.StringIO()
    mixed = run(mixed_cache, mixed_cal, cfg, RANGE, tmp / "run-mixed", offline=True, stdout=stdout)
    control_sessions = [
        s
        if s.session != WED
        else replace(s, bars=tuple(b for b in s.bars if b.instrument == "MNQ"))
        for s in _sessions()
    ]
    control_cache = write_cache(tmp / "cache-control", control_sessions)
    control_cal = _calendars(tmp / "cal-control", ROLL)
    control = run(control_cache, control_cal, cfg, RANGE, tmp / "run-control")
    return mixed, control, stdout.getvalue()


def test_a_mixed_session_is_skipped_with_its_cause(
    runs: tuple[BacktestResult, BacktestResult, str],
) -> None:
    mixed, _, _ = runs
    manifest = _json(mixed, MANIFEST_FILE_NAME)
    assert manifest["sessions_evaluated"] == ["2026-03-03", "2026-03-05"]
    assert manifest["sessions_skipped"] == [
        {"date": "2026-03-04", "missing": ["mixed_roll_session"], "names": ["MES"]}
    ]
    assert {line["session"] for line in _log_lines(mixed)} == {"2026-03-03", "2026-03-05"}
    outcomes = _json(mixed, SESSION_OUTCOMES_FILE_NAME)
    assert [o["session"] for o in outcomes] == ["2026-03-03", "2026-03-05"]
    assert [o.session for o in mixed.outcomes] == [TUE, THU]


def test_the_combine_attempt_runs_across_a_mixed_session(
    runs: tuple[BacktestResult, BacktestResult, str],
) -> None:
    mixed, _, _ = runs
    assert len(mixed.attempts) == 1
    attempt = mixed.attempts[0]
    assert attempt.outcome == "incomplete"
    assert (attempt.first_session, attempt.last_session) == (TUE, THU)


def test_a_mixed_skip_matches_a_missing_data_skip(
    runs: tuple[BacktestResult, BacktestResult, str],
) -> None:
    mixed, control, _ = runs
    assert control.manifest.sessions_skipped[0].missing == ("bars",)
    assert mixed.attempts == control.attempts
    assert mixed.outcomes == control.outcomes
    assert mixed.trades == control.trades
    assert mixed.decision_times == control.decision_times
    assert mixed.holdout == control.holdout
    assert _log_lines(mixed) == _log_lines(control)
    mixed_manifest = _json(mixed, MANIFEST_FILE_NAME)
    control_manifest = _json(control, MANIFEST_FILE_NAME)
    assert mixed_manifest.pop("sessions_skipped") == [
        {"date": "2026-03-04", "missing": ["mixed_roll_session"], "names": ["MES"]}
    ]
    assert control_manifest.pop("sessions_skipped") == [
        {"date": "2026-03-04", "missing": ["bars"], "names": ["MES"]}
    ]
    assert mixed_manifest == control_manifest


def test_offline_and_summary_name_the_mixed_session(
    runs: tuple[BacktestResult, BacktestResult, str],
) -> None:
    mixed, _, printout = runs
    lines = printout.splitlines()
    assert "offline: 2026-03-04: mixed roll session for MES; skipped" in lines
    assert not [line for line in lines if line.startswith("offline: 2026-03-04 ")]
    assert "skipped 2026-03-04: mixed roll session for MES" in summary_lines(mixed)


def test_check_sessions_reads_no_catalog_for_a_mixed_session(tmp_path: Path) -> None:
    roll_path = _calendars(tmp_path / "cal", ROLL + MIXED) / ROLL_CALENDAR_FILE
    roll = load_roll_calendar(roll_path)
    with DataCache(tmp_path / "empty", calendar=CALENDAR) as cache:
        checks = check_sessions(
            cache,
            CALENDAR,
            (WED, THU),
            symbols=SYMBOLS,
            view_id=VIEW.view_id(),
            instruments=INSTRUMENTS,
            roll=roll,
        )
    wed, thu = checks
    assert (wed.missing, wed.names, wed.gaps, wed.windows) == (
        ("mixed_roll_session",),
        ("MES",),
        (),
        (),
    )
    # 03-05 is mixed for ES only, which is not configured: checked as usual.
    assert thu.mixed_roll == ()
    assert thu.missing == ("snapshots", "bars")
    assert thu.gaps
