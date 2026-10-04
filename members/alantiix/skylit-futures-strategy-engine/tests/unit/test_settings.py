"""Unit tests for ``fse.settings``: default directories under ``~/.skylit-fse/``.

**Validates: Requirements 1.10**
"""

from __future__ import annotations

from dataclasses import astuple
from pathlib import Path

import pytest

from fse.settings import OUTPUT_DIR_NAMES, app_home, default_paths

PROJECT_DIR = Path(__file__).resolve().parents[2]


def test_defaults_live_under_home_skylit_fse(isolated_home: Path) -> None:
    paths = default_paths()
    root = isolated_home / ".skylit-fse"
    assert app_home() == root
    assert paths.root == root
    assert paths.cache == root / "cache"
    assert paths.runs == root / "runs"
    assert paths.recordings == root / "recordings"
    assert paths.live_state == root / "live-state"
    assert paths.logs == root / "logs"
    for path in astuple(paths):
        assert path.is_absolute()
        assert not path.resolve().is_relative_to(PROJECT_DIR)


def test_explicit_home_overrides_path_home(tmp_path: Path) -> None:
    assert default_paths(tmp_path).cache == tmp_path / ".skylit-fse" / "cache"


def test_home_is_read_at_call_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert default_paths().root == tmp_path / ".skylit-fse"


def test_default_paths_create_nothing(isolated_home: Path) -> None:
    default_paths()
    app_home()
    assert list(isolated_home.iterdir()) == []


def test_output_dir_names_are_gitignored() -> None:
    entries = (PROJECT_DIR / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert OUTPUT_DIR_NAMES == ("cache", "runs", "recordings", "live-state", "logs")
    for name in OUTPUT_DIR_NAMES:
        assert f"{name}/" in entries, name
