"""Property 59: Ablation variants.

*For any* base config with N enabled Gates, the ablation produces N + 1
configs, each variant differing from the base in exactly one Gate's enabled
flag, all evaluated on the same session list and seed, and each reported
change equals variant minus base, or not available when either value is
undefined or the config failed.

**Inputs.** A Data_Cache with full data on 8 of the 9 March sessions of the
test calendar (2026-03-04 has none), written once. A base config with a
random subset of Gates enabled, a holdout fraction from 0.05 to 0.50 and a
sample minimum; a requested range inside March; a seed; 1 or 2 workers.

**Evaluator.** :func:`fake_evaluator` stands in for the backtest. It writes
the task it got (range, seed, config hash) to ``task.json`` in the
configuration's directory, then derives the outcome from the config hash
alone (:func:`planned`): a failure (an error naming a fake secret) or a
trade count, the five metrics (some ``NotApplicable``) and a pass count. It
is a module-level function, so it also runs in a worker process.

**Model.** The Holdout_Period is the newest ``ceil(fraction x 8)`` sessions
with data; the experiment's sessions are the requested sessions before it.
Each change is variant minus base when both are numbers, else not available.

**Checks.**

- No session outside the Holdout_Period: the run is refused with nothing
  written. Otherwise:
- N + 1 outcomes: ``base`` first, then ``no_<gate>`` per enabled Gate in Gate
  order. Each variant's dump differs from the base's only at
  ``gates.<gate>.enabled`` (true to false).
- Every task got the same range, spanning the model's sessions, and the
  experiment's seed; ``comparison.json`` and the Run_Manifest record the
  sessions, seed and Holdout_Period dates.
- Each cell equals the plan; each change equals variant minus base, or is
  ``None`` when either value is not a number or a config failed. A failed
  config carries a redacted error. "insufficient sample" is set exactly for
  completed configs below the minimum; the distinct count is N + 1.

**Validates: Requirements 19.13, 19.14, 19.15, 19.16**
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import math
import tempfile
from collections.abc import Iterator, Mapping
from datetime import date
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, Final

import pytest
from hypothesis import given
from hypothesis import strategies as st

from fse.analytics.metrics import Metrics, MetricsCfg, summarize
from fse.analytics.montecarlo import PassEstimate
from fse.backtest.manifest import MANIFEST_FILE_NAME, DataRange
from fse.config.hashing import config_hash
from fse.config.schema import StrategyConfig
from fse.config.schema.gates import GATE_IDS
from fse.engine.types import NotApplicable
from fse.experiments.ablation import (
    ABLATION_FILE_NAME,
    ABLATION_METRICS,
    ablation_rows,
    metric_value,
    run_ablation,
)
from fse.experiments.runner import (
    COMPARISON_FILE_NAME,
    ConfigResult,
    EvalTask,
    ExperimentInputError,
    ExperimentResult,
)
from fse.experiments.variants import disable
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

NO_DATA: Final = date(2026, 3, 4)
WITH_DATA: Final[tuple[date, ...]] = tuple(d for d in SESSIONS if d != NO_DATA)
TASK_FILE: Final = "task.json"
PATHS: Final = 1_000
_NA: Final = NotApplicable()


# ---------------------------------------------------------------- the fake evaluator


def planned(cfg_hash: str) -> dict[str, Any] | None:
    """The outcome of the config with hash ``cfg_hash``: ``None`` for a failure."""
    d = hashlib.sha256(cfg_hash.encode()).digest()
    if d[0] % 4 == 0:
        return None
    return {
        "trade_count": d[1] % 60,
        "trades_per_day": Fraction(d[2], 7),
        "win_rate": _NA if d[3] % 5 == 0 else Fraction(d[3] * 100, 255),
        "expectancy_r": _NA if d[4] % 5 == 0 else Fraction(d[4] - 128, 64),
        "profit_factor": _NA if d[5] % 3 == 0 else Fraction(d[5], 37),
        "max_drawdown_usd": _NA if d[6] % 5 == 0 else Decimal(d[6]) / 4,
        "passed": d[7] * 3,
    }


def _metrics(sessions: tuple[date, ...], plan: Mapping[str, Any]) -> Metrics:
    m = summarize([], sessions, MetricsCfg())
    return dataclasses.replace(
        m,
        trade_count=plan["trade_count"],
        trades_per_day=plan["trades_per_day"],
        primary_win_rate_pct=plan["win_rate"],
        win_rate_a_pct=plan["win_rate"],
        expectancy_r=plan["expectancy_r"],
        profit_factor=plan["profit_factor"],
        max_drawdown_usd=plan["max_drawdown_usd"],
    )


def fake_evaluator(task: EvalTask, writer: LogWriter) -> ConfigResult:
    """Record the task, then return (or raise) the planned outcome of its config."""
    cfg_hash = config_hash(task.cfg)
    task.out_dir.mkdir(parents=True)
    writer.write_json(
        task.out_dir / TASK_FILE,
        {"range": task.data_range.to_json(), "seed": task.seed, "config_hash": cfg_hash},
    )
    plan = planned(cfg_hash)
    if plan is None:
        raise RuntimeError(f"planned failure near {FAKE_SECRET}")
    sessions = CALENDAR.sessions(task.data_range.start, task.data_range.end)
    estimate = PassEstimate(
        seed=task.seed, paths=PATHS, max_days=60, min_sessions=40, sessions=len(sessions),
        truncated_by_run_account=0, passed=plan["passed"], failed=0,
        unresolved=PATHS - plan["passed"], median_days_to_pass=None, p90_days_to_pass=None,
    )  # fmt: skip
    return ConfigResult(
        run_id=f"fake-{cfg_hash[:8]}",
        sessions=sessions,
        skipped=(),
        metrics=_metrics(sessions, plan),
        pass_estimate=estimate,
    )


# ---------------------------------------------------------------- inputs


@pytest.fixture(scope="module")
def data_dirs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("p59")
    sessions = [build_session(d, 5900 + d.day, bar_opens(d, 12, None)) for d in WITH_DATA]
    return write_cache(root / "cache", sessions), write_calendars(root / "calendars")


@st.composite
def base_configs(draw: st.DrawFn) -> StrategyConfig:
    data = minimal_config_data()
    data.update(
        {
            "data": {"symbols": list(SYMBOLS), "nq_sources": ["QQQ"]},
            "gates": {g: {"enabled": draw(st.booleans(), label=g)} for g in GATE_IDS},
            "orders": {"max_open": draw(st.integers(1, 3), label="max_open")},
            "experiments": {
                "holdout_fraction": draw(st.sampled_from((0.05, 0.1, 0.2, 0.34, 0.5))),
                "walkforward": {"min_trades": draw(st.integers(1, 60), label="min trades")},
            },
        }
    )
    return StrategyConfig.model_validate(data)


@st.composite
def ranges(draw: st.DrawFn) -> DataRange:
    first = draw(st.integers(0, len(SESSIONS) - 1), label="first")
    last = draw(st.integers(first, len(SESSIONS) - 1), label="last")
    return DataRange(SESSIONS[first], SESSIONS[last])


def _flat(obj: object, path: str = "") -> Iterator[tuple[str, object]]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _flat(v, f"{path}.{k}" if path else str(k))
    else:
        yield path, obj


def _number(value: Fraction | Decimal | NotApplicable) -> Fraction | NotApplicable:
    return value if isinstance(value, NotApplicable) else Fraction(value)


# ---------------------------------------------------------------- the property


@given(
    base=base_configs(),
    requested=ranges(),
    seed=st.integers(0, 2**63 - 1),
    workers=st.sampled_from((1, 1, 1, 1, 1, 1, 1, 2)),
)
def test_ablation_variants(
    data_dirs: tuple[Path, Path],
    base: StrategyConfig,
    requested: DataRange,
    seed: int,
    workers: int,
) -> None:
    cache_dir, calendar_dir = data_dirs
    held = math.ceil(Fraction(repr(base.experiments.holdout_fraction)) * len(WITH_DATA))
    holdout = WITH_DATA[-held:]
    expected = tuple(d for d in SESSIONS if d in requested and d < holdout[0])
    writer = LogWriter(Redactor([FAKE_SECRET]), stdout=io.StringIO(), stderr=io.StringIO())

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "ablation"

        def ablate() -> ExperimentResult:
            return run_ablation(
                base, requested, cache_dir=cache_dir, calendar_dir=calendar_dir, out_dir=out,
                writer=writer, seed=seed, workers=workers, evaluator=fake_evaluator,
                code_version="test-version",
            )  # fmt: skip

        if not expected:
            with pytest.raises(ExperimentInputError):
                ablate()
            assert not out.exists()
            return
        result = ablate()

        # N + 1 configurations: the base, then one variant per enabled Gate in Gate order.
        enabled = base.gates.enabled_ids()
        names = [o.name for o in result.outcomes]
        assert names == ["base", *(f"no_{g}" for g in enabled)]
        assert result.outcomes[0].config_hash == config_hash(base)
        variants = [base, *(disable(base, g) for g in enabled)]
        base_flat = dict(_flat(base.model_dump(mode="python")))
        for gate, variant in zip(enabled, variants[1:], strict=True):
            flat = dict(_flat(variant.model_dump(mode="python")))
            diff = {k for k in base_flat if base_flat[k] != flat[k]}
            assert diff == {f"gates.{gate}.enabled"}
            assert base_flat[f"gates.{gate}.enabled"] is True
            assert flat[f"gates.{gate}.enabled"] is False
        hashes = [config_hash(v) for v in variants]
        assert [o.config_hash for o in result.outcomes] == hashes
        assert result.distinct_configurations == len(enabled) + 1

        # The same sessions and seed for every configuration, Holdout_Period excluded.
        assert result.sessions == expected
        assert result.data_range == DataRange(expected[0], expected[-1])
        assert result.holdout is not None
        assert result.holdout.sessions == holdout
        for o in result.outcomes:
            task_dir = result.run_dir / "configs" / f"{o.index:03d}-{o.name}"
            task = json.loads((task_dir / TASK_FILE).read_text())
            assert task == {
                "range": result.data_range.to_json(),
                "seed": seed,
                "config_hash": o.config_hash,
            }
        comparison = json.loads((result.run_dir / COMPARISON_FILE_NAME).read_text())
        assert comparison["seed"] == seed
        assert comparison["calendar_sessions"] == [d.isoformat() for d in expected]
        held_json = {"first": holdout[0].isoformat(), "last": holdout[-1].isoformat()}
        assert comparison["holdout"] == held_json
        assert [c["config_hash"] for c in comparison["configurations"]] == hashes
        manifest = json.loads((result.run_dir / MANIFEST_FILE_NAME).read_text())
        assert manifest["kind"] == "ablation"
        assert manifest["status"] == "completed"
        assert manifest["seed"] == seed
        assert manifest["holdout"] == comparison["holdout"]
        assert (result.run_dir / ABLATION_FILE_NAME).is_file()

        # Statuses, labels and errors follow the plan.
        for o in result.outcomes:
            plan = planned(o.config_hash)
            if plan is None:
                assert o.status == "failed"
                assert o.result is None
                assert o.error is not None
                assert FAKE_SECRET not in o.error
                assert "[REDACTED]" in o.error
                assert not o.insufficient_sample
            else:
                assert o.status == "completed"
                assert o.error is None
                assert o.insufficient_sample == (
                    plan["trade_count"] < base.experiments.walkforward.min_trades
                )

        # Each change is variant minus base, or not available.
        rows = ablation_rows(result)
        assert [r.gate_id for r in rows] == list(enabled)
        base_plan = planned(result.outcomes[0].config_hash)
        for row, o in zip(rows, result.outcomes[1:], strict=True):
            plan = planned(o.config_hash)
            for metric in ABLATION_METRICS:
                cell = row.cells[metric]
                for value, p in ((cell.base, base_plan), (cell.variant, plan)):
                    if p is None:
                        assert value is None
                    elif metric == "pass_probability":
                        assert value == Fraction(p["passed"], PATHS)
                    else:
                        assert value == _number(p[metric])
                assert metric_value(o, metric) == cell.variant
                if isinstance(cell.base, Fraction) and isinstance(cell.variant, Fraction):
                    assert cell.change == cell.variant - cell.base
                else:
                    assert cell.change is None
