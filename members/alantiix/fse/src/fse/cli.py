"""The ``fse`` command line (design "Module layout", "Exit codes").

``fse <command> [options]``. Each command is a module in :mod:`fse.commands`
that exposes ``register(subparsers)``; :func:`discover_commands` finds them, so
adding a command needs no change here.

Environment. Variables come from the shell environment and from the ``.env``
file in the Project folder (:func:`fse.settings.project_dir`), never from a
``.env`` in the current directory (Req 1.6). Without a Project folder (the
package was not installed from it) only the shell environment is read.

Output. Every line goes through one Log_Writer built from those values (Req
1.9): argparse help, usage and errors are captured and re-emitted through it,
command output uses ``context.writer``, and :meth:`LogWriter.install` redacts
``logging`` records and uncaught-error output while a command runs.

Exit statuses (design "Exit codes"):

====  ====================================================================
0     success
1     Secret_Scanner found matching files; for other commands, an
      unexpected internal error (redacted traceback on stderr)
2     invalid input, including command-line usage errors
3     credentials or access, including an unreadable ``.env`` file
4     data or I/O failure
5     Secret_Scanner could not run
130   Operator interrupt
====  ====================================================================

An exception with an integer ``exit_code`` attribute (``PathGuardError``,
``CalendarError``, ``CacheError``, ``ScanError``, ...) is an expected failure:
its message is printed as ``error:`` lines and its ``exit_code`` returned.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import os
import pkgutil
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Final, TextIO

import fse.commands
from fse.commands import CommandContext, SubParsers
from fse.logio.log_writer import LogWriter
from fse.secrets.env import EnvFileError, EnvView, load_env
from fse.settings import project_dir as find_project_dir

__all__ = [
    "EXIT_CREDENTIALS",
    "EXIT_DATA_IO",
    "EXIT_FAILURE",
    "EXIT_INTERRUPTED",
    "EXIT_INVALID_INPUT",
    "EXIT_OK",
    "EXIT_SCAN_CANNOT_RUN",
    "EXIT_SECRETS_FOUND",
    "build_parser",
    "discover_commands",
    "main",
    "run",
]

# Design "Exit codes".
EXIT_OK: Final = 0
EXIT_SECRETS_FOUND: Final = 1
EXIT_INVALID_INPUT: Final = 2
EXIT_CREDENTIALS: Final = 3
EXIT_DATA_IO: Final = 4
EXIT_SCAN_CANNOT_RUN: Final = 5
EXIT_INTERRUPTED: Final = 130
# An unexpected error in a command that sets no failure_exit_code.
EXIT_FAILURE: Final = 1

type Register = Callable[[SubParsers], None]

_EPILOG = """\
exit status:
  0    success
  1    scan-secrets found matching files; other commands: unexpected error
  2    invalid input (config, calendar, date range, path, usage)
  3    credentials or access (blank SKYLIT_API_KEY, 401/402/403, login failure)
  4    data or I/O failure
  5    scan-secrets could not run
  130  interrupted

Variables are read from the shell environment and from the .env file in the
Project folder (a non-blank shell value wins), never from the current directory."""


def main() -> int:
    """Console-script entry point: ``fse`` with ``sys.argv``."""
    return run(sys.argv[1:], project_dir=find_project_dir())


def run(
    argv: Sequence[str] | None = None,
    *,
    project_dir: Path | None,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Parse ``argv``, run the command, and return the exit status.

    ``project_dir`` is the folder whose ``.env`` is read (``None``: no
    ``.env``). ``environ`` defaults to ``os.environ``; ``stdout`` and
    ``stderr`` default to ``sys.stdout`` and ``sys.stderr`` at write time.
    """
    shell = dict(os.environ if environ is None else environ)
    env_error: EnvFileError | None = None
    try:
        env = EnvView(shell, {}) if project_dir is None else load_env(project_dir, environ=shell)
    except EnvFileError as exc:
        # Redact with the shell values that are known; report once the command is known.
        env, env_error = EnvView(shell, {}), exc
    writer = LogWriter.from_env(env, stdout=stdout, stderr=stderr)
    with writer.install():
        return _run(argv, writer, env, env_error, project_dir)


def build_parser(commands: Sequence[Register] | None = None) -> argparse.ArgumentParser:
    """The ``fse`` parser with one subcommand per ``register`` (default: discovered)."""
    parser = argparse.ArgumentParser(
        prog="fse",
        description="Skylit futures strategy engine: pull, backtest, report and paper runs.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        color=False,  # plain text: help is captured and redacted before it is printed
    )
    subparsers = parser.add_subparsers(
        dest="command", metavar="<command>", title="commands", required=True
    )
    for register in discover_commands() if commands is None else commands:
        register(subparsers)
    return parser


def discover_commands(package: ModuleType = fse.commands) -> list[Register]:
    """``register`` of each public module in ``package``, in module-name order.

    Modules whose name starts with ``_`` are skipped, as are modules without a
    callable ``register``. An import error propagates: a broken command module
    is a bug, not a missing command.
    """
    found: list[Register] = []
    for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda m: m.name):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{package.__name__}.{info.name}")
        register = getattr(module, "register", None)
        if callable(register):
            found.append(register)
    return found


# ---------------------------------------------------------------- internals


def _run(
    argv: Sequence[str] | None,
    writer: LogWriter,
    env: EnvView,
    env_error: EnvFileError | None,
    project_dir: Path | None,
) -> int:
    args: argparse.Namespace | None = None
    try:
        parsed = _parse(build_parser(), argv, writer)
        if isinstance(parsed, int):
            return parsed
        args = parsed
        if env_error is not None:
            writer.error(f"error: {env_error}")
            return _failure_code(args, EXIT_CREDENTIALS)
        handler = getattr(args, "handler", None)
        if not callable(handler):
            raise RuntimeError(f"command {args.command!r} registered no handler")
        status = handler(args, CommandContext(env=env, writer=writer, project_dir=project_dir))
        return _exit_status(status, writer)
    except KeyboardInterrupt:
        writer.error("interrupted")
        return EXIT_INTERRUPTED
    except SystemExit as exc:  # raised inside a command
        return _exit_status(exc.code, writer)
    except Exception as exc:
        code = getattr(exc, "exit_code", None)
        if isinstance(code, int) and not isinstance(code, bool):
            for line in str(exc).splitlines() or [type(exc).__name__]:
                writer.error(f"error: {line}")
            return code
        writer.exception(exc, message="error: unexpected failure")
        return _failure_code(args, EXIT_FAILURE)


def _parse(
    parser: argparse.ArgumentParser, argv: Sequence[str] | None, writer: LogWriter
) -> argparse.Namespace | int:
    """Parsed arguments, or the exit status when argparse exits (help, usage error).

    argparse writes to ``sys.stdout`` and ``sys.stderr`` itself; both are
    captured and re-emitted through ``writer``, so a secret typed as an
    argument is redacted in the usage error.
    """
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            return parser.parse_args(argv)
    except SystemExit as exc:
        return _exit_status(exc.code, writer)
    finally:
        if out.getvalue():
            writer.echo(out.getvalue())
        if err.getvalue():
            writer.error(err.getvalue())


def _exit_status(code: object, writer: LogWriter) -> int:
    """An exit status from a ``SystemExit`` code or a handler's return value."""
    if code is None:
        return EXIT_OK
    if isinstance(code, int) and not isinstance(code, bool):
        return code
    writer.error(f"error: {code}")
    return EXIT_FAILURE


def _failure_code(args: argparse.Namespace | None, default: int) -> int:
    code = getattr(args, "failure_exit_code", None)
    return code if isinstance(code, int) and not isinstance(code, bool) else default


if __name__ == "__main__":
    raise SystemExit(main())
