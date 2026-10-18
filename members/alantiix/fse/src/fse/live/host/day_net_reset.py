"""One-shot ``day_net_reset.json``: zero a plugin's paper day net while running.

``fse day-loss-reset`` writes the file under ``<live-state>/plugins/<id>/``.
The host consumes it (unlink + ``PluginPaperBroker.reset_day_net``) on the next
decision or status tick, so sizing again uses the full ``max_daily_loss`` budget.
The runner YAML ``risk.max_daily_loss`` is never changed.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Final

__all__ = [
    "DAY_NET_RESET_FILE_NAME",
    "day_net_reset_path",
    "read_day_net_reset",
    "take_day_net_reset",
    "write_day_net_reset",
]

DAY_NET_RESET_FILE_NAME: Final = "day_net_reset.json"


def day_net_reset_path(plugin_dir: Path) -> Path:
    return Path(plugin_dir) / DAY_NET_RESET_FILE_NAME


def write_day_net_reset(plugin_dir: Path, *, reason: str, t_ns: int | None = None) -> Path:
    """Write (or overwrite) the one-shot request. Creates ``plugin_dir`` if needed."""
    directory = Path(plugin_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = day_net_reset_path(directory)
    stamp = time.time_ns() if t_ns is None else t_ns
    path.write_text(
        json.dumps({"reason": reason, "t_ns": stamp}, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return path


def read_day_net_reset(plugin_dir: Path) -> dict[str, Any] | None:
    """The request payload, or ``None`` when the file is absent or unreadable."""
    path = day_net_reset_path(plugin_dir)
    if not path.is_file():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


def take_day_net_reset(plugin_dir: Path) -> dict[str, Any] | None:
    """Read and unlink the request. Returns the payload (possibly empty) when present."""
    path = day_net_reset_path(plugin_dir)
    if not path.is_file():
        return None
    payload = read_day_net_reset(plugin_dir) or {}
    try:
        path.unlink(missing_ok=True)
    except OSError:
        # Still report the request so the host can zero day_net; a stuck file
        # is retried on the next tick until unlink succeeds.
        return payload
    return payload
