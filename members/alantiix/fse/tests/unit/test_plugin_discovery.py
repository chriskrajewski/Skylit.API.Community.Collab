"""Plugin self-description and discovery (``StrategyPlugin`` metadata, ``discover_plugins``).

Every plugin must work with every fse feature on day one: the console lists it,
builds its settings form from ``config_model``, and explains every setting from
the model's field docstrings. These tests fail when a plugin or a runner block
adds a setting without saying what it does.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from fse.live.host.config import DataPlaneConfig, FillsBlock, PluginEntry, RiskBlock
from fse.live.plugins.api import StrategyPlugin
from fse.live.plugins.loader import discover_plugins, resolve_plugin_class

PROJECT_DIR = Path(__file__).resolve().parents[2]

EXPECTED = {
    "fse.live.plugins.playbook:PlaybookPlugin",
    "fse.live.plugins.template:TemplatePlugin",
}


def undocumented(model: type[BaseModel]) -> list[str]:
    """Every property in the model's JSON schema (nested models included) with no description."""
    schema = model.model_json_schema()
    out: list[str] = []

    def walk(node: dict[str, Any], where: str) -> None:
        for name, prop in (node.get("properties") or {}).items():
            if not prop.get("description"):
                out.append(f"{where}.{name}")

    walk(schema, model.__name__)
    for name, node in (schema.get("$defs") or {}).items():
        walk(node, name)
    return out


def test_every_plugin_is_discovered_once_under_its_yaml_module() -> None:
    infos, problems = discover_plugins(PROJECT_DIR)
    assert problems == []
    modules = [i.module for i in infos]
    assert len(modules) == len(set(modules))
    assert set(modules) >= EXPECTED
    for info in infos:
        # the discovered path is what a runner YAML names, and it loads
        assert resolve_plugin_class("x", info.module, project_dir=PROJECT_DIR) is info.cls


@pytest.mark.parametrize("module", sorted(EXPECTED))
def test_every_plugin_describes_itself(module: str) -> None:
    cls = resolve_plugin_class("x", module, project_dir=PROJECT_DIR)
    assert cls.label, f"{module} has no label"
    assert len(cls.description) > 20, f"{module} has no description"
    assert cls.brokerage in ("host", "self")
    assert cls.config_model is not None, f"{module} has no config_model"
    assert undocumented(cls.config_model) == []


@pytest.mark.parametrize("model", [FillsBlock, RiskBlock, DataPlaneConfig, PluginEntry])
def test_every_runner_setting_is_explained(model: type[BaseModel]) -> None:
    assert undocumented(model) == []


def test_validate_config_reports_problems_by_field() -> None:
    cls = resolve_plugin_class(
        "x", "fse.live.plugins.template:TemplatePlugin", project_dir=PROJECT_DIR
    )
    assert cls.validate_config({"entry_time": "10:00"}) == []
    problems = cls.validate_config({"qty": 0, "nope": 2})
    assert any(p.startswith("config.qty:") for p in problems)
    assert any(p.startswith("config.nope:") for p in problems)


def test_a_plugin_without_metadata_still_works() -> None:
    class Bare(StrategyPlugin):
        def needs(self) -> Any:
            raise NotImplementedError

    assert Bare.display_label() == "Bare"
    assert Bare.validate_config({"anything": 1}) == []
