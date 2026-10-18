"""Paper by default, by construction (design 10.2, D4, T18): a static scan of the runner's modules.

No module of ``fse.live.host``, ``fse.live.plugins``, ``fse.commands.run``,
or any ``research/<name>/plugin.py``, ``research/<name>/paper_io.py`` or
``research/<name>/plugin/`` package may **directly**
import ``fse.projectx.broker``, ``fse.live.broker_safety``, ``fse.commands.live``,
or ``MirrorBroker``/``broker_routing``/``resolve_order_mode`` from
``fse.live.order_router``, with one exception: ``src/fse/live/host/live_orders.py``
(:data:`ALLOWED`), the opt-in broker path. That module in turn may be imported
only by ``src/fse/live/host/host.py`` and ``src/fse/commands/run.py``
(:data:`LIVE_ORDERS_IMPORTERS`), so no plugin and no other host module reaches
the broker through its re-exports. Relative imports are resolved to absolute
module names before either rule is checked. Transitive imports
(``fse.live.runner`` imports the broker modules) are harmless: importing a
module sends nothing. Files that do not exist yet are skipped, so later work
only extends ``SCANNED``.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

import pytest

ENGINE: Final = Path(__file__).resolve().parents[2]
SCANNED: Final = (
    "src/fse/live/host",
    "src/fse/live/plugins",
    "src/fse/commands/run.py",
)
RESEARCH_GLOBS: Final = ("*/plugin.py", "*/paper_io.py", "*/plugin/**/*.py")
"""Plugins a user adds under ``research/``; none ships, so these globs may match nothing."""
FORBIDDEN_MODULES: Final = frozenset(
    {"fse.projectx.broker", "fse.live.broker_safety", "fse.commands.live"}
)
FORBIDDEN_NAMES: Final = {
    "fse.live.order_router": frozenset({"MirrorBroker", "broker_routing", "resolve_order_mode"}),
}
ALLOWED: Final = frozenset({"src/fse/live/host/live_orders.py"})
"""Exempt from the forbidden list: the one module of the opt-in broker path."""
LIVE_ORDERS: Final = "fse.live.host.live_orders"
LIVE_ORDERS_IMPORTERS: Final = frozenset({"src/fse/live/host/host.py", "src/fse/commands/run.py"})


def modules() -> list[Path]:
    out: list[Path] = []
    for rel in SCANNED:
        path = ENGINE / rel
        if path.is_dir():
            out.extend(sorted(path.rglob("*.py")))
        elif path.exists():
            out.append(path)
    for pattern in RESEARCH_GLOBS:
        out.extend(sorted((ENGINE / "research").glob(pattern)))
    return out


def _package(rel: str) -> list[str]:
    """The package parts of a scanned file (``src/fse/live/host/x.py`` → ``fse.live.host``)."""
    parts = Path(rel).with_suffix("").parts
    if parts and parts[0] == "src":
        parts = parts[1:]
    return list(parts[:-1])


def _absolute(node: ast.ImportFrom, rel: str) -> str | None:
    if node.level == 0:
        return node.module
    base = _package(rel)
    if node.level - 1 > len(base):
        return None
    base = base[: len(base) - (node.level - 1)]
    return ".".join([*base, *([node.module] if node.module else [])]) or None


def _forbidden_module(name: str) -> bool:
    return name in FORBIDDEN_MODULES or any(name.startswith(f"{m}.") for m in FORBIDDEN_MODULES)


def _live_orders(name: str) -> bool:
    return name == LIVE_ORDERS or name.startswith(f"{LIVE_ORDERS}.")


def violations(source: str, rel: str = "src/fse/live/host/host.py") -> list[str]:
    """The forbidden imports of ``source`` read as the scanned file ``rel``."""
    broker_ok = rel in ALLOWED
    live_ok = rel in LIVE_ORDERS_IMPORTERS or rel in ALLOWED
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not broker_ok and _forbidden_module(alias.name):
                    found.append(f"import {alias.name}")
                if not live_ok and _live_orders(alias.name):
                    found.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = _absolute(node, rel)
            if module is None:
                continue
            full = [f"{module}.{a.name}" for a in node.names]
            if not broker_ok:
                if _forbidden_module(module):
                    found.append(f"from {module} import ...")
                names = FORBIDDEN_NAMES.get(module, frozenset())
                found.extend(
                    f"from {module} import {a.name}" for a in node.names if a.name in names
                )
                found.extend(f"from {module} import {f}" for f in full if f in FORBIDDEN_MODULES)
            if not live_ok and (_live_orders(module) or any(f == LIVE_ORDERS for f in full)):
                found.append(f"from {module} import ... ({LIVE_ORDERS})")
    return found


def test_the_scan_finds_the_new_packages() -> None:
    names = {p.relative_to(ENGINE).as_posix() for p in modules()}
    assert "src/fse/live/host/host.py" in names
    assert "src/fse/live/host/broker.py" in names
    assert "src/fse/live/host/live_orders.py" in names
    assert "src/fse/live/plugins/api.py" in names
    assert "src/fse/live/plugins/playbook.py" in names
    assert "src/fse/commands/run.py" in names
    assert "src/fse/live/host/dataplane.py" in names
    assert "src/fse/live/host/sources.py" in names
    assert "src/fse/live/host/barstore.py" in names


@pytest.mark.parametrize("path", modules(), ids=lambda p: p.relative_to(ENGINE).as_posix())
def test_no_direct_broker_imports(path: Path) -> None:
    rel = path.relative_to(ENGINE).as_posix()
    assert violations(path.read_text(encoding="utf-8"), rel) == []


@pytest.mark.parametrize(
    "source",
    [
        "import fse.projectx.broker",
        "from fse.live import broker_safety",
        "from fse.live.broker_safety import BrokerSafety",
        "from fse.live.order_router import resolve_order_mode",
        "from fse.live.order_router import MirrorBroker as M",
        "from fse.commands import live",
        "def f():\n    import fse.commands.live\n",
        "from ..broker_safety import BrokerSafety",
        "from ... import commands\nfrom ...commands import live",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import fse.projectx.broker\n",
    ],
)
def test_the_scan_catches_each_forbidden_form(source: str) -> None:
    assert violations(source) != []


@pytest.mark.parametrize(
    ("source", "rel"),
    [
        ("from .live_orders import OrderLink", "src/fse/live/host/gate.py"),
        ("from fse.live.host.live_orders import OrderLink", "research/example/plugin.py"),
        ("from fse.live.host import live_orders", "research/example/plugin.py"),
        ("import fse.live.host.live_orders", "research/example/plugin.py"),
        ("from fse.live.host import live_orders", "src/fse/live/host/gate.py"),
        ("import fse.live.host.live_orders", "src/fse/live/host/gate.py"),
        ("from . import live_orders", "src/fse/live/host/gate.py"),
        ("from fse.live.host.live_orders import OrderLink", "src/fse/live/plugins/api.py"),
        ("from fse.live.host.live_orders import OrderLink", "research/example/paper_io.py"),
    ],
)
def test_live_orders_is_caught_outside_host_and_run(source: str, rel: str) -> None:
    assert violations(source, rel) != []


@pytest.mark.parametrize(
    "source",
    [
        "from .live_orders import OrderLink",
        "from fse.live.host.live_orders import OrderLink",
        "from fse.live.host import live_orders",
        "import fse.live.host.live_orders",
    ],
)
@pytest.mark.parametrize("rel", sorted(LIVE_ORDERS_IMPORTERS))
def test_host_and_run_may_import_live_orders(source: str, rel: str) -> None:
    if rel == "src/fse/commands/run.py" and source.startswith("from ."):
        source = "from ..live.host.live_orders import OrderLink"
    assert violations(source, rel) == []


@pytest.mark.parametrize("rel", sorted(LIVE_ORDERS_IMPORTERS))
def test_host_and_run_still_may_not_import_the_broker_directly(rel: str) -> None:
    assert violations("from ..broker_safety import BrokerSafety", "src/fse/live/host/host.py")
    assert violations("from fse.projectx.broker import BrokerAdapter", rel)


def test_only_live_orders_may_import_the_broker() -> None:
    source = "from fse.live.broker_safety import BrokerSafety\nimport fse.projectx.broker"
    assert violations(source, "src/fse/live/host/live_orders.py") == []
    assert violations(source, "src/fse/live/host/broker.py") != []


def test_allowed_imports_pass() -> None:
    assert (
        violations("from fse.live.order_router import paper_routing\nimport fse.live.runner") == []
    )
