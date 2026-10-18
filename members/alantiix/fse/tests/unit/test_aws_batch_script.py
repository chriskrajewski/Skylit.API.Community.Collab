"""Operator batch scripts (scripts/aws-batch.sh, scripts/aws-seed-cache.sh) with a stub ``aws``.

The stub records one JSON line per call and answers ``ecs run-task``, ``ecs wait``,
``ecs describe-tasks``, ``logs tail`` and the ``s3api`` lock calls from environment
variables. Nothing reaches AWS: the stub is first on PATH and every input is set.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
BATCH = SCRIPTS / "aws-batch.sh"
SEED = SCRIPTS / "aws-seed-cache.sh"
SEED_EFS = SCRIPTS / "aws-seed-efs-cache.sh"
TASK_ARN = "arn:aws:ecs:us-east-1:123456789012:task/fse/0123456789abcdef"
BUCKET = "skylit-fse-data-test"

_EFS_TD = {
    "taskDefinition": {
        "family": "fse-batch",
        "revision": 26,
        "volumes": [
            {
                "name": "fse-cache",
                "efsVolumeConfiguration": {
                    "fileSystemId": "fs-0123456789abcdef0",
                    "authorizationConfig": {"accessPointId": "fsap-0123456789abcdef0"},
                },
            }
        ],
        "containerDefinitions": [
            {
                "name": "fse",
                "mountPoints": [
                    {
                        "sourceVolume": "fse-cache",
                        "containerPath": "/home/fse/.skylit-fse/cache",
                    }
                ],
                "environment": [{"name": "FSE_CACHE_BACKEND", "value": "efs"}],
            }
        ],
    }
}

_STUB_AWS = """#!{python}
import json, os, sys, time
args = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as fh:
    fh.write(json.dumps(args) + "\\n")
op = args[:2]
if op == ["ecs", "run-task"]:
    print(json.dumps({{"tasks": [{{"taskArn": os.environ["STUB_TASK_ARN"]}}], "failures": []}}))
elif op == ["ecs", "wait"] and args[2] == "tasks-stopped":
    sleep_s = float(os.environ.get("STUB_WAIT_SLEEP", "0"))
    if sleep_s:
        open(os.environ["STUB_WAITING"], "w").close()
        time.sleep(sleep_s)
elif op == ["ecs", "describe-task-definition"]:
    print(os.environ.get("STUB_TASK_DEFINITION_JSON", "{{}}"))
elif op == ["ecs", "describe-tasks"]:
    code = int(os.environ.get("STUB_EXIT_CODE", "0"))
    status = os.environ.get("STUB_TASK_STATUS", "STOPPED")
    task = {{"lastStatus": status, "containers": [{{"name": "fse", "exitCode": code}}]}}
    if status != "STOPPED":
        task["containers"][0].pop("exitCode", None)
    print(json.dumps({{"tasks": [task]}}))
elif op == ["logs", "tail"]:
    print("2026-01-02T00:00:00 fse: hello from the task")
elif op == ["s3api", "put-object"] and os.environ.get("STUB_LOCK_HELD") == "1":
    sys.stderr.write(
        "An error occurred (PreconditionFailed) when calling the PutObject operation: "
        "At least one of the pre-conditions you specified did not hold\\n"
    )
    sys.exit(254)
elif op == ["s3api", "get-object"]:
    if os.environ.get("STUB_NO_LOCK") == "1":
        sys.stderr.write("An error occurred (NoSuchKey)\\n")
        sys.exit(254)
    with open(args[-1], "w") as out:
        out.write(os.environ.get("STUB_LOCK_BODY", "user=other\\n"))
    print("{{}}")
elif op[0] in ("s3api", "ecs"):
    print("{{}}")
"""

_STUB_FAIL = '#!/bin/sh\necho "$0 must not run in tests" >&2\nexit 1\n'


class Operator:
    """A temporary PATH with stub aws, terraform and pgrep, and the batch inputs set."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        (self.bin / "aws").write_text(_STUB_AWS.format(python=sys.executable))
        (self.bin / "terraform").write_text(_STUB_FAIL)
        (self.bin / "pgrep").write_text("#!/bin/sh\nexit 1\n")
        (self.bin / "python3").symlink_to(sys.executable)
        for name in ("aws", "terraform", "pgrep"):
            (self.bin / name).chmod(0o755)
        self.log = tmp_path / "aws-calls.jsonl"
        self.waiting = tmp_path / "waiting"
        self.cache = tmp_path / "cache"

    def env(self, **extra: str) -> dict[str, str]:
        env = dict(os.environ)
        env.update(
            PATH=f"{self.bin}{os.pathsep}{env.get('PATH', '/usr/bin:/bin')}",
            STUB_LOG=str(self.log),
            STUB_TASK_ARN=TASK_ARN,
            STUB_WAITING=str(self.waiting),
            STUB_TASK_DEFINITION_JSON=json.dumps(_EFS_TD),
            AWS_PROFILE="stub",
            AWS_REGION="us-east-1",
            FSE_SUBNETS="subnet-aaa,subnet-bbb",
            FSE_SECURITY_GROUP="sg-batch",
            DATA_BUCKET=BUCKET,
            FSE_CACHE_DIR=str(self.cache),
            FSE_LOG_DRAIN_S="10",
            FSE_SEED_POLL_S="0",
        )
        env.update(extra)
        return env

    def run(self, script: Path, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(script), *args],
            env=self.env(**extra),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def ops(self) -> list[str]:
        return [" ".join(argv[:2]) for argv in self.calls()]

    def call(self, op: str) -> list[str]:
        matches = [argv for argv in self.calls() if " ".join(argv[:2]) == op]
        assert len(matches) == 1, (op, self.ops())
        return matches[0]


def _value(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


@pytest.fixture
def op(tmp_path: Path) -> Operator:
    return Operator(tmp_path)


# ------------------------------------------------------------------ aws-batch.sh


def test_run_task_uses_fargate_spot_with_public_ip(op: Operator) -> None:
    result = op.run(BATCH, "backtest", "--config", "configs/x.yaml")

    assert result.returncode == 0, result.stderr
    run = op.call("ecs run-task")
    assert _value(run, "--cluster") == "fse"
    assert _value(run, "--task-definition") == "fse-batch"
    assert _value(run, "--capacity-provider-strategy") == "capacityProvider=FARGATE_SPOT,weight=1"
    assert "--launch-type" not in run
    assert _value(run, "--network-configuration") == (
        "awsvpcConfiguration={subnets=[subnet-aaa,subnet-bbb],"
        "securityGroups=[sg-batch],assignPublicIp=ENABLED}"
    )


def test_override_command_keeps_args_with_spaces(op: Operator) -> None:
    result = op.run(BATCH, "pull", "--from", "2026-01-02", "a b")

    assert result.returncode == 0, result.stderr
    overrides = json.loads(_value(op.call("ecs run-task"), "--overrides"))
    assert overrides == {
        "containerOverrides": [
            {"name": "fse", "command": ["batch", "pull", "--from", "2026-01-02", "a b"]}
        ]
    }


@pytest.mark.parametrize("code", [0, 3])
def test_exit_code_propagates(op: Operator, code: int) -> None:
    result = op.run(BATCH, "pull", STUB_EXIT_CODE=str(code))

    assert result.returncode == code, result.stderr
    assert ("needs a person" in result.stderr) == (code == 3)
    assert "hello from the task" in result.stdout
    task_id = TASK_ARN.rsplit("/", 1)[1]
    tail = op.call("logs tail")
    assert tail[2] == "/fse/batch"
    assert "--follow" in tail
    assert _value(tail, "--log-stream-name-prefix") == f"batch/fse/{task_id}"
    assert op.ops().count("ecs wait") == 2
    assert "ecs stop-task" not in op.ops()


def test_backtest_failure_is_not_a_person_message(op: Operator) -> None:
    result = op.run(BATCH, "backtest", STUB_EXIT_CODE="3")

    assert result.returncode == 3
    assert "needs a person" not in result.stderr


def test_pull_takes_lock_and_releases_it(op: Operator) -> None:
    result = op.run(BATCH, "pull", "--start", "2026-01-02")

    assert result.returncode == 0, result.stderr
    ops = op.ops()
    assert ops.index("s3api put-object") < ops.index("ecs run-task")
    assert ops[-1] == "s3api delete-object"
    put = op.call("s3api put-object")
    assert _value(put, "--bucket") == BUCKET
    assert _value(put, "--key") == "locks/pull.lock"
    assert _value(put, "--if-none-match") == "*"
    delete = op.call("s3api delete-object")
    assert _value(delete, "--key") == "locks/pull.lock"


def test_held_lock_exits_75_without_run_task(op: Operator) -> None:
    result = op.run(BATCH, "pull", STUB_LOCK_HELD="1", STUB_LOCK_BODY="user=alice\nhost=mini\n")

    assert result.returncode == 75
    assert "user=alice" in result.stderr
    ops = op.ops()
    assert "ecs run-task" not in ops
    assert "s3api delete-object" not in ops


def test_backtest_takes_no_lock(op: Operator) -> None:
    result = op.run(BATCH, "backtest")

    assert result.returncode == 0, result.stderr
    assert not [o for o in op.ops() if o.startswith("s3api")]


def test_sigterm_stops_task_then_deletes_lock(op: Operator) -> None:
    proc = subprocess.Popen(
        ["bash", str(BATCH), "pull", "--start", "2026-01-02"],
        env=op.env(STUB_WAIT_SLEEP="30"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 20
        while not op.waiting.exists():
            assert proc.poll() is None, proc.communicate()
            assert time.monotonic() < deadline, "the wrapper never reached tasks-stopped"
            time.sleep(0.05)
        proc.send_signal(signal.SIGTERM)
        _, stderr = proc.communicate(timeout=20)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()

    assert proc.returncode == 143, stderr
    ops = op.ops()
    assert ops.index("ecs stop-task") < ops.index("s3api delete-object")
    stop = op.call("ecs stop-task")
    assert _value(stop, "--task") == TASK_ARN
    assert _value(stop, "--cluster") == "fse"
    assert "ecs describe-tasks" not in ops


def test_unlock_needs_yes(op: Operator) -> None:
    result = op.run(BATCH, "unlock")

    assert result.returncode == 2
    assert "s3api delete-object" not in op.ops()


def test_unlock_yes_deletes_lock(op: Operator) -> None:
    result = op.run(BATCH, "unlock", "--yes")

    assert result.returncode == 0, result.stderr
    assert op.ops() == ["s3api delete-object"]
    assert _value(op.call("s3api delete-object"), "--key") == "locks/pull.lock"


@pytest.mark.parametrize("args", [(), ("deploy",)])
def test_bad_command_prints_usage_and_calls_nothing(op: Operator, args: tuple[str, ...]) -> None:
    result = op.run(BATCH, *args)

    assert result.returncode == 2
    assert "scripts/aws-batch.sh pull" in result.stdout
    assert "set -euo" not in result.stdout
    assert op.calls() == []


# ------------------------------------------------------------- aws-seed-cache.sh


def test_seed_without_yes_calls_no_aws(op: Operator) -> None:
    op.cache.mkdir()
    result = op.run(SEED)

    assert result.returncode != 0
    assert "aws-seed-cache.sh --yes" in result.stdout
    assert op.calls() == []


def test_seed_refuses_while_a_pull_runs(op: Operator) -> None:
    op.cache.mkdir()
    (op.bin / "pgrep").write_text("#!/bin/sh\nexit 0\n")
    result = op.run(SEED, "--yes")

    assert result.returncode == 1
    assert "fse pull is running" in result.stderr
    assert op.calls() == []


def test_seed_syncs_cache_to_bucket(op: Operator) -> None:
    op.cache.mkdir()
    (op.cache / "window.parquet").write_bytes(b"x" * 10)
    result = op.run(SEED, "--yes")

    assert result.returncode == 0, result.stderr
    assert op.calls() == [
        ["s3", "sync", f"{op.cache}/", f"s3://{BUCKET}/cache/", "--only-show-errors"]
    ]


# --------------------------------------------------------- aws-seed-efs-cache.sh


def test_seed_efs_without_yes_calls_no_aws(op: Operator) -> None:
    result = op.run(SEED_EFS)

    assert result.returncode == 2
    assert "aws-seed-efs-cache.sh --yes" in result.stdout
    assert op.calls() == []


def test_seed_efs_uses_fargate_ondemand_and_owns_lock(op: Operator) -> None:
    result = op.run(SEED_EFS, "--yes")

    assert result.returncode == 0, result.stderr
    ops = op.ops()
    assert ops[0] == "ecs describe-task-definition"
    assert ops.index("s3api put-object") < ops.index("ecs run-task")
    assert ops[-1] == "s3api delete-object"
    run = op.call("ecs run-task")
    assert _value(run, "--capacity-provider-strategy") == "capacityProvider=FARGATE,weight=1"
    overrides = json.loads(_value(run, "--overrides"))
    assert overrides == {
        "containerOverrides": [
            {
                "name": "fse",
                "command": ["seed-efs-cache"],
                "environment": [{"name": "FSE_SEED_LOCK_EXTERNAL", "value": "1"}],
            }
        ]
    }
    assert "Fargate On-Demand" in result.stderr
    assert "ecs wait" not in ops
    assert any(o == "ecs describe-tasks" for o in ops)


def test_seed_efs_held_lock_exits_75_without_run_task(op: Operator) -> None:
    result = op.run(
        SEED_EFS, "--yes", STUB_LOCK_HELD="1", STUB_LOCK_BODY="user=seed-efs-cache\nhost=old\n"
    )

    assert result.returncode == 75
    assert "user=seed-efs-cache" in result.stderr
    assert "--force" in result.stderr
    ops = op.ops()
    assert "ecs run-task" not in ops
    assert "s3api delete-object" not in ops


def test_seed_efs_force_clears_stale_lock(op: Operator) -> None:
    result = op.run(
        SEED_EFS,
        "--yes",
        "--force",
        STUB_LOCK_BODY="user=seed-efs-cache\nhost=dead-task\n",
    )

    assert result.returncode == 0, result.stderr
    ops = op.ops()
    # force: get+delete stale lock, then put, run-task, cleanup delete
    assert ops.count("s3api delete-object") == 2
    assert ops.index("s3api delete-object") < ops.index("s3api put-object")
    assert "ecs run-task" in ops
    assert "Deleting stale pull lock" in result.stderr
