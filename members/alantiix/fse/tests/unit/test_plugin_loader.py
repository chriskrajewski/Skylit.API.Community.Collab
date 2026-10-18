"""Plugin loading (design 3.5) and needs validation (design 3.6)."""

from __future__ import annotations

import sys
from datetime import time
from pathlib import Path
from typing import Any

import pytest

from fse.config.schema.data import HeatmapViewConfig
from fse.live.plugins import BarNeed, DataNeeds, SkylitNeed
from fse.live.plugins.loader import (
    PluginLoadError,
    load_plugin_classes,
    needs_problems,
    resolve_plugin_class,
)
from fse.pit.market_view import heatmap_view
from tests.fakes.dataplane import PROJECT_DIR, host_entry, runner_config
from tests.fakes.plugins import MinimalPlugin

VIEW_ID = heatmap_view(HeatmapViewConfig()).view_id()


def test_loads_a_plugin_from_the_project() -> None:
    cls = resolve_plugin_class("p1", "tests.fakes.plugins:MinimalPlugin", project_dir=PROJECT_DIR)
    assert cls is MinimalPlugin


@pytest.mark.parametrize(
    ("module", "message"),
    [
        ("tests.fakes.plugins.MinimalPlugin", "is not 'package.module:Class'"),
        ("tests.fakes.plugins:Minimal Plugin", "is not 'package.module:Class'"),
        ("tests.fakes.no_such_module:X", "cannot import"),
        ("tests.fakes.plugins:NotAPlugin", "is not a StrategyPlugin subclass"),
        ("tests.fakes.plugins:Missing", "is not a StrategyPlugin subclass"),
        ("tests.fakes.plugins:OldApiPlugin", "has api_version 0"),
    ],
)
def test_load_failures_name_the_plugin(module: str, message: str) -> None:
    with pytest.raises(PluginLoadError) as info:
        resolve_plugin_class("p1", module, project_dir=PROJECT_DIR)
    assert message in str(info.value)
    assert "plugin 'p1'" in str(info.value)
    assert info.value.exit_code == 2


def test_a_module_outside_the_project_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "outside_plugin_mod.py").write_text(
        "from tests.fakes.plugins import MinimalPlugin as Plugin\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(PluginLoadError, match="outside the Project folder"):
        resolve_plugin_class("p1", "outside_plugin_mod:Plugin", project_dir=PROJECT_DIR)
    sys.modules.pop("outside_plugin_mod", None)


def test_the_project_dir_goes_on_sys_path_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "proj"
    (project / "pkgx").mkdir(parents=True)
    (project / "pkgx" / "__init__.py").write_text("")
    (project / "pkgx" / "mod.py").write_text(
        "from tests.fakes.plugins import MinimalPlugin\nclass Local(MinimalPlugin):\n    pass\n"
    )
    monkeypatch.setattr(sys, "path", list(sys.path))
    first = resolve_plugin_class("p1", "pkgx.mod:Local", project_dir=project)
    second = resolve_plugin_class("p2", "pkgx.mod:Local", project_dir=project)
    assert first is second
    assert sys.path.count(str(project)) == 1
    sys.modules.pop("pkgx.mod", None)
    sys.modules.pop("pkgx", None)


def test_load_plugin_classes_lists_every_problem() -> None:
    cfg = runner_config(
        [
            host_entry("a", module="tests.fakes.plugins:MinimalPlugin"),
            host_entry("b", module="tests.fakes.plugins:NotAPlugin"),
            host_entry("c", module="bad"),
            host_entry("d", module="bad", enabled=False),
        ]
    )
    with pytest.raises(PluginLoadError) as info:
        load_plugin_classes(cfg, project_dir=PROJECT_DIR)
    assert len(info.value.problems) == 2
    assert "plugin 'b'" in info.value.problems[0]
    assert "plugin 'c'" in info.value.problems[1]


def sky(**kw: Any) -> SkylitNeed:
    args: dict[str, Any] = {
        "symbols": ("SPY",),
        "view_id": VIEW_ID,
        "mode": "stream",
        "refresh_interval_s": 15,
        "window": (time(9, 30), time(16, 0)),
    }
    args.update(kw)
    return SkylitNeed(**args)


def test_matching_needs_have_no_problems() -> None:
    cfg = runner_config([host_entry("p1")])
    needs = DataNeeds(bars=(BarNeed("MNQ"),), hours="eth", skylit=sky())
    assert needs_problems(cfg.plugins[0], needs, cfg.data_plane) == []


def test_every_needs_mismatch_is_listed() -> None:
    cfg = runner_config([host_entry("p1", fills=None, risk=None)])
    needs = DataNeeds(
        bars=(BarNeed("MNQ"), BarNeed("ES", 300)),
        hours="eth",
        skylit=sky(
            symbols=("SPY", "SPX"),
            view_id="other",
            mode="polling",
            refresh_interval_s=30,
            window=(time(8, 0), time(16, 0)),
            dark_pool_tickers=("SPY",),
        ),
    )
    problems = needs_problems(cfg.plugins[0], needs, cfg.data_plane)
    text = "\n".join(problems)
    for part in (
        "'ES' are not in data_plane.instruments",
        "bar interval 300 s",
        "Skylit symbols ['SPX']",
        "view 'other'",
        "mode polling",
        "refresh 30 s",
        "window 08:00-16:00",
        "dark_pool.enabled is false",
        "dark-pool tickers ['SPY']",
        "needs a risk block",
        "commission_rt has no entry for MNQ",
    ):
        assert part in text, part
    assert len(problems) == 12  # ES also lacks a commission


def test_self_brokered_rules() -> None:
    cfg = runner_config([host_entry("p1")])
    needs = DataNeeds(bars=(BarNeed("MNQ"),), hours="rth", brokerage="self")
    assert needs_problems(cfg.plugins[0], needs, cfg.data_plane) == [
        "plugin 'p1': a self-brokered plugin must not set fills or risk"
    ]
    cfg = runner_config([{"id": "p1", "module": "a.b:C"}])
    assert needs_problems(cfg.plugins[0], needs, cfg.data_plane) == []


def test_skylit_need_without_skylit() -> None:
    cfg = runner_config([host_entry("p1")], data_plane={"skylit": {"enabled": False}})
    needs = DataNeeds(bars=(BarNeed("MNQ"),), hours="eth", skylit=sky())
    assert needs_problems(cfg.plugins[0], needs, cfg.data_plane) == [
        "plugin 'p1': needs Skylit data but data_plane.skylit.enabled is false"
    ]
