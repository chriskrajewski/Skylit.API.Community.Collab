"""Unit tests for ``fse.secrets.env``: precedence, Secret_Variable list, isolation.

Every test reads a ``.env`` written to ``tmp_path`` and uses fake values only.
No test points ``load_env`` at the Project folder.

**Validates: Requirements 1.6**
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from fse.secrets.env import (
    SECRET_LIST_VARIABLE,
    SECRET_VARIABLES,
    EnvFileError,
    EnvView,
    is_blank,
    load_env,
    parse_secret_list,
    resolve_value,
)
from tests.conftest import FakeEnv

PROJECT_DIR = Path(__file__).resolve().parents[2]


def _write_env(folder: Path, text: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ".env"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _never_the_project_folder(tmp_path: Path) -> None:
    # Guard for this module: tmp_path must not sit inside the Project folder,
    # so no test here can read the Operator's real .env.
    assert not tmp_path.resolve().is_relative_to(PROJECT_DIR)


# ---------------------------------------------------------------- precedence


@pytest.mark.parametrize(
    ("shell", "dotenv", "expected"),
    [
        ("fake-shell-0000", "fake-dotenv-0000", "fake-shell-0000"),
        ("fake-shell-0000", None, "fake-shell-0000"),
        (None, "fake-dotenv-0000", "fake-dotenv-0000"),
        ("", "fake-dotenv-0000", "fake-dotenv-0000"),
        (" \t ", "fake-dotenv-0000", "fake-dotenv-0000"),
        ("", "  ", None),
        (None, "", None),
        (None, None, None),
        ("  fake-padded-0000 ", "fake-dotenv-0000", "  fake-padded-0000 "),
    ],
)
def test_resolve_value_precedence(
    shell: str | None, dotenv: str | None, expected: str | None
) -> None:
    assert resolve_value(shell, dotenv) == expected


@pytest.mark.parametrize("value", [None, "", " ", "\t\n", "\u00a0"])
def test_is_blank_true(value: str | None) -> None:
    assert is_blank(value)


def test_is_blank_false_for_text() -> None:
    assert not is_blank(" x ")


def test_load_env_non_blank_shell_wins(tmp_path: Path) -> None:
    _write_env(tmp_path, "SKYLIT_API_KEY=fake-dotenv-key-0000\n")
    env = load_env(tmp_path, environ={"SKYLIT_API_KEY": "fake-shell-key-0000"})
    assert env.get("SKYLIT_API_KEY") == "fake-shell-key-0000"


@pytest.mark.parametrize("shell", ["", "   "])
def test_load_env_blank_shell_falls_back_to_dotenv(tmp_path: Path, shell: str) -> None:
    _write_env(tmp_path, "SKYLIT_API_KEY=fake-dotenv-key-0000\n")
    env = load_env(tmp_path, environ={"SKYLIT_API_KEY": shell})
    assert env.get("SKYLIT_API_KEY") == "fake-dotenv-key-0000"


def test_load_env_unset_shell_uses_dotenv(tmp_path: Path) -> None:
    _write_env(tmp_path, "PROJECTX_API_KEY=fake-projectx-key-0000\n")
    env = load_env(tmp_path, environ={})
    assert env.get("PROJECTX_API_KEY") == "fake-projectx-key-0000"


@pytest.mark.parametrize("line", ["SKYLIT_API_KEY=", "SKYLIT_API_KEY=   ", "SKYLIT_API_KEY"])
def test_load_env_blank_in_both_is_none(tmp_path: Path, line: str) -> None:
    _write_env(tmp_path, line + "\n")
    env = load_env(tmp_path, environ={"SKYLIT_API_KEY": " "})
    assert env.get("SKYLIT_API_KEY") is None


def test_load_env_missing_file_uses_shell_only(tmp_path: Path) -> None:
    env = load_env(tmp_path, environ={"NARRATOR_API_KEY": "fake-narrator-key-0000"})
    assert env.get("NARRATOR_API_KEY") == "fake-narrator-key-0000"
    assert env.get("SKYLIT_API_KEY") is None


def test_load_env_parses_comments_quotes_and_export(tmp_path: Path) -> None:
    _write_env(
        tmp_path,
        "# a comment\n"
        'SKYLIT_API_KEY="fake-quoted-key-0000"\n'
        "export PROJECTX_USERNAME=fake-user-0000  # trailing comment\n"
        "NARRATOR_API_KEY='fake-single-0000'\n",
    )
    env = load_env(tmp_path, environ={})
    assert env.get("SKYLIT_API_KEY") == "fake-quoted-key-0000"
    assert env.get("PROJECTX_USERNAME") == "fake-user-0000"
    assert env.get("NARRATOR_API_KEY") == "fake-single-0000"


def test_load_env_does_not_interpolate(tmp_path: Path) -> None:
    _write_env(tmp_path, "SKYLIT_API_KEY=fake-${HOME}-0000\n")
    env = load_env(tmp_path, environ={"HOME": "/fake/home"})
    assert env.get("SKYLIT_API_KEY") == "fake-${HOME}-0000"


def test_load_env_reads_only_the_given_folder(tmp_path: Path) -> None:
    _write_env(tmp_path, "SKYLIT_API_KEY=fake-parent-key-0000\n")
    child = tmp_path / "project"
    child.mkdir()
    env = load_env(child, environ={})
    assert env.get("SKYLIT_API_KEY") is None


def test_load_env_defaults_to_os_environ(tmp_path: Path, fake_secrets: FakeEnv) -> None:
    env = load_env(tmp_path)
    for name, value in fake_secrets.values.items():
        assert env.get(name) == value
    assert env.secret_values() == fake_secrets.secret_values()


def test_load_env_is_a_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_env(tmp_path, "PROJECTX_API_KEY=fake-before-0000\n")
    monkeypatch.setenv("SKYLIT_API_KEY", "fake-before-key-0000")
    env = load_env(tmp_path)
    monkeypatch.setenv("SKYLIT_API_KEY", "fake-after-key-0000")
    path.write_text("PROJECTX_API_KEY=fake-after-0000\n", encoding="utf-8")
    assert env.get("SKYLIT_API_KEY") == "fake-before-key-0000"
    assert env.get("PROJECTX_API_KEY") == "fake-before-0000"


def test_load_env_does_not_modify_os_environ(tmp_path: Path) -> None:
    _write_env(tmp_path, "SKYLIT_API_KEY=fake-dotenv-key-0000\n")
    before = dict(os.environ)
    load_env(tmp_path)
    assert dict(os.environ) == before


def test_unreadable_env_file_names_path_only(tmp_path: Path) -> None:
    (tmp_path / ".env").mkdir()
    with pytest.raises(EnvFileError) as info:
        load_env(tmp_path, environ={})
    assert str(info.value) == f"cannot read {tmp_path / '.env'}"
    assert info.value.__cause__ is None


def test_non_utf8_env_file_hides_content(tmp_path: Path) -> None:
    (tmp_path / ".env").write_bytes(b"SKYLIT_API_KEY=fake-\xff-0000\n")
    with pytest.raises(EnvFileError) as info:
        load_env(tmp_path, environ={})
    assert "fake" not in str(info.value)
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__


# ---------------------------------------------------------------- Secret_Variables


def test_builtin_secret_variables() -> None:
    assert SECRET_VARIABLES == (
        "SKYLIT_API_KEY",
        "PROJECTX_USERNAME",
        "PROJECTX_API_KEY",
        "NOTIFIER_WEBHOOK_URL",
        "NARRATOR_API_KEY",
    )
    assert SECRET_LIST_VARIABLE == "FSE_SECRET_VARS"


def test_account_ids_are_not_secret_unless_listed(tmp_path: Path) -> None:
    _write_env(
        tmp_path,
        "SKYLIT_API_KEY=fake-skylit-key-0000\n"
        "PRACTICE_ACCOUNT_ID=FAKE-ACCOUNT-1\n"
        "COMBINE_ACCOUNT_ID=FAKE-ACCOUNT-2\n"
        "FSE_SECRET_VARS=\n",
    )
    env = load_env(tmp_path, environ={})
    assert env.secret_names() == SECRET_VARIABLES
    assert env.secret_values() == ["fake-skylit-key-0000"]
    assert env.get("PRACTICE_ACCOUNT_ID") == "FAKE-ACCOUNT-1"


def test_fse_secret_vars_adds_names_from_dotenv(tmp_path: Path) -> None:
    _write_env(
        tmp_path,
        "PRACTICE_ACCOUNT_ID=FAKE-ACCOUNT-1\n"
        "COMBINE_ACCOUNT_ID=FAKE-ACCOUNT-2\n"
        "FSE_SECRET_VARS= PRACTICE_ACCOUNT_ID , ,COMBINE_ACCOUNT_ID,\n",
    )
    env = load_env(tmp_path, environ={})
    assert env.secret_names() == (*SECRET_VARIABLES, "PRACTICE_ACCOUNT_ID", "COMBINE_ACCOUNT_ID")
    assert env.secret_values() == ["FAKE-ACCOUNT-1", "FAKE-ACCOUNT-2"]


def test_fse_secret_vars_follows_precedence(tmp_path: Path) -> None:
    _write_env(
        tmp_path,
        "PRACTICE_ACCOUNT_ID=FAKE-ACCOUNT-1\n"
        "COMBINE_ACCOUNT_ID=FAKE-ACCOUNT-2\n"
        "FSE_SECRET_VARS=PRACTICE_ACCOUNT_ID\n",
    )
    env = load_env(tmp_path, environ={"FSE_SECRET_VARS": "COMBINE_ACCOUNT_ID"})
    assert env.secret_values() == ["FAKE-ACCOUNT-2"]


def test_secret_values_skip_blanks_and_repeats(tmp_path: Path) -> None:
    _write_env(
        tmp_path,
        "SKYLIT_API_KEY=fake-shared-0000\n"
        "PROJECTX_API_KEY=fake-shared-0000\n"
        "NARRATOR_API_KEY=   \n"
        "FSE_SECRET_VARS=SKYLIT_API_KEY,EXTRA_TOKEN,UNSET_NAME\n"
        "EXTRA_TOKEN=fake-extra-0000\n",
    )
    env = load_env(tmp_path, environ={"PROJECTX_USERNAME": "fake-user-0000"})
    assert env.secret_names() == (*SECRET_VARIABLES, "EXTRA_TOKEN", "UNSET_NAME")
    assert env.secret_values() == ["fake-shared-0000", "fake-user-0000", "fake-extra-0000"]


def test_no_secret_values_when_all_blank(tmp_path: Path) -> None:
    env = load_env(tmp_path, environ={})
    assert env.secret_values() == []


def test_parse_secret_list() -> None:
    assert parse_secret_list(None) == ()
    assert parse_secret_list(" , ") == ()
    assert parse_secret_list("A, B,A ,C") == ("A", "B", "C")


def test_repr_shows_no_values() -> None:
    view = EnvView({"SKYLIT_API_KEY": "fake-skylit-key-0000"}, {"OTHER": "fake-other-0000"})
    assert "fake" not in repr(view)
    assert "fake" not in str(view)
