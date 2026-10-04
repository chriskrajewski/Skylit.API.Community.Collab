"""The Run_Manifest: one record per run, written in ``finally`` (design "Run_Manifest", Req 18.4).

A run wraps its work in :func:`run_manifest`, which yields a
:class:`ManifestRecorder` and writes ``run_manifest.json`` in the run directory
from a ``finally`` block, so a run that completes, fails or is interrupted
still leaves one manifest (Req 18.4, design "Error Handling" 4)::

    with run_manifest(spec, writer=writer, out_dir=out_dir) as rec:
        for session in sessions:
            ...
            rec.evaluated(session)          # or rec.skipped(session, ["bars"], ["MES"])
        rec.output(out_dir / DECISION_LOG_FILE_NAME)
        rec.record_bootstrap(intervals)
    manifest = rec.manifest

Fields (design "Run_Manifest", Req 18.3, 18.4, 20.12, 22.3): ``run_id``,
``kind``, ``config_hash``, ``config_path``, ``code_version``, ``data_range``
(``{start, end}``), ``sessions_evaluated``, ``sessions_skipped``
(``[{date, missing, names}]``, ``missing`` from ``snapshots`` and ``bars``),
``seed``, ``bootstrap`` (``{seed, resamples}`` or ``null``), ``holdout``
(``{first, last}`` or ``null``), ``outputs`` (paths inside the run directory
are relative to it), ``status`` (``completed`` or ``aborted``), ``error`` (the
redacted error of an aborted run, else ``null``), ``warnings``,
``started_at`` and ``ended_at`` (Instants, each with an ``_ny`` string).

The status is ``aborted`` for any exception leaving the block, interrupts
included; the exception always propagates. The file goes through the
Log_Writer: canonical JSON, redacted, temp file, fsync and rename (Req 1.9).
Before the block runs, the path guard checks the run directory
(``PathGuardError``, exit 2, nothing written; Req 1.11).

``code_version`` is ``git describe --always --dirty`` run in the Project
folder (:func:`describe_code_version`), or ``unknown`` when git cannot answer.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Final, Literal

from fse.analytics.bootstrap import BootstrapIntervals
from fse.data.path_guard import check_output_dir
from fse.experiments.holdout import HoldoutPeriod
from fse.logio import LogWriter
from fse.logio.canonical_json import JsonValue, instant_fields
from fse.settings import project_dir
from fse.timekit import Instant

__all__ = [
    "CODE_VERSION_UNKNOWN",
    "GIT_DESCRIBE_ARGV",
    "MANIFEST_FILE_NAME",
    "MISSING_DATA",
    "OUTPUT_DIR_LABEL",
    "BootstrapRecord",
    "CommandRunner",
    "DataRange",
    "ManifestRecorder",
    "MissingData",
    "RunManifest",
    "RunSpec",
    "RunStatus",
    "SkippedSession",
    "describe_code_version",
    "run_manifest",
]

MANIFEST_FILE_NAME: Final = "run_manifest.json"
OUTPUT_DIR_LABEL: Final = "run output directory"

CODE_VERSION_UNKNOWN: Final = "unknown"
"""The ``code_version`` recorded when ``git describe`` cannot answer."""

GIT_DESCRIBE_ARGV: Final = ("git", "describe", "--always", "--dirty")

type RunStatus = Literal["completed", "aborted"]
type MissingData = Literal["snapshots", "bars"]

MISSING_DATA: Final[tuple[MissingData, ...]] = ("snapshots", "bars")
"""The missing data types of a skipped session (Req 18.3), in recorded order."""

type CommandRunner = Callable[[Sequence[str], Path], subprocess.CompletedProcess[bytes]]
"""Runs an argv (no shell) in a directory; injected in tests."""

_GIT_TIMEOUT_S: Final = 30.0
_VERSION_MAX_LEN: Final = 200

# Variables that override git's repository discovery (as in fse.data.path_guard):
# a git hook running the suite sets GIT_DIR, for example. git runs without them.
_GIT_DISCOVERY_VARS: Final = frozenset(
    {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_CEILING_DIRECTORIES"}
)


# ---------------------------------------------------------------- code version


def _run_command(argv: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[bytes]:
    env = {k: v for k, v in os.environ.items() if k not in _GIT_DISCOVERY_VARS}
    return subprocess.run(
        list(argv),
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=_GIT_TIMEOUT_S,
    )


def describe_code_version(project: Path | None = None, *, run: CommandRunner = _run_command) -> str:
    """``git describe --always --dirty`` in ``project`` (default: the Project folder).

    Never raises: with no Project folder, when git cannot be run, exits non-zero
    or prints anything but one short line, the result is :data:`CODE_VERSION_UNKNOWN`.
    """
    folder = project_dir() if project is None else project
    if folder is None:
        return CODE_VERSION_UNKNOWN
    try:
        result = run(GIT_DESCRIBE_ARGV, folder)
        if result.returncode != 0:
            return CODE_VERSION_UNKNOWN
        text = os.fsdecode(result.stdout).strip()
    except Exception:  # an absent git, a timeout or a broken runner: never fail a run here
        return CODE_VERSION_UNKNOWN
    if not text or len(text) > _VERSION_MAX_LEN or not text.isprintable() or " " in text:
        return CODE_VERSION_UNKNOWN
    return text


# ---------------------------------------------------------------- records


def _require_date(name: str, value: object) -> None:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise ValueError(f"{name} must be a date (not a datetime), got {value!r}")


def _require_text(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")


def _require_count(name: str, value: object, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be a whole number of at least {minimum}, got {value!r}")


@dataclass(frozen=True, slots=True)
class DataRange:
    """The run's inclusive session date range."""

    start: date
    end: date

    def __post_init__(self) -> None:
        _require_date("DataRange.start", self.start)
        _require_date("DataRange.end", self.end)
        if self.end < self.start:
            raise ValueError(f"the data range ends ({self.end}) before it starts ({self.start})")

    def __contains__(self, d: object) -> bool:
        return isinstance(d, date) and not isinstance(d, datetime) and self.start <= d <= self.end

    def to_json(self) -> dict[str, JsonValue]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}


@dataclass(frozen=True, slots=True)
class SkippedSession:
    """A session skipped for missing data (Req 18.3).

    ``missing`` holds the missing data types, in :data:`MISSING_DATA` order;
    ``names`` the configured ``SYMBOL/metric`` pairs or instruments without data.
    """

    session: date
    missing: tuple[MissingData, ...]
    names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_date("SkippedSession.session", self.session)
        kinds = set(self.missing)
        if not kinds or not kinds <= set(MISSING_DATA):
            raise ValueError(
                f"SkippedSession.missing must name at least one of {list(MISSING_DATA)}, "
                f"got {list(self.missing)!r}"
            )
        for name in self.names:
            _require_text("SkippedSession.names entry", name)
        object.__setattr__(self, "missing", tuple(m for m in MISSING_DATA if m in kinds))
        object.__setattr__(self, "names", tuple(dict.fromkeys(self.names)))

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "date": self.session.isoformat(),
            "missing": list(self.missing),
            "names": list(self.names),
        }


@dataclass(frozen=True, slots=True)
class BootstrapRecord:
    """The seed and resample count the bootstrap intervals were drawn with (Req 20.12)."""

    seed: int
    resamples: int

    def __post_init__(self) -> None:
        _require_count("BootstrapRecord.seed", self.seed, 0)
        _require_count("BootstrapRecord.resamples", self.resamples, 1)

    @classmethod
    def of(cls, intervals: BootstrapIntervals) -> BootstrapRecord:
        return cls(intervals.seed, intervals.resamples)

    def to_json(self) -> dict[str, JsonValue]:
        return {"seed": self.seed, "resamples": self.resamples}


@dataclass(frozen=True, slots=True)
class RunSpec:
    """What a run knows when it starts.

    ``kind`` names the run (``backtest``, ``replay``, an experiment kind).
    ``seed`` is the run's random seed, ``None`` when the run uses none.
    ``holdout`` is the Holdout_Period an experiment excluded (Req 22.3).
    """

    run_id: str
    kind: str
    config_hash: str
    data_range: DataRange
    seed: int | None
    config_path: str | None = None
    holdout: HoldoutPeriod | None = None

    def __post_init__(self) -> None:
        _require_text("RunSpec.run_id", self.run_id)
        _require_text("RunSpec.kind", self.kind)
        _require_text("RunSpec.config_hash", self.config_hash)
        if self.seed is not None:
            _require_count("RunSpec.seed", self.seed, 0)
        if self.config_path is not None:
            _require_text("RunSpec.config_path", self.config_path)


@dataclass(frozen=True, slots=True)
class RunManifest:
    """One run's Run_Manifest; :meth:`to_json` is the file's content."""

    spec: RunSpec
    code_version: str
    sessions_evaluated: tuple[date, ...]
    sessions_skipped: tuple[SkippedSession, ...]
    bootstrap: BootstrapRecord | None
    outputs: tuple[str, ...]
    warnings: tuple[str, ...]
    status: RunStatus
    error: str | None
    started_at: Instant
    ended_at: Instant

    def to_json(self) -> dict[str, JsonValue]:
        s = self.spec
        return {
            "run_id": s.run_id,
            "kind": s.kind,
            "config_hash": s.config_hash,
            "config_path": s.config_path,
            "code_version": self.code_version,
            "data_range": s.data_range.to_json(),
            "sessions_evaluated": [d.isoformat() for d in self.sessions_evaluated],
            "sessions_skipped": [k.to_json() for k in self.sessions_skipped],
            "seed": s.seed,
            "bootstrap": None if self.bootstrap is None else self.bootstrap.to_json(),
            "holdout": None if s.holdout is None else s.holdout.to_json(),
            "outputs": list(self.outputs),
            "status": self.status,
            "error": self.error,
            "warnings": list(self.warnings),
            **instant_fields("started_at", self.started_at),
            **instant_fields("ended_at", self.ended_at),
        }


# ---------------------------------------------------------------- the recorder


class ManifestRecorder:
    """Collects what a run did; :func:`run_manifest` writes it on exit.

    Each session is recorded once, as evaluated or skipped, and must lie in the
    data range. Outputs are kept once each, in call order.
    """

    __slots__ = (
        "_bootstrap",
        "_code_version",
        "_evaluated",
        "_manifest",
        "_out_dir",
        "_outputs",
        "_seen",
        "_skipped",
        "_spec",
        "_started_at",
        "_warnings",
    )

    def __init__(self, spec: RunSpec, code_version: str, started_at: Instant, out_dir: Path):
        self._spec = spec
        self._code_version = code_version
        self._started_at = started_at
        self._out_dir = out_dir
        self._evaluated: list[date] = []
        self._skipped: list[SkippedSession] = []
        self._seen: set[date] = set()
        self._outputs: dict[str, None] = {}
        self._warnings: list[str] = []
        self._bootstrap: BootstrapRecord | None = None
        self._manifest: RunManifest | None = None

    @property
    def spec(self) -> RunSpec:
        return self._spec

    @property
    def out_dir(self) -> Path:
        """The run directory, as the path guard resolved it."""
        return self._out_dir

    @property
    def code_version(self) -> str:
        return self._code_version

    @property
    def started_at(self) -> Instant:
        return self._started_at

    @property
    def manifest(self) -> RunManifest | None:
        """The manifest written on exit; ``None`` until then."""
        return self._manifest

    def evaluated(self, session: date) -> None:
        """Record ``session`` as evaluated."""
        self._claim(session)
        self._evaluated.append(session)

    def skipped(
        self, session: date, missing: Iterable[MissingData], names: Iterable[str] = ()
    ) -> None:
        """Record ``session`` as skipped for the ``missing`` data types (Req 18.3)."""
        record = SkippedSession(session, tuple(missing), tuple(names))
        self._claim(session)
        self._skipped.append(record)

    def output(self, path: str | os.PathLike[str]) -> None:
        """Record an output file; a path inside the run directory is kept relative to it."""
        p = Path(path)
        if p.is_absolute():
            resolved = p.resolve()
            p = resolved.relative_to(self._out_dir) if resolved.is_relative_to(self._out_dir) else p
        self._outputs.setdefault(p.as_posix(), None)

    def warn(self, message: str) -> None:
        """Record a warning (for example the Holdout_Log overlap, design §22)."""
        _require_text("a warning", message)
        self._warnings.append(message)

    def record_bootstrap(self, intervals: BootstrapIntervals) -> None:
        """Record the bootstrap seed and resample count (Req 20.12)."""
        self._bootstrap = BootstrapRecord.of(intervals)

    def _claim(self, session: date) -> None:
        _require_date("session", session)
        if session not in self._spec.data_range:
            raise ValueError(f"session {session} is outside the run's data range")
        if session in self._seen:
            raise ValueError(f"session {session} is already recorded")
        self._seen.add(session)

    def _build(self, status: RunStatus, error: str | None, ended_at: Instant) -> RunManifest:
        return RunManifest(
            spec=self._spec,
            code_version=self._code_version,
            sessions_evaluated=tuple(self._evaluated),
            sessions_skipped=tuple(self._skipped),
            bootstrap=self._bootstrap,
            outputs=tuple(self._outputs),
            warnings=tuple(self._warnings),
            status=status,
            error=error,
            started_at=self._started_at,
            ended_at=ended_at,
        )

    def __repr__(self) -> str:
        return f"ManifestRecorder({self._spec.run_id!r})"


# ---------------------------------------------------------------- the context manager


@contextmanager
def run_manifest(
    spec: RunSpec,
    *,
    writer: LogWriter,
    out_dir: str | Path,
    clock: Callable[[], Instant] = time.time_ns,
    code_version: str | None = None,
) -> Iterator[ManifestRecorder]:
    """Yield a :class:`ManifestRecorder`; write ``run_manifest.json`` in ``finally``.

    ``out_dir`` passes the path guard first (``PathGuardError``; nothing is
    written). ``code_version`` defaults to :func:`describe_code_version`. On a
    normal exit the status is ``completed``; on any exception it is
    ``aborted`` with the redacted error, and the exception propagates. If the
    manifest cannot be written while an exception is in flight, the write
    error is printed to stderr and added to that exception as a note; after a
    normal exit the write error is raised.
    """
    target = check_output_dir(out_dir, label=OUTPUT_DIR_LABEL)
    version = describe_code_version() if code_version is None else code_version
    recorder = ManifestRecorder(spec, version, clock(), target)
    pending: BaseException | None = None
    try:
        yield recorder
    except BaseException as exc:
        pending = exc
        raise
    finally:
        _write_on_exit(recorder, pending, writer, target, clock)


def _describe(exc: BaseException) -> str:
    text = str(exc)
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _write_on_exit(
    recorder: ManifestRecorder,
    pending: BaseException | None,
    writer: LogWriter,
    out_dir: Path,
    clock: Callable[[], Instant],
) -> None:
    status: RunStatus = "completed" if pending is None else "aborted"
    error = None if pending is None else writer.redact(_describe(pending))
    try:
        manifest = recorder._build(status, error, clock())
        writer.write_json(out_dir / MANIFEST_FILE_NAME, manifest.to_json())
    except Exception as exc:
        if pending is None:
            raise
        message = (
            f"the Run_Manifest of run {recorder.spec.run_id} could not be written: {_describe(exc)}"
        )
        writer.error(f"error: {message}")
        pending.add_note(writer.redact(message))
        return
    recorder._manifest = manifest
