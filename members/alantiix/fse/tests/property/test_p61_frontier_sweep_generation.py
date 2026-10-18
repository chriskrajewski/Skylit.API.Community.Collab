"""Property 61: Frontier sweep generation.

*For any* valid sweep definition, the generated configurations are exactly
one per (Exit_Mode, R multiple) pair plus one per Exit_Mode without an R
parameter, each with min_reward_risk = min(R multiple, base threshold) (base
threshold when no R multiple), and identical to the base in every other
parameter, data range, cost setting and seed; any invalid definition is
rejected before evaluation with each invalid value named.

**Inputs.** A base config with a random global Exit_Mode, random per-Regime
settings (some unset), random ``r_multiple`` values and a random
min_reward_risk threshold. A sweep definition: a non-empty list of distinct
Exit_Modes, distinct R multiples in (0, 10] (or omitted: the defaults), and
an optional ranking objective and bootstrap resample count. An invalid
definition adds one to three defects (no or an unknown or repeated Exit_Mode,
no or a non-positive, too large, non-finite, non-numeric or repeated R
multiple, an unknown objective, a resample count out of range, an unknown key).

**Evaluator.** :func:`recording_evaluator` writes the task it got (range,
seed, bootstrap resamples, config hash) into the configuration's directory
and returns a zero-trade result.

**Checks.**

- Invalid: :class:`SweepError` names each defect's value; nothing is
  evaluated and the experiment directory is not created.
- Valid: the configurations are, in order, one per listed Exit_Mode, or one
  per R multiple for an Exit_Mode with an R parameter; each differs from the
  base only in the Exit_Mode selection keys, that mode's ``r_multiple`` and
  ``gates.min_reward_risk.min`` = min(R, base) (base when no R). Every task
  got the same data range, seed and resample count; ``frontier.json`` lists
  the rows in order with their Exit_Mode, R multiple and threshold.

**Validates: Requirements 20.7, 20.8**
"""

from __future__ import annotations

import io
import json
import math
import tempfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any, Final

import pytest
from hypothesis import given
from hypothesis import strategies as st

from fse.analytics.metrics import MetricsCfg, summarize
from fse.analytics.montecarlo import PassEstimate
from fse.backtest.manifest import DataRange
from fse.config.hashing import config_hash
from fse.config.schema import StrategyConfig
from fse.config.schema.bootstrap import BOOTSTRAP_RESAMPLES_MAX, BOOTSTRAP_RESAMPLES_MIN
from fse.config.schema.exits import EXIT_MODES, EXIT_REGIMES
from fse.config.schema.experiments import RANKING_OBJECTIVES
from fse.experiments.runner import ConfigResult, EvalTask
from fse.experiments.sweep import (
    DEFAULT_R_MULTIPLES,
    FRONTIER_FILE_NAME,
    R_EXIT_MODES,
    SweepError,
    point_name,
    run_sweep,
)
from fse.logio import LogWriter, Redactor
from tests.fakes.configs import minimal_config_data
from tests.strategies.backtest_inputs import (
    CALENDAR,
    FAKE_SECRET,
    SESSIONS,
    SYMBOLS,
    bar_opens,
    build_session,
    write_cache,
    write_calendars,
)

TASK_FILE: Final = "task.json"
PATHS: Final = 1_000
STOP_RULES: Final = ("one_node_beyond", "fixed_ticks")


def recording_evaluator(task: EvalTask, writer: LogWriter) -> ConfigResult:
    """Record the task, then return a zero-trade result over the range's sessions."""
    task.out_dir.mkdir(parents=True)
    writer.write_json(
        task.out_dir / TASK_FILE,
        {
            "range": task.data_range.to_json(),
            "seed": task.seed,
            "bootstrap_resamples": task.bootstrap_resamples,
            "config_hash": config_hash(task.cfg),
        },
    )
    sessions = CALENDAR.sessions(task.data_range.start, task.data_range.end)
    estimate = PassEstimate(
        seed=task.seed, paths=PATHS, max_days=60, min_sessions=40, sessions=len(sessions),
        truncated_by_run_account=0, passed=0, failed=0, unresolved=PATHS,
        median_days_to_pass=None, p90_days_to_pass=None,
    )  # fmt: skip
    return ConfigResult(
        run_id=f"fake-{task.index}",
        sessions=sessions,
        skipped=(),
        metrics=summarize([], sessions, MetricsCfg()),
        pass_estimate=estimate,
    )


# ---------------------------------------------------------------- inputs


@pytest.fixture(scope="module")
def data_dirs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("p61")
    sessions = [build_session(d, 6100 + d.day, bar_opens(d, 12, None)) for d in SESSIONS]
    return write_cache(root / "cache", sessions), write_calendars(root / "calendars")


R_VALUES: Final = st.one_of(
    st.sampled_from((0.5, 1, 1.5, 2, 3, 10, 0.25)),
    st.floats(min_value=0, max_value=10, exclude_min=True, allow_nan=False),
)
SCHEMA_R: Final = st.floats(min_value=0.5, max_value=10.0, allow_nan=False)


@st.composite
def base_configs(draw: st.DrawFn) -> StrategyConfig:
    data = minimal_config_data()
    setting = st.fixed_dictionaries(
        {"mode": st.sampled_from(EXIT_MODES), "stop_rule": st.sampled_from(STOP_RULES)}
    )
    threshold = draw(st.floats(min_value=0.25, max_value=20.0, allow_nan=False), label="min_rr")
    data.update(
        {
            "data": {"symbols": list(SYMBOLS), "nq_sources": ["QQQ"]},
            "exits": {
                "global": draw(setting, label="global"),
                "per_regime": {r: draw(st.none() | setting, label=r) for r in EXIT_REGIMES},
                "modes": {
                    "fixed_r": {"r_multiple": draw(SCHEMA_R)},
                    "opposition_or_fixed_r": {"r_multiple": draw(SCHEMA_R)},
                },
            },
            "gates": {
                "min_reward_risk": {
                    "enabled": draw(st.booleans()),
                    "min": threshold,
                    "alert_min": min(2.0, threshold),
                }
            },
            "experiments": {"holdout_fraction": 0.05},
        }
    )
    return StrategyConfig.model_validate(data)


@st.composite
def valid_definitions(draw: st.DrawFn) -> dict[str, Any]:
    modes = draw(st.lists(st.sampled_from(EXIT_MODES), min_size=1, max_size=5, unique=True))
    out: dict[str, Any] = {"exit_modes": modes}
    if draw(st.booleans(), label="list R multiples"):
        out["r_multiples"] = draw(
            st.lists(R_VALUES, min_size=1, max_size=4, unique_by=float), label="r_multiples"
        )
    if draw(st.booleans(), label="set objective"):
        out["ranking_objective"] = draw(st.sampled_from(RANKING_OBJECTIVES))
    if draw(st.booleans(), label="set resamples"):
        out["bootstrap_resamples"] = draw(
            st.integers(BOOTSTRAP_RESAMPLES_MIN, BOOTSTRAP_RESAMPLES_MAX)
        )
    return out


BAD_R: Final = st.sampled_from(
    (0, -1, 0.0, -0.5, 10.000001, 11, 10**400, math.nan, math.inf, "2", True, None)
)

# Each defect returns the value an error must name (by repr), or a phrase.
_DEFECTS: Final = (
    "no_modes",
    "missing_modes",
    "unknown_mode",
    "repeated_mode",
    "no_r",
    "bad_r",
    "repeated_r",
    "bad_objective",
    "bad_resamples",
    "unknown_key",
)


@st.composite
def invalid_definitions(draw: st.DrawFn) -> tuple[dict[str, Any], list[str]]:
    """A definition with one to three defects, and the text each error must contain."""
    defn = draw(valid_definitions())
    defects = draw(st.lists(st.sampled_from(_DEFECTS), min_size=1, max_size=3, unique=True))
    if "no_modes" in defects and "missing_modes" in defects:
        defects.remove("missing_modes")
    if {"no_modes", "missing_modes"} & set(defects):
        defects = [d for d in defects if d not in ("unknown_mode", "repeated_mode")]
    if "no_r" in defects:
        defects = [d for d in defects if d not in ("bad_r", "repeated_r")]
    named: list[str] = []
    for d in defects:
        if d == "no_modes":
            defn["exit_modes"] = []
            named.append("no Exit_Mode")
        elif d == "missing_modes":
            del defn["exit_modes"]
            named.append("no Exit_Mode")
        elif d == "unknown_mode":
            bad = draw(st.sampled_from(("fixed", "Fixed_R", "", 3, "trail")))
            defn["exit_modes"] = [*defn["exit_modes"], bad]
            named.append(repr(bad))
        elif d == "repeated_mode":
            again = defn["exit_modes"][0]
            defn["exit_modes"] = [*defn["exit_modes"], again]
            named.append(f"{again!r} is listed twice")
        elif d == "no_r":
            defn["r_multiples"] = []
            named.append("no R multiple")
        elif d == "bad_r":
            bad_r = draw(BAD_R)
            defn["r_multiples"] = [*defn.get("r_multiples", [1.0]), bad_r]
            named.append(repr(bad_r))
        elif d == "repeated_r":
            listed = defn.get("r_multiples", [1.0])
            defn["r_multiples"] = [*listed, float(listed[0])]
            named.append(f"{float(listed[0])!r} is listed twice")
        elif d == "bad_objective":
            bad_o = draw(st.sampled_from(("win_rate", "Expectancy", "", 1)))
            defn["ranking_objective"] = bad_o
            named.append(repr(bad_o))
        elif d == "bad_resamples":
            bad_n = draw(st.sampled_from((0, 999, 100_001, 5000.0, True, "5000", -1)))
            defn["bootstrap_resamples"] = bad_n
            named.append(repr(bad_n))
        else:
            defn["extra"] = 1
            named.append("'extra'")
    return defn, named


def _flat(obj: object, path: str = "") -> Iterator[tuple[str, object]]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _flat(v, f"{path}.{k}" if path else str(k))
    else:
        yield path, obj


# ---------------------------------------------------------------- the properties

FIRST: Final[date] = SESSIONS[0]
LAST: Final[date] = SESSIONS[-1]


def _run(base: StrategyConfig, defn: object, out: Path, dirs: tuple[Path, Path], seed: int) -> Any:
    cache_dir, calendar_dir = dirs
    writer = LogWriter(Redactor([FAKE_SECRET]), stdout=io.StringIO(), stderr=io.StringIO())
    return run_sweep(
        base, defn, DataRange(FIRST, LAST), cache_dir=cache_dir, calendar_dir=calendar_dir,
        out_dir=out, writer=writer, seed=seed, evaluator=recording_evaluator,
        code_version="test-version",
    )  # fmt: skip


@given(base=base_configs(), case=invalid_definitions(), seed=st.integers(0, 2**63 - 1))
def test_invalid_sweep_rejected(
    data_dirs: tuple[Path, Path],
    base: StrategyConfig,
    case: tuple[dict[str, Any], list[str]],
    seed: int,
) -> None:
    defn, named = case
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "sweep"
        with pytest.raises(SweepError) as info:
            _run(base, defn, out, data_dirs, seed)
        assert not out.exists()
    text = str(info.value)
    for phrase in named:
        assert phrase in text, (phrase, text)
    assert len(info.value.problems) >= len(named)


@given(base=base_configs(), defn=valid_definitions(), seed=st.integers(0, 2**63 - 1))
def test_frontier_sweep_generation(
    data_dirs: tuple[Path, Path], base: StrategyConfig, defn: dict[str, Any], seed: int
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(base, defn, Path(tmp) / "sweep", data_dirs, seed)

        r_multiples = [float(r) for r in defn.get("r_multiples", DEFAULT_R_MULTIPLES)]
        base_rr = base.gates.min_reward_risk.min
        expected: list[tuple[str, float | None]] = []
        for mode in defn["exit_modes"]:
            if mode in R_EXIT_MODES:
                expected.extend((mode, r) for r in r_multiples)
            else:
                expected.append((mode, None))
        assert [(p.exit_mode, p.r_multiple) for p in result.points] == expected
        assert [o.name for o in result.experiment.outcomes] == [
            point_name(m, r) for m, r in expected
        ]
        assert result.definition.ranking_objective == defn.get(
            "ranking_objective", base.experiments.ranking_objective
        )
        resamples = defn.get("bootstrap_resamples", base.reporting.bootstrap_resamples)
        assert result.definition.bootstrap_resamples == resamples

        base_flat = dict(_flat(base.model_dump(mode="python")))
        for point in result.points:
            mode, r = point.exit_mode, point.r_multiple
            threshold = base_rr if r is None else min(r, base_rr)
            assert point.min_reward_risk == threshold
            want = dict(base_flat)
            want["exits.global.mode"] = mode
            for regime in EXIT_REGIMES:
                if f"exits.per_regime.{regime}.mode" in base_flat:  # configured, not None
                    want[f"exits.per_regime.{regime}.mode"] = mode
            if r is not None:
                want[f"exits.modes.{mode}.r_multiple"] = r
            want["gates.min_reward_risk.min"] = threshold
            assert dict(_flat(point.cfg.model_dump(mode="python"))) == want

        # Same data range, seed and resample count for every configuration.
        run_dir = result.experiment.run_dir
        rng = result.experiment.data_range.to_json()
        for o in result.experiment.outcomes:
            task_dir = run_dir / "configs" / f"{o.index:03d}-{o.name}"
            task = json.loads((task_dir / TASK_FILE).read_text())
            assert task == {
                "range": rng,
                "seed": seed,
                "bootstrap_resamples": resamples,
                "config_hash": o.config_hash,
            }
        frontier = json.loads((run_dir / FRONTIER_FILE_NAME).read_text())
        assert [
            (row["exit_mode"], row["r_multiple"], row["min_reward_risk"])
            for row in frontier["rows"]
        ] == [
            (p.exit_mode, None if p.r_multiple is None else repr(p.r_multiple),
             repr(p.min_reward_risk))
            for p in result.points
        ]  # fmt: skip
