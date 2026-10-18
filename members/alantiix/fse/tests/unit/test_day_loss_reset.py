"""``fse day-loss-reset`` writes a one-shot request the host consumes."""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

from fse.commands.day_loss_reset import run_day_loss_reset
from fse.live.host.day_net_reset import (
    DAY_NET_RESET_FILE_NAME,
    take_day_net_reset,
    write_day_net_reset,
)
from fse.logio.log_writer import LogWriter
from fse.secrets.env import EnvView


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(
        writer=LogWriter.from_env(EnvView({}, {}), stdout=io.StringIO(), stderr=io.StringIO())
    )


def test_write_and_take_day_net_reset(tmp_path: Path) -> None:
    plugin = tmp_path / "plugins" / "alpha"
    path = write_day_net_reset(plugin, reason="ops reset")
    assert path.name == DAY_NET_RESET_FILE_NAME
    assert path.is_file()
    payload = take_day_net_reset(plugin)
    assert payload is not None
    assert payload["reason"] == "ops reset"
    assert take_day_net_reset(plugin) is None


def test_cli_writes_request_under_plugin_dir(tmp_path: Path) -> None:
    state = tmp_path / "live-state"
    state.mkdir()
    args = SimpleNamespace(plugin="alpha", state_dir=state, reason="from console")
    assert run_day_loss_reset(args, _ctx()) == 0  # type: ignore[arg-type]
    req = state / "plugins" / "alpha" / DAY_NET_RESET_FILE_NAME
    assert req.is_file()
    assert json.loads(req.read_text())["reason"] == "from console"


def test_cli_rejects_bad_plugin_id(tmp_path: Path) -> None:
    state = tmp_path / "live-state"
    state.mkdir()
    args = SimpleNamespace(plugin="Bad-Id", state_dir=state, reason="x")
    assert run_day_loss_reset(args, _ctx()) == 2  # type: ignore[arg-type]
    assert not (state / "plugins").exists()
