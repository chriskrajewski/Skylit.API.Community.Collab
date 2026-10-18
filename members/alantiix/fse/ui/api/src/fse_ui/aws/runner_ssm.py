"""The runner on AWS: the ``fse-runner`` systemd unit, controlled through the
``fse-runner-control`` SSM document (design.md 10.7).

``start``/``restart`` publish the mode and config path in SSM, then send one
command; the document uses ``systemctl --no-block``, so no request waits on
the container stop. ``status()`` never calls SSM: the state comes from the
file host hooks write (read-only here), the log tail from CloudWatch Logs.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fse_ui.aws.errors import AWS_ERRORS, error_code
from fse_ui.configs import ConfigStore
from fse_ui.db import Db
from fse_ui.jobs import Sleep, exit_meaning
from fse_ui.runner import RunnerError, runner_settings, runner_target

log = logging.getLogger(__name__)

MODE_PARAM = "/fse/runner_mode"
CONFIG_PARAM = "/fse/runner_config"
PENDING_S = 120.0
LOG_CACHE_S = 10.0
LOG_WARN_EVERY_S = 60.0
COMMAND_POLL_S = 1.0
COMMAND_CAP_S = 60.0
COMMAND_DONE = frozenset({"Success", "Failed", "Cancelled", "TimedOut", "Undeliverable"})
RUNNING_ALREADY = "the runner is running already; use Restart to apply new settings"
CHANGING_STATE = "the runner is changing state; retry"
NOT_REPORTED = "the host has not reported the runner state yet"


class SsmRunnerController:
    def __init__(
        self,
        db: Db,
        store: ConfigStore,
        ssm: Any,
        logs: Any,
        *,
        instance_id: str,
        document: str,
        log_group: str,
        state_file: Path,
        held_file: Path | None = None,
        enable_live: bool,
        redact: Callable[[], Callable[[str], str]],
        clock: Callable[[], float] = time.time,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if enable_live:  # the systemd runner only runs fse run or fse paper
            raise RunnerError("the SSM runner controller never runs fse live")
        self._db, self._store = db, store
        self._ssm, self._logs = ssm, logs
        self._instance, self._document, self._log_group = instance_id, document, log_group
        self._state_file = state_file
        self._held_file = held_file or state_file.with_name("runner-held")
        self._redact = redact
        self._clock, self._sleep = clock, sleep
        self._lock = asyncio.Lock()
        self._pending: dict[str, Any] | None = None
        self._last_command: dict[str, Any] | None = None
        self._log_cache: tuple[float, list[str], str | None] | None = None
        self._log_warned = 0.0
        self._pollers: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------ status

    def _read_state(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
        except OSError, ValueError:
            return None
        return data if isinstance(data, dict) else None

    def _log_tail(self) -> tuple[list[str], str | None]:
        now = self._clock()
        if self._log_cache is not None and now - self._log_cache[0] < LOG_CACHE_S:
            return self._log_cache[1], self._log_cache[2]
        try:
            resp = self._logs.get_log_events(
                logGroupName=self._log_group,
                logStreamName="runner",
                startFromHead=False,
                limit=200,
            )
        except AWS_ERRORS as exc:
            if now - self._log_warned >= LOG_WARN_EVERY_S:
                self._log_warned = now
                log.warning("cannot read %s: %s", self._log_group, error_code(exc))
            result: tuple[list[str], str | None] = (
                [],
                f"cannot read {self._log_group}: {error_code(exc)}",
            )
        else:
            redact = self._redact()
            lines = [
                redact(line)
                for event in resp.get("events", [])
                for line in str(event.get("message", "")).split("\n")
            ]
            result = (lines[-200:], None)
        self._log_cache = (now, *result)
        return result

    def _pending_done(self, pending: dict[str, Any], st: dict[str, Any] | None, now: float) -> bool:
        """Whether ``pending`` has finished (timeout, or the host report that ends it).

        ``systemctl restart`` writes ExecStopPost ``stopped`` before the new
        container is up. Clearing on that report made the console show
        stopped/failed mid-restart and accept another Restart (stop/start storm).
        Start/restart complete only on ``running``; stop completes on ``stopped``.
        """
        if now - pending["at"] > PENDING_S:
            return True
        if st is None or float(st.get("at") or 0) < pending["at"]:
            return False
        host_state = st.get("state")
        action = pending["action"]
        if action == "stop":
            return host_state == "stopped"
        if action in ("start", "restart"):
            return host_state == "running"
        return True

    async def status(self) -> dict[str, Any]:
        st = self._read_state()
        now = self._clock()
        error: str | None = None
        pending = self._pending
        if pending is not None and self._pending_done(pending, st, now):
            pending = self._pending = None
        state: str
        if pending is not None:
            state = "stopping" if pending["action"] == "stop" else "starting"
        elif st is None:
            state, error = "unknown", NOT_REPORTED
        elif st.get("state") == "running":
            state = "running"
        elif st.get("state") == "stopped":
            result = st.get("result")
            state = "stopped" if result in (None, "", "success") else "failed"
        else:
            state, error = "unknown", NOT_REPORTED
        exit_status = None if st is None else st.get("exit_status")
        code = int(exit_status) if isinstance(exit_status, str) and exit_status.isdigit() else None
        mode = str(runner_settings(self._db).get("mode") or "run")
        command = (mode,) if mode in {"run", "paper"} else ("run",)
        lines, log_error = await asyncio.to_thread(self._log_tail)
        return {
            "controller": "systemd",
            "state": state,
            "pid": None,
            "started_at": st.get("at") if st is not None and state == "running" else None,
            "last_exit_code": code,
            "last_exit_meaning": exit_meaning(code, command)
            if code is not None
            else (str(exit_status) if exit_status else None),
            "settings": runner_settings(self._db),
            "log": lines,
            "pending": None if pending is None else pending["action"],
            "last_command": self._last_command,
            "held": state == "stopped" and self._held_file.exists(),
            "error": error or log_error,
        }

    # ------------------------------------------------------------ actions

    async def start(self) -> dict[str, Any]:
        async with self._lock:
            current = await self.status()
            if current["state"] in ("running", "starting"):
                raise RunnerError(RUNNING_ALREADY)
            if current["state"] == "stopping":
                raise RunnerError(CHANGING_STATE)
            await self._apply("start")
        return await self.status()

    async def restart(self) -> dict[str, Any]:
        async with self._lock:
            current = await self.status()
            # A second Restart while start/restart is in flight stacked systemctl
            # restarts (Combine/Practice order-mode and Live → Restart storms).
            # Restart while stopping is allowed: it replaces the pending stop.
            if current["state"] == "starting":
                raise RunnerError(CHANGING_STATE)
            await self._apply("restart")
        return await self.status()

    async def stop(self) -> dict[str, Any]:
        async with self._lock:
            current = await self.status()
            if current["state"] == "stopping":
                raise RunnerError(CHANGING_STATE)
            self._sent("stop", await asyncio.to_thread(self._send, "stop"))
        return await self.status()

    async def close(self) -> None:
        # The systemd runner outlives the console: nothing is sent.
        for task in list(self._pollers):
            task.cancel()
            with contextlib.suppress(BaseException):
                await task

    async def _apply(self, action: str) -> None:
        mode, path = runner_target(self._db, self._store, enable_live=False)
        self._sent(action, await asyncio.to_thread(self._publish_and_send, action, mode, str(path)))

    def _sent(self, action: str, command_id: str) -> None:
        now = self._clock()
        self._pending = {"action": action, "at": now}
        self._last_command = {"action": action, "status": "Pending", "at": now, "output": []}
        task = asyncio.get_running_loop().create_task(self._poll_command(command_id, action))
        self._pollers.add(task)
        task.add_done_callback(self._pollers.discard)

    def _publish_and_send(self, action: str, mode: str, config: str) -> str:
        try:
            found = self._ssm.get_parameters(Names=[MODE_PARAM, CONFIG_PARAM])
        except AWS_ERRORS as exc:
            raise RunnerError(
                f"cannot send {action} to the runner host: {error_code(exc)}"
            ) from None
        previous = {p["Name"]: str(p["Value"]) for p in found.get("Parameters", [])}
        written: list[str] = []
        try:
            for name, value in ((MODE_PARAM, mode), (CONFIG_PARAM, config)):
                self._ssm.put_parameter(Name=name, Value=value, Type="String", Overwrite=True)
                written.append(name)
            return self._send(action)
        except RunnerError as exc:
            raise RunnerError(f"{exc}{self._restore(previous, written)}") from None
        except AWS_ERRORS as exc:
            message = f"cannot send {action} to the runner host: {error_code(exc)}"
            raise RunnerError(f"{message}{self._restore(previous, written)}") from None

    def _restore(self, previous: dict[str, str], written: list[str]) -> str:
        """Put back the values the failed action overwrote; '' or the failure text."""
        failed: list[str] = []
        for name in written:
            try:
                if name in previous:
                    self._ssm.put_parameter(
                        Name=name, Value=previous[name], Type="String", Overwrite=True
                    )
                else:
                    self._ssm.delete_parameter(Name=name)
            except AWS_ERRORS as exc:
                log.error("cannot restore %s: %s", name, error_code(exc))
                failed.append(f"{name} ({error_code(exc)})")
        if not failed:
            return ""
        return f"; the previous runner settings could not be restored: {', '.join(failed)}"

    def _send(self, action: str) -> str:
        """SendCommand; the command id."""
        try:
            sent = self._ssm.send_command(
                InstanceIds=[self._instance],
                DocumentName=self._document,
                Parameters={"action": [action]},
                Comment="fse console",
            )
        except AWS_ERRORS as exc:
            log.warning("cannot send %s to the runner host: %s", action, error_code(exc))
            raise RunnerError(
                f"cannot send {action} to the runner host: {error_code(exc)}"
            ) from None
        return str(sent["Command"]["CommandId"])

    async def _poll_command(self, command_id: str, action: str) -> None:
        started = self._clock()
        while True:
            await self._sleep(COMMAND_POLL_S)
            try:
                inv = await asyncio.to_thread(
                    self._ssm.get_command_invocation,
                    CommandId=command_id,
                    InstanceId=self._instance,
                )
            except AWS_ERRORS as exc:
                if error_code(exc) == "InvocationDoesNotExist" and (
                    self._clock() - started < COMMAND_CAP_S
                ):
                    continue
                self._record(action, f"unknown ({error_code(exc)})", [])
                return
            status = str(inv.get("Status", "Unknown"))
            output = "\n".join(
                s for s in (inv.get("StandardOutputContent"), inv.get("StandardErrorContent")) if s
            )
            redact = self._redact()
            lines = [redact(line) for line in output.splitlines()][-20:]
            if status in COMMAND_DONE or self._clock() - started >= COMMAND_CAP_S:
                self._record(action, status, lines)
                return

    def _record(self, action: str, status: str, output: list[str]) -> None:
        self._last_command = {
            "action": action,
            "status": status,
            "at": self._clock(),
            "output": output,
        }
