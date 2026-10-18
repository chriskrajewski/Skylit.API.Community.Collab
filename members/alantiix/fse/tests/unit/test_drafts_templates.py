"""Smoke test for the Revised_Draft templates (task 30.5, Req 26.13, 26.14, 26.16).

Both templates (``revised_SKILL.md``, ``revised_TASK.md``) pass the full
check with the draft lint as written, hold the Narrator restatement rule and
the engine-decides rule each on one line, and still pass once filled with
the Unmeasured rows of a rules table. The lint
itself fails a forecast word next to a figure and a trade instruction aimed
at the Narrator.
"""

from __future__ import annotations

from importlib import resources

import pytest

from fse.skilldocs.check import (
    ENGINE_DECIDES_RULE,
    RESTATEMENT_RULE,
    Check,
    check_document,
    draft_failures,
)
from fse.skilldocs.drafts import parse_unmeasured, render_draft

TEMPLATES = ("revised_SKILL.md", "revised_TASK.md")
FIGURE = (
    "- `gates.midpoint` (Gate): Kept. Change in mean trades per session: +0.1000; change in "
    "expectancy: -0.0500 R per trade. Chosen: 40 trades, 20 sessions, 2026-01-02 to 2026-02-27 "
    "(pre-holdout), config hash 0123456789abcdef; comparison: 45 trades, 20 sessions, "
    "2026-01-02 to 2026-02-27 (pre-holdout), config hash fedcba9876543210."
)


RULES_TABLE = """# Rules table

## Rules

| ID | Rule | Source | Codified as |
| --- | --- | --- | --- |
| R01 | A rule a gate codifies. | SKILL | `gates.midpoint` |
| R02 | A rule nothing codifies. | SKILL | not codified: judgment call |
| R03 | Another rule nothing codifies. | TASK | not codified: needs live context |
"""
"""A synthetic rules table in the format ``docs/rules_table.md`` has."""


def _template(name: str) -> bytes:
    return resources.files("fse.skilldocs").joinpath("templates", name).read_bytes()


@pytest.mark.parametrize("name", TEMPLATES)
def test_template_passes_the_lint_and_holds_both_rules(name: str) -> None:
    data = _template(name)
    assert check_document(data, front_matter_required=True, draft=True) == ()
    lines = data.decode("utf-8").split("\n")
    assert RESTATEMENT_RULE in lines
    assert ENGINE_DECIDES_RULE in lines


@pytest.mark.parametrize("kind", ["SKILL", "TASK"])
def test_filled_template_passes_the_lint(kind: str) -> None:
    unmeasured = parse_unmeasured(RULES_TABLE)
    assert len(unmeasured) == 2
    text = render_draft(
        kind,  # type: ignore[arg-type]
        {
            "name": '"Skill"',
            "description": '"Narrator rules."',
            "draft_date": "2026-10-05",
            "measurement": "Chosen Strategy_Config: `chosen.yaml`.",
            "holdout": "- Holdout_Period evaluation shown here.",
            "rules": FIGURE,
            "other": "None.",
            "unmeasured": "\n".join(
                f"- {u.rule_id} ({u.source}): Unmeasured. Reason: {u.reason}" for u in unmeasured
            ),
        },
    )
    assert check_document(text.encode("utf-8"), front_matter_required=True, draft=True) == ()


def test_lint_fails_forecasts_and_trade_instructions() -> None:
    rules = f"{RESTATEMENT_RULE}\n{ENGINE_DECIDES_RULE}\n"
    assert draft_failures(rules + FIGURE) == ()
    forecast = FIGURE.replace("Kept.", "Kept, and the win rate will hold.")
    assert [f.check for f in draft_failures(rules + forecast)] == [Check.NO_FORECAST]
    for line in ("Take the long at the floor.", "The Narrator should cancel the order."):
        assert [f.check for f in draft_failures(rules + line)] == [Check.NO_TRADE_INSTRUCTION]
    assert [f.check for f in draft_failures(FIGURE)] == [Check.NARRATOR_RULES]
