"""``fse`` subcommands, one module each (design "Module layout").

:mod:`fse.cli` imports every public module in this package (names without a
leading ``_``) and calls its ``register(subparsers)``. ``register`` adds one
subparser and sets two defaults on it:

- ``handler``: a :data:`CommandHandler`, called with the parsed arguments and
  a :class:`CommandContext`; it returns the exit status;
- ``failure_exit_code`` (optional): the status for a failure the command does
  not classify itself, such as an unreadable ``.env`` file or an unexpected
  error. Without it the CLI uses 3 for an unreadable ``.env`` and 1 otherwise.

A handler writes every line through ``context.writer`` (the Log_Writer) and
signals an expected failure by raising an exception with an integer
``exit_code`` attribute, such as ``PathGuardError`` (2) or ``CacheError`` (4).
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fse.logio.log_writer import LogWriter
from fse.secrets.env import EnvView

__all__ = ["CommandContext", "CommandHandler", "SubParsers"]

type SubParsers = argparse._SubParsersAction[argparse.ArgumentParser]


@dataclass(frozen=True, slots=True)
class CommandContext:
    """What every command handler receives besides its arguments."""

    env: EnvView  # shell values, then the Project .env (Req 1.6)
    writer: LogWriter  # every console line goes through it (Req 1.9)
    project_dir: Path | None  # the Project folder; None when not installed from it


type CommandHandler = Callable[[argparse.Namespace, CommandContext], int]
