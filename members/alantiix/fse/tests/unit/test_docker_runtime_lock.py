"""docker/requirements-runtime.lock stays a subset of requirements.lock.

The image installs the runtime lock; the suite and the Operator machine install
requirements.lock. Every pin in the runtime lock must carry the same version and
only hashes that requirements.lock also lists, so both resolve to the same files.
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==(\S+)")
_HASH = re.compile(r"--hash=(sha256:[0-9a-f]{64})")


def _parse(path: Path) -> dict[str, tuple[str, frozenset[str]]]:
    pins: dict[str, tuple[str, set[str]]] = {}
    current: str | None = None
    for line in path.read_text().splitlines():
        match = _PIN.match(line)
        if match:
            current = re.sub(r"[-_.]+", "-", match.group(1)).lower()
            pins[current] = (match.group(2), set())
        elif not line.startswith(" "):
            current = None
        if current is not None:
            pins[current][1].update(_HASH.findall(line))
    return {name: (version, frozenset(hashes)) for name, (version, hashes) in pins.items()}


def test_runtime_lock_is_a_subset_of_requirements_lock() -> None:
    runtime = _parse(PROJECT / "docker" / "requirements-runtime.lock")
    full = _parse(PROJECT / "requirements.lock")
    assert runtime, "runtime lock has no pins"
    for name, (version, hashes) in runtime.items():
        assert name in full, f"{name} is not in requirements.lock"
        assert version == full[name][0], f"{name}: {version} != {full[name][0]}"
        assert hashes, f"{name} has no hashes"
        assert hashes <= full[name][1], f"{name}: hashes missing from requirements.lock"


def test_runtime_lock_has_the_runtime_dependencies() -> None:
    runtime = _parse(PROJECT / "docker" / "requirements-runtime.lock")
    for name in ("httpx", "pydantic", "pyarrow", "numpy", "pyyaml", "websockets", "boto3"):
        assert name in runtime
    for name in ("pytest", "moto", "pandas", "mypy", "ruff"):
        assert name not in runtime
