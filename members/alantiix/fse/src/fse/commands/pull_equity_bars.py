"""``fse pull-equity-bars``: exact QQQ/SPY Atlas RTH bars.

The command is deliberately not a mode of ``fse pull``. It uses only Atlas,
60-second RTH requests, and the separate ``equity_bars`` cache dataset.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import random
import time
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any, Final

import httpx

from fse.calendars import (
    EXCHANGE_CALENDAR_FILE,
    CalendarError,
    load_exchange_calendar,
    project_calendar_dir,
)
from fse.clock import SystemClock
from fse.commands import CommandContext, SubParsers
from fse.data.bar_source import AtlasBars
from fse.data.cache import DataCache
from fse.data.coverage import TimeRange
from fse.data.equity_bars import (
    EQUITY_INTERVAL_S,
    EQUITY_SYMBOLS,
    EquitySession,
    EquitySessionQuality,
    estimate_equity_requests,
    manifest_timestamp_values,
    pull_equity_bars,
)
from fse.data.path_guard import check_output_dirs
from fse.settings import default_paths
from fse.skylit.client import ClientConfig, SkylitClient
from fse.skylit.fetch_log import FETCH_LOG_FILE_NAME, FetchLog
from fse.timekit import NS_PER_SECOND, SessionCalendar, ny_datetime

__all__ = ["PullEquityUsageError", "register", "run_pull_equity_bars"]

NAME: Final = "pull-equity-bars"
EXIT_INVALID_INPUT: Final = 2
EXIT_DATA_IO: Final = 4

_DESCRIPTION = """\
Pull exact QQQ and/or SPY 60-second Atlas OHLCV bars into the separate
``equity_bars`` research dataset. Requests are RTH-only, use each exchange
calendar session's exact [09:30, close) window, and include early closes.

Each successful Atlas /v1/history request costs one credit. The command prints
an account/config/history request and credit estimate before any live request.
Use --dry-run (or --preflight) for local validation and an estimate only; it
creates no client, network request, cache row, coverage file, or Fetch_Log.
"""

_EPILOG = """\
Reruns skip only complete, readable, hash-verified sessions with full RTH
coverage. Invalid or incomplete responses stay incomplete and never replace a
valid existing file. QQQ/SPY rows are never written to the futures bars
_dataset and are not consumed by the main futures backtester automatically.

Atlas history requests cost credits; tests and --dry-run make no network request."""


class PullEquityUsageError(Exception):
    """Invalid local input for the equity-bar command."""

    exit_code = EXIT_INVALID_INPUT


def _stable_pull_id(
    start: date, end: date, symbols: tuple[str, ...], interval_s: int
) -> str:
    """Derive one coverage identity for the same resumable request."""
    identity = f"{start.isoformat()}|{end.isoformat()}|{interval_s}|{','.join(sorted(symbols))}"
    digest = hashlib.sha256(identity.encode("ascii")).hexdigest()[:12]
    return f"{start:%Y%m%d}-{end:%Y%m%d}-{digest}"


def _date(text: str) -> date:
    try:
        if len(text) != 10 or text != text.strip():
            raise ValueError
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a YYYY-MM-DD date") from None


def _symbols(text: str) -> tuple[str, ...]:
    if not text or text != text.strip():
        raise argparse.ArgumentTypeError("--symbols must be QQQ,SPY with no whitespace")
    items = tuple(text.split(","))
    if not items or any(item not in EQUITY_SYMBOLS for item in items):
        raise argparse.ArgumentTypeError(
            "--symbols must be a non-empty subset of exact QQQ and SPY"
        )
    if len(set(items)) != len(items):
        raise argparse.ArgumentTypeError("--symbols must not contain duplicates")
    return items


def _interval(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        value = -1
    if value != EQUITY_INTERVAL_S:
        raise argparse.ArgumentTypeError("--interval accepts only 60 seconds in v1")
    return value


def register(subparsers: SubParsers) -> None:
    parser = subparsers.add_parser(
        NAME,
        help="pull separate QQQ/SPY Atlas 60-second RTH equity bars; history costs credits",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add = parser.add_argument
    add(
        "--start",
        type=_date,
        required=True,
        metavar="YYYY-MM-DD",
        help="first inclusive NY session",
    )
    add(
        "--end",
        type=_date,
        required=True,
        metavar="YYYY-MM-DD",
        help="last inclusive NY session",
    )
    add(
        "--symbols",
        type=_symbols,
        default=("QQQ", "SPY"),
        metavar="QQQ[,SPY]",
        help="exact uppercase symbols, no whitespace (default: QQQ,SPY)",
    )
    add(
        "--interval",
        type=_interval,
        default=EQUITY_INTERVAL_S,
        metavar="SECONDS",
        help="bar interval; v1 accepts only 60 seconds (RTH-only)",
    )
    add(
        "--dry-run",
        "--preflight",
        dest="dry_run",
        action="store_true",
        help="validate and estimate without network or writes",
    )
    paths = parser.add_argument_group("paths")
    paths.add_argument("--cache-dir", type=Path, metavar="DIR", help="equity cache root")
    paths.add_argument(
        "--calendar-dir",
        type=Path,
        metavar="DIR",
        help="folder containing exchange_calendar.yaml",
    )
    paths.add_argument(
        "--out", type=Path, metavar="DIR", help="coverage and Fetch_Log output directory"
    )
    parser.set_defaults(handler=run_pull_equity_bars)


def _calendar(
    args: argparse.Namespace, ctx: CommandContext
) -> tuple[SessionCalendar, tuple[date, ...]]:
    if args.start > args.end:
        raise PullEquityUsageError("--start must not be after --end")
    calendar_dir = args.calendar_dir
    if calendar_dir is None:
        if ctx.project_dir is None:
            raise PullEquityUsageError("the Project folder was not found; pass --calendar-dir DIR")
        calendar_dir = project_calendar_dir(ctx.project_dir)
    try:
        loaded = load_exchange_calendar(calendar_dir / EXCHANGE_CALENDAR_FILE)
    except CalendarError:
        raise
    if not loaded.covers.contains(args.start) or not loaded.covers.contains(args.end):
        raise PullEquityUsageError(
            f"requested dates {args.start} to {args.end} are outside the exchange calendar "
            f"coverage {loaded.covers}"
        )
    sessions = loaded.sessions.sessions(args.start, args.end)
    if not sessions:
        raise PullEquityUsageError("the requested range contains no exchange sessions")
    return loaded.sessions, sessions


def _estimate(
    calendar: SessionCalendar, sessions: tuple[date, ...], symbols: tuple[str, ...]
) -> dict[str, int]:
    estimate = estimate_equity_requests(sessions, symbols, calendar=calendar)
    estimate["expected_rth_rows"] = sum(
        (calendar.rth_close(session) - calendar.rth_open(session)) // (60 * NS_PER_SECOND)
        for session in sessions
    ) * len(symbols)
    return estimate


def _print_estimate(
    ctx: CommandContext, estimate: dict[str, int], symbols: tuple[str, ...]
) -> None:
    ctx.writer.echo(
        "Equity preflight: "
        f"symbols={','.join(symbols)} sessions={estimate['sessions']} "
        f"expected_RTH_rows={estimate['expected_rth_rows']} "
        f"account_requests={estimate['account_requests']} (free) "
        f"config_requests={estimate['config_requests']} (free) "
        f"history_requests={estimate['history_requests']} "
        f"estimated_credits={estimate['credits']}"
    )


def _empty_result(
    symbol: str, session: date, calendar: SessionCalendar, error: str
) -> EquitySession:
    start, end = calendar.rth_open(session), calendar.rth_close(session)
    expected = (end - start) // (60 * NS_PER_SECOND)
    return EquitySession(
        symbol=symbol,
        session=session,
        status="incomplete",
        bars=(),
        quality=EquitySessionQuality(
            expected_rows=expected,
            missing_minutes=(TimeRange(start, end),),
            error=error,
        ),
        request_from_s=start // NS_PER_SECOND,
        request_to_s=end // NS_PER_SECOND,
        error=error,
    )


def _ranges(values: Iterable[TimeRange]) -> list[dict[str, object]]:
    return [
        {
            "start": ny_datetime(item.start_ns).isoformat(),
            "end": ny_datetime(item.end_ns).isoformat(),
        }
        for item in values
    ]


def _session_json(item: EquitySession, store: DataCache) -> dict[str, object]:
    record = store.equity_bars.record(item.symbol, EQUITY_INTERVAL_S, item.session)
    sha256 = None if record is None else record.sha256
    return {
        "session": item.session.isoformat(),
        "status": item.status,
        "row_count": item.row_count,
        "expected_rth_minutes": item.quality.expected_rows,
        "missing_minutes": _ranges(item.quality.missing_minutes),
        "duplicate_minutes": manifest_timestamp_values(item.quality.duplicate_timestamps),
        "off_rth_minutes": manifest_timestamp_values(item.quality.out_of_rth_timestamps),
        "off_grid_timestamps": manifest_timestamp_values(item.quality.off_grid_timestamps),
        "source": "atlas",
        "sha256": sha256,
        "request": {
            "from_s": item.request_from_s,
            "to_s": item.request_to_s,
            "extended": False,
        },
        "error": item.error,
    }


def _manifest(
    *,
    args: argparse.Namespace,
    calendar: SessionCalendar,
    sessions: tuple[date, ...],
    symbols: tuple[str, ...],
    estimate: dict[str, int],
    results: dict[str, list[EquitySession]],
    store: DataCache,
    error: str | None,
) -> dict[str, Any]:
    by_symbol: dict[str, Any] = {}
    problems: list[str] = []
    if error is not None:
        problems.append(error)
    for symbol in symbols:
        known = {item.session: item for item in results.get(symbol, [])}
        items = [
            known.get(session)
            or _empty_result(
                symbol,
                session,
                calendar,
                error or "no result was returned for this requested session",
            )
            for session in sessions
        ]
        complete = sum(item.status == "complete" for item in items)
        no_data = sum(item.status == "no_data" for item in items)
        by_symbol[symbol] = {
            "sessions_requested": len(sessions),
            "sessions_complete": complete,
            "sessions_no_data": no_data,
            "sessions_incomplete": len(sessions) - complete - no_data,
            "sessions": [_session_json(item, store) for item in items],
        }
        problems.extend(
            f"{symbol} {item.session}: {item.error or 'incomplete RTH coverage'}"
            for item in items
            if item.status != "complete"
        )
    complete_pull = not problems and all(
        len(results.get(symbol, [])) == len(sessions) for symbol in symbols
    )
    outcome = "completed" if complete_pull else "failed"
    return {
        "format": "fse.equity-coverage/1",
        "command": NAME,
        "range": {"start": args.start.isoformat(), "end": args.end.isoformat()},
        "symbols": list(symbols),
        "interval_s": EQUITY_INTERVAL_S,
        "session_type": "RTH",
        "extended": False,
        "source": "atlas",
        "estimate": {
            "account_requests": estimate["account_requests"],
            "config_requests": estimate["config_requests"],
            "history_requests": estimate["history_requests"],
            "credits": estimate["credits"],
        },
        "by_symbol": by_symbol,
        "outcome": outcome,
        "problems": list(dict.fromkeys(problems)),
    }


def _markdown(manifest: dict[str, Any]) -> str:
    by_symbol = manifest["by_symbol"]
    lines = [
        "# Equity Atlas coverage",
        "",
        f"- Outcome: {manifest['outcome']}",
        f"- Range: {manifest['range']['start']} to {manifest['range']['end']} "
        "(New York, inclusive)",
        f"- Symbols: {', '.join(manifest['symbols'])}; interval: 60 seconds; session: RTH",
        "- Source: Atlas `/v1/history`; `extended=false`",
        "- Credit warning: each successful history request costs one Atlas credit.",
        "",
    ]
    for symbol, section in by_symbol.items():
        lines += [
            f"## {symbol}",
            "",
            f"- Sessions: {section['sessions_requested']}; "
            f"complete: {section['sessions_complete']}; "
            f"no_data: {section['sessions_no_data']}; "
            f"incomplete: {section['sessions_incomplete']}",
            "",
            "| Session | Status | Rows | Expected RTH minutes | SHA256 |",
            "| --- | --- | ---: | ---: | --- |",
        ]
        for item in section["sessions"]:
            lines.append(
                f"| {item['session']} | {item['status']} | {item['row_count']} | "
                f"{item['expected_rth_minutes']} | {item['sha256'] or 'null'} |"
            )
        lines.append("")
    problems = manifest["problems"]
    if problems:
        lines += ["## Problems", "", *[f"- {problem}" for problem in problems], ""]
    return "\n".join(lines)


async def _run_live(
    *,
    args: argparse.Namespace,
    ctx: CommandContext,
    calendar: SessionCalendar,
    sessions: tuple[date, ...],
    cache_dir: Path,
    out_dir: Path,
    pull_id: str,
    started_ns: int,
    symbols: tuple[str, ...],
    estimate: dict[str, int],
) -> int:
    results: dict[str, list[EquitySession]] = {}
    partial_results: list[EquitySession] = []
    error: BaseException | None = None
    with DataCache(cache_dir, calendar=calendar) as cache:
        try:
            if cache.catalog.pull(pull_id) is None:
                cache.catalog.record_pull(
                    pull_id,
                    started_ns,
                    ctx.writer.json_text(
                        {
                            "command": NAME,
                            "start": args.start.isoformat(),
                            "end": args.end.isoformat(),
                            "symbols": symbols,
                        }
                    ),
                )
            async with httpx.AsyncClient(follow_redirects=False) as http:
                with FetchLog(ctx.writer, out_dir / FETCH_LOG_FILE_NAME) as fetch_log:
                    client = SkylitClient(
                        ctx.env,
                        ClientConfig(),
                        fetch_log,
                        SystemClock(),
                        random.Random(),
                        http=http,
                    )
                    atlas = AtlasBars(client)
                    pulled = await pull_equity_bars(
                        atlas,
                        cache.equity_bars,
                        calendar,
                        sessions,
                        symbols,
                        partial_results=partial_results,
                    )
                    for symbol in symbols:
                        results[symbol] = [
                            item for item in pulled.results if item.symbol == symbol
                        ]
        except BaseException as exc:
            error = exc
            for symbol in symbols:
                results[symbol] = [
                    item for item in partial_results if item.symbol == symbol
                ]
        manifest = _manifest(
            args=args,
            calendar=calendar,
            sessions=sessions,
            symbols=symbols,
            estimate=estimate,
            results=results,
            store=cache,
            error=None if error is None else str(error),
        )
        ctx.writer.write_json(out_dir / f"equity_coverage_{pull_id}.json", manifest)
        ctx.writer.write_text(out_dir / f"equity_coverage_{pull_id}.md", _markdown(manifest))
    if error is not None:
        raise error
    return 0 if manifest["outcome"] == "completed" else EXIT_DATA_IO


def run_pull_equity_bars(args: argparse.Namespace, ctx: CommandContext) -> int:
    if args.interval != EQUITY_INTERVAL_S:
        raise PullEquityUsageError("--interval accepts only 60 seconds in v1")
    calendar, sessions = _calendar(args, ctx)
    symbols = tuple(args.symbols)
    defaults = default_paths()
    cache_dir = defaults.cache if args.cache_dir is None else args.cache_dir
    started_ns = time.time_ns()
    pull_id = _stable_pull_id(args.start, args.end, symbols, args.interval)
    out_dir = defaults.runs / f"equity_pull_{pull_id}" if args.out is None else args.out
    checked = check_output_dirs({"cache directory": cache_dir, "pull output directory": out_dir})
    cache_dir, out_dir = checked["cache directory"], checked["pull output directory"]
    estimate = _estimate(calendar, sessions, symbols)
    _print_estimate(ctx, estimate, symbols)
    if args.dry_run:
        ctx.writer.echo(
            "Dry run: no network request, cache row, coverage file, or Fetch_Log was created."
        )
        return 0
    try:
        return asyncio.run(
            _run_live(
                args=args,
                ctx=ctx,
                calendar=calendar,
                sessions=sessions,
                cache_dir=cache_dir,
                out_dir=out_dir,
                pull_id=pull_id,
                started_ns=started_ns,
                symbols=symbols,
                estimate=estimate,
            )
        )
    except asyncio.CancelledError:
        raise KeyboardInterrupt from None
