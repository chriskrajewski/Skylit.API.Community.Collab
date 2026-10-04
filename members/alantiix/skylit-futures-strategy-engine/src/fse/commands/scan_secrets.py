"""``fse scan-secrets``: the Secret_Scanner command (design §1, Req 1.13-1.16).

Output. stdout holds one line per matching file: its path relative to the
working tree root. stderr holds fixed status lines (counts, causes, paths that
could not be read). No line ever holds a secret value or any content of a
scanned file; every line also passes through the Log_Writer's Redactor.

Values come from the shell environment and the ``.env`` file in the Project
folder (see :func:`fse.settings.project_dir`), not from the current directory.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from fse.commands import CommandContext, SubParsers
from fse.logio.log_writer import LogWriter
from fse.secrets.scanner import EXIT_CANNOT_RUN, ScanResult, find_repo_root, scan

__all__ = ["NAME", "register", "report", "run"]

NAME = "scan-secrets"

_DESCRIPTION = """\
Search the working-tree content of every file in the git index, newly staged
files included, for each non-blank Secret_Variable value, and print the path
of each file that contains one. Values are read from the shell environment and
from the .env file in the Project folder (a non-blank shell value wins). No
secret value and no file content is ever printed."""

_EPILOG = """\
exit status:
  0  no tracked file contains a secret value
  1  one or more tracked files contain a secret value (paths on stdout)
  5  the scan could not run: no secret value is configured, git could not
     list the tracked files, or a tracked file could not be read"""


def register(subparsers: SubParsers) -> None:
    parser = subparsers.add_parser(
        NAME,
        help="list tracked files that contain a secret value",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--repo",
        type=Path,
        metavar="DIR",
        help=(
            "any directory inside the git working tree to scan "
            "(default: the working tree that holds the Project folder)"
        ),
    )
    parser.set_defaults(handler=run, failure_exit_code=EXIT_CANNOT_RUN)


def run(args: argparse.Namespace, ctx: CommandContext) -> int:
    """Scan, print the result, and return 0, 1 or 5.

    A :class:`~fse.secrets.scanner.ScanError` propagates to the CLI, which
    prints its cause and exits with its ``exit_code`` (5).
    """
    repo: Path | None = args.repo
    if ctx.project_dir is None:
        ctx.writer.error(
            f"{NAME}: the Project folder was not found; reading the shell environment only"
        )
    start = repo or ctx.project_dir or Path.cwd()
    result = scan(find_repo_root(start), ctx.env)
    return report(result, ctx.writer)


def report(result: ScanResult, writer: LogWriter) -> int:
    """Print ``result``: matching paths on stdout, status lines on stderr. Returns the status."""
    for path in result.matches:
        writer.echo(_shown(path))
    for path in result.unreadable:
        writer.error(f"error: cannot read tracked file {_shown(path)}")
    if result.skipped:
        writer.error(
            f"{NAME}: {_files(len(result.skipped))} in the index "
            "have no working-tree file (deleted, sparse checkout or submodule) "
            "and were not searched"
        )
    if result.matches:
        writer.error(
            f"{NAME}: {_files(len(result.matches))} of {result.searched} searched "
            "contain a secret value"
        )
    elif result.unreadable:
        writer.error(f"error: scan incomplete: {_files(len(result.unreadable))} could not be read")
    else:
        writer.error(f"{NAME}: no secret value found in {_files(result.searched)}")
    return result.exit_code


def _shown(path: str) -> str:
    """``path`` on one line: a path with a control character is printed escaped."""
    return path if path.isprintable() else repr(path)


def _files(count: int) -> str:
    return f"{count} tracked file{'' if count == 1 else 's'}"
