"""``fse drafts``: build and promote the Revised_Drafts (design §26, Req 26.9-26.18).

- ``build --chosen FILE --start --end``: label every Pattern, Gate, Exit_Mode
  and Kill_Switch of the Playbook_Baseline (``--baseline``, default the
  Project's ``configs/playbook_baseline.yaml``) against the chosen config,
  run one comparison config per rule on the pre-holdout sessions of the
  range, and write ``revised_SKILL_<date>.md`` and ``revised_TASK_<date>.md``
  as new files in the fine-tune folder (:mod:`fse.skilldocs.drafts`). The
  chosen config needs a Holdout_Log entry first (``fse experiment holdout``).
- ``promote --draft revised_SKILL_<date>.md``: copy the draft into
  ``SKILL.md`` only when its sha256 is recorded in ``approvals.yaml``
  (:mod:`fse.skilldocs.approvals`).

The runs read only the local Data_Cache: no network request, no key.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Final

from fse.analytics.montecarlo import resolve_seed
from fse.commands import CommandContext, SubParsers
from fse.commands._config import load_config
from fse.commands.experiment import (
    ExperimentUsageError,
    _calendar_dir,
    _date,
    _default_dir,
    _dirs,
    _echo_result,
    _echo_start,
    _paths,
    _requested,
    _seed,
    _workers,
)
from fse.config.hashing import config_hash
from fse.experiments.holdout import HOLDOUT_LOG_FILE_NAME
from fse.settings import default_paths
from fse.skilldocs import APPROVALS_FILE, FINE_TUNE_DIR, SKILL_ROOT_NAME, default_skill_root
from fse.skilldocs.approvals import promote
from fse.skilldocs.drafts import DRAFTS_KIND, build_drafts

__all__ = ["NAME", "register"]

NAME: Final = "drafts"
BASELINE_FILE: Final = Path("configs") / "playbook_baseline.yaml"
TRACEABILITY_FILE: Final = Path("docs") / "traceability.md"

_EPILOG = """\
exit status:
  0    done
  1    unexpected error (build: the Run_Manifest is written with status
       aborted, or a rendered draft failed its own check; no draft written)
  2    invalid input: a flag, a Strategy_Config, the date range, a path, a
       draft that already exists, no Holdout_Log entry for the chosen config,
       or (promote) a draft with no recorded approval of its current bytes
  4    a file could not be read: a cached file, SKILL.md, the traceability
       file, the Holdout_Log, the approvals file, or the chosen config's run
       failed
  130  interrupted"""

_BUILD_DESCRIPTION = """\
Label every Pattern, Gate, Exit_Mode and Kill_Switch of the Playbook_Baseline
Kept, Changed or Removed against the chosen Strategy_Config, run one
comparison config per rule on the sessions before the Holdout_Period, and
write revised_SKILL_<date>.md and revised_TASK_<date>.md as new files in the
fine-tune folder. Run `fse experiment holdout --config <chosen>` first."""

_PROMOTE_DESCRIPTION = f"""\
Copy a revised_SKILL_<date>.md draft into SKILL.md. The sha256 of the draft's
current bytes must be recorded in {FINE_TUNE_DIR}/{APPROVALS_FILE}:

  approvals:
    - draft: revised_SKILL_<date>.md
      sha256: <the draft's sha256sum>

Without it SKILL.md is left unchanged and the command exits 2."""


def _skill_root_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--skill-root",
        type=Path,
        metavar="DIR",
        help=f"the folder that holds SKILL.md (default: ../{SKILL_ROOT_NAME} next to the Project)",
    )


def register(subparsers: SubParsers) -> None:
    parser = subparsers.add_parser(
        NAME,
        help="build or promote the Revised_Drafts",
        description="Revised_Draft tools.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = parser.add_subparsers(
        dest="drafts_action", metavar="<action>", title="actions", required=True
    )
    p = actions.add_parser(
        "build",
        help="build the Revised_Drafts for a chosen config",
        description=_BUILD_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add = p.add_argument
    add("--chosen", type=Path, required=True, metavar="FILE", help="the chosen Strategy_Config")
    add("--start", type=_date, required=True, metavar="YYYY-MM-DD", help="first session")
    add("--end", type=_date, required=True, metavar="YYYY-MM-DD", help="last session")
    add(
        "--baseline",
        type=Path,
        metavar="FILE",
        help=f"the Playbook_Baseline (default: the Project's {BASELINE_FILE.as_posix()})",
    )
    add(
        "--traceability",
        type=Path,
        metavar="FILE",
        help=f"the traceability table (default: the Project's {TRACEABILITY_FILE.as_posix()})",
    )
    add(
        "--holdout-log",
        type=Path,
        metavar="FILE",
        help=f"the Holdout_Log (default: ~/.skylit-fse/{HOLDOUT_LOG_FILE_NAME})",
    )
    _skill_root_arg(p)
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
        help="the experiment directory (default: ~/.skylit-fse/runs/drafts-<hash>)",
    )
    add(
        "--workers",
        type=_workers,
        default=1,
        metavar="N",
        help="configurations run in N worker processes (default 1)",
    )
    _paths(p)
    p.set_defaults(handler=_build)

    p = actions.add_parser(
        "promote",
        help="copy an approved skill draft into SKILL.md",
        description=_PROMOTE_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--draft", required=True, metavar="NAME", help="the revised_SKILL_<date>.md file name"
    )
    _skill_root_arg(p)
    p.set_defaults(handler=_promote)


def _project_file(given: Path | None, ctx: CommandContext, default: Path, flag: str) -> Path:
    if given is not None:
        return given
    if ctx.project_dir is None:
        raise ExperimentUsageError(f"the Project folder was not found; pass {flag} FILE")
    return ctx.project_dir / default


def _skill_root(args: argparse.Namespace, ctx: CommandContext) -> Path:
    given: Path | None = args.skill_root
    if given is not None:
        return given
    if ctx.project_dir is None:
        raise ExperimentUsageError("the Project folder was not found; pass --skill-root DIR")
    return default_skill_root(ctx.project_dir)


def _build(args: argparse.Namespace, ctx: CommandContext) -> int:
    writer = ctx.writer
    chosen = load_config(args.chosen, writer)
    baseline_path = _project_file(args.baseline, ctx, BASELINE_FILE, "--baseline")
    baseline = load_config(baseline_path, writer)
    traceability = _project_file(args.traceability, ctx, TRACEABILITY_FILE, "--traceability")
    skill_root = _skill_root(args, ctx)
    requested = _requested(args)
    calendar_dir = _calendar_dir(args, ctx)
    seed = resolve_seed(args.seed)
    log_path: Path = (
        default_paths().root / HOLDOUT_LOG_FILE_NAME
        if args.holdout_log is None
        else args.holdout_log
    )
    cache, out = _dirs(
        args,
        _default_dir(
            DRAFTS_KIND,
            config_hash(chosen),
            config_hash(baseline),
            requested.start,
            requested.end,
            seed,
        ),
    )
    _echo_start(writer, DRAFTS_KIND, seed, out)
    done = build_drafts(
        chosen, baseline, requested, chosen_name=args.chosen.name,
        baseline_name=baseline_path.name, skill_root=skill_root, traceability=traceability,
        holdout_log=log_path, cache_dir=cache, calendar_dir=calendar_dir, out_dir=out,
        writer=writer, seed=seed, workers=args.workers, secret_values=ctx.env.secret_values(),
    )  # fmt: skip
    _echo_result(writer, done.experiment)
    writer.echo(f"Revised_Draft: {done.skill_draft}")
    writer.echo(f"Revised_Draft: {done.task_draft}")
    writer.echo(
        "Next: review both drafts, run `fse skilldocs check` and `fse scan-secrets`, and record "
        f"an approval in {FINE_TUNE_DIR}/{APPROVALS_FILE} only after the review."
    )
    return 0


def _promote(args: argparse.Namespace, ctx: CommandContext) -> int:
    done = promote(_skill_root(args, ctx), args.draft)
    state = "updated" if done.changed else "already held these bytes"
    ctx.writer.echo(f"Promoted {done.draft.name} (sha256 {done.sha256}): {done.skill} {state}")
    return 0
