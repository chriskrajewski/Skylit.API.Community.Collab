"""The Project folder and the default cache and output directories (design §1, Req 1.6, 1.10).

Project folder. Every command reads ``.env`` from the Project folder
(``members/alantiix/skylit-futures-strategy-engine/``), never from the current
directory: Req 1.6 allows values only from the shell environment and the
Project ``.env`` file. :func:`project_dir` finds the folder from this package's
own location, which works for the editable install the README describes
(``pip install -e .``), from any current directory. When the package is not
installed from the Project folder (a plain wheel install), there is no Project
folder and commands read the shell environment only.

Defaults live under ``~/.skylit-fse/``, outside the repository working tree.
The home directory is read at call time, so a changed ``HOME`` is honoured.
Nothing here creates a directory: the path guard checks a path first.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "APP_DIR_NAME",
    "CACHE_DIR_NAME",
    "LIVE_STATE_DIR_NAME",
    "LOGS_DIR_NAME",
    "OUTPUT_DIR_NAMES",
    "PROJECT_NAME",
    "RECORDINGS_DIR_NAME",
    "RUNS_DIR_NAME",
    "DefaultPaths",
    "app_home",
    "default_paths",
    "project_dir",
]

# The [project] name in the Project folder's pyproject.toml.
PROJECT_NAME = "skylit-futures-strategy-engine"

APP_DIR_NAME = ".skylit-fse"

CACHE_DIR_NAME = "cache"
RUNS_DIR_NAME = "runs"
RECORDINGS_DIR_NAME = "recordings"
LIVE_STATE_DIR_NAME = "live-state"
LOGS_DIR_NAME = "logs"

# The Project .gitignore lists each of these names (Req 1.12).
OUTPUT_DIR_NAMES: tuple[str, ...] = (
    CACHE_DIR_NAME,
    RUNS_DIR_NAME,
    RECORDINGS_DIR_NAME,
    LIVE_STATE_DIR_NAME,
    LOGS_DIR_NAME,
)


@dataclass(frozen=True, slots=True)
class DefaultPaths:
    """The default Data_Cache and output directories under one root."""

    root: Path
    cache: Path
    runs: Path
    recordings: Path
    live_state: Path
    logs: Path


def app_home(home: Path | None = None) -> Path:
    """``<home>/.skylit-fse`` as an absolute path; ``home`` defaults to ``Path.home()``."""
    base = Path.home() if home is None else home
    return base.absolute() / APP_DIR_NAME


def default_paths(home: Path | None = None) -> DefaultPaths:
    """Every default directory under ``app_home(home)``. Creates nothing."""
    root = app_home(home)
    return DefaultPaths(
        root=root,
        cache=root / CACHE_DIR_NAME,
        runs=root / RUNS_DIR_NAME,
        recordings=root / RECORDINGS_DIR_NAME,
        live_state=root / LIVE_STATE_DIR_NAME,
        logs=root / LOGS_DIR_NAME,
    )


def project_dir(package_file: Path | None = None) -> Path | None:
    """The Project folder that holds this package's source, or ``None``.

    The package lives at ``<Project folder>/src/fse/``. The folder two levels
    above the package counts only when it holds a ``pyproject.toml`` whose
    ``[project]`` name is :data:`PROJECT_NAME`, so a wheel installed into
    ``site-packages`` never reads a ``.env`` from an unrelated folder.
    ``package_file`` (default: this module) is any file directly inside the
    package. Reads only ``pyproject.toml``; never reads ``.env``.
    """
    module = Path(__file__) if package_file is None else package_file
    candidate = module.resolve().parent.parent.parent
    try:
        with (candidate / "pyproject.toml").open("rb") as fh:
            name = tomllib.load(fh).get("project", {}).get("name")
    except OSError, tomllib.TOMLDecodeError, AttributeError:
        return None
    return candidate if name == PROJECT_NAME else None
