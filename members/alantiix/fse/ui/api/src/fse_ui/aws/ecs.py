"""EcsExecutor: batch commands as Fargate Spot tasks, with the same container command and
S3 pull lock as ``scripts/aws-batch.sh`` (design.md 10.4).

The task is the source of truth. The console stores the task ARN and the log
cursor as soon as they are known, detaches on shutdown (no StopTask, no lock
release) and re-attaches at the next start (:meth:`EcsExecutor.resume`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import stat
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

from fse_ui.aws.errors import AWS_ERRORS, error_code, is_access_denied, is_transient
from fse_ui.aws.s3store import HOLDOUT_LOCK_KEY, PULL_LOCK_KEY, LockError, S3Lock
from fse_ui.batch_inputs import stage_backtest_inputs
from fse_ui.jobs import JobRequest, LineSink, RemoteTaskGone, RunContext, Sleep

log = logging.getLogger(__name__)

MAX_STAGED_BYTES = 50 * 1024 * 1024
MAX_OVERRIDES_CHARS = 8192
# One backtest-plugin task. The family default stays 8 vCPU / 32 GiB for pull
# and the other batch commands. 1 vCPU allows 2-8 GiB; 8 GiB covers a replay
# that expands one recording day. 40 GiB ephemeral is above the 20 GiB free
# tier and below the 100 GiB task-definition default.
BACKTEST_CPU = "1024"
BACKTEST_MEMORY = "8192"
BACKTEST_EPHEMERAL_GIB = 40
# Rules-plugin card tasks read 1m bars and catalog.sqlite. Heatmaps are ~20 GB
# and dark-pool files are unused. The entrypoint honors this over the command map.
CARD_CACHE_PREFIXES = "bars,catalog.sqlite"
# The batch task definition defaults FSE_CACHE_BACKEND=efs (PR #39) and the image
# does too when the variable is unset. A task with no EFS mount then exits in
# seconds. Card bars are already in the bucket, so a card task syncs them from S3.
CARD_CACHE_BACKEND = "s3"
LOST_CONTACT_S = 15 * 60.0
BACKOFF_FIRST_S = 5.0
BACKOFF_MAX_S = 60.0
WARN_EVERY_S = 60.0
HEARTBEAT_S = 60.0
"""A silent, not yet STOPPED task gets one ``still <status>`` line per this many seconds."""
UPLOAD_ID_RE = re.compile(r"[a-f0-9]{16}")
STOP_REASON = "cancelled in the fse console"
UNLOCK_WARNING = "warning: could not delete the lock; use Unlock on the Jobs page"


def started_by(job_id: str) -> str:
    """ECS allows letters, digits, ``-``, ``/`` and ``_`` (36 at most): 23 characters here."""
    return f"fse-ui_{job_id}"


def lock_key(program: str, command: Sequence[str]) -> str | None:
    if program != "fse":
        return None
    path = tuple(command)
    if path in (("pull",), ("pull-equity-bars",), ("import-vix",)):  # all write cache/
        return PULL_LOCK_KEY
    if path == ("experiment", "holdout"):  # appends to state/holdout_log.jsonl
        return HOLDOUT_LOCK_KEY
    return None


def lock_key_for(request: JobRequest) -> str | None:
    return lock_key(request.program, request.command)


def lock_name(key: str) -> str:
    return "holdout" if key == HOLDOUT_LOCK_KEY else "pull"


def card_backtest(cmd: Sequence[str]) -> bool:
    """True when the task command is a strategy-card config (``configs/cards/``)."""
    for i, arg in enumerate(cmd):
        if arg.startswith("--config=") and "/cards/" in arg:
            return True
        if arg == "--config" and i + 1 < len(cmd) and "/cards/" in cmd[i + 1]:
            return True
    return False


def container_command(request: JobRequest) -> list[str]:
    """The task's command, mirroring ``aws-batch.sh`` (entrypoint ``batch``/``batch-py``)."""
    if request.program == "fse":
        return ["batch", *request.command, *request.args]
    return ["batch-py", request.program, *request.command, *request.args]


def _task_id(arn: str) -> str:
    return arn.rsplit("/", 1)[-1]


def _fmt_elapsed(seconds: float) -> str:
    """45s, 2m00s, 1h02m03s (the entrypoint's progress lines use the same form)."""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{s % 3600 // 60:02d}m{s % 60:02d}s"


class _Missing(Exception):
    """DescribeTasks reports the task MISSING."""


@dataclass(slots=True)
class _Staged:
    path: str
    key: str


class _Backoff:
    """Consecutive transient failures: 5 → 60 s waits, a warning line per minute,
    and a give-up after 15 minutes."""

    def __init__(self, clock: Callable[[], float], sleep: Sleep) -> None:
        self._clock, self._sleep = clock, sleep
        self._since: float | None = None
        self._warned: float | None = None
        self._wait = BACKOFF_FIRST_S

    def ok(self) -> None:
        self._since, self._warned, self._wait = None, None, BACKOFF_FIRST_S

    async def failed(self, exc: BaseException, on_line: LineSink | None, what: str) -> bool:
        """Wait before the next try; False once 15 minutes of failures have passed."""
        now = self._clock()
        if self._since is None:
            self._since = now
        if now - self._since >= LOST_CONTACT_S:
            return False
        if self._warned is None or now - self._warned >= WARN_EVERY_S:
            self._warned = now
            log.warning("%s failed (%s); retrying", what, error_code(exc))
            if on_line is not None:
                on_line(f"warning: {what} failed ({error_code(exc)}); retrying")
        await self._sleep(self._wait)
        self._wait = min(self._wait * 2, BACKOFF_MAX_S)
        return True


class EcsExecutor:
    name = "ecs"
    survives_restart = True

    def __init__(
        self,
        *,
        ecs: Any,
        logs: Any,
        s3: Any,
        cluster: str,
        family: str,
        container: str,
        subnets: Sequence[str],
        security_group: str,
        log_group: str,
        bucket: str,
        data_dir: Path,
        locks: Mapping[str, S3Lock] | None = None,
        poll_status_s: float = 10.0,
        poll_logs_s: float = 3.0,
        drain_s: float = 10.0,
        heartbeat_s: float = HEARTBEAT_S,
        sleep: Sleep = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ecs, self._logs, self._s3 = ecs, logs, s3
        self._cluster, self._family, self._container = cluster, family, container
        self._subnets = list(subnets)
        self._sg = security_group
        self._log_group = log_group
        self._bucket = bucket
        self._data_dir = data_dir
        self._locks: dict[str, S3Lock] = {lock.key: lock for lock in (locks or {}).values()}
        self._poll_status_s, self._poll_logs_s, self._drain_s = poll_status_s, poll_logs_s, drain_s
        self._heartbeat_s = heartbeat_s
        self._sleep, self._clock = sleep, clock
        self._tasks: dict[str, str] = {}
        """job id → task ARN, for Cancel (also after a re-attach)."""
        self._cancel_requested: set[str] = set()

    def _lock(self, key: str) -> S3Lock:
        if key not in self._locks:
            self._locks[key] = S3Lock(self._s3, self._bucket, key)
        return self._locks[key]

    # ------------------------------------------------------------ staging

    def _plan_staging(self, tokens: Sequence[str]) -> tuple[list[_Staged], list[str]] | str:
        """Configs and uploads the argv references; the reject line when one is unusable.

        Lexical checks only (``data_dir`` is itself a symlink in the UI image): never
        ``resolve()``; ``..`` escapes fall outside the roots and are ignored.
        """
        configs_root = str(self._data_dir / "configs") + os.sep
        uploads_root = str(self._data_dir / "ui" / "uploads") + os.sep
        staged: dict[str, _Staged] = {}
        upload_ids: list[str] = []
        for token in tokens:
            candidates = [token]
            if token.startswith("-") and "=" in token:
                candidates.append(token.split("=", 1)[1])
            for candidate in candidates:
                p = os.path.normpath(candidate)
                if p.startswith(configs_root):
                    key = "configs/" + p.removeprefix(configs_root).replace(os.sep, "/")
                elif p.startswith(uploads_root):
                    rest = p.removeprefix(uploads_root)
                    upload_id = rest.split(os.sep, 1)[0]
                    if not UPLOAD_ID_RE.fullmatch(upload_id):
                        continue
                    key = "uploads/" + rest.replace(os.sep, "/")
                    if upload_id not in upload_ids:
                        upload_ids.append(upload_id)
                else:
                    continue
                reject = (
                    f"error: {key} is not a regular file under configs/ or uploads/ "
                    f"(or is over 50 MiB)"
                )
                try:
                    st = os.lstat(p)  # the final component only
                except OSError:
                    return reject
                if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_STAGED_BYTES:
                    return reject
                staged[key] = _Staged(p, key)
        return list(staged.values()), upload_ids

    def _upload(self, item: _Staged) -> None:
        with open(item.path, "rb") as fh:
            body = fh.read(MAX_STAGED_BYTES + 1)
        self._s3.put_object(Bucket=self._bucket, Key=item.key, Body=body)

    # ------------------------------------------------------------ run

    async def run(
        self,
        job_id: str,
        argv: Sequence[str],  # the task has its own env; secrets come from the task definition
        env: Mapping[str, str],
        cwd: Path,
        on_line: LineSink,
        *,
        ctx: RunContext | None = None,
    ) -> int | None:
        if ctx is None:
            raise ValueError("EcsExecutor.run needs a RunContext")
        req = ctx.request
        cmd = container_command(req)
        planned = self._plan_staging(req.args)
        if isinstance(planned, str):
            log.info("job %s: %s", job_id, planned)
            on_line(planned)
            return 2
        staged, upload_ids = planned
        key = lock_key_for(req)
        environment = [{"name": "FSE_SYNC_CONFIGS", "value": "1"}]
        if upload_ids:
            environment.append({"name": "FSE_SYNC_UPLOADS", "value": " ".join(upload_ids)})
        if key == HOLDOUT_LOCK_KEY:
            environment.append({"name": "FSE_SYNC_STATE", "value": "1"})
        if tuple(req.command) == ("backtest-plugin",) and card_backtest(cmd):
            environment.append({"name": "FSE_CACHE_PREFIXES", "value": CARD_CACHE_PREFIXES})
            environment.append({"name": "FSE_CACHE_BACKEND", "value": CARD_CACHE_BACKEND})
        overrides: dict[str, Any] = {
            "containerOverrides": [
                {"name": self._container, "command": cmd, "environment": environment}
            ]
        }
        if tuple(req.command) == ("backtest-plugin",):
            overrides["cpu"] = BACKTEST_CPU
            overrides["memory"] = BACKTEST_MEMORY
            overrides["ephemeralStorage"] = {"sizeInGiB": BACKTEST_EPHEMERAL_GIB}
        if len(json.dumps(overrides)) > MAX_OVERRIDES_CHARS:
            on_line("error: the command line is too long for ECS (8 KiB overrides limit)")
            return 2
        for item in staged:
            try:
                await asyncio.to_thread(self._upload, item)
            except AWS_ERRORS as exc:
                log.warning("job %s: cannot upload %s: %s", job_id, item.key, error_code(exc))
                on_line(
                    f"error: cannot upload {item.key} to s3://{self._bucket}: {error_code(exc)}"
                )
                return 1
        if tuple(req.command) == ("backtest-plugin",):
            await stage_backtest_inputs(
                self._s3,
                self._bucket,
                self._data_dir,
                job_id=job_id,
                user=ctx.user,
                on_line=on_line,
                locks=self._locks,
            )
        lock_created = False
        try:
            if key is not None:
                rc = await self._acquire(ctx, key)
                if rc is not None:
                    return rc
                lock_created = True
            return await self._start_and_poll(ctx, cmd, overrides)
        finally:
            self._tasks.pop(job_id, None)
            self._cancel_requested.discard(job_id)
            if lock_created and key is not None and not ctx.detaching():
                await self._release(ctx.on_line, key, job_id)

    async def _acquire(self, ctx: RunContext, key: str) -> int | None:
        lock, name = self._lock(key), lock_name(key)
        utc = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = f"user={ctx.user}\nhost=fse-console\nstarted={utc}\njob={ctx.job_id}\n"
        try:
            if await lock.acquire(body):
                return None
        except LockError as exc:
            log.warning("job %s: cannot create %s: %s", ctx.job_id, lock.url, exc.code)
            ctx.on_line(f"error: cannot create the {name} lock {lock.url}: {exc.code}")
            return 1
        try:
            holder = await lock.holder() or []
        except LockError as exc:
            holder = [f"(cannot read the lock: {exc.code})"]
        log.info("job %s: %s is held", ctx.job_id, lock.url)
        ctx.on_line(f"{name} lock {lock.url} is held by:")
        for line in holder:
            ctx.on_line(f"  {line}")
        ctx.on_line(f"Another {name} is running. If it is not, use Unlock on the Jobs page.")
        return 75

    async def _release(self, on_line: LineSink | None, key: str, job_id: str) -> None:
        try:
            await self._lock(key).release_if(job_id)
        except LockError as exc:
            log.warning("job %s: cannot delete %s: %s", job_id, self._lock(key).url, exc.code)
            if on_line is not None:
                on_line(UNLOCK_WARNING)

    async def _start_and_poll(
        self, ctx: RunContext, cmd: list[str], overrides: dict[str, Any]
    ) -> int | None:
        job_id, on_line = ctx.job_id, ctx.on_line
        kwargs = {
            "cluster": self._cluster,
            "taskDefinition": self._family,
            "count": 1,
            "capacityProviderStrategy": [{"capacityProvider": "FARGATE_SPOT", "weight": 1}],
            "networkConfiguration": {
                "awsvpcConfiguration": {
                    "subnets": self._subnets,
                    "securityGroups": [self._sg],
                    "assignPublicIp": "ENABLED",
                }
            },
            "startedBy": started_by(job_id),
            "overrides": overrides,
        }
        try:
            resp = await asyncio.to_thread(self._ecs.run_task, **kwargs)
        except ClientError as exc:
            message = exc.response.get("Error", {}).get("Message", "")
            log.warning("job %s: run-task refused: %s", job_id, error_code(exc))
            on_line(f"error: run-task refused: {error_code(exc)}: {message}")
            return 1
        except AWS_ERRORS as exc:
            log.warning("job %s: run-task failed: %s", job_id, error_code(exc))
            on_line(f"error: run-task refused: {error_code(exc)}")
            return 1
        failures, tasks = resp.get("failures") or [], resp.get("tasks") or []
        if failures or not tasks:
            log.warning("job %s: run-task failures: %s", job_id, failures)
            for f in failures or [{"reason": "no task returned", "detail": ""}]:
                on_line(f"run-task failure: {f.get('reason', '')} {f.get('detail', '')}".rstrip())
            return 1
        arn = str(tasks[0]["taskArn"])
        ctx.set_external(arn, None)
        self._tasks[job_id] = arn
        label = " ".join((ctx.request.program, *ctx.request.command))
        on_line(f"Started {label} as ECS task {_task_id(arn)}; waiting for Fargate Spot capacity")
        if job_id in self._cancel_requested:  # Cancel arrived while RunTask was in flight
            self._cancel_requested.discard(job_id)
            await self._stop(job_id, arn)
        code, _stopped = await self._poll(ctx, arn, None, missing_is_gone=False)
        return code

    # ------------------------------------------------------------ polling

    async def _read_logs(self, stream: str, cursor: str | None) -> tuple[list[str], str | None]:
        kw: dict[str, Any] = {
            "logGroupName": self._log_group,
            "logStreamName": stream,
            "startFromHead": True,
        }
        if cursor is not None:  # botocore rejects nextToken=None
            kw["nextToken"] = cursor
        try:
            resp = await asyncio.to_thread(self._logs.get_log_events, **kw)
        except ClientError as exc:
            if error_code(exc) == "ResourceNotFoundException":  # the container has not started
                return [], cursor
            raise
        lines: list[str] = []
        for event in resp.get("events", []):
            lines.extend(str(event.get("message", "")).split("\n"))
        token = resp.get("nextForwardToken")
        return lines, (str(token) if token else cursor)

    async def _poll(
        self, ctx: RunContext, arn: str, cursor: str | None, *, missing_is_gone: bool
    ) -> tuple[int | None, bool]:
        """Stream logs and follow the task to STOPPED: ``(exit code, reached STOPPED)``."""
        on_line = ctx.on_line
        task_id = _task_id(arn)
        stream = f"batch/{self._container}/{task_id}"
        backoff = _Backoff(self._clock, self._sleep)
        last_status: str | None = None
        next_status = self._clock()
        stopped: dict[str, Any] | None = None
        stopped_at = 0.0
        empty_pages = 0
        # Heartbeat: last log line or status change, and the last heartbeat line.
        last_output = last_beat = self._clock()
        while True:
            try:
                if stopped is None and self._clock() >= next_status:
                    resp = await asyncio.to_thread(
                        self._ecs.describe_tasks, cluster=self._cluster, tasks=[arn]
                    )
                    tasks = resp.get("tasks") or []
                    if not tasks:
                        reasons = {f.get("reason") for f in resp.get("failures") or []}
                        if "MISSING" in reasons and missing_is_gone:
                            raise _Missing(task_id)
                        raise ClientError(
                            {"Error": {"Code": "MISSING", "Message": "task not listed yet"}},
                            "DescribeTasks",
                        )
                    task = tasks[0]
                    status = str(task.get("lastStatus", "UNKNOWN"))
                    if status != last_status:
                        on_line(f"task {task_id}: {status}")
                        last_status = status
                        last_output = self._clock()
                    if status == "STOPPED":
                        stopped, stopped_at = task, self._clock()
                    next_status = self._clock() + self._poll_status_s
                lines, token = await self._read_logs(stream, cursor)
                backoff.ok()
            except _Missing:
                raise
            except AWS_ERRORS as exc:
                if is_access_denied(exc):
                    log.error("job %s: access denied: %s", ctx.job_id, error_code(exc))
                    on_line(f"error: access denied by AWS ({error_code(exc)}); task {task_id}")
                    return None, False
                if not is_transient(exc):
                    log.error("job %s: cannot follow the task: %s", ctx.job_id, error_code(exc))
                    on_line(f"error: cannot follow ECS task {task_id}: {error_code(exc)}")
                    return None, False
                if not await backoff.failed(exc, on_line, "reading ECS task state"):
                    log.error("job %s: lost contact with ECS for 15 min", ctx.job_id)
                    on_line(
                        f"error: lost contact with ECS for 15 min; task {task_id} may still run"
                    )
                    return None, False
                continue
            for line in lines:
                on_line(line)
            now = self._clock()
            if lines:
                last_output = now
            elif (
                stopped is None
                and last_status is not None
                and self._heartbeat_s > 0
                and now - max(last_output, last_beat) >= self._heartbeat_s
            ):
                # Spot waits and long S3 syncs can be quiet for minutes: say it is alive.
                quiet = _fmt_elapsed(now - last_output)
                on_line(f"task {task_id}: still {last_status}, no new output for {quiet}")
                last_beat = now
            unchanged = not lines and token == cursor
            if token != cursor:
                cursor = token
                ctx.set_external(arn, cursor)
            if stopped is not None:
                empty_pages = empty_pages + 1 if unchanged else 0
                if self._clock() - stopped_at >= self._drain_s and empty_pages >= 2:
                    break
            await self._sleep(self._poll_logs_s)
        assert stopped is not None
        return self._exit_code(stopped, task_id, on_line), True

    def _exit_code(self, task: dict[str, Any], task_id: str, on_line: LineSink) -> int | None:
        containers = task.get("containers") or []
        found = next((c for c in containers if c.get("name") == self._container), None)
        code = None if found is None else found.get("exitCode")
        if code is None:
            reason = task.get("stoppedReason") or "unknown"
            log.info("task %s stopped without an exit code: %s", task_id, reason)
            on_line(f"error: task {task_id} stopped without an exit code: {reason}")
            return None
        return int(code)

    # ------------------------------------------------------------ re-attach

    async def resume(self, ctx: RunContext, external_id: str, cursor: str | None) -> int | None:
        job_id = ctx.job_id
        task_id = _task_id(external_id)
        key = lock_key_for(ctx.request)
        self._tasks[job_id] = external_id
        try:
            if job_id in self._cancel_requested:
                self._cancel_requested.discard(job_id)
                await self._stop(job_id, external_id)
            try:
                code, stopped = await self._poll(ctx, external_id, cursor, missing_is_gone=True)
            except _Missing:
                log.warning("job %s: task %s is MISSING", job_id, task_id)
                ctx.on_line(
                    f"task {task_id} is no longer known to ECS; its log is in {self._log_group} "
                    f"stream batch/{self._container}/{task_id}"
                )
                if key is not None:
                    await self._release(ctx.on_line, key, job_id)
                raise RemoteTaskGone(task_id) from None
            if stopped and key is not None and not ctx.detaching():
                await self._release(ctx.on_line, key, job_id)
            return code
        finally:
            self._tasks.pop(job_id, None)
            self._cancel_requested.discard(job_id)

    async def find(self, job_id: str) -> str | None:
        backoff = _Backoff(self._clock, self._sleep)
        for desired in ("RUNNING", "STOPPED"):
            while True:
                try:
                    resp = await asyncio.to_thread(
                        self._ecs.list_tasks,
                        cluster=self._cluster,
                        startedBy=started_by(job_id),
                        desiredStatus=desired,
                    )
                    backoff.ok()
                    break
                except AWS_ERRORS as exc:
                    if not is_transient(exc) or not await backoff.failed(exc, None, "ListTasks"):
                        raise
            arns = resp.get("taskArns") or []
            if arns:
                return str(arns[0])
        return None

    async def abandon(
        self, job_id: str, request: JobRequest, on_line: LineSink | None = None
    ) -> None:
        key = lock_key_for(request)
        if key is not None:
            await self._release(on_line, key, job_id)

    # ------------------------------------------------------------ cancel

    async def _stop(self, job_id: str, arn: str) -> bool:
        try:
            await asyncio.to_thread(
                self._ecs.stop_task, cluster=self._cluster, task=arn, reason=STOP_REASON
            )
        except AWS_ERRORS as exc:
            log.warning("job %s: stop-task failed: %s", job_id, error_code(exc))
            return False
        return True

    async def cancel(self, job_id: str) -> bool:
        arn = self._tasks.get(job_id)
        if arn is None:
            self._cancel_requested.add(job_id)  # RunTask may be in flight (run step 6)
            return False
        return await self._stop(job_id, arn)
