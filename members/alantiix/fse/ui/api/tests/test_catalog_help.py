"""Every workflow option is explained, and each path option picks the right configs."""

from __future__ import annotations

import sys
from pathlib import Path

from fse_ui.catalog import Catalog, config_kind

PROJECT = Path(__file__).resolve().parents[3]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))  # the research CLIs import from the Project folder


def test_every_option_of_every_command_has_help() -> None:
    cat = Catalog()
    assert cat.errors() == {}
    missing = [
        f"{c.id} {a['flag'] or a['dest']}"
        for c in cat.commands()
        for a in c.args
        if not a["help"]
    ]
    assert missing == [], f"add help= to: {missing}"
    assert [c.id for c in cat.commands() if not c.help] == []


def test_the_plugin_backtest_and_a_research_program_are_in_the_catalog() -> None:
    ids = {c.id for c in Catalog().commands()}
    assert "fse backtest-plugin" in ids
    assert "research.example.run backtest" in ids


def test_config_kind_per_program() -> None:
    assert config_kind("fse", ("backtest-plugin",), "config") == {"kind": "runner"}
    assert config_kind("fse", ("backtest",), "config") == {"kind": "strategy"}
    assert config_kind("fse", ("experiment", "sweep"), "sweep") == {"kind": "sweep"}
    assert config_kind("research.example.run", ("backtest",), "config") == {
        "kind": "research",
        "prefix": "example-",
    }
    assert config_kind("research.example.check", (), "config") is None
    assert config_kind("fse", ("backtest",), "out") is None
    spec = Catalog().get("research.example.run backtest")
    cfg = next(a for a in spec.args if a["dest"] == "config")
    assert cfg["config_kind"] == {"kind": "research", "prefix": "example-"}


def test_the_plugin_backtest_is_batch_on_aws() -> None:
    assert Catalog(target="aws").get("fse backtest-plugin").placement == "batch"
