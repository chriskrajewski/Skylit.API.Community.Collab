"""Blocking runners and ``task_done`` wakes (design 3.3; plan MEDIUM test 3).

``test_thread_runner_task_done_wake`` uses the production
``ThreadBlockingRunner`` on a real event loop with a real-time clock: the job
runs on a worker thread, and its one ``task_done`` wake (``on_wake``) comes
back on the dispatcher's thread.
"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from fse.live.host.host import HOST_LOG_FILE_NAME, InlineBlockingRunner, ThreadBlockingRunner
from fse.live.plugins import PluginContext
from fse.timekit import Instant
from tests.fakes.clock import FakeClock
from tests.fakes.dataplane import FakeDataPlane, host_entry, make_host, ny, read_jsonl
from tests.fakes.plugins import RecordingPlugin

WED = date(2026, 10, 14)


class RealTimeClock:
    """Real elapsed time from a fixed starting Instant."""

    def __init__(self, start: Instant) -> None:
        self._start = start
        self._t0 = time.monotonic_ns()

    def now(self) -> Instant:
        return self._start + time.monotonic_ns() - self._t0

    async def sleep(self, delay_ns: int) -> None:
        await asyncio.sleep(max(0, delay_ns) / 1e9)


class Job(RecordingPlugin):
    """Starts one blocking job in ``on_start``; records the threads involved."""

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.dispatcher_thread: int | None = None
        self.job_thread: int | None = None
        self.wakes: list[tuple[int, bool, object]] = []
        self.wake_times: list[Instant] = []
        self.task: asyncio.Task[int] | None = None

    def _job(self, value: int) -> int:
        self.job_thread = threading.get_ident()
        time.sleep(0.05)
        return value

    def on_start(self, ctx: PluginContext) -> tuple[()]:
        self.dispatcher_thread = threading.get_ident()
        self.task = self.services.run_blocking("job", self._job, 42)
        return ()

    def on_wake(self, now: Instant, ctx: PluginContext) -> tuple[()]:
        task = self.task
        assert task is not None
        self.wakes.append((threading.get_ident(), task.done(), task.result()))
        self.wake_times.append(now)
        return ()


@pytest.mark.asyncio
async def test_thread_runner_task_done_wake(tmp_path: Path) -> None:
    clock = RealTimeClock(ny(WED, 10, 0))
    host = make_host(
        tmp_path,
        {"p1": Job},
        [host_entry("p1")],
        clock,
        FakeDataPlane(clock),
        blocking=ThreadBlockingRunner(),
    )
    plugin = host.plugin("p1")
    assert isinstance(plugin, Job)
    run = asyncio.create_task(host.run())
    for _ in range(500):
        if plugin.wakes:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.2)  # no second wake follows
    host.request_stop()
    assert await asyncio.wait_for(run, 5) == 0
    assert plugin.dispatcher_thread == threading.get_ident()
    assert plugin.job_thread is not None
    assert plugin.job_thread != plugin.dispatcher_thread
    assert plugin.wakes == [(plugin.dispatcher_thread, True, 42)]


@pytest.mark.asyncio
async def test_inline_runner_finishes_at_once_with_one_wake(tmp_path: Path) -> None:
    clock = FakeClock(ny(WED, 10, 0))
    host = make_host(
        tmp_path,
        {"p1": Job},
        [host_entry("p1")],
        clock,
        FakeDataPlane(clock),
        blocking=InlineBlockingRunner(),
    )
    plugin = host.plugin("p1")
    assert isinstance(plugin, Job)
    await clock.run(host.run(until_ns=ny(WED, 10, 5)))
    assert plugin.wakes == [(threading.get_ident(), True, 42)]
    assert plugin.job_thread == threading.get_ident()
    # the wake came at the instant the job was started: virtual time did not move
    assert plugin.wake_times == [ny(WED, 10, 0)]


class Spawner(RecordingPlugin):
    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.wake_count = 0

    def on_start(self, ctx: PluginContext) -> tuple[()]:
        async def broken() -> None:
            raise RuntimeError("notifier down")

        self.services.spawn("notifier", broken())
        return ()

    def on_wake(self, now: Instant, ctx: PluginContext) -> tuple[()]:
        self.wake_count += 1
        return ()


@pytest.mark.asyncio
async def test_spawned_task_failure_logs_and_exits_1_without_fault(tmp_path: Path) -> None:
    clock = FakeClock(ny(WED, 10, 0))
    host = make_host(tmp_path, {"p1": Spawner}, [host_entry("p1")], clock, FakeDataPlane(clock))
    assert await clock.run(host.run(until_ns=ny(WED, 10, 1))) == 1
    plugin = host.plugin("p1")
    assert isinstance(plugin, Spawner)
    assert host.faults() == {}
    assert plugin.wake_count == 1
    (entry,) = [
        e
        for e in read_jsonl(tmp_path / "run" / HOST_LOG_FILE_NAME)
        if e["kind"] == "spawned_task_failed"
    ]
    assert entry["task"] == "notifier"
    assert "notifier down" in entry["traceback"]
