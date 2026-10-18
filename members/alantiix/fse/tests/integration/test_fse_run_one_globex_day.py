"""``fse run`` over one Globex day, end to end, in virtual time (design 16.2, 10.2-10.3).

A fake clock runs ``fse.commands.run``'s async core from Sunday 2026-10-11
18:00 NY to ``--until`` Monday 18:10 with the template and Playbook plugins
enabled. Everything behind the data plane is a fake:

- ProjectX REST (``loginKey``, ``retrieveBars``) behind the suite's respx
  router (``assert_all_mocked``), with one synthetic bar per minute;
- the ProjectX market hub through an injected connect function (no socket);
- the Skylit stream (``GET /v1/stream``, one snapshot per symbol per
  connection), ``GET /v1/account`` and ``GET /v1/gex/levels`` behind respx.

The respx routes are the network allow-list (design 10.3): any other request
fails the test, and the test checks that only those routes were called.
``BrokerAdapter.__init__`` and ``BrokerSafety.__init__`` raise for the whole
run (design 10.2, runtime check): nothing may construct a broker adapter.

Checked: exit 0 at ``--until``; the data-plane recording and the bar store
for Monday; the template plugin's state; the Playbook's run directory (decision log,
live log, cards), its own recording and its state files; no plugin fault.
"""

from __future__ import annotations

import argparse
import io
import json
from collections.abc import Callable
from datetime import date, time
from pathlib import Path
from typing import Any, Final

import httpx
import pytest
import respx
import yaml

from fse.calendars import load_calendars
from fse.commands import CommandContext
from fse.commands import run as run_command
from fse.config.schema import StrategyConfig
from fse.live.host.barstore import LiveBarStoreView
from fse.live.host.config import parse_runner_config
from fse.live.host.dataplane import dataplane_recording_path
from fse.live.host.host import HOST_LOG_FILE_NAME, InlineBlockingRunner
from fse.live.plugins.loader import load_plugin_classes
from fse.live.recorder import read_recording, recording_path
from fse.live.runner import LIVE_LOG_FILE_NAME
from fse.live.state_store import ENGINE_STATE_FILE_NAME, PAPER_STATE_FILE_NAME
from fse.logio import LogWriter, Redactor
from fse.logio.canonical_json import ny_iso
from fse.notify.notifier import CARDS_FILE_NAME
from fse.projectx.session import DEFAULT_BASE_URL
from fse.secrets.env import EnvView
from fse.timekit import NS_PER_MINUTE, Instant, ny_instant
from tests.fakes.bar_api import RespxBarsGateway
from tests.fakes.clock import FakeClock
from tests.fakes.dataplane import CALENDAR_DIR, PROJECT_DIR, read_jsonl
from tests.fakes.market_hub import FakeMarketHub
from tests.fakes.playbook import BASE_CFG

SUN: Final = date(2026, 10, 11)
MON: Final = date(2026, 10, 12)
SKYLIT: Final = "https://api.skylit.ai"
SYMBOLS: Final = ("SPX", "QQQ")
ENV: Final = {
    "SKYLIT_API_KEY": "fake-skylit-key-0000",
    "PROJECTX_USERNAME": "fake-projectx-user-0000",
    "PROJECTX_API_KEY": "fake-projectx-key-0000",
}
ALLOWED: Final = frozenset(
    {
        ("POST", "api.topstepx.com", "/api/Auth/loginKey"),
        ("POST", "api.topstepx.com", "/api/History/retrieveBars"),
        ("GET", "api.skylit.ai", "/v1/account"),
        ("GET", "api.skylit.ai", "/v1/stream"),
        ("GET", "api.skylit.ai", "/v1/gex/levels"),
    }
)


def ny(d: date, hh: int, mm: int = 0) -> Instant:
    return ny_instant(d, time(hh, mm))


def playbook_strategy() -> StrategyConfig:
    """The funnel Strategy_Config on the stream at 15 s, run window 09:28-09:40."""
    data = BASE_CFG.model_dump(mode="python", by_alias=True)
    data["time"]["decision_cadence_s"] = 60
    data["live"].update(
        {
            "mode": "stream",
            "refresh_interval_s": 15,
            "run_window": {"start": "09:28", "end": "09:40"},
        }
    )
    data["notify"]["sinks"] = ["file"]
    data["notify"]["premarket"] = "09:28"
    return StrategyConfig.model_validate(data)


def runner_doc(tmp_path: Path) -> dict[str, Any]:
    strategy = tmp_path / "playbook.yaml"
    strategy.write_text(
        yaml.safe_dump(playbook_strategy().model_dump(mode="json", by_alias=True)),
        encoding="utf-8",
    )
    shipped = yaml.safe_load((PROJECT_DIR / "configs" / "runner.yaml").read_text("utf-8"))
    template = next(p for p in shipped["plugins"] if p["id"] == "template")
    return {
        "version": 1,
        "data_plane": {
            "instruments": ["MNQ", "MES"],
            "projectx": {
                "transport": "auto",
                "subscription": "auto",
                "poll_interval_s": 30,
                "hot_poll_interval_s": 1,
                "backfill_sessions": 0,
            },
            "skylit": {
                "mode": "stream",
                "refresh_interval_s": 15,
                "symbols": list(SYMBOLS),
                "window": {"start": "09:20", "end": "09:45"},
            },
        },
        "plugins": [
            template,
            {
                "id": "playbook",
                "module": "fse.live.plugins.playbook:PlaybookPlugin",
                "enabled": True,
                "config": {
                    "strategy_config": str(strategy),
                    "out_root": str(tmp_path / "playbook-runs"),
                },
            },
        ],
    }


class SkylitStream:
    """``GET /v1/stream``: one snapshot per symbol stamped now, then the connection ends."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.opened: list[tuple[Instant, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        now = self.clock.now()
        metric = request.url.params["metric"]
        self.opened.append((now, metric))
        symbols = request.url.params["symbols"].split(",")
        parts = [f"event: connected\ndata: {json.dumps({'symbols': symbols})}\n\n"]
        for k, symbol in enumerate(symbols):
            spot = 5800.0 if symbol == "SPX" else 500.0
            board = {
                "symbol": symbol,
                "asOf": ny_iso(now),
                "spot": spot,
                "previousClose": None,
                "expirations": [MON.isoformat()],
                "strikes": [
                    {"strike": spot + d, "value": v, "nodeType": None}
                    for d, v in ((-10.0, -1.0e9), (0.0, 2.0e9), (10.0, 1.5e9))
                ],
            }
            event_id = f"{symbol}:{now // 1_000_000 + k}"
            parts.append(f"id: {event_id}\nevent: snapshot\ndata: {json.dumps(board)}\n\n")
        return httpx.Response(
            200, content="".join(parts).encode(), headers={"content-type": "text/event-stream"}
        )


def install_skylit(router: respx.MockRouter, clock: FakeClock) -> SkylitStream:
    router.get(f"{SKYLIT}/v1/account").respond(
        200,
        json={"data": {"customerId": "fake-customer-0000", "status": "active",
                       "apiEligible": True, "unlimited": False, "creditsBalance": 100000,
                       "limits": {}}},
    )  # fmt: skip
    stream = SkylitStream(clock)
    router.get(f"{SKYLIT}/v1/stream").mock(side_effect=stream)
    router.get(f"{SKYLIT}/v1/gex/levels").respond(
        200,
        json={
            "data": {"symbols": []},
            "meta": {"metric": "gamma", "resolution": "1s", "mode": "live", "cached": False},
        },
    )
    return stream


@pytest.mark.asyncio
async def test_fse_run_one_globex_day(
    tmp_path: Path, respx_router: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    constructed: list[str] = []

    def refuse(name: str) -> Callable[..., None]:
        def init(self: object, *args: object, **kwargs: object) -> None:
            constructed.append(name)
            raise AssertionError(f"fse run must never construct a {name}")

        return init

    monkeypatch.setattr("fse.projectx.broker.BrokerAdapter.__init__", refuse("BrokerAdapter"))
    monkeypatch.setattr("fse.live.broker_safety.BrokerSafety.__init__", refuse("BrokerSafety"))

    clock = FakeClock(ny(SUN, 18))
    RespxBarsGateway(clock, base=DEFAULT_BASE_URL).install(respx_router)
    stream = install_skylit(respx_router, clock)
    hub = FakeMarketHub(clock=clock)

    doc = runner_doc(tmp_path)
    config_path = tmp_path / "runner.yaml"
    config_path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    cfg = parse_runner_config(doc, config_path)
    out = io.StringIO()
    err = io.StringIO()
    writer = LogWriter(Redactor(list(ENV.values())), stdout=out, stderr=err)
    ctx = CommandContext(env=EnvView(dict(ENV), {}), writer=writer, project_dir=PROJECT_DIR)
    args = argparse.Namespace(
        state_dir=tmp_path / "live-state",
        recordings_root=tmp_path / "rec",
        out=tmp_path / "run",
        bar_store=tmp_path / "live-bars",
        cache_dir=tmp_path / "cache",
        calendar_dir=CALENDAR_DIR,
    )
    dirs = run_command._dirs(args, cfg, ctx)
    calendars = load_calendars(CALENDAR_DIR, SUN, MON)
    classes = load_plugin_classes(cfg, project_dir=PROJECT_DIR)
    until = run_command.parse_until("2026-10-12T18:10:00-04:00")
    assert until == ny(MON, 18, 10)

    code = await clock.run(
        run_command._run(
            ctx,
            cfg,
            dirs,
            calendars,
            classes,
            until,
            clock=clock,
            connect=hub.connect,
            blocking=InlineBlockingRunner(),
        ),
        max_steps=1_000_000,
    )

    host_log = read_jsonl(tmp_path / "run" / HOST_LOG_FILE_NAME)
    faults = [e for e in host_log if e["kind"] in ("plugin_faulted", "spawned_task_failed")]
    assert faults == []
    assert code == 0, err.getvalue()
    assert host_log[-1]["kind"] == "host_stopped"
    assert host_log[-1]["exit_code"] == 0
    assert constructed == []

    # The network allow-list: only the mocked routes were called.
    called = {
        (c.request.method, c.request.url.host, c.request.url.path) for c in respx_router.calls
    }
    assert called <= ALLOWED
    assert ("POST", "api.topstepx.com", "/api/History/retrieveBars") in called
    assert ("GET", "api.skylit.ai", "/v1/stream") in called
    assert hub.connections  # the market hub transport was up
    assert {m for _, m in stream.opened} == {"gamma", "vanna"}
    assert all(ny(MON, 9, 20) <= t < ny(MON, 9, 45) for t, _ in stream.opened)

    # The data plane: Monday's recording and bar store.
    rec = read_recording(dataplane_recording_path(tmp_path / "rec", MON))
    kinds = {e.kind for e in rec.entries}
    assert {"bar", "snapshot", "operator_event"} <= kinds
    store = LiveBarStoreView(tmp_path / "live-bars")
    for instrument in ("MNQ", "MES"):
        bars = store.bars(instrument, MON)
        assert len(bars) >= 1380  # Sunday 18:00 to Monday 17:00 (and the 17:02 slack)
        assert bars[0].open_ns == ny(SUN, 18)
        assert ny(MON, 17) <= bars[-1].close_ns <= ny(MON, 17, 2)
        assert all(b.close_ns - b.open_ns == NS_PER_MINUTE for b in bars)

    # The template plugin: its state and log are written.
    assert (tmp_path / "live-state" / "plugins" / "template" / "plugin.json").is_file()
    assert (tmp_path / "run" / "plugins" / "template" / "plugin_log.jsonl").is_file()

    # The Playbook plugin: its run directory, recording and state, as under fse paper.
    runs = sorted((tmp_path / "playbook-runs").glob(f"live-{MON.isoformat()}-*"))
    assert len(runs) == 1
    run_dir = runs[0]
    decisions = read_jsonl(run_dir / "decision_log.jsonl")
    assert len(decisions) >= 10
    assert all(ny(MON, 9, 30) <= d["t"] < ny(MON, 9, 40) for d in decisions)
    live_log = read_jsonl(run_dir / LIVE_LOG_FILE_NAME)
    assert live_log[0]["event"] == "start"
    assert live_log[0]["order_mode"] == "paper"
    assert live_log[-1]["event"] == "session_end"
    assert (run_dir / CARDS_FILE_NAME).read_text(encoding="utf-8")
    own = read_recording(recording_path(tmp_path / "rec", MON))
    assert {"snapshot", "bar", "decision_time"} <= {e.kind for e in own.entries}
    assert (tmp_path / "live-state" / ENGINE_STATE_FILE_NAME).is_file()
    assert (tmp_path / "live-state" / PAPER_STATE_FILE_NAME).is_file()
    # The Fetch_Log of the Skylit requests is the data plane's (design 9.2).
    assert (tmp_path / "run" / "dataplane" / MON.isoformat() / "fetch_log.jsonl").is_file()
    assert not (run_dir / "fetch_log.jsonl").exists()
    # No secret reached a log or the console.
    texts = [out.getvalue(), err.getvalue(), (tmp_path / "run" / HOST_LOG_FILE_NAME).read_text()]
    for value in ENV.values():
        assert all(value not in t for t in texts)
