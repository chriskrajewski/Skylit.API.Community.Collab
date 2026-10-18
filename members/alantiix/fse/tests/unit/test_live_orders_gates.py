"""The ``fse run`` order gates (design D2, T11 unit level): :func:`resolve_plugin_order_mode`.

The local opt-in (``--plugin-orders`` and ``FSE_RUN_PLUGIN_ORDERS=enabled``)
is checked before any Broker_Adapter exists: ``BrokerAdapter.__init__`` is
patched to raise in those cases. The account cases run against a respx
gateway (no network).
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from fse.live.host.config import RunnerConfig, parse_runner_config
from fse.live.host.gate import plugin_dir
from fse.live.host.live_orders import (
    PLUGIN_ORDERS_ENV,
    JournalError,
    PluginOrderSetup,
    resolve_plugin_order_mode,
)
from fse.logio import LogWriter, Redactor
from fse.projectx.broker import BrokerAdapter
from fse.projectx.session import API_KEY_VARIABLE, USERNAME_VARIABLE, ProjectXSession
from fse.secrets.env import EnvView
from fse.timekit import parse_rfc3339
from tests.fakes.clock import FakeClock
from tests.fakes.projectx_gateway import BASE, FakeGateway

T0 = parse_rfc3339("2026-03-02T14:35:00Z")
CREDS = {USERNAME_VARIABLE: "fake-projectx-user-0000", API_KEY_VARIABLE: "fake-projectx-key-0000"}
ON = {PLUGIN_ORDERS_ENV: "enabled"}


def config(mode: str = "practice", *, enabled: bool = True) -> RunnerConfig:
    entry: dict[str, Any] = {
        "id": "alpha",
        "module": "x.y:Plugin",
        "enabled": enabled,
        "risk": {"max_contracts": 4, "max_daily_loss": "500.00", "max_trades_per_day": 6},
        "hold_through_break": False,
        "execution": {"order_mode": mode},
    }
    accounts: list[dict[str, Any]] = []
    if mode == "combine":
        entry["account"] = "tsx"
        accounts.append({"id": "tsx", "preset": "topstep_50k_combine"})
    plugins = [entry]
    if not enabled:
        plugins.append({"id": "other", "module": "x.y:Plugin"})
    return parse_runner_config(
        {"version": 1, "plugins": plugins, "accounts": accounts}, Path("runner.yaml")
    )


def writer() -> LogWriter:
    return LogWriter(Redactor(list(CREDS.values())), stdout=io.StringIO(), stderr=io.StringIO())


async def resolve(
    tmp: Path,
    cfg: RunnerConfig,
    values: dict[str, str],
    *,
    plugin_orders: bool = True,
    live_orders: bool = False,
) -> PluginOrderSetup | None:
    clock = FakeClock(T0)
    env = EnvView({**CREDS, **values}, {})
    async with httpx.AsyncClient() as http:
        session = ProjectXSession(env, Redactor(), http, clock, base_url=BASE)
        return await clock.run(
            resolve_plugin_order_mode(
                cfg,
                env,
                session,
                clock,
                writer(),
                tmp,
                plugin_orders=plugin_orders,
                live_orders=live_orders,
            )
        )


@pytest.fixture
def no_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_a: object, **_k: object) -> None:
        raise AssertionError("a BrokerAdapter was built")

    monkeypatch.setattr(BrokerAdapter, "__init__", refuse)


@pytest.mark.parametrize(
    ("values", "flag", "expected"),
    [
        (
            {**ON, "PRACTICE_ACCOUNT_ID": "101"},
            False,
            ["the start command has no --plugin-orders flag"],
        ),
        (
            {"PRACTICE_ACCOUNT_ID": "101"},
            True,
            ["FSE_RUN_PLUGIN_ORDERS is not set to enabled"],
        ),
        (
            {PLUGIN_ORDERS_ENV: "Enabled", "PRACTICE_ACCOUNT_ID": "101"},
            True,
            ["FSE_RUN_PLUGIN_ORDERS is not set to enabled"],
        ),
        (
            {"PRACTICE_ACCOUNT_ID": "101"},
            False,
            [
                "the start command has no --plugin-orders flag",
                "FSE_RUN_PLUGIN_ORDERS is not set to enabled",
            ],
        ),
        (dict(ON), True, ["PRACTICE_ACCOUNT_ID is unset"]),
        (
            {**ON, "PRACTICE_ACCOUNT_ID": "101", "COMBINE_ACCOUNT_ID": "101"},
            True,
            ["PRACTICE_ACCOUNT_ID equals COMBINE_ACCOUNT_ID"],
        ),
    ],
)
@pytest.mark.asyncio
@pytest.mark.usefixtures("no_adapter")
async def test_practice_local_failures_build_no_adapter(
    tmp_path: Path, values: dict[str, str], flag: bool, expected: list[str]
) -> None:
    setup = await resolve(tmp_path, config(), values, plugin_orders=flag)
    assert setup is not None
    assert setup.decision.mode == "paper"
    assert list(setup.decision.failures) == expected
    assert setup.adapter is None
    assert "101" not in (setup.decision.note or "")
    assert not plugin_dir(tmp_path, "alpha").exists()


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_adapter")
async def test_combine_lists_every_local_failure_in_order(tmp_path: Path) -> None:
    setup = await resolve(tmp_path, config("combine"), {}, plugin_orders=False)
    assert setup is not None
    assert list(setup.decision.failures) == [
        "the start command has no --plugin-orders flag",
        "FSE_RUN_PLUGIN_ORDERS is not set to enabled",
        "COMBINE_ACCOUNT_ID is unset",
        "the start command has no --live-orders flag",
    ]


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_adapter")
async def test_no_non_paper_plugin_resolves_nothing(tmp_path: Path) -> None:
    cfg = parse_runner_config(
        {"version": 1, "plugins": [{"id": "a", "module": "x.y:Plugin"}]}, Path("runner.yaml")
    )
    assert await resolve(tmp_path, cfg, dict(ON)) is None
    assert await resolve(tmp_path, config(enabled=False), dict(ON)) is None


@pytest.fixture
def gateway(respx_router: respx.MockRouter) -> FakeGateway:
    gw = FakeGateway()
    gw.install(respx_router)
    return gw


@pytest.mark.asyncio
async def test_practice_with_every_gate_gives_a_bound_adapter_and_state(
    tmp_path: Path, gateway: FakeGateway
) -> None:
    setup = await resolve(tmp_path, config(), {**ON, "PRACTICE_ACCOUNT_ID": "101"})
    assert setup is not None
    assert setup.decision.mode == "practice"
    assert setup.adapter is not None
    assert setup.adapter.account_id == 101
    assert setup.journal is not None
    assert setup.ids is not None
    assert setup.blocks is not None
    assert setup.journal.path.parent == plugin_dir(tmp_path, "alpha")
    assert not gateway.order_paths()


@pytest.mark.parametrize(
    ("mode", "values", "live", "text"),
    [
        ("practice", {"PRACTICE_ACCOUNT_ID": "999"}, False, "matches no account"),
        ("combine", {"COMBINE_ACCOUNT_ID": "303"}, True, "matches no account"),
    ],
)
@pytest.mark.asyncio
async def test_unresolved_accounts_run_paper(
    tmp_path: Path,
    gateway: FakeGateway,
    mode: str,
    values: dict[str, str],
    live: bool,
    text: str,
) -> None:
    setup = await resolve(tmp_path, config(mode), {**ON, **values}, live_orders=live)
    assert setup is not None
    assert setup.decision.mode == "paper"
    note = setup.decision.note or ""
    assert text in note
    for value in values.values():
        assert value not in note
    assert setup.journal is None
    assert not gateway.order_paths()


@pytest.mark.asyncio
async def test_combine_with_every_gate(tmp_path: Path, gateway: FakeGateway) -> None:
    setup = await resolve(
        tmp_path, config("combine"), {**ON, "COMBINE_ACCOUNT_ID": "202"}, live_orders=True
    )
    assert setup is not None
    assert setup.decision.mode == "combine"
    assert setup.adapter is not None
    assert setup.adapter.account_id == 202


@pytest.mark.asyncio
async def test_an_unreadable_journal_raises(tmp_path: Path, gateway: FakeGateway) -> None:
    journal = plugin_dir(tmp_path, "alpha") / "order_journal.jsonl"
    journal.mkdir(parents=True)  # a directory where the file should be: unreadable
    with pytest.raises(JournalError):
        await resolve(tmp_path, config(), {**ON, "PRACTICE_ACCOUNT_ID": "101"})
