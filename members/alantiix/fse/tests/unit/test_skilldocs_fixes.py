"""Unit tests for the one-time Skill_Document fixes (``fse.skilldocs.fixes``).

Every document is a temporary copy under ``tmp_path`` that mirrors the
target lines of the real Skill_Documents, and every secret value is fake.
The real Skill_Documents and any real ``.env`` are never read or written.

**Validates: Requirements 26.4, 26.5, 26.6, 26.8**
"""

from __future__ import annotations

import io
from pathlib import Path, PurePosixPath

import pytest

from fse.secrets.env import EnvView
from fse.skilldocs import FINE_TUNE_DIR, SKILL_ROOT_NAME
from fse.skilldocs.check import EXIT_FAILED, EXIT_PASSED, check_skill_docs
from fse.skilldocs.fixes import (
    SKILL_MD_FIXES,
    CharFix,
    FixError,
    Merge,
    apply_fixes,
    fix_text,
    main,
)

FAKE_ENV = EnvView({"SKYLIT_API_KEY": "fake-skilldocs-fix-key-0001"}, {})

WORKING = f"{FINE_TUNE_DIR}/current_working_SKILL_100226.md"
TASK = f"{FINE_TUNE_DIR}/TASK.md"

# SKILL.md lines 107, 144 and 154, before and after the fixes (Req 26.5, 26.6).
LINE_107 = ("Fresh ? tested ? delivered ? decaying.", "Fresh → tested → delivered → decaying.")
LINE_144 = (
    "3. Tap entry, correct stop, ?3:1, regime-matched, commandment 9-10.",
    "3. Tap entry, correct stop, ≥3:1, regime-matched, commandment 9-10.",
)
_CHAIN_TAIL = " A+ or pass. Cite `asOf`. Do not dump every strike."
LINE_154 = (
    "Chart thesis (ES/NQ) ? converted node ? King / floor / ceiling ? GEX vs VEX control"
    " ? Trinity ? tap count ?" + _CHAIN_TAIL,
    "Chart thesis (ES/NQ) → converted node → King / floor / ceiling → GEX vs VEX control"
    " → Trinity → tap count →" + _CHAIN_TAIL,
)
# Real question marks that stay (SKILL.md lines 59 and 61).
LINE_59 = "5. Where is spot: floor, ceiling, Gatekeeper, or air?"
LINE_61 = "7. VEX + VIX: who is in control?"


def skill_md(*, fixed: bool) -> str:
    """A 154-line SKILL.md with the real target lines, before or after the fixes."""
    lines = [f"Body line {n}: a ? b." for n in range(1, 155)]
    lines[0:5] = ["---", "name: Skylit Academy playbook", "description: >-", "  Use this.", "---"]
    lines[58], lines[60] = LINE_59, LINE_61
    for number, (before, after) in ((107, LINE_107), (144, LINE_144), (154, LINE_154)):
        lines[number - 1] = after if fixed else before
    return "\n".join(lines) + "\n"


# The two stacked blocks of current_working_SKILL_100226.md (lines 1-14).
FIRST_BLOCK = (
    "---\n"
    "name: Skylit Academy playbook\n"
    "description: >-\n"
    "  use this when reading Skylit Heatseeker or Flowseeker, grading a setup, or\n"
    "  proposing an ES/NQ/GC/SL entry, exit, or pass\n"
    "---\n"
)
SECOND_BLOCK = (
    "---\n"
    "name: Skylit Academy playbook\n"
    "description: >-\n"
    "  Use this when reading Skylit Heatseeker or Flowseeker, grading a setup, or\n"
    "  proposing an ES/NQ/GC/SL entry, exit, or pass. Chris's VivCouncilHS knowledge\n"
    "  base wins over public Academy pages when they conflict. Bootcamp session\n"
    "  notes overlay kill switches and risk protocol.\n"
    "---\n"
)
WORKING_BODY = "# Skylit Academy playbook\n\nWhere is spot?\nFresh → tested.\n"
TASK_TEXT = "You are a test copilot. Is this a question? Yes.\n"


def make_root(tmp_path: Path) -> Path:
    root = tmp_path / SKILL_ROOT_NAME
    (root / FINE_TUNE_DIR).mkdir(parents=True)
    (root / "SKILL.md").write_text(skill_md(fixed=False), encoding="utf-8")
    (root / WORKING).write_text(FIRST_BLOCK + SECOND_BLOCK + WORKING_BODY, encoding="utf-8")
    (root / TASK).write_text(TASK_TEXT, encoding="utf-8")
    return root


def read(root: Path, rel: str) -> bytes:
    return (root / rel).read_bytes()


# ---------------------------------------------------------------- the planned fixes


def test_fixes_change_only_the_listed_spans_and_the_check_passes(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    assert check_skill_docs(root, FAKE_ENV).exit_code == EXIT_FAILED  # stacked blocks

    working, skill = apply_fixes(root)

    assert working.path == PurePosixPath(WORKING)
    assert working.merge == Merge(1, 14, 2, 8, ("name", "description"))
    assert skill.merge is None
    assert [fix.line for fix in skill.applied] == [107, 144, 154]
    # The merged block is the second block verbatim; the body is untouched.
    assert read(root, WORKING) == (SECOND_BLOCK + WORKING_BODY).encode("utf-8")
    assert read(root, "SKILL.md") == skill_md(fixed=True).encode("utf-8")
    assert read(root, TASK) == TASK_TEXT.encode("utf-8")
    fixed_lines = read(root, "SKILL.md").decode("utf-8").split("\n")
    assert (fixed_lines[58], fixed_lines[60]) == (LINE_59, LINE_61)
    assert check_skill_docs(root, FAKE_ENV).exit_code == EXIT_PASSED


def test_second_run_changes_and_writes_nothing(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    apply_fixes(root)
    before = {
        rel: (read(root, rel), (root / rel).stat().st_mtime_ns) for rel in ("SKILL.md", WORKING)
    }

    results = apply_fixes(root)

    assert not any(fix.changed for fix in results)
    assert results[1].already_applied == SKILL_MD_FIXES
    after = {
        rel: (read(root, rel), (root / rel).stat().st_mtime_ns) for rel in ("SKILL.md", WORKING)
    }
    assert after == before


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    original = {rel: read(root, rel) for rel in ("SKILL.md", WORKING, TASK)}
    results = apply_fixes(root, write=False)
    assert all(fix.changed for fix in results)
    assert {rel: read(root, rel) for rel in original} == original


def test_unexpected_line_stops_the_run_before_any_write(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    text = skill_md(fixed=False).replace(LINE_107[0], "Fresh ? tested ? delivered → decaying.")
    (root / "SKILL.md").write_text(text, encoding="utf-8")
    working = read(root, WORKING)

    with pytest.raises(FixError, match=r"^SKILL\.md: line 107: expected 3 ' \? ' spans") as info:
        apply_fixes(root)

    assert info.value.exit_code == EXIT_FAILED
    assert read(root, WORKING) == working  # planned first, still not written
    assert read(root, "SKILL.md") == text.encode("utf-8")


def test_main_reports_line_numbers(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    out, err = io.StringIO(), io.StringIO()
    assert main(["--skill-root", str(root)], environ={}, stdout=out, stderr=err) == EXIT_PASSED
    assert out.getvalue().splitlines() == [
        f"{WORKING}: lines 1-14: merged 2 stacked front-matter blocks into one block of "
        "8 lines (keys: name, description)",
        "SKILL.md: line 107: replaced 3 ' ? ' with ' → '",
        "SKILL.md: line 144: replaced 1 '?3:1' with '≥3:1'",
        "SKILL.md: line 154: replaced 6 ' ? ' with ' → '",
    ]
    assert "changed 2 of 2 documents" in err.getvalue()

    out = io.StringIO()
    assert main(["--skill-root", str(root)], environ={}, stdout=out, stderr=err) == EXIT_PASSED
    assert out.getvalue().splitlines()[0] == f"{WORKING}: nothing to fix"


# ---------------------------------------------------------------- fix_text


def test_merge_keeps_every_key_and_takes_the_later_value() -> None:
    text = "---\nname: a\nextra: 1\n---\n\n---\nname: fuller a\ndescription: >-\n  b\n---\nx ? y\n"
    result = fix_text(text)
    assert result.text == "---\nname: fuller a\nextra: 1\ndescription: >-\n  b\n---\nx ? y\n"
    assert result.merge == Merge(1, 10, 2, 6, ("name", "extra", "description"))
    assert fix_text(result.text).text == result.text
    assert not fix_text(result.text).changed


@pytest.mark.parametrize(
    ("text", "fixes", "message"),
    [
        ("---\nname: a ? b\n---\n", (CharFix(2, " ? ", " → ", 1),), "inside the front matter"),
        ("x ? y ? z\n", (CharFix(1, " ? ", " → ", 1),), "expected 1"),
        ("x\n", (CharFix(5, " ? ", " → ", 1),), "has only 2 lines"),
        ("---\nname: a\n---\n---\nname: b\n", (), "never closes"),
        ("---\nname: a\n---\n---\n- b\n---\n", (), "not a YAML mapping"),
        ("---\nname: a\n---\n---\n# note\nname: b\n---\n", (), "text before the first key"),
    ],
)
def test_fix_text_refuses_unexpected_documents(
    text: str, fixes: tuple[CharFix, ...], message: str
) -> None:
    with pytest.raises(FixError, match=message):
        fix_text(text, fixes)


@pytest.mark.parametrize(
    ("old", "new"),
    [("a", "b"), ("??", "→?"), (" ? ", " -> "), (" ? ", "-→ "), (" ? ", " ? ")],
)
def test_char_fix_replaces_only_its_question_mark(old: str, new: str) -> None:
    with pytest.raises(ValueError, match="CharFix"):
        CharFix(1, old, new, 1)
