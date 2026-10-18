"""Unit tests for the order of a holdout evaluation's writes (``run_holdout_evaluation``).

The Holdout_Log entry is appended only after the Run_Manifest is written as
``completed``: a failure at any earlier point, the manifest write included,
leaves the log byte-identical, so no entry sits beside an aborted manifest.

**Validates: Requirements 22.5, 22.6**
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from fse.backtest.manifest import MANIFEST_FILE_NAME
from fse.config.printer import dump
from fse.experiments.holdout import HOLDOUT_RESULT_FILE_NAME, run_holdout_evaluation
from fse.experiments.runner import ConfigResult, EvalTask
from fse.logio import LogWriter, Redactor
from fse.logio.log_writer import PathLike
from tests.strategies.backtest_inputs import STARTED, write_calendars
from tests.strategies.experiment_inputs import (
    ALL_SESSIONS,
    LightSession,
    light_config,
    reliable_evaluator,
    write_light_cache,
)

EARLIER = b'{"config_hash": "ab", "evaluated_at": 1, "holdout": {"first": "2026-02-02", "last": "2026-02-03"}}\n'  # noqa: E501


class _ManifestWriteFails(LogWriter):
    """A Log_Writer whose Run_Manifest write fails (a full disk, say)."""

    def write_json(self, path: PathLike, obj: object) -> Path:
        if Path(path).name == MANIFEST_FILE_NAME:
            raise OSError("no space left on device")
        return super().write_json(path, obj)


@pytest.fixture(scope="module")
def dirs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("holdout-evaluation")
    cache = write_light_cache(root / "cache", [LightSession(d) for d in ALL_SESSIONS])
    return cache, write_calendars(root / "calendars")


def _evaluate(
    tmp_path: Path,
    dirs: tuple[Path, Path],
    writer: LogWriter,
    evaluator: Callable[[EvalTask, LogWriter], ConfigResult] = reliable_evaluator,
) -> None:
    cache_dir, calendar_dir = dirs
    config_path = tmp_path / "config.yaml"
    config_path.write_text(dump(light_config(montecarlo={"paths": 1_000})), encoding="utf-8")
    log_path = tmp_path / "holdout_log.jsonl"
    log_path.write_bytes(EARLIER)
    run_holdout_evaluation(
        config_path, log_path=log_path, cache_dir=cache_dir, calendar_dir=calendar_dir,
        out_dir=tmp_path / "run", writer=writer, seed=7,
        evaluator=evaluator, clock=lambda: STARTED, code_version="test-version",
    )  # fmt: skip


def _writer(kind: type[LogWriter] = LogWriter) -> LogWriter:
    return kind(Redactor(), stdout=io.StringIO(), stderr=io.StringIO())


def test_a_completed_evaluation_appends_after_the_completed_manifest(
    tmp_path: Path, dirs: tuple[Path, Path]
) -> None:
    manifest_path = tmp_path / "run" / MANIFEST_FILE_NAME
    log_path = tmp_path / "holdout_log.jsonl"
    at_append: list[str | None] = []  # the manifest status when the entry is appended

    class _Watch(LogWriter):
        def append_json(self, path: PathLike, obj: object, *, fsync: bool = False) -> None:
            if Path(path) == log_path:
                found = manifest_path.exists()
                at_append.append(
                    json.loads(manifest_path.read_bytes())["status"] if found else None
                )
            super().append_json(path, obj, fsync=fsync)

    _evaluate(tmp_path, dirs, _writer(_Watch))
    assert at_append == ["completed"]
    after = log_path.read_bytes()
    assert after.startswith(EARLIER)
    assert after[len(EARLIER) :].count(b"\n") == 1


def test_a_failed_manifest_write_leaves_the_holdout_log_byte_identical(
    tmp_path: Path, dirs: tuple[Path, Path]
) -> None:
    with pytest.raises(OSError, match="no space left"):
        _evaluate(tmp_path, dirs, _writer(_ManifestWriteFails))
    assert (tmp_path / "holdout_log.jsonl").read_bytes() == EARLIER


def test_a_failed_result_write_leaves_the_holdout_log_byte_identical(
    tmp_path: Path, dirs: tuple[Path, Path]
) -> None:
    def evaluator(task: EvalTask, writer: LogWriter) -> ConfigResult:
        result = reliable_evaluator(task, writer)
        (task.out_dir / HOLDOUT_RESULT_FILE_NAME).mkdir()  # the summary write then fails
        return result

    with pytest.raises(IsADirectoryError):
        _evaluate(tmp_path, dirs, _writer(), evaluator)
    assert (tmp_path / "holdout_log.jsonl").read_bytes() == EARLIER
    manifest = json.loads((tmp_path / "run" / MANIFEST_FILE_NAME).read_text(encoding="utf-8"))
    assert manifest["status"] == "aborted"
