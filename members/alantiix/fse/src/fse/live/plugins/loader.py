"""Plugin discovery and loading (design 3.5) and needs validation (design 3.6).

A runner YAML names each plugin by a dotted path ``"pkg.mod:Class"``; there
are no entry points in v1. Loading, per plugin (each failure is a
:class:`PluginLoadError`, exit 2, naming the plugin id):

1. ``module`` matches ``^[A-Za-z_][\\w.]*:[A-Za-z_]\\w*$``;
2. the Project folder, when known, goes to ``sys.path[0]`` once (logged), so
   ``research.*`` imports work from the console script;
3. the module is imported, and its file must lie inside the Project folder or
   the installed ``fse`` package; anything else (site-packages, ``/tmp``) is
   refused;
4. the attribute is a :class:`StrategyPlugin` subclass whose ``api_version``
   is :data:`PLUGIN_API_VERSION`.

The host then constructs each plugin and checks its ``needs()`` against the
data plane with :func:`needs_problems`; every mismatch of every plugin is
reported together.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Final

import fse
from fse.live.host.config import PLUGIN_ID_PATTERN, DataPlaneConfig, PluginEntry, RunnerConfig
from fse.live.plugins.api import BAR_INTERVAL_S, PLUGIN_API_VERSION, DataNeeds, StrategyPlugin
from fse.pit.market_view import heatmap_view

__all__ = [
    "MODULE_RE",
    "PluginInfo",
    "PluginLoadError",
    "discover_plugins",
    "load_plugin_classes",
    "needs_problems",
    "resolve_plugin_class",
]

MODULE_RE: Final = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$", re.ASCII)
EXIT_INVALID_INPUT: Final = 2
_ID_RE: Final = re.compile(PLUGIN_ID_PATTERN, re.ASCII)

_log = logging.getLogger(__name__)
_added_paths: set[str] = set()


class PluginLoadError(Exception):
    """A plugin that cannot load or whose needs do not fit the data plane (exit 2)."""

    exit_code: ClassVar[int] = EXIT_INVALID_INPUT

    def __init__(self, problems: Iterable[str]) -> None:
        self.problems = list(problems)
        super().__init__("\n".join(self.problems))


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _add_project_dir(project_dir: Path) -> None:
    text = str(project_dir)
    if text in _added_paths:
        return
    _added_paths.add(text)
    if text not in sys.path:
        sys.path.insert(0, text)
        _log.info("added the Project folder %s to sys.path for plugin imports", text)


def resolve_plugin_class(
    plugin_id: str, module: str, *, project_dir: Path | None
) -> type[StrategyPlugin]:
    """Steps 1-4 of the module notes; :class:`PluginLoadError` naming ``plugin_id``."""
    where = f"plugin {plugin_id!r}"
    if MODULE_RE.fullmatch(module) is None:
        raise PluginLoadError([f"{where}: module {module!r} is not 'package.module:Class'"])
    module_name, _, attr = module.partition(":")
    if project_dir is not None:
        _add_project_dir(project_dir)
    try:
        mod = importlib.import_module(module_name)
    except Exception as exc:  # any import-time failure is a load error
        raise PluginLoadError(
            [f"{where}: cannot import {module_name!r}: {type(exc).__name__}: {exc}"]
        ) from exc
    file = getattr(mod, "__file__", None)
    roots = [Path(next(iter(fse.__path__)))]
    if project_dir is not None:
        roots.insert(0, project_dir)
    if file is None or not any(_inside(Path(file), root) for root in roots):
        raise PluginLoadError(
            [
                f"{where}: module {module_name!r} is outside the Project folder and the "
                f"fse package ({file}); plugins load only from there"
            ]
        )
    cls = getattr(mod, attr, None)
    if not isinstance(cls, type) or not issubclass(cls, StrategyPlugin):
        raise PluginLoadError([f"{where}: {module} is not a StrategyPlugin subclass"])
    version = getattr(cls, "api_version", None)
    if version != PLUGIN_API_VERSION:
        raise PluginLoadError(
            [f"{where}: {module} has api_version {version!r}; this host needs {PLUGIN_API_VERSION}"]
        )
    return cls


@dataclass(frozen=True, slots=True)
class PluginInfo:
    """One plugin class found by :func:`discover_plugins`."""

    module: str
    """The runner YAML ``module`` value, ``"pkg.mod:Class"``."""
    cls: type[StrategyPlugin]

    @property
    def label(self) -> str:
        return self.cls.display_label()


# Where plugins live: fse's own, and in the Project folder ``research/<name>/plugin.py``
# (a module) or ``research/<name>/plugin/`` (a package).
_FSE_PLUGIN_PACKAGE: Final = "fse.live.plugins"
_FSE_NOT_PLUGINS: Final = frozenset({"api", "loader"})


def _candidates(project_dir: Path | None) -> list[str]:
    names: list[str] = []
    pkg = importlib.import_module(_FSE_PLUGIN_PACKAGE)
    for info in pkgutil.iter_modules(pkg.__path__):
        if not info.name.startswith("_") and info.name not in _FSE_NOT_PLUGINS:
            names.append(f"{_FSE_PLUGIN_PACKAGE}.{info.name}")
    if project_dir is not None:
        research = project_dir / "research"
        for sub in sorted(research.glob("*/")) if research.is_dir() else []:
            if not sub.is_dir() or sub.name.startswith(("_", ".")):
                continue
            if (sub / "plugin.py").is_file() or (sub / "plugin" / "__init__.py").is_file():
                names.append(f"research.{sub.name}.plugin")
    return names


def discover_plugins(
    project_dir: Path | None,
) -> tuple[list[PluginInfo], list[str]]:
    """Every loadable plugin class, and one problem line per module that failed to import.

    A class is listed once, under the shortest module path that exports it
    (``research.example.plugin:ExamplePlugin``, not ``research.example.plugin.strategy``).
    Abstract classes and classes with another ``api_version`` are skipped.
    """
    if project_dir is not None:
        _add_project_dir(project_dir)
    found: dict[type[StrategyPlugin], str] = {}
    problems: list[str] = []
    for name in _candidates(project_dir):
        try:
            mod = importlib.import_module(name)
        except Exception as exc:
            problems.append(f"{name}: cannot import: {type(exc).__name__}: {exc}")
            continue
        for attr, value in sorted(vars(mod).items()):
            if (
                not isinstance(value, type)
                or not issubclass(value, StrategyPlugin)
                or value is StrategyPlugin
                or inspect.isabstract(value)
                or getattr(value, "api_version", None) != PLUGIN_API_VERSION
                or not value.__module__.startswith(name)
            ):
                continue
            path = f"{name}:{attr}"
            if value not in found or len(path) < len(found[value]):
                found[value] = path
    infos = [PluginInfo(module=path, cls=cls) for cls, path in found.items()]
    infos.sort(key=lambda p: (p.module.startswith(_FSE_PLUGIN_PACKAGE), p.label.lower()))
    return infos, problems


def load_plugin_classes(
    cfg: RunnerConfig, *, project_dir: Path | None
) -> dict[str, type[StrategyPlugin]]:
    """The class of every enabled plugin, by id; every failure reported together."""
    out: dict[str, type[StrategyPlugin]] = {}
    problems: list[str] = []
    for entry in cfg.enabled_plugins:
        try:
            out[entry.id] = resolve_plugin_class(entry.id, entry.module, project_dir=project_dir)
        except PluginLoadError as exc:
            problems.extend(exc.problems)
    if problems:
        raise PluginLoadError(problems)
    return out


def needs_problems(entry: PluginEntry, needs: DataNeeds, data_plane: DataPlaneConfig) -> list[str]:
    """Every mismatch between a plugin's ``needs()`` and the data plane (design 3.6)."""
    where = f"plugin {entry.id!r}"
    out: list[str] = []
    if _ID_RE.fullmatch(entry.id) is None:
        out.append(f"{where}: the id must match {PLUGIN_ID_PATTERN}")
    given: object = needs
    if not isinstance(given, DataNeeds):
        return [*out, f"{where}: needs() returned {type(given).__name__}, not DataNeeds"]
    instruments = set(data_plane.instruments)
    for need in needs.bars:
        if need.instrument not in instruments:
            out.append(
                f"{where}: bars of {need.instrument!r} are not in data_plane.instruments "
                f"{list(data_plane.instruments)}"
            )
        if need.interval_s != BAR_INTERVAL_S:
            out.append(
                f"{where}: bar interval {need.interval_s} s for {need.instrument}; "
                f"only {BAR_INTERVAL_S} s is supported"
            )
    sky = needs.skylit
    dp = data_plane.skylit
    if sky is not None:
        if not dp.enabled:
            out.append(f"{where}: needs Skylit data but data_plane.skylit.enabled is false")
        else:
            missing = sorted(set(sky.symbols) - set(dp.symbols))
            if missing:
                out.append(
                    f"{where}: Skylit symbols {missing} are not in data_plane.skylit.symbols"
                )
            view_id = heatmap_view(dp.heatmap_view).view_id()
            if sky.view_id != view_id:
                out.append(
                    f"{where}: Skylit view {sky.view_id!r} differs from the data plane's "
                    f"{view_id!r}"
                )
            if sky.mode != dp.mode:
                out.append(
                    f"{where}: Skylit mode {sky.mode} differs from the data plane's {dp.mode}"
                )
            if sky.layout == "matrix" and dp.layout != "matrix":
                out.append(
                    f"{where}: needs the matrix layout but data_plane.skylit.layout is {dp.layout}"
                )
            if sky.refresh_interval_s != dp.refresh_interval_s:
                out.append(
                    f"{where}: Skylit refresh {sky.refresh_interval_s} s differs from the data "
                    f"plane's {dp.refresh_interval_s} s"
                )
            start, end = sky.window
            if not (dp.window.start <= start < end <= dp.window.end):
                out.append(
                    f"{where}: Skylit window {start:%H:%M}-{end:%H:%M} is not inside the data "
                    f"plane's {dp.window.start:%H:%M}-{dp.window.end:%H:%M}"
                )
            if sky.dark_pool_tickers:
                if not dp.dark_pool.enabled:
                    out.append(f"{where}: needs dark-pool prints but dark_pool.enabled is false")
                extra = sorted(set(sky.dark_pool_tickers) - set(dp.dark_pool.tickers))
                if extra:
                    out.append(
                        f"{where}: dark-pool tickers {extra} are not in data_plane.skylit."
                        "dark_pool.tickers"
                    )
    if needs.brokerage == "self":
        if entry.fills is not None or entry.risk is not None:
            out.append(f"{where}: a self-brokered plugin must not set fills or risk")
    else:
        if entry.risk is None:
            out.append(f"{where}: a host-brokered plugin needs a risk block with max_contracts")
        rt = {} if entry.fills is None else entry.fills.commission_rt
        for need in needs.bars:
            if need.instrument not in rt:
                out.append(f"{where}: fills.commission_rt has no entry for {need.instrument}")
    return out
