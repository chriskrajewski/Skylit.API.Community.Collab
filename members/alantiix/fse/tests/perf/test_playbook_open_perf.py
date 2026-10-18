"""``PlaybookPlugin`` session-open latency (design 16.3): p95 < 250 ms.

Run with ``python -m pytest -m perf tests/perf/test_playbook_open_perf.py``.
The session inputs are prepared a minute before the open (``prep_lead_s:
60``, the Regime medians file already built), so the measured ``on_wake`` is
the hook-side work only: the ``LiveSession`` (its small state files), the
paper-only check, the scheduler, the start log and the catch-up.
"""

from __future__ import annotations

import io
import time
from collections.abc import Sequence
from datetime import time as wall_time
from pathlib import Path
from typing import Any, Final

import pytest
import yaml

from fse.calendars import load_calendars
from fse.commands.paper import session_inputs
from fse.config.schema import StrategyConfig
from fse.live.host.config import parse_runner_config
from fse.live.host.host import HostPaths, HostResources, InlineBlockingRunner, PluginHost
from fse.live.plugins.api import PluginContext
from fse.live.plugins.playbook import PlaybookPlugin
from fse.logio import LogWriter, Redactor
from fse.secrets.env import EnvView
from fse.timekit import Instant, ny_instant
from tests.fakes.clock import FakeClock
from tests.fakes.dataplane import PROJECT_DIR, FakeDataPlane
from tests.fakes.playbook import BASE_CFG, FUNNEL, make_world, own_calendars, pinned_plugin

RUNS: Final = 20
BUDGET_NS: Final = 250_000_000
DAY: Final = FUNNEL.sessions[0].session


def strategy() -> StrategyConfig:
    data = BASE_CFG.model_dump(mode="python", by_alias=True)
    data["live"]["run_window"] = {"start": "09:28", "end": "09:40"}
    data["notify"]["sinks"] = ["file"]
    data["notify"]["premarket"] = "09:28"
    return StrategyConfig.model_validate(data)


@pytest.mark.perf
@pytest.mark.asyncio
async def test_playbook_session_open_p95_under_250_ms(tmp_path: Path) -> None:
    world = make_world(tmp_path / "world")
    cfg = strategy()
    session_inputs(cfg, own_calendars(world, cfg, DAY), DAY, world.cache)  # builds the medians
    path = tmp_path / "strategy.yaml"
    path.write_text(yaml.safe_dump(cfg.model_dump(mode="json", by_alias=True)), "utf-8")
    latencies: list[int] = []

    class Timed(PlaybookPlugin):
        def on_wake(self, now: Instant, ctx: PluginContext) -> Sequence[Any]:
            opened = self.session is not None
            started = time.perf_counter_ns()
            out = super().on_wake(now, ctx)
            if not opened and self.session is not None:
                latencies.append(time.perf_counter_ns() - started)
            return out

    for i in range(RUNS):
        root = tmp_path / f"run-{i}"
        doc = {
            "version": 1,
            "data_plane": {
                "instruments": ["MES", "MNQ"],
                "projectx": {"transport": "polling", "backfill_sessions": 0},
                "skylit": {
                    "mode": "polling",
                    "refresh_interval_s": 5,
                    "symbols": ["SPX", "QQQ"],
                    "window": {"start": "09:28", "end": "09:40"},
                },
            },
            "plugins": [
                {
                    "id": "playbook",
                    "module": "fse.live.plugins.playbook:PlaybookPlugin",
                    "config": {
                        "strategy_config": str(path),
                        "out_root": str(root / "runs"),
                        "open_lead_s": 0,
                        "prep_lead_s": 60,
                    },
                }
            ],
        }
        clock = FakeClock(ny_instant(DAY, wall_time(9, 20)))
        host = PluginHost(
            parse_runner_config(doc, root / "runner.yaml"),
            {"playbook": pinned_plugin(Timed)},
            dataplane=FakeDataPlane(clock),
            clock=clock,
            writer=LogWriter(Redactor(), stdout=io.StringIO(), stderr=io.StringIO()),
            paths=HostPaths(
                state_root=root / "live-state",
                run_dir=root / "run",
                cache_dir=world.cache,
                calendar_dir=world.calendar_dir,
                project_dir=PROJECT_DIR,
                recordings_root=root / "rec",
            ),
            calendars=load_calendars(world.calendar_dir, DAY, DAY),
            resources=HostResources(env=EnvView({}, {})),
            blocking=InlineBlockingRunner(),
        )
        assert await clock.run(host.run(ny_instant(DAY, wall_time(9, 28, 1)))) == 0
    assert len(latencies) == RUNS
    p95 = sorted(latencies)[int(0.95 * (RUNS - 1))]
    assert p95 < BUDGET_NS, f"p95 {p95 / 1e6:.1f} ms"
