"""Property 85: Draft labels and comparison configs.

*For any* Playbook_Baseline and chosen config, every Pattern, Gate,
Exit_Mode and Kill_Switch in the baseline gets exactly one of Kept, Changed
or Removed by the Requirement 26.10 definitions, and each rule's comparison
config differs from the chosen config only in that rule (disabled for Kept,
set to the baseline for Changed or Removed).

**Inputs.** A baseline and a chosen config, each the minimal valid config
with, per rule, a drawn ``enabled`` flag and a drawn offset (0, 1 or 2)
added to the entry's first numeric parameter (when it has one), so the two
entries can be equal, differ only in ``enabled``, only in a parameter, or
both. Per configuration, a drawn outcome: failed, or a trades-per-session
value and an expectancy (a number or not applicable).

**Model.** Written from the Req 26.10 text on the entries' dumps: Removed
when the chosen entry is disabled; Kept when both are enabled with equal
dumps; Changed otherwise, listing exactly the differing keys.

**Checks.**

- One labeled rule per Pattern, Gate, Exit_Mode and Kill_Switch, in schema
  order (8 + 27 + 5 + 6), each with the model's label and changes.
- Each comparison config's dump differs from the chosen config's only under
  the rule's key path, and its entry dumps as the chosen entry with
  ``enabled`` false (Kept) or as the baseline entry (Changed, Removed).
- :func:`draft_configs` lists the chosen config first and each distinct
  config hash once, and maps every rule to a configuration whose hash is
  its comparison's.
- Each rule's figures are chosen minus comparison: not available when either
  run failed, not applicable when either expectancy is (Req 26.11).

**Validates: Requirements 26.10, 26.11**
"""

from __future__ import annotations

import dataclasses
from datetime import date
from fractions import Fraction
from typing import Any

from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel

from fse.analytics.metrics import MetricsCfg, summarize
from fse.analytics.montecarlo import PassEstimate
from fse.config.hashing import config_hash
from fse.config.schema import StrategyConfig
from fse.config.schema.exits import EXIT_MODES
from fse.config.schema.gates import GATE_IDS
from fse.config.schema.kill_switches import KILL_SWITCH_IDS
from fse.config.schema.patterns import DETECTOR_IDS
from fse.engine.types import NotApplicable
from fse.experiments.runner import STATUS_COMPLETED, STATUS_FAILED, ConfigOutcome, ConfigResult
from fse.skilldocs.drafts import (
    CHOSEN_NAME,
    Rule,
    baseline_rules,
    draft_configs,
    label_rules,
    rule_entry,
    rule_figures,
    with_entry,
)
from tests.fakes.configs import minimal_config_data

BASE = StrategyConfig.model_validate(minimal_config_data())
RULES = baseline_rules()
SESSIONS = (date(2026, 3, 2), date(2026, 3, 3))
NA = NotApplicable()


def _first_number(entry: BaseModel) -> str | None:
    for name, value in entry.model_dump().items():
        if isinstance(value, int | float) and not isinstance(value, bool):
            return name
    return None


def _set(cfg: StrategyConfig, rule: Rule, enabled: bool, offset: int) -> StrategyConfig:
    entry = rule_entry(cfg, rule)
    update: dict[str, Any] = {"enabled": enabled}
    field = _first_number(entry)
    if field is not None and offset:
        update[field] = getattr(entry, field) + offset
    return with_entry(cfg, rule, entry.model_copy(update=update))


@st.composite
def configs(draw: st.DrawFn) -> StrategyConfig:
    cfg = BASE
    for rule in RULES:
        enabled = draw(st.booleans())
        offset = draw(st.sampled_from((0, 0, 1, 2)))
        cfg = _set(cfg, rule, enabled, offset)
    return cfg


def _leaves(value: object, prefix: str = "") -> dict[str, object]:
    if isinstance(value, dict):
        out: dict[str, object] = {}
        for k, v in value.items():
            out.update(_leaves(v, f"{prefix}.{k}" if prefix else str(k)))
        return out
    return {prefix: value}


def _differing(a: object, b: object) -> set[str]:
    la, lb = _leaves(a), _leaves(b)
    return {k for k in la.keys() | lb.keys() if la.get(k) != lb.get(k)}


outcome_plans = st.one_of(
    st.none(),
    st.tuples(
        st.fractions(0, 10, max_denominator=8),
        st.one_of(st.just(NA), st.fractions(-5, 5, max_denominator=8)),
    ),
)


def _outcome(name: str, h: str, plan: tuple[Fraction, Fraction | NotApplicable] | None) -> Any:
    if plan is None:
        return ConfigOutcome(0, name, h, STATUS_FAILED, "failed", None, False)
    metrics = dataclasses.replace(
        summarize([], SESSIONS, MetricsCfg()), trades_per_day=plan[0], expectancy_r=plan[1]
    )
    estimate = PassEstimate(
        seed=1, paths=10, max_days=60, min_sessions=40, sessions=2, truncated_by_run_account=0,
        passed=0, failed=0, unresolved=10, median_days_to_pass=None, p90_days_to_pass=None,
    )  # fmt: skip
    result = ConfigResult("run", SESSIONS, (), metrics, estimate)
    return ConfigOutcome(0, name, h, STATUS_COMPLETED, None, result, False)


@given(baseline=configs(), chosen=configs(), data=st.data())
def test_draft_labels_and_comparison_configs(
    baseline: StrategyConfig, chosen: StrategyConfig, data: st.DataObject
) -> None:
    labeled = label_rules(baseline, chosen)
    expected_paths = (
        [f"patterns.{i}" for i in DETECTOR_IDS]
        + [f"gates.{i}" for i in GATE_IDS]
        + [f"exits.modes.{i}" for i in EXIT_MODES]
        + [f"kill_switches.{i}" for i in KILL_SWITCH_IDS]
    )
    assert [lr.rule.key_path for lr in labeled] == expected_paths

    chosen_dump = chosen.model_dump(mode="json")
    for lr in labeled:
        b = rule_entry(baseline, lr.rule).model_dump(mode="json")
        c = rule_entry(chosen, lr.rule).model_dump(mode="json")
        if not c["enabled"]:
            assert lr.label == "Removed"
            assert lr.changes == ()
            want = b
        elif b["enabled"] and b == c:
            assert lr.label == "Kept"
            assert lr.changes == ()
            want = {**c, "enabled": False}
        else:
            assert lr.label == "Changed"
            assert {ch.key for ch in lr.changes} == _differing(b, c)
            assert lr.changes
            want = b
        assert rule_entry(lr.comparison, lr.rule).model_dump(mode="json") == want
        prefix = f"{lr.rule.key_path}."
        moved = _differing(chosen_dump, lr.comparison.model_dump(mode="json"))
        assert all(k.startswith(prefix) for k in moved), (lr.rule.key_path, moved)

    configs_, names = draft_configs(chosen, labeled)
    hashes = [config_hash(c.cfg) for c in configs_]
    assert configs_[0].name == CHOSEN_NAME
    assert hashes[0] == config_hash(chosen)
    assert len(set(hashes)) == len(hashes)
    by_name = {c.name: h for c, h in zip(configs_, hashes, strict=True)}
    for lr in labeled:
        assert by_name[names[lr.rule.key_path]] == config_hash(lr.comparison)

    plans = {c.name: data.draw(outcome_plans, label=c.name) for c in configs_}
    outcomes = {c.name: _outcome(c.name, by_name[c.name], plans[c.name]) for c in configs_}
    for lr in labeled:
        cmp_name = names[lr.rule.key_path]
        f = rule_figures(outcomes[CHOSEN_NAME], outcomes[cmp_name])
        a, z = plans[CHOSEN_NAME], plans[cmp_name]
        if a is None or z is None:
            assert f.trades_per_session is None
            assert f.expectancy_r is None
            continue
        assert f.trades_per_session == a[0] - z[0]
        if isinstance(a[1], NotApplicable) or isinstance(z[1], NotApplicable):
            assert isinstance(f.expectancy_r, NotApplicable)
        else:
            assert f.expectancy_r == a[1] - z[1]
