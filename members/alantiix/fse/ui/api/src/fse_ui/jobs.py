"""Jobs: one command run, its status, exit code and log, from start to finish.

:class:`JobManager` stores jobs in SQLite, keeps each log in
``<data>/ui/jobs/<id>.log`` plus a ring buffer for live viewers, and runs the
argv through an :class:`Executor` (``LocalExecutor``: a child process with an
argv list, never a shell; ``EcsExecutor`` in ``fse_ui.aws.ecs``: a Fargate task).

Remote executors (``survives_restart``) outlive the console: on shutdown their
jobs are detached, not cancelled, and :meth:`JobManager.resume_remote`
re-attaches them at the next start.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import secrets
import signal
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Literal, Protocol, runtime_checkable

from fse_ui.db import Db

log = logging.getLogger(__name__)

type JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled", "interrupted"]

TERMINAL: frozenset[str] = frozenset({"succeeded", "failed", "cancelled", "interrupted"})
RING_LINES = 2000
CANCEL_GRACE_S = 30.0
READ_LIMIT = 1 << 20
FIND_RETRY_AGE_S = 120.0
"""A row younger than this with no task found is looked up once more (ListTasks lags)."""
FIND_RETRY_WAIT_S = 30.0

EXIT_MEANINGS: dict[int, str] = {
    0: "ok",
    1: "failure",
    2: "invalid input",
    3: "failure",
    4: "data or I/O failure",
    75: "the pull lock is held",
    76: "the EFS cache is not mounted",
    130: "interrupted",
}
# A code means one thing for the command that returned it. An unused code, or a
# command with no row here, stays on EXIT_MEANINGS. It never borrows another
# command's sentence (scan-secrets 1 is not backtest-plugin 1).
COMMAND_EXIT_MEANINGS: dict[str, dict[int, str]] = {
    "scan-secrets": {1: "secrets found", 5: "the scan could not run"},
    "backtest-plugin": {3: "needed bars or other inputs are missing"},
    "pull": {3: "credentials missing or blank"},
    "run": {3: "credentials missing or blank"},
    "paper": {1: "the feed stopped", 3: "credentials missing or blank"},
    "account": {3: "the runner is already running"},
}


def log_line_limit(
    lines: int | None = None, limit: int | None = None, tail: int | None = None
) -> int:
    """How many log lines to return. ``lines``, ``limit`` and ``tail`` are the same cap.

    The first one that is set wins. Default 2000. Clamped to 1..20000.
    """
    asked = next((n for n in (lines, limit, tail) if n is not None), RING_LINES)
    return min(max(asked, 1), 20000)


def exit_meaning(code: int | None, command: Sequence[str] | None = None) -> str | None:
    """What an exit code means for this command.

    Each command owns its codes. ``backtest-plugin`` 1 is a failure and 3 means
    a needed bar or other input is missing. ``scan-secrets`` 1 means secrets
    were found. ``run``, ``paper`` and ``pull`` use 3 for a blank credential or
    a refused login. ``account`` 3 means the runner is already running. A code
    with no row for this command uses the generic sentence, never another
    command's.
    """
    if code is None:
        return None
    if code < 0:
        return f"killed by signal {-code}"
    name = "" if not command else str(command[0])
    specific = COMMAND_EXIT_MEANINGS.get(name, {})
    if code in specific:
        return specific[code]
    return EXIT_MEANINGS.get(code, "failure")


type LineSink = Callable[[str], None]
type Sleep = Callable[[float], Awaitable[None]]


class RemoteTaskGone(Exception):
    """The remote task is no longer known to its service: the job ends ``interrupted``.

    Raised by a remote executor's ``resume``. Defined here so this module never
    imports boto code.
    """


@dataclass(frozen=True, slots=True)
class JobRequest:
    program: str
    command: tuple[str, ...]
    argv: tuple[str, ...]
    placement: str
    title: str
    heavy: bool
    executor: str = "local"
    args: tuple[str, ...] = ()
    """The arguments after the program and command path (what a remote task runs)."""
    note: str | None = None
    """A line written at the top of the job log (a warmup clamp, for example)."""


@dataclass(frozen=True, slots=True)
class RunContext:
    """What an executor may know about and do to its job."""

    job_id: str
    request: JobRequest
    user: str
    on_line: LineSink
    set_external: Callable[[str | None, str | None], None]
    """``(external_id, cursor)``: persisted at once (re-attach after a restart)."""
    detaching: Callable[[], bool]
    """True once the console is shutting down and remote jobs are being detached."""


class Executor(Protocol):
    """Outcome contract of ``run`` (and a remote executor's ``resume``): an ``int`` is
    the exit code (0 → succeeded, else failed); ``None`` → failed with no exit code;
    :class:`RemoteTaskGone` → interrupted."""

    name: str
    survives_restart: bool

    async def run(
        self,
        job_id: str,
        argv: Sequence[str],
        env: Mapping[str, str],
        cwd: Path,
        on_line: LineSink,
        *,
        ctx: RunContext | None = None,
    ) -> int | None: ...

    async def cancel(self, job_id: str) -> bool: ...


@runtime_checkable
class RemoteExecutor(Executor, Protocol):
    async def resume(self, ctx: RunContext, external_id: str, cursor: str | None) -> int | None: ...

    async def find(self, job_id: str) -> str | None:
        """The task started for ``job_id`` (by its ``startedBy`` tag), or None."""
        ...

    async def abandon(
        self, job_id: str, request: JobRequest, on_line: LineSink | None = None
    ) -> None:
        """No task exists for the job: release what it holds (its S3 lock). Never raises."""
        ...


class LocalExecutor:
    """Runs argv as a child process in its own session; cancel = SIGINT, then SIGTERM."""

    name = "local"
    survives_restart = False

    def __init__(self, *, grace_s: float = CANCEL_GRACE_S) -> None:
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._grace = grace_s

    async def run(
        self,
        job_id: str,
        argv: Sequence[str],
        env: Mapping[str, str],
        cwd: Path,
        on_line: LineSink,
        *,
        ctx: RunContext | None = None,  # a child process needs nothing more
    ) -> int:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=dict(env),
                cwd=cwd,
                start_new_session=True,
                limit=READ_LIMIT,
            )
        except OSError as exc:
            on_line(f"error: cannot start {argv[0]}: {exc.strerror or type(exc).__name__}")
            return 127
        self._procs[job_id] = proc
        try:
            assert proc.stdout is not None
            while True:
                try:
                    raw = await proc.stdout.readline()
                except ValueError:  # a line over READ_LIMIT
                    raw = await proc.stdout.read(READ_LIMIT)
                if not raw:
                    break
                on_line(raw.decode("utf-8", "replace").rstrip("\n"))
            return await proc.wait()
        finally:
            self._procs.pop(job_id, None)

    async def cancel(self, job_id: str) -> bool:
        proc = self._procs.get(job_id)
        if proc is None or proc.returncode is not None:
            return False
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, sig)
            try:
                await asyncio.wait_for(asyncio.shield(proc.wait()), self._grace)
                return True
            except TimeoutError:
                continue
        return True


class _Slots:
    """A counting slot pool: ``acquire`` waits for room; ``take`` never waits (re-attach)."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._running = 0
        self._cond = asyncio.Condition()

    @property
    def running(self) -> int:
        return self._running

    async def acquire(self) -> None:
        async with self._cond:
            await self._cond.wait_for(lambda: self._running < self._limit)
            self._running += 1

    def take(self) -> None:
        self._running += 1

    async def release(self) -> None:
        async with self._cond:
            self._running -= 1
            self._cond.notify_all()

    async def __aenter__(self) -> None:
        await self.acquire()

    async def __aexit__(self, *exc: object) -> None:
        await self.release()


@dataclass(slots=True)
class _Live:
    lines: deque[str] = field(default_factory=lambda: deque(maxlen=RING_LINES))
    subscribers: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)
    cancel_requested: bool = False


def _row(r: Any) -> dict[str, Any]:
    command = json.loads(r["command"])
    return {
        "id": r["id"],
        "program": r["program"],
        "command": command,
        "argv": json.loads(r["argv"]),
        "placement": r["placement"],
        "executor": r["executor"],
        "status": r["status"],
        "exit_code": r["exit_code"],
        "exit_meaning": exit_meaning(r["exit_code"], command),
        "title": r["title"],
        "user": r["user"],
        "created_at": r["created_at"],
        "started_at": r["started_at"],
        "stopped_at": r["stopped_at"],
        "run_dir": r["run_dir"],
        "external_id": r["external_id"],
    }


def _task_id(external_id: str | None) -> str:
    return "(not known yet)" if not external_id else external_id.rsplit("/", 1)[-1]


class JobManager:
    def __init__(
        self,
        db: Db,
        *,
        log_dir: Path,
        runs_dir: Path,
        executors: Mapping[str, Executor],
        env: Callable[[], dict[str, str]],
        redact: Callable[[], Callable[[str], str]],
        cwd: Path,
        heavy_slots: int = 1,
        ecs_slots: int = 4,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._db = db
        self._log_dir = log_dir
        self._runs_dir = runs_dir
        self._executors = dict(executors)
        self._env = env
        self._redact = redact
        self._cwd = cwd
        self._heavy = asyncio.Semaphore(heavy_slots)
        self._ecs_slots = _Slots(ecs_slots)
        self._sleep = sleep
        self._detaching = False
        self._live: dict[str, _Live] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._on_finish: list[Callable[[dict[str, Any]], Awaitable[None] | None]] = []
        log_dir.mkdir(parents=True, exist_ok=True)

    def recover(self) -> int:
        """Jobs left queued or running by an earlier process: marked ``interrupted``."""
        return self._db.execute(
            "UPDATE jobs SET status = 'interrupted', stopped_at = ? "
            "WHERE status IN ('queued', 'running') AND executor = 'local'",
            (time.time(),),
        )

    def resume_remote(self) -> int:
        """Schedule a re-attach for every remote job left queued or running (no AWS call)."""
        rows = self._db.all(
            "SELECT id, executor FROM jobs "
            "WHERE status IN ('queued', 'running') AND executor != 'local'"
        )
        loop = asyncio.get_running_loop()
        scheduled = 0
        for r in rows:
            job_id = r["id"]
            executor = self._executors.get(r["executor"])
            if not isinstance(executor, RemoteExecutor) or job_id in self._tasks:
                # The console no longer runs that executor: nothing can re-attach.
                self._set(job_id, status="interrupted", stopped_at=time.time())
                continue
            self._live[job_id] = _Live()
            self._tasks[job_id] = loop.create_task(self._resume(job_id, executor))
            scheduled += 1
        return scheduled

    def on_finish(self, fn: Callable[[dict[str, Any]], Awaitable[None] | None]) -> None:
        self._on_finish.append(fn)

    # ------------------------------------------------------------ queries

    def get(self, job_id: str) -> dict[str, Any] | None:
        r = self._db.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        return None if r is None else _row(r)

    def list_jobs(self, *, limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self._db.all(
                "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            rows = self._db.all("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))
        return [_row(r) for r in rows]

    def active_with(self, predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any] | None:
        """The first queued or running job that matches ``predicate``."""
        rows = self._db.all(
            "SELECT * FROM jobs WHERE status IN ('queued', 'running') ORDER BY created_at"
        )
        return next((j for j in map(_row, rows) if predicate(j)), None)

    def log_path(self, job_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9]{12,40}", job_id):
            raise KeyError(job_id)
        return self._log_dir / f"{job_id}.log"

    def log_tail(self, job_id: str, *, lines: int = RING_LINES) -> list[str]:
        """The last ``lines`` of the job log.

        The live ring keeps ``RING_LINES``. A larger ``lines`` reads the log file,
        which keeps every line the ring has already dropped.
        """
        filed = self._file_tail(job_id, lines)
        live = self._live.get(job_id)
        if live is None:
            return filed
        ring = list(live.lines)[-lines:]
        if len(filed) > len(ring):
            return filed
        return ring

    def _file_tail(self, job_id: str, lines: int) -> list[str]:
        try:
            text = self.log_path(job_id).read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError, KeyError:
            return []
        return text.splitlines()[-lines:]

    def subscribe(self, job_id: str) -> asyncio.Queue[dict[str, Any]] | None:
        live = self._live.get(job_id)
        if live is None:
            return None
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=5000)
        live.subscribers.add(q)
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue[dict[str, Any]]) -> None:
        live = self._live.get(job_id)
        if live is not None:
            live.subscribers.discard(q)

    # ------------------------------------------------------------ lifecycle

    def submit(self, req: JobRequest, *, user: str) -> dict[str, Any]:
        if req.executor not in self._executors:
            raise ValueError(f"no executor {req.executor!r}")
        job_id = secrets.token_hex(8)
        self._db.execute(
            "INSERT INTO jobs (id, program, command, argv, placement, executor, status, title, "
            "user, created_at) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
            (
                job_id,
                req.program,
                json.dumps(list(req.command)),
                json.dumps(list(req.argv)),
                req.placement,
                req.executor,
                req.title,
                user,
                time.time(),
            ),
        )
        self._live[job_id] = _Live()
        self._tasks[job_id] = asyncio.get_running_loop().create_task(self._run(job_id, req, user))
        job = self.get(job_id)
        assert job is not None
        return job

    def record_rejection(
        self,
        *,
        program: str,
        command: tuple[str, ...],
        argv: tuple[str, ...],
        placement: str,
        title: str,
        user: str,
        message: str,
        executor: str = "local",
    ) -> dict[str, Any]:
        """A failed job that never starts a task.

        The log's first line is ``error: ...`` so a card phase shows this refusal
        instead of an older job. ``exit_code`` stays null.
        """
        job_id = secrets.token_hex(8)
        now = time.time()
        self._db.execute(
            "INSERT INTO jobs (id, program, command, argv, placement, executor, status, title, "
            "user, created_at, started_at, stopped_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'failed', ?, ?, ?, ?, ?)",
            (
                job_id,
                program,
                json.dumps(list(command)),
                json.dumps(list(argv)),
                placement,
                executor,
                title,
                user,
                now,
                now,
                now,
            ),
        )
        self._log_dir.mkdir(parents=True, exist_ok=True)
        text = message if message.startswith("error:") else f"error: {message}"
        path = self.log_path(job_id)
        path.write_text(text + "\n", encoding="utf-8")
        os.chmod(path, 0o600)
        job = self.get(job_id)
        assert job is not None
        return job

    async def wait(self, job_id: str) -> dict[str, Any]:
        task = self._tasks.get(job_id)
        if task is not None:
            await asyncio.shield(task)
        job = self.get(job_id)
        assert job is not None
        return job

    async def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None or job["status"] in TERMINAL:
            return False
        live = self._live.get(job_id)
        if live is not None:
            live.cancel_requested = True
        if job["status"] == "queued":
            # _run sees cancel_requested once it gets a slot and never starts the process.
            self._set(job_id, status="cancelled", stopped_at=time.time())
            return True
        executor = self._executors.get(job["executor"])
        return False if executor is None else await executor.cancel(job_id)

    def _publish(self, job_id: str, event: dict[str, Any]) -> None:
        live = self._live.get(job_id)
        if live is None:
            return
        for q in list(live.subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(event)

    def _set(self, job_id: str, **fields: Any) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._db.execute(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), job_id))  # noqa: S608
        job = self.get(job_id)
        if job is not None:
            self._publish(job_id, {"type": "status", "job": job})

    def _set_external(self, job_id: str, external_id: str | None, cursor: str | None) -> None:
        row = self._db.one("SELECT external_id FROM jobs WHERE id = ?", (job_id,))
        if row is not None and row["external_id"] != external_id:
            self._set(job_id, external_id=external_id, external_cursor=cursor)
            return
        self._db.execute("UPDATE jobs SET external_cursor = ? WHERE id = ?", (cursor, job_id))

    def _sink(self, job_id: str, live: _Live, fh: IO[str], found_dir: list[str]) -> LineSink:
        redact = self._redact()
        runs_re = re.compile(re.escape(str(self._runs_dir)) + r"/([A-Za-z0-9_.:+-]+)")

        def on_line(line: str) -> None:
            clean = redact(line)
            live.lines.append(clean)
            fh.write(clean + "\n")
            fh.flush()
            m = runs_re.search(clean)
            if m and not found_dir:
                found_dir.append(m.group(1))
            self._publish(job_id, {"type": "line", "line": clean})

        return on_line

    def _context(self, job_id: str, req: JobRequest, user: str, on_line: LineSink) -> RunContext:
        return RunContext(
            job_id=job_id,
            request=req,
            user=user,
            on_line=on_line,
            set_external=lambda eid, cursor: self._set_external(job_id, eid, cursor),
            detaching=lambda: self._detaching,
        )

    def _detach_line(self, job_id: str, on_line: LineSink) -> None:
        row = self._db.one("SELECT external_id FROM jobs WHERE id = ?", (job_id,))
        task = _task_id(None if row is None else row["external_id"])
        on_line(
            f"console stopped; ECS task {task} keeps running and is re-attached at the next start"
        )

    def _slot(self, req: JobRequest) -> Any:
        if req.executor == "local":
            return self._heavy if req.heavy else _NullLock()
        return self._ecs_slots if req.executor == "ecs" else _NullLock()

    async def _run(self, job_id: str, req: JobRequest, user: str) -> None:
        live = self._live[job_id]
        log_file = self.log_path(job_id)
        before = _run_dirs(self._runs_dir) if req.executor == "local" else set()
        found_dir: list[str] = []
        detached = False
        with log_file.open("a", encoding="utf-8") as fh:
            os.chmod(log_file, 0o600)
            on_line = self._sink(job_id, live, fh, found_dir)
            executor = self._executors[req.executor]
            started = False
            code: int | None = None
            try:
                async with self._slot(req):
                    if not live.cancel_requested:
                        started = True
                        self._set(job_id, status="running", started_at=time.time())
                        if req.note:
                            on_line(req.note)
                        on_line("$ " + " ".join(_quote(a) for a in req.argv))
                        code = await executor.run(
                            job_id,
                            req.argv,
                            self._env(),
                            self._cwd,
                            on_line,
                            ctx=self._context(job_id, req, user, on_line),
                        )
            except asyncio.CancelledError:
                if not (self._detaching and executor.survives_restart):
                    raise
                self._detach_line(job_id, on_line)
                detached = True
            except Exception as exc:  # executor failure: the job fails, the API stays up
                on_line(f"error: the executor failed: {type(exc).__name__}: {exc}")
            if not detached:
                self._finish(
                    job_id,
                    code,
                    started=started,
                    found_dir=found_dir,
                    live=live,
                    on_line=on_line,
                    before=before,
                    executor=req.executor,
                    command=req.command,
                )
        await self._close(job_id, finished=not detached)
        if detached:
            raise asyncio.CancelledError

    async def _resume(self, job_id: str, executor: RemoteExecutor) -> None:
        """Re-attach a remote job left running by an earlier console process."""
        r = self._db.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        assert r is not None
        req = JobRequest(
            program=r["program"],
            command=tuple(json.loads(r["command"])),
            argv=tuple(json.loads(r["argv"])),
            placement=r["placement"],
            title=r["title"],
            heavy=False,
            executor=r["executor"],
            args=(),
        )
        live = self._live[job_id]
        live.lines.extend(self._file_tail(job_id, RING_LINES))
        found_dir: list[str] = []
        detached = False
        log_file = self.log_path(job_id)
        with log_file.open("a", encoding="utf-8") as fh:
            os.chmod(log_file, 0o600)
            on_line = self._sink(job_id, live, fh, found_dir)
            ctx = self._context(job_id, req, r["user"], on_line)
            started = False
            code: int | None = None
            status: JobStatus | None = None
            self._ecs_slots.take()  # already running on Fargate: never waits for a slot
            try:
                external_id: str | None = r["external_id"]
                cursor: str | None = r["external_cursor"]
                if external_id is None:
                    external_id = await self._find(job_id, executor, float(r["created_at"]))
                    if external_id is not None:
                        cursor = None
                        ctx.set_external(external_id, None)
                if external_id is None:
                    on_line("no ECS task was started for this job (ListTasks found none)")
                    await executor.abandon(job_id, req, on_line)
                    # A queued row cancelled meanwhile stays cancelled.
                    status = "cancelled" if live.cancel_requested else "interrupted"
                else:
                    if r["status"] == "queued":
                        self._set(job_id, status="running", started_at=time.time())
                    on_line(f"console started; re-attached to ECS task {_task_id(external_id)}")
                    started = True
                    code = await executor.resume(ctx, external_id, cursor)
            except asyncio.CancelledError:
                if not self._detaching:
                    raise
                self._detach_line(job_id, on_line)
                detached = True
            except RemoteTaskGone:
                code, status = None, "interrupted"
            except Exception as exc:
                log.exception("cannot re-attach job %s", job_id)
                on_line(f"error: cannot re-attach: {type(exc).__name__}")
                code, status = None, "failed"
            finally:
                await self._ecs_slots.release()
            if not detached:
                self._finish(
                    job_id,
                    code,
                    started=started,
                    found_dir=found_dir,
                    live=live,
                    on_line=on_line,
                    before=set(),
                    executor=req.executor,
                    command=req.command,
                    status=status,
                )
        await self._close(job_id, finished=not detached)
        if detached:
            raise asyncio.CancelledError

    async def _find(self, job_id: str, executor: RemoteExecutor, created_at: float) -> str | None:
        found = await executor.find(job_id)
        if found is None and time.time() - created_at < FIND_RETRY_AGE_S:
            await self._sleep(FIND_RETRY_WAIT_S)  # ListTasks is eventually consistent
            found = await executor.find(job_id)
        return found

    def _finish(
        self,
        job_id: str,
        code: int | None,
        *,
        started: bool,
        found_dir: list[str],
        live: _Live,
        on_line: LineSink,
        before: set[str],
        executor: str,
        command: Sequence[str] = (),
        status: JobStatus | None = None,
    ) -> None:
        if status is None:
            if live.cancel_requested:
                status = "cancelled"
            elif code == 0:
                status = "succeeded"
            else:
                status = "failed"
        run_dir: str | None = None
        if started:
            if found_dir:
                run_dir = found_dir[0]
            elif executor == "local":  # S3 outputs never appear in the local runs dir
                run_dir = _new_run_dir(before, _run_dirs(self._runs_dir))
            meaning = exit_meaning(code, command)
            on_line(
                f"exit {code if code is not None else 'none'}"
                + (f" ({meaning})" if meaning else "")
            )
        self._set(job_id, status=status, exit_code=code, stopped_at=time.time(), run_dir=run_dir)

    async def _close(self, job_id: str, *, finished: bool) -> None:
        job = self.get(job_id)
        self._publish(job_id, {"type": "end"})
        self._live.pop(job_id, None)
        self._tasks.pop(job_id, None)
        if finished and job is not None:
            for fn in self._on_finish:
                result = fn(job)
                if asyncio.iscoroutine(result):
                    await result

    async def shutdown(self) -> None:
        """Cancel local jobs; detach remote ones (their tasks keep running)."""
        self._detaching = True
        for job_id, task in list(self._tasks.items()):
            job = self.get(job_id)
            executor = None if job is None else self._executors.get(job["executor"])
            if executor is not None and executor.survives_restart:
                task.cancel()
            else:
                await self.cancel(job_id)
        for task in list(self._tasks.values()):
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(task, CANCEL_GRACE_S + 5)


class _NullLock:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> None:
        return None


def _quote(arg: str) -> str:
    return arg if re.fullmatch(r"[A-Za-z0-9_./:=@%+,-]+", arg) else json.dumps(arg)


def _run_dirs(root: Path) -> set[str]:
    try:
        return {p.name for p in root.iterdir() if p.is_dir()}
    except OSError:
        return set()


def _new_run_dir(before: set[str], after: set[str]) -> str | None:
    new = sorted(after - before)
    return new[0] if len(new) == 1 else None
