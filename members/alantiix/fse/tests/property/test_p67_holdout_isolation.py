"""Property 67: Holdout isolation.

*For any* set of sessions with data and holdout fraction, the Holdout_Period
is the newest ceil(fraction x n) sessions; no sweep, ablation, cadence
comparison or walk-forward test evaluates a holdout session, even when the
requested range includes it; and a holdout evaluation evaluates only holdout
sessions.

**Inputs.** One of four light Data_Caches (:mod:`tests.strategies.experiment_inputs`)
over the 29 sessions of the test calendar: every session with data, every
fourth session without, only the newest 18 with data, or every other session
with data. A holdout fraction from 0.05 to 0.50, a requested range (the
whole calendar half of the time, else anywhere in it), and a seed.

**Model.** The Holdout_Period is the newest ``ceil(fraction x n)`` of the
``n`` sessions with data.

**Checks.** For each of an ablation, a sweep (one Exit_Mode, one R multiple),
a cadence comparison (60 s and 300 s, which every light session supports)
and a walk-forward test (10 training and 5 test sessions) over the requested
range, with the fake evaluator:

- when no requested session lies outside the Holdout_Period (for the cadence
  comparison: no such session with data; for the walk-forward test: fewer
  than 15 such sessions with data) the run is refused and nothing is written;
- otherwise the Run_Manifest records the Holdout_Period's first and last
  dates, every configuration's task range ends before the Holdout_Period,
  and no evaluated session is a holdout session.

A holdout evaluation of the base config runs one task on exactly the holdout
sessions (range first to last, ``only_sessions`` the holdout sessions) and
evaluates every holdout session and nothing else.

**Validates: Requirements 22.1, 22.2, 22.4**
"""

from __future__ import annotations

import io
import json
import math
import tempfile
from collections.abc import Iterator
from datetime import date
from fractions import Fraction
from pathlib import Path
from typing import Final

import pytest
from hypothesis import event, given
from hypothesis import strategies as st

from fse.backtest.manifest import MANIFEST_FILE_NAME, DataRange
from fse.config.printer import dump
from fse.config.schema import StrategyConfig
from fse.experiments.ablation import run_ablation
from fse.experiments.cadence import run_cadence_comparison
from fse.experiments.holdout import compute_holdout_period, run_holdout_evaluation
from fse.experiments.runner import ExperimentConfig, ExperimentInputError, ExperimentResult
from fse.experiments.sweep import run_sweep
from fse.experiments.walkforward import WalkForwardResult, run_walkforward
from fse.logio import LogWriter, Redactor
from tests.strategies.backtest_inputs import FAKE_SECRET, write_calendars
from tests.strategies.experiment_inputs import (
    ALL_SESSIONS,
    LightSession,
    fake_evaluator,
    light_config,
    read_task,
    reliable_evaluator,
    write_light_cache,
)

PATTERNS: Final[dict[str, tuple[date, ...]]] = {
    "all": ALL_SESSIONS,
    "gaps": tuple(d for i, d in enumerate(ALL_SESSIONS) if i % 4 != 3),
    "newest": ALL_SESSIONS[-18:],
    "every_other": ALL_SESSIONS[::2],
}
FRACTIONS: Final = (0.05, 0.1, 0.2, 0.25, 0.34, 0.5)
TRAIN: Final = 10
TEST: Final = 5


@pytest.fixture(scope="module")
def data_dirs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, tuple[Path, Path]]:
    root = tmp_path_factory.mktemp("p67")
    calendars = write_calendars(root / "calendars")
    return {
        name: (write_light_cache(root / name, [LightSession(d) for d in days]), calendars)
        for name, days in PATTERNS.items()
    }


@st.composite
def ranges(draw: st.DrawFn) -> DataRange:
    if draw(st.booleans(), label="whole calendar"):  # so walk-forward tests run often
        return DataRange(ALL_SESSIONS[0], ALL_SESSIONS[-1])
    first = draw(st.integers(0, len(ALL_SESSIONS) - 1), label="first")
    last = draw(st.integers(first, len(ALL_SESSIONS) - 1), label="last")
    return DataRange(ALL_SESSIONS[first], ALL_SESSIONS[last])


def _config_dirs(run_dir: Path) -> Iterator[Path]:
    yield from sorted(p.parent for p in run_dir.rglob("task.json"))


def _check_experiment(result: ExperimentResult, holdout: tuple[date, ...]) -> None:
    manifest = json.loads((result.run_dir / MANIFEST_FILE_NAME).read_text())
    assert manifest["holdout"] == {"first": holdout[0].isoformat(), "last": holdout[-1].isoformat()}
    assert result.data_range.end < holdout[0]
    assert not set(result.sessions) & set(holdout)
    for o in result.outcomes:
        if o.result is not None:
            assert not set(o.result.sessions) & set(holdout)
    _check_tasks(result.run_dir, holdout)


def _check_tasks(run_dir: Path, holdout: tuple[date, ...]) -> None:
    dirs = list(_config_dirs(run_dir))
    assert dirs
    for d in dirs:
        task = read_task(d)
        assert date.fromisoformat(task["range"]["end"]) < holdout[0]
        assert not {date.fromisoformat(x) for x in task["only_sessions"] or ()} & set(holdout)


@given(
    pattern=st.sampled_from(sorted(PATTERNS)),
    fraction=st.sampled_from(FRACTIONS),
    requested=ranges(),
    seed=st.integers(0, 2**63 - 1),
)
def test_holdout_isolation(
    data_dirs: dict[str, tuple[Path, Path]],
    pattern: str,
    fraction: float,
    requested: DataRange,
    seed: int,
) -> None:
    cache_dir, calendar_dir = data_dirs[pattern]
    with_data = PATTERNS[pattern]
    held = math.ceil(Fraction(repr(fraction)) * len(with_data))
    holdout = with_data[-held:]
    assert compute_holdout_period(with_data, fraction).sessions == holdout
    outside = tuple(d for d in ALL_SESSIONS if d in requested and d < holdout[0])
    outside_with_data = tuple(d for d in with_data if d in outside)
    base = light_config(
        holdout_fraction=fraction,
        walkforward={"train": TRAIN, "test": TEST, "min_trades": 1},
        montecarlo={"paths": 1_000},
    )
    writer = LogWriter(Redactor([FAKE_SECRET]), stdout=io.StringIO(), stderr=io.StringIO())

    def ablation(out: Path) -> ExperimentResult:
        return run_ablation(
            base, requested, cache_dir=cache_dir, calendar_dir=calendar_dir, out_dir=out,
            writer=writer, seed=seed, evaluator=fake_evaluator, code_version="test-version",
        )  # fmt: skip

    def sweep(out: Path) -> ExperimentResult:
        return run_sweep(
            base, {"exit_modes": ["fixed_r"], "r_multiples": [1.0]}, requested,
            cache_dir=cache_dir, calendar_dir=calendar_dir, out_dir=out, writer=writer,
            seed=seed, evaluator=fake_evaluator, code_version="test-version",
        ).experiment  # fmt: skip

    def cadence(out: Path) -> ExperimentResult:
        return run_cadence_comparison(
            base, requested, cadences=(60, 300), cache_dir=cache_dir,
            calendar_dir=calendar_dir, out_dir=out, writer=writer, seed=seed,
            evaluator=fake_evaluator, code_version="test-version",
        )  # fmt: skip

    def walkforward(out: Path) -> WalkForwardResult:
        configs = [ExperimentConfig("a", base), ExperimentConfig("b", _variant(base))]
        return run_walkforward(
            base, configs, requested, cache_dir=cache_dir, calendar_dir=calendar_dir,
            out_dir=out, writer=writer, seed=seed, evaluator=fake_evaluator,
            code_version="test-version",
        )  # fmt: skip

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for run in (ablation, sweep, cadence):
            out = root / run.__name__
            # A cadence comparison also needs a session with stored data (Req 18.9).
            if not (outside_with_data if run is cadence else outside):
                with pytest.raises(ExperimentInputError):
                    run(out)
                assert not out.exists(), run.__name__
            else:
                event(f"{run.__name__} ran")
                _check_experiment(run(out), holdout)

        # Walk-forward: windows over the sessions with data outside the Holdout_Period.
        out = root / "walkforward"
        if len(outside_with_data) < TRAIN + TEST:
            with pytest.raises(ExperimentInputError):
                walkforward(out)
            assert not out.exists()
        else:
            wf = walkforward(out)
            event(f"walk-forward ran, {len(wf.pairs)} pair(s)")
            assert wf.holdout is not None
            assert wf.holdout.sessions == holdout
            assert wf.sessions == outside_with_data
            for p in wf.pairs:
                _check_experiment(p.train, holdout)
                if p.test is not None:
                    _check_experiment(p.test, holdout)

        # The holdout evaluation: exactly the holdout sessions.
        config_path = root / "base.yaml"
        config_path.write_text(dump(base), encoding="utf-8")
        log_path = root / "holdout_log.jsonl"
        ev = run_holdout_evaluation(
            config_path, log_path=log_path, cache_dir=cache_dir, calendar_dir=calendar_dir,
            out_dir=root / "holdout", writer=writer, seed=seed, evaluator=reliable_evaluator,
            code_version="test-version",
        )  # fmt: skip
        assert ev.holdout.sessions == holdout
        assert ev.result.sessions == holdout
        task = read_task(ev.run_dir)
        assert task["range"] == {"start": holdout[0].isoformat(), "end": holdout[-1].isoformat()}
        assert task["only_sessions"] == [d.isoformat() for d in holdout]
        manifest = json.loads((ev.run_dir / MANIFEST_FILE_NAME).read_text())
        assert manifest["kind"] == "holdout"
        assert manifest["sessions_evaluated"] == [d.isoformat() for d in holdout]
        assert len(log_path.read_bytes().splitlines()) == 1


def _variant(base: StrategyConfig) -> StrategyConfig:
    data = base.model_dump(mode="python", by_alias=True)
    data["orders"]["max_open"] = 2 if data["orders"]["max_open"] != 2 else 1
    return StrategyConfig.model_validate(data)
