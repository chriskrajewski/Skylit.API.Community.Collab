"""Unit tests for the Secret_Scanner (``fse.secrets.scanner``).

Every repository here is a temporary git repository under ``tmp_path`` and
every value is fake. The real repository and any real ``.env`` are never read.

**Validates: Requirements 1.13, 1.14, 1.15, 1.16**
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from fse.secrets import scanner
from fse.secrets.env import EnvView
from fse.secrets.scanner import (
    EXIT_CANNOT_RUN,
    EXIT_CLEAN,
    EXIT_MATCHES,
    GitListingError,
    NoSecretValuesError,
    find_repo_root,
    list_index_paths,
    scan,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

FAKE_KEY = "fake-scanner-key-0001"
FAKE_HOOK = "https://webhook.invalid/fake-scanner-hook-0002"


@pytest.fixture(autouse=True)
def _isolated_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the Operator's git config, global excludes and GIT_* overrides out."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(name, raising=False)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "scanner@example.invalid")
    _git(root, "config", "user.name", "scanner test")
    return root.resolve()


def _env(**values: str) -> EnvView:
    return EnvView(values, {})


def _write(repo: Path, rel: str, data: str | bytes) -> Path:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)
    return path


def _commit_all(repo: Path) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fixture")


# ---------------------------------------------------------------- matching


def test_clean_repo_exits_zero(repo: Path) -> None:
    _write(repo, "README.md", "nothing secret here\n")
    _write(repo, "src/app.py", "KEY = os.environ['SKYLIT_API_KEY']\n")
    _commit_all(repo)
    result = scan(repo, _env(SKYLIT_API_KEY=FAKE_KEY))
    assert result.matches == ()
    assert result.searched == 2
    assert result.exit_code == EXIT_CLEAN


def test_reports_each_matching_file_once(repo: Path) -> None:
    _write(repo, "a.txt", f"key={FAKE_KEY}\nagain {FAKE_KEY}\n")
    _write(repo, "dir/b.json", f'{{"hook": "{FAKE_HOOK}"}}')
    _write(repo, "c.txt", "clean\n")
    _commit_all(repo)
    env = _env(SKYLIT_API_KEY=FAKE_KEY, NOTIFIER_WEBHOOK_URL=FAKE_HOOK)
    result = scan(repo, env)
    assert result.matches == ("a.txt", "dir/b.json")
    assert result.exit_code == EXIT_MATCHES


def test_newly_staged_file_is_searched(repo: Path) -> None:
    _write(repo, "base.txt", "clean\n")
    _commit_all(repo)
    _write(repo, "staged.txt", FAKE_KEY)
    _git(repo, "add", "staged.txt")
    assert scan(repo, _env(SKYLIT_API_KEY=FAKE_KEY)).matches == ("staged.txt",)


def test_working_tree_content_is_searched_not_the_index(repo: Path) -> None:
    _write(repo, "edited.txt", "clean\n")
    _write(repo, "cleaned.txt", FAKE_KEY)
    _commit_all(repo)
    _write(repo, "edited.txt", f"now holds {FAKE_KEY}\n")  # unstaged edit
    _write(repo, "cleaned.txt", "value removed\n")
    assert scan(repo, _env(SKYLIT_API_KEY=FAKE_KEY)).matches == ("edited.txt",)


def test_untracked_and_ignored_files_are_not_searched(repo: Path) -> None:
    _write(repo, ".gitignore", ".env\n")
    _commit_all(repo)
    _write(repo, ".env", f"SKYLIT_API_KEY={FAKE_KEY}\n")  # fake value, ignored
    _write(repo, "untracked.txt", FAKE_KEY)
    result = scan(repo, _env(SKYLIT_API_KEY=FAKE_KEY))
    assert result.matches == ()
    assert result.searched == 1


def test_operator_listed_variable_is_searched(repo: Path) -> None:
    _write(repo, "ids.txt", "account FAKE-ACCOUNT-9\n")
    _commit_all(repo)
    plain = _env(PRACTICE_ACCOUNT_ID="FAKE-ACCOUNT-9")
    listed = _env(PRACTICE_ACCOUNT_ID="FAKE-ACCOUNT-9", FSE_SECRET_VARS="PRACTICE_ACCOUNT_ID")
    with pytest.raises(NoSecretValuesError):
        scan(repo, plain)  # an account id is a Secret_Variable only when listed
    assert scan(repo, listed).matches == ("ids.txt",)


def test_value_across_a_chunk_edge_is_found(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scanner, "_CHUNK_SIZE", 8)
    _write(repo, "edge.bin", b"x" * 13 + FAKE_KEY.encode() + b"y" * 5)
    _write(repo, "near.bin", b"x" * 13 + FAKE_KEY[:-1].encode() + b"y" * 5)
    _commit_all(repo)
    assert scan(repo, _env(SKYLIT_API_KEY=FAKE_KEY)).matches == ("edge.bin",)


def test_non_ascii_value_matches_its_utf8_bytes(repo: Path) -> None:
    value = "fake-clé-0003"
    _write(repo, "utf8.txt", f"x {value} y")
    _write(repo, "latin1.txt", f"x {value} y".encode("latin-1"))
    _commit_all(repo)
    assert scan(repo, _env(SKYLIT_API_KEY=value)).matches == ("utf8.txt",)


def test_path_with_spaces_and_unicode(repo: Path) -> None:
    _write(repo, "dír/with space.txt", FAKE_KEY)
    _commit_all(repo)
    assert scan(repo, _env(SKYLIT_API_KEY=FAKE_KEY)).matches == ("dír/with space.txt",)


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks")
def test_symlink_is_searched_by_link_text_and_never_followed(repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text(FAKE_KEY, encoding="utf-8")
    (repo / "link-out").symlink_to(outside)
    (repo / f"link-{FAKE_KEY}").symlink_to("missing-target")
    _commit_all(repo)
    result = scan(repo, _env(SKYLIT_API_KEY=FAKE_KEY))
    assert result.matches == ()  # neither link's text is the value itself
    named = _env(SKYLIT_API_KEY="missing-target")
    assert scan(repo, named).matches == (f"link-{FAKE_KEY}",)


def test_deleted_file_is_skipped_not_matched(repo: Path) -> None:
    _write(repo, "gone.txt", FAKE_KEY)
    _write(repo, "kept.txt", "clean\n")
    _commit_all(repo)
    (repo / "gone.txt").unlink()
    result = scan(repo, _env(SKYLIT_API_KEY=FAKE_KEY))
    assert result.matches == ()
    assert result.skipped == ("gone.txt",)
    assert result.searched == 1
    assert result.exit_code == EXIT_CLEAN


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="needs a non-root POSIX user")
def test_unreadable_file_makes_the_scan_incomplete(repo: Path) -> None:
    locked = _write(repo, "locked.txt", "clean\n")
    _write(repo, "ok.txt", "clean\n")
    _commit_all(repo)
    locked.chmod(0)
    try:
        result = scan(repo, _env(SKYLIT_API_KEY=FAKE_KEY))
    finally:
        locked.chmod(0o600)
    assert result.unreadable == ("locked.txt",)
    assert not result.complete
    assert result.exit_code == EXIT_CANNOT_RUN


def test_index_paths_are_listed_once_from_the_root(repo: Path) -> None:
    _write(repo, "sub/inner.txt", "x")
    _write(repo, "top.txt", "y")
    _commit_all(repo)
    assert list_index_paths(repo) == [b"sub/inner.txt", b"top.txt"]
    assert find_repo_root(repo / "sub") == repo
    assert find_repo_root(repo / "sub" / "inner.txt") == repo


# ---------------------------------------------------------------- cannot run


def test_no_secret_values_is_exit_5_and_names_the_variables(repo: Path) -> None:
    _write(repo, "a.txt", "x")
    _commit_all(repo)
    with pytest.raises(NoSecretValuesError) as info:
        scan(repo, _env(SKYLIT_API_KEY="   ", OTHER="not a secret"))
    assert info.value.exit_code == EXIT_CANNOT_RUN
    assert "no secret values configured" in str(info.value)
    assert "SKYLIT_API_KEY" in str(info.value)


def test_outside_a_repository_is_exit_5(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(GitListingError) as info:
        find_repo_root(plain)
    assert info.value.exit_code == EXIT_CANNOT_RUN
    assert "cannot list the repository's tracked files" in str(info.value)


def test_missing_start_directory_is_exit_5(tmp_path: Path) -> None:
    with pytest.raises(GitListingError, match="does not exist"):
        find_repo_root(tmp_path / "nowhere")


def test_git_not_on_path_is_exit_5(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(repo / "no-bin"))
    with pytest.raises(GitListingError, match="git is not installed"):
        scan(repo, _env(SKYLIT_API_KEY=FAKE_KEY))
