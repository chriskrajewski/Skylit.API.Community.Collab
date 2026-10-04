"""Smoke tests for the fixed Skill_Documents (task 6.5, design "Smoke tests").

These tests read the real Skill_Documents in the sibling member project
``members/alantiix/skylit-academy-playbook-skill/``, read-only. Secret values
are the suite's fake values; the Project ``.env`` is never read (the command
runs with ``project_dir=None`` and an explicit ``--skill-root``).

No assertion shows document text: :func:`_line_facts` reduces each line to
counts and booleans before anything is compared, and the checker's failures
hold only check names, fixed reasons and line numbers.

**Validates: Requirements 26.1, 26.5, 26.6, 26.7**
"""

from __future__ import annotations

import io
from pathlib import Path, PurePosixPath
from typing import Final

from fse import cli
from fse.secrets.env import EnvView
from fse.skilldocs import (
    FINE_TUNE_DIR,
    SKILL_FILE,
    TASK_FILE,
    WORKING_SKILL_FILE,
    default_skill_root,
)
from fse.skilldocs.check import EXIT_PASSED, check_skill_docs
from tests.conftest import FakeEnv

PROJECT_DIR: Final = Path(__file__).resolve().parents[2]
SKILL_ROOT: Final = default_skill_root(PROJECT_DIR)

SKILL_DOCUMENTS: Final = (
    PurePosixPath(SKILL_FILE),
    PurePosixPath(FINE_TUNE_DIR) / WORKING_SKILL_FILE,
    PurePosixPath(FINE_TUNE_DIR) / TASK_FILE,
)

ARROW: Final = " \u2192 "  # " → "
FRESH_CHAIN: Final = "Fresh \u2192 tested \u2192 delivered \u2192 decaying"  # Req 26.5
AT_LEAST_3_TO_1: Final = "\u22653:1"  # "≥3:1", Req 26.6


def _line_facts(path: Path, numbers: tuple[int, ...]) -> dict[int, dict[str, int | bool]]:
    """Counts and booleans for the given 1-based lines; never the line text."""
    lines = path.read_bytes().decode("utf-8").split("\n")
    facts: dict[int, dict[str, int | bool]] = {}
    for number in numbers:
        line = lines[number - 1] if number <= len(lines) else ""
        facts[number] = {
            "fresh_chain": FRESH_CHAIN in line,
            "arrows": line.count(ARROW),
            "spaced_question_marks": line.count(" ? "),
            "question_marks": line.count("?"),
            "at_least_3_to_1": line.count(AT_LEAST_3_TO_1),
            "question_3_to_1": line.count("?3:1"),
            "how_to_speak_a_read": "how to speak a read" in line.lower(),
        }
    return facts


def test_skill_root_holds_the_three_skill_documents() -> None:
    assert SKILL_ROOT.is_dir(), "the sibling skylit-academy-playbook-skill folder is missing"
    missing = [str(p) for p in SKILL_DOCUMENTS if not (SKILL_ROOT / p).is_file()]
    assert missing == []


def test_check_passes_on_the_three_skill_documents(fake_secrets: FakeEnv) -> None:
    result = check_skill_docs(SKILL_ROOT, EnvView(dict(fake_secrets.values), {}))
    assert result.secret_values_configured  # no-secret searched for the fake values
    outcomes = {d.path: (d.failures, d.unreadable) for d in result.documents}
    assert {p: outcomes.get(p) for p in SKILL_DOCUMENTS} == {p: ((), None) for p in SKILL_DOCUMENTS}


def test_fse_skilldocs_check_exits_0_on_the_real_documents(fake_secrets: FakeEnv) -> None:
    out, err = io.StringIO(), io.StringIO()
    code = cli.run(
        ["skilldocs", "check", "--skill-root", str(SKILL_ROOT)],
        project_dir=None,  # no Project .env: only the fake values below
        environ=dict(fake_secrets.values),
        stdout=out,
        stderr=err,
    )
    # stdout lists failed checks as "<path>: <check>: <reason>", never content.
    assert (code, out.getvalue()) == (EXIT_PASSED, "")
    assert "pass every check" in err.getvalue()
    for value in fake_secrets.secret_values():
        assert value not in out.getvalue() + err.getvalue()


def test_skill_md_shows_the_fixed_strings_at_lines_107_144_and_154() -> None:
    facts = _line_facts(SKILL_ROOT / SKILL_FILE, (107, 144, 152, 154))
    # Line 107: "Fresh → tested → delivered → decaying", no `?` left (Req 26.5).
    assert facts[107]["fresh_chain"]
    assert (facts[107]["arrows"], facts[107]["question_marks"]) == (3, 0)
    # Line 144: "≥3:1" in place of "?3:1" (Req 26.6).
    assert (facts[144]["at_least_3_to_1"], facts[144]["question_3_to_1"]) == (1, 0)
    # Line 154: the "How to speak a read" chain (heading on line 152) uses `→` (Req 26.5).
    assert facts[152]["how_to_speak_a_read"]
    assert (facts[154]["arrows"], facts[154]["spaced_question_marks"]) == (6, 0)


def test_skill_md_keeps_its_real_question_marks() -> None:
    facts = _line_facts(SKILL_ROOT / SKILL_FILE, (59, 61))
    assert [facts[n]["question_marks"] for n in (59, 61)] == [1, 1]
