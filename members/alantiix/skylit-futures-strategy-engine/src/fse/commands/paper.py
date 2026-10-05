"""``fse paper``: today's session on live data in Paper Order_Mode (design §23, Req 23).

``fse paper --config FILE [--cache-dir DIR] [--calendar-dir DIR]
[--state-dir DIR] [--recordings-root DIR] [--out DIR]`` runs the Live_Runner
(:mod:`fse.live.runner`) for the session whose trading day holds the current
time, always in Paper Order_Mode: every order goes to the in-process
Paper_Broker and no order request is ever sent to ProjectX (Req 23.1,
23.15). A config whose ``order_mode`` is ``practice`` or ``combine`` still
runs in Paper mode here, and the first Finding_Card says so.

Inputs: Skylit Map_State (``SKYLIT_API_KEY``), ProjectX closed 1-minute bars
(the two ProjectX credential variables of ``fse.projectx.session``, read-only), the Data_Cache
for the trailing Regime medians and the VIX daily records, and the calendar
files (the roll calendar's contract and ProjectX contract id per instrument,
else ``live.expected_contracts``).

Outputs, outside the repository (default ``~/.skylit-fse/``): the run
directory ``runs/live-<session>-<id>/`` (decision log, live log, Finding_Cards,
Fetch_Log), ``recordings/<session>.jsonl.gz`` and ``live-state/``.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import ClassVar, Final

import httpx

from fse.backtest.manifest import describe_code_version
from fse.backtest.runner import trading_fees
from fse.calendars import Calendars, load_calendars, project_calendar_dir
from fse.clock import SystemClock
from fse.commands import CommandContext, SubParsers
from fse.commands._config import load_config
from fse.config.schema import StrategyConfig
from fse.data.aux_stores import VixDailyRecord
from fse.data.cache import DataCache
from fse.data.medians import MedianParams, RegimeMedianStore
from fse.data.path_guard import check_output_dirs
from fse.data.vix import prior_session
from fse.engine.step import Engine, EngineParams
from fse.live.bar_feed import FeedInstrument
from fse.live.broker_safety import BrokerSafety
from fse.live.order_router import ModeDecision
from fse.live.recorder import RECORDINGS_DIR_NAME
from fse.live.runner import (
    LiveClients,
    LiveRunner,
    LiveSession,
    LiveStartError,
    check_refresh_interval,
    feed_instruments,
    session_window,
)
from fse.notify.notifier import Notifier
from fse.pit.market_view import heatmap_view
from fse.projectx.bars import ProjectXBars
from fse.projectx.session import API_KEY_VARIABLE, USERNAME_VARIABLE, ProjectXSession
from fse.settings import default_paths
from fse.skylit.client import ClientConfig, SkylitClient
from fse.skylit.fetch_log import FETCH_LOG_FILE_NAME, FetchLog
from fse.timekit import SessionTimes, ny_datetime

__all__ = [
    "NAME",
    "BrokerSetup",
    "PaperUsageError",
    "Prepared",
    "add_live_arguments",
    "prepare",
    "register",
    "run_live",
    "run_paper_command",
]

NAME: Final = "paper"
EXIT_INVALID_INPUT: Final = 2
EXIT_CREDENTIALS: Final = 3
EXIT_FEED_STOPPED: Final = 1

_DESCRIPTION = """\
Run today's session on live data in Paper Order_Mode. The Strategy_Engine
runs at each Decision_Cadence tick and on each new Map_State or closed bar;
every order goes to the in-process Paper_Broker, which fills it by the
Fill_Simulator rules. No order request is ever sent to ProjectX.

Finding_Cards go to the configured Notifier sinks. Every input is recorded
for replay (the Backtester's replay mode), and the state is saved after every
Decision_Time, so a restart continues the session. fse halt blocks new
entries; fse clear lifts the blocks."""

_EPILOG = """\
exit status:
  0  the session ran to its end
  1  a feed stopped during the run (entries were blocked while the map was
     stale), or an unexpected error
  2  invalid input: a flag, the Strategy_Config (a refresh interval under 5 s
     included), a calendar file, a path, no session now, or no contract id
  3  SKYLIT_API_KEY or a ProjectX credential variable is blank"""


class PaperUsageError(Exception):
    """Flags or settings that cannot start a paper run (exit 2)."""

    exit_code: ClassVar[int] = EXIT_INVALID_INPUT


class PaperCredentialsError(Exception):
    """A required credential is blank (exit 3)."""

    exit_code: ClassVar[int] = EXIT_CREDENTIALS


def register(subparsers: SubParsers) -> None:
    parser = subparsers.add_parser(
        NAME,
        help="run today's session on live data in Paper Order_Mode",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_live_arguments(parser)
    parser.set_defaults(handler=run_paper_command)


def add_live_arguments(parser: argparse.ArgumentParser) -> None:
    """``--config`` and the directory flags ``fse paper`` and ``fse live`` share."""
    parser.add_argument("--config", required=True, type=Path, metavar="FILE")
    for flag, default in (
        ("--cache-dir", "~/.skylit-fse/cache"),
        ("--calendar-dir", "the Project's calendars/ folder"),
        ("--state-dir", "~/.skylit-fse/live-state"),
        ("--recordings-root", "~/.skylit-fse (recordings go in its recordings/ folder)"),
        ("--out", "~/.skylit-fse/runs/live-<session>-<id>"),
    ):
        parser.add_argument(flag, type=Path, metavar="DIR", help=f"default: {default}")


@dataclass(frozen=True, slots=True)
class Prepared:
    """Everything a live run needs, checked before any request is sent."""

    cfg: StrategyConfig
    engine: Engine
    session: date
    calendars: Calendars
    out_dir: Path
    dirs: dict[str, Path]
    notes: list[str]
    feeds: tuple[FeedInstrument, ...]
    vix: tuple[VixDailyRecord | None, VixDailyRecord | None]


type BrokerSetup = Callable[
    [Prepared, ProjectXSession, SystemClock],
    Awaitable[tuple[ModeDecision | None, BrokerSafety | None]],
]
"""Resolves the Order_Mode for ``fse live``: the decision and its BrokerSafety (or ``None``)."""


def run_paper_command(args: argparse.Namespace, ctx: CommandContext) -> int:
    prep = prepare(args, ctx)
    if prep.cfg.order_mode != "paper":
        prep.notes.append(
            f"fse paper runs in Paper Order_Mode; the config's order_mode {prep.cfg.order_mode} "
            "is not used"
        )
    return run_live(ctx, prep, None)


def prepare(args: argparse.Namespace, ctx: CommandContext) -> Prepared:
    """Load and check the config, the credentials, the calendars and the directories."""
    writer = ctx.writer
    cfg = load_config(args.config, writer)
    check_refresh_interval(cfg)
    for name in ("SKYLIT_API_KEY", USERNAME_VARIABLE, API_KEY_VARIABLE):
        value = ctx.env.get(name)
        if value is None or not value.strip():
            raise PaperCredentialsError(f"{name} is blank; fse paper needs it")
    clock = SystemClock()
    now = clock.now()
    defaults = default_paths()
    calendar_dir: Path | None = args.calendar_dir
    if calendar_dir is None:
        if ctx.project_dir is None:
            raise PaperUsageError("there is no Project folder; pass --calendar-dir")
        calendar_dir = project_calendar_dir(ctx.project_dir)
    times = cfg.account.session_times(SessionTimes())
    today = ny_datetime(now).date()
    calendars = load_calendars(calendar_dir, today, today, times=times)
    calendar = calendars.exchange.sessions
    session = calendar.trading_day_of(now)
    if session is None:
        raise PaperUsageError("no session's trading day holds the current time")
    window = session_window(cfg, calendar, session)
    if now >= window.dt_end:
        raise PaperUsageError(f"the {session} Decision_Time window has ended")
    params = EngineParams.from_sections(cfg)
    instruments = params.instruments
    trading_fees(cfg, instruments)  # exit 2 before any request when a fee is unset
    feeds = feed_instruments(
        cfg, [(i, calendars.roll.contract_for(i, session)) for i in instruments]
    )
    run_id = f"live-{session.isoformat()}-{secrets.token_hex(4)}"
    dirs = check_output_dirs(
        {
            "cache directory": args.cache_dir or defaults.cache,
            "live-state directory": args.state_dir or defaults.live_state,
            "recordings directory": (args.recordings_root or defaults.root) / RECORDINGS_DIR_NAME,
            "live run directory": args.out or defaults.runs / run_id,
        }
    )
    out_dir = dirs["live run directory"]
    view_id = heatmap_view(cfg.data.heatmap_view).view_id()
    with DataCache(dirs["cache directory"], calendar=calendar) as cache:
        medians = RegimeMedianStore(cache).load_or_build(
            calendar, view_id=view_id, params=MedianParams.from_config(cfg.regime, cfg.data)
        )
        daily = cache.vix.read_daily()
    prior = prior_session(calendar, session)
    engine = Engine(params, calendar, medians.by_session)
    vix = (daily.get(session), None if prior is None else daily.get(prior))
    return Prepared(cfg, engine, session, calendars, out_dir, dirs, [], feeds, vix)


def run_live(ctx: CommandContext, prep: Prepared, broker: BrokerSetup | None) -> int:
    """Run the prepared session; ``broker`` (``fse live`` only) resolves the Order_Mode."""
    writer = ctx.writer
    command = NAME if broker is None else "live"
    writer.echo(f"fse {command} {prep.session}: run directory {prep.out_dir}")
    try:
        return asyncio.run(_run(ctx, prep, broker))
    except LiveStartError:
        raise
    except asyncio.CancelledError:
        writer.error("interrupted")
        return 130


async def _run(ctx: CommandContext, prep: Prepared, broker: BrokerSetup | None) -> int:
    cfg, session, calendars, out_dir, dirs = (
        prep.cfg,
        prep.session,
        prep.calendars,
        prep.out_dir,
        prep.dirs,
    )
    clock = SystemClock()
    rng = random.Random()
    writer = ctx.writer
    async with httpx.AsyncClient(follow_redirects=False) as http:
        with FetchLog(writer, out_dir / FETCH_LOG_FILE_NAME) as fetch_log:
            skylit = SkylitClient(ctx.env, ClientConfig(), fetch_log, clock, rng, http=http)
            px_session = ProjectXSession(ctx.env, writer.redactor, http, clock)
            px = ProjectXBars(px_session, clock, rng)
            mode, safety = (None, None) if broker is None else await broker(prep, px_session, clock)
            notifier = Notifier(cfg.notify, ctx.env, writer, clock, out_dir, http=http)
            live = LiveSession(
                cfg=cfg,
                engine=prep.engine,
                calendar=calendars.exchange.sessions,
                session=session,
                writer=writer,
                run_dir=out_dir,
                state_dir=dirs["live-state directory"],
                recordings_root=dirs["recordings directory"].parent,
                outbox=notifier,
                events=[
                    e for e in calendars.events.events if abs((e.release_date - session).days) <= 1
                ],
                vix_daily=prep.vix,
                notes=prep.notes,
                mode=mode,
                safety=safety,
            )
            runner = LiveRunner(
                live,
                LiveClients(
                    map=skylit, bars=px, instruments=prep.feeds, levels=skylit, dark_pool=skylit
                ),
                clock,
                notifier,
                code_version=describe_code_version(ctx.project_dir),
            )
            await runner.run(cfg)
    return EXIT_FEED_STOPPED if runner.feed_errors else 0
