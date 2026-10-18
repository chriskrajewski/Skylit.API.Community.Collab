"""The plugin API surface (design 3): types, tags, no-op hooks and re-exports."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

import fse.live.plugins as plugins
from fse.engine import types as engine_types
from fse.live.host import dayclock
from fse.live.plugins import (
    PLUGIN_API_VERSION,
    TAG_RE,
    BarNeed,
    DataNeeds,
    PluginServices,
    StrategyPlugin,
    api,
)
from fse.logio import LogWriter
from fse.logio.redact import Redactor
from fse.secrets.env import EnvView
from tests.fakes.clock import FakeClock
from tests.fakes.dataplane import MemoryBarStore
from tests.fakes.plugins import MinimalPlugin


class Tasks:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def run_blocking(self, name: str, fn: object, *args: object) -> object:
        self.calls.append(("run_blocking", name))
        return "task"

    def spawn(self, name: str, coro: object) -> object:
        self.calls.append(("spawn", name))
        return "task"


def services(tasks: Tasks | None = None) -> PluginServices:
    return PluginServices(
        tasks=tasks or Tasks(),  # type: ignore[arg-type]
        calendars=lambda: None,  # type: ignore[arg-type,return-value]
        writer=LogWriter(Redactor(), stdout=io.StringIO(), stderr=io.StringIO()),
        clock=FakeClock(0),
        calendar_dir=Path("calendars"),
        state_dir=Path("state"),
        out_dir=Path("out"),
        project_dir=None,
        bar_store=MemoryBarStore(),
        cache_dir=Path("cache"),
        env=EnvView({}, {}),
    )


def test_version_and_tag_rule() -> None:
    assert PLUGIN_API_VERSION == 1
    assert StrategyPlugin.api_version == 1
    for good in ("20261012T0945", "a", "A.b-c_d", "x" * 48):
        assert TAG_RE.fullmatch(good)
    for bad in ("", "a:b", "x" * 49, "a b", "é"):
        assert TAG_RE.fullmatch(bad) is None


def test_needs_is_abstract_and_other_hooks_are_no_ops() -> None:
    with pytest.raises(TypeError, match="needs"):
        StrategyPlugin("p", {}, services())  # type: ignore[abstract]
    plugin = MinimalPlugin("p", {"k": 1}, services())
    assert plugin.plugin_id == "p"
    assert plugin.config == {"k": 1}
    assert plugin.needs() == DataNeeds(bars=(BarNeed("MNQ"),), hours="eth")
    ctx = object()
    assert plugin.on_start(ctx) == ()  # type: ignore[arg-type]
    assert plugin.on_wake(0, ctx) == ()  # type: ignore[arg-type]
    assert plugin.next_wake(0) is None
    assert plugin.snapshot() is None
    plugin.restore({"a": 1})  # accepts anything, returns nothing
    plugin.on_stop(ctx)  # type: ignore[arg-type]


def test_data_needs_defaults() -> None:
    needs = DataNeeds(bars=(BarNeed("MNQ"), BarNeed("MES")), hours="eth")
    assert needs.instruments == frozenset({"MNQ", "MES"})
    assert (needs.skylit, needs.quotes, needs.brokerage, needs.hold_through_break) == (
        None,
        False,
        "host",
        False,
    )
    assert BarNeed("MNQ").interval_s == 60


def test_services_delegate_background_work() -> None:
    tasks = Tasks()
    s = services(tasks)

    async def noop() -> None:
        return None

    coro = noop()
    s.spawn("n", coro)
    coro.close()
    s.run_blocking("job", print)
    assert tasks.calls == [("spawn", "n"), ("run_blocking", "job")]


def test_thread_confinement_rule_is_documented() -> None:
    for doc in (api.__doc__, StrategyPlugin.__doc__, PluginServices.run_blocking.__doc__):
        assert doc is not None
    assert "Thread confinement" in (api.__doc__ or "")
    assert "DataCache" in (PluginServices.run_blocking.__doc__ or "")


def test_reexports_so_plugins_never_name_vendor_modules() -> None:
    assert plugins.Bar is engine_types.Bar
    assert plugins.Snapshot is engine_types.Snapshot
    assert plugins.DarkPoolPrint is engine_types.DarkPoolPrint
    assert plugins.Fill is engine_types.Fill
    assert plugins.Trade is engine_types.Trade
    assert plugins.Quote is api.Quote
    assert plugins.globex_day_at is dayclock.globex_day_at
    assert plugins.GlobexDay is dayclock.GlobexDay
    assert plugins.next_globex_day is dayclock.next_globex_day
    assert plugins.globex_day is dayclock.globex_day
    assert plugins.BrokerTerms is api.BrokerTerms
    assert plugins.PaperRisk is api.PaperRisk
    assert plugins.PaperFills is api.PaperFills
    assert plugins.ModifyStop is api.ModifyStop
    for name in plugins.__all__:
        assert hasattr(plugins, name)


def test_risk_and_broker_terms_are_frozen_exported_and_default_to_none() -> None:
    from dataclasses import FrozenInstanceError
    from decimal import Decimal
    from types import MappingProxyType
    from typing import get_args

    assert plugins.BrokerTerms is api.BrokerTerms
    assert plugins.PaperRisk is api.PaperRisk
    assert plugins.ModifyStop is api.ModifyStop
    for name in ("BrokerTerms", "PaperRisk", "ModifyStop"):
        assert name in plugins.__all__
        assert name in api.__all__
    assert api.ModifyStop in get_args(api.Intent.__value__)
    risk = api.PaperRisk(2, 1, None, Decimal("200"))
    terms = api.BrokerTerms(
        1, 0, "at_stop", MappingProxyType({"MNQ": Decimal("1.24")}), 2, 1, None, None
    )
    with pytest.raises(FrozenInstanceError):
        risk.max_contracts = 3  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        terms.slippage_ticks = 3  # type: ignore[misc]
    with pytest.raises(TypeError):
        terms.commission_rt["MNQ"] = Decimal("0")  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        api.ModifyStop("t1", 100).stop_price = 101  # type: ignore[misc]
    s = services()
    assert (s.fills, s.risk, s.broker_terms) == (None, None, None)
