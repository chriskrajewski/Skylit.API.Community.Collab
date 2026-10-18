"""Opt-in live smoke test: the real Skylit API, free read-only endpoints only.

Run with ``python -m pytest -m live -rP``; the default run excludes the ``live``
marker (``addopts`` in ``pyproject.toml``). It sends exactly two requests,
``GET /v1/account`` and ``GET /v1/symbols``, both 0 credits per the credit
table in https://www.skylit.ai/docs/openapi.yaml.

Safety:

- ``SKYLIT_API_KEY`` is read only through ``fse.secrets.env.load_env`` on the
  Project folder (shell value first, then the Project ``.env``). The test
  never opens ``.env`` itself and never prints a value.
- The suite's network guard stays on: only the two endpoints above get a
  respx pass-through route, for this test only. Any other request fails.
- The suite's environment isolation is relaxed here only: ``isolated_home`` is
  overridden to keep the temporary ``HOME`` without blanking the
  Secret_Variables, so the Operator's real precedence applies.
- Assertions are on shape only (status 200, required fields, positive
  limits). The one summary line goes through the Log_Writer, which redacts
  every Secret_Variable value; the Fetch_Log goes to a temporary folder.

**Validates: Requirements 1.6, 1.7, 2.1**
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest
import respx

from fse.clock import SystemClock
from fse.data.planner import DEFAULT_SYMBOLS
from fse.logio import LogWriter
from fse.secrets.env import load_env
from fse.settings import project_dir
from fse.skylit.client import KEY_VARIABLE, ClientConfig, Failed, SkylitClient
from fse.skylit.endpoints import ACCOUNT, SYMBOLS
from fse.skylit.fetch_log import FETCH_LOG_FILE_NAME, FetchLog
from fse.skylit.models import AccountInfo, SymbolCatalog

pytestmark = pytest.mark.live


@pytest.fixture
def isolated_home(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Override of the suite fixture: a temporary HOME, Secret_Variables left as they are."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


@pytest.fixture
def free_endpoints(respx_router: respx.MockRouter) -> respx.MockRouter:
    """Let exactly the two free endpoints through the suite's network guard."""
    for endpoint in (ACCOUNT, SYMBOLS):
        respx_router.route(
            method="GET", scheme="https", host=endpoint.host.value, path=endpoint.path
        ).pass_through()
    return respx_router


def _cause(result: Failed) -> str:
    """Status and error code only."""
    return f"{result.path}: {result.cause}"


@pytest.mark.asyncio
async def test_free_endpoints_answer_with_the_documented_shape(
    free_endpoints: respx.MockRouter, tmp_path: Path
) -> None:
    folder = project_dir()
    assert folder is not None, "the Project folder was not found"
    env = load_env(folder)
    if env.get(KEY_VARIABLE) is None:
        pytest.skip(f"{KEY_VARIABLE} is blank in the shell and the Project .env")
    writer = LogWriter.from_env(env)
    log_path = tmp_path / FETCH_LOG_FILE_NAME

    try:
        with FetchLog(writer, log_path) as log:
            async with SkylitClient(
                env, ClientConfig(), log, SystemClock(), random.Random()
            ) as client:
                account = await client.account()
                catalog = await client.symbols()
                limits = client.limits
                sent = client.attempts_sent
        text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    finally:
        # The Fetch_Log may hold X-Credits-Remaining; keep no copy of it.
        log_path.unlink(missing_ok=True)

    entries = [json.loads(line) for line in text.splitlines()]
    statuses = [(e["path"], e["status"], e["error_code"]) for e in entries]
    assert not isinstance(account, Failed), _cause(account)
    assert not isinstance(catalog, Failed), _cause(catalog)
    assert isinstance(account, AccountInfo)
    assert isinstance(catalog, SymbolCatalog)
    assert statuses == [("/v1/account", 200, None), ("/v1/symbols", 200, None)]
    assert sent == 2

    rpm = account.limits.requests_per_minute
    hif = account.limits.historical_in_flight
    # AccountLimits holds only positive integers here, so `is not None` is the shape check.
    assert rpm is not None, "limits.requestsPerMinute is not a positive integer"
    assert hif is not None, "limits.historicalInFlight is not a positive integer"
    assert rpm > 0
    assert hif > 0
    assert limits is not None
    assert limits.requests_per_minute_from_account
    assert limits.historical_in_flight_from_account
    assert len(catalog.symbols) > 0
    assert all(s.symbol and s.metrics for s in catalog.symbols)

    listed = sum(catalog.get(symbol) is not None for symbol in DEFAULT_SYMBOLS)
    with_history = sum(s.history is not None for s in catalog.symbols)
    writer.echo(
        f"live smoke passed: GET /v1/account 200 (requestsPerMinute={rpm}, "
        f"historicalInFlight={hif}); GET /v1/symbols 200 ({len(catalog.symbols)} symbols, "
        f"{with_history} with history dates, {listed}/{len(DEFAULT_SYMBOLS)} default "
        "pull symbols listed)"
    )
