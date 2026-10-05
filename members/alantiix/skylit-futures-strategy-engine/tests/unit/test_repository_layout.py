"""Smoke tests for the repository layout (task 37.2, design "Smoke tests").

Static content only: the Project README, ``LICENSE``, ``.env.example``,
``.gitignore``, the member page row in ``members/alantiix/README.md``,
``docs/account-rules.md``, the Narrator prompt lint and the ``fse`` command
line. Every file is read from the working tree, read-only; the Project
``.env`` is never read.

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.12, 1.17, 15.19, 15.20,
24.7, 24.28, 26.13, 26.14**
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import shlex
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest
from pydantic import BaseModel

from fse import cli
from fse.config.schema import StrategyConfig
from fse.notify.narrator import load_prompt
from fse.projectx.broker import ACCOUNT_VARIABLES
from fse.secrets.env import SECRET_LIST_VARIABLE, SECRET_VARIABLES
from fse.skilldocs.check import draft_failures

PROJECT_DIR: Final = Path(__file__).resolve().parents[2]
MEMBER_DIR: Final = PROJECT_DIR.parent
REPO_ROOT: Final = MEMBER_DIR.parents[1]
PROJECT_NAME: Final = "skylit-futures-strategy-engine"

README: Final = PROJECT_DIR / "README.md"
ACCOUNT_RULES: Final = PROJECT_DIR / "docs" / "account-rules.md"

# Req 1.5: Skylit, every ProjectX credential and account id, the webhook and the
# Narrator: the built-in Secret_Variables plus each broker Order_Mode's account id.
REQUIRED_ENV_NAMES: Final = (*SECRET_VARIABLES, *ACCOUNT_VARIABLES.values())
# Req 1.12: .env, the Data_Cache and every run output holding Snapshots, bars or prints.
REQUIRED_GITIGNORE: Final = (".env", "cache/", "runs/", "recordings/", "live-state/", "logs/")
# Req 1.17: one complete command line for each of these.
DOCUMENTED_COMMANDS: Final = ("pull", "backtest", "report", "paper", "scan-secrets")
README_SECTIONS: Final = (
    "What it does",
    "Skylit surface",
    "Setup",
    "How to run",
    "Environment variables",
    "Account rules",
    "Practice and Combine Order_Modes",
)
# Req 24.7: no option or argument of any command names a credential or account.
CREDENTIAL_OPTION: Final = re.compile(
    r"key|token|secret|password|passwd|credential|webhook|username|account|api",
    re.IGNORECASE,
)
DATE: Final = re.compile(r"\d{4}-\d{2}-\d{2}")


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return " ".join(text.replace("**", "").split())


def _sections(text: str) -> dict[str, str]:
    """``## heading`` → its body, up to the next ``## `` heading."""
    parts = re.split(r"^## (.+)$", text, flags=re.MULTILINE)
    return {parts[i].strip(): parts[i + 1] for i in range(1, len(parts), 2)}


def _table_rows(text: str) -> list[list[str]]:
    """Cells of every Markdown table body row (header and rule rows dropped)."""
    rows: list[list[str]] = []
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if not line.startswith("|"):
            continue
        following = lines[index + 1] if index + 1 < len(lines) else ""
        if re.fullmatch(r"\|[\s:|-]+\|", line) or re.fullmatch(r"\|[\s:|-]+\|", following):
            continue
        rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows


def _code_lines(text: str) -> list[str]:
    """Every line inside a fenced code block."""
    lines: list[str] = []
    for block in re.findall(r"^\s*```[a-z]*\n(.*?)^\s*```", text, flags=re.MULTILINE | re.DOTALL):
        lines.extend(line.strip() for line in block.split("\n") if line.strip())
    return lines


def _slug(heading: str) -> str:
    """GitHub's heading anchor."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def _all_parsers(
    parser: argparse.ArgumentParser, path: str = "fse"
) -> Iterator[tuple[str, argparse.ArgumentParser]]:
    yield path, parser
    for name, sub in _subparsers(parser).items():
        yield from _all_parsers(sub, f"{path} {name}")


def _required_options(parser: argparse.ArgumentParser) -> list[str]:
    return [
        action.option_strings[-1]
        for action in parser._actions
        if action.required and action.option_strings
    ]


# --------------------------------------------------------------------- README


def test_readme_identity_lines_and_sections() -> None:
    text = _text(README)
    for entry in (
        "- Discord username: alantiix",
        f"- Project identifier: {PROJECT_NAME}",
        "- Kind: agent",
    ):
        assert entry in text.split("\n"), entry
    sections = _sections(text)
    missing = [name for name in README_SECTIONS if name not in sections]
    assert missing == []
    what = sections["What it does"]
    assert what.strip()
    assert "Describe the project" not in what


def test_readme_skylit_surface() -> None:
    surface = _sections(_text(README))["Skylit surface"]
    lines = surface.split("\n")
    assert "- Access: REST" in lines
    products = next(line for line in lines if line.startswith("- Products:"))
    assert "Heatseeker" in products
    assert "Flowseeker" in products


def test_readme_setup_steps() -> None:
    setup = _sections(_text(README))["Setup"]
    assert "pip install --require-hashes -r requirements.lock" in setup
    assert "cp .env.example .env" in setup
    assert "SKYLIT_API_KEY" in setup


@pytest.mark.parametrize("command", DOCUMENTED_COMMANDS)
def test_readme_has_one_complete_command_line(command: str) -> None:
    lines = [
        line
        for line in _code_lines(_text(README))
        if line == f"fse {command}" or line.startswith(f"fse {command} ")
    ]
    assert len(lines) == 1, f"{len(lines)} command lines for fse {command}"
    parser = cli.build_parser()
    required = _required_options(_subparsers(parser)[command])
    tokens = shlex.split(re.sub(r"<[^>]+>", "placeholder", lines[0]))
    assert [option for option in required if option not in tokens] == []
    parsed = parser.parse_args(tokens[1:])  # argparse exits on a bad command line
    assert parsed.command == command


def test_readme_documents_every_env_example_variable() -> None:
    rows = {
        row[0].strip("`"): row
        for row in _table_rows(_sections(_text(README))["Environment variables"])
    }
    for name in _env_example_names():
        assert name in rows, name
        row = rows[name]
        assert len(row) == 4, name  # variable, purpose, Secret_Variable, needed by
        assert all(cell for cell in row), name


def test_readme_notices() -> None:
    sections = _sections(_text(README))
    rules = _flat(sections["Account rules"])
    assert "docs/account-rules.md" in rules
    assert re.search(
        r"Operator verifies every account rule value against the \[?Topstep help center\]?"
        r"(\([^)]*\))? before enabling Combine Order_Mode",
        rules,
    )
    modes = _flat(sections["Practice and Combine Order_Modes"])
    assert re.search(
        r"Operator confirms the current TopstepX API terms, including the device and VPN rules,"
        r" before enabling Combine Order_Mode",
        modes,
    )
    assert "Auto OCO Brackets" in modes
    assert "OQ7" in modes


def test_readme_points_to_the_practice_checklist() -> None:
    modes = _sections(_text(README))["Practice and Combine Order_Modes"]
    links = re.findall(r"\]\(([^)#]*design\.md)#([^)]+)\)", modes)
    assert len(links) == 1
    target, anchor = links[0]
    design = (README.parent / target).resolve()
    assert design.is_file()
    headings = re.findall(r"^#+ (.+)$", _text(design), flags=re.MULTILINE)
    checklist = [h for h in headings if h.startswith("Practice checklist before Combine")]
    assert len(checklist) == 1
    assert _slug(checklist[0]) == anchor


# --------------------------------------------------------------------- files


def test_license_has_full_text_and_no_template_placeholder() -> None:
    text = _text(PROJECT_DIR / "LICENSE")
    template = _text(REPO_ROOT / "templates" / "project" / "LICENSE")
    placeholders = [line for line in template.split("\n") if line.strip() and line in text]
    assert placeholders == []
    assert "Replace this file" not in text
    assert len(text) > 1_000


def _env_example_names() -> list[str]:
    names: list[str] = []
    for line in _text(PROJECT_DIR / ".env.example").split("\n"):
        if not line.strip() or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        assert sep == "=", "a non-comment line without '='"
        assert value == "", f"{name} has a value"
        assert re.fullmatch(r"[A-Z][A-Z0-9_]*", name), "a malformed variable name"
        names.append(name)
    return names


def test_env_example_names_with_empty_values() -> None:
    names = _env_example_names()
    assert len(names) == len(set(names))
    assert len(REQUIRED_ENV_NAMES) == 7
    expected = (*REQUIRED_ENV_NAMES, SECRET_LIST_VARIABLE)
    assert [name for name in expected if name not in names] == []


def test_gitignore_entries() -> None:
    entries = {
        line.strip()
        for line in _text(PROJECT_DIR / ".gitignore").split("\n")
        if line.strip() and not line.startswith("#")
    }
    assert [entry for entry in REQUIRED_GITIGNORE if entry not in entries] == []


def test_member_page_has_exactly_one_row() -> None:
    rows = [row for row in _table_rows(_text(MEMBER_DIR / "README.md")) if PROJECT_NAME in row[0]]
    assert len(rows) == 1
    link, kind, description = rows[0]
    assert link == f"[{PROJECT_NAME}]({PROJECT_NAME}/README.md)"
    assert kind == "`agent`"
    assert description
    assert "\n" not in description


# --------------------------------------------------------------- documents


def _model_field(model: type[BaseModel], name: str) -> type[BaseModel] | None:
    """The model class of field ``name`` (by name or alias), or ``None`` if not a model."""
    for field, info in model.model_fields.items():
        if name in (field, info.alias):
            annotation = info.annotation
            return (
                annotation
                if isinstance(annotation, type) and issubclass(annotation, BaseModel)
                else None
            )
    raise AssertionError(f"no Strategy_Config key {name!r}")


def test_account_rules_fields() -> None:
    text = _text(ACCOUNT_RULES)
    header = next(line for line in text.split("\n") if line.startswith("| Rule |"))
    assert [cell.strip() for cell in header.strip("|").split("|")] == [
        "Rule",
        "Config key",
        "Default",
        "Source",
        "Date checked (YYYY-MM-DD)",
    ]
    defaults = _sections(text)["Default values"]
    rows = _table_rows(defaults)
    assert len(rows) >= 8
    for rule, key, default, source, checked in rows:
        assert rule
        assert key
        assert default
        assert source
        if checked:
            assert DATE.fullmatch(checked), rule
            dt.date.fromisoformat(checked)
        path = key.strip("`")
        if path.startswith("account."):
            model: type[BaseModel] | None = StrategyConfig
            for part in path.split("."):
                assert model is not None, rule
                model = _model_field(model, part)
    statement = (
        r"Operator verifies every account rule value (below )?against the \[?Topstep help center\]?"
        r"(\([^)]*\))? before enabling Combine Order_Mode"
    )
    assert re.search(statement, _flat(text))


def test_narrator_prompt_passes_the_draft_lint() -> None:
    prompt = load_prompt()
    assert prompt is not None
    assert draft_failures(prompt) == ()


# --------------------------------------------------------------------- CLI


def test_cli_has_no_credential_arguments() -> None:
    offending: list[str] = []
    for path, parser in _all_parsers(cli.build_parser()):
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                continue
            names = (*action.option_strings, action.dest, str(action.metavar or ""))
            if any(CREDENTIAL_OPTION.search(name) for name in names):
                offending.append(f"{path} {action.option_strings or action.dest}")
    assert offending == []
