"""Unit tests for the ``fse`` entry point and ``fse scan-secrets``.

Every run passes a temporary Project folder and an explicit environment, so no
test reads the real shell credentials or a real ``.env`` file. Every
repository is a temporary git repository under ``tmp_path``; every value is fake.

**Validates: Requirements 1.6, 1.9, 1.13, 1.14, 1.15, 1.16**
"""

from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

from fse import cli
from fse.calendars import CalendarError, CalendarProblem
from fse.commands import CommandContext, SubParsers, scan_secrets
from fse.data.cache_io import CacheError
from fse.data.path_guard import PathGuardError, PathRejection
from fse.settings import PROJECT_NAME, project_dir

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

FAKE_KEY = "fake-cli-key-0001"
FAKE_DOTENV_KEY = "fake-dotenv-key-0002"
FAKE_CWD_KEY = "fake-cwd-key-0003"
PROJECT_DIR = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Outcome:
    code: int
    out: str
    err: str

    @property
    def text(self) -> str:
        return self.out + self.err


def fse(*argv: str, project: Path | None, environ: Mapping[str, str] | None = None) -> Outcome:
    out, err = io.StringIO(), io.StringIO()
    code = cli.run(
        list(argv), project_dir=project, environ=dict(environ or {}), stdout=out, stderr=err
    )
    return Outcome(code, out.getvalue(), err.getvalue())


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A temporary Project folder with no ``.env``."""
    path = tmp_path / "project"
    path.mkdir()
    return path


@pytest.fixture(autouse=True)
def _isolated_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    return root.resolve()


def _track(repo: Path, files: Mapping[str, str]) -> None:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)


# ---------------------------------------------------------------- parser and discovery


def test_discovery_finds_scan_secrets() -> None:
    assert scan_secrets.register in cli.discover_commands()


def test_top_level_help(project: Path) -> None:
    result = fse("--help", project=project)
    assert result.code == 0
    assert "scan-secrets" in result.out
    assert "exit status:" in result.out
    assert result.err == ""


def test_scan_secrets_help(project: Path) -> None:
    result = fse("scan-secrets", "--help", project=project)
    assert result.code == 0
    assert "--repo DIR" in result.out
    assert "5  the scan could not run" in result.out


def test_missing_command_is_a_usage_error(project: Path) -> None:
    result = fse(project=project)
    assert result.code == cli.EXIT_INVALID_INPUT
    assert "usage: fse" in result.err


def test_usage_error_is_redacted(project: Path) -> None:
    result = fse("scan-secrets", FAKE_KEY, project=project, environ={"SKYLIT_API_KEY": FAKE_KEY})
    assert result.code == cli.EXIT_INVALID_INPUT
    assert "unrecognized arguments: [REDACTED]" in result.err
    assert FAKE_KEY not in result.text


# ---------------------------------------------------------------- scan-secrets


@needs_git
def test_scan_prints_only_matching_paths(project: Path, repo: Path) -> None:
    _track(
        repo,
        {
            "leak.txt": f"token = {FAKE_KEY}  # marker-line\n",
            "nested/also.yaml": f"key: {FAKE_KEY}\n",
            "clean.txt": "marker-clean\n",
        },
    )
    result = fse(
        "scan-secrets", "--repo", str(repo), project=project, environ={"SKYLIT_API_KEY": FAKE_KEY}
    )
    assert result.code == cli.EXIT_SECRETS_FOUND
    assert result.out.splitlines() == ["leak.txt", "nested/also.yaml"]
    assert result.err.splitlines() == [
        "scan-secrets: 2 tracked files of 3 searched contain a secret value"
    ]
    assert FAKE_KEY not in result.text
    assert "marker" not in result.text  # no file content


@needs_git
def test_scan_clean_repo_exits_zero(project: Path, repo: Path) -> None:
    _track(repo, {"a.txt": "nothing\n"})
    result = fse(
        "scan-secrets",
        "--repo",
        str(repo / "."),
        project=project,
        environ={"SKYLIT_API_KEY": FAKE_KEY},
    )
    assert result.code == cli.EXIT_OK
    assert result.out == ""
    assert result.err.splitlines() == ["scan-secrets: no secret value found in 1 tracked file"]


@needs_git
def test_scan_reads_the_project_env_not_the_cwd_env(
    project: Path, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Both .env files are fake fixtures written by this test.
    (project / ".env").write_text(f"SKYLIT_API_KEY={FAKE_DOTENV_KEY}\n", encoding="utf-8")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / ".env").write_text(f"NARRATOR_API_KEY={FAKE_CWD_KEY}\n", encoding="utf-8")
    monkeypatch.chdir(cwd)
    _track(repo, {"from-dotenv.txt": FAKE_DOTENV_KEY, "from-cwd.txt": FAKE_CWD_KEY})
    result = fse("scan-secrets", "--repo", str(repo), project=project)
    assert result.code == cli.EXIT_SECRETS_FOUND
    assert result.out.splitlines() == ["from-dotenv.txt"]
    assert FAKE_DOTENV_KEY not in result.text


@needs_git
def test_scan_defaults_to_the_project_working_tree(repo: Path) -> None:
    project = repo / "members" / "someone" / "proj"
    _track(repo, {"top.txt": FAKE_KEY, "members/someone/proj/x.txt": "clean"})
    result = fse("scan-secrets", project=project, environ={"SKYLIT_API_KEY": FAKE_KEY})
    assert result.code == cli.EXIT_SECRETS_FOUND
    assert result.out.splitlines() == ["top.txt"]  # the whole index, not only the project


@needs_git
def test_scan_path_with_newline_stays_on_one_line(project: Path, repo: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("no newline in Windows file names")
    _track(repo, {"bad\nname.txt": FAKE_KEY})
    result = fse(
        "scan-secrets", "--repo", str(repo), project=project, environ={"SKYLIT_API_KEY": FAKE_KEY}
    )
    assert result.code == cli.EXIT_SECRETS_FOUND
    assert result.out.splitlines() == [repr("bad\nname.txt")]


@needs_git
def test_scan_without_values_exits_5(project: Path, repo: Path) -> None:
    _track(repo, {"a.txt": "x"})
    result = fse(
        "scan-secrets", "--repo", str(repo), project=project, environ={"SKYLIT_API_KEY": " "}
    )
    assert result.code == cli.EXIT_SCAN_CANNOT_RUN
    assert result.out == ""
    assert result.err.startswith("error: no secret values configured")


def test_scan_outside_a_repository_exits_5(project: Path, tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    result = fse(
        "scan-secrets", "--repo", str(plain), project=project, environ={"SKYLIT_API_KEY": FAKE_KEY}
    )
    assert result.code == cli.EXIT_SCAN_CANNOT_RUN
    assert result.out == ""
    assert result.err.startswith("error: cannot list the repository's tracked files")


@needs_git
def test_scan_without_project_folder_reads_shell_only(repo: Path) -> None:
    _track(repo, {"a.txt": FAKE_KEY})
    result = fse(
        "scan-secrets", "--repo", str(repo), project=None, environ={"SKYLIT_API_KEY": FAKE_KEY}
    )
    assert result.code == cli.EXIT_SECRETS_FOUND
    assert "the Project folder was not found" in result.err


# ---------------------------------------------------------------- error mapping


def _register_probe(subparsers: SubParsers) -> None:
    parser = subparsers.add_parser("probe")
    parser.add_argument("action")
    parser.set_defaults(handler=_probe)


def _probe(args: argparse.Namespace, ctx: CommandContext) -> int:
    action: str = args.action
    if action == "echo":
        ctx.writer.echo(f"value {ctx.env.get('SKYLIT_API_KEY')}")
        return 0
    if action == "path":
        target = Path("/repo/cache")
        raise PathGuardError([PathRejection("cache", str(target), target, "cache is bad.")])
    if action == "calendar":
        raise CalendarError([CalendarProblem(Path("cal.yaml"), 3, None, "bad date")])
    if action == "cache":
        raise CacheError("Data_Cache write failed for key")
    if action == "interrupt":
        raise KeyboardInterrupt
    if action == "exit":
        raise SystemExit(3)
    raise RuntimeError(f"boom with {ctx.env.get('SKYLIT_API_KEY')}")


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> None:
    commands = [*cli.discover_commands(), _register_probe]
    monkeypatch.setattr(cli, "discover_commands", lambda: commands)


@pytest.mark.usefixtures("probe")
@pytest.mark.parametrize(
    ("action", "code", "message"),
    [
        ("path", 2, "error: cache is bad."),
        ("calendar", 2, "error: cal.yaml, line 3: bad date"),
        ("cache", 4, "error: Data_Cache write failed for key"),
        ("interrupt", 130, "interrupted"),
        ("exit", 3, ""),
    ],
)
def test_errors_map_to_design_exit_codes(
    project: Path, action: str, code: int, message: str
) -> None:
    result = fse("probe", action, project=project)
    assert result.code == code
    assert result.err.strip() == message


@pytest.mark.usefixtures("probe")
def test_command_output_and_tracebacks_are_redacted(project: Path) -> None:
    environ = {"SKYLIT_API_KEY": FAKE_KEY}
    echoed = fse("probe", "echo", project=project, environ=environ)
    assert echoed.out == "value [REDACTED]\n"
    crashed = fse("probe", "boom", project=project, environ=environ)
    assert crashed.code == cli.EXIT_FAILURE
    assert crashed.err.startswith("error: unexpected failure\nTraceback")
    assert "RuntimeError: boom with [REDACTED]" in crashed.err
    assert FAKE_KEY not in crashed.text


@pytest.mark.usefixtures("probe")
def test_unreadable_dotenv_exits_3_or_5_for_scan_secrets(project: Path) -> None:
    (project / ".env").write_bytes(b"SKYLIT_API_KEY=\xff\xfe\n")  # not UTF-8
    probe = fse("probe", "echo", project=project)
    assert probe.code == cli.EXIT_CREDENTIALS
    assert probe.out == ""
    assert probe.err.splitlines() == [f"error: cannot read {project / '.env'}"]
    scanned = fse("scan-secrets", project=project)
    assert scanned.code == cli.EXIT_SCAN_CANNOT_RUN


def test_hooks_are_restored_after_a_run(project: Path) -> None:
    before = (sys.excepthook, sys.unraisablehook)
    fse("--help", project=project)
    assert (sys.excepthook, sys.unraisablehook) == before


def test_main_reads_argv_and_the_project_folder(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "find_project_dir", lambda: project)
    monkeypatch.setattr(sys, "argv", ["fse", "--help"])
    assert cli.main() == 0
    assert "scan-secrets" in capsys.readouterr().out


# ---------------------------------------------------------------- project folder


def test_project_dir_is_the_folder_holding_pyproject() -> None:
    assert project_dir() == PROJECT_DIR


def test_project_dir_needs_the_project_pyproject(tmp_path: Path) -> None:
    package = tmp_path / "site" / "lib" / "fse"
    package.mkdir(parents=True)
    module = package / "settings.py"
    assert project_dir(module) is None  # no pyproject.toml
    (tmp_path / "site" / "pyproject.toml").write_text(
        '[project]\nname = "other"\n', encoding="utf-8"
    )
    assert project_dir(module) is None
    (tmp_path / "site" / "pyproject.toml").write_text(
        f'[project]\nname = "{PROJECT_NAME}"\n', encoding="utf-8"
    )
    assert project_dir(module) == (tmp_path / "site").resolve()
