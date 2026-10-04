"""Checks for the task 1.1 harness: pins, network guard, env isolation, profiles.

**Validates: Requirements 1.1**
"""

from __future__ import annotations

import os
import tomllib
from importlib.metadata import version
from pathlib import Path

import httpx
import pytest
import respx
from hypothesis import given, settings
from hypothesis import strategies as st
from respx.models import AllMockedAssertionError

from tests.conftest import ENV_EXAMPLE_VARIABLES, FakeEnv

PROJECT_DIR = Path(__file__).resolve().parents[2]

RUNTIME_PINS = {
    "httpx": "0.28.1",
    "httpx-sse": "0.4.3",
    "pydantic": "2.13.5",
    "pyarrow": "25.0.1",
    "numpy": "2.5.3",
    "PyYAML": "6.0.3",
    "python-dotenv": "1.2.4",
    "tzdata": "2026.4",
}
DEV_PINS = {
    "pytest": "9.1.1",
    "hypothesis": "6.168.3",
    "respx": "0.23.1",
    "pytest-asyncio": "1.4.0",
    "ruff": "0.16.10",
    "mypy": "2.4.0",
}


def _pyproject() -> dict[str, object]:
    with (PROJECT_DIR / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


# ---------------------------------------------------------------- packaging


def test_project_lives_in_member_folder() -> None:
    assert PROJECT_DIR.parts[-3:] == ("members", "alantiix", "skylit-futures-strategy-engine")


def test_pyproject_declares_exact_design_pins() -> None:
    project = _pyproject()["project"]
    assert isinstance(project, dict)
    assert project["requires-python"] == ">=3.14"
    assert project["scripts"] == {"fse": "fse.cli:main"}
    assert project["dependencies"] == [f"{n}=={v}" for n, v in RUNTIME_PINS.items()]
    assert project["optional-dependencies"]["dev"] == [f"{n}=={v}" for n, v in DEV_PINS.items()]


def test_installed_versions_match_pins() -> None:
    for name, pinned in {**RUNTIME_PINS, **DEV_PINS}.items():
        assert version(name) == pinned, name


def test_lock_pins_every_direct_dependency_with_hashes() -> None:
    lock = (PROJECT_DIR / "requirements.lock").read_text(encoding="utf-8")
    entries = lock.split("\n")
    for name, pinned in {**RUNTIME_PINS, **DEV_PINS}.items():
        line = f"{name.lower()}=={pinned}"
        idx = next(i for i, text in enumerate(entries) if text.startswith(line))
        assert entries[idx + 1].strip().startswith("--hash=sha256:"), name


def test_markers_registered(pytestconfig: pytest.Config) -> None:
    names = {line.split(":", 1)[0] for line in pytestconfig.getini("markers")}
    assert {"perf", "integration"} <= names


# ---------------------------------------------------------------- network guard


def test_unmocked_sync_request_is_blocked() -> None:
    with pytest.raises(AllMockedAssertionError):
        httpx.get("https://api.skylit.invalid/v1/account")


@pytest.mark.asyncio
async def test_unmocked_async_request_is_blocked() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(AllMockedAssertionError):
            await client.get("https://gateway.projectx.invalid/api/Auth/loginKey")


def test_respx_router_fixture_serves_routes(respx_router: respx.MockRouter) -> None:
    route = respx_router.get("https://api.skylit.invalid/v1/symbols").respond(json=["SPX"])
    assert httpx.get("https://api.skylit.invalid/v1/symbols").json() == ["SPX"]
    assert route.call_count == 1


def test_nested_respx_mock_still_guarded() -> None:
    with respx.mock(assert_all_called=False) as inner:
        inner.get("https://api.skylit.invalid/v1/ok").respond(204)
        assert httpx.get("https://api.skylit.invalid/v1/ok").status_code == 204
        with pytest.raises(AllMockedAssertionError):
            httpx.get("https://api.skylit.invalid/v1/other")


# ---------------------------------------------------------------- environment


def test_home_is_temporary(isolated_home: Path) -> None:
    assert Path.home() == isolated_home
    assert Path("~/.skylit-fse").expanduser().is_relative_to(isolated_home)
    assert not (isolated_home / ".skylit-fse").exists()


def test_secret_variables_blank_by_default() -> None:
    for name in ENV_EXAMPLE_VARIABLES:
        assert name not in os.environ, name


def test_fake_secrets_sets_only_fake_values(fake_secrets: FakeEnv) -> None:
    assert Path.home() == fake_secrets.home
    for name in ENV_EXAMPLE_VARIABLES:
        assert os.environ[name] == fake_secrets.values[name]
    assert len(fake_secrets.secret_values()) == 7
    for value in fake_secrets.secret_values():
        assert "fake" in value.lower()


# ---------------------------------------------------------------- Hypothesis


def test_hypothesis_profiles_registered() -> None:
    assert settings.get_profile("dev").max_examples == 100
    assert settings.get_profile("ci").max_examples == 200
    assert settings().max_examples in {100, 200}


@given(st.integers())
def test_hypothesis_runs_under_active_profile(n: int) -> None:
    assert isinstance(n, int)
