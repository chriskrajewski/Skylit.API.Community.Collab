"""Rules replay stays on the live host path and spends its time on bars, not durability.

``--lag-s`` is virtual receipt delay. The slow run is the old host path: a full
settle on every bar, a ``plugin.json`` encode per bar, and a parquet rewrite
per bar. The fast run is what ``fse backtest-plugin`` does now. Fills, the
final book (ignoring ``seq``) and the stored bars match.
"""

from __future__ import annotations

import io
import json
from datetime import time
from pathlib import Path
from time import monotonic

import pytest
import yaml

from fse.data.cache import DataCache
from fse.live.host import replay as replay_mod
from fse.live.host.barstore import read_day
from fse.live.host.config import load_runner_config
from fse.live.host.replay import ReplayResult, run_replay
from fse.live.plugins.loader import load_plugin_classes
from fse.logio import LogWriter, Redactor
from fse.strategy.spec import example_spec
from fse.timekit import ny_datetime
from tests.fakes.dataplane import PROJECT_DIR, make_bar
from tests.unit.test_backtest_plugin import WED, history_days, session_bars

_SPEEDUP: float = 4.0


def _cache(root: Path) -> None:
    """One Globex session plus two history days, with the example-spec spike."""
    days = [*history_days(WED, 2), WED]
    script = [(80000, 80000, 80000, 80000)] * 30
    script.append((80000, 80120, 79990, 80080))
    script.append((80040, 80050, 80005, 80030))
    with DataCache(root) as store:
        for day in days:
            bars = []
            rth = 0
            for bar in session_bars(day, base=80000):
                ny = ny_datetime(bar.open_ns)
                if day == WED and time(9, 30) <= ny.time() < time(16, 0) and rth < len(script):
                    o, h, low, c = script[rth]
                    rth += 1
                    bars.append(make_bar("MNQ", bar.open_ns, o, h, low, c))
                else:
                    bars.append(make_bar("MNQ", bar.open_ns, 80000, 80000, 80000, 80000))
            store.bars.write_session("MNQ", 60, day, bars, contract="MNQZ6", source="projectx")


def _config(path: Path) -> None:
    spec = example_spec()
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "data_plane": {"instruments": ["MNQ"], "skylit": {"enabled": False}},
                "plugins": [
                    {
                        "id": "rules",
                        "module": "fse.live.plugins.rules:RulesPlugin",
                        "enabled": True,
                        "config": spec.model_dump(mode="json"),
                        "fills": {
                            "trade_through_ticks": 0,
                            "slippage_ticks": 0,
                            "stop_fill": "at_stop",
                            "commission_rt": {"MNQ": "0.00"},
                        },
                        "risk": {"max_contracts": 8, "max_open_trades": 4},
                        "hold_through_break": False,
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _book(work: Path) -> object:
    doc = json.loads((work / "live-state" / "plugins" / "rules" / "plugin.json").read_text())
    doc.pop("seq")
    return doc


def _run(
    cache: Path,
    cfg: Path,
    work: Path,
    *,
    quiet: int,
    coalesce_state: bool,
    defer_bar_files: bool,
) -> tuple[float, ReplayResult]:
    replay_mod._QUIET = quiet
    loaded = load_runner_config(cfg)
    classes = load_plugin_classes(loaded, project_dir=PROJECT_DIR)
    writer = LogWriter(Redactor(), stdout=io.StringIO(), stderr=io.StringIO())
    started = monotonic()
    result = run_replay(
        loaded,
        classes,
        first=WED,
        last=WED,
        work=work,
        source_cache=cache,
        recordings_root=work / "none",
        calendar_dir=PROJECT_DIR / "calendars",
        project_dir=PROJECT_DIR,
        writer=writer,
        history=2,
        lag_s=2,
        coalesce_state=coalesce_state,
        defer_bar_files=defer_bar_files,
    )
    return monotonic() - started, result


def test_rules_replay_matches_the_per_bar_host_and_is_much_faster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    _cache(cache)
    cfg = tmp_path / "runner.yaml"
    _config(cfg)
    fast_quiet = replay_mod._QUIET
    monkeypatch.setattr(replay_mod, "_QUIET", fast_quiet)
    slow_s, slow = _run(
        cache,
        cfg,
        tmp_path / "slow",
        quiet=200,
        coalesce_state=False,
        defer_bar_files=False,
    )
    fast_s, fast = _run(
        cache,
        cfg,
        tmp_path / "fast",
        quiet=fast_quiet,
        coalesce_state=True,
        defer_bar_files=True,
    )
    bars = len(session_bars(WED))
    assert slow.exit_code == 0
    assert fast.exit_code == 0
    assert slow.trades
    assert fast.trades == slow.trades
    assert _book(tmp_path / "fast") == _book(tmp_path / "slow")
    assert read_day(tmp_path / "fast" / "bars", "MNQ", WED) == read_day(
        tmp_path / "slow" / "bars", "MNQ", WED
    )
    speedup = slow_s / fast_s
    assert speedup >= _SPEEDUP, (
        f"speedup {speedup:.1f}x ({bars / fast_s:.0f} vs {bars / slow_s:.0f} bars/s)"
    )
    # 2s of --lag-s per bar would be most of an hour. The fast run is one session.
    assert fast_s < 15
