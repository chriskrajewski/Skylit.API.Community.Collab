"""Property 68: Holdout_Log is append-only.

*For any* sequence of holdout evaluations and failures, each successful
evaluation appends exactly one entry and leaves every earlier byte unchanged,
each failed request leaves the log byte-identical, and the overlap warning
count equals the number of earlier entries whose Holdout_Period shares a
session with the current one.

**Inputs.** A light Data_Cache with data on all 29 sessions of the test
calendar. A Holdout_Log that starts with 0 to 6 drawn entries (each a span of
calendar sessions), and is sometimes made unreadable (a partial last line, a
line that is not JSON, or an entry without its Holdout_Period). Then 1 to 5
requests, each one of: a valid config at a drawn holdout fraction, a missing
config file, a config that fails validation, or a valid config whose
backtest fails.

**Model.** The Holdout_Period is the newest ``ceil(fraction x 29)`` sessions.
An earlier entry overlaps when one of those sessions lies between its first
and last dates.

**Checks.** A failed request raises (``ConfigLoadError`` for the config,
``HoldoutLogError`` for an unreadable log, the evaluator's error) and the log
bytes are unchanged. A successful one leaves the earlier bytes as a prefix
and adds exactly one line: the config hash, the evaluation time, the
Holdout_Period dates, the trade count, expectancy, win rate and pass
probability. Its overlap count equals the model's; with a count above 0 the
warning (with the count) is printed on stderr and recorded in the Run_Manifest,
otherwise there is no warning.

**Validates: Requirements 22.5, 22.6, 22.7**
"""

from __future__ import annotations

import io
import json
import math
import tempfile
from datetime import date
from fractions import Fraction
from pathlib import Path
from typing import Final

import pytest
from hypothesis import given
from hypothesis import strategies as st

from fse.backtest.manifest import MANIFEST_FILE_NAME
from fse.config.hashing import config_hash
from fse.config.loader import ConfigLoadError
from fse.config.printer import dump
from fse.experiments.holdout import HoldoutEvaluation, HoldoutLogError, run_holdout_evaluation
from fse.experiments.runner import ConfigResult, EvalTask
from fse.logio import LogWriter, Redactor
from tests.strategies.backtest_inputs import FAKE_SECRET, STARTED, write_calendars
from tests.strategies.experiment_inputs import (
    ALL_SESSIONS,
    LightSession,
    light_config,
    planned,
    reliable_evaluator,
    write_light_cache,
)

FRACTIONS: Final = (0.05, 0.1, 0.2, 0.34, 0.5)
REQUESTS: Final = ("valid", "missing_config", "invalid_config", "backtest_fails")
CORRUPTIONS: Final = (None, None, None, "partial_line", "not_json", "no_holdout")


def failing_evaluator(task: EvalTask, writer: LogWriter) -> ConfigResult:
    raise RuntimeError(f"backtest failed near {FAKE_SECRET}")


@pytest.fixture(scope="module")
def data_dirs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("p68")
    cache = write_light_cache(root / "cache", [LightSession(d) for d in ALL_SESSIONS])
    return cache, write_calendars(root / "calendars")


@st.composite
def spans(draw: st.DrawFn) -> tuple[date, date]:
    first = draw(st.integers(0, len(ALL_SESSIONS) - 1))
    last = draw(st.integers(first, len(ALL_SESSIONS) - 1))
    return ALL_SESSIONS[first], ALL_SESSIONS[last]


def _line(i: int, first: date, last: date) -> bytes:
    obj = {
        "config_hash": f"{i:064x}",
        "evaluated_at": 1_700_000_000_000_000_000 + i,
        "holdout": {"first": first.isoformat(), "last": last.isoformat()},
    }
    return json.dumps(obj, sort_keys=True).encode() + b"\n"


def _corrupt(kind: str) -> bytes:
    return {
        "partial_line": b'{"config_hash": "ab"',
        "not_json": b"not json\n",
        "no_holdout": b'{"config_hash": "ab", "evaluated_at": 1}\n',
    }[kind]


@given(
    initial=st.lists(spans(), max_size=6),
    corruption=st.sampled_from(CORRUPTIONS),
    requests=st.lists(
        st.tuples(st.sampled_from(REQUESTS), st.sampled_from(FRACTIONS)), min_size=1, max_size=5
    ),
    seed=st.integers(0, 2**63 - 1),
)
def test_holdout_log_append_only(
    data_dirs: tuple[Path, Path],
    initial: list[tuple[date, date]],
    corruption: str | None,
    requests: list[tuple[str, float]],
    seed: int,
) -> None:
    cache_dir, calendar_dir = data_dirs
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        log_path = root / "holdout_log.jsonl"
        content = b"".join(_line(i, a, b) for i, (a, b) in enumerate(initial))
        if corruption is not None:
            content += _corrupt(corruption)
        if content:
            log_path.write_bytes(content)
        spans_so_far = list(initial)

        for i, (request, fraction) in enumerate(requests):
            cfg = light_config(holdout_fraction=fraction, montecarlo={"paths": 1_000})
            config_path = root / f"config-{i}.yaml"
            if request == "invalid_config":
                config_path.write_text("experiments:\n  holdout_fraction: 0.9\n", encoding="utf-8")
            elif request != "missing_config":
                config_path.write_text(dump(cfg), encoding="utf-8")
            before = log_path.read_bytes() if log_path.exists() else None
            stderr = io.StringIO()
            writer = LogWriter(Redactor([FAKE_SECRET]), stdout=io.StringIO(), stderr=stderr)

            evaluator = failing_evaluator if request == "backtest_fails" else reliable_evaluator

            def evaluate() -> HoldoutEvaluation:
                return run_holdout_evaluation(
                    config_path, log_path=log_path, cache_dir=cache_dir,  # noqa: B023
                    calendar_dir=calendar_dir, out_dir=root / f"run-{i}",  # noqa: B023
                    writer=writer, seed=seed, evaluator=evaluator,  # noqa: B023
                    clock=lambda: STARTED + i,  # noqa: B023
                    code_version="test-version",
                )  # fmt: skip

            fails = request != "valid" or corruption is not None
            if fails:
                expected: type[BaseException]
                if request in ("missing_config", "invalid_config"):
                    expected = ConfigLoadError
                elif corruption is not None:
                    expected = HoldoutLogError
                else:
                    expected = RuntimeError
                with pytest.raises(expected):
                    evaluate()
                after = log_path.read_bytes() if log_path.exists() else None
                assert after == before
                continue

            ev = evaluate()
            after = log_path.read_bytes()
            prefix = before or b""
            assert after.startswith(prefix)
            added = after[len(prefix) :]
            assert added.endswith(b"\n")
            assert added.count(b"\n") == 1

            # The model's Holdout_Period and overlap count.
            held = math.ceil(Fraction(repr(fraction)) * len(ALL_SESSIONS))
            holdout = ALL_SESSIONS[-held:]
            overlapping = sum(1 for a, b in spans_so_far if any(a <= d <= b for d in holdout))
            assert ev.overlapping_entries == overlapping

            entry = json.loads(added)
            assert entry["config_hash"] == config_hash(cfg)
            assert entry["evaluated_at"] == STARTED + i
            assert entry["holdout"] == {
                "first": holdout[0].isoformat(),
                "last": holdout[-1].isoformat(),
            }
            plan = planned(config_hash(cfg), holdout, failures=False)
            assert plan is not None
            assert entry["trade_count"] == plan["trade_count"]
            for key in ("expectancy_r", "win_rate_pct", "pass_probability"):
                assert key in entry
            assert entry["pass_probability"] == str(Fraction(plan["passed"], 1_000))

            manifest = json.loads((root / f"run-{i}" / MANIFEST_FILE_NAME).read_text())
            warnings = [w for w in manifest["warnings"] if "no longer unseen" in w]
            if overlapping:
                assert len(warnings) == 1
                assert f": {overlapping} earlier Holdout_Log" in warnings[0]
                assert warnings[0] in stderr.getvalue()
            else:
                assert warnings == []
                assert "no longer unseen" not in stderr.getvalue()
            spans_so_far.append((holdout[0], holdout[-1]))
