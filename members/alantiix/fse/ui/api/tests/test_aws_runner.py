"""SsmRunnerController (design.md 10.7) against moto SSM and CloudWatch Logs."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError

from fse_ui.aws.runner_ssm import (
    CHANGING_STATE,
    CONFIG_PARAM,
    MODE_PARAM,
    RUNNING_ALREADY,
    SsmRunnerController,
)
from fse_ui.configs import ConfigStore
from fse_ui.db import Db
from fse_ui.runner import RunnerError, set_runner_settings
from fse_ui.secrets_store import Redactor
from tests.aws_support import INSTANCE, RUNNER_LOGS, Aws, FakeTime
from tests.conftest import PROJECT, Console

SECRET = "canary-RUNNER-secret-91c2"
OLD_CONFIG = "/home/fse/.skylit-fse/configs/runner/old/current.yaml"


class Host:
    """The controller plus the files the host hooks would write."""

    def __init__(self, tmp_path: Path, aws: Aws, fake: FakeTime) -> None:
        data = tmp_path / ".skylit-fse"
        self.db = Db(data / "ui" / "ui.sqlite")
        self.store = ConfigStore(data / "configs")
        self.store.seed(PROJECT)
        self.state_file = data / "host" / "runner-state.json"
        self.held_file = data / "host" / "runner-held"
        self.state_file.parent.mkdir(parents=True)
        self.aws, self.fake = aws, fake
        aws.ssm.put_parameter(Name=MODE_PARAM, Value="run", Type="String")
        aws.ssm.put_parameter(Name=CONFIG_PARAM, Value=OLD_CONFIG, Type="String")
        self.runner = SsmRunnerController(
            self.db,
            self.store,
            aws.ssm,
            aws.logs,
            instance_id=INSTANCE,
            document="fse-runner-control",
            log_group=RUNNER_LOGS,
            state_file=self.state_file,
            held_file=self.held_file,
            enable_live=False,
            redact=lambda: Redactor([SECRET]),
            clock=fake.clock,
            sleep=fake.sleep,
        )

    def report(self, state: str, *, at: float | None = None, result: str = "success") -> None:
        self.state_file.write_text(
            json.dumps(
                {
                    "state": state,
                    "at": self.fake.now if at is None else at,
                    "exit_status": "0",
                    "result": result,
                }
            )
        )

    def commands(self) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = self.aws.ssm.list_commands()["Commands"]
        return found

    def param(self, name: str) -> tuple[str, int]:
        p = self.aws.ssm.get_parameter(Name=name)["Parameter"]
        return str(p["Value"]), int(p["Version"])


@pytest.fixture
def host(tmp_path: Path, aws: Aws, fake_time: FakeTime) -> Host:
    fake_time.now = time.time()
    return Host(tmp_path, aws, fake_time)


@pytest.mark.asyncio
async def test_start_publishes_mode_and_config_then_sends_one_start(host: Host) -> None:
    host.report("stopped")
    host.fake.now += 1
    status = await host.runner.start()
    assert status["state"] == "starting" and status["pending"] == "start"
    assert host.param(MODE_PARAM) == ("run", 2)
    assert host.param(CONFIG_PARAM)[0] == str(host.store.current_path("runner", "runner"))
    assert host.param(CONFIG_PARAM)[0].endswith("/.skylit-fse/configs/runner/runner/current.yaml")
    [cmd] = host.commands()
    assert cmd["DocumentName"] == "fse-runner-control"
    assert cmd["Parameters"] == {"action": ["start"]}
    assert cmd["InstanceIds"] == [INSTANCE]
    await host.runner.close()


@pytest.mark.asyncio
async def test_start_on_a_running_runner_is_refused_with_nothing_written(host: Host) -> None:
    host.report("running")
    with pytest.raises(RunnerError, match="use Restart to apply new settings") as exc:
        await host.runner.start()
    assert str(exc.value) == RUNNING_ALREADY
    assert host.param(MODE_PARAM) == ("run", 1) and host.param(CONFIG_PARAM)[1] == 1
    assert host.commands() == []
    # stop and restart are allowed on a running runner
    await host.runner.stop()
    await host.runner.restart()
    assert [c["Parameters"]["action"] for c in host.commands()] == [["stop"], ["restart"]]
    await host.runner.close()


@pytest.mark.asyncio
async def test_start_while_a_start_is_pending_is_refused(host: Host) -> None:
    host.report("stopped")
    host.fake.now += 1
    await host.runner.start()
    host.fake.now += 30  # within 120 s, and the host has not reported since
    assert (await host.runner.status())["state"] == "starting"
    with pytest.raises(RunnerError, match="running already"):
        await host.runner.start()
    assert len(host.commands()) == 1
    await host.runner.close()


@pytest.mark.asyncio
async def test_restart_keeps_pending_through_execstoppost_stopped(host: Host) -> None:
    """systemctl restart writes stopped before the new container is up; that must not
    clear pending or a second Restart stacks (order-mode / Live restart storm)."""
    host.report("running")
    host.fake.now += 1
    await host.runner.restart()
    assert (await host.runner.status())["state"] == "starting"
    host.fake.now += 2
    host.report("stopped", result="exit-code")  # ExecStopPost mid-restart
    status = await host.runner.status()
    assert status["state"] == "starting" and status["pending"] == "restart"
    with pytest.raises(RunnerError, match=CHANGING_STATE):
        await host.runner.restart()
    assert len(host.commands()) == 1
    host.fake.now += 5
    host.report("running")  # ExecStartPost
    status = await host.runner.status()
    assert status["state"] == "running" and status["pending"] is None
    await host.runner.close()


@pytest.mark.asyncio
async def test_second_restart_while_starting_is_refused(host: Host) -> None:
    host.report("running")
    host.fake.now += 1
    await host.runner.restart()
    with pytest.raises(RunnerError, match=CHANGING_STATE):
        await host.runner.restart()
    assert len(host.commands()) == 1
    await host.runner.close()


@pytest.mark.asyncio
async def test_state_file_mapping(host: Host) -> None:
    status = await host.runner.status()
    assert status["state"] == "unknown"
    assert status["error"] == "the host has not reported the runner state yet"
    host.state_file.write_text("{not json")
    assert (await host.runner.status())["state"] == "unknown"
    host.report("running")
    status = await host.runner.status()
    assert status["state"] == "running" and status["controller"] == "systemd"
    assert status["pid"] is None and status["started_at"] == host.fake.now
    host.report("stopped")
    assert (await host.runner.status())["state"] == "stopped"
    host.report("stopped", result="exit-code")
    assert (await host.runner.status())["state"] == "failed"
    host.report("running")
    host.fake.now += 1
    await host.runner.stop()
    assert (await host.runner.status())["state"] == "stopping"
    host.fake.now += 5
    host.report("stopped")  # the host reported after the command
    status = await host.runner.status()
    assert status["state"] == "stopped" and status["pending"] is None
    await host.runner.start()
    host.fake.now += 121  # the pending window is over
    assert (await host.runner.status())["state"] == "stopped"
    await host.runner.close()


@pytest.mark.asyncio
async def test_held_is_reported_only_when_stopped(host: Host) -> None:
    host.held_file.write_text("")
    host.report("stopped")
    assert (await host.runner.status())["held"] is True
    host.report("running")
    assert (await host.runner.status())["held"] is False


@pytest.mark.asyncio
async def test_live_and_non_paper_order_modes_are_refused(host: Host) -> None:
    with pytest.raises(RunnerError, match="never runs fse live"):
        SsmRunnerController(
            host.db,
            host.store,
            host.aws.ssm,
            host.aws.logs,
            instance_id=INSTANCE,
            document="fse-runner-control",
            log_group=RUNNER_LOGS,
            state_file=host.state_file,
            enable_live=True,
            redact=lambda: Redactor([]),
        )
    host.report("stopped")
    host.db.set_kv(
        "runner",
        {"mode": "live", "runner_config": "runner", "strategy_config": "playbook_baseline"},
    )
    with pytest.raises(RunnerError, match="not allowed"):
        await host.runner.start()
    text = host.store.get("strategy", "playbook_baseline")["text"]
    ok, _ = host.store.save(
        "strategy",
        "practice_cfg",
        text.replace("order_mode: paper", "order_mode: practice"),
        author="op",
        create=True,
    )
    assert ok.ok
    set_runner_settings(
        host.db,
        mode="paper",
        runner_config="runner",
        strategy_config="practice_cfg",
        store=host.store,
    )
    with pytest.raises(RunnerError, match="only paper runs are allowed"):
        await host.runner.start()
    assert host.commands() == [] and host.param(MODE_PARAM) == ("run", 1)


@pytest.mark.asyncio
async def test_paper_mode_publishes_the_strategy_config(host: Host) -> None:
    host.report("stopped")
    host.fake.now += 1
    set_runner_settings(
        host.db,
        mode="paper",
        runner_config="runner",
        strategy_config="playbook_baseline",
        store=host.store,
    )
    await host.runner.start()
    assert host.param(MODE_PARAM)[0] == "paper"
    assert host.param(CONFIG_PARAM)[0] == str(
        host.store.current_path("strategy", "playbook_baseline")
    )
    await host.runner.close()


@pytest.mark.asyncio
async def test_send_command_failure_restores_the_previous_parameters(host: Host) -> None:
    def refuse(**_: Any) -> None:
        raise ClientError(
            {"Error": {"Code": "InvalidInstanceId", "Message": "not managed"}}, "SendCommand"
        )

    host.aws.ssm.meta.events.register("before-call.ssm.SendCommand", refuse)
    host.report("stopped")
    with pytest.raises(RunnerError) as exc:
        await host.runner.start()
    assert str(exc.value) == "cannot send start to the runner host: InvalidInstanceId"
    assert host.param(MODE_PARAM)[0] == "run"
    assert host.param(CONFIG_PARAM)[0] == OLD_CONFIG
    assert host.commands() == []


@pytest.mark.asyncio
async def test_a_failed_restore_is_named_in_the_error(host: Host) -> None:
    calls = {"put": 0}

    def refuse_send(**_: Any) -> None:
        raise ClientError({"Error": {"Code": "Throttling", "Message": "x"}}, "SendCommand")

    def refuse_restore(**_: Any) -> None:
        calls["put"] += 1
        if calls["put"] > 2:  # the two publishes pass; the restores fail
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "x"}}, "PutParameter")

    host.aws.ssm.meta.events.register("before-call.ssm.SendCommand", refuse_send)
    host.aws.ssm.meta.events.register("before-call.ssm.PutParameter", refuse_restore)
    host.report("stopped")
    with pytest.raises(RunnerError) as exc:
        await host.runner.restart()
    assert "cannot send restart to the runner host: Throttling" in str(exc.value)
    assert "could not be restored: /fse/runner_mode (AccessDenied)" in str(exc.value)


@pytest.mark.asyncio
async def test_last_command_output_is_polled_and_redacted(host: Host) -> None:
    def inject(parsed: dict[str, Any], **_: Any) -> None:
        parsed["StandardOutputContent"] = f"restarting fse-runner\ntoken {SECRET}\n"
        parsed["Status"] = "Success"

    host.aws.ssm.meta.events.register("after-call.ssm.GetCommandInvocation", inject)
    host.report("running")
    await host.runner.restart()
    for task in list(host.runner._pollers):
        await task
    last = (await host.runner.status())["last_command"]
    assert last["action"] == "restart" and last["status"] == "Success"
    assert last["output"] == ["restarting fse-runner", "token [redacted]"]


@pytest.mark.asyncio
async def test_log_tail_is_redacted_and_close_sends_nothing(host: Host) -> None:
    host.aws.logs.put_log_events(
        logGroupName=RUNNER_LOGS,
        logStreamName="runner",
        logEvents=[
            {"timestamp": int(time.time() * 1000), "message": "runner started"},
            {"timestamp": int(time.time() * 1000) + 1, "message": f"key={SECRET}"},
        ],
    )
    host.report("running")
    status = await host.runner.status()
    assert status["log"] == ["runner started", "key=[redacted]"]
    assert SECRET not in json.dumps(status)
    await host.runner.close()
    assert host.commands() == []


@pytest.mark.asyncio
async def test_log_read_errors_are_reported_not_raised(
    tmp_path: Path, aws: Aws, fake_time: FakeTime
) -> None:
    host = Host(tmp_path, aws, fake_time)
    host.runner._log_group = "/fse/missing"
    host.report("running")
    status = await host.runner.status()
    assert status["log"] == []
    assert status["error"] == "cannot read /fse/missing: ResourceNotFoundException"


# ---------------------------------------------------------------- through the API


def _ssm_console(aws_console: Callable[..., Console], aws: Aws) -> Console:
    aws.ssm.put_parameter(Name=MODE_PARAM, Value="run", Type="String")
    aws.ssm.put_parameter(Name=CONFIG_PARAM, Value=OLD_CONFIG, Type="String")
    return aws_console(runner_controller="ssm", runner_instance_id=INSTANCE)


def test_restart_route_publishes_then_sends_exactly_one_restart(
    aws_console: Callable[..., Console], aws: Aws
) -> None:
    console = _ssm_console(aws_console, aws)
    state = console.app.state.settings.runner_state_file
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"state": "running", "at": time.time() - 5, "result": "success"}))
    r = console.post("/api/runner/restart")
    assert r.status_code == 200, r.text
    assert r.json()["controller"] == "systemd" and r.json()["pending"] == "restart"
    [cmd] = aws.ssm.list_commands()["Commands"]
    assert cmd["Parameters"] == {"action": ["restart"]}
    mode = aws.ssm.get_parameter(Name=MODE_PARAM)["Parameter"]
    config = aws.ssm.get_parameter(Name=CONFIG_PARAM)["Parameter"]
    assert (mode["Value"], mode["Version"]) == ("run", 2)
    assert config["Version"] == 2 and config["Value"].endswith(
        "/configs/runner/runner/current.yaml"
    )
    assert cmd["RequestedDateTime"] >= max(mode["LastModifiedDate"], config["LastModifiedDate"])


def test_start_route_on_a_running_runner_is_409(
    aws_console: Callable[..., Console], aws: Aws
) -> None:
    console = _ssm_console(aws_console, aws)
    state = console.app.state.settings.runner_state_file
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"state": "running", "at": time.time(), "result": "success"}))
    r = console.post("/api/runner/start")
    assert r.status_code == 409 and r.json()["detail"] == RUNNING_ALREADY
    assert aws.ssm.list_commands()["Commands"] == []
    assert aws.ssm.get_parameter(Name=MODE_PARAM)["Parameter"]["Version"] == 1
