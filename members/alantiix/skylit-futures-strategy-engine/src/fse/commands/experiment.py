"""``fse experiment``: ablation, sweep, pass estimate, holdout, walk-forward, cadence.

Six subcommands, one per Experiment_Runner experiment (design §19-22):

- ``ablation --config FILE --start --end``: the base and one variant per
  enabled Gate (Req 19.13-19.16); writes ``ablation.json`` and ``ablation.md``;
- ``sweep --config FILE --sweep FILE --start --end``: the frontier sweep of
  the YAML sweep definition (Req 20.7-20.11, 20.14); ``frontier.json`` and
  ``frontier.md``;
- ``montecarlo --run RUN_ID --config FILE``: the Combine pass estimate of a
  completed backtest (Req 21); ``pass_estimate.json`` and ``pass_estimate.md``;
- ``holdout --config FILE [--holdout-log FILE]``: the named config on the
  Holdout_Period sessions, appending one Holdout_Log entry (Req 22.4-22.8);
  ``holdout.json`` and ``holdout.md``;
- ``walkforward --config FILE (--candidate FILE ... | --sweep FILE) --start
  --end``: window pairs over the candidates (Req 22.9-22.16);
  ``walkforward.json`` and ``walkforward.md``;
- ``cadence --config FILE --start --end [--cadences 5,60,300]``: one config at
  each cadence on the eligible sessions (Req 18.8-18.10); ``cadence.json`` and
  ``cadence.md``.

Every subcommand loads the Strategy_Config first (every error with its key
path, exit 2; contradiction warnings on stderr), checks its flags and the
path guard, and only then runs. Each experiment checks its own inputs before
it writes anything. The runs read only the local Data_Cache: no network
request, no key.

**Directories.** ``--out`` (default ``~/.skylit-fse/runs/<kind>-<hash>``,
the hash of the kind, the config hash, the range, the seed and the
kind's own inputs) must not hold the experiment's output yet. A seed is
drawn and printed when ``--seed`` is not given, so the default directory of
a rerun with the same ``--seed`` already exists and the rerun is refused
(exit 2), as with ``fse backtest``. The pass estimate's default directory is
its run id. The Holdout_Log defaults to ``~/.skylit-fse/holdout_log.jsonl``.

**Reports.** After the experiment's Run_Manifest is written, the command
reads the JSON files back and writes the Markdown report beside them
(:mod:`fse.reports.markdown`). The Markdown is derived from those files and
is not listed in the Run_Manifest.

**Holdout_Period basis.** The sweeps, ablations, cadence comparisons and
walk-forward tests compute it with the ``--config`` base; the holdout
evaluation computes it with the named config, which it runs unchanged. The
two agree whenever the named config keeps the base's symbols, metrics,
Heatmap_View and instruments; both commands print the period they used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any, ClassVar, Final, cast

import yaml

from fse.analytics.montecarlo import (
    PASS_ESTIMATE_FILE_NAME,
    load_run_outcomes,
    pass_estimate_run_id,
    resolve_seed,
    run_pass_estimate,
)
from fse.backtest.manifest import MANIFEST_FILE_NAME, DataRange
from fse.backtest.runner import SEED_BITS, backtest_range
from fse.calendars import project_calendar_dir
from fse.commands import CommandContext, SubParsers
from fse.commands._config import load_config
from fse.config.hashing import config_hash
from fse.config.loader import UniqueKeySafeLoader, load_or_raise
from fse.config.schema import StrategyConfig
from fse.config.schema.experiments import RANKING_OBJECTIVES, RankingObjective
from fse.data.path_guard import check_output_dir, check_output_dirs
from fse.experiments.ablation import ABLATION_FILE_NAME, run_ablation
from fse.experiments.cadence import CADENCE_FILE_NAME, CADENCES_DEFAULT, run_cadence_comparison
from fse.experiments.holdout import (
    HOLDOUT_LOG_FILE_NAME,
    HOLDOUT_RESULT_FILE_NAME,
    run_holdout_evaluation,
)
from fse.experiments.runner import COMPARISON_FILE_NAME, ExperimentConfig, ExperimentResult
from fse.experiments.sweep import FRONTIER_FILE_NAME, parse_sweep, run_sweep, sweep_configs
from fse.experiments.walkforward import WALKFORWARD_FILE_NAME, run_walkforward
from fse.logio import LogWriter
from fse.reports.markdown import (
    ExperimentHeader,
    experiment_header,
    holdout_excluded,
    holdout_included,
    render_ablation,
    render_cadence,
    render_frontier,
    render_holdout,
    render_pass_estimate,
    render_walkforward,
)
from fse.reports.tables import load_run, run_header
from fse.settings import default_paths

__all__ = [
    "EXIT_INVALID_INPUT",
    "KINDS",
    "NAME",
    "ExperimentUsageError",
    "candidate_name",
    "register",
]

NAME: Final = "experiment"
KINDS: Final[tuple[str, ...]] = (
    "ablation",
    "sweep",
    "montecarlo",
    "holdout",
    "walkforward",
    "cadence",
)

# Design "Exit codes".
EXIT_INVALID_INPUT: Final = 2

SEED_MAX: Final = 2**SEED_BITS - 1
_DIR_HEX: Final = 16
_UNSAFE: Final = re.compile(r"[^a-z0-9_.-]+")
PER_ROW_TRADES: Final = "see the trade count of each configuration below"

_EPILOG = """\
exit status:
  0    the experiment completed and its report was written
  1    unexpected error; the Run_Manifest is written with status aborted
  2    invalid input: a flag, the Strategy_Config, a sweep or candidate file,
       the date range, a calendar file, a path, too few sessions, or a
       directory that already holds the experiment's output
  4    a cached file, the backtest run (montecarlo) or the Holdout_Log
       (holdout) is missing or unreadable
  130  interrupted"""


class ExperimentUsageError(Exception):
    """Flags or an input file that cannot start an experiment (exit 2)."""

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


def _workers(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        value = 0
    if value < 1:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number of at least 1")
    return value


def _cadences(text: str) -> tuple[int, ...]:
    try:
        values = tuple(int(part) for part in text.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a comma-separated list of whole seconds, such as 5,60,300"
        ) from None
    return values


# ---------------------------------------------------------------- the parser


def _common(parser: argparse.ArgumentParser, *, dated: bool, workers: bool) -> None:
    add = parser.add_argument
    add("--config", type=Path, required=True, metavar="FILE", help="the Strategy_Config file")
    if dated:
        add("--start", type=_date, required=True, metavar="YYYY-MM-DD", help="first session")
        add("--end", type=_date, required=True, metavar="YYYY-MM-DD", help="last session")
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
        help="the experiment directory (default: ~/.skylit-fse/runs/<kind>-<hash>)",
    )
    if workers:
        add(
            "--workers",
            type=_workers,
            default=1,
            metavar="N",
            help="configurations run in N worker processes (default 1)",
        )


def _paths(parser: argparse.ArgumentParser) -> None:
    paths = parser.add_argument_group("paths")
    paths.add_argument(
        "--cache-dir", type=Path, metavar="DIR", help="the Data_Cache (default ~/.skylit-fse/cache)"
    )
    paths.add_argument(
        "--calendar-dir",
        type=Path,
        metavar="DIR",
        help="the folder holding the calendar files (default: the Project's calendars/)",
    )


def _sub(
    subparsers: SubParsers, name: str, help_text: str, description: str
) -> argparse.ArgumentParser:
    return subparsers.add_parser(
        name,
        help=help_text,
        description=description,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )


def register(subparsers: SubParsers) -> None:
    parser = subparsers.add_parser(
        NAME,
        help="run an ablation, sweep, pass estimate, holdout, walk-forward or cadence test",
        description=(
            "Experiments over cached sessions. Every sweep, ablation, cadence comparison and "
            "walk-forward test leaves out the Holdout_Period, the newest sessions with data."
        ),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    kinds = parser.add_subparsers(dest="kind", metavar="<kind>", title="kinds", required=True)

    p = _sub(
        kinds, "ablation", "the base config and one variant per enabled Gate",
        "Run the base Strategy_Config and one variant per enabled Gate with only that Gate "
        "disabled, on the same sessions and seed, and report each variant's change in trades "
        "per day, win rate, expectancy, profit factor, maximum drawdown and Combine_Pass "
        "probability.",
    )  # fmt: skip
    _common(p, dated=True, workers=True)
    _paths(p)
    p.set_defaults(handler=_ablation)

    p = _sub(
        kinds, "sweep", "the win-rate vs reward:risk frontier",
        "Run one configuration per Exit_Mode and swept R multiple of the sweep definition "
        "(YAML keys: exit_modes, r_multiples, ranking_objective, bootstrap_resamples) and "
        "write the frontier table, the Pareto set, the reference win-rate list and the ranking.",
    )  # fmt: skip
    _common(p, dated=True, workers=True)
    p.add_argument("--sweep", type=Path, required=True, metavar="FILE", help="the sweep definition")
    _paths(p)
    p.set_defaults(handler=_sweep)

    p = _sub(
        kinds, "montecarlo", "the Combine pass estimate of a completed backtest",
        "Resample the sessions of a completed backtest into Combine paths and report the pass, "
        "failure and unresolved shares and the trading days to pass. --config must be the "
        "run's Strategy_Config; its experiments.montecarlo section sets the paths.",
    )  # fmt: skip
    p.add_argument("--run", required=True, metavar="RUN_ID", help="the run id or run directory")
    p.add_argument(
        "--runs-dir",
        type=Path,
        metavar="DIR",
        help="the folder holding the run directories (default: ~/.skylit-fse/runs)",
    )
    _common(p, dated=False, workers=False)
    p.set_defaults(handler=_montecarlo)

    p = _sub(
        kinds, "holdout", "the named config on the Holdout_Period sessions",
        "Run the named Strategy_Config, unchanged, on the Holdout_Period sessions only and "
        "append one entry to the Holdout_Log. A warning is printed first when an earlier "
        "entry shares a session with this Holdout_Period.",
    )  # fmt: skip
    _common(p, dated=False, workers=False)
    p.add_argument(
        "--holdout-log",
        type=Path,
        metavar="FILE",
        help=f"the Holdout_Log (default: ~/.skylit-fse/{HOLDOUT_LOG_FILE_NAME})",
    )
    _paths(p)
    p.set_defaults(handler=_holdout)

    p = _sub(
        kinds, "walkforward", "train-and-test window pairs over candidate configs",
        "Split the sessions before the Holdout_Period into training and test window pairs "
        "(experiments.walkforward of --config), select the best candidate on each training "
        "window and run it on the test window. Candidates are the --candidate files and the "
        "configurations of --sweep, in that order.",
    )  # fmt: skip
    _common(p, dated=True, workers=True)
    p.add_argument(
        "--candidate",
        type=Path,
        action="append",
        default=[],
        metavar="FILE",
        help="a candidate Strategy_Config file (repeatable)",
    )
    p.add_argument("--sweep", type=Path, metavar="FILE", help="a sweep definition of candidates")
    p.add_argument(
        "--objective",
        choices=RANKING_OBJECTIVES,
        help="the selection objective (default: experiments.walkforward.objective)",
    )
    _paths(p)
    p.set_defaults(handler=_walkforward)

    p = _sub(
        kinds, "cadence", "one config at several Decision_Cadences",
        "Run one Strategy_Config at each cadence on the sessions whose stored Snapshot and bar "
        "intervals are no longer than the shortest cadence, and report the per-cadence values "
        "and the shorter-minus-longer differences.",
    )  # fmt: skip
    _common(p, dated=True, workers=True)
    p.add_argument(
        "--cadences",
        type=_cadences,
        default=CADENCES_DEFAULT,
        metavar="S,S,...",
        help="the compared cadences in seconds (default 5,60,300)",
    )
    _paths(p)
    p.set_defaults(handler=_cadence)


# ---------------------------------------------------------------- shared steps


def _calendar_dir(args: argparse.Namespace, ctx: CommandContext) -> Path:
    given: Path | None = args.calendar_dir
    if given is not None:
        return given
    if ctx.project_dir is None:
        raise ExperimentUsageError("the Project folder was not found; pass --calendar-dir DIR")
    return project_calendar_dir(ctx.project_dir)


def _default_dir(kind: str, *parts: object) -> Path:
    text = "|".join(str(p) for p in (kind, *parts))
    return default_paths().runs / f"{kind}-{hashlib.sha256(text.encode()).hexdigest()[:_DIR_HEX]}"


def _dirs(args: argparse.Namespace, default_out: Path) -> tuple[Path, Path]:
    cache: Path = default_paths().cache if args.cache_dir is None else args.cache_dir
    out: Path = default_out if args.out is None else args.out
    checked = check_output_dirs({"cache directory": cache, "experiment directory": out})
    return checked["cache directory"], checked["experiment directory"]


def _requested(args: argparse.Namespace) -> DataRange:
    return backtest_range(args.start, args.end)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_report(writer: LogWriter, path: Path, text: str) -> None:
    writer.write_text(path, text)
    writer.echo(f"Report: {path}")


def _config_text(path: Path, cfg: StrategyConfig) -> str:
    return f"{path} (hash {config_hash(cfg)})"


def _echo_start(writer: LogWriter, kind: str, seed: int, out: Path, extra: str = "") -> None:
    writer.echo(f"Experiment {kind}: seed {seed}; output {out}{extra}")


def _echo_result(writer: LogWriter, result: ExperimentResult) -> None:
    held = result.holdout
    period = "none" if held is None else f"{held.first} to {held.last} ({len(held)} sessions)"
    writer.echo(
        f"Experiment {result.run_id} completed: {len(result.compared_sessions)} sessions "
        f"compared; {result.distinct_configurations} distinct configurations; "
        f"Holdout_Period excluded: {period}"
    )
    for o in result.outcomes:
        if not o.completed:
            writer.error(f"configuration {o.name} failed: {o.error}")
    writer.echo(f"Run_Manifest: {result.run_dir / MANIFEST_FILE_NAME}")


def _multi_header(
    cfg: StrategyConfig,
    title: str,
    result: ExperimentResult,
    config: str,
    *,
    decision_cadence: str | None = None,
) -> ExperimentHeader:
    return experiment_header(
        cfg,
        title=title,
        sessions=result.compared_sessions,
        trades=PER_ROW_TRADES,
        holdout=holdout_excluded(result.holdout),
        config=config,
        seed=result.seed,
        decision_cadence=decision_cadence,
    )


def _read_yaml(path: Path, what: str) -> object:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ExperimentUsageError(f"the {what} {path} does not exist") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise ExperimentUsageError(f"the {what} {path} cannot be read: {exc}") from None
    loader = UniqueKeySafeLoader(text)
    try:
        data = loader.get_single_data()
        duplicates = list(loader.duplicates)
    except yaml.YAMLError as exc:
        raise ExperimentUsageError(f"the {what} {path} is not valid YAML: {exc}") from None
    finally:
        loader.dispose()
    if duplicates:
        keys = ", ".join(f"{d.key_path} (line {d.line})" for d in duplicates)
        raise ExperimentUsageError(f"the {what} {path} repeats the key(s) {keys}")
    return data


def candidate_name(path: Path) -> str:
    """A configuration name from a candidate file name: lower case, path-safe, 1-64 chars."""
    name = _UNSAFE.sub("-", path.stem.lower()).lstrip("-_.")[:64]
    return name or "config"


# ---------------------------------------------------------------- handlers


def _ablation(args: argparse.Namespace, ctx: CommandContext) -> int:
    writer = ctx.writer
    cfg = load_config(args.config, writer)
    requested = _requested(args)
    calendar_dir = _calendar_dir(args, ctx)
    seed = resolve_seed(args.seed)
    cache, out = _dirs(
        args, _default_dir("ablation", config_hash(cfg), requested.start, requested.end, seed)
    )
    _echo_start(writer, "ablation", seed, out)
    result = run_ablation(
        cfg, requested, cache_dir=cache, calendar_dir=calendar_dir, out_dir=out, writer=writer,
        seed=seed, workers=args.workers, config_path=str(args.config),
    )  # fmt: skip
    _echo_result(writer, result)
    header = _multi_header(cfg, f"Ablation {result.run_id}", result, _config_text(args.config, cfg))
    md = render_ablation(
        header,
        _read_json(result.run_dir / ABLATION_FILE_NAME),
        _read_json(result.run_dir / COMPARISON_FILE_NAME),
    )
    _write_report(writer, result.run_dir / "ablation.md", md)
    return 0


def _sweep(args: argparse.Namespace, ctx: CommandContext) -> int:
    writer = ctx.writer
    cfg = load_config(args.config, writer)
    definition = _read_yaml(args.sweep, "sweep definition")
    parse_sweep(definition, cfg)  # SweepError (exit 2) before any directory is chosen
    requested = _requested(args)
    calendar_dir = _calendar_dir(args, ctx)
    seed = resolve_seed(args.seed)
    digest = hashlib.sha256(json.dumps(definition, sort_keys=True, default=str).encode())
    cache, out = _dirs(
        args,
        _default_dir(
            "sweep", config_hash(cfg), requested.start, requested.end, seed, digest.hexdigest()
        ),
    )
    _echo_start(writer, "sweep", seed, out)
    swept = run_sweep(
        cfg, definition, requested, cache_dir=cache, calendar_dir=calendar_dir, out_dir=out,
        writer=writer, seed=seed, workers=args.workers, config_path=str(args.config),
    )  # fmt: skip
    result = swept.experiment
    _echo_result(writer, result)
    header = _multi_header(
        cfg, f"Frontier sweep {result.run_id}", result, _config_text(args.config, cfg)
    )
    md = render_frontier(header, _read_json(result.run_dir / FRONTIER_FILE_NAME))
    _write_report(writer, result.run_dir / "frontier.md", md)
    return 0


def _run_dir(run: str, runs_dir: Path | None) -> Path:
    if not run.strip():
        raise ExperimentUsageError("--run must name a run id or a run directory")
    given = Path(run).expanduser()
    if runs_dir is None and (len(given.parts) > 1 or given.is_absolute()):
        return given
    base = default_paths().runs if runs_dir is None else runs_dir
    if len(given.parts) != 1 or given.name in {".", ".."}:
        raise ExperimentUsageError(f"--run {run!r} is not a run id inside --runs-dir {base}")
    return base / run


def _montecarlo(args: argparse.Namespace, ctx: CommandContext) -> int:
    writer = ctx.writer
    cfg = load_config(args.config, writer)
    run_dir = _run_dir(args.run, args.runs_dir)
    source = load_run_outcomes(run_dir)  # MonteCarloRunError (exit 4) before anything else
    seed = resolve_seed(args.seed)
    mc = cfg.experiments.montecarlo
    run_id = pass_estimate_run_id(source.run_id, mc.paths, mc.max_days, mc.min_sessions, seed)
    out = check_output_dir(
        default_paths().runs / run_id if args.out is None else args.out,
        label="pass-estimate directory",
    )
    _echo_start(writer, "montecarlo", seed, out, f"; source run {source.run_id}")
    done = run_pass_estimate(
        run_dir, cfg, out_dir=out, writer=writer, seed=seed, config_path=str(args.config)
    )
    est = done.estimate
    writer.echo(
        f"Pass estimate {done.run_id} completed: {est.passed} of {est.paths} paths passed, "
        f"{est.failed} failed, {est.unresolved} unresolved"
        + ("; insufficient sample" if est.insufficient_sample else "")
    )
    writer.echo(f"Run_Manifest: {done.run_dir / MANIFEST_FILE_NAME}")
    run = load_run(run_dir)
    trades = sum(1 for t in run.trades if not t.shadow)
    h = run_header(run, trades)
    header = experiment_header(
        cfg,
        title=f"Combine pass estimate {done.run_id}",
        sessions=tuple(o.session for o in source.outcomes),
        trades=f"{trades} (source run {source.run_id})",
        holdout=holdout_included(h),
        config=_config_text(args.config, cfg),
        seed=seed,
        decision_cadence=f"{h.decision_cadence_s} s",
    )
    md = render_pass_estimate(header, _read_json(done.run_dir / PASS_ESTIMATE_FILE_NAME))
    _write_report(writer, done.run_dir / "pass_estimate.md", md)
    return 0


def _holdout(args: argparse.Namespace, ctx: CommandContext) -> int:
    writer = ctx.writer
    # Loaded again inside, which prints the contradiction warnings once.
    cfg = load_or_raise(args.config).config
    calendar_dir = _calendar_dir(args, ctx)
    seed = resolve_seed(args.seed)
    defaults = default_paths()
    log_path: Path = (
        defaults.root / HOLDOUT_LOG_FILE_NAME if args.holdout_log is None else args.holdout_log
    )
    check_output_dir(log_path.parent, label="Holdout_Log directory")
    cache, out = _dirs(args, _default_dir("holdout", config_hash(cfg), seed))
    _echo_start(writer, "holdout", seed, out, f"; Holdout_Log {log_path}")
    done = run_holdout_evaluation(
        args.config, log_path=log_path, cache_dir=cache, calendar_dir=calendar_dir,
        out_dir=out, writer=writer, seed=seed,
    )  # fmt: skip
    held = done.holdout
    writer.echo(
        f"Holdout evaluation {done.run_id} completed on the Holdout_Period {held.first} to "
        f"{held.last} ({len(held)} sessions): {done.result.metrics.trade_count} trades; "
        f"{done.overlapping_entries} earlier overlapping Holdout_Log entries"
    )
    writer.echo(f"Run_Manifest: {done.run_dir / MANIFEST_FILE_NAME}")
    header = experiment_header(
        cfg,
        title=f"Holdout evaluation {done.run_id}",
        sessions=done.result.sessions,
        trades=str(done.result.metrics.trade_count),
        holdout=(
            f"yes; every session is a Holdout_Period session ({held.first} to {held.last}, "
            f"{len(held)} sessions)"
        ),
        config=_config_text(args.config, cfg),
        seed=seed,
    )
    md = render_holdout(
        header,
        _read_json(done.run_dir / HOLDOUT_RESULT_FILE_NAME),
        min_sample_trades=cfg.reporting.min_sample_trades,
    )
    _write_report(writer, done.run_dir / "holdout.md", md)
    return 0


def _candidates(
    args: argparse.Namespace, cfg: StrategyConfig, writer: LogWriter
) -> tuple[list[ExperimentConfig], list[object]]:
    """The walk-forward candidates (files, then sweep points) and what identifies them."""
    if not args.candidate and args.sweep is None:
        raise ExperimentUsageError("walkforward needs a --candidate FILE or a --sweep FILE")
    configs: list[ExperimentConfig] = []
    ident: list[object] = []
    for path in args.candidate:
        loaded = load_config(path, writer)
        configs.append(ExperimentConfig(candidate_name(path), loaded))
        ident.append(config_hash(loaded))
    if args.sweep is not None:
        definition = _read_yaml(args.sweep, "sweep definition")
        for point in sweep_configs(cfg, parse_sweep(definition, cfg)):
            configs.append(ExperimentConfig(point.name, point.cfg))
            ident.append(config_hash(point.cfg))
    return configs, ident


def _walkforward(args: argparse.Namespace, ctx: CommandContext) -> int:
    writer = ctx.writer
    cfg = load_config(args.config, writer)
    configs, ident = _candidates(args, cfg, writer)
    requested = _requested(args)
    calendar_dir = _calendar_dir(args, ctx)
    seed = resolve_seed(args.seed)
    objective = cast("RankingObjective | None", args.objective)
    cache, out = _dirs(
        args,
        _default_dir(
            "walkforward", config_hash(cfg), requested.start, requested.end, seed, objective,
            *ident,
        ),
    )  # fmt: skip
    _echo_start(writer, "walkforward", seed, out)
    result = run_walkforward(
        cfg, configs, requested, cache_dir=cache, calendar_dir=calendar_dir, out_dir=out,
        writer=writer, seed=seed, workers=args.workers, config_path=str(args.config),
        objective=objective,
    )  # fmt: skip
    held = result.holdout
    period = "none" if held is None else f"{held.first} to {held.last} ({len(held)} sessions)"
    writer.echo(
        f"Walk-forward {result.run_id} completed: {len(result.pairs)} window pairs, "
        f"{result.no_selection_count} with no selection; {result.distinct_configurations} "
        f"distinct configurations; Holdout_Period excluded: {period}"
    )
    writer.echo(f"Run_Manifest: {result.run_dir / MANIFEST_FILE_NAME}")
    header = experiment_header(
        cfg,
        title=f"Walk-forward test {result.run_id}",
        sessions=result.sessions,
        trades="see the in-sample and out-of-sample trade counts below",
        holdout=holdout_excluded(held),
        config=_config_text(args.config, cfg),
        seed=result.seed,
        decision_cadence="per candidate configuration",
        notes=(f"Candidates: {', '.join(c.name for c in configs)}",),
    )
    md = render_walkforward(
        header,
        _read_json(result.run_dir / WALKFORWARD_FILE_NAME),
        min_sample_trades=cfg.reporting.min_sample_trades,
    )
    _write_report(writer, result.run_dir / "walkforward.md", md)
    return 0


def _cadence(args: argparse.Namespace, ctx: CommandContext) -> int:
    writer = ctx.writer
    cfg = load_config(args.config, writer)
    cadences: Sequence[int] = args.cadences
    requested = _requested(args)
    calendar_dir = _calendar_dir(args, ctx)
    seed = resolve_seed(args.seed)
    cache, out = _dirs(
        args,
        _default_dir(
            "cadence", config_hash(cfg), requested.start, requested.end, seed,
            ",".join(str(c) for c in cadences),
        ),
    )  # fmt: skip
    _echo_start(writer, "cadence", seed, out)
    result = run_cadence_comparison(
        cfg, requested, cadences=cadences, cache_dir=cache, calendar_dir=calendar_dir,
        out_dir=out, writer=writer, seed=seed, workers=args.workers,
        config_path=str(args.config),
    )  # fmt: skip
    _echo_result(writer, result)
    shown = ", ".join(str(c) for c in sorted(set(cadences)))
    header = _multi_header(
        cfg,
        f"Cadence comparison {result.run_id}",
        result,
        _config_text(args.config, cfg),
        decision_cadence=f"per row ({shown} s)",
    )
    md = render_cadence(
        header,
        _read_json(result.run_dir / CADENCE_FILE_NAME),
        _read_json(result.run_dir / COMPARISON_FILE_NAME),
        min_sample_trades=cfg.reporting.min_sample_trades,
    )
    _write_report(writer, result.run_dir / "cadence.md", md)
    return 0
