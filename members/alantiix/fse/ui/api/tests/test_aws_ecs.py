"""EcsExecutor and JobManager detach/re-attach (design.md 10.3, 10.4, decision b).

ECS is botocore Stubber (moto's awsvpc RunTask crashes); S3 and Logs are moto.
Every Stubber queue is checked with ``assert_no_pending_responses``: an extra or
missing ECS call fails the test.
"""

from __future__ import annotations

import asyncio
import re
import sys
import threading
import time
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

import boto3
import pytest
from botocore.stub import Stubber
from fse.strategy.spec import EXAMPLE_SPEC

from fse_ui.aws.ecs import (
    BACKTEST_CPU,
    BACKTEST_EPHEMERAL_GIB,
    BACKTEST_MEMORY,
    CARD_CACHE_BACKEND,
    CARD_CACHE_PREFIXES,
    started_by,
)
from fse_ui.aws.s3store import HOLDOUT_LOCK_KEY
from fse_ui.bar_gaps import session_days, sessions_before
from fse_ui.db import Db
from fse_ui.jobs import JobRequest, LocalExecutor
from tests.aws_support import (
    BATCH_LOGS,
    BUCKET,
    REGION,
    Aws,
    FakeTime,
    Harness,
    add_describe,
    add_list_tasks,
    add_missing,
    add_run_task,
    add_stop_task,
    close,
    insert_row,
    task_arn,
)
from tests.conftest import Console, wait_job
from tests.fixtures import backtest_run

USER = "op@example.com"
SYNC_CONFIGS = {"name": "FSE_SYNC_CONFIGS", "value": "1"}


def _log(console: Console, job_id: str) -> list[str]:
    lines: list[str] = console.get(f"/api/jobs/{job_id}/log").json()["lines"]
    return lines


def _until(predicate: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


def _seed_row(home: Path, req: JobRequest, job_id: str, **kw: Any) -> None:
    """A row an earlier console process left behind (the DB file the app opens)."""
    db = Db(home / ".skylit-fse" / "ui" / "ui.sqlite")
    insert_row(db, job_id, req, **kw)
    db.close()


# ================================================================ through the API


def test_lifecycle_runs_on_fargate_streams_logs_and_lists_the_s3_run(
    aws_console: Callable[..., Console], aws: Aws, job_ids: list[str], tmp_path: Path
) -> None:
    console = aws_console()
    base = console.get("/api/configs/strategy/playbook_baseline").json()["text"]
    assert (
        console.post("/api/configs/strategy/x", json={"text": base, "create": True}).status_code
        == 200
    )
    settings = console.app.state.settings
    path = settings.configs_dir / "strategy" / "x" / "current.yaml"
    assert str(path).endswith("/.skylit-fse/configs/strategy/x/current.yaml")
    jid = "a000000000000001"
    cmd = ["batch", "backtest", f"--config={path}", "--start=2026-10-06", "--end=2026-10-07"]
    add_run_task(aws.ecs, jid, "t1", cmd)
    add_describe(aws.ecs, "t1", "RUNNING")
    add_describe(aws.ecs, "t1", "STOPPED", exit_code=0)
    aws.put_log("t1", "backtest: 2 sessions", f"wrote {settings.runs_dir}/backtest-x\nall done")
    src = backtest_run(tmp_path / "src", "backtest-x", ["1.5", "-1"])
    for f in src.iterdir():
        aws.s3.put_object(Bucket=BUCKET, Key=f"runs/backtest-x/{f.name}", Body=f.read_bytes())

    r = console.post(
        "/api/jobs",
        json={
            "command": "fse backtest",
            "values": {"config": str(path), "start": "2026-10-06", "end": "2026-10-07"},
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["executor"] == "ecs"
    job = wait_job(console, jid)
    lines = _log(console, jid)
    assert job["status"] == "succeeded", lines
    assert job["external_id"] == task_arn("t1")
    assert job["run_dir"] == "backtest-x"
    assert "backtest: 2 sessions" in lines and "all done" in lines
    assert "Started fse backtest as ECS task t1; waiting for Fargate Spot capacity" in lines
    staged = aws.s3.get_object(Bucket=BUCKET, Key="configs/strategy/x/current.yaml")
    assert staged["Body"].read().decode() == path.read_text()
    runs = {run["name"]: run for run in console.get("/api/runs").json()}
    assert runs["backtest-x"]["kind"] == "backtest"
    assert console.get("/api/runs/backtest-x/metrics").json()["source"] == "trades.json"
    aws.ecs.assert_no_pending_responses()


def test_shutdown_detaches_a_pull_and_the_next_start_finishes_it_and_releases_its_lock(
    aws_console: Callable[..., Console], aws: Aws, job_ids: list[str]
) -> None:
    console = aws_console(ecs_kw={"poll_status_s": 1e9})
    jid = "a000000000000001"
    add_run_task(aws.ecs, jid, "p1", ["batch", "pull", "--start=2026-10-06", "--end=2026-10-07"])
    add_describe(aws.ecs, "p1", "RUNNING")
    r = console.post(
        "/api/jobs",
        json={"command": "fse pull", "values": {"start": "2026-10-06", "end": "2026-10-07"}},
    )
    assert r.status_code == 201, r.text
    _until(lambda: "task p1: RUNNING" in _log(console, jid))
    assert f"job={jid}" in (aws.lock_body() or "")
    close(console)
    aws.ecs.assert_no_pending_responses()  # no StopTask on detach

    row = Db(console.app.state.settings.db_path).one("SELECT * FROM jobs WHERE id = ?", (jid,))
    assert row is not None and row["status"] == "running"
    assert row["external_id"] == task_arn("p1")
    assert f"job={jid}" in (aws.lock_body() or "")  # the task still writes cache/
    stub = aws.new_ecs()
    add_describe(stub, "p1", "STOPPED", exit_code=0)
    again = aws_console()
    job = wait_job(again, jid)
    lines = _log(again, jid)
    assert job["status"] == "succeeded", lines
    assert "console stopped; ECS task p1 keeps running and is re-attached at the next start" in (
        lines
    )
    assert "console started; re-attached to ECS task p1" in lines
    assert aws.lock_body() is None
    stub.assert_no_pending_responses()


def test_cancel_after_reattach_stops_the_stored_task(
    aws_console: Callable[..., Console], aws: Aws, home: Path, fake_time: FakeTime
) -> None:
    jid = "b000000000000001"
    _seed_row(home, Harness.request(("report",), ("--run=x",)), jid, external_id=task_arn("c9"))
    add_describe(aws.ecs, "c9", "RUNNING")
    add_stop_task(aws.ecs, "c9")
    add_describe(aws.ecs, "c9", "STOPPED", exit_code=130)

    def bump(**_: Any) -> None:  # the next DescribeTasks is due right after StopTask
        fake_time.now += 2e9

    aws.clients.ecs.meta.events.register("after-call.ecs.StopTask", bump)
    console = aws_console(ecs_kw={"poll_status_s": 1e9})
    _until(lambda: "task c9: RUNNING" in _log(console, jid))
    assert console.post(f"/api/jobs/{jid}/cancel").status_code == 200
    job = wait_job(console, jid)
    assert job["status"] == "cancelled" and job["exit_code"] == 130
    aws.ecs.assert_no_pending_responses()


def test_startup_serves_before_aws_answers_and_backs_off_on_throttling(
    aws_console: Callable[..., Console], aws: Aws, home: Path, fake_time: FakeTime
) -> None:
    jid = "b000000000000002"
    _seed_row(home, Harness.request(("report",), ("--run=x",)), jid, external_id=task_arn("r1"))
    gate, entered = threading.Event(), threading.Event()

    def wait_handler(**_: Any) -> None:  # runs in the to_thread worker
        entered.set()
        gate.wait(10)

    aws.clients.ecs.meta.events.register("before-parameter-build.ecs.DescribeTasks", wait_handler)
    for _ in range(2):
        aws.ecs.add_client_error(
            "describe_tasks", service_error_code="ThrottlingException", http_status_code=400
        )
    add_describe(aws.ecs, "r1", "RUNNING")
    console = aws_console(ecs_kw={"poll_status_s": 1e9})  # lifespan has reached yield
    assert entered.wait(5)  # ...while the first DescribeTasks is still in flight
    assert console.get("/api/health").status_code == 200
    assert console.get(f"/api/jobs/{jid}").json()["status"] == "running"
    gate.set()
    _until(lambda: "task r1: RUNNING" in _log(console, jid))
    lines = _log(console, jid)
    assert "warning: reading ECS task state failed (ThrottlingException); retrying" in lines
    assert fake_time.sleeps[:2] == [5.0, 10.0]
    assert console.get(f"/api/jobs/{jid}").json()["status"] == "running"
    aws.ecs.assert_no_pending_responses()


# ================================================================ the executor (harness)


def _submit(h: Harness, req: JobRequest) -> str:
    return str(h.jobs.submit(req, user=USER)["id"])


@pytest.mark.asyncio
async def test_held_pull_lock_is_exit_75_with_the_holder_and_no_run_task(
    tmp_path: Path, aws: Aws, fake_time: FakeTime, job_ids: list[str]
) -> None:
    h = Harness(tmp_path, aws, fake_time)
    aws.put_lock("someone0else0000")
    jid = _submit(h, h.request(("pull",), ("--start=2026-10-06", "--end=2026-10-07")))
    done = await h.jobs.wait(jid)
    lines = h.lines(jid)
    assert done["status"] == "failed" and done["exit_code"] == 75, lines
    assert f"pull lock s3://{BUCKET}/locks/pull.lock is held by:" in lines
    assert "  job=someone0else0000" in lines
    assert "Another pull is running. If it is not, use Unlock on the Jobs page." in lines
    assert "job=someone0else0000" in (aws.lock_body() or "")
    aws.ecs.assert_no_pending_responses()


@pytest.mark.asyncio
async def test_cancel_sends_stop_task_and_the_job_is_cancelled(
    tmp_path: Path, aws: Aws, fake_time: FakeTime, job_ids: list[str]
) -> None:
    h = Harness(tmp_path, aws, fake_time, poll_status_s=1e9)
    jid = "a000000000000001"
    add_run_task(aws.ecs, jid, "c1", ["batch", "report", "--run=x"])
    add_describe(aws.ecs, "c1", "RUNNING")
    add_stop_task(aws.ecs, "c1")
    add_describe(aws.ecs, "c1", "STOPPED", exit_code=130)
    aws.clients.ecs.meta.events.register(
        "after-call.ecs.StopTask", lambda **_: setattr(fake_time, "now", fake_time.now + 2e9)
    )
    assert _submit(h, h.request(("report",), ("--run=x",))) == jid
    await h.until(lambda: "task c1: RUNNING" in h.lines(jid))
    assert await h.jobs.cancel(jid) is True
    done = await h.jobs.wait(jid)
    assert done["status"] == "cancelled" and done["exit_code"] == 130
    aws.ecs.assert_no_pending_responses()


@pytest.mark.asyncio
async def test_cancel_while_run_task_is_in_flight_stops_the_task_at_once(
    tmp_path: Path, aws: Aws, fake_time: FakeTime, job_ids: list[str]
) -> None:
    h = Harness(tmp_path, aws, fake_time)
    jid = "a000000000000001"
    gate, entered = threading.Event(), threading.Event()

    def hold(**_: Any) -> None:
        entered.set()
        gate.wait(10)

    aws.clients.ecs.meta.events.register("before-parameter-build.ecs.RunTask", hold)
    add_run_task(aws.ecs, jid, "i1", ["batch", "report", "--run=x"])
    add_stop_task(aws.ecs, "i1")  # Stubber enforces the order: StopTask right after RunTask
    add_describe(aws.ecs, "i1", "STOPPED", exit_code=143)
    _submit(h, h.request(("report",), ("--run=x",)))
    await h.until(entered.is_set)
    assert await h.jobs.cancel(jid) is False  # no ARN yet: recorded for step 6
    gate.set()
    done = await h.jobs.wait(jid)
    assert done["status"] == "cancelled" and done["exit_code"] == 143
    aws.ecs.assert_no_pending_responses()


@pytest.mark.asyncio
async def test_stopped_without_an_exit_code_is_failed_with_the_reason(
    tmp_path: Path, aws: Aws, fake_time: FakeTime, job_ids: list[str]
) -> None:
    h = Harness(tmp_path, aws, fake_time)
    jid = "a000000000000001"
    add_run_task(aws.ecs, jid, "m1", ["batch", "report", "--run=x"])
    add_describe(aws.ecs, "m1", "STOPPED", reason="Your Spot Task was interrupted.")
    _submit(h, h.request(("report",), ("--run=x",)))
    done = await h.jobs.wait(jid)
    assert (done["status"], done["exit_code"]) == ("failed", None)
    assert (
        "error: task m1 stopped without an exit code: Your Spot Task was interrupted."
        in h.lines(jid)
    )


@pytest.mark.asyncio
async def test_research_program_runs_as_batch_py(
    tmp_path: Path, aws: Aws, fake_time: FakeTime, job_ids: list[str]
) -> None:
    h = Harness(tmp_path, aws, fake_time)
    jid = "a000000000000001"
    add_run_task(
        aws.ecs,
        jid,
        "r1",
        ["batch-py", "research.example.run", "backtest", "--first=2026-10-06"],
    )
    add_describe(aws.ecs, "r1", "STOPPED", exit_code=0)
    req = h.request(("backtest",), ("--first=2026-10-06",), program="research.example.run")
    _submit(h, req)
    assert (await h.jobs.wait(jid))["status"] == "succeeded"
    aws.ecs.assert_no_pending_responses()


@pytest.mark.asyncio
async def test_import_vix_stages_its_positional_upload_and_takes_the_pull_lock(
    tmp_path: Path, aws: Aws, fake_time: FakeTime, job_ids: list[str]
) -> None:
    h = Harness(tmp_path, aws, fake_time)
    jid = "a000000000000001"
    folder = h.data_dir / "ui" / "uploads" / "0123456789abcdef"
    folder.mkdir(parents=True)
    csv = folder / "vix.csv"
    csv.write_text("date,close\n2026-10-06,17.2\n")
    lock_at_run_task: list[str | None] = []
    aws.clients.ecs.meta.events.register(
        "before-parameter-build.ecs.RunTask", lambda **_: lock_at_run_task.append(aws.lock_body())
    )
    env = [SYNC_CONFIGS, {"name": "FSE_SYNC_UPLOADS", "value": "0123456789abcdef"}]
    add_run_task(aws.ecs, jid, "v1", ["batch", "import-vix", str(csv)], env)
    add_describe(aws.ecs, "v1", "STOPPED", exit_code=0)
    _submit(h, h.request(("import-vix",), (str(csv),)))
    assert (await h.jobs.wait(jid))["status"] == "succeeded", h.lines(jid)
    body = aws.s3.get_object(Bucket=BUCKET, Key="uploads/0123456789abcdef/vix.csv")["Body"]
    assert body.read().decode() == csv.read_text()
    assert f"job={jid}" in (lock_at_run_task[0] or "")
    assert aws.lock_body() is None  # released after the task stopped
    aws.ecs.assert_no_pending_responses()


@pytest.mark.asyncio
async def test_experiment_holdout_takes_the_holdout_lock_and_syncs_state(
    tmp_path: Path, aws: Aws, fake_time: FakeTime, job_ids: list[str]
) -> None:
    h = Harness(tmp_path, aws, fake_time)
    jid = "a000000000000001"
    cfg = h.data_dir / "configs" / "strategy" / "h" / "current.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("order_mode: paper\n")
    held: list[str | None] = []
    aws.clients.ecs.meta.events.register(
        "before-parameter-build.ecs.RunTask",
        lambda **_: held.append(aws.lock_body(HOLDOUT_LOCK_KEY)),
    )
    env = [SYNC_CONFIGS, {"name": "FSE_SYNC_STATE", "value": "1"}]
    add_run_task(aws.ecs, jid, "h1", ["batch", "experiment", "holdout", f"--config={cfg}"], env)
    add_describe(aws.ecs, "h1", "STOPPED", exit_code=0)
    _submit(h, h.request(("experiment", "holdout"), (f"--config={cfg}",)))
    assert (await h.jobs.wait(jid))["status"] == "succeeded", h.lines(jid)
    assert f"job={jid}" in (held[0] or "")
    assert aws.lock_body(HOLDOUT_LOCK_KEY) is None
    staged = aws.s3.get_object(Bucket=BUCKET, Key="configs/strategy/h/current.yaml")["Body"]
    assert staged.read() == b"order_mode: paper\n"
    aws.ecs.assert_no_pending_responses()


@pytest.mark.asyncio
async def test_overrides_over_8_kib_are_exit_2_without_run_task(
    tmp_path: Path, aws: Aws, fake_time: FakeTime, job_ids: list[str]
) -> None:
    h = Harness(tmp_path, aws, fake_time)
    jid = _submit(h, h.request(("report",), ("--run=" + "x" * 9000,)))
    done = await h.jobs.wait(jid)
    assert done["exit_code"] == 2
    assert "error: the command line is too long for ECS (8 KiB overrides limit)" in h.lines(jid)
    aws.ecs.assert_no_pending_responses()


@pytest.mark.asyncio
async def test_a_symlink_in_configs_is_rejected_and_nothing_is_uploaded(
    tmp_path: Path, aws: Aws, fake_time: FakeTime, job_ids: list[str]
) -> None:
    h = Harness(tmp_path, aws, fake_time)
    target = tmp_path / "elsewhere.yaml"
    target.write_text("secret: no\n")
    link = h.data_dir / "configs" / "strategy" / "evil" / "current.yaml"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    jid = _submit(h, h.request(("backtest",), (f"--config={link}", "--start=2026-10-06")))
    done = await h.jobs.wait(jid)
    assert done["exit_code"] == 2
    assert (
        "error: configs/strategy/evil/current.yaml is not a regular file under configs/ or "
        "uploads/ (or is over 50 MiB)" in h.lines(jid)
    )
    assert aws.s3.list_objects_v2(Bucket=BUCKET).get("KeyCount", 0) == 0
    aws.ecs.assert_no_pending_responses()


@pytest.mark.asyncio
async def test_a_dotdot_path_out_of_configs_is_not_staged(
    tmp_path: Path, aws: Aws, fake_time: FakeTime, job_ids: list[str]
) -> None:
    h = Harness(tmp_path, aws, fake_time)
    jid = "a000000000000001"
    (h.data_dir / "x").write_text("outside\n")
    token = f"--run={h.data_dir}/configs/../x"
    add_run_task(aws.ecs, jid, "n1", ["batch", "report", token])
    add_describe(aws.ecs, "n1", "STOPPED", exit_code=0)
    _submit(h, h.request(("report",), (token,)))
    assert (await h.jobs.wait(jid))["status"] == "succeeded"
    assert aws.s3.list_objects_v2(Bucket=BUCKET).get("KeyCount", 0) == 0
    aws.ecs.assert_no_pending_responses()


@pytest.mark.asyncio
async def test_logs_drain_after_stopped_and_the_first_read_has_no_next_token(
    tmp_path: Path, aws: Aws, fake_time: FakeTime
) -> None:
    logs = boto3.client("logs", region_name=REGION)
    lstub = Stubber(logs)
    base = {"logGroupName": BATCH_LOGS, "logStreamName": "batch/fse/d1", "startFromHead": True}
    pages: list[tuple[str | None, list[str], str]] = [
        (None, [], "f/1"),
        ("f/1", ["final summary"], "f/2"),  # the final event, on the second-to-last page...
        ("f/2", [], "f/2"),
        ("f/2", [], "f/2"),  # two empty pages, but only 9 s since STOPPED
        ("f/2", [], "f/2"),
    ]
    for token, messages, nxt in pages:
        expected = dict(base) if token is None else {**base, "nextToken": token}
        lstub.add_response(
            "get_log_events",
            {
                "events": [{"timestamp": 1, "message": m} for m in messages],
                "nextForwardToken": nxt,
            },
            expected,
        )
    lstub.activate()
    h = Harness(tmp_path, aws, fake_time, poll_logs_s=3.0, drain_s=10.0, logs=logs)
    jid = "b000000000000001"
    h.insert(jid, h.request(("report",), ("--run=x",)), external_id=task_arn("d1"))
    add_describe(aws.ecs, "d1", "STOPPED", exit_code=0)
    done = await h.resume(jid)
    assert done["status"] == "succeeded", h.lines(jid)
    assert "final summary" in h.lines(jid)
    lstub.assert_no_pending_responses()  # no sixth read
    row = h.db.one("SELECT external_cursor FROM jobs WHERE id = ?", (jid,))
    assert row is not None and row["external_cursor"] == "f/2"


class _TimedEcs:
    """DescribeTasks on the fake clock: RUNNING until ``stop_at``, then STOPPED (exit 0)."""

    def __init__(self, fake: FakeTime, task_id: str, stop_at: float) -> None:
        self.fake, self.task_id, self.stop_at = fake, task_id, stop_at

    def describe_tasks(self, **_kw: Any) -> dict[str, Any]:
        status = "STOPPED" if self.fake.now >= self.stop_at else "RUNNING"
        container: dict[str, Any] = {"name": "fse", "lastStatus": status}
        if status == "STOPPED":
            container["exitCode"] = 0
        return {
            "tasks": [
                {"taskArn": task_arn(self.task_id), "lastStatus": status, "containers": [container]}
            ],
            "failures": [],
        }


class _TimedLogs:
    """GetLogEvents on the fake clock: each message appears once the clock reaches its time."""

    def __init__(self, fake: FakeTime, events: list[tuple[float, str]]) -> None:
        self.fake, self.events, self.sent = fake, events, 0

    def get_log_events(self, **_kw: Any) -> dict[str, Any]:
        due = [m for at, m in self.events[self.sent :] if at <= self.fake.now]
        self.sent += len(due)
        return {
            "events": [{"timestamp": 1, "message": m} for m in due],
            "nextForwardToken": f"f/{self.sent}",
        }


@pytest.mark.asyncio
async def test_lines_stream_while_running_before_stopped(
    tmp_path: Path, aws: Aws, fake_time: FakeTime
) -> None:
    t0 = fake_time.now
    ecs = _TimedEcs(fake_time, "r1", stop_at=t0 + 120)
    logs = _TimedLogs(fake_time, [(t0 + 90, "fse-entrypoint: downloading cache: 1.0 GB")])
    h = Harness(tmp_path, aws, fake_time, poll_status_s=10.0, poll_logs_s=3.0, ecs=ecs, logs=logs)
    jid = "b000000000000011"
    h.insert(jid, h.request(("pull",)), external_id=task_arn("r1"))
    done = await h.resume(jid)
    assert done["status"] == "succeeded", h.lines(jid)
    lines = h.lines(jid)
    line = lines.index("fse-entrypoint: downloading cache: 1.0 GB")
    assert line < lines.index("task r1: STOPPED"), lines


@pytest.mark.asyncio
async def test_silence_while_running_emits_a_heartbeat_per_minute(
    tmp_path: Path, aws: Aws, fake_time: FakeTime
) -> None:
    t0 = fake_time.now
    ecs = _TimedEcs(fake_time, "r2", stop_at=t0 + 200)
    logs = _TimedLogs(fake_time, [])
    h = Harness(tmp_path, aws, fake_time, poll_status_s=10.0, poll_logs_s=3.0, ecs=ecs, logs=logs)
    jid = "b000000000000012"
    h.insert(jid, h.request(("pull",)), external_id=task_arn("r2"))
    done = await h.resume(jid)
    assert done["status"] == "succeeded", h.lines(jid)
    lines = h.lines(jid)
    beats = [x for x in lines if x.startswith("task r2: still RUNNING, no new output for ")]
    assert [b.rsplit(" ", 1)[1] for b in beats] == ["1m00s", "2m00s", "3m00s"], lines
    stopped = lines.index("task r2: STOPPED")
    assert all(lines.index(b) < stopped for b in beats)
    assert not any("still" in x for x in lines[stopped:])


@pytest.mark.asyncio
async def test_output_resets_the_heartbeat(tmp_path: Path, aws: Aws, fake_time: FakeTime) -> None:
    t0 = fake_time.now
    ecs = _TimedEcs(fake_time, "r3", stop_at=t0 + 110)
    logs = _TimedLogs(fake_time, [(t0 + 30, "a"), (t0 + 60, "b"), (t0 + 90, "c")])
    h = Harness(tmp_path, aws, fake_time, poll_status_s=10.0, poll_logs_s=3.0, ecs=ecs, logs=logs)
    jid = "b000000000000013"
    h.insert(jid, h.request(("pull",)), external_id=task_arn("r3"))
    assert (await h.resume(jid))["status"] == "succeeded"
    assert not any("no new output" in x for x in h.lines(jid)), h.lines(jid)


# ================================================================ re-attach outcomes


@pytest.mark.asyncio
async def test_reattach_after_stopped_appends_the_remaining_logs(
    tmp_path: Path, aws: Aws, fake_time: FakeTime
) -> None:
    h = Harness(tmp_path, aws, fake_time)
    jid = "b000000000000001"
    aws.put_log("s1", "first line")
    cursor = aws.logs.get_log_events(
        logGroupName=BATCH_LOGS, logStreamName="batch/fse/s1", startFromHead=True
    )["nextForwardToken"]
    aws.put_log("s1", "second line")
    h.insert(jid, h.request(("report",), ("--run=x",)), external_id=task_arn("s1"), cursor=cursor)
    (h.data_dir / "ui" / "jobs" / f"{jid}.log").write_text("earlier line\nfirst line\n")
    add_describe(aws.ecs, "s1", "STOPPED", exit_code=0)
    done = await h.resume(jid)
    lines = h.lines(jid)
    assert done["status"] == "succeeded", lines
    assert lines[:2] == ["earlier line", "first line"]
    assert lines.count("first line") == 1 and "second line" in lines
    aws.ecs.assert_no_pending_responses()


@pytest.mark.asyncio
async def test_outcome_missing_is_interrupted_and_releases_its_own_lock(
    tmp_path: Path, aws: Aws, fake_time: FakeTime
) -> None:
    h = Harness(tmp_path, aws, fake_time)
    jid = "b000000000000001"
    h.insert(jid, h.request(("pull",), ("--start=2026-10-06",)), external_id=task_arn("g1"))
    aws.put_lock(jid)
    add_missing(aws.ecs, "g1")
    done = await h.resume(jid)
    assert (done["status"], done["exit_code"]) == ("interrupted", None)
    assert (
        f"task g1 is no longer known to ECS; its log is in {BATCH_LOGS} stream batch/fse/g1"
        in h.lines(jid)
    )
    assert aws.lock_body() is None


@pytest.mark.asyncio
async def test_outcome_lost_contact_for_15_minutes_is_failed(
    tmp_path: Path, aws: Aws, fake_time: FakeTime
) -> None:
    h = Harness(tmp_path, aws, fake_time)
    jid = "b000000000000001"
    h.insert(jid, h.request(("report",), ("--run=x",)), external_id=task_arn("l1"))
    for _ in range(40):
        aws.ecs.add_client_error(
            "describe_tasks", service_error_code="ThrottlingException", http_status_code=400
        )
    done = await h.resume(jid)
    assert (done["status"], done["exit_code"]) == ("failed", None)
    assert "error: lost contact with ECS for 15 min; task l1 may still run" in h.lines(jid)
    assert sum(fake_time.sleeps) >= 15 * 60 - 60


@pytest.mark.asyncio
async def test_outcome_access_denied_is_failed_at_once(
    tmp_path: Path, aws: Aws, fake_time: FakeTime
) -> None:
    h = Harness(tmp_path, aws, fake_time)
    jid = "b000000000000001"
    h.insert(jid, h.request(("report",), ("--run=x",)), external_id=task_arn("ad"))
    aws.ecs.add_client_error(
        "describe_tasks", service_error_code="AccessDeniedException", http_status_code=400
    )
    done = await h.resume(jid)
    assert (done["status"], done["exit_code"]) == ("failed", None)
    assert any(
        ln.startswith("error: access denied by AWS (AccessDeniedException)") for ln in h.lines(jid)
    )
    aws.ecs.assert_no_pending_responses()


@pytest.mark.asyncio
async def test_no_task_found_for_an_old_row_is_interrupted_and_its_orphaned_lock_deleted(
    tmp_path: Path, aws: Aws, fake_time: FakeTime
) -> None:
    h = Harness(tmp_path, aws, fake_time)
    jid = "b000000000000001"
    h.insert(jid, h.request(("pull",), ("--start=2026-10-06",)), age_s=600)
    aws.put_lock(jid)
    add_list_tasks(aws.ecs, jid, "RUNNING", [])
    add_list_tasks(aws.ecs, jid, "STOPPED", [])
    done = await h.resume(jid)
    assert (done["status"], done["exit_code"]) == ("interrupted", None)
    assert "no ECS task was started for this job (ListTasks found none)" in h.lines(jid)
    assert aws.lock_body() is None
    assert 30.0 not in fake_time.sleeps  # older than 120 s: no second lookup
    aws.ecs.assert_no_pending_responses()


@pytest.mark.asyncio
async def test_no_task_found_leaves_a_lock_that_names_another_job(
    tmp_path: Path, aws: Aws, fake_time: FakeTime
) -> None:
    h = Harness(tmp_path, aws, fake_time)
    jid = "b000000000000001"
    h.insert(jid, h.request(("pull",), ("--start=2026-10-06",)), age_s=600)
    aws.put_lock("c000000000000009")
    add_list_tasks(aws.ecs, jid, "RUNNING", [])
    add_list_tasks(aws.ecs, jid, "STOPPED", [])
    assert (await h.resume(jid))["status"] == "interrupted"
    assert "job=c000000000000009" in (aws.lock_body() or "")


@pytest.mark.asyncio
async def test_no_external_id_but_list_tasks_stopped_finds_it(
    tmp_path: Path, aws: Aws, fake_time: FakeTime
) -> None:
    h = Harness(tmp_path, aws, fake_time)
    jid = "b000000000000001"
    h.insert(jid, h.request(("report",), ("--run=x",)), age_s=600)
    add_list_tasks(aws.ecs, jid, "RUNNING", [])
    add_list_tasks(aws.ecs, jid, "STOPPED", ["f1"])
    add_describe(aws.ecs, "f1", "STOPPED", exit_code=4)
    done = await h.resume(jid)
    assert (done["status"], done["exit_code"]) == ("failed", 4)
    assert done["external_id"] == task_arn("f1")
    aws.ecs.assert_no_pending_responses()


@pytest.mark.asyncio
async def test_a_young_row_is_looked_up_again_after_30_s_and_keeps_its_lock(
    tmp_path: Path, aws: Aws, fake_time: FakeTime
) -> None:
    h = Harness(tmp_path, aws, fake_time, poll_status_s=1e9)
    jid = "b000000000000001"
    h.insert(jid, h.request(("pull",), ("--start=2026-10-06",)), age_s=10)
    aws.put_lock(jid)
    add_list_tasks(aws.ecs, jid, "RUNNING", [])
    add_list_tasks(aws.ecs, jid, "STOPPED", [])
    add_list_tasks(aws.ecs, jid, "RUNNING", ["y1"])
    add_describe(aws.ecs, "y1", "RUNNING")
    h.jobs.resume_remote()
    await h.until(lambda: "task y1: RUNNING" in h.lines(jid))
    assert 30.0 in fake_time.sleeps
    await h.jobs.shutdown()
    job = h.jobs.get(jid)
    assert job is not None and job["status"] == "running"
    assert job["external_id"] == task_arn("y1")
    assert f"job={jid}" in (aws.lock_body() or "")
    aws.ecs.assert_no_pending_responses()


@pytest.mark.asyncio
async def test_resume_after_stopped_leaves_a_lock_that_names_another_job(
    tmp_path: Path, aws: Aws, fake_time: FakeTime
) -> None:
    h = Harness(tmp_path, aws, fake_time)
    jid = "b000000000000001"
    h.insert(jid, h.request(("pull",), ("--start=2026-10-06",)), external_id=task_arn("o1"))
    aws.put_lock("c000000000000009")
    add_describe(aws.ecs, "o1", "STOPPED", exit_code=0)
    assert (await h.resume(jid))["status"] == "succeeded"
    assert "job=c000000000000009" in (aws.lock_body() or "")


# ================================================================ slots and startedBy


class _GateExecutor:
    """A remote executor whose jobs run until the test opens their gate."""

    name = "ecs"
    survives_restart = True

    def __init__(self) -> None:
        self.gates: dict[str, asyncio.Event] = {}
        self.started: list[str] = []

    async def run(self, job_id: str, *_a: Any, **_k: Any) -> int:
        self.started.append(job_id)
        await self.gates.setdefault(job_id, asyncio.Event()).wait()
        return 0

    async def cancel(self, job_id: str) -> bool:
        return False

    async def resume(self, *_a: Any) -> int:
        return 0

    async def find(self, job_id: str) -> str | None:
        return None

    async def abandon(self, *_a: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_ecs_slots_queue_new_jobs_and_never_take_the_heavy_slot(
    tmp_path: Path, aws: Aws, fake_time: FakeTime
) -> None:
    gate = _GateExecutor()
    h = Harness(
        tmp_path,
        aws,
        fake_time,
        heavy_slots=1,
        ecs_slots=1,
        executors={"local": LocalExecutor(), "ecs": gate},
    )
    a = _submit(h, h.request(("report",), ("--run=a",)))
    b = _submit(h, h.request(("report",), ("--run=b",)))
    await h.until(lambda: a in gate.started)
    await asyncio.sleep(0.05)
    assert h.jobs.get(b)["status"] == "queued"  # type: ignore[index]
    local = JobRequest(
        program="fse",
        command=("scan-secrets",),
        argv=(sys.executable, "-c", "print('local ok')"),
        placement="local",
        title="heavy local",
        heavy=True,
    )
    c = _submit(h, local)  # the heavy slot is free: the ECS job does not hold it
    assert (await asyncio.wait_for(h.jobs.wait(c), 10))["status"] == "succeeded"
    gate.gates[a].set()
    await h.until(lambda: b in gate.started)
    gate.gates[b].set()
    assert (await h.jobs.wait(b))["status"] == "succeeded"


def test_started_by_is_within_the_ecs_character_set() -> None:
    value = started_by("a000000000000001")
    assert value == "fse-ui_a000000000000001"
    assert re.fullmatch(r"[A-Za-z0-9_/-]{1,36}", value)


def _card_body() -> dict[str, Any]:
    return {
        "source": {
            "url": "https://x.com/trader/status/1",
            "author": "trader",
            "post_id": "1",
            "text": "10/21 EMA cross, Supertrend up, buy the pullback.",
        },
        "spec": EXAMPLE_SPEC,
        "hypothesis": "The cross with Supertrend context lifts the 2R hit rate.",
        "design": {
            "symbols": ["MNQ", "MES"],
            "timeframe": "1m",
            "design_range": ["2026-01-05", "2026-06-17"],
            "holdout_range": ["2026-06-18", "2026-10-01"],
            "judged_at": "2R",
            "k": 2,
        },
    }


def test_a_card_backtest_runs_on_fargate_at_the_smaller_size(
    aws_console: Callable[..., Console],
    aws: Aws,
    job_ids: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fse_ui.strategy_cards as cards

    class Frozen(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return datetime(2026, 10, 15, 3, 4, 5, tzinfo=tz)

    monkeypatch.setattr(cards, "datetime", Frozen)
    monkeypatch.setattr(cards.secrets, "token_hex", lambda _n: "0123456789abcdef")
    console = aws_console()
    settings = console.app.state.settings
    card_id = "c0123456789abcdef"
    stamp = "20261015T030405000000Z"
    nonce = "0123456789abcdef"
    leaf = f"{card_id}-{stamp}-{nonce}"
    config = settings.configs_dir / "cards" / f"{leaf}.yaml"
    out = settings.runs_dir / f"card-{leaf}"
    jid = "a000000000000001"
    cmd = [
        "batch",
        "backtest-plugin",
        f"--config={config}",
        "--plugin=rules",
        "--first=2026-01-05",
        "--last=2026-06-17",
        f"--out={out}",
        f"--recordings-s3=s3://{BUCKET}/",
        "--history=1",
        "--lag-s=2",
    ]
    add_run_task(
        aws.ecs,
        jid,
        "c1",
        cmd,
        env=[
            {"name": "FSE_SYNC_CONFIGS", "value": "1"},
            {"name": "FSE_CACHE_PREFIXES", "value": CARD_CACHE_PREFIXES},
            {"name": "FSE_CACHE_BACKEND", "value": CARD_CACHE_BACKEND},
        ],
        size={
            "cpu": BACKTEST_CPU,
            "memory": BACKTEST_MEMORY,
            "ephemeralStorage": {"sizeInGiB": BACKTEST_EPHEMERAL_GIB},
        },
    )
    add_describe(aws.ecs, "c1", "RUNNING")
    add_describe(aws.ecs, "c1", "STOPPED", exit_code=0)
    aws.put_log("c1", "replay 1/1 starting")
    created = console.post("/api/cards", json=_card_body())
    assert created.status_code == 200, created.text
    assert created.json()["id"] == card_id
    days = session_days(settings.project_dir, date(2026, 1, 5), date(2026, 6, 17))
    days += sessions_before(settings.project_dir, date(2026, 1, 5), 1)
    for day in days:
        aws.s3.put_object(
            Bucket=BUCKET, Key=f"cache/bars/MNQ/60s/{day.isoformat()}.parquet", Body=b"x"
        )
    aws.s3.put_object(Bucket=BUCKET, Key="cache/catalog.sqlite", Body=b"catalog")
    posted = console.post(f"/api/cards/{card_id}/backtest", json={})
    assert posted.status_code == 200, posted.text
    assert posted.json()["job"]["executor"] == "ecs"
    argv = " ".join(posted.json()["job"]["argv"])
    assert "--cache-dir" not in argv
    assert "--recordings-root" not in argv
    assert f"--recordings-s3=s3://{BUCKET}/" in argv
    assert not out.exists()
    job = wait_job(console, jid)
    assert job["status"] == "succeeded", _log(console, jid)
    staged = aws.s3.get_object(Bucket=BUCKET, Key=f"configs/cards/{leaf}.yaml")
    assert b"rules" in staged["Body"].read()
    aws.ecs.assert_no_pending_responses()
    assert job_ids[0] == jid


def test_a_card_reads_trades_from_s3_when_the_folder_is_not_local(
    aws_console: Callable[..., Console], aws: Aws
) -> None:
    console = aws_console()
    created = console.post("/api/cards", json=_card_body())
    assert created.status_code == 200, created.text
    card_id = created.json()["id"]
    name = "card-remote-1"
    missing = console.app.state.settings.runs_dir / name
    aws.s3.put_object(
        Bucket=BUCKET,
        Key=f"runs/{name}/trades.json",
        Body=b'[{"net": "1.50", "r_multiple": "2"}]',
    )
    aws.s3.put_object(
        Bucket=BUCKET,
        Key=f"runs/{name}/run/plugins/rules/outcomes.jsonl",
        Body=(
            b'{"symbol":"MNQ","day":"2026-02-01","clock":"10:15","side":"long",'
            b'"mfe_r":3,"hit_1":true,"hit_2":true,"hit_3":false,"hit_4":false}\n'
        ),
    )
    console.app.state.db.execute(
        "UPDATE strategy_cards SET run_dir = ? WHERE id = ?", (str(missing), card_id)
    )
    view = console.get(f"/api/cards/{card_id}/render").json()
    assert view["equity"]["trades"] == 1
    assert view["equity"]["net"] == "1.50"
    assert view["probabilities"][1]["label"] == "2R"
    assert view["probabilities"][1]["pct"] == 100.0
    assert not missing.exists()
