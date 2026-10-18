from __future__ import annotations

import os
import stat
import sys
import textwrap
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from fse_ui.app import create_app
from fse_ui.settings import Settings
from tests.runner_fixture import seed_configs

PROJECT = Path(__file__).resolve().parents[3]
ORIGIN = "http://localhost:5173"

STUB_FSE = textwrap.dedent(
    """\
    #!{python}
    import os, signal, sys, time
    def stop(*_):
        print("stub: interrupted", flush=True)
        sys.exit(130)
    signal.signal(signal.SIGINT, stop)
    print("stub argv:", sys.argv[1:], flush=True)
    if sys.argv[1:2] == ["accounts"]:
        print(
            '{{"kind":"fse_accounts","accounts":['
            '{{"id":"101","name":"PRAC-FAKE-1","can_trade":true,"role":""}},'
            '{{"id":"202","name":"50KTC-FAKE-2","can_trade":true,"role":""}}],'
            '"diagnostics":[]}}',
            flush=True,
        )
    canary = os.environ.get("STUB_PRINT_ENV")
    if canary:
        print("env value:", os.environ.get(canary, ""), flush=True)
    for i in range(int(os.environ.get("STUB_LINES", "0"))):
        print(f"line {{i}}", flush=True)
    time.sleep(float(os.environ.get("STUB_SLEEP", "0")))
    sys.exit(int(os.environ.get("STUB_EXIT", "0")))
    """
)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def stub_fse(tmp_path: Path) -> Path:
    path = tmp_path / "bin" / "fse"
    path.parent.mkdir()
    path.write_text(STUB_FSE.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def make_settings(home: Path, **over: Any) -> Settings:
    base: dict[str, Any] = {
        "env": "local",
        "home": home,
        "project_dir": PROJECT,
        "public_url": ORIGIN,
        "dev_auth": True,
    }
    base.update(over)
    return Settings(**base)


class Console:
    """A logged-in TestClient that sends the CSRF header on mutations."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        client.get("/auth/login", follow_redirects=False)
        self.csrf = client.get("/api/session").json()["csrf"]

    def get(self, url: str, **kw: Any) -> Any:
        return self.client.get(url, **kw)

    def post(self, url: str, **kw: Any) -> Any:
        return self.client.post(url, headers={"X-FSE-CSRF": self.csrf}, **kw)

    def put(self, url: str, **kw: Any) -> Any:
        return self.client.put(url, headers={"X-FSE-CSRF": self.csrf}, **kw)

    @property
    def app(self) -> Any:
        return self.client.app


@pytest.fixture
def console_factory(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[..., Console]]:
    for name in ("STUB_EXIT", "STUB_SLEEP", "STUB_LINES", "STUB_PRINT_ENV"):
        monkeypatch.delenv(name, raising=False)
    clients: list[TestClient] = []

    def make(**over: Any) -> Console:
        client = TestClient(create_app(make_settings(home, **over)), base_url=ORIGIN)
        client.__enter__()
        clients.append(client)
        return Console(client)

    yield make
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def console(console_factory: Callable[..., Console], stub_fse: Path) -> Console:
    return console_factory(fse_bin=str(stub_fse))


def wait_job(console: Console, job_id: str, timeout: float = 20.0) -> dict[str, Any]:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job: dict[str, Any] = console.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed", "cancelled", "interrupted"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


@pytest.fixture(scope="session", autouse=True)
def _test_project_configs() -> Iterator[None]:
    """Give every console the test runner and baseline configs, and the test research CLIs.

    The shipped catalog lists only ``fse``, the shipped runner has only paper plugins and
    the shipped baseline needs Operator values, so the tests seed variants of them (see
    ``tests/runner_fixture.py`` and ``tests/fake_research.py``).
    """
    import fse_ui.catalog as catalog
    from fse_ui.configs import ConfigStore
    from tests import fake_research

    original = ConfigStore.seed

    def seed(self: ConfigStore, project_dir: Path) -> int:
        added = 0
        for kind, name in (("runner", "runner"), ("strategy", "playbook_baseline")):
            added += not (self._root / kind / name / "meta.json").exists()
        seed_configs(self, project_dir)
        return added + original(self, project_dir)

    patch = pytest.MonkeyPatch()
    patch.setattr(ConfigStore, "seed", seed)
    patch.setattr(catalog, "PROGRAMS", (*catalog.PROGRAMS, *fake_research.PROGRAMS))
    patch.setattr(catalog, "_AWS_HOST_LOCAL", fake_research.HOST_LOCAL)
    yield
    patch.undo()


@pytest.fixture(autouse=True)
def _no_aws(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("AWS_") or name.startswith("FSE_UI_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


# AWS adapter fixtures (moto + Stubber), shared by the test_aws_* modules.
from tests.aws_support import aws, aws_console, fake_time, job_ids  # noqa: E402, F401
