"""Shared pieces for the AWS adapter tests: moto for S3/Logs/SSM/CloudWatch, botocore
Stubber for ECS (moto's awsvpc RunTask crashes), fake clocks, and a JobManager harness.

No real credentials: the autouse ``_no_aws`` fixture in conftest.py sets fake ones.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import boto3
import pytest
from botocore.stub import ANY, Stubber
from fastapi.testclient import TestClient
from moto import mock_aws

import fse_ui.jobs as jobs_module
from fse_ui.app import create_app
from fse_ui.aws.clients import AwsClients
from fse_ui.aws.ecs import HEARTBEAT_S, EcsExecutor
from fse_ui.aws.s3store import HOLDOUT_LOCK_KEY, PULL_LOCK_KEY, S3Lock
from fse_ui.db import Db
from fse_ui.jobs import JobManager, JobRequest, LocalExecutor
from tests.conftest import ORIGIN, Console, make_settings

REGION = "us-east-1"
BUCKET = "fse-data-test"
CLUSTER = "fse"
SUBNET = "subnet-0123456789abcdef0"
SG = "sg-0123456789abcdef0"
ACCOUNT = "123456789012"
BATCH_LOGS = "/fse/batch"
RUNNER_LOGS = "/fse/runner"
INSTANCE = "i-0123456789abcdef0"


def task_arn(task_id: str) -> str:
    return f"arn:aws:ecs:{REGION}:{ACCOUNT}:task/{CLUSTER}/{task_id}"


class FakeTime:
    """A clock that only moves when something sleeps (or a test moves it)."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)


@dataclass
class Aws:
    clients: AwsClients
    ecs: Stubber
    s3: Any
    logs: Any
    ssm: Any
    cloudwatch: Any

    def put_log(self, task_id: str, *messages: str, container: str = "fse") -> None:
        stream = f"batch/{container}/{task_id}"
        with contextlib.suppress(self.logs.exceptions.ResourceAlreadyExistsException):
            self.logs.create_log_stream(logGroupName=BATCH_LOGS, logStreamName=stream)
        now = int(time.time() * 1000)
        self.logs.put_log_events(
            logGroupName=BATCH_LOGS,
            logStreamName=stream,
            logEvents=[{"timestamp": now + i, "message": m} for i, m in enumerate(messages)],
        )

    def lock_body(self, key: str = PULL_LOCK_KEY) -> str | None:
        try:
            return str(self.s3.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode())
        except self.s3.exceptions.NoSuchKey:
            return None

    def put_lock(self, job_id: str, key: str = PULL_LOCK_KEY) -> None:
        started = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = f"user=op@example.com\nhost=fse-console\nstarted={started}\njob={job_id}\n"
        self.s3.put_object(Bucket=BUCKET, Key=key, Body=body.encode())

    def new_ecs(self) -> Stubber:
        """A fresh stubbed ECS client (a new console process)."""
        ecs = boto3.client("ecs", region_name=REGION)
        stub = Stubber(ecs)
        stub.activate()
        self.ecs = stub
        self.clients = AwsClients(
            ecs=ecs, s3=self.s3, logs=self.logs, ssm=self.ssm, cloudwatch=self.cloudwatch
        )
        return stub


@pytest.fixture
def aws() -> Iterator[Aws]:
    with mock_aws():
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=BUCKET)
        logs = boto3.client("logs", region_name=REGION)
        logs.create_log_group(logGroupName=BATCH_LOGS)
        logs.create_log_group(logGroupName=RUNNER_LOGS)
        logs.create_log_stream(logGroupName=RUNNER_LOGS, logStreamName="runner")
        ssm = boto3.client("ssm", region_name=REGION)
        cloudwatch = boto3.client("cloudwatch", region_name=REGION)
        ecs = boto3.client("ecs", region_name=REGION)
        stub = Stubber(ecs)
        stub.activate()
        clients = AwsClients(ecs=ecs, s3=s3, logs=logs, ssm=ssm, cloudwatch=cloudwatch)
        yield Aws(clients, stub, s3, logs, ssm, cloudwatch)
        stub.deactivate()


@pytest.fixture
def job_ids(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Deterministic job ids: ``a000000000000001``, ``a000000000000002``, ..."""
    issued: list[str] = []
    counter = itertools.count(1)

    def token_hex(_n: int) -> str:
        job_id = f"a{next(counter):015d}"
        issued.append(job_id)
        return job_id

    monkeypatch.setattr(jobs_module, "secrets", SimpleNamespace(token_hex=token_hex))
    return issued


# ---------------------------------------------------------------- Stubber responses


def overrides(
    cmd: list[str],
    env: list[dict[str, str]] | None = None,
    size: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "containerOverrides": [
            {
                "name": "fse",
                "command": cmd,
                "environment": env or [{"name": "FSE_SYNC_CONFIGS", "value": "1"}],
            }
        ]
    }
    if size:
        body.update(size)
    return body


def add_run_task(
    stub: Stubber,
    job_id: str,
    task_id: str,
    cmd: list[str] | Any,
    env: Any = None,
    size: dict[str, Any] | None = None,
) -> None:
    stub.add_response(
        "run_task",
        {"tasks": [{"taskArn": task_arn(task_id), "lastStatus": "PROVISIONING"}], "failures": []},
        {
            "cluster": CLUSTER,
            "taskDefinition": "fse-batch",
            "count": 1,
            "capacityProviderStrategy": [{"capacityProvider": "FARGATE_SPOT", "weight": 1}],
            "networkConfiguration": {
                "awsvpcConfiguration": {
                    "subnets": [SUBNET],
                    "securityGroups": [SG],
                    "assignPublicIp": "ENABLED",
                }
            },
            "startedBy": f"fse-ui_{job_id}",
            "overrides": cmd if cmd is ANY else overrides(cmd, env, size),
        },
    )


def add_describe(
    stub: Stubber,
    task_id: str,
    status: str,
    *,
    exit_code: int | None = None,
    reason: str | None = None,
) -> None:
    container: dict[str, Any] = {"name": "fse", "lastStatus": status}
    if exit_code is not None:
        container["exitCode"] = exit_code
    task: dict[str, Any] = {
        "taskArn": task_arn(task_id),
        "lastStatus": status,
        "containers": [container],
    }
    if reason is not None:
        task["stoppedReason"] = reason
    stub.add_response(
        "describe_tasks", {"tasks": [task], "failures": []}, {"cluster": CLUSTER, "tasks": [ANY]}
    )


def add_missing(stub: Stubber, task_id: str) -> None:
    stub.add_response(
        "describe_tasks",
        {"tasks": [], "failures": [{"arn": task_arn(task_id), "reason": "MISSING"}]},
        {"cluster": CLUSTER, "tasks": [task_arn(task_id)]},
    )


def add_list_tasks(stub: Stubber, job_id: str, desired: str, task_ids: list[str]) -> None:
    stub.add_response(
        "list_tasks",
        {"taskArns": [task_arn(t) for t in task_ids]},
        {"cluster": CLUSTER, "startedBy": f"fse-ui_{job_id}", "desiredStatus": desired},
    )


def add_stop_task(stub: Stubber, task_id: str) -> None:
    stub.add_response(
        "stop_task",
        {"task": {"taskArn": task_arn(task_id), "lastStatus": "STOPPING"}},
        {"cluster": CLUSTER, "task": task_arn(task_id), "reason": "cancelled in the fse console"},
    )


# ---------------------------------------------------------------- JobManager harness


def insert_row(
    db: Db,
    job_id: str,
    req: JobRequest,
    *,
    status: str = "running",
    external_id: str | None = None,
    cursor: str | None = None,
    age_s: float = 600.0,
) -> None:
    """A job row an earlier console process left behind."""
    now = time.time()
    db.execute(
        "INSERT INTO jobs (id, program, command, argv, placement, executor, status, title, "
        "user, created_at, started_at, external_id, external_cursor) "
        "VALUES (?, ?, ?, ?, ?, 'ecs', ?, ?, 'op@example.com', ?, ?, ?, ?)",
        (
            job_id,
            req.program,
            json.dumps(list(req.command)),
            json.dumps(list(req.argv)),
            req.placement,
            status,
            req.title,
            now - age_s,
            now - age_s if status == "running" else None,
            external_id,
            cursor,
        ),
    )


class Harness:
    """A JobManager with the local and ECS executors, built inside the test's event loop."""

    def __init__(
        self,
        tmp_path: Path,
        aws: Aws,
        fake: FakeTime,
        *,
        heavy_slots: int = 1,
        ecs_slots: int = 4,
        poll_status_s: float = 0.0,
        poll_logs_s: float = 0.001,
        drain_s: float = 0.0,
        heartbeat_s: float = HEARTBEAT_S,
        executors: dict[str, Any] | None = None,
        logs: Any = None,
        ecs: Any = None,
    ) -> None:
        self.home = tmp_path / "home"
        self.data_dir = self.home / ".skylit-fse"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = Db(self.data_dir / "ui" / "ui.sqlite")
        self.aws = aws
        self.fake = fake
        self.locks = {
            "pull": S3Lock(aws.s3, BUCKET, PULL_LOCK_KEY),
            "holdout": S3Lock(aws.s3, BUCKET, HOLDOUT_LOCK_KEY),
        }
        self.executor = EcsExecutor(
            ecs=ecs if ecs is not None else aws.clients.ecs,
            logs=logs if logs is not None else aws.logs,
            s3=aws.s3,
            cluster=CLUSTER,
            family="fse-batch",
            container="fse",
            subnets=[SUBNET],
            security_group=SG,
            log_group=BATCH_LOGS,
            bucket=BUCKET,
            data_dir=self.data_dir,
            locks=self.locks,
            poll_status_s=poll_status_s,
            poll_logs_s=poll_logs_s,
            drain_s=drain_s,
            heartbeat_s=heartbeat_s,
            sleep=fake.sleep,
            clock=fake.clock,
        )
        self.jobs = JobManager(
            self.db,
            log_dir=self.data_dir / "ui" / "jobs",
            runs_dir=self.data_dir / "runs",
            executors=executors or {"local": LocalExecutor(), "ecs": self.executor},
            env=dict,
            redact=lambda: lambda line: line,
            cwd=tmp_path,
            heavy_slots=heavy_slots,
            ecs_slots=ecs_slots,
            sleep=fake.sleep,
        )

    @staticmethod
    def request(
        command: tuple[str, ...], args: tuple[str, ...] = (), *, program: str = "fse"
    ) -> JobRequest:
        head = ("fse",) if program == "fse" else ("python", "-m", program)
        return JobRequest(
            program=program,
            command=command,
            argv=(*head, *command, *args),
            placement="batch",
            title=" ".join((program, *command)),
            heavy=True,
            executor="ecs",
            args=args,
        )

    def insert(self, job_id: str, req: JobRequest, **kw: Any) -> None:
        insert_row(self.db, job_id, req, **kw)

    def lines(self, job_id: str) -> list[str]:
        try:
            return (self.data_dir / "ui" / "jobs" / f"{job_id}.log").read_text().splitlines()
        except FileNotFoundError:
            return []

    async def resume(self, job_id: str) -> dict[str, Any]:
        self.jobs.resume_remote()
        return await self.jobs.wait(job_id)

    async def until(self, predicate: Callable[[], bool], timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while not predicate():
            assert time.monotonic() < deadline, "timed out"
            await asyncio.sleep(0.01)


# ---------------------------------------------------------------- the API on "AWS"


def aws_settings(home: Path, **over: Any) -> Any:
    base: dict[str, Any] = {
        "executor": "ecs",
        "secret_store": "ssm",
        "data_bucket": BUCKET,
        "ecs_subnets": (SUBNET,),
        "ecs_security_group": SG,
    }
    base.update(over)
    return make_settings(home, **base)


@pytest.fixture
def fake_time() -> FakeTime:
    return FakeTime()


@pytest.fixture
def aws_console(
    home: Path, aws: Aws, fake_time: FakeTime, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[..., Console]]:
    """``make(**settings)``: a logged-in console wired to the moto/Stubber clients.

    The EcsExecutor polls on the fake clock (no real 10 s waits).
    """
    real = EcsExecutor

    def fast_executor(**kw: Any) -> EcsExecutor:
        kw.setdefault("poll_status_s", 0.0)
        kw.setdefault("poll_logs_s", 0.001)
        kw.setdefault("drain_s", 0.0)
        return real(**kw, sleep=fake_time.sleep, clock=fake_time.clock)

    clients: list[TestClient] = []
    tuning: dict[str, Any] = {}

    def fast_executor_tuned(**kw: Any) -> EcsExecutor:
        return fast_executor(**{**tuning, **kw})

    monkeypatch.setattr("fse_ui.app.EcsExecutor", fast_executor_tuned)

    def make(*, ecs_kw: dict[str, Any] | None = None, **over: Any) -> Console:
        tuning.clear()
        tuning.update(ecs_kw or {})
        settings = aws_settings(home, **over)
        app = create_app(settings, aws=aws.clients, ssm_client=aws.ssm)
        client = TestClient(app, base_url=ORIGIN)
        client.__enter__()
        clients.append(client)
        return Console(client)

    yield make
    for c in clients:
        with contextlib.suppress(Exception):  # already closed by close()
            c.__exit__(None, None, None)


def close(console: Console) -> None:
    """Stop that console process (lifespan shutdown)."""
    console.client.__exit__(None, None, None)
