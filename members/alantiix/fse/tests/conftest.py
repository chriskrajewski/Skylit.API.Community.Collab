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
- AWS isolation: every test gets fake ``testing`` credentials and nonexistent
  AWS config files, so no test can resolve the Operator's profiles or keys.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final

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

# ---------------------------------------------------------------- CI sharding

# FSE_TEST_SHARD=<k>/<n> keeps one of n disjoint shards (CI runs n jobs at once).
# Whole test files are assigned greedily, heaviest first, so the long Hypothesis
# files spread across shards. Seconds per file come from the CI --durations
# output; a file not listed counts SHARD_ITEM_S per test. Stale weights only make
# the shards less even: every test still runs in exactly one shard.
SHARD_FILE_S: Final[Mapping[str, float]] = MappingProxyType(
    {
        "tests/property/test_p73_observer_independence.py": 183.0,
        "tests/property/test_p70_replay_parity.py": 180.0,
        "tests/property/test_p72_stale_map_guard.py": 126.0,
        "tests/property/test_p55_inter_decision_taps.py": 110.0,
        "tests/property/test_p14_no_look_ahead.py": 87.0,
        "tests/property/test_p80_card_completeness.py": 84.0,
        "tests/property/test_p71_paper_isolation.py": 79.0,
        "tests/unit/test_data_equity_bars.py": 73.0,
        "tests/property/test_p56_funnel_accounting.py": 68.0,
        "tests/property/test_p49_restart_parity.py": 58.0,
        "tests/property/test_p34_backtest_determinism.py": 57.0,
        "tests/property/test_p57_shadow_no_leak.py": 51.0,
        "tests/integration/test_playbook_plugin_matches_live_runner.py": 49.0,
        "tests/property/test_p53_decision_log_completeness.py": 42.0,
        "tests/property/test_p08_interrupted_windows.py": 40.0,
    }
)
SHARD_ITEM_S: Final = 0.07


def _shard_of(value: str) -> tuple[int, int]:
    try:
        k, n = (int(x) for x in value.split("/"))
    except ValueError:
        raise pytest.UsageError(f"FSE_TEST_SHARD must be <k>/<n>, got {value!r}") from None
    if not 0 <= k < n:
        raise pytest.UsageError(f"FSE_TEST_SHARD {value!r}: k must be in 0..n-1")
    return k, n


def shard_files(weights: Mapping[str, float], n: int) -> dict[str, int]:
    """File -> shard: longest-processing-time first, ties by name (deterministic)."""
    loads = [0.0] * n
    out: dict[str, int] = {}
    for name in sorted(weights, key=lambda f: (-weights[f], f)):
        best = min(range(n), key=lambda i: (loads[i], i))
        out[name] = best
        loads[best] += weights[name]
    return out


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    value = os.environ.get("FSE_TEST_SHARD", "").strip()
    if not value:
        return
    k, n = _shard_of(value)
    counts: dict[str, int] = {}
    for item in items:
        counts[item.nodeid.split("::", 1)[0]] = counts.get(item.nodeid.split("::", 1)[0], 0) + 1
    weights = {f: SHARD_FILE_S.get(f, c * SHARD_ITEM_S) for f, c in counts.items()}
    owner = shard_files(weights, n)
    keep = [i for i in items if owner[i.nodeid.split("::", 1)[0]] == k]
    drop = [i for i in items if owner[i.nodeid.split("::", 1)[0]] != k]
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep


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


# Every AWS variable that can point botocore at real credentials, a real profile
# or a real endpoint. The file variables are replaced, not just removed.
_AWS_ENV_DROP: Final = (
    "AWS_PROFILE",
    "AWS_DEFAULT_PROFILE",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_SECURITY_TOKEN",
    "AWS_ROLE_ARN",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AWS_ENDPOINT_URL",
    "AWS_CA_BUNDLE",
)


@pytest.fixture(autouse=True)
def _aws_isolated(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No test can resolve real AWS credentials, whatever the Operator's shell exports."""
    for name in _AWS_ENV_DROP:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(isolated_home / "no-aws-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(isolated_home / "no-aws-credentials"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")  # fake: a stray real call fails auth
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture
def fake_secrets(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> FakeEnv:
    """Set a fake, non-blank value for every `.env.example` variable."""
    for name, value in _FAKE_VALUES.items():
        monkeypatch.setenv(name, value)
    return FakeEnv(home=isolated_home, values=_FAKE_VALUES)
