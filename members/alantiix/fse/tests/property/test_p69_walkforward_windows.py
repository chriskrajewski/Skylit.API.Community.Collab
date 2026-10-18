"""Property 69: Walk-forward windows and selection.

*For any* session count and window lengths, training windows directly
precede their test windows, test windows never overlap and step by the test
length, a short final test window is dropped, the selected configuration on
each training window is the highest-objective configuration that is not
labeled insufficient sample (first listed on ties), and that configuration
runs unchanged on the following test window.

**Inputs.** A light Data_Cache over the 29 sessions of the test calendar,
with data on every session or on all but every fifth. A holdout fraction,
training (10 to 14) and test (5 to 8) window lengths, the walk-forward
objective, the "insufficient sample" minimum (1 to 8 trades), and 1 to 4
configurations (``orders.max_open`` drawn, so two can be the same config).
The fake evaluator's outcomes sit on a coarse grid, so ties are common.

**Model.** The sessions are those with data before the Holdout_Period. Pair
``k`` trains on sessions ``[k*test, k*test + train)`` and tests on the next
``test`` sessions, while the test window is whole. On each training window
the selection is the first configuration with the highest objective among
completed ones with at least the minimum trades and a numeric objective, or
"no selection".

**Checks.** With fewer sessions than one training plus one test window the
test is refused and nothing is written. Otherwise the pairs, their windows,
the training runs' ranges, the selections and the "no selection" count equal
the model; every test run evaluates the selected configuration's hash on
exactly its test window; and ``walkforward.json`` records the same.

**Validates: Requirements 22.9, 22.10, 22.11, 22.16**
"""

from __future__ import annotations

import io
import json
import math
import tempfile
from datetime import date
from fractions import Fraction
from pathlib import Path
from typing import Any, Final

import pytest
from hypothesis import event, given
from hypothesis import strategies as st

from fse.backtest.manifest import DataRange
from fse.config.hashing import config_hash
from fse.config.schema import StrategyConfig
from fse.config.schema.experiments import RANKING_OBJECTIVES
from fse.experiments.runner import ExperimentConfig
from fse.experiments.walkforward import (
    WALKFORWARD_FILE_NAME,
    WalkForwardError,
    WalkForwardResult,
    run_walkforward,
)
from fse.logio import LogWriter, Redactor
from tests.strategies.backtest_inputs import FAKE_SECRET, write_calendars
from tests.strategies.experiment_inputs import (
    ALL_SESSIONS,
    PATHS,
    LightSession,
    fake_evaluator,
    light_config,
    planned,
    read_task,
    write_light_cache,
)

PATTERNS: Final[dict[str, tuple[date, ...]]] = {
    "all": ALL_SESSIONS,
    "gaps": tuple(d for i, d in enumerate(ALL_SESSIONS) if i % 5 != 4),
}
FRACTIONS: Final = (0.05, 0.1, 0.2, 0.34)


@pytest.fixture(scope="module")
def data_dirs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, tuple[Path, Path]]:
    root = tmp_path_factory.mktemp("p69")
    calendars = write_calendars(root / "calendars")
    return {
        name: (write_light_cache(root / name, [LightSession(d) for d in days]), calendars)
        for name, days in PATTERNS.items()
    }


def _with_max_open(base: StrategyConfig, max_open: int) -> StrategyConfig:
    data = base.model_dump(mode="python", by_alias=True)
    data["orders"]["max_open"] = max_open
    return StrategyConfig.model_validate(data)


def _objective(plan: dict[str, Any] | None, objective: str) -> object:
    assert plan is not None
    if objective == "combine_pass_probability":
        return Fraction(plan["passed"], PATHS)
    return plan["expectancy_r" if objective == "expectancy" else "profit_factor"]


def _model_selection(
    hashes: list[str], train: tuple[date, ...], objective: str, min_trades: int
) -> int | None:
    best: int | None = None
    best_value: Fraction | None = None
    for i, h in enumerate(hashes):
        plan = planned(h, train)
        if plan is None or plan["trade_count"] < min_trades:
            continue
        value = _objective(plan, objective)
        if isinstance(value, Fraction) and (best_value is None or value > best_value):
            best, best_value = i, value
    return best


@given(
    pattern=st.sampled_from(sorted(PATTERNS)),
    fraction=st.sampled_from(FRACTIONS),
    train=st.integers(10, 14),
    test=st.integers(5, 8),
    objective=st.sampled_from(RANKING_OBJECTIVES),
    min_trades=st.integers(1, 8),
    max_opens=st.lists(st.integers(1, 3), min_size=1, max_size=4),
    seed=st.integers(0, 2**63 - 1),
)
def test_walkforward_windows_and_selection(
    data_dirs: dict[str, tuple[Path, Path]],
    pattern: str,
    fraction: float,
    train: int,
    test: int,
    objective: str,
    min_trades: int,
    max_opens: list[int],
    seed: int,
) -> None:
    cache_dir, calendar_dir = data_dirs[pattern]
    with_data = PATTERNS[pattern]
    held = math.ceil(Fraction(repr(fraction)) * len(with_data))
    sessions = with_data[:-held]
    base = light_config(
        holdout_fraction=fraction,
        walkforward={
            "train": train,
            "test": test,
            "objective": objective,
            "min_trades": min_trades,
        },
        montecarlo={"paths": 1_000},
    )
    configs = [ExperimentConfig(f"c{i}", _with_max_open(base, m)) for i, m in enumerate(max_opens)]
    hashes = [config_hash(c.cfg) for c in configs]
    writer = LogWriter(Redactor([FAKE_SECRET]), stdout=io.StringIO(), stderr=io.StringIO())
    requested = DataRange(ALL_SESSIONS[0], ALL_SESSIONS[-1])

    # The model's window pairs.
    pairs: list[tuple[tuple[date, ...], tuple[date, ...]]] = []
    k = 0
    while k * test + train + test <= len(sessions):
        start = k * test
        pairs.append(
            (sessions[start : start + train], sessions[start + train : start + train + test])
        )
        k += 1

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "wf"

        def walk() -> WalkForwardResult:
            return run_walkforward(
                base, configs, requested, cache_dir=cache_dir, calendar_dir=calendar_dir,
                out_dir=out, writer=writer, seed=seed, evaluator=fake_evaluator,
                code_version="test-version",
            )  # fmt: skip

        if len(sessions) < train + test:
            assert not pairs
            with pytest.raises(WalkForwardError, match=f"{train + test} sessions"):
                walk()
            assert not out.exists()
            return
        result = walk()
        event(f"{len(pairs)} pair(s)")
        assert result.sessions == sessions
        assert [(p.pair.train, p.pair.test) for p in result.pairs] == pairs

        index = {d: i for i, d in enumerate(sessions)}
        previous_test_end = -1
        for p in result.pairs:
            tr, te = p.pair.train, p.pair.test
            # Training directly precedes testing; tests step by `test` and never overlap.
            assert len(tr) == train
            assert len(te) == test
            assert index[te[0]] == index[tr[-1]] + 1
            assert index[te[0]] > previous_test_end
            assert index[te[0]] == p.pair.index * test + train
            previous_test_end = index[te[-1]]

            # Every configuration trains on exactly the training window.
            assert p.train.data_range == DataRange(tr[0], tr[-1])
            assert [o.config_hash for o in p.train.outcomes] == hashes
            assert p.configurations_evaluated == len(set(hashes))
            for o in p.train.outcomes:
                assert o.insufficient_sample == (
                    o.result is not None and o.result.metrics.trade_count < min_trades
                )

            expected = _model_selection(hashes, tr, objective, min_trades)
            assert p.selected == expected
            if expected is None:
                event("no selection")
                assert p.test is None
                continue
            # The selected configuration runs unchanged on the next test window.
            assert p.test is not None
            assert [o.config_hash for o in p.test.outcomes] == [hashes[expected]]
            assert p.test.data_range == DataRange(te[0], te[-1])
            task = read_task(p.test.run_dir / "configs" / f"000-{configs[expected].name}")
            assert task["config_hash"] == hashes[expected]
            assert task["range"] == {"start": te[0].isoformat(), "end": te[-1].isoformat()}
            if p.test.outcomes[0].result is not None:
                assert p.test.outcomes[0].result.sessions == te

        # A whole test window does not fit after the last pair.
        last = result.pairs[-1].pair.index
        assert (last + 1) * test + train + test > len(sessions)

        saved = json.loads((result.run_dir / WALKFORWARD_FILE_NAME).read_text())
        assert saved["no_selection_pairs"] == sum(1 for p in result.pairs if p.selected is None)
        assert saved["no_selection_pairs"] == result.no_selection_count
        assert saved["distinct_configurations"] == len(set(hashes))
        assert [s["selected"] for s in saved["pairs"]] == [
            None if p.selected is None else configs[p.selected].name for p in result.pairs
        ]
        assert [s["configurations_evaluated"] for s in saved["pairs"]] == [len(set(hashes))] * len(
            result.pairs
        )
