"""Mixed roll sessions are left out of every experiment's session selection.

The light Data_Cache (``tests/strategies/experiment_inputs``) holds full data
for every test-calendar session. The test roll calendar lists the newest
session, 2026-03-13, as mixed for MES and 2026-03-03 as mixed for MNQ, so both
count as sessions without data: the Holdout_Period, the sessions with data,
the holdout evaluation, the cadence eligibility and an experiment's
Holdout_Period all leave them out.
"""

from __future__ import annotations

import io
import json
import math
from datetime import date
from pathlib import Path

import pytest

from fse.backtest.manifest import MANIFEST_FILE_NAME, DataRange
from fse.backtest.runner import cache_holdout, cache_sessions_with_data
from fse.calendars import ROLL_CALENDAR_FILE, RollCalendar, load_roll_calendar
from fse.config.printer import dump
from fse.data.cache import DataCache
from fse.experiments.cadence import eligible, run_cadence_comparison, stored_intervals
from fse.experiments.holdout import run_holdout_evaluation
from fse.experiments.runner import ExperimentConfig, run_experiment
from fse.logio import LogWriter, Redactor
from tests.strategies.backtest_inputs import CALENDAR, STARTED, write_calendars
from tests.strategies.experiment_inputs import (
    ALL_SESSIONS,
    TASK_FILE,
    LightSession,
    light_config,
    read_task,
    reliable_evaluator,
    write_light_cache,
)

NEWEST, OLDER = date(2026, 3, 13), date(2026, 3, 3)
MIXED_DATES = frozenset({NEWEST, OLDER})
FRACTION = 0.2

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
  - {session: 2026-03-03, instruments: [MNQ], switch: "20:00"}
  - {session: 2026-03-13, instruments: [MES], switch: "19:00"}
"""


def _calendars(directory: Path, roll: str) -> Path:
    write_calendars(directory)
    (directory / ROLL_CALENDAR_FILE).write_text(roll, encoding="utf-8")
    return directory


def _roll(calendar_dir: Path) -> RollCalendar:
    return load_roll_calendar(calendar_dir / ROLL_CALENDAR_FILE)


def _writer() -> LogWriter:
    return LogWriter(Redactor(), stdout=io.StringIO(), stderr=io.StringIO())


@pytest.fixture(scope="module")
def dirs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path]:
    """The light cache, the mixed calendar dir and a calendar dir without mixed sessions."""
    root = tmp_path_factory.mktemp("experiments-mixed")
    cache = write_light_cache(root / "cache", [LightSession(d) for d in ALL_SESSIONS])
    return cache, _calendars(root / "mixed", ROLL + MIXED), _calendars(root / "plain", ROLL)


def _expected_holdout() -> tuple[date, ...]:
    with_data = [d for d in ALL_SESSIONS if d not in MIXED_DATES]
    return tuple(with_data[-math.ceil(FRACTION * len(with_data)) :])


def test_mixed_sessions_do_not_count_toward_the_holdout(dirs: tuple[Path, Path, Path]) -> None:
    cache_dir, mixed_dir, plain_dir = dirs
    cfg = light_config(holdout_fraction=FRACTION)
    with DataCache(cache_dir) as cache:
        mixed = cache_holdout(cfg, cache, CALENDAR, roll=_roll(mixed_dir))
        plain = cache_holdout(cfg, cache, CALENDAR, roll=_roll(plain_dir))
    assert mixed is not None
    assert plain is not None
    assert mixed.n_sessions_with_data == len(ALL_SESSIONS) - 2
    assert mixed.sessions == _expected_holdout()
    assert NEWEST not in mixed.sessions
    assert mixed.last == date(2026, 3, 12)
    assert plain.n_sessions_with_data == len(ALL_SESSIONS)
    assert plain.last == NEWEST


def test_cache_sessions_with_data_leaves_out_mixed_sessions(
    dirs: tuple[Path, Path, Path],
) -> None:
    cache_dir, mixed_dir, _ = dirs
    cfg = light_config(holdout_fraction=FRACTION)
    with DataCache(cache_dir) as cache:
        sessions = cache_sessions_with_data(cfg, cache, CALENDAR, roll=_roll(mixed_dir))
    assert sessions == tuple(d for d in ALL_SESSIONS if d not in MIXED_DATES)


def test_the_holdout_evaluation_runs_no_mixed_session(
    dirs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    cache_dir, mixed_dir, _ = dirs
    config_path = tmp_path / "config.yaml"
    cfg = light_config(holdout_fraction=FRACTION, montecarlo={"paths": 1_000})
    config_path.write_text(dump(cfg), encoding="utf-8")
    evaluation = run_holdout_evaluation(
        config_path, log_path=tmp_path / "holdout_log.jsonl", cache_dir=cache_dir,
        calendar_dir=mixed_dir, out_dir=tmp_path / "run", writer=_writer(), seed=7,
        evaluator=reliable_evaluator, clock=lambda: STARTED, code_version="test-version",
    )  # fmt: skip
    assert evaluation.holdout.sessions == _expected_holdout()
    task_files = list((tmp_path / "run").rglob(TASK_FILE))
    assert len(task_files) == 1
    only = read_task(task_files[0].parent)["only_sessions"]
    assert only == [d.isoformat() for d in _expected_holdout()]
    assert not MIXED_DATES & {date.fromisoformat(d) for d in only}
    manifest = json.loads((tmp_path / "run" / MANIFEST_FILE_NAME).read_text(encoding="utf-8"))
    assert manifest["holdout"]["last"] == "2026-03-12"


def test_cadence_eligibility_excludes_mixed_sessions(dirs: tuple[Path, Path, Path]) -> None:
    cache_dir, mixed_dir, plain_dir = dirs
    cfg = light_config(holdout_fraction=FRACTION)
    sessions = (date(2026, 3, 2), OLDER, date(2026, 3, 4))
    with DataCache(cache_dir) as cache:
        mixed = stored_intervals(cfg, cache, sessions, roll=_roll(mixed_dir))
        plain = stored_intervals(cfg, cache, sessions, roll=_roll(plain_dir))
    assert [i.bar_s for i in mixed] == [60, None, 60]
    assert [i.bar_s for i in plain] == [60, 60, 60]
    assert eligible(mixed, (60,)) == (date(2026, 3, 2), date(2026, 3, 4))
    assert eligible(plain, (60,)) == sessions


def test_the_cadence_comparison_lists_a_mixed_session_as_excluded(
    dirs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    cache_dir, mixed_dir, _ = dirs
    cfg = light_config(holdout_fraction=FRACTION)
    result = run_cadence_comparison(
        cfg, DataRange(date(2026, 3, 2), date(2026, 3, 4)), cadences=(60, 300),
        cache_dir=cache_dir, calendar_dir=mixed_dir, out_dir=tmp_path / "cadence",
        writer=_writer(), seed=7, evaluator=reliable_evaluator, code_version="test-version",
    )  # fmt: skip
    assert result.excluded == (OLDER,)


def test_an_experiment_records_the_holdout_without_mixed_sessions(
    dirs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    cache_dir, mixed_dir, _ = dirs
    cfg = light_config(holdout_fraction=FRACTION)
    out = tmp_path / "experiment"
    run_experiment(
        "test", cfg, [ExperimentConfig("a", cfg)], DataRange(ALL_SESSIONS[0], NEWEST),
        cache_dir=cache_dir, calendar_dir=mixed_dir, out_dir=out, writer=_writer(), seed=7,
        evaluator=reliable_evaluator, code_version="test-version",
    )  # fmt: skip
    manifest = json.loads((out / MANIFEST_FILE_NAME).read_text(encoding="utf-8"))
    expected = _expected_holdout()
    assert manifest["holdout"] == {"first": expected[0].isoformat(), "last": "2026-03-12"}
    # The configuration runs on the sessions before the Holdout_Period; the
    # mixed 03-03 is not among the sessions it evaluated.
    evaluated = manifest["sessions_evaluated"]
    assert OLDER.isoformat() not in evaluated
    assert evaluated == [d.isoformat() for d in ALL_SESSIONS if d < expected[0] and d != OLDER]
