"""The Revised_Drafts builder behind ``fse drafts build`` (design §26, Req 26.9-26.17).

**Rules** (Req 26.10). The rules are every Pattern (``patterns.<id>``), Gate
(``gates.<id>``), Exit_Mode (``exits.modes.<mode>``) and Kill_Switch
(``kill_switches.<id>``) of the Config_Schema, in that order and in schema
order within each kind; the Playbook_Baseline sets each one, written or by
default. A rule's setting is its whole entry (the ``enabled`` flag and every
parameter). Each rule gets one label from the chosen Strategy_Config and the
Playbook_Baseline:

- Kept: enabled in the chosen config, entry equal to the baseline's;
- Changed: enabled in the chosen config, and disabled in the baseline or
  with a parameter value that differs (each differing key is listed);
- Removed: disabled in the chosen config.

**Comparison configs** (Req 26.11). Per rule, the chosen config with only
that rule's entry replaced: ``enabled`` set to false for Kept, the baseline's
entry for Changed or Removed. The copy goes through ``model_copy``, so no
other key is re-parsed or re-defaulted. Comparison configs with equal config
hashes run once; one equal to the chosen config is not run again, and its
figures are 0.

**Figures.** The chosen config and the comparison configs run through
:func:`fse.experiments.runner.run_experiment` (kind ``drafts``, base = the
chosen config), so they share one session list before the Holdout_Period
and one seed. Per rule: the change in mean trades per session and the change
in expectancy (mean R_Multiple per trade), each chosen minus comparison;
"not available" when either run failed, "not applicable" when either
expectancy is undefined. Every figure line names, for both runs, the trade
count, the session count, the first and last session dates labeled
pre-holdout, and the config hash (Req 26.15).

**Holdout_Period** (Req 26.9). The build needs a Holdout_Log entry for the
chosen config's hash (``fse experiment holdout`` first); the newest one is
shown with its trade count, session count, dates labeled Holdout_Period,
config hash and the Monte_Carlo_Simulator path count of the chosen config.

**Unmeasured** (Req 26.12). Every rules-table row of ``docs/traceability.md``
whose "Codified as" cell starts with "not codified" is listed by id and
source with that cell as its reason, and no figure. The rule text itself is
not copied: many rows are trade instructions (Req 26.14).

**Files** (Req 26.9). ``revised_SKILL_<date>.md`` and ``revised_TASK_<date>.md``
(``<date>``: the New York date, YYYY-MM-DD) in the fine-tune folder, from the
templates in ``templates/``, each with one front-matter block. The skill
draft takes ``name`` from ``SKILL.md``. Each draft must pass the full
:func:`fse.skilldocs.check.check_document` check with the draft lint before
it is written, and is created only if no file of that name exists: no
existing file is ever changed. ``SKILL.md`` is written only by
:func:`fse.skilldocs.approvals.promote`.
"""

from __future__ import annotations

import json
import os
import string
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from importlib import resources
from pathlib import Path
from typing import Any, ClassVar, Final, Literal, cast

import yaml
from pydantic import BaseModel

from fse.analytics.metrics import round_fraction
from fse.backtest.manifest import DataRange
from fse.config.hashing import config_hash
from fse.config.schema import StrategyConfig
from fse.config.schema.exits import EXIT_MODES
from fse.config.schema.gates import GATE_IDS
from fse.config.schema.kill_switches import KILL_SWITCH_IDS
from fse.config.schema.patterns import DETECTOR_IDS
from fse.engine.types import NotApplicable
from fse.experiments.holdout import read_holdout_log
from fse.experiments.runner import (
    ConfigOutcome,
    Evaluator,
    ExperimentConfig,
    ExperimentResult,
    backtest_evaluator,
    run_experiment,
)
from fse.logio import LogWriter
from fse.logio.canonical_json import JsonValue
from fse.skilldocs import (
    FINE_TUNE_DIR,
    REVISED_SKILL_PREFIX,
    REVISED_TASK_PREFIX,
    SKILL_FILE,
)
from fse.skilldocs.check import check_document
from fse.timekit import Instant, SessionTimes, ny_datetime

__all__ = [
    "CHOSEN_NAME",
    "DRAFTS_FILE_NAME",
    "DRAFTS_KIND",
    "LABELS",
    "RULE_KINDS",
    "Change",
    "DraftBuild",
    "DraftError",
    "Label",
    "LabeledRule",
    "Rule",
    "RuleFigures",
    "RuleKind",
    "UnmeasuredRule",
    "baseline_rules",
    "build_drafts",
    "comparison_name",
    "draft_configs",
    "draft_paths",
    "label_rule",
    "label_rules",
    "parse_unmeasured",
    "render_draft",
    "rule_entry",
    "rule_figures",
    "with_entry",
]

DRAFTS_KIND: Final = "drafts"
DRAFTS_FILE_NAME: Final = "drafts.json"
CHOSEN_NAME: Final = "chosen"

type RuleKind = Literal["Pattern", "Gate", "Exit_Mode", "Kill_Switch"]
type Label = Literal["Kept", "Changed", "Removed"]
RULE_KINDS: Final[tuple[RuleKind, ...]] = ("Pattern", "Gate", "Exit_Mode", "Kill_Switch")
LABELS: Final[tuple[Label, ...]] = ("Kept", "Changed", "Removed")

_SECTIONS: Final[Mapping[RuleKind, tuple[tuple[str, ...], tuple[str, ...]]]] = {
    "Pattern": (("patterns",), DETECTOR_IDS),
    "Gate": (("gates",), GATE_IDS),
    "Exit_Mode": (("exits", "modes"), EXIT_MODES),
    "Kill_Switch": (("kill_switches",), KILL_SWITCH_IDS),
}
_TEMPLATES: Final = {"SKILL": "revised_SKILL.md", "TASK": "revised_TASK.md"}
_DESCRIPTIONS: Final = {
    "SKILL": (
        "Narrator rules and measured rule changes for the Skylit Futures Strategy Engine. "
        "The Narrator restates Finding_Card data; the Strategy_Engine makes every entry and "
        "exit decision."
    ),
    "TASK": (
        "Session task for the Narrator of the Skylit Futures Strategy Engine. The Narrator "
        "restates Finding_Card data; the Strategy_Engine makes every entry and exit decision."
    ),
}
_PLACES: Final = 4
_NOT_CODIFIED: Final = "not codified"
_RULES_HEADING: Final = "## Rules"
_ARROW: Final = "\u2192"
_NONE: Final = "None."


class DraftError(Exception):
    """The drafts cannot be built; nothing was written to the fine-tune folder."""

    exit_code: int

    INVALID: ClassVar[int] = 2
    UNREADABLE: ClassVar[int] = 4
    UNEXPECTED: ClassVar[int] = 1

    def __init__(self, message: str, *, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


# ---------------------------------------------------------------- rules and labels


@dataclass(frozen=True, slots=True)
class Rule:
    """One Pattern, Gate, Exit_Mode or Kill_Switch, by its Strategy_Config path."""

    kind: RuleKind
    rule_id: str
    path: tuple[str, ...]

    @property
    def key_path(self) -> str:
        return ".".join(self.path)


@dataclass(frozen=True, slots=True)
class Change:
    """One key of a rule's entry whose baseline (``old``) and chosen (``new``) values differ."""

    key: str
    old: JsonValue
    new: JsonValue


@dataclass(frozen=True, slots=True)
class LabeledRule:
    """A rule's label, its differing keys (Changed) and its comparison config."""

    rule: Rule
    label: Label
    changes: tuple[Change, ...]
    comparison: StrategyConfig


def baseline_rules() -> tuple[Rule, ...]:
    """Every Pattern, Gate, Exit_Mode and Kill_Switch, in the module-notes order."""
    return tuple(
        Rule(kind, rule_id, (*prefix, rule_id))
        for kind in RULE_KINDS
        for prefix, ids in (_SECTIONS[kind],)
        for rule_id in ids
    )


def rule_entry(cfg: StrategyConfig, rule: Rule) -> BaseModel:
    """The entry of ``rule`` in ``cfg``."""
    node: Any = cfg
    for part in rule.path:
        node = getattr(node, part)
    if not isinstance(node, BaseModel):
        raise TypeError(f"{rule.key_path} is not a config entry")
    return node


def _enabled(entry: BaseModel) -> bool:
    flag = getattr(entry, "enabled", None)
    if not isinstance(flag, bool):
        raise TypeError(f"{type(entry).__name__} has no enabled flag")
    return flag


def with_entry(cfg: StrategyConfig, rule: Rule, entry: BaseModel) -> StrategyConfig:
    """``cfg`` with the entry of ``rule`` replaced by ``entry`` and nothing else changed."""
    parents: list[BaseModel] = [cfg]
    for part in rule.path[:-1]:
        parents.append(getattr(parents[-1], part))
    new: BaseModel = entry
    for parent, part in zip(reversed(parents), reversed(rule.path), strict=True):
        new = parent.model_copy(update={part: new})
    if not isinstance(new, StrategyConfig):
        raise TypeError("the rebuilt config is not a StrategyConfig")
    return new


def _leaves(value: object, prefix: str = "") -> dict[str, JsonValue]:
    if isinstance(value, dict):
        out: dict[str, JsonValue] = {}
        for key, item in value.items():
            out.update(_leaves(item, f"{prefix}.{key}" if prefix else str(key)))
        return out
    return {prefix: cast(JsonValue, value)}


def _diff(old: object, new: object) -> tuple[Change, ...]:
    a, b = _leaves(old), _leaves(new)
    return tuple(
        Change(key, a.get(key), b.get(key))
        for key in sorted(a.keys() | b.keys())
        if a.get(key) != b.get(key)
    )


def label_rule(baseline: StrategyConfig, chosen: StrategyConfig, rule: Rule) -> LabeledRule:
    """The Req 26.10 label of ``rule`` and its Req 26.11 comparison config (module notes)."""
    base_entry, chosen_entry = rule_entry(baseline, rule), rule_entry(chosen, rule)
    changes = _diff(base_entry.model_dump(mode="json"), chosen_entry.model_dump(mode="json"))
    label: Label
    shown: tuple[Change, ...] = ()
    if not _enabled(chosen_entry):
        label = "Removed"
    elif _enabled(base_entry) and not changes:
        label = "Kept"
    else:
        label, shown = "Changed", changes
    if label == "Kept":
        comparison = with_entry(chosen, rule, chosen_entry.model_copy(update={"enabled": False}))
    else:
        comparison = with_entry(chosen, rule, base_entry)
    return LabeledRule(rule, label, shown, comparison)


def label_rules(baseline: StrategyConfig, chosen: StrategyConfig) -> tuple[LabeledRule, ...]:
    """:func:`label_rule` for every rule of :func:`baseline_rules`, in that order."""
    return tuple(label_rule(baseline, chosen, r) for r in baseline_rules())


def comparison_name(rule: Rule) -> str:
    """The experiment configuration name of ``rule``'s comparison: ``cmp-<section>-<id>``."""
    return f"cmp-{rule.path[0]}-{rule.rule_id}"


def draft_configs(
    chosen: StrategyConfig, labeled: Sequence[LabeledRule]
) -> tuple[tuple[ExperimentConfig, ...], dict[str, str]]:
    """The chosen config, then each distinct comparison; and each rule's configuration name.

    The mapping goes from a rule's key path to the configuration that runs
    its comparison: :data:`CHOSEN_NAME` when the comparison is the chosen
    config, else the first rule's :func:`comparison_name` with that hash.
    """
    configs = [ExperimentConfig(CHOSEN_NAME, chosen)]
    by_hash = {config_hash(chosen): CHOSEN_NAME}
    names: dict[str, str] = {}
    for lr in labeled:
        h = config_hash(lr.comparison)
        if h not in by_hash:
            by_hash[h] = comparison_name(lr.rule)
            configs.append(ExperimentConfig(by_hash[h], lr.comparison))
        names[lr.rule.key_path] = by_hash[h]
    return tuple(configs), names


# ---------------------------------------------------------------- figures


@dataclass(frozen=True, slots=True)
class RuleFigures:
    """Chosen minus comparison: ``None`` not available, ``NotApplicable`` undefined."""

    trades_per_session: Fraction | None
    expectancy_r: Fraction | NotApplicable | None


def rule_figures(chosen: ConfigOutcome, comparison: ConfigOutcome) -> RuleFigures:
    """The two Req 26.11 figures of one rule."""
    a, b = chosen.result, comparison.result
    if a is None or b is None:
        return RuleFigures(None, None)
    tps = a.metrics.trades_per_day - b.metrics.trades_per_day
    ea, eb = a.metrics.expectancy_r, b.metrics.expectancy_r
    if isinstance(ea, NotApplicable) or isinstance(eb, NotApplicable):
        return RuleFigures(tps, NotApplicable())
    return RuleFigures(tps, ea - eb)


# ---------------------------------------------------------------- traceability


@dataclass(frozen=True, slots=True)
class UnmeasuredRule:
    """A Skill_Document rule that no Pattern, Gate, Exit_Mode or Kill_Switch codifies."""

    rule_id: str
    source: str
    reason: str


def parse_unmeasured(text: str) -> tuple[UnmeasuredRule, ...]:
    """Every "not codified" row of the rules tables in ``text`` (module notes).

    Raises :class:`DraftError` (exit 4) when there is no ``## Rules`` section
    or a rules-table row does not have four cells.
    """
    lines = text.split("\n")
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == _RULES_HEADING)
    except StopIteration:
        raise DraftError(
            "the traceability file has no '## Rules' section", exit_code=DraftError.UNREADABLE
        ) from None
    out: list[UnmeasuredRule] = []
    for n, line in enumerate(lines[start + 1 :], start + 2):
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        if not stripped.startswith("|") or stripped.startswith(("| ID", "| ---")):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) != 4:
            raise DraftError(
                f"the traceability file line {n} is not a four-cell rules row",
                exit_code=DraftError.UNREADABLE,
            )
        rule_id, _rule, source, codified = cells
        if codified.startswith(_NOT_CODIFIED):
            out.append(UnmeasuredRule(rule_id, source, " ".join(codified.split("<br>"))))
    return tuple(out)


# ---------------------------------------------------------------- holdout


def _holdout_entry(log_path: Path, cfg_hash: str) -> dict[str, Any]:
    """The newest Holdout_Log entry for ``cfg_hash``; :class:`DraftError` when there is none."""
    entries = [e for e in read_holdout_log(log_path) if e.config_hash == cfg_hash]
    if not entries:
        raise DraftError(
            f"the Holdout_Log {log_path} has no entry for the chosen config (hash {cfg_hash}); "
            "run `fse experiment holdout --config <chosen>` first"
        )
    line = log_path.read_text(encoding="utf-8").splitlines()[entries[-1].line - 1]
    entry: dict[str, Any] = json.loads(line)
    return entry


# ---------------------------------------------------------------- rendering


def _json_value(value: JsonValue) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _signed(value: Fraction) -> str:
    return f"{round_fraction(value, _PLACES):+f}"


def _run_facts(o: ConfigOutcome) -> str:
    r = o.result
    if r is None:
        return f"failed, config hash {o.config_hash}"
    low = " (insufficient sample)" if o.insufficient_sample else ""
    return (
        f"{r.metrics.trade_count} trades{low}, {len(r.sessions)} sessions, "
        f"{r.sessions[0] if r.sessions else 'none'} to {r.sessions[-1] if r.sessions else 'none'} "
        f"(pre-holdout), config hash {o.config_hash}"
    )


def _figure_text(f: RuleFigures) -> str:
    tps = "not available" if f.trades_per_session is None else _signed(f.trades_per_session)
    if f.expectancy_r is None:
        exp = "not available"
    elif isinstance(f.expectancy_r, NotApplicable):
        exp = "not applicable"
    else:
        exp = f"{_signed(f.expectancy_r)} R per trade"
    return f"Change in mean trades per session: {tps}; change in expectancy: {exp}."


def _rule_line(lr: LabeledRule, chosen: ConfigOutcome, comparison: ConfigOutcome) -> str:
    label: str = lr.label
    if lr.changes:
        shown = "; ".join(
            f"`{c.key}`: {_json_value(c.old)} {_ARROW} {_json_value(c.new)}" for c in lr.changes
        )
        label = f"{label} ({shown})"
    same = " The comparison config equals the chosen config." if comparison is chosen else ""
    return (
        f"- `{lr.rule.key_path}` ({lr.rule.kind}): {label}. "
        f"{_figure_text(rule_figures(chosen, comparison))}{same} "
        f"Chosen: {_run_facts(chosen)}; comparison: {_run_facts(comparison)}."
    )


def _other_lines(baseline: StrategyConfig, chosen: StrategyConfig) -> str:
    prefixes = tuple(f"{r.key_path}." for r in baseline_rules())
    changes = [
        c
        for c in _diff(baseline.model_dump(mode="json"), chosen.model_dump(mode="json"))
        if not c.key.startswith(prefixes)
    ]
    if not changes:
        return _NONE
    return "\n".join(
        f"- `{c.key}`: {_json_value(c.old)} {_ARROW} {_json_value(c.new)}" for c in changes
    )


def _holdout_text(entry: Mapping[str, Any], paths: int) -> str:
    held = entry["holdout"]
    expectancy = str(entry["expectancy_r"])
    if expectancy != "not applicable":
        expectancy = f"{expectancy} R per trade"
    win = str(entry["win_rate_pct"])
    if win != "not applicable":
        win = f"{win}%"
    pass_p = f"{round_fraction(Fraction(str(entry['pass_probability'])), _PLACES)}"
    low = " (insufficient sample)" if entry.get("pass_estimate_insufficient_sample") else ""
    return (
        f"- Chosen config on the Holdout_Period: expectancy {expectancy}; "
        f"Primary_Win_Rate {win}; Combine_Pass probability {pass_p}{low}. "
        f"{entry['trade_count']} trades, {entry['holdout_sessions']} sessions, "
        f"{held['first']} to {held['last']} (Holdout_Period), "
        f"config hash {entry['config_hash']}, {paths} Monte_Carlo_Simulator paths, "
        f"run {entry['run_id']}."
    )


def _measurement_text(
    result: ExperimentResult,
    *,
    chosen_name: str,
    chosen_hash: str,
    baseline_name: str,
    baseline_hash: str,
) -> str:
    sessions = result.compared_sessions
    held = result.holdout
    period = "none" if held is None else f"{held.first} to {held.last}"
    span = f"from {sessions[0]} to {sessions[-1]}" if sessions else "(none completed)"
    return "\n\n".join(
        (
            f"Chosen Strategy_Config: `{chosen_name}`, config hash {chosen_hash}. "
            f"Playbook_Baseline: `{baseline_name}`, config hash {baseline_hash}.",
            f"Comparison runs: experiment {result.run_id}, seed {result.seed}, "
            f"{len(sessions)} pre-holdout sessions {span}, "
            f"{result.distinct_configurations} distinct configurations. "
            f"The Holdout_Period ({period}) was left out of every comparison run.",
            "The two figures per rule are the change in mean trades per session and the change "
            "in expectancy (mean R_Multiple per trade).",
        )
    )


def render_draft(kind: Literal["SKILL", "TASK"], values: Mapping[str, str]) -> str:
    """Fill the ``kind`` template with ``values`` (every ``$`` placeholder must be given)."""
    template = resources.files("fse.skilldocs").joinpath("templates", _TEMPLATES[kind])
    return string.Template(template.read_text(encoding="utf-8")).substitute(values)


# ---------------------------------------------------------------- files


def draft_paths(skill_root: Path, draft_date: date) -> tuple[Path, Path]:
    """The skill and task Revised_Draft paths for ``draft_date``."""
    folder = skill_root / FINE_TUNE_DIR
    day = draft_date.isoformat()
    return folder / f"{REVISED_SKILL_PREFIX}{day}.md", folder / f"{REVISED_TASK_PREFIX}{day}.md"


def _skill_name(skill_root: Path) -> str:
    path = skill_root / SKILL_FILE
    try:
        lines = path.read_text(encoding="utf-8").split("\n")
    except (OSError, UnicodeDecodeError) as exc:
        raise DraftError(
            f"cannot read {path}: {type(exc).__name__}", exit_code=DraftError.UNREADABLE
        ) from None
    close = next((i for i in range(1, len(lines)) if lines[i].rstrip() == "---"), None)
    data: object = None
    if lines[0].rstrip() == "---" and close is not None:
        try:
            data = yaml.safe_load("\n".join(lines[1:close]))
        except yaml.YAMLError:
            data = None
    name = data.get("name") if isinstance(data, dict) else None
    if not isinstance(name, str) or not name.strip():
        raise DraftError(
            f"{path} has no front-matter name to give the skill draft",
            exit_code=DraftError.UNREADABLE,
        )
    return name.strip()


def _create(path: Path, data: bytes) -> None:
    """Create ``path`` with ``data``; never replace an existing file."""
    with path.open("xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


# ---------------------------------------------------------------- the build


@dataclass(frozen=True, slots=True)
class DraftBuild:
    """What :func:`build_drafts` ran and wrote."""

    experiment: ExperimentResult
    labeled: tuple[LabeledRule, ...]
    unmeasured: tuple[UnmeasuredRule, ...]
    skill_draft: Path
    task_draft: Path


def _drafts_json(
    labeled: Sequence[LabeledRule],
    names: Mapping[str, str],
    unmeasured: Sequence[UnmeasuredRule],
    holdout_run: str,
    result: ExperimentResult,
) -> dict[str, JsonValue]:
    by_name = {o.name: o for o in result.outcomes}
    chosen = by_name[CHOSEN_NAME]

    def value(v: Fraction | NotApplicable | None) -> JsonValue:
        if v is None:
            return None
        if isinstance(v, NotApplicable):
            return "not applicable"
        return str(round_fraction(v, 6))

    rules: list[JsonValue] = []
    for lr in labeled:
        comparison = by_name[names[lr.rule.key_path]]
        f = rule_figures(chosen, comparison)
        rules.append(
            {
                "kind": lr.rule.kind,
                "id": lr.rule.rule_id,
                "key_path": lr.rule.key_path,
                "label": lr.label,
                "changes": [{"key": c.key, "old": c.old, "new": c.new} for c in lr.changes],
                "comparison": comparison.name,
                "comparison_config_hash": comparison.config_hash,
                "trades_per_session_change": value(f.trades_per_session),
                "expectancy_r_change": value(f.expectancy_r),
            }
        )
    return {
        "chosen_config_hash": chosen.config_hash,
        "holdout_run_id": holdout_run,
        "rules": rules,
        "unmeasured": [
            {"id": u.rule_id, "source": u.source, "reason": u.reason} for u in unmeasured
        ],
    }


def build_drafts(
    chosen: StrategyConfig,
    baseline: StrategyConfig,
    requested: DataRange,
    *,
    chosen_name: str,
    baseline_name: str,
    skill_root: Path,
    traceability: Path,
    holdout_log: Path,
    cache_dir: Path,
    calendar_dir: Path,
    out_dir: Path,
    writer: LogWriter,
    seed: int | None = None,
    workers: int = 1,
    evaluator: Evaluator = backtest_evaluator,
    secret_values: Sequence[str] = (),
    base_times: SessionTimes | None = None,
    clock: Callable[[], Instant] = time.time_ns,
    code_version: str | None = None,
) -> DraftBuild:
    """Run the comparisons and write the two Revised_Drafts (module notes).

    Every check of the fine-tune folder, ``SKILL.md``, the traceability file
    and the Holdout_Log runs before the experiment starts. Raises
    :class:`DraftError` (or an experiment error) with nothing written to
    the fine-tune folder.
    """
    draft_date = ny_datetime(clock()).date()
    skill_path, task_path = draft_paths(skill_root, draft_date)
    if not skill_path.parent.is_dir():
        raise DraftError(
            f"the fine-tune folder {skill_path.parent} does not exist",
            exit_code=DraftError.UNREADABLE,
        )
    taken = [p.name for p in (skill_path, task_path) if p.exists()]
    if taken:
        raise DraftError(
            f"{', '.join(taken)} already exist(s) in {skill_path.parent}; drafts are written "
            "as new files only"
        )
    name = _skill_name(skill_root)
    try:
        trace_text = traceability.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise DraftError(
            f"cannot read the traceability file {traceability}: {type(exc).__name__}",
            exit_code=DraftError.UNREADABLE,
        ) from None
    unmeasured = parse_unmeasured(trace_text)
    chosen_hash, baseline_hash = config_hash(chosen), config_hash(baseline)
    entry = _holdout_entry(holdout_log, chosen_hash)

    labeled = label_rules(baseline, chosen)
    configs, names = draft_configs(chosen, labeled)
    result = run_experiment(
        DRAFTS_KIND,
        chosen,
        configs,
        requested,
        cache_dir=cache_dir,
        calendar_dir=calendar_dir,
        out_dir=out_dir,
        writer=writer,
        seed=seed,
        workers=workers,
        evaluator=evaluator,
        outputs=lambda r: {
            DRAFTS_FILE_NAME: _drafts_json(labeled, names, unmeasured, str(entry["run_id"]), r)
        },
        config_path=chosen_name,
        base_times=base_times,
        clock=clock,
        code_version=code_version,
    )
    by_name = {o.name: o for o in result.outcomes}
    chosen_outcome = by_name[CHOSEN_NAME]
    if not chosen_outcome.completed:
        raise DraftError(
            f"the chosen config's run failed ({chosen_outcome.error}); no draft was written",
            exit_code=DraftError.UNREADABLE,
        )
    rules_text = "\n".join(
        _rule_line(lr, chosen_outcome, by_name[names[lr.rule.key_path]]) for lr in labeled
    )
    unmeasured_text = (
        "\n".join(f"- {u.rule_id} ({u.source}): Unmeasured. Reason: {u.reason}" for u in unmeasured)
        or _NONE
    )
    shared = {
        "draft_date": draft_date.isoformat(),
        "measurement": _measurement_text(
            result,
            chosen_name=chosen_name,
            chosen_hash=chosen_hash,
            baseline_name=baseline_name,
            baseline_hash=baseline_hash,
        ),
        "holdout": _holdout_text(entry, chosen.experiments.montecarlo.paths),
        "rules": rules_text,
        "other": _other_lines(baseline, chosen),
        "unmeasured": unmeasured_text,
    }
    texts = {
        "SKILL": render_draft(
            "SKILL",
            {**shared, "name": json.dumps(name), "description": json.dumps(_DESCRIPTIONS["SKILL"])},
        ),
        "TASK": render_draft(
            "TASK",
            {
                **shared,
                "name": json.dumps(f"{name} task"),
                "description": json.dumps(_DESCRIPTIONS["TASK"]),
            },
        ),
    }
    for kind, text in texts.items():
        failures = check_document(
            text.encode("utf-8"),
            front_matter_required=True,
            secret_values=secret_values,
            draft=True,
        )
        if failures:
            listed = "; ".join(f"{f.check}: {f.reason}" for f in failures)
            raise DraftError(
                f"the {kind} draft fails the check ({listed}); no draft was written",
                exit_code=DraftError.UNEXPECTED,
            )
    _create(skill_path, texts["SKILL"].encode("utf-8"))
    _create(task_path, texts["TASK"].encode("utf-8"))
    return DraftBuild(result, labeled, unmeasured, skill_path, task_path)
