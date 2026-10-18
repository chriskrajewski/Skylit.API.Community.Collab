"""``fse report``: the report of a completed backtest run (design §20, Req 20.15).

``fse report --run RUN_ID [--runs-dir DIR]`` reads the run directory
``DIR/RUN_ID`` (default ``~/.skylit-fse/runs/RUN_ID``). ``--run`` may also be
the path of a run directory, for a backtest run with ``--out``. It writes, in
the run directory's ``report/`` folder:

- ``report.md``: the Markdown report (``fse.reports.markdown``);
- ``report.json``: the same values as JSON (``fse.reports.tables``);
- ``trade_excursions.csv``: MAE and MFE in R per trade (Req 20.6).

The report reads only the run directory: no Data_Cache, no network. Writing
it again gives the same files. Every file goes through the Log_Writer.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import ClassVar, Final

from fse.commands import CommandContext, SubParsers
from fse.data.path_guard import check_output_dir
from fse.reports.markdown import render
from fse.reports.tables import build_report, excursions_csv, load_run, report_jsonable
from fse.settings import default_paths

__all__ = [
    "EXCURSIONS_FILE_NAME",
    "NAME",
    "REPORT_DIR_NAME",
    "REPORT_JSON_FILE_NAME",
    "REPORT_MD_FILE_NAME",
    "ReportUsageError",
    "register",
    "run_report_command",
]

NAME: Final = "report"
REPORT_DIR_NAME: Final = "report"
REPORT_MD_FILE_NAME: Final = "report.md"
REPORT_JSON_FILE_NAME: Final = "report.json"
EXCURSIONS_FILE_NAME: Final = "trade_excursions.csv"

# Design "Exit codes".
EXIT_INVALID_INPUT: Final = 2

_DESCRIPTION = """\
Write the report of a completed backtest run: the header (sessions, trades,
data resolution, fills and costs, Holdout_Period), the metrics with their
95% bootstrap intervals, the
Gate_Funnel with the Shadow_Trades of rejected setups, the King and Gatekeeper
agreement and the Tap counts per session.

The files go into the run directory's report/ folder: report.md, report.json
and trade_excursions.csv. Nothing else is read or written."""

_EPILOG = """\
exit status:
  0  the report was written
  2  invalid input: a flag or the report path
  4  the run directory, its Run_Manifest or another run file is missing or
     unreadable, or the run did not complete"""


class ReportUsageError(Exception):
    """A ``--run`` value that names no run (exit 2)."""

    exit_code: ClassVar[int] = EXIT_INVALID_INPUT


def register(subparsers: SubParsers) -> None:
    parser = subparsers.add_parser(
        NAME,
        help="write the report of a completed backtest run",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--run",
        required=True,
        metavar="RUN_ID",
        help="the run id (or the path of the run directory)",
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        metavar="DIR",
        help="the folder holding the run directories (default: ~/.skylit-fse/runs)",
    )
    parser.set_defaults(handler=run_report_command)


def _run_dir(run: str, runs_dir: Path | None) -> Path:
    if not run.strip():
        raise ReportUsageError("--run must name a run id or a run directory")
    given = Path(run).expanduser()
    if runs_dir is None and (len(given.parts) > 1 or given.is_absolute()):
        return given
    base = default_paths().runs if runs_dir is None else runs_dir
    if len(given.parts) != 1 or given.name in {".", ".."}:
        raise ReportUsageError(f"--run {run!r} is not a run id inside --runs-dir {base}")
    return base / run


def run_report_command(args: argparse.Namespace, ctx: CommandContext) -> int:
    """Read the run, write the three report files, print where they are."""
    run_dir = _run_dir(args.run, args.runs_dir)
    report = build_report(load_run(run_dir))
    out = check_output_dir(run_dir / REPORT_DIR_NAME, label="report directory")
    writer = ctx.writer
    md = writer.write_text(out / REPORT_MD_FILE_NAME, render(report))
    writer.write_json(out / REPORT_JSON_FILE_NAME, report_jsonable(report))
    writer.write_text(out / EXCURSIONS_FILE_NAME, excursions_csv(report.trades))
    h = report.header
    writer.echo(
        f"Report of backtest {h.run_id}: {h.session_count} sessions, {h.trade_count} trades; "
        f"Holdout_Period sessions included: {len(h.holdout_included)}"
    )
    writer.echo(f"Written: {md}, {out / REPORT_JSON_FILE_NAME}, {out / EXCURSIONS_FILE_NAME}")
    return 0
