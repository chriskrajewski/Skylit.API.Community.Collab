"""The runner YAML schema (design 11.1): defaults, every rule, and all problems at once."""

from __future__ import annotations

from datetime import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from fse.live.host.config import (
    RunnerConfig,
    RunnerConfigError,
    load_runner_config,
    parse_runner_config,
)

EXAMPLE: dict[str, Any] = {
    "id": "alpha",
    "module": "research.example.plugin:ExamplePlugin",
    "config": {"late_bar_s": 20, "anything": ["opaque"]},
    "fills": {
        "trade_through_ticks": 1,
        "slippage_ticks": 0,
        "stop_fill": "at_stop",
        "commission_rt": {"MNQ": "1.24"},
    },
    "risk": {
        "max_contracts": 16,
        "max_open_trades": 1,
        "max_daily_loss": "3000.00",
        "max_trades_per_day": 15,
    },
}


def parse(doc: dict[str, Any]) -> RunnerConfig:
    return parse_runner_config(doc, Path("runner.yaml"))


def problems(doc: dict[str, Any]) -> list[str]:
    with pytest.raises(RunnerConfigError) as info:
        parse(doc)
    assert info.value.exit_code == 2
    return info.value.problems


def test_defaults() -> None:
    cfg = parse({"version": 1, "plugins": [EXAMPLE]})
    dp = cfg.data_plane
    assert dp.instruments == ("MNQ", "MES")
    px = dp.projectx
    assert (px.transport, px.subscription, px.poll_interval_s, px.hot_poll_interval_s) == (
        "auto",
        "auto",
        5,
        1,
    )
    assert (px.record_quotes, px.backfill_sessions, px.expected_contracts) == ("sampled_1s", 20, {})
    assert px.rtc_url == "https://rtc.topstepx.com/hubs/market"
    sky = dp.skylit
    assert (sky.enabled, sky.mode, sky.refresh_interval_s, sky.symbols) == (
        True,
        "stream",
        15,
        ("QQQ", "SPY"),
    )
    assert sky.window.pair() == (time(9, 0), time(16, 15))
    assert sky.dark_pool.enabled is False
    entry = cfg.plugins[0]
    assert entry.enabled
    assert entry.hold_through_break
    assert entry.config == {"late_bar_s": 20, "anything": ["opaque"]}
    assert entry.risk is not None
    assert entry.risk.max_daily_loss == Decimal("3000.00")
    assert cfg.paths.state_dir is None


def test_fills_block_gives_half_the_round_turn_per_side() -> None:
    cfg = parse({"version": 1, "plugins": [EXAMPLE]})
    fills = cfg.plugins[0].fills
    assert fills is not None
    fc = fills.fills_config()
    costs = fc.costs_for("MNQ")
    assert costs is not None
    assert costs.commission == Decimal("0.62")
    assert costs.exchange_fee == Decimal("0.00")
    assert (fc.trade_through_ticks, fc.slippage_ticks) == (1, 0)
    assert fills.stop_fill == "at_stop"


def test_paths_expand_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = parse({"version": 1, "paths": {"state_dir": "~/state"}, "plugins": [EXAMPLE]})
    assert cfg.paths.state_dir == tmp_path / "state"


def test_every_problem_is_listed_with_its_path() -> None:
    doc: dict[str, Any] = {
        "version": 2,
        "extra": True,
        "data_plane": {
            "instruments": ["MNQ", "XYZ", "MNQ"],
            "projectx": {"poll_interval_s": 5, "hot_poll_interval_s": 5, "rtc_url": "http://x"},
            "skylit": {"window": {"start": "16:00", "end": "09:00"}, "refresh_interval_s": 1},
        },
        "plugins": [
            {**EXAMPLE, "id": "Bad-Id"},
            {**EXAMPLE, "fills": {"commission_rt": {"MNQ": "1.25"}}},
            {**EXAMPLE, "risk": {"max_contracts": 0}},
        ],
    }
    found = problems(doc)
    text = "\n".join(found)
    for part in (
        "version:",
        "extra:",
        "data_plane.instruments[1]",
        "data_plane.projectx.rtc_url",
        "data_plane.skylit.window",
        "data_plane.skylit.refresh_interval_s",
        "plugins[0].id",
        "plugins[1].fills.commission_rt",
        "plugins[2].risk.max_contracts",
    ):
        assert part in text, part
    assert len(found) >= 9


@pytest.mark.parametrize(
    ("doc", "part"),
    [
        ({"version": 1, "plugins": []}, "plugins"),
        ({"version": 1, "plugins": [{**EXAMPLE, "enabled": False}]}, "no plugin is enabled"),
        ({"version": 1, "plugins": [EXAMPLE, EXAMPLE]}, "listed more than once"),
        (
            {
                "version": 1,
                "data_plane": {"projectx": {"hot_poll_interval_s": 5}},
                "plugins": [EXAMPLE],
            },
            "hot_poll_interval_s (5) must be below poll_interval_s (5)",
        ),
        (
            {"version": 1, "plugins": [{**EXAMPLE, "fills": {"commission_rt": {"MNQ": "51.00"}}}]},
            "from 0.00 to 50.00",
        ),
        (
            {
                "version": 1,
                "plugins": [{**EXAMPLE, "risk": {"max_contracts": 1, "max_daily_loss": "0"}}],
            },
            "plugins[0].risk.max_daily_loss",
        ),
        ({"version": 1, "data_plane": {"instruments": "MNQ"}, "plugins": [EXAMPLE]}, "instruments"),
        ({"version": 1, "plugins": [{**EXAMPLE, "fills": {"stop_fill": "x"}}]}, "stop_fill"),
        (
            {"version": 1, "data_plane": {"skylit": {"symbols": ["XXX"]}}, "plugins": [EXAMPLE]},
            "symbols",
        ),
    ],
)
def test_rules(doc: dict[str, Any], part: str) -> None:
    assert part in "\n".join(problems(doc))


def test_file_errors(tmp_path: Path) -> None:
    with pytest.raises(RunnerConfigError, match="cannot read"):
        load_runner_config(tmp_path / "missing.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: [1\n")
    with pytest.raises(RunnerConfigError, match="invalid YAML"):
        load_runner_config(bad)
    bad.write_text("- 1\n")
    with pytest.raises(RunnerConfigError, match="must be a mapping"):
        load_runner_config(bad)


def test_loads_a_yaml_file(tmp_path: Path) -> None:
    path = tmp_path / "runner.yaml"
    path.write_text(
        "version: 1\n"
        "data_plane:\n"
        "  instruments: [MNQ]\n"
        "  skylit:\n"
        "    window: {start: '09:30', end: '16:00'}\n"
        "    dark_pool: {enabled: true, tickers: [SPY]}\n"
        "plugins:\n"
        "  - id: alpha\n"
        "    module: research.example.plugin:ExamplePlugin\n"
        "    fills: {stop_fill: at_stop, commission_rt: {MNQ: '1.24'}}\n"
        "    risk: {max_contracts: 16}\n"
        "  - id: playbook\n"
        "    module: fse.live.plugins.playbook:PlaybookPlugin\n"
        "    enabled: false\n"
        "    config: {strategy_config: configs/playbook_baseline.yaml}\n"
    )
    cfg = load_runner_config(path)
    assert [p.id for p in cfg.enabled_plugins] == ["alpha"]
    assert cfg.data_plane.skylit.dark_pool.tickers == ("SPY",)
