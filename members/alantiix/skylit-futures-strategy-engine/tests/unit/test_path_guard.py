"""Unit tests for the cache and output path guard.

Every git working tree here is a temporary repository under ``tmp_path``; the
real repository is only read (its Project ``.gitignore``), never written.

**Validates: Requirements 1.10, 1.11**
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from fse.data.path_guard import (
    EXIT_INVALID_INPUT,
    PathGuardError,
    check_output_dir,
    check_output_dirs,
    enforce_output_dirs,
)

PROJECT_DIR = Path(__file__).resolve().parents[2]
PROJECT_REL = Path("members", "alantiix", "skylit-futures-strategy-engine")
DEFAULT_OUTPUT_NAMES = ("cache", "runs", "recordings", "live-state", "logs")

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@pytest.fixture(autouse=True)
def _isolated_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the Operator's git config, global excludes and GIT_* overrides out."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    for name in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_CEILING_DIRECTORIES",
    ):
        monkeypatch.delenv(name, raising=False)


def _git_init(root: Path, gitignore: str) -> Path:
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    (root / ".gitignore").write_text(gitignore, encoding="utf-8")
    return root.resolve()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return _git_init(tmp_path / "repo", "cache/\nruns/\n")


def _tree(root: Path) -> set[Path]:
    """Every path under ``root``, including ``.git``, to prove nothing was written."""
    return {p.relative_to(root) for p in root.rglob("*")}


# ---------------------------------------------------------------- paths that pass


def test_path_outside_any_repo_passes(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere" / "cache"
    assert check_output_dir(target) == target.resolve()
    assert not (tmp_path / "elsewhere").exists()


def test_default_home_dirs_pass(isolated_home: Path) -> None:
    # Req 1.10: the defaults under ~/.skylit-fse/ lie outside every working tree.
    paths = {name: f"~/.skylit-fse/{name}" for name in DEFAULT_OUTPUT_NAMES}
    resolved = check_output_dirs(paths)
    for name in DEFAULT_OUTPUT_NAMES:
        assert resolved[name] == isolated_home.resolve() / ".skylit-fse" / name
        assert resolved[name].is_absolute()
    assert not (isolated_home / ".skylit-fse").exists()


@pytest.mark.parametrize("rel", ["cache", "cache/heatmap/2024", "runs"])
@pytest.mark.parametrize("exists", [False, True])
def test_ignored_dir_inside_repo_passes(repo: Path, rel: str, exists: bool) -> None:
    target = repo / rel
    if exists:
        target.mkdir(parents=True)
    before = _tree(repo)
    assert check_output_dir(target) == target
    assert _tree(repo) == before


@pytest.mark.parametrize("name", DEFAULT_OUTPUT_NAMES)
def test_project_gitignore_covers_default_output_names(tmp_path: Path, name: str) -> None:
    project_gitignore = (PROJECT_DIR / ".gitignore").read_text(encoding="utf-8")
    root = _git_init(tmp_path / "community", "")
    project = root / PROJECT_REL
    project.mkdir(parents=True)
    (project / ".gitignore").write_text(project_gitignore, encoding="utf-8")
    assert check_output_dir(project / name) == project / name
    with pytest.raises(PathGuardError):
        check_output_dir(project / "data")


def test_symlink_inside_repo_to_outside_passes(repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "link").symlink_to(outside, target_is_directory=True)
    assert check_output_dir(repo / "link" / "cache") == outside.resolve() / "cache"


# ---------------------------------------------------------------- paths that are rejected


@pytest.mark.parametrize(
    ("root_ignore", "own_ignore"),
    [
        ("", "*\n"),  # cache/'s own .gitignore never applies to cache/ itself
        ("", "*/\n"),  # ignores sub-directories only: cache/x.bin is committable
        ("*\n!cache/\n!*.bin\n", None),  # cache/ kept, cache/x.bin re-included
        ("cache/*\n!cache/*.bin\n", None),  # cache/ itself is not ignored
    ],
)
def test_dir_git_does_not_ignore_is_rejected(
    tmp_path: Path, root_ignore: str, own_ignore: str | None
) -> None:
    # `git check-ignore cache/` reports each of these as ignored: git also matches
    # the empty name after the slash. `git check-ignore cache` does not.
    repo = _git_init(tmp_path / "repo", root_ignore)
    if own_ignore is not None:
        (repo / "cache").mkdir()
        (repo / "cache" / ".gitignore").write_text(own_ignore, encoding="utf-8")
    before = _tree(repo)
    with pytest.raises(PathGuardError) as info:
        check_output_dir(repo / "cache")
    assert "git does not ignore it" in str(info.value)
    assert _tree(repo) == before


def test_symlinked_ancestor_gitignore_fails_closed(tmp_path: Path) -> None:
    repo = _git_init(tmp_path / "repo", "")
    (repo / "sub").mkdir()
    (tmp_path / "rules").write_text("cache/\n", encoding="utf-8")
    (repo / "sub" / ".gitignore").symlink_to(tmp_path / "rules")
    with pytest.raises(PathGuardError) as info:
        check_output_dir(repo / "sub" / "cache")
    assert "is not a regular file" in str(info.value)


@pytest.mark.parametrize("rel", ["data", "src/out", ".", "cache/../data"])
def test_unignored_dir_inside_repo_is_rejected(repo: Path, rel: str) -> None:
    target = (repo / rel).resolve()
    before = _tree(repo)
    with pytest.raises(PathGuardError) as info:
        check_output_dir(repo / rel, label="cache directory")
    assert info.value.exit_code == EXIT_INVALID_INPUT == 2
    [rejection] = info.value.rejections
    assert rejection.resolved == target
    assert rejection.message.startswith(f"cache directory {target}")
    assert str(repo) in rejection.message
    assert _tree(repo) == before


def test_relative_path_resolves_against_cwd(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(repo)
    assert check_output_dir("cache") == repo / "cache"
    with pytest.raises(PathGuardError) as info:
        check_output_dir("data")
    assert f"{repo / 'data'} (configured as data)" in str(info.value)


def test_symlink_outside_repo_into_repo_is_rejected(repo: Path, tmp_path: Path) -> None:
    (repo / "data").mkdir()
    link = tmp_path / "into-repo"
    link.symlink_to(repo / "data", target_is_directory=True)
    with pytest.raises(PathGuardError) as info:
        check_output_dir(link)
    assert info.value.rejections[0].resolved == repo / "data"
    assert str(repo / "data") in str(info.value)


def test_enforce_prints_every_rejected_path_and_exits_2(repo: Path) -> None:
    paths = {
        "cache directory": repo / "cache",
        "runs directory": repo / "data",
        "logs directory": repo / "src" / "logs",
    }
    before = _tree(repo)
    out = io.StringIO()
    with pytest.raises(SystemExit) as info:
        enforce_output_dirs(paths, stream=out)
    assert info.value.code == 2
    lines = out.getvalue().splitlines()
    assert len(lines) == 2
    assert lines[0].startswith(f"error: runs directory {repo / 'data'} ")
    assert lines[1].startswith(f"error: logs directory {repo / 'src' / 'logs'} ")
    assert _tree(repo) == before


def test_enforce_prints_nothing_when_all_paths_pass(repo: Path, tmp_path: Path) -> None:
    out = io.StringIO()
    resolved = enforce_output_dirs({"cache": repo / "cache", "runs": tmp_path / "x"}, stream=out)
    assert resolved == {"cache": repo / "cache", "runs": (tmp_path / "x").resolve()}
    assert out.getvalue() == ""


def test_git_discovery_variables_are_ignored(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = _git_init(tmp_path / "other", "*\n")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    with pytest.raises(PathGuardError):
        check_output_dir(repo / "data")
    assert check_output_dir(tmp_path / "plain") == (tmp_path / "plain").resolve()


# ---------------------------------------------------------------- fail closed


def test_fails_closed_without_git(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    with pytest.raises(PathGuardError) as info:
        check_output_dir(repo / "cache")
    assert "git could not be run" in str(info.value)
    assert str(repo / "cache") in str(info.value)
    assert check_output_dir(tmp_path / "plain") == (tmp_path / "plain").resolve()


def test_fails_closed_on_unreadable_repo(tmp_path: Path) -> None:
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / ".git").write_text("gitdir: /nonexistent/fse-path-guard\n", encoding="utf-8")
    before = _tree(broken)
    with pytest.raises(PathGuardError) as info:
        check_output_dir(broken / "cache")
    assert str(broken.resolve() / "cache") in str(info.value)
    assert _tree(broken) == before
