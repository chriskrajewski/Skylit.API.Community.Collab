"""Unit tests for the Run_Manifest (design "Run_Manifest", Req 18.3, 18.4, 20.12, 22.3).

**Validates: Requirements 18.3, 18.4, 20.12**
"""

from __future__ import annotations

import io
import json
import subprocess
from collections.abc import Callable, Iterator, Sequence
from datetime import date
from pathlib import Path

import pytest

from fse.analytics.bootstrap import bootstrap_intervals
from fse.analytics.metrics import MetricsCfg
from fse.backtest.manifest import (
    CODE_VERSION_UNKNOWN,
    GIT_DESCRIBE_ARGV,
    MANIFEST_FILE_NAME,
    DataRange,
    ManifestRecorder,
    RunSpec,
    SkippedSession,
    describe_code_version,
    run_manifest,
)
from fse.data.path_guard import PathGuardError
from fse.experiments.holdout import HoldoutPeriod
from fse.logio import REDACTED, LogWriter, Redactor
from fse.logio.canonical_json import ny_iso

D1, D2, D3 = date(2026, 3, 4), date(2026, 3, 5), date(2026, 3, 6)
STARTED, ENDED = 1_772_721_000_000_000_000, 1_772_721_060_000_000_000
SECRET = "fake-manifest-secret-0000"
SPEC = RunSpec(
    run_id="20260305T143000Z-abc123",
    kind="backtest",
    config_hash="0" * 64,
    data_range=DataRange(D1, D3),
    seed=42,
    config_path="configs/playbook_baseline.yaml",
)


def clock() -> Callable[[], int]:
    """The start Instant, then the end Instant."""
    times: Iterator[int] = iter((STARTED, ENDED))
    return lambda: next(times)


def writer(stderr: io.StringIO | None = None) -> LogWriter:
    return LogWriter(Redactor([SECRET]), stderr=stderr)


def read_manifest(out_dir: Path) -> dict[str, object]:
    loaded = json.loads((out_dir / MANIFEST_FILE_NAME).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


class FakeRunner:
    """Records the argv and directory; answers with a fixed result or raises."""

    def __init__(self, result: subprocess.CompletedProcess[bytes] | BaseException) -> None:
        self.result = result
        self.calls: list[tuple[list[str], Path]] = []

    def __call__(self, argv: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[bytes]:
        self.calls.append((list(argv), cwd))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def completed(returncode: int, stdout: bytes) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(list(GIT_DESCRIBE_ARGV), returncode, stdout, b"")


# ---------------------------------------------------------------- code version


def test_code_version_runs_git_describe_in_the_project_folder(tmp_path: Path) -> None:
    runner = FakeRunner(completed(0, b"a04a31b-dirty\n"))
    assert describe_code_version(tmp_path, run=runner) == "a04a31b-dirty"
    assert runner.calls == [(["git", "describe", "--always", "--dirty"], tmp_path)]


@pytest.mark.parametrize(
    "result",
    [
        completed(128, b""),
        completed(0, b""),
        completed(0, b"a04a31b\nsecond line\n"),
        FileNotFoundError("git"),
        subprocess.TimeoutExpired(list(GIT_DESCRIBE_ARGV), 30.0),
    ],
    ids=["non-zero exit", "no output", "two lines", "no git", "timeout"],
)
def test_code_version_falls_back_and_never_raises(
    tmp_path: Path, result: subprocess.CompletedProcess[bytes] | BaseException
) -> None:
    assert describe_code_version(tmp_path, run=FakeRunner(result)) == CODE_VERSION_UNKNOWN


def test_code_version_outside_a_git_repository_is_unknown(tmp_path: Path) -> None:
    assert describe_code_version(tmp_path) == CODE_VERSION_UNKNOWN


# ---------------------------------------------------------------- the manifest


def test_completed_run_writes_every_field(tmp_path: Path) -> None:
    out_dir = tmp_path / "run"
    intervals = bootstrap_intervals((), MetricsCfg(), seed=42, resamples=1_000)
    holdout = HoldoutPeriod((D3,), 0.2, 5)
    spec = RunSpec(
        run_id=SPEC.run_id,
        kind="sweep",
        config_hash=SPEC.config_hash,
        data_range=SPEC.data_range,
        seed=SPEC.seed,
        holdout=holdout,
    )
    with run_manifest(
        spec, writer=writer(), out_dir=out_dir, clock=clock(), code_version="a04a31b"
    ) as rec:
        rec.evaluated(D1)
        rec.skipped(D2, ["bars", "snapshots"], ["SPX/gamma", "MES"])
        rec.output(rec.out_dir / "decision_log.jsonl")
        rec.output("trades.csv")
        rec.output(rec.out_dir / "trades.csv")
        rec.warn("2 earlier holdout evaluations overlap this period")
        rec.record_bootstrap(intervals)
        before_exit = rec.manifest

    assert before_exit is None
    assert read_manifest(out_dir) == {
        "run_id": SPEC.run_id,
        "kind": "sweep",
        "config_hash": "0" * 64,
        "config_path": None,
        "code_version": "a04a31b",
        "data_range": {"start": "2026-03-04", "end": "2026-03-06"},
        "sessions_evaluated": ["2026-03-04"],
        "sessions_skipped": [
            {"date": "2026-03-05", "missing": ["snapshots", "bars"], "names": ["SPX/gamma", "MES"]}
        ],
        "seed": 42,
        "bootstrap": {"seed": 42, "resamples": 1_000},
        "holdout": {"first": "2026-03-06", "last": "2026-03-06"},
        "outputs": ["decision_log.jsonl", "trades.csv"],
        "status": "completed",
        "error": None,
        "warnings": ["2 earlier holdout evaluations overlap this period"],
        "started_at": STARTED,
        "started_at_ny": ny_iso(STARTED),
        "ended_at": ENDED,
        "ended_at_ny": ny_iso(ENDED),
    }
    assert rec.manifest is not None
    assert rec.manifest.to_json() == read_manifest(out_dir)


def evaluate_then_fail(rec: ManifestRecorder) -> None:
    rec.evaluated(D1)
    raise RuntimeError(f"engine failed near {SECRET}")


def test_error_aborts_the_run_and_still_writes_the_redacted_manifest(tmp_path: Path) -> None:
    out_dir = tmp_path / "run"
    with (
        pytest.raises(RuntimeError, match="engine failed"),
        run_manifest(
            SPEC, writer=writer(), out_dir=out_dir, clock=clock(), code_version="x"
        ) as rec,
    ):
        evaluate_then_fail(rec)

    manifest = read_manifest(out_dir)
    assert manifest["status"] == "aborted"
    assert manifest["error"] == f"RuntimeError: engine failed near {REDACTED}"
    assert manifest["sessions_evaluated"] == ["2026-03-04"]
    assert manifest["seed"] == 42
    assert manifest["config_path"] == "configs/playbook_baseline.yaml"
    assert SECRET not in (out_dir / MANIFEST_FILE_NAME).read_text(encoding="utf-8")
    assert rec.manifest is not None
    assert rec.manifest.status == "aborted"


def test_interrupt_aborts_the_run(tmp_path: Path) -> None:
    out_dir = tmp_path / "run"
    with (
        pytest.raises(KeyboardInterrupt),
        run_manifest(SPEC, writer=writer(), out_dir=out_dir, clock=clock(), code_version="x"),
    ):
        raise KeyboardInterrupt
    manifest = read_manifest(out_dir)
    assert (manifest["status"], manifest["error"]) == ("aborted", "KeyboardInterrupt")


def test_default_code_version_comes_from_git_describe(tmp_path: Path) -> None:
    with run_manifest(SPEC, writer=writer(), out_dir=tmp_path / "run", clock=clock()) as rec:
        pass
    version = read_manifest(tmp_path / "run")["code_version"]
    assert isinstance(version, str)
    assert version
    assert version == rec.code_version


def test_out_dir_inside_a_working_tree_is_rejected_before_any_write(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    out_dir = repo / "runs"
    with (
        pytest.raises(PathGuardError),
        run_manifest(SPEC, writer=writer(), out_dir=out_dir, clock=clock(), code_version="x"),
    ):
        pytest.fail("the block must not run")  # pragma: no cover
    assert not out_dir.exists()


def test_write_failure_after_an_error_keeps_the_error(tmp_path: Path) -> None:
    out_dir = tmp_path / "run"
    (out_dir / MANIFEST_FILE_NAME).mkdir(parents=True)  # os.replace onto a directory fails
    stderr = io.StringIO()
    with (
        pytest.raises(RuntimeError, match="engine failed") as caught,
        run_manifest(SPEC, writer=writer(stderr), out_dir=out_dir, clock=clock(), code_version="x"),
    ):
        raise RuntimeError("engine failed")
    assert "could not be written" in stderr.getvalue()
    assert any("could not be written" in note for note in caught.value.__notes__)


def test_write_failure_after_a_normal_exit_is_raised(tmp_path: Path) -> None:
    out_dir = tmp_path / "run"
    (out_dir / MANIFEST_FILE_NAME).mkdir(parents=True)
    with (
        pytest.raises(IsADirectoryError),
        run_manifest(SPEC, writer=writer(), out_dir=out_dir, clock=clock(), code_version="x"),
    ):
        pass


# ---------------------------------------------------------------- validation


def test_each_session_is_recorded_once_inside_the_range(tmp_path: Path) -> None:
    with run_manifest(
        SPEC, writer=writer(), out_dir=tmp_path / "run", clock=clock(), code_version="x"
    ) as rec:
        rec.evaluated(D1)
        with pytest.raises(ValueError, match="already recorded"):
            rec.skipped(D1, ["bars"])
        with pytest.raises(ValueError, match="outside"):
            rec.evaluated(date(2026, 3, 9))
    assert read_manifest(tmp_path / "run")["sessions_skipped"] == []


def test_records_validate_their_values() -> None:
    with pytest.raises(ValueError, match="ends"):
        DataRange(D3, D1)
    with pytest.raises(ValueError, match="missing"):
        SkippedSession(D1, ())
    with pytest.raises(ValueError, match="missing"):
        SkippedSession(D1, ("quotes",))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="seed"):
        RunSpec("r", "backtest", "h", DataRange(D1, D1), seed=-1)
    with pytest.raises(ValueError, match="run_id"):
        RunSpec(" ", "backtest", "h", DataRange(D1, D1), seed=None)
    skipped = SkippedSession(D1, ("bars", "snapshots", "bars"), ("MES", "MES"))
    assert (skipped.missing, skipped.names) == (("snapshots", "bars"), ("MES",))
