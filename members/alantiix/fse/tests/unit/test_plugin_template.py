"""The example plugin ``fse.live.plugins.template``: loads through the loader, runs on the
fake data plane, and the shipped ``configs/runner.yaml`` carries it enabled."""

from __future__ import annotations

import asyncio
from datetime import date
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from fse.live.host.config import load_runner_config
from fse.live.host.host import BarsEvent, PluginHost
from fse.live.plugins.loader import load_plugin_classes, resolve_plugin_class
from fse.live.plugins.template import TemplatePlugin
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND
from tests.fakes.clock import FakeClock
from tests.fakes.dataplane import (
    PROJECT_DIR,
    FakeDataPlane,
    host_entry,
    make_bar,
    make_host,
    ny,
    read_jsonl,
    runner_config,
)

WED = date(2026, 10, 14)
MODULE = "fse.live.plugins.template:TemplatePlugin"


def entry(**config: Any) -> dict[str, Any]:
    base = {"instrument": "MNQ", "entry_time": "10:00", "direction": "long", "qty": 1,
            "stop_ticks": 40, "target_ticks": 40, "flatten_time": "10:20", "warmup_bars": 3}
    base.update(config)
    return host_entry("template", module=MODULE, config=base,
                      risk={"max_contracts": 1, "max_open_trades": 1})


def script(prices: list[int], first: int) -> list[tuple[int, BarsEvent]]:
    """One bar per minute from ``first``; bar ``i`` opens at ``prices[i]`` and closes at
    ``prices[i + 1]``; received 2 s after its close."""
    out: list[tuple[int, BarsEvent]] = []
    for i, (o, c) in enumerate(pairwise(prices)):
        bar = make_bar("MNQ", first + i * NS_PER_MINUTE, o, max(o, c) + 1, min(o, c) - 1, c)
        received = bar.close_ns + 2 * NS_PER_SECOND
        out.append((received, BarsEvent((bar,), received)))
    return out


def run(tmp_path: Path, prices: list[int], until: int, **config: Any) -> PluginHost:
    cfg = runner_config([entry(**config)])
    classes = load_plugin_classes(cfg, project_dir=PROJECT_DIR)  # the YAML module path
    assert classes == {"template": TemplatePlugin}
    first = ny(WED, 9, 50)
    clock = FakeClock(first)
    plane = FakeDataPlane(clock, script(prices, first))
    host = make_host(tmp_path, classes, [entry(**config)], clock, plane)
    assert asyncio.run(clock.run(host.run(until_ns=until))) == 0, host.faults()
    return host


def log(tmp_path: Path) -> list[dict[str, Any]]:
    return read_jsonl(tmp_path / "run" / "plugins" / "template" / "plugin_log.jsonl")


def statuses(tmp_path: Path) -> list[str]:
    return [e["status"] for e in log(tmp_path) if e["kind"] == "order_update"]


def test_template_enters_at_entry_time_and_hits_target(tmp_path: Path) -> None:
    # flat until 10:00, then up 10 ticks a minute: the 40-tick target fills
    prices = [80_000] * 11 + [80_000 + 10 * k for k in range(1, 15)]
    run(tmp_path, prices, ny(WED, 10, 30))
    assert statuses(tmp_path) == ["accepted", "filled_entry", "filled_exit", "closed"]
    closed = next(e for e in log(tmp_path) if e.get("status") == "closed")
    assert closed["tag"] == "tpl20261014"
    assert float(closed["net"]) > 0
    assert log(tmp_path)[0]["kind"] == "template_start"


def test_template_flattens_at_flatten_time(tmp_path: Path) -> None:
    prices = [80_000] * 40  # never reaches the stop or the target
    host = run(tmp_path, prices, ny(WED, 10, 28), target_ticks=400, stop_ticks=400)
    assert statuses(tmp_path)[:2] == ["accepted", "filled_entry"]
    assert statuses(tmp_path)[-1] == "closed"
    plugin = host.plugin("template")
    assert isinstance(plugin, TemplatePlugin)
    assert plugin.snapshot() == {"version": 1, "day": "2026-10-14", "entered": True,
                                 "exited": True}


def test_template_state_round_trip_and_bad_state(tmp_path: Path) -> None:
    host = run(tmp_path, [80_000] * 5, ny(WED, 9, 54))
    plugin = host.plugin("template")
    assert isinstance(plugin, TemplatePlugin)
    state = plugin.snapshot()
    plugin.restore(state)
    assert plugin.snapshot() == state
    with pytest.raises(ValueError, match="version 1"):
        plugin.restore({"version": 2})


def test_template_rejects_unknown_config_keys() -> None:
    cls = resolve_plugin_class("template", MODULE, project_dir=PROJECT_DIR)
    with pytest.raises(ValueError, match="bad config"):
        cls("template", {"bogus": 1}, None)  # type: ignore[arg-type]


def test_shipped_runner_yaml_has_the_template_enabled() -> None:
    cfg = load_runner_config(PROJECT_DIR / "configs" / "runner.yaml")
    tpl = next(p for p in cfg.plugins if p.id == "template")
    assert tpl.module == MODULE
    assert tpl.enabled is True
    TemplatePlugin("template", tpl.config, None)  # type: ignore[arg-type]  # config is valid
