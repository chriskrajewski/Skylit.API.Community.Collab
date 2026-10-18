"""Checks for the task 1.1 harness: pins, network guard, env isolation, profiles.

The AWS isolation tests prove that no test can reach the Operator's AWS
profiles or keys (aws-export design review round 3, finding 1).

**Validates: Requirements 1.1; aws-export design §3 and §14 (AWS isolation)**
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
    "websockets": "17.2",  # plugin-runner design 4.3: the ProjectX market hub
}
DEV_PINS = {
    "pytest": "9.1.1",
    "hypothesis": "6.168.3",
    "respx": "0.23.1",
    "pytest-asyncio": "1.4.0",
    "pytest-xdist": "3.8.0",
    "ruff": "0.16.10",
    "mypy": "2.4.0",
    "moto[s3,glue]": "5.2.3",
    "pandas": "3.0.6",  # tests that compare against pandas only
}
AWS_PINS = {"boto3": "1.43.108"}
ALL_PINS = {**RUNTIME_PINS, **DEV_PINS, **AWS_PINS}


def _pyproject() -> dict[str, object]:
    with (PROJECT_DIR / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


# ---------------------------------------------------------------- packaging


def test_pyproject_declares_exact_design_pins() -> None:
    project = _pyproject()["project"]
    assert isinstance(project, dict)
    assert project["requires-python"] == ">=3.14"
    assert project["scripts"] == {"fse": "fse.cli:main"}
    assert project["dependencies"] == [f"{n}=={v}" for n, v in RUNTIME_PINS.items()]
    assert project["optional-dependencies"]["dev"] == [f"{n}=={v}" for n, v in DEV_PINS.items()]
    assert project["optional-dependencies"]["aws"] == [f"{n}=={v}" for n, v in AWS_PINS.items()]


def _dist(name: str) -> str:
    """The distribution name without its ``[extras]``."""
    return name.split("[", 1)[0]


def test_installed_versions_match_pins() -> None:
    for name, pinned in ALL_PINS.items():
        assert version(_dist(name)) == pinned, name


def test_lock_pins_every_direct_dependency_with_hashes() -> None:
    lock = (PROJECT_DIR / "requirements.lock").read_text(encoding="utf-8")
    entries = lock.split("\n")
    for name, pinned in ALL_PINS.items():
        line = f"{_dist(name).lower()}=={pinned}"
        idx = next(i for i, text in enumerate(entries) if text.startswith(line))
        assert entries[idx + 1].strip().startswith("--hash=sha256:"), name


def test_markers_registered(pytestconfig: pytest.Config) -> None:
    names = {line.split(":", 1)[0] for line in pytestconfig.getini("markers")}
    assert {"perf", "integration", "live", "localcache"} <= names


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


# ---------------------------------------------------------------- AWS isolation


def test_aws_profile_hts_is_unreachable() -> None:
    import boto3
    import botocore.exceptions

    with pytest.raises(botocore.exceptions.ProfileNotFound):
        boto3.Session(profile_name="myprofile")


def test_aws_env_is_fake() -> None:
    assert os.environ["AWS_ACCESS_KEY_ID"] == "testing"
    assert os.environ["AWS_SECRET_ACCESS_KEY"] == "testing"
    assert os.environ["AWS_EC2_METADATA_DISABLED"] == "true"
    assert os.environ["AWS_DEFAULT_REGION"] == "us-east-1"
    for name in ("AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE"):
        assert not Path(os.environ[name]).exists(), name
    for name in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_SESSION_TOKEN", "AWS_ROLE_ARN"):
        assert name not in os.environ, name
    assert "AWS_ENDPOINT_URL" not in os.environ


def test_aws_default_session_uses_fake_keys() -> None:
    import boto3

    credentials = boto3.Session().get_credentials()  # local resolution only, no request
    assert credentials is not None
    assert credentials.access_key == "testing"


# ---------------------------------------------------------------- Hypothesis


def test_hypothesis_profiles_registered() -> None:
    assert settings.get_profile("dev").max_examples == 100
    assert settings.get_profile("ci").max_examples == 200
    assert settings().max_examples in {100, 200}


@given(st.integers())
def test_hypothesis_runs_under_active_profile(n: int) -> None:
    assert isinstance(n, int)
