"""Test-only research CLIs, so the catalog's research-program support stays covered.

The shipped catalog lists only ``fse``. A user adds ``python -m research.<name>.run`` CLIs
as extra :class:`~fse_ui.catalog.Program` entries; the console tests register these two
(never imported, only parsed) to exercise that path:

- ``research.example.run`` with the subcommands ``backtest`` (a ``--config`` that picks the
  ``example-*`` research configs) and ``host-diff`` (runs on the host on AWS);
- ``research.example.check``, a flat CLI with one ``--date`` option (also host-local).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from fse_ui.catalog import Program

__all__ = ["HOST_LOCAL", "PROGRAMS"]


def _run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m research.example.run", description="Example research CLI."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    backtest = sub.add_parser("backtest", help="back-test the example plugin")
    backtest.add_argument(
        "--config", type=Path, help="the example research config (YAML)"
    )
    backtest.add_argument("--first", help="first slot date, YYYY-MM-DD")
    backtest.add_argument("--last", help="last slot date, YYYY-MM-DD")
    backtest.add_argument("--out", help="output folder")
    host_diff = sub.add_parser("host-diff", help="compare a paper epoch with a replay")
    host_diff.add_argument("--epoch", required=True, help="the epoch id")
    return parser


def _check_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m research.example.check", description="Check one day."
    )
    parser.add_argument("--date", help="the day to check, YYYY-MM-DD")
    return parser


PROGRAMS: tuple[Program, ...] = (
    Program("research.example.run", "research example", "research.example.run", _run_parser),
    Program(
        "research.example.check", "example check", "research.example.check", _check_parser
    ),
)

HOST_LOCAL: frozenset[tuple[str, tuple[str, ...]]] = frozenset(
    {("research.example.run", ("host-diff",)), ("research.example.check", ())}
)
