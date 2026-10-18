"""System facts for the System and Health pages: disk, cache size, versions, daily checks."""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(\.partial)?$")
"""A full report ``YYYY-MM-DD`` or an intraday one ``YYYY-MM-DD.partial``."""
_SIZE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
SIZE_TTL_S = 300.0
SIZE_BUDGET_S = 2.0


def _version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def dir_size(path: Path) -> dict[str, Any]:
    """Bytes under ``path``; stops after 2 s (``complete`` false) and caches for 5 minutes."""
    key = str(path)
    hit = _SIZE_CACHE.get(key)
    if hit is not None and time.monotonic() - hit[0] < SIZE_TTL_S:
        return hit[1]
    total, files, complete = 0, 0, True
    deadline = time.monotonic() + SIZE_BUDGET_S
    for root, _dirs, names in os.walk(path):
        for n in names:
            try:
                total += os.lstat(os.path.join(root, n)).st_size
                files += 1
            except OSError:
                continue
        if time.monotonic() > deadline:
            complete = False
            break
    result = {"bytes": total, "files": files, "complete": complete, "exists": path.exists()}
    _SIZE_CACHE[key] = (time.monotonic(), result)
    return result


def system_info(
    *, data_dir: Path, cache_dir: Path, live_state: Path, image_tag: str | None, env: str
) -> dict[str, Any]:
    target = data_dir if data_dir.exists() else data_dir.parent
    usage = shutil.disk_usage(target)
    lock = live_state / "runner.lock"
    return {
        "env": env,
        "image_tag": image_tag,
        "versions": {
            "fse_ui": _version("fse-ui"),
            "engine": _version("skylit-futures-strategy-engine"),
        },
        "data_dir": str(data_dir),
        "disk": {"total": usage.total, "used": usage.used, "free": usage.free},
        "cache": dir_size(cache_dir),
        "runner_lock": {
            "path": str(lock),
            "exists": lock.exists(),
            "modified": lock.stat().st_mtime if lock.exists() else None,
        },
    }


def daily_reports(daily_dir: Path) -> list[dict[str, Any]]:
    out = []
    if daily_dir.is_dir():
        for p in sorted(daily_dir.glob("*.json"), reverse=True):
            if DAY_RE.fullmatch(p.stem):
                out.append(
                    {
                        "day": p.stem,
                        "has_md": (daily_dir / f"{p.stem}.md").exists(),
                        "size": p.stat().st_size,
                        "partial": p.stem.endswith(".partial"),
                    }
                )
    return out


def daily_report(daily_dir: Path, day: str) -> dict[str, Any] | None:
    if not DAY_RE.fullmatch(day):
        return None
    path = daily_dir / f"{day}.json"
    if not path.is_file():
        return None
    md = daily_dir / f"{day}.md"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        data = None
    return {
        "day": day,
        "json": data,
        "markdown": md.read_text(encoding="utf-8") if md.is_file() else None,
    }
