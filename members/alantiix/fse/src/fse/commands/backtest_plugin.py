"""``fse backtest-plugin``: backtest runner plugins exactly as ``fse run`` trades them.

``fse backtest-plugin --config RUNNER_YAML [--plugin ID ...] --first DATE --last DATE
[--out DIR] [--cache-dir DIR] [--recordings-root DIR] [--recordings-s3 URI] [--history N]
[--lag-s N] [--allow-gaps] [--coverage-only]`` replays the chosen enabled plugins of a runner YAML
through the live host and paper broker (:mod:`fse.live.host.replay`): the same fills,
risk caps, gate, wakes and day rotation as live, fed with the inputs the plugins
declare in ``needs()``. Any plugin works; nothing here names one.

Outputs in the run folder: ``coverage.json`` (where each input of each day came from),
``trades.json`` (every closed trade, the ``fse backtest`` format, so the console's Runs
page computes metrics), ``summary.json`` (per plugin), ``run_manifest.json`` (kind
``plugin_backtest``) and the runner layout under ``run/`` (``host_log.jsonl``, each
plugin's ``plugin_log.jsonl`` and outputs) and ``live-state/`` (``status.json``).
"""

from __future__ import annotations

import argparse
import io
import secrets
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from fse.backtest.manifest import DataRange, RunSpec, run_manifest
from fse.calendars import project_calendar_dir
from fse.commands import CommandContext, SubParsers
from fse.live.host.config import RunnerConfigError, load_runner_config
from fse.live.host.replay import (
    DEFAULT_HISTORY,
    DEFAULT_LAG_S,
    ReplayResult,
    config_digest,
    run_replay,
)
from fse.live.plugins.loader import PluginLoadError, load_plugin_classes
from fse.logio import LogWriter, canonical_json
from fse.settings import default_paths

__all__ = ["KIND", "NAME", "register"]

NAME: Final = "backtest-plugin"
KIND: Final = "plugin_backtest"
EXIT_OK: Final = 0
EXIT_FAULT: Final = 1
EXIT_INVALID: Final = 2
EXIT_GAPS: Final = 3

_DESCRIPTION = """\
Backtest one or more plugins of a runner config (for example configs/runner.paper.yaml)
exactly as `fse run` trades them: the same paper fills, risk caps, entry blocks and
day rotation, fed with the bars, Skylit maps, dark-pool prints and quotes each plugin
asks for. Days the runner recorded use the recording (real receipt times); other days
use the Data_Cache. A missing input is reported before anything runs."""

_EPILOG = """\
exit status:
  0  finished with no plugin fault (or --coverage-only)
  1  a plugin faulted during the replay
  2  invalid input: the runner config, a plugin id, a date
  3  some needed input is missing for a day (see coverage.json; --allow-gaps runs anyway)"""


def _date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a YYYY-MM-DD date") from None


def register(subparsers: SubParsers) -> None:
    p = subparsers.add_parser(
        NAME,
        help="backtest runner plugins exactly as fse run trades them (paper fills, caps)",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--config",
        required=True,
        type=Path,
        metavar="FILE",
        help="the runner config whose plugins, fills, risk caps and data plane are used",
    )
    p.add_argument(
        "--plugin",
        action="append",
        metavar="ID",
        help="a plugin id from the config to backtest (repeat for several); "
        "default: every enabled plugin",
    )
    p.add_argument(
        "--first", required=True, type=_date, metavar="DATE", help="first Globex day (YYYY-MM-DD)"
    )
    p.add_argument(
        "--last", required=True, type=_date, metavar="DATE", help="last Globex day (YYYY-MM-DD)"
    )
    p.add_argument(
        "--out",
        type=Path,
        metavar="DIR",
        help="the run folder (default ~/.skylit-fse/runs/plugin-backtest-<UTC stamp>-<hex>)",
    )
    p.add_argument(
        "--cache-dir",
        type=Path,
        metavar="DIR",
        help="the Data_Cache to read history from (default ~/.skylit-fse/cache); read only",
    )
    p.add_argument(
        "--recordings-root",
        type=Path,
        metavar="DIR",
        help="the folder holding recordings/dataplane/ from fse run (default ~/.skylit-fse); "
        "recorded days replay with their real receipt times",
    )
    p.add_argument(
        "--recordings-s3",
        metavar="URI",
        help="an s3://bucket[/prefix] holding recordings/dataplane/ offloaded from the runner "
        "host; days missing locally are downloaded into the run folder one at a time and "
        "deleted after replay (default: $FSE_RECORDINGS_S3)",
    )
    p.add_argument(
        "--history",
        type=int,
        default=DEFAULT_HISTORY,
        metavar="N",
        help=(
            f"1m sessions before --first copied into the run cache (default {DEFAULT_HISTORY}). "
            "The rules plugin replays them with entries suppressed"
        ),
    )
    p.add_argument(
        "--lag-s",
        type=int,
        default=DEFAULT_LAG_S,
        metavar="N",
        help="seconds from a bar's close (or a cached map's time) to when the plugin gets it, "
        f"for days without a recording (default {DEFAULT_LAG_S})",
    )
    p.add_argument(
        "--allow-gaps",
        action="store_true",
        help="run even when a needed input is missing for some day (the plugin sees no data)",
    )
    p.add_argument(
        "--coverage-only",
        action="store_true",
        help="only report which inputs exist for each day (coverage.json); run nothing",
    )
    p.set_defaults(handler=backtest_plugin_command)


def _trades_json(trades: Sequence[tuple[str, Any]]) -> list[Any]:
    return [canonical_json.to_jsonable(t) for _, t in trades]


def _summary(result: ReplayResult) -> dict[str, Any]:
    per: dict[str, dict[str, Any]] = {
        pid: {"trades": 0, "wins": 0, "net": Decimal("0")} for pid in result.plugin_ids
    }
    for pid, t in result.trades:
        row = per.setdefault(pid, {"trades": 0, "wins": 0, "net": Decimal("0")})
        row["trades"] += 1
        row["wins"] += 1 if t.net > 0 else 0
        row["net"] += t.net
    return {
        "exit_code": result.exit_code,
        "faults": result.faults,
        "coverage_complete": result.coverage.complete,
        "plugins": {
            pid: {
                **r,
                "net": str(r["net"]),
                "win_rate": (r["wins"] / r["trades"]) if r["trades"] else None,
            }
            for pid, r in per.items()
        },
    }


def backtest_plugin_command(args: argparse.Namespace, ctx: CommandContext) -> int:
    writer: LogWriter = ctx.writer
    if args.first > args.last:
        writer.error(f"--first {args.first} is after --last {args.last}")
        return EXIT_INVALID
    defaults = default_paths()  # before the replay moves HOME into the run folder
    try:
        text = args.config.read_text(encoding="utf-8")
        cfg = load_runner_config(args.config)
    except (OSError, RunnerConfigError) as exc:
        writer.error(f"runner config {args.config}: {exc}")
        return EXIT_INVALID
    enabled = [e.id for e in cfg.enabled_plugins]
    chosen = args.plugin or enabled
    unknown = sorted(set(chosen) - set(enabled))
    if unknown:
        writer.error(
            f"not an enabled plugin of {args.config}: {', '.join(unknown)} "
            f"(enabled: {', '.join(enabled) or 'none'})"
        )
        return EXIT_INVALID
    cfg = cfg.model_copy(
        update={"plugins": tuple(e for e in cfg.plugins if e.id in chosen and e.enabled)}
    )
    try:
        classes = load_plugin_classes(cfg, project_dir=ctx.project_dir)
    except PluginLoadError as exc:
        for line in exc.problems:
            writer.error(line)
        return EXIT_INVALID
    calendar_dir = cfg.paths.calendar_dir
    if calendar_dir is None:
        if ctx.project_dir is None:
            writer.error("there is no Project folder for the calendars; set paths.calendar_dir")
            return EXIT_INVALID
        calendar_dir = project_calendar_dir(ctx.project_dir)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out: Path = args.out or defaults.runs / f"plugin-backtest-{stamp}-{secrets.token_hex(4)}"
    out = out.expanduser().absolute()
    source_cache = (args.cache_dir or cfg.paths.cache_dir or defaults.cache).expanduser().absolute()
    rec_root = args.recordings_root or cfg.paths.recordings_root or defaults.root
    rec_root = rec_root.expanduser().absolute()
    rec_s3 = args.recordings_s3 or ctx.env.get("FSE_RECORDINGS_S3") or None
    if rec_s3 is not None and not rec_s3.startswith("s3://"):
        writer.error(f"--recordings-s3 {rec_s3!r} is not an s3://bucket[/prefix] URI")
        return EXIT_INVALID
    digest = config_digest(text, chosen)
    spec = RunSpec(
        run_id=out.name,
        kind=KIND,
        config_hash=digest,
        data_range=DataRange(args.first, args.last),
        seed=None,
        config_path=str(args.config),
    )
    code = EXIT_OK
    with run_manifest(spec, writer=writer, out_dir=out) as rec:
        quiet = LogWriter(writer.redactor, stdout=io.StringIO(), stderr=io.StringIO())
        try:
            result = run_replay(
                cfg,
                classes,
                first=args.first,
                last=args.last,
                work=rec.out_dir,
                source_cache=source_cache,
                recordings_root=rec_root,
                calendar_dir=calendar_dir,
                project_dir=ctx.project_dir,
                writer=quiet,
                history=args.history,
                lag_s=args.lag_s,
                allow_gaps=args.allow_gaps,
                coverage_only=args.coverage_only,
                recordings_s3=rec_s3,
                progress=writer,
            )
        except (PluginLoadError, ValueError) as exc:
            writer.error(str(exc))
            return EXIT_INVALID
        cov = result.coverage
        writer.write_json(rec.out_dir / "coverage.json", cov.to_json())
        rec.output(rec.out_dir / "coverage.json")
        for line in cov.lines():
            writer.echo(line)
        for d in cov.days:
            if d.exchange_session and not d.missing:
                rec.evaluated(d.day)
        if args.coverage_only:
            return EXIT_OK
        if result.exit_code == EXIT_GAPS and not cov.complete and not args.allow_gaps:
            writer.error(
                "some needed input is missing; see coverage.json (--allow-gaps runs anyway)"
            )
            return EXIT_GAPS
        writer.write_json(rec.out_dir / "trades.json", _trades_json(result.trades))
        rec.output(rec.out_dir / "trades.json")
        summary = _summary(result)
        writer.write_json(rec.out_dir / "summary.json", summary)
        rec.output(rec.out_dir / "summary.json")
        for pid, row in summary["plugins"].items():
            writer.echo(f"{pid}: {row['trades']} trades, net ${row['net']}")
        if result.faults:
            for pid, why in result.faults.items():
                writer.error(f"plugin {pid} faulted: {why}")
            code = EXIT_FAULT
        elif result.exit_code != EXIT_OK:
            code = EXIT_FAULT
        writer.echo(f"run folder: {rec.out_dir}")
    return code
