"""Suite-wide test harness.

Owned by task 1.1; later tasks add fixtures in their own modules, not here.

- Hypothesis profiles: ``dev`` (100 examples, the default) and ``ci`` (200).
  Pick one with ``--hypothesis-profile=ci`` or ``HYPOTHESIS_PROFILE=ci``.
- Network guard: a session-wide respx router with ``assert_all_mocked=True``
  is active for every test, so no request can reach Skylit, Atlas, ProjectX,
  a webhook or the Narrator. Tests add routes through the ``respx_router``
  fixture (routes are cleared after each test) or open their own
  ``respx.mock(...)`` block, which nests inside the guard.
- Environment isolation: every test gets a temporary ``HOME`` and a blank
  value for every Secret_Variable, so no test reads real credentials from the
  shell or touches the real ``~/.skylit-fse``. The ``fake_secrets`` fixture
  then sets fake, non-blank values.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import pytest
import respx
from hypothesis import HealthCheck, settings

# ---------------------------------------------------------------- Hypothesis

settings.register_profile(
    "dev",
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.register_profile(
    "ci",
    max_examples=200,
    deadline=None,
    print_blob=True,
    suppress_health_check=[HealthCheck.too_slow],
)
# `--hypothesis-profile` on the command line overrides this choice.
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))

# ---------------------------------------------------------------- network guard


@pytest.fixture(scope="session", autouse=True)
def _network_guard() -> Iterator[respx.MockRouter]:
    """Fail any HTTP request that no test route matches."""
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


@pytest.fixture
def respx_router(_network_guard: respx.MockRouter) -> Iterator[respx.MockRouter]:
    """The session router, with routes and call history cleared after the test."""
    yield _network_guard
    _network_guard.clear()
    _network_guard.reset()


# ---------------------------------------------------------------- environment

# Secret_Variables per design §1, plus the account id variables, which count as
# Secret_Variables only when listed in FSE_SECRET_VARS.
SECRET_VARIABLES: tuple[str, ...] = (
    "SKYLIT_API_KEY",
    "PROJECTX_USERNAME",
    "PROJECTX_API_KEY",
    "NOTIFIER_WEBHOOK_URL",
    "NARRATOR_API_KEY",
)
ENV_EXAMPLE_VARIABLES: tuple[str, ...] = (
    *SECRET_VARIABLES,
    "PRACTICE_ACCOUNT_ID",
    "COMBINE_ACCOUNT_ID",
    "FSE_SECRET_VARS",
)

# Fake values only. This is a public repository: no real key, credential,
# account name or account id may appear in any file.
_FAKE_VALUES: Mapping[str, str] = MappingProxyType(
    {
        "SKYLIT_API_KEY": "fake-skylit-key-0000",
        "PROJECTX_USERNAME": "fake-projectx-user-0000",
        "PROJECTX_API_KEY": "fake-projectx-key-0000",
        "NOTIFIER_WEBHOOK_URL": "https://webhook.invalid/fake-notifier-hook-0000",
        "NARRATOR_API_KEY": "fake-narrator-key-0000",
        "PRACTICE_ACCOUNT_ID": "FAKE-ACCOUNT-1",
        "COMBINE_ACCOUNT_ID": "FAKE-ACCOUNT-2",
        "FSE_SECRET_VARS": "PRACTICE_ACCOUNT_ID,COMBINE_ACCOUNT_ID",
    }
)


@dataclass(frozen=True)
class FakeEnv:
    """What ``fake_secrets`` set: the temporary HOME and every fake value."""

    home: Path
    values: Mapping[str, str]

    def secret_values(self) -> list[str]:
        """Non-blank Secret_Variable values, including FSE_SECRET_VARS names."""
        extra = [n.strip() for n in self.values["FSE_SECRET_VARS"].split(",") if n.strip()]
        return [self.values[name] for name in (*SECRET_VARIABLES, *extra)]


@pytest.fixture(autouse=True)
def isolated_home(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Give each test a temporary HOME and blank every Secret_Variable.

    Names the Operator listed in a real FSE_SECRET_VARS are removed too. Only
    variable names are read here, never values.
    """
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    extra = [n.strip() for n in os.environ.get("FSE_SECRET_VARS", "").split(",") if n.strip()]
    for name in (*ENV_EXAMPLE_VARIABLES, *extra):
        monkeypatch.delenv(name, raising=False)
    return home


@pytest.fixture
def fake_secrets(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> FakeEnv:
    """Set a fake, non-blank value for every `.env.example` variable."""
    for name, value in _FAKE_VALUES.items():
        monkeypatch.setenv(name, value)
    return FakeEnv(home=isolated_home, values=_FAKE_VALUES)
