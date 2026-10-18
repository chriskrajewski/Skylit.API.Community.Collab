"""``fse run``: the long-running plugin runner on live data, paper only (design 11.2, 12.1).

``fse run --config FILE [--until ISO_INSTANT] [--state-dir DIR]
[--recordings-root DIR] [--out DIR] [--bar-store DIR] [--cache-dir DIR]
[--calendar-dir DIR]`` loads the runner YAML (:mod:`fse.live.host.config`),
starts the always-on data plane (:mod:`fse.live.host.dataplane`: ProjectX
bars, the market hub's quotes, the Skylit stream, recordings and the bar
store) and runs every enabled plugin in the :class:`~fse.live.host.host.PluginHost`,
each with its own paper book. Paper is the default: no order request reaches
ProjectX unless one host-brokered plugin that supports broker orders sets
``execution.order_mode`` to ``practice`` or ``combine``, the start command
has ``--plugin-orders`` and the environment has
``FSE_RUN_PLUGIN_ORDERS=enabled`` (combine also needs ``--live-orders``);
see :mod:`fse.live.host.live_orders`. Locally:
``FSE_RUN_PLUGIN_ORDERS=enabled fse run --plugin-orders --config ...``.

Startup order (design 12.1): flags and the runner YAML; the output
directories (``check_output_dirs``) and the ``RunnerLock`` on
``<live-state>/runner.lock``; the credentials (before any request; a blank
one exits 3 naming the variable only); the host calendars; the plugins and
their needs; then the ProjectX login, the subscription probe and the
backfill; then the host (restore, ``on_start``, repair, ``on_day_open``, the
feeds and the dispatcher). Flags override the YAML ``paths``.

It runs until SIGINT/SIGTERM or ``--until`` (an ISO 8601 instant with an
offset). A second SIGINT during shutdown exits at once with 130.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import random
import secrets
import signal
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import ClassVar, Final

import httpx

from fse.apilog import FILE_NAME, enable
from fse.calendars import Calendars, load_calendars, project_calendar_dir
from fse.clock import Clock, SystemClock
from fse.commands import CommandContext, SubParsers
from fse.data.cache import DataCache
from fse.data.catalog import FINAL_STATUSES
from fse.data.path_guard import check_output_dirs
from fse.live.host.config import RunnerConfig, load_runner_config
from fse.live.host.dataplane import DataPlane, DataPlanePaths
from fse.live.host.host import (
    HOST_LOG_FILE_NAME,
    BlockingRunner,
    HostPaths,
    HostResources,
    PluginHost,
)
from fse.live.host.live_orders import (
    BROKER_MODES,
    LIVE_ORDERS_FLAG,
    PLUGIN_ORDERS_ENV,
    PLUGIN_ORDERS_FLAG,
    JournalError,
    make_safety_factory,
    resolve_plugin_order_mode,
)
from fse.live.host.lock import RunnerLock
from fse.live.host.nexus import HttpNexusTransport, NexusTransport, resolve_nexus_accounts
from fse.live.host.sources import HubFactory
from fse.live.plugins.api import StrategyPlugin
from fse.live.plugins.loader import load_plugin_classes
from fse.live.recorder import RECORDINGS_DIR_NAME
from fse.logio import LogWriter
from fse.logio.canonical_json import JsonValue
from fse.projectx.bars import ProjectXBars, bars_rate_limiter
from fse.projectx.realtime import HubConnect, MarketHubClient, hub_logger, websockets_connect
from fse.projectx.session import API_KEY_VARIABLE, USERNAME_VARIABLE, ProjectXSession
from fse.settings import default_paths
from fse.skylit.client import ClientConfig, SkylitClient
from fse.skylit.fetch_log import FetchLog
from fse.timekit import NS_PER_SECOND, Instant, ny_datetime

__all__ = [
    "BAR_STORE_DIR_NAME",
    "NAME",
    "RunCredentialsError",
    "RunUsageError",
    "parse_until",
    "register",
    "run_command",
]

NAME: Final = "run"
BAR_STORE_DIR_NAME: Final = "live-bars"
EXIT_OK: Final = 0
EXIT_INVALID_INPUT: Final = 2
EXIT_CREDENTIALS: Final = 3
EXIT_JOURNAL: Final = 4
EXIT_INTERRUPTED: Final = 130
CALENDAR_DAYS_AHEAD: Final = 7
SKYLIT_KEY_VARIABLE: Final = "SKYLIT_API_KEY"

_DESCRIPTION = f"""\
Run the plugin runner on live data, paper by default. The data plane records
ProjectX 1-minute bars (REST, triggered by the market hub's trades when it is
connected), the hub's quotes and the Skylit stream every Globex day, and
writes closed bars to the bar store; each enabled plugin of the runner YAML
trades its own in-process paper book.

Opt-in orders: one plugin whose plugins[].execution.order_mode is practice or
combine also sends orders to ProjectX, only when the start command has
{PLUGIN_ORDERS_FLAG} and {PLUGIN_ORDERS_ENV}=enabled is set (combine also
needs {LIVE_ORDERS_FLAG}). Otherwise it runs paper and its first card says why.
Local only: {PLUGIN_ORDERS_ENV}=enabled fse run {PLUGIN_ORDERS_FLAG} --config FILE

The run continues across the daily 17:00-18:00 break, weekends and holidays
until SIGINT/SIGTERM or --until. State is saved after every change, so a
restart continues. fse halt blocks new entries; fse clear lifts the blocks."""

_EPILOG = """\
exit status:
  0    stopped cleanly (signal or --until) with no fault
  1    stopped after a feed stop or a plugin fault
  2    invalid input: a flag (--until without an offset included), the runner
       config, a plugin (load, config, needs), a calendar file, a path, or
       another runner holds <live-state>/runner.lock
  3    a blank credential (SKYLIT_API_KEY when Skylit is enabled, or a ProjectX
       credential variable) or a ProjectX auth failure that stops the bar feed
  4    the order journal of the broker plugin cannot be read
  130  a second SIGINT during shutdown"""


class RunUsageError(Exception):
    """Flags or settings that cannot start ``fse run`` (exit 2)."""

    exit_code: ClassVar[int] = EXIT_INVALID_INPUT


class RunCredentialsError(Exception):
    """A required credential is blank (exit 3). The message names the variable only."""

    exit_code: ClassVar[int] = EXIT_CREDENTIALS


def register(subparsers: SubParsers) -> None:
    parser = subparsers.add_parser(
        NAME,
        help="run the strategy plugins on live data, paper by default, until stopped",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        metavar="FILE",
        help="the runner config: the plugins to run and the data feeds they need",
    )
    parser.add_argument(
        "--until",
        metavar="ISO_INSTANT",
        help="stop at this instant (ISO 8601 with an offset, e.g. 2026-10-12T18:10:00-04:00)",
    )
    parser.add_argument(
        PLUGIN_ORDERS_FLAG,
        action="store_true",
        help="send a non-paper plugins[].execution.order_mode to ProjectX (local runs only; "
        f"also needs {PLUGIN_ORDERS_ENV}=enabled); without it every plugin runs paper",
    )
    parser.add_argument(
        LIVE_ORDERS_FLAG,
        action="store_true",
        help="the explicit opt-in a combine execution.order_mode needs (Combine_Opt_In); "
        "no effect otherwise",
    )
    for flag, default in (
        ("--state-dir", "paths.state_dir, else ~/.skylit-fse/live-state"),
        ("--recordings-root", "paths.recordings_root, else ~/.skylit-fse"),
        ("--out", "<paths.out_root>/run-<UTC stamp>-<hex8> (default root ~/.skylit-fse/runs)"),
        ("--bar-store", "paths.bar_store, else ~/.skylit-fse/live-bars"),
        ("--cache-dir", "paths.cache_dir, else ~/.skylit-fse/cache"),
        ("--calendar-dir", "paths.calendar_dir, else the Project's calendars/ folder"),
    ):
        parser.add_argument(flag, type=Path, metavar="DIR", help=f"default: {default}")
    parser.set_defaults(handler=run_command)


def parse_until(text: str | None) -> Instant | None:
    """``--until`` as an Instant; :class:`RunUsageError` without an offset or when unparsable."""
    if text is None:
        return None
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        raise RunUsageError(f"--until {text!r} is not an ISO 8601 instant") from None
    if when.tzinfo is None or when.utcoffset() is None:
        raise RunUsageError(f"--until {text!r} must carry a UTC offset (e.g. -04:00 or Z)")
    delta = when - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * NS_PER_SECOND + delta.microseconds * 1000


@dataclass(frozen=True, slots=True)
class _Dirs:
    state: Path
    recordings_root: Path
    run_dir: Path
    bar_store: Path
    cache: Path
    calendar: Path


def _dirs(args: argparse.Namespace, cfg: RunnerConfig, ctx: CommandContext) -> _Dirs:
    defaults = default_paths()
    paths = cfg.paths
    calendar_dir: Path | None = args.calendar_dir or paths.calendar_dir
    if calendar_dir is None:
        if ctx.project_dir is None:
            raise RunUsageError("there is no Project folder; pass --calendar-dir")
        calendar_dir = project_calendar_dir(ctx.project_dir)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = args.out or (paths.out_root or defaults.runs) / f"run-{stamp}-{secrets.token_hex(4)}"
    recordings_root = args.recordings_root or paths.recordings_root or defaults.root
    checked = check_output_dirs(
        {
            "cache directory": args.cache_dir or paths.cache_dir or defaults.cache,
            "live-state directory": args.state_dir or paths.state_dir or defaults.live_state,
            "recordings directory": recordings_root / RECORDINGS_DIR_NAME,
            "run directory": run_dir,
            "bar store directory": args.bar_store
            or paths.bar_store
            or defaults.root / BAR_STORE_DIR_NAME,
        }
    )
    return _Dirs(
        state=checked["live-state directory"],
        recordings_root=checked["recordings directory"].parent,
        run_dir=checked["run directory"],
        bar_store=checked["bar store directory"],
        cache=checked["cache directory"],
        calendar=Path(calendar_dir),
    )


def _check_credentials(cfg: RunnerConfig, ctx: CommandContext) -> None:
    names = [USERNAME_VARIABLE, API_KEY_VARIABLE]
    if cfg.data_plane.skylit.enabled:
        names.insert(0, SKYLIT_KEY_VARIABLE)
    for name in names:
        value = ctx.env.get(name)
        if value is None or not value.strip():
            raise RunCredentialsError(f"{name} is blank; fse run needs it")


def run_command(args: argparse.Namespace, ctx: CommandContext) -> int:
    until_ns = parse_until(args.until)
    cfg = load_runner_config(args.config)
    dirs = _dirs(args, cfg, ctx)
    enable(dirs.state / FILE_NAME)
    with RunnerLock(dirs.state):
        _check_credentials(cfg, ctx)
        today = ny_datetime(SystemClock().now()).date()
        calendars = load_calendars(dirs.calendar, today, today + timedelta(CALENDAR_DAYS_AHEAD))
        classes = load_plugin_classes(cfg, project_dir=ctx.project_dir)
        ctx.writer.echo(f"fse run: run directory {dirs.run_dir}")
        try:
            return asyncio.run(
                _run(
                    ctx,
                    cfg,
                    dirs,
                    calendars,
                    classes,
                    until_ns,
                    plugin_orders=bool(getattr(args, "plugin_orders", False)),
                    live_orders=bool(getattr(args, "live_orders", False)),
                )
            )
        except asyncio.CancelledError:
            ctx.writer.error("interrupted")
            return EXIT_INTERRUPTED
        except JournalError as exc:
            ctx.writer.error(str(exc))
            return EXIT_JOURNAL


def _nexus(
    cfg: RunnerConfig, ctx: CommandContext, http: httpx.AsyncClient
) -> tuple[NexusTransport | None, dict[str, str], dict[str, str]]:
    """The Nexus transport and the resolved/disabled mirror accounts (paper-analytics §5).

    A transport is built only when an account has ``nexus.enabled`` and
    ``SKYLIT_API_KEY`` is set; without the key the host reports each mirror
    ``disabled: no api key`` and the run continues (H-F4).
    """
    if not any(a.nexus.enabled for a in cfg.accounts):
        return None, {}, {}
    resolved, disabled = resolve_nexus_accounts(cfg.accounts, ctx.env)
    key = ctx.env.get(SKYLIT_KEY_VARIABLE)
    if key is None or not key.strip():
        return None, resolved, disabled
    return HttpNexusTransport(http, key, writer=ctx.writer), resolved, disabled


def _host_log(writer: LogWriter, run_dir: Path, clock: Clock) -> Callable[..., None]:
    def log(entry: Mapping[str, JsonValue]) -> None:
        try:
            writer.append_json(run_dir / HOST_LOG_FILE_NAME, {"t_ns": clock.now(), **entry})
        except OSError:
            writer.error(f"cannot write the host log: {entry.get('kind')}")

    return log


@contextlib.contextmanager
def _cached_days(cache_dir: Path) -> Iterator[Callable[[str, date], bool]]:
    """Whether the Data_Cache holds a final session (read-only, this thread only)."""
    with DataCache(cache_dir) as cache:

        def cached(instrument: str, d: date) -> bool:
            try:
                return cache.bars.status(instrument, 60, d) in FINAL_STATUSES
            except Exception:
                return False

        yield cached


async def _run(
    ctx: CommandContext,
    cfg: RunnerConfig,
    dirs: _Dirs,
    calendars: Calendars,
    classes: Mapping[str, type[StrategyPlugin]],
    until_ns: Instant | None,
    *,
    plugin_orders: bool = False,
    live_orders: bool = False,
    clock: Clock | None = None,
    connect: HubConnect | None = None,
    blocking: BlockingRunner | None = None,
) -> int:
    """The run itself; ``clock``, ``connect`` and ``blocking`` are injected by tests only."""
    clock = clock or SystemClock()
    rng = random.Random()
    writer = ctx.writer
    log = _host_log(writer, dirs.run_dir, clock)
    px = cfg.data_plane.projectx

    def loader(first: date, last: date) -> Calendars:
        return load_calendars(dirs.calendar, first, last)

    fatal: list[int] = []
    holder: list[PluginHost] = []

    def on_fatal(code: int, detail: str) -> None:
        fatal.append(code)
        if holder:
            holder[0].fail("feed_stopped", detail)
            holder[0].request_stop()

    async with httpx.AsyncClient(follow_redirects=False) as http:
        session = ProjectXSession(ctx.env, writer.redactor, http, clock)
        limiter = bars_rate_limiter(clock)

        def bars(live: bool) -> ProjectXBars:
            return ProjectXBars(session, clock, rng, limiter=limiter, live=live)

        hub: HubFactory | None = None
        if px.transport != "polling":
            hub_connect = connect or websockets_connect(hub_logger(writer))

            def make_hub(
                contract_ids: tuple[str, ...],
                on_quote: Callable[..., None],
                on_trade: Callable[..., None],
                on_state: Callable[[bool, str], None],
            ) -> MarketHubClient:
                return MarketHubClient(
                    session,
                    clock,
                    rtc_url=px.rtc_url,
                    contract_ids=contract_ids,
                    connect=hub_connect,
                    on_quote=on_quote,
                    on_trade=on_trade,
                    on_state=on_state,
                    log=plane.feed_log,
                )

            hub = make_hub

        def skylit(fetch_log: FetchLog) -> SkylitClient:
            return SkylitClient(ctx.env, ClientConfig(), fetch_log, clock, rng, http=http)

        plane = DataPlane(
            cfg.data_plane,
            clock=clock,
            writer=writer,
            paths=DataPlanePaths(dirs.recordings_root, dirs.run_dir, dirs.bar_store),
            calendars=calendars,
            bars=bars,
            log=log,
            on_fatal=on_fatal,
            hub=hub,
            skylit=skylit,
            calendar_loader=loader,
        )
        nexus, nexus_resolved, nexus_disabled = _nexus(cfg, ctx, http)
        host = PluginHost(
            cfg,
            classes,
            dataplane=plane,
            clock=clock,
            writer=writer,
            paths=HostPaths(
                state_root=dirs.state,
                run_dir=dirs.run_dir,
                cache_dir=dirs.cache,
                calendar_dir=dirs.calendar,
                project_dir=ctx.project_dir,
                recordings_root=dirs.recordings_root,
            ),
            calendars=calendars,
            resources=HostResources(
                env=ctx.env,
                http=http,
                skylit_levels=plane.levels,
                nexus=nexus,
                nexus_resolved=nexus_resolved,
                nexus_disabled=nexus_disabled,
            ),
            calendar_loader=loader,
            blocking=blocking,
        )
        holder.append(host)
        await session.token()  # the login: a refusal exits 3
        await plane.probe()
        with _cached_days(dirs.cache) as cached:
            await plane.backfill(cached)
        setup = await resolve_plugin_order_mode(
            cfg,
            ctx.env,
            session,
            clock,
            writer,
            dirs.state,
            plugin_orders=plugin_orders,
            live_orders=live_orders,
        )
        factory = (
            make_safety_factory(
                setup,
                plane,
                clock,
                host.broker_entry_gate(setup.plugin_id),
                host.broker_instruments(setup.plugin_id),
            )
            if setup is not None and setup.decision.mode in BROKER_MODES
            else None
        )
        host.attach_order_mode(setup, factory, plugin_orders=plugin_orders, live_orders=live_orders)
        _install_signals(host)
        code = await host.run(until_ns)
    if EXIT_CREDENTIALS in fatal:
        return EXIT_CREDENTIALS
    return code


def _install_signals(host: PluginHost) -> None:
    loop = asyncio.get_running_loop()
    main = asyncio.current_task()
    seen: list[int] = []

    def handle(signum: int) -> None:
        seen.append(signum)
        if len(seen) == 1:
            host.request_stop()
        elif signum == signal.SIGINT and main is not None:
            main.cancel()  # a second SIGINT: exit at once with 130

    for signum in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
            loop.add_signal_handler(signum, handle, signum)
