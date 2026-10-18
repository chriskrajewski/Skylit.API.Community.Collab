"""``RunnerLock`` (design 10.8), also taken by ``fse paper`` and ``fse live``.

The one intended change to ``fse paper``/``fse live``: while another runner
holds ``<live-state>/runner.lock`` they exit 2 with "another runner holds
<path>"; otherwise they run as before and release the lock at the end.
"""

from __future__ import annotations

import argparse
import io
import os
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from fse.commands import paper
from fse.live.host.lock import RUNNER_LOCK_FILE_NAME, RunnerLock, RunnerLockHeld
from fse.logio import LogWriter
from fse.logio.redact import Redactor


def test_acquire_release_and_held(tmp_path: Path) -> None:
    lock = RunnerLock(tmp_path / "state")
    lock.acquire()
    assert lock.held
    assert lock.path == tmp_path / "state" / RUNNER_LOCK_FILE_NAME
    assert lock.path.read_text().strip() == str(os.getpid())
    other = RunnerLock(tmp_path / "state")
    with pytest.raises(RunnerLockHeld) as info:
        other.acquire()
    assert info.value.exit_code == 2
    assert str(info.value).startswith(f"another runner holds {lock.path}")
    assert str(os.getpid()) in str(info.value)
    lock.release()
    assert not lock.held
    with other:
        assert other.held
    assert not other.held


def test_a_stale_pid_in_the_file_does_not_wedge_the_lock(tmp_path: Path) -> None:
    # A KeepAlive restart after a crash finds the dead runner's pid still in the file:
    # the flock is gone with the process, so the pid text alone never blocks.
    state = tmp_path / "state"
    state.mkdir()
    (state / RUNNER_LOCK_FILE_NAME).write_text("91905\n", encoding="utf-8")
    with RunnerLock(state) as lock:
        assert lock.held
        assert lock.path.read_text().strip() == str(os.getpid())


def test_a_killed_holder_releases_the_lock(tmp_path: Path) -> None:
    # The holder dies with SIGKILL (no release, no cleanup): the kernel drops its flock.
    state = tmp_path / "state"
    code = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "from fse.live.host.lock import RunnerLock\n"
        "RunnerLock(Path(sys.argv[1])).acquire()\n"
        "print('held', flush=True)\n"
        "time.sleep(60)\n"
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    proc = subprocess.Popen(
        [sys.executable, "-c", code, str(state)], stdout=subprocess.PIPE, text=True, env=env
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "held"
        with pytest.raises(RunnerLockHeld, match=str(proc.pid)):
            RunnerLock(state).acquire()
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    with RunnerLock(state) as lock:  # the dead pid is still in the file
        assert lock.held


def prepared(state: Path) -> Any:
    return SimpleNamespace(
        session="2026-10-14",
        out_dir=state.parent / "run",
        dirs={"live-state directory": state},
    )


def ctx() -> Any:
    return SimpleNamespace(
        writer=LogWriter(Redactor(), stdout=io.StringIO(), stderr=io.StringIO()),
        project_dir=None,
    )


def test_fse_paper_refuses_to_start_while_the_lock_is_held(tmp_path: Path) -> None:
    state = tmp_path / "live-state"
    with RunnerLock(state), pytest.raises(RunnerLockHeld, match="another runner holds"):
        paper.run_live(ctx(), prepared(state), None)


def test_fse_paper_holds_the_lock_while_it_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "live-state"
    seen: list[bool] = []

    async def fake_run(c: Any, prep: Any, broker: Any) -> int:
        try:
            RunnerLock(state).acquire()
        except RunnerLockHeld:
            seen.append(True)
        return 0

    monkeypatch.setattr(paper, "_run", fake_run)
    assert paper.run_live(ctx(), prepared(state), None) == 0
    assert seen == [True]
    with RunnerLock(state):  # released afterwards
        pass


def test_the_cli_reports_exit_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = tmp_path / "live-state"

    def fake_prepare(args: argparse.Namespace, c: Any) -> Any:
        prep = prepared(state)
        prep.cfg = SimpleNamespace(order_mode="paper")
        prep.notes = []
        return prep

    monkeypatch.setattr(paper, "prepare", fake_prepare)
    with RunnerLock(state), pytest.raises(RunnerLockHeld) as info:
        paper.run_paper_command(argparse.Namespace(), ctx())
    assert getattr(info.value, "exit_code", None) == 2
