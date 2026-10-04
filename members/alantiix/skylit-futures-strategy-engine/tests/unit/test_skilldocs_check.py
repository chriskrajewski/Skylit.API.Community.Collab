"""Unit tests for ``fse skilldocs check`` (``fse.skilldocs.check`` and its command).

Every document is a temporary file under ``tmp_path`` and every secret value
is fake. The real Skill_Documents and any real ``.env`` are never read.

**Validates: Requirements 26.1, 26.2, 26.3, 26.7, 26.17**
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pytest

from fse import cli
from fse.commands import skilldocs as skilldocs_command
from fse.secrets.env import EnvView
from fse.skilldocs import FINE_TUNE_DIR, SKILL_ROOT_NAME
from fse.skilldocs.check import (
    EXIT_FAILED,
    EXIT_PASSED,
    EXIT_UNREADABLE,
    Check,
    SkillDocsError,
    check_document,
    check_skill_docs,
)

FAKE_KEY = "fake-skilldocs-key-0001"
BODY_TOKEN = "body-token-do-not-print"

GOOD = (
    "---\n"
    "name: Example skill\n"
    "description: >-\n"
    "  Use this when testing.\n"
    "---\n"
    f"# Body {BODY_TOKEN}\n"
    "A real question? Yes.\n"
)
# Two blocks with one blank line between: the second starts on line 6.
STACKED = "---\nname: a\ndescription: b\n---\n\n---\nname: a\ndescription: fuller b\n---\nbody\n"
TASK = f"You are a test copilot. {BODY_TOKEN}\n"


def failures(
    text: str | bytes, *, required: bool = True, secrets: tuple[str, ...] = ()
) -> list[tuple[Check, str]]:
    data = text.encode("utf-8") if isinstance(text, str) else text
    found = check_document(data, front_matter_required=required, secret_values=secrets)
    return [(f.check, f.reason) for f in found]


# ---------------------------------------------------------------- front matter


def test_one_block_with_name_and_description_passes() -> None:
    assert failures(GOOD) == []
    assert failures(GOOD, required=False) == []
    assert failures(GOOD.replace("\n", "\r\n")) == []


def test_no_front_matter_fails_a_skill_file_and_passes_a_task_file() -> None:
    assert failures(TASK) == [(Check.FRONT_MATTER, "no front-matter block on line 1")]
    assert failures(TASK, required=False) == []
    assert failures("", required=False) == []


def test_stacked_blocks_fail_and_name_the_second_block() -> None:
    reason = "a second `---` block starts on line 6, after the block on lines 1-4"
    assert failures(STACKED) == [(Check.FRONT_MATTER, reason)]
    assert failures(STACKED, required=False) == [(Check.FRONT_MATTER, reason)]


def test_block_not_on_line_1_fails_even_for_a_task_file() -> None:
    reason = "the front-matter block starts on line 3, not line 1"
    assert failures("\n\n" + GOOD) == [(Check.FRONT_MATTER, reason)]
    assert failures("\n\n" + GOOD, required=False) == [(Check.FRONT_MATTER, reason)]


def test_unclosed_block_fails() -> None:
    reason = "the front-matter block on line 1 has no closing `---` line"
    assert failures("---\nname: a\ndescription: b\n") == [(Check.FRONT_MATTER, reason)]


@pytest.mark.parametrize(
    ("block", "reason"),
    [
        (f"name: {BODY_TOKEN}\ndescription: b: c", "the front matter is not valid YAML (line 3)"),
        (f"- {BODY_TOKEN}\n- b", "the front matter is a list, not a YAML mapping"),
        (BODY_TOKEN, "the front matter is a scalar, not a YAML mapping"),
        ("", "the front matter is empty, not a YAML mapping"),
        ("name: a\ndescription: b\nwhen: 2026-02-30", "the front matter is not valid YAML"),
    ],
)
def test_block_that_is_not_a_mapping_fails_without_quoting_it(block: str, reason: str) -> None:
    found = failures(f"---\n{block}\n---\nbody\n")
    assert found == [(Check.YAML_MAPPING, reason)]
    assert BODY_TOKEN not in found[0][1]


@pytest.mark.parametrize(
    ("block", "reasons"),
    [
        ("description: b", ["the front matter has no `name` key"]),
        ("name: a\ndescription: '   '", ["`description` is empty"]),
        ("name:\ndescription: b", ["`name` is empty"]),
        (
            "name: 123\ndescription: [b]",
            ["`name` is not a string", "`description` is not a string"],
        ),
        (
            "title: a",
            ["the front matter has no `name` key", "the front matter has no `description` key"],
        ),
    ],
)
def test_name_and_description_must_be_non_empty_strings(block: str, reasons: list[str]) -> None:
    found = failures(f"---\n{block}\n---\nbody\n")
    assert found == [(Check.NAME_DESCRIPTION, reason) for reason in reasons]


# ---------------------------------------------------------------- encoding and secrets


def test_replacement_character_fails_and_names_the_lines() -> None:
    text = GOOD + "Fresh \ufffd tested\nclean\n\ufffd3:1\n"
    assert failures(text) == [(Check.UTF8, "holds U+FFFD on lines 8, 10")]
    many = GOOD + "\ufffd\n" * 12  # lines 8-19
    assert failures(many) == [
        (Check.UTF8, "holds U+FFFD on lines 8, 9, 10, 11, 12, 13, 14, 15, 16, 17 and 2 more")
    ]


def test_invalid_utf8_fails_and_front_matter_is_still_checked() -> None:
    data = GOOD.encode("utf-8") + b"bad \xff byte\n"
    assert failures(data) == [(Check.UTF8, "not valid UTF-8 (first invalid byte on line 8)")]
    stacked = STACKED.encode("utf-8") + b"\xc3\n"
    assert [check for check, _ in failures(stacked)] == [Check.FRONT_MATTER, Check.UTF8]


def test_secret_value_fails_without_printing_it() -> None:
    found = failures(GOOD + f"key: {FAKE_KEY}\n", secrets=(FAKE_KEY,))
    assert found == [(Check.NO_SECRET, "holds a Secret_Scanner value")]
    assert failures(GOOD, secrets=(FAKE_KEY,)) == []
    assert failures(GOOD + FAKE_KEY) == []  # no value configured: nothing to search for


# ---------------------------------------------------------------- documents under a skill root


def _write(root: Path, files: Mapping[str, str | bytes]) -> Path:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        else:
            path.write_bytes(content)
    return root


def _skill_root(tmp_path: Path, **overrides: str | bytes | None) -> Path:
    """A skill root with three passing Skill_Documents; an override of None removes one."""
    files: dict[str, str | bytes | None] = {
        "SKILL.md": GOOD,
        f"{FINE_TUNE_DIR}/current_working_SKILL_100226.md": GOOD,
        f"{FINE_TUNE_DIR}/TASK.md": TASK,
    }
    files.update({k.replace("__", "/"): v for k, v in overrides.items()})
    root = tmp_path / SKILL_ROOT_NAME
    return _write(root, {k: v for k, v in files.items() if v is not None})


def test_checks_the_skill_documents_then_each_revised_draft(tmp_path: Path) -> None:
    root = _skill_root(tmp_path)
    _write(
        root / FINE_TUNE_DIR,
        {
            "revised_SKILL_2026-02-01.md": STACKED,
            "revised_SKILL_2026-01-01.md": GOOD,
            "revised_TASK_2026-01-01.md": TASK,  # task drafts may omit front matter
            "notes.md": STACKED,  # neither a Skill_Document nor a Revised_Draft
        },
    )
    result = check_skill_docs(root, EnvView({"SKYLIT_API_KEY": FAKE_KEY}, {}))
    fine = PurePosixPath(FINE_TUNE_DIR)
    assert [d.path for d in result.documents] == [
        PurePosixPath("SKILL.md"),
        fine / "current_working_SKILL_100226.md",
        fine / "TASK.md",
        fine / "revised_SKILL_2026-01-01.md",
        fine / "revised_SKILL_2026-02-01.md",
        fine / "revised_TASK_2026-01-01.md",
    ]
    assert [d.path.name for d in result.failed] == ["revised_SKILL_2026-02-01.md"]
    assert result.secret_values_configured
    assert result.exit_code == EXIT_FAILED


def test_missing_document_is_unreadable_and_exit_4(tmp_path: Path) -> None:
    root = _skill_root(tmp_path, **{f"{FINE_TUNE_DIR}__TASK.md": None})
    result = check_skill_docs(root, EnvView({}, {}))
    assert [(d.path.name, d.unreadable) for d in result.unreadable] == [
        ("TASK.md", "file not found")
    ]
    assert result.exit_code == EXIT_UNREADABLE
    assert not result.secret_values_configured


def test_missing_skill_root_raises_exit_4(tmp_path: Path) -> None:
    with pytest.raises(SkillDocsError, match="does not exist") as info:
        check_skill_docs(tmp_path / "nowhere", EnvView({}, {}))
    assert info.value.exit_code == EXIT_UNREADABLE


# ---------------------------------------------------------------- the command


@dataclass(frozen=True)
class Outcome:
    code: int
    out: str
    err: str


def fse(*argv: str, project: Path | None, environ: Mapping[str, str] | None = None) -> Outcome:
    out, err = io.StringIO(), io.StringIO()
    code = cli.run(
        list(argv), project_dir=project, environ=dict(environ or {}), stdout=out, stderr=err
    )
    return Outcome(code, out.getvalue(), err.getvalue())


def test_command_is_discovered() -> None:
    assert skilldocs_command.register in cli.discover_commands()


def test_command_prints_paths_and_failed_checks_only(tmp_path: Path) -> None:
    root = _skill_root(
        tmp_path,
        **{
            "SKILL.md": GOOD + f"Fresh \ufffd tested {FAKE_KEY}\n",
            f"{FINE_TUNE_DIR}__current_working_SKILL_100226.md": STACKED,
        },
    )
    environ = {"SKYLIT_API_KEY": FAKE_KEY}
    result = fse("skilldocs", "check", "--skill-root", str(root), project=None, environ=environ)
    assert result.code == EXIT_FAILED
    assert result.out.splitlines() == [
        "SKILL.md: utf-8: holds U+FFFD on line 8",
        "SKILL.md: no-secret: holds a Secret_Scanner value",
        f"{FINE_TUNE_DIR}/current_working_SKILL_100226.md: front-matter: "
        "a second `---` block starts on line 6, after the block on lines 1-4",
    ]
    assert "2 of 3 documents" in result.err
    for text in (result.out, result.err):
        assert FAKE_KEY not in text
        assert BODY_TOKEN not in text


def test_command_defaults_to_the_sibling_skill_root(tmp_path: Path) -> None:
    _skill_root(tmp_path)
    project = tmp_path / "skylit-futures-strategy-engine"
    project.mkdir()  # no .env
    result = fse("skilldocs", "check", project=project, environ={"SKYLIT_API_KEY": FAKE_KEY})
    assert result.code == EXIT_PASSED
    assert result.out == ""
    assert "3 documents" in result.err
    assert "pass every check" in result.err


def test_command_without_project_needs_skill_root() -> None:
    result = fse("skilldocs", "check", project=None, environ={"SKYLIT_API_KEY": FAKE_KEY})
    assert result.code == EXIT_FAILED
    assert "pass --skill-root" in result.err


def test_command_notes_when_no_secret_value_is_configured(tmp_path: Path) -> None:
    root = _skill_root(tmp_path)
    result = fse("skilldocs", "check", "--skill-root", str(root), project=None)
    assert result.code == EXIT_PASSED
    assert "no Secret_Variable value is configured" in result.err
