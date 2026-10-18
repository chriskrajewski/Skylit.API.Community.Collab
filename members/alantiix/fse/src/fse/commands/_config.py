"""Load a command's ``--config`` Strategy_Config (design §17, Req 17.3-17.6).

Commands call :func:`load_config` before any network request, Decision_Time
or output file. A rejected file raises
:class:`~fse.config.loader.ConfigLoadError` (exit 2), whose message lists
every error with its key path, one per line. The contradiction warnings of a
valid file are printed on stderr and the command goes on (Req 17.6).
"""

from __future__ import annotations

from pathlib import Path

from fse.config.loader import load_or_raise
from fse.config.schema import StrategyConfig
from fse.logio import LogWriter

__all__ = ["load_config"]


def load_config(path: Path, writer: LogWriter) -> StrategyConfig:
    """The Strategy_Config at ``path``; each warning goes through ``writer``."""
    loaded = load_or_raise(path)
    for warning in loaded.warnings:
        writer.error(str(warning))
    return loaded.config
