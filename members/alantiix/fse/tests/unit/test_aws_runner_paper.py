"""The AWS runner service sends plugin orders only behind the control file (design D2, T29).

A static read of ``docker/entrypoint.sh`` and the Phase 1 host script. The runner
service starts a fixed ``fse run`` command; ``--plugin-orders`` and ``--live-orders``
appear once each, inside the guard on the literal read-only control file
``/run/fse-control/plugin_orders``. No environment variable
(``FSE_RUN_PLUGIN_ORDERS`` through ``/fse/secrets/`` or a ``FSE_CONTROL_FILE``
override) can enable them, and Phase 1's ``fse-start.sh`` never mounts the control
directory. ``--live-orders`` is required for Combine; Practice ignores it.
The behavior matrix lives in ``test_docker_entrypoint.py``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from fse.live.host.live_orders import LIVE_ORDERS_FLAG, PLUGIN_ORDERS_ENV, PLUGIN_ORDERS_FLAG

ENGINE: Final = Path(__file__).resolve().parents[2]
ENTRYPOINT: Final = ENGINE / "docker" / "entrypoint.sh"
PHASE1_START: Final = ENGINE / "infra" / "modules" / "runner" / "files" / "fse-start.sh"
CONTROL_FILE: Final = "/run/fse-control/plugin_orders"


def _code() -> str:
    """The entrypoint without comment lines."""
    lines = ENTRYPOINT.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


def _runner_body() -> str:
    code = _code()
    start = code.index("\nrunner() {")
    return code[start : code.index("\n}\n", start)]


def test_the_plugin_and_live_orders_flags_appear_once_inside_the_control_file_guard() -> None:
    code = _code()
    assert code.count(PLUGIN_ORDERS_FLAG) == 1
    assert code.count(LIVE_ORDERS_FLAG) == 1
    body = _runner_body()
    guard = body.index(
        'if [[ "$mode" == run && -n "${FSE_RUNNER_MODE:-}" && -f "$FSE_CONTROL_FILE"'
    )
    flag = body.index(f"plugin_args=({PLUGIN_ORDERS_FLAG} {LIVE_ORDERS_FLAG})")
    end = body.index("\n  fi\n", guard)
    assert guard < flag < end
    assert f"child_env=({PLUGIN_ORDERS_ENV}=enabled)" in body[guard:end]
    assert '! -L "$FSE_CONTROL_FILE"' in body[guard:end]
    # Never in paper_loop: the flags only reach the fse run call.
    paper = code[code.index("\npaper_loop() {") : code.index("\nrunner() {")]
    assert "plugin_args" not in paper
    assert PLUGIN_ORDERS_ENV not in paper
    assert LIVE_ORDERS_FLAG not in paper


def test_the_control_file_is_a_literal_assigned_once_and_never_read_from_env() -> None:
    code = _code()
    assigns = re.findall(r"^.*\bFSE_CONTROL_FILE=.*$", code, re.M)
    assert assigns == [f"readonly FSE_CONTROL_FILE={CONTROL_FILE}"]
    assert "${FSE_CONTROL_FILE:" not in code
    assert "${FSE_CONTROL_FILE-" not in code
    assert code.count(CONTROL_FILE) == 1


def test_the_runner_unsets_the_inherited_variable_first() -> None:
    body = _runner_body()
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    assert lines[0] == "runner() {"
    assert lines[1].startswith("local d=")
    assert lines[2] == f"unset {PLUGIN_ORDERS_ENV}"


def test_the_fse_run_call_takes_the_guarded_env_and_flag_only() -> None:
    body = _runner_body()
    call = body[body.index("run_child env ") : body.index("\n  case", body.index("run_child env "))]
    assert call.startswith('run_child env ${child_env[@]+"${child_env[@]}"} fse run ')
    assert call.rstrip().endswith('${plugin_args[@]+"${plugin_args[@]}"}')


def test_phase1_start_script_never_mounts_the_control_dir() -> None:
    assert "/run/fse-control" not in PHASE1_START.read_text(encoding="utf-8")
