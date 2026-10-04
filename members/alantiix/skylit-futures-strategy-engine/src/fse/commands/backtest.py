"""``fse backtest``: run a Strategy_Config over cached sessions (design §18, Req 18).

``fse backtest --config FILE --start YYYY-MM-DD --end YYYY-MM-DD [--offline]
[--seed N] [--out DIR]`` runs :func:`fse.backtest.runner.run_backtest` over
every session from ``--start`` to ``--end``, both inclusive.

Before any Decision_Time and any output file, the command loads the
Strategy_Config (every error with its key path, exit 2; contradiction warnings
are printed and the run goes on, Req 17.3-17.6), checks the date range (Req
18.2) and runs the path guard on the Data_Cache and the run directory (Req
1.11). The Backtester then loads the calendar files and checks that the range
holds a session, also exit 2.

The run reads only the local Data_Cache. It builds no Skylit_Client and never
reads ``SKYLIT_API_KEY``, with or without ``--offline`` (Req 1.8, 3.12).
``--offline`` also prints each requested session with absent or incomplete
Cache_Windows before the first Decision_Time (Req 3.13).

``--seed`` is recorded in the Run_Manifest; without it a seed is drawn,
printed and recorded (design §21). The run directory defaults to
``~/.skylit-fse/runs/<run id>``. The run id hashes the config hash, the range,
the seed and the mode, so a rerun with the same ``--seed`` needs a new
``--out``: a run directory that already holds a backtest output is refused.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import ClassVar, Final

from fse.backtest.manifest import MANIFEST_FILE_NAME
from fse.backtest.runner import (
    SEED_BITS,
    BacktestMode,
    BacktestResult,
    backtest_range,
    default_run_id,
    resolve_seed,
    run_backtest,
)
from fse.calendars import project_calendar_dir
from fse.commands import CommandContext, SubParsers
from fse.commands._config import load_config
from fse.config.hashing import config_hash
from fse.data.cache import DataCache
from fse.data.path_guard import check_output_dirs
from fse.settings import default_paths

__all__ = [
    "EXIT_INVALID_INPUT",
    "NAME",
    "BacktestUsageError",
    "register",
    "run_backtest_command",
    "summary_lines",
]

NAME: Final = "backtest"

# Design "Exit codes".
EXIT_INVALID_INPUT: Final = 2

MODE: Final[BacktestMode] = "historical"
SEED_MAX: Final = 2**SEED_BITS - 1

_DESCRIPTION = """\
Run a Strategy_Config over the cached sessions from --start to --end (both
inclusive): the Strategy_Engine at every Decision_Time, with the
Fill_Simulator and the Account_Simulator.

The run reads only the local Data_Cache and makes no network request. A
session with no Snapshot for a configured symbol and metric, or no bars for a
configured instrument, is skipped and listed in the Run_Manifest. With
--offline, every session with absent or incomplete Cache_Windows is printed
before the first Decision_Time.

The run directory gets decision_log.jsonl, trades.csv, trades.json,
combine_attempts.json, session_outcomes.json, setups.json (each Setup_Key's
final status and Shadow_Trade), shadow_trades.csv, gate_funnel.json,
report_inputs.json and run_manifest.json. fse report --run <run id> writes
the report from them."""

_EPILOG = """\
exit status:
  0    the run completed
  1    unexpected error; the Run_Manifest is written with status aborted
  2    invalid input: a flag, the Strategy_Config, the date range, a calendar
       file, a path, or a run directory that already holds a backtest output
  4    a cached file failed its check; the Run_Manifest is written with status
       aborted
  130  interrupted; the Run_Manifest is written with status aborted"""


class BacktestUsageError(Exception):
    """Flags that cannot be turned into a backtest (exit 2)."""

    exit_code: ClassVar[int] = EXIT_INVALID_INPUT


# ---------------------------------------------------------------- argument types


def _date(text: str) -> date:
    try:
        if len(text) != 10:
            raise ValueError(text)
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a YYYY-MM-DD date") from None


def _seed(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        value = -1
    if not 0 <= value <= SEED_MAX:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a whole number from 0 to {SEED_MAX} (2**{SEED_BITS} - 1)"
        )
    return value


# ---------------------------------------------------------------- the parser


def register(subparsers: SubParsers) -> None:
    parser = subparsers.add_parser(
        NAME,
        help="run a Strategy_Config over cached sessions",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add = parser.add_argument
    add("--config", type=Path, required=True, metavar="FILE", help="the Strategy_Config file")
    add("--start", type=_date, required=True, metavar="YYYY-MM-DD", help="first session")
    add("--end", type=_date, required=True, metavar="YYYY-MM-DD", help="last session")
    add(
        "--offline",
        action="store_true",
        help="print each session with absent or incomplete Cache_Windows before the run",
    )
    add(
        "--seed",
        type=_seed,
        metavar="N",
        help="the random seed recorded in the Run_Manifest (default: a new one, printed)",
    )
    add(
        "--out",
        type=Path,
        metavar="DIR",
        help="the run directory (default: ~/.skylit-fse/runs/<run id>)",
    )

    paths = parser.add_argument_group("paths")
    paths.add_argument(
        "--cache-dir",
        type=Path,
        metavar="DIR",
        help="the Data_Cache (default: ~/.skylit-fse/cache)",
    )
    paths.add_argument(
        "--calendar-dir",
        type=Path,
        metavar="DIR",
        help="the folder holding the calendar files (default: the Project's calendars/)",
    )
    parser.set_defaults(handler=run_backtest_command)


# ---------------------------------------------------------------- the handler


def run_backtest_command(args: argparse.Namespace, ctx: CommandContext) -> int:
    """Check everything local, then run the backtest. Failures raise with an exit code."""
    config_path: Path = args.config
    cfg = load_config(config_path, ctx.writer)
    data_range = backtest_range(args.start, args.end)
    calendar_dir: Path | None = args.calendar_dir
    if calendar_dir is None:
        if ctx.project_dir is None:
            raise BacktestUsageError("the Project folder was not found; pass --calendar-dir DIR")
        calendar_dir = project_calendar_dir(ctx.project_dir)
    seed = resolve_seed(args.seed)
    run_id = default_run_id(config_hash(cfg), data_range, seed, MODE)
    defaults = default_paths()
    cache_dir: Path = defaults.cache if args.cache_dir is None else args.cache_dir
    out_dir: Path = defaults.runs / run_id if args.out is None else args.out
    checked = check_output_dirs({"cache directory": cache_dir, "backtest run directory": out_dir})
    cache_dir, out_dir = checked["cache directory"], checked["backtest run directory"]
    ctx.writer.echo(
        f"Backtest {run_id}: sessions {data_range.start} to {data_range.end}; seed {seed}; "
        f"config {config_path}; Data_Cache {cache_dir}; output {out_dir}"
    )
    with DataCache(cache_dir) as cache:
        result = run_backtest(
            cfg,
            data_range,
            cache,
            out_dir,
            seed,
            MODE,
            writer=ctx.writer,
            calendar_dir=calendar_dir,
            offline=args.offline,
            config_path=str(config_path),
            run_id=run_id,
        )
    for line in summary_lines(result):
        ctx.writer.echo(line)
    return 0


def summary_lines(result: BacktestResult) -> list[str]:
    """What the run did: sessions, skips, Decision_Times, trades, attempts and the manifest."""
    manifest = result.manifest
    lines = [
        f"Backtest {manifest.spec.run_id} {manifest.status}: "
        f"{len(manifest.sessions_evaluated)} sessions evaluated, "
        f"{len(manifest.sessions_skipped)} skipped; {result.decision_times} Decision_Times; "
        f"{len(result.trades)} trades; {len(result.attempts)} Combine_Attempts"
    ]
    for s in manifest.sessions_skipped:
        names = f" for {', '.join(s.names)}" if s.names else ""
        lines.append(f"skipped {s.session}: no {' or '.join(s.missing)}{names}")
    lines.append(f"Run_Manifest: {result.run_dir / MANIFEST_FILE_NAME}")
    return lines
