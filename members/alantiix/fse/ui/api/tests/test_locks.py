"""requirements-dev.lock (CI) must carry every runtime pin of requirements.lock (image)."""

from __future__ import annotations

import re
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==([^\s;\\]+)")


def _pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in path.read_text().splitlines():
        m = PIN.match(line)
        if m:
            pins[re.sub(r"[-_.]+", "-", m.group(1)).lower()] = m.group(2)
    return pins


def test_dev_lock_contains_every_runtime_pin() -> None:
    runtime = _pins(API_DIR / "requirements.lock")
    dev = _pins(API_DIR / "requirements-dev.lock")
    assert runtime, "requirements.lock has no pins"
    mismatched = {n: (v, dev.get(n)) for n, v in runtime.items() if dev.get(n) != v}
    assert mismatched == {}


def test_dev_lock_documents_its_command() -> None:
    header = (API_DIR / "requirements-dev.lock").read_text().splitlines()[:3]
    assert any("--extra dev -o requirements-dev.lock" in line for line in header)
