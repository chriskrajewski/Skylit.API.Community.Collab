"""``fse pull``: pull Heatseeker history, futures bars, VIX and dark-pool prints (design §3-4).

``fse pull --start YYYY-MM-DD --end YYYY-MM-DD [options]`` runs
:func:`fse.data.puller.run_pull`. Before any network request it checks the
flags, loads the calendar files for the range (``CalendarError``, exit 2),
checks the dates against the exchange calendar and the current New York date
(``PullPlanError``, exit 2) and runs the path guard on the Data_Cache and
output directories (``PathGuardError``, exit 2). Defaults are the requirement
defaults (Req 3.1-3.4, 3.7, 4.1, 4.12).

Outputs go to ``--out`` (default ``~/.skylit-fse/runs/pull_{pull_id}``):
``coverage_{pull_id}.md`` and ``.json`` (Req 3.11) and ``fetch_log.jsonl``
(one line per Skylit attempt, Req 2.8). Rerunning the same command resumes:
completed sessions' stored windows are served from the Data_Cache.

ProjectX is the alternate bar source (Req 4.3) and the only source of
sub-minute bars (OQ9). It is set up only when ``PROJECTX_USERNAME`` and
``PROJECTX_API_KEY`` are both set, and logs in only when a bar request needs
it. ``--config`` (task 20.4) will set dark-pool fetching from the
Strategy_Config; until then ``--dark-pool`` turns it on.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import secrets
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from datetime import time as wall_time
from pathlib import Path
from typing import ClassVar, Final

import httpx

from fse.calendars import Calendars, load_calendars, project_calendar_dir
from fse.clock import Clock, SystemClock
from fse.commands import CommandContext, SubParsers
from fse.data.cache import (
    STORAGE_INTERVAL_MAX_S,
    STORAGE_INTERVAL_MIN_S,
    DataCache,
    HeatmapView,
    ViewLimit,
    check_storage_interval,
)
from fse.data.darkpool import DEFAULT_DARK_POOL_TICKERS
from fse.data.estimate import (
    DEFAULT_HISTORICAL_CREDITS,
    DEFAULT_RANGE_CREDITS,
    DEFAULT_SIZE_LIMIT_GB,
)
from fse.data.path_guard import check_output_dirs
from fse.data.planner import (
    DEFAULT_SAMPLE_INTERVAL_MINUTES,
    DEFAULT_SYMBOLS,
    LABEL_SAMPLE_MAX_MINUTES,
    LABEL_SAMPLE_MIN_MINUTES,
    SAMPLE_INTERVAL_MAX_MINUTES,
    SAMPLE_INTERVAL_MIN_MINUTES,
    PullSpec,
    pull_date,
    validate_pull,
)
from fse.data.puller import (
    BAR_INTERVALS_S,
    DEFAULT_BAR_INTERVAL_S,
    DEFAULT_INSTRUMENTS,
    PullContext,
    PullOptions,
    run_pull,
)
from fse.logio import LogWriter
from fse.projectx.bars import ProjectXBars
from fse.projectx.session import API_KEY_VARIABLE, USERNAME_VARIABLE, ProjectXSession
from fse.secrets.env import EnvView
from fse.settings import default_paths
from fse.skylit.client import ClientConfig, SkylitClient
from fse.skylit.fetch_log import FETCH_LOG_FILE_NAME, FetchLog
from fse.timekit import NS_PER_SECOND, Instant, SessionTimes

__all__ = [
    "EXIT_INTERRUPTED",
    "EXIT_INVALID_INPUT",
    "NAME",
    "PullUsageError",
    "new_pull_id",
    "options_from_args",
    "register",
    "run_pull_command",
    "session_times_from_args",
]

NAME: Final = "pull"

# Design "Exit codes".
EXIT_INVALID_INPUT: Final = 2
EXIT_INTERRUPTED: Final = 130

_DEFAULT_TIMES: Final = SessionTimes()

_DESCRIPTION = """\
Pull Heatseeker history into the Data_Cache for a range of sessions (start and
end inclusive), with ES and NQ bars, VIX daily values and, with --dark-pool,
dark-pool prints.

Sessions less than 365 days old come from GET /v1/historical/range at full
resolution; older sessions are sampled with GET /v1/historical every
--sample-interval-min minutes. Newest sessions are pulled first. The estimate
(requests, credits, disk) is printed before the first Replay_Request; above
--size-limit-gb the pull asks for confirmation, and a non-interactive run stops.

Rerun the same command to resume: completed sessions' stored windows are served
from the Data_Cache, and windows left incomplete are requested again."""

_EPILOG = """\
exit status:
  0    the pull ran to the end (windows whose requests failed stay incomplete;
       rerun to retry them)
  2    invalid input: a flag, the date range, a calendar file, a path, or a pull
       over the size limit that was not confirmed
  3    blank SKYLIT_API_KEY, Skylit 401/402/403, or a ProjectX login failure
  4    GET /v1/symbols failed, a Data_Cache write failed, or Atlas and ProjectX
       bar timestamps disagree by a whole bar (OQ10)
  130  interrupted; the coverage report is still written"""


class PullUsageError(Exception):
    """Flags that cannot be combined or turned into a pull (exit 2)."""

    exit_code: ClassVar[int] = EXIT_INVALID_INPUT


# ---------------------------------------------------------------- argument types


def _date(text: str) -> date:
    try:
        if len(text) != 10:
            raise ValueError(text)
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a YYYY-MM-DD date") from None


def _name_list(text: str) -> tuple[str, ...]:
    items = tuple(item.strip() for item in text.split(","))
    if not items or any(not item for item in items):
        raise argparse.ArgumentTypeError(f"{text!r} is not a comma-separated list of names")
    return items


def _whole(text: str, lo: int, hi: int, unit: str) -> int:
    try:
        value = int(text)
    except ValueError:
        value = lo - 1
    if not lo <= value <= hi:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a whole number of {unit} from {lo} to {hi}"
        )
    return value


def _between(lo: int, hi: int, unit: str) -> Callable[[str], int]:
    def parse(text: str) -> int:
        return _whole(text, lo, hi, unit)

    return parse


def _view_limit(text: str) -> ViewLimit:
    if text == "all":
        return "all"
    try:
        value = int(text)
    except ValueError:
        value = 0
    if value < 1:
        raise argparse.ArgumentTypeError(f"{text!r} is neither a whole number above 0 nor 'all'")
    return value


def _storage_interval(text: str) -> int:
    value = _whole(text, STORAGE_INTERVAL_MIN_S, STORAGE_INTERVAL_MAX_S, "seconds")
    try:
        return check_storage_interval(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _bar_interval(text: str) -> int:
    value = _whole(text, 1, 60, "seconds")
    if value not in BAR_INTERVALS_S:
        raise argparse.ArgumentTypeError(
            f"{text!r} does not divide a minute; use one of {', '.join(map(str, BAR_INTERVALS_S))}"
        )
    return value


def _positive_gb(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        value = 0.0
    if not 0 < value < float("inf"):
        raise argparse.ArgumentTypeError(f"{text!r} is not a number of GB above 0")
    return value


def _wall(text: str) -> wall_time:
    if len(text) != 5 or text[2] != ":":
        raise ValueError(text)
    return wall_time(int(text[:2]), int(text[3:]))


def _pull_window(text: str) -> tuple[wall_time, wall_time]:
    try:
        start, end = text.split("-")
        lo, hi = _wall(start.strip()), _wall(end.strip())
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a New York time range HH:MM-HH:MM"
        ) from None
    if not lo < hi:
        raise argparse.ArgumentTypeError(f"the Pull_Window {text!r} must start before it ends")
    return lo, hi


# ---------------------------------------------------------------- the parser


def register(subparsers: SubParsers) -> None:
    parser = subparsers.add_parser(
        NAME,
        help="pull Heatseeker history, futures bars, VIX and dark-pool prints into the Data_Cache",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add = parser.add_argument
    add("--start", type=_date, required=True, metavar="YYYY-MM-DD", help="first session")
    add("--end", type=_date, required=True, metavar="YYYY-MM-DD", help="last session")
    add(
        "--symbols",
        type=_name_list,
        default=DEFAULT_SYMBOLS,
        metavar="LIST",
        help=f"Heatseeker symbols (default: {','.join(DEFAULT_SYMBOLS)})",
    )

    view = parser.add_argument_group("Heatmap_View")
    view.add_argument(
        "--max-strikes",
        type=_view_limit,
        default=HeatmapView().max_strikes,
        metavar="N|all",
        help="strikes per Snapshot (default: %(default)s)",
    )
    view.add_argument(
        "--max-expirations",
        type=_view_limit,
        default=HeatmapView().max_expirations,
        metavar="N|all",
        help="expirations per Snapshot (default: %(default)s)",
    )
    view.add_argument(
        "--expirations",
        type=_name_list,
        metavar="LIST",
        help="only these expirations, YYYY-MM-DD,... (default: the nearest ones)",
    )
    view.add_argument("--include-empty", action="store_true", help="keep strikes with no exposure")

    timing = parser.add_argument_group("sessions and sampling")
    timing.add_argument(
        "--pull-window",
        type=_pull_window,
        default=(_DEFAULT_TIMES.pull_start, _DEFAULT_TIMES.pull_end),
        metavar="HH:MM-HH:MM",
        help="Pull_Window in New York time; it ends at the early close on early-close "
        f"days (default: {_DEFAULT_TIMES.pull_start:%H:%M}-{_DEFAULT_TIMES.pull_end:%H:%M})",
    )
    timing.add_argument(
        "--sample-interval-min",
        type=_between(SAMPLE_INTERVAL_MIN_MINUTES, SAMPLE_INTERVAL_MAX_MINUTES, "minutes"),
        default=DEFAULT_SAMPLE_INTERVAL_MINUTES,
        metavar="N",
        help="GET /v1/historical sample interval for sessions 365 or more days old, "
        f"{SAMPLE_INTERVAL_MIN_MINUTES} to {SAMPLE_INTERVAL_MAX_MINUTES} (default: %(default)s)",
    )
    timing.add_argument(
        "--label-sample-minutes",
        type=_between(LABEL_SAMPLE_MIN_MINUTES, LABEL_SAMPLE_MAX_MINUTES, "minutes"),
        metavar="N",
        help="also sample GET /v1/historical every N minutes on range sessions, for "
        "nodeType labels (default: off)",
    )
    timing.add_argument(
        "--storage-interval-s",
        type=_storage_interval,
        metavar="N",
        help="store one Snapshot per N seconds of each Cache_Window; N divides 900 "
        "(default: every Snapshot)",
    )

    budget = parser.add_argument_group("estimate")
    budget.add_argument(
        "--size-limit-gb",
        type=_positive_gb,
        default=DEFAULT_SIZE_LIMIT_GB,
        metavar="GB",
        help="ask before a pull estimated above this size (default: %(default)g)",
    )
    budget.add_argument(
        "--range-credits",
        type=_between(0, 1_000_000, "credits"),
        default=DEFAULT_RANGE_CREDITS,
        metavar="N",
        help="credits per GET /v1/historical/range request (default: %(default)s)",
    )
    budget.add_argument(
        "--historical-credits",
        type=_between(0, 1_000_000, "credits"),
        default=DEFAULT_HISTORICAL_CREDITS,
        metavar="N",
        help="credits per GET /v1/historical request (default: %(default)s)",
    )

    aux = parser.add_argument_group("futures bars and dark pool")
    aux.add_argument(
        "--instruments",
        type=_name_list,
        default=DEFAULT_INSTRUMENTS,
        metavar="LIST",
        help=f"futures instruments of the roll calendar (default: {','.join(DEFAULT_INSTRUMENTS)})",
    )
    aux.add_argument(
        "--bar-interval",
        type=_bar_interval,
        default=DEFAULT_BAR_INTERVAL_S,
        metavar="SECONDS",
        help="bar size; sub-minute bars come from ProjectX (default: %(default)s)",
    )
    aux.add_argument(
        "--dark-pool",
        action="store_true",
        help="fetch dark-pool prints, for a config that enables dark_pool_confluence",
    )
    aux.add_argument(
        "--dark-pool-tickers",
        type=_name_list,
        metavar="LIST",
        help="dark-pool tickers, with --dark-pool "
        f"(default: {','.join(DEFAULT_DARK_POOL_TICKERS)})",
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
    paths.add_argument(
        "--out",
        type=Path,
        metavar="DIR",
        help="coverage report and Fetch_Log folder (default: ~/.skylit-fse/runs/pull_<pull id>)",
    )
    parser.set_defaults(handler=run_pull_command)


# ---------------------------------------------------------------- arguments to options


def session_times_from_args(args: argparse.Namespace) -> SessionTimes:
    start, end = args.pull_window
    try:
        return SessionTimes(pull_start=start, pull_end=end)
    except ValueError as exc:
        raise PullUsageError(f"--pull-window: {exc}") from None


def options_from_args(args: argparse.Namespace) -> PullOptions:
    """The parsed flags as :class:`PullOptions`; :class:`PullUsageError` (exit 2) if invalid."""
    tickers: tuple[str, ...] = ()
    if args.dark_pool:
        tickers = (
            DEFAULT_DARK_POOL_TICKERS if args.dark_pool_tickers is None else args.dark_pool_tickers
        )
    elif args.dark_pool_tickers is not None:
        raise PullUsageError("--dark-pool-tickers applies only with --dark-pool")
    try:
        view = HeatmapView(
            max_strikes=args.max_strikes,
            max_expirations=args.max_expirations,
            expirations=args.expirations,
            include_empty=args.include_empty,
        )
        spec = PullSpec(
            start=args.start,
            end=args.end,
            symbols=tuple(args.symbols),
            view=view,
            sample_interval_minutes=args.sample_interval_min,
            label_sample_minutes=args.label_sample_minutes,
        )
        problems = spec.problems()
        if problems:
            raise ValueError("; ".join(problems))
        return PullOptions(
            spec=spec,
            instruments=tuple(args.instruments),
            bar_interval_s=args.bar_interval,
            storage_interval_s=args.storage_interval_s,
            size_limit_gb=args.size_limit_gb,
            range_credits=args.range_credits,
            historical_credits=args.historical_credits,
            dark_pool_tickers=tuple(tickers),
        )
    except ValueError as exc:
        raise PullUsageError(str(exc)) from None


def new_pull_id(started_ns: Instant) -> str:
    """``YYYYMMDDTHHMMSSZ-xxxxxx``: the UTC start second plus 6 random hex digits."""
    stamp = datetime.fromtimestamp(started_ns // NS_PER_SECOND, tz=UTC)
    return f"{stamp:%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"


# ---------------------------------------------------------------- the handler


def run_pull_command(args: argparse.Namespace, ctx: CommandContext) -> int:
    """Check everything local, then run the pull. Failures raise with an exit code."""
    options = options_from_args(args)
    times = session_times_from_args(args)
    calendar_dir: Path | None = args.calendar_dir
    if calendar_dir is None:
        if ctx.project_dir is None:
            raise PullUsageError("the Project folder was not found; pass --calendar-dir DIR")
        calendar_dir = project_calendar_dir(ctx.project_dir)
    started_ns = time.time_ns()
    spec = options.spec
    first, last = sorted((spec.start, spec.end))
    calendars = load_calendars(calendar_dir, first, last, times=times)
    sessions = calendars.exchange.sessions
    # Dates, fields and calendar coverage, before any network request; the
    # symbol listing is checked again against GET /v1/symbols.
    validate_pull(
        spec,
        listed=dict.fromkeys(spec.symbols, date.min),
        today=pull_date(started_ns),
        calendar=sessions,
    )
    defaults = default_paths()
    pull_id = new_pull_id(started_ns)
    cache_dir: Path = defaults.cache if args.cache_dir is None else args.cache_dir
    out_dir: Path = defaults.runs / f"pull_{pull_id}" if args.out is None else args.out
    checked = check_output_dirs({"cache directory": cache_dir, "pull output directory": out_dir})
    cache_dir, out_dir = checked["cache directory"], checked["pull output directory"]
    ctx.writer.echo(
        f"Pull {pull_id}: sessions {spec.start} to {spec.end}; Data_Cache {cache_dir}; "
        f"output {out_dir}"
    )
    try:
        return asyncio.run(_pull(options, ctx, calendars, cache_dir, out_dir, pull_id, started_ns))
    except asyncio.CancelledError:
        ctx.writer.error("interrupted")
        return EXIT_INTERRUPTED


async def _pull(
    options: PullOptions,
    ctx: CommandContext,
    calendars: Calendars,
    cache_dir: Path,
    out_dir: Path,
    pull_id: str,
    started_ns: Instant,
) -> int:
    sessions = calendars.exchange.sessions
    clock = SystemClock()
    rng = random.Random()
    async with httpx.AsyncClient(follow_redirects=False) as http:
        with (
            FetchLog(ctx.writer, out_dir / FETCH_LOG_FILE_NAME) as fetch_log,
            DataCache(
                cache_dir, calendar=sessions, storage_interval_s=options.storage_interval_s
            ) as cache,
        ):
            client = SkylitClient(ctx.env, ClientConfig(), fetch_log, clock, rng, http=http)
            await run_pull(
                options,
                PullContext(
                    client=client,
                    cache=cache,
                    calendar=sessions,
                    roll=calendars.roll,
                    writer=ctx.writer,
                    out_dir=out_dir,
                    pull_id=pull_id,
                    started_ns=started_ns,
                    projectx=_projectx(ctx.env, ctx.writer, http, clock, rng),
                ),
            )
    return 0


def _projectx(
    env: EnvView, writer: LogWriter, http: httpx.AsyncClient, clock: Clock, rng: random.Random
) -> ProjectXBars | None:
    """ProjectX bars when both credentials are set; it logs in on its first bar request."""
    if env.get(USERNAME_VARIABLE) is None or env.get(API_KEY_VARIABLE) is None:
        return None
    session = ProjectXSession(env, writer.redactor, http, clock)
    return ProjectXBars(session, clock, rng)
