"""Engine purity guard (design "Layering and dependency rule" and D7).

Parses every module under ``src/fse/engine`` and fails on:

- an import outside the allowed set: ``fse.engine.*``, ``fse.config.schema``
  (and its section modules), ``fse.timekit``, ``fse.pit.protocols``, the
  standard library and numpy;
- any use of the clock (``time``, ``datetime.now`` and friends), randomness
  (``random``, ``numpy.random``), the environment (``os.environ``), or file and
  console I/O.

The checker is also run on small sources, so the guard cannot pass vacuously.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
ENGINE_DIR = SRC_DIR / "fse" / "engine"

# First-party and third-party modules the engine may import (with submodules).
ALLOWED_PREFIXES: tuple[str, ...] = (
    "fse.engine",
    "fse.config.schema",
    "fse.timekit",
    "fse.pit.protocols",
    "numpy",
)

_CLOCK = "reads the clock"
_RNG = "draws randomness"
_ENV = "reaches the environment or file system (os.environ, os.getenv, os.open)"
_IO = "does file or console I/O"
_DYNAMIC = "bypasses the import check"

_IO_MODULES = (
    *("io", "pathlib", "shutil", "tempfile", "glob", "fileinput", "mmap", "logging"),
    *("sqlite3", "dbm", "shelve", "gzip", "bz2", "lzma", "zipfile", "tarfile"),
    *("socket", "ssl", "subprocess", "urllib", "http", "asyncio", "threading"),
    *("multiprocessing", "concurrent", "codecs.open", "sys.stdin", "sys.stdout", "sys.stderr"),
)
_NUMPY_FILE_FUNCTIONS = (
    *("load", "save", "savez", "savez_compressed", "loadtxt", "savetxt"),
    *("genfromtxt", "fromfile", "fromregex", "memmap"),
)

# Dotted names the engine must never reach; a match on any prefix counts.
FORBIDDEN: dict[str, str] = {
    "time": _CLOCK,
    "datetime.datetime.now": _CLOCK,
    "datetime.datetime.utcnow": _CLOCK,
    "datetime.datetime.today": _CLOCK,
    "datetime.date.today": _CLOCK,
    "random": _RNG,
    "secrets": _RNG,
    "uuid": _RNG,
    "numpy.random": _RNG,
    "os": _ENV,
    **dict.fromkeys(_IO_MODULES, _IO),
    **dict.fromkeys((f"numpy.{name}" for name in _NUMPY_FILE_FUNCTIONS), _IO),
    "importlib": _DYNAMIC,
    "builtins": _DYNAMIC,
    "ctypes": _DYNAMIC,
}
FORBIDDEN_BUILTINS: dict[str, str] = {
    "open": _IO,
    "print": _IO,
    "input": _IO,
    "breakpoint": _IO,
    "__import__": _DYNAMIC,
    "eval": _DYNAMIC,
    "exec": _DYNAMIC,
}
# Method names that do file I/O on whatever object they are called on.
FORBIDDEN_METHODS = frozenset({"read_text", "write_text", "read_bytes", "write_bytes", "tofile"})


def _forbidden(dotted: str) -> str | None:
    parts = dotted.split(".")
    for i in range(1, len(parts) + 1):
        reason = FORBIDDEN.get(".".join(parts[:i]))
        if reason is not None:
            return reason
    return None


def _allowed(dotted: str) -> bool:
    if _forbidden(dotted) is not None:
        return False
    if any(dotted == p or dotted.startswith(p + ".") for p in ALLOWED_PREFIXES):
        return True
    top = dotted.partition(".")[0]
    return top not in {"fse", "numpy"} and top in sys.stdlib_module_names


def _bound_names(tree: ast.Module) -> frozenset[str]:
    """Every name the module binds, so a local ``open`` is not the builtin."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            names.add(node.id)
    return frozenset(names)


def _attribute_chain(node: ast.Attribute) -> tuple[list[str], ast.expr]:
    attrs: list[str] = []
    cur: ast.expr = node
    while isinstance(cur, ast.Attribute):
        attrs.append(cur.attr)
        cur = cur.value
    attrs.reverse()
    return attrs, cur


@dataclass(frozen=True, order=True)
class Violation:
    line: int
    message: str


class _EngineChecker(ast.NodeVisitor):
    def __init__(self, module: str, is_package: bool, tree: ast.Module) -> None:
        self.package = module if is_package else module.rpartition(".")[0]
        self.aliases: dict[str, str] = {}
        self.bound = _bound_names(tree)
        self.found: set[Violation] = set()

    def _add(self, node: ast.AST, message: str) -> None:
        self.found.add(Violation(getattr(node, "lineno", 0), message))

    def _check_module(self, node: ast.AST, label: str, dotted: str) -> None:
        reason = _forbidden(dotted)
        if reason is not None:
            self._add(node, f"{label}: {reason}")
        elif not _allowed(dotted):
            self._add(node, f"{label}: outside the engine's allowed imports")

    def _resolve_from(self, node: ast.ImportFrom) -> str | None:
        if node.level == 0:
            return node.module
        parts = self.package.split(".")
        keep = len(parts) - (node.level - 1)
        if keep <= 0:
            return None
        base = ".".join(parts[:keep])
        return f"{base}.{node.module}" if node.module else base

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._check_module(node, f"import {alias.name}", alias.name)
            if alias.asname:
                self.aliases[alias.asname] = alias.name
            else:
                top = alias.name.partition(".")[0]
                self.aliases[top] = top

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = self._resolve_from(node)
        if base is None:
            self._add(node, "relative import beyond the top-level package")
            return
        for alias in node.names:
            label = f"from {base} import {alias.name}"
            if alias.name == "*":
                self._check_module(node, label, base)
                continue
            target = f"{base}.{alias.name}"
            reason = _forbidden(target)
            if reason is not None:
                self._add(node, f"{label}: {reason}")
            elif not (_allowed(base) or _allowed(target)):
                self._add(node, f"{label}: outside the engine's allowed imports")
            self.aliases[alias.asname or alias.name] = target

    def visit_Attribute(self, node: ast.Attribute) -> None:
        attrs, base = _attribute_chain(node)
        for attr in attrs:
            if attr in FORBIDDEN_METHODS:
                self._add(node, f".{attr}(): {_IO}")
        if isinstance(base, ast.Name) and base.id in self.aliases:
            dotted = ".".join([self.aliases[base.id], *attrs])
            reason = _forbidden(dotted)
            if reason is not None:
                self._add(node, f"{dotted}: {reason}")
            elif dotted.startswith("fse.") and not _allowed(dotted):
                self._add(node, f"{dotted}: outside the engine's allowed imports")
        else:
            self.visit(base)

    def visit_Name(self, node: ast.Name) -> None:
        reason = FORBIDDEN_BUILTINS.get(node.id)
        if reason is not None and isinstance(node.ctx, ast.Load) and node.id not in self.bound:
            self._add(node, f"{node.id}(): {reason}")


def engine_violations(
    source: str, module: str = "fse.engine.sample", is_package: bool = False
) -> list[str]:
    """Every rule violation in ``source``, as ``line N: message`` strings."""
    tree = ast.parse(source)
    checker = _EngineChecker(module, is_package, tree)
    checker.visit(tree)
    return [f"line {v.line}: {v.message}" for v in sorted(checker.found)]


def _module_name(path: Path) -> tuple[str, bool]:
    parts = path.relative_to(SRC_DIR).with_suffix("").parts
    if parts[-1] == "__init__":
        return ".".join(parts[:-1]), True
    return ".".join(parts), False


ENGINE_MODULES = sorted(ENGINE_DIR.rglob("*.py"))


# ---------------------------------------------------------------- the real engine


def test_engine_modules_are_discovered() -> None:
    names = {_module_name(p)[0] for p in ENGINE_MODULES}
    assert {"fse.engine", "fse.engine.types", "fse.engine.setups", "fse.engine.gates"} <= names


@pytest.mark.parametrize("path", ENGINE_MODULES, ids=lambda p: p.relative_to(SRC_DIR).as_posix())
def test_engine_module_is_pure(path: Path) -> None:
    module, is_package = _module_name(path)
    violations = engine_violations(path.read_text(encoding="utf-8"), module, is_package)
    assert violations == [], f"{path.relative_to(SRC_DIR)}:\n" + "\n".join(violations)


# ---------------------------------------------------------------- the checker itself

ALLOWED_SOURCE = """
from __future__ import annotations
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import numpy as np
import numpy.typing as npt
import fse.config.schema.nodes
from fse import timekit
from fse.timekit import Instant, SessionCalendar
from fse.pit import protocols
from fse.pit.protocols import MarketView
from fse.config.schema import nodes
from fse.engine.types import Bar
from . import types
from .types import Snapshot
from .setups import registry

OPEN = time(9, 30)
start = datetime.combine(date(2026, 3, 5), OPEN)
arr = np.asarray([1.0, 2.0])
section = fse.config.schema.nodes

def labels(state, open):
    return state.today, state.now, open
"""


def test_allowed_imports_and_names_pass() -> None:
    assert engine_violations(ALLOWED_SOURCE) == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import time", "import time: reads the clock"),
        ("from time import monotonic", "reads the clock"),
        ("import datetime\ndatetime.datetime.now()", "datetime.datetime.now: reads the clock"),
        ("import datetime as dt\ndt.date.today()", "datetime.date.today: reads the clock"),
        ("from datetime import datetime\nnow = datetime.now", "datetime.datetime.now"),
        ("from datetime import datetime as D\nD.utcnow()", "datetime.datetime.utcnow"),
        ("import random", "import random: draws randomness"),
        ("from random import choice", "draws randomness"),
        ("import numpy as np\nnp.random.default_rng(0)", "numpy.random.default_rng: draws"),
        ("from numpy import random", "draws randomness"),
        ("import os\nos.environ['SKYLIT_API_KEY']", "os.environ"),
        ("from os import environ", "from os import environ: reaches the environment"),
        ("from os import getenv", "from os import getenv: reaches the environment"),
        ("open('notes.txt')", "open(): does file or console I/O"),
        ("from pathlib import Path", "from pathlib import Path: does file"),
        ("def f(p):\n    return p.read_text()", ".read_text(): does file"),
        ("import numpy as np\nnp.save('x.npy', [])", "numpy.save: does file"),
        ("print('debug')", "print(): does file or console I/O"),
        ("__import__('time')", "__import__(): bypasses the import check"),
        ("import importlib", "import importlib: bypasses"),
        ("import httpx", "import httpx: outside the engine's allowed imports"),
        ("import pydantic", "import pydantic: outside"),
        ("from fse import settings", "from fse import settings: outside"),
        ("import fse.data.cache", "import fse.data.cache: outside"),
        ("from fse.pit import market_view", "from fse.pit import market_view: outside"),
        ("from fse.config import loader", "from fse.config import loader: outside"),
        ("from fse.pit import *", "from fse.pit import *: outside"),
        ("from ..data import cache", "from fse.data import cache: outside"),
        ("from ...x import y", "relative import beyond the top-level package"),
        ("import fse.engine.types\nfse.data.cache", "fse.data.cache: outside"),
    ],
)
def test_forbidden_source_is_reported(source: str, expected: str) -> None:
    violations = engine_violations(source)
    assert any(expected in v for v in violations), violations
