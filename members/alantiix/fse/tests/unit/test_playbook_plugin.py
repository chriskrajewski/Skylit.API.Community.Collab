"""``PlaybookPlugin`` hosted by ``PluginHost`` over a scripted data plane (design 9.2, 10.1).

Covered:

- own calendars with the account's session times: ``account.flat_deadline:
  "15:55"`` opens a session without a fault (``AccountSim`` refuses a calendar
  whose Flat_Deadline differs from the account's);
- per-day errors (``CalendarError``, ``LiveStartError``, the ``session_window``
  ``ValueError``, ``StateDecodeError``) give one ``session_skipped`` log, no
  fault, and the next day opens normally;
- the paper-only assertion: a session with ``safety`` set, or not in paper
  mode, faults the plugin before any ``decide``;
- NIT 3: a bar batch sets ``input_pending`` and the next drain calls
  ``on_wake`` once, which decides at the bar's receipt instant;
- config and start checks (exit 2 at load).
"""

from __future__ import annotations

import io
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final

import pytest
import yaml

import fse.live.plugins.playbook as playbook_module
from fse.calendars import CalendarError, CalendarProblem, load_calendars
from fse.config.schema import StrategyConfig
from fse.engine.state import StateDecodeError
from fse.live.host.config import parse_runner_config
from fse.live.host.host import (
    HOST_LOG_FILE_NAME,
    BarsEvent,
    HostPaths,
    HostResources,
    InlineBlockingRunner,
    MarketEvent,
    PluginHost,
)
from fse.live.plugins.api import PluginContext, PluginServices
from fse.live.plugins.loader import PluginLoadError, resolve_plugin_class
from fse.live.plugins.playbook import PlaybookPlugin
from fse.live.runner import LiveSession, LiveStartError
from fse.logio import LogWriter, Redactor
from fse.secrets.env import EnvView
from fse.timekit import NS_PER_SECOND, Instant, NotASessionError, ny_instant
from tests.fakes.clock import FakeClock
from tests.fakes.dataplane import PROJECT_DIR, FakeDataPlane, read_jsonl
from tests.fakes.playbook import BASE_CFG, FUNNEL, World, make_world, pinned_plugin
from tests.strategies.backtest_inputs import SessionInputs

S: Final = NS_PER_SECOND
MON: Final[SessionInputs] = FUNNEL.sessions[0]
TUE: Final[SessionInputs] = FUNNEL.sessions[1]


def ny(d: date, hh: int, mm: int, ss: int = 0) -> Instant:
    return ny_instant(d, time(hh, mm, ss))


def strategy(**account: Any) -> StrategyConfig:
    data = BASE_CFG.model_dump(mode="python", by_alias=True)
    data["time"]["decision_cadence_s"] = 60
    data["live"]["run_window"] = {"start": "09:28", "end": "09:40"}
    data["notify"]["sinks"] = ["file"]
    data["notify"]["premarket"] = "09:28"
    data["account"].update(account)
    return StrategyConfig.model_validate(data)


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    return make_world(tmp_path_factory.mktemp("playbook-plugin"))


def plugin_doc(tmp_path: Path, cfg: StrategyConfig, **config: Any) -> dict[str, Any]:
    path = tmp_path / "strategy.yaml"
    path.write_text(yaml.safe_dump(cfg.model_dump(mode="json", by_alias=True)), encoding="utf-8")
    return {
        "id": "playbook",
        "module": "fse.live.plugins.playbook:PlaybookPlugin",
        "config": {
            "strategy_config": str(path),
            "out_root": str(tmp_path / "runs"),
            "open_lead_s": 0,
            "prep_lead_s": 0,
            **config,
        },
    }


DATA_PLANE: Final = {
    "instruments": ["MES", "MNQ"],
    "projectx": {"transport": "polling", "backfill_sessions": 0},
    "skylit": {
        "mode": "polling",
        "refresh_interval_s": 5,
        "symbols": ["SPX", "QQQ"],
        "window": {"start": "09:28", "end": "09:40"},
        "dark_pool": {"enabled": False, "tickers": []},
    },
}


@dataclass
class Rig:
    host: PluginHost
    plane: FakeDataPlane
    clock: FakeClock
    tmp_path: Path

    @property
    def plugin(self) -> PlaybookPlugin:
        p = self.host.plugin("playbook")
        assert isinstance(p, PlaybookPlugin)
        return p

    def plugin_log(self) -> list[dict[str, Any]]:
        return read_jsonl(self.tmp_path / "run" / "plugins" / "playbook" / "plugin_log.jsonl")

    def host_log(self) -> list[dict[str, Any]]:
        return read_jsonl(self.tmp_path / "run" / HOST_LOG_FILE_NAME)


def bar_script(s: SessionInputs, until: Instant) -> list[tuple[Instant, MarketEvent]]:
    """Each fixture bar at its close (one event per instrument and minute)."""
    return [(b.close_ns, BarsEvent((b,), b.close_ns)) for b in s.bars if b.close_ns < until]


def make_rig(
    world: World,
    tmp_path: Path,
    cfg: StrategyConfig,
    start: Instant,
    script: Sequence[tuple[Instant, MarketEvent]] = (),
    *,
    cls: type[PlaybookPlugin] | None = None,
    days: tuple[date, date] = (MON.session, TUE.session),
) -> Rig:
    clock = FakeClock(start)
    plane = FakeDataPlane(clock, script)
    rc = parse_runner_config(
        {"version": 1, "data_plane": DATA_PLANE, "plugins": [plugin_doc(tmp_path, cfg)]},
        tmp_path / "runner.yaml",
    )
    calendars = load_calendars(world.calendar_dir, days[0], days[1])
    host = PluginHost(
        rc,
        {"playbook": cls or pinned_plugin()},
        dataplane=plane,
        clock=clock,
        writer=LogWriter(Redactor(), stdout=io.StringIO(), stderr=io.StringIO()),
        paths=HostPaths(
            state_root=tmp_path / "live-state",
            run_dir=tmp_path / "run",
            cache_dir=world.cache,
            calendar_dir=world.calendar_dir,
            project_dir=PROJECT_DIR,
            recordings_root=tmp_path / "rec",
        ),
        calendars=calendars,
        resources=HostResources(env=EnvView({}, {})),
        blocking=InlineBlockingRunner(),
    )
    return Rig(host, plane, clock, tmp_path)


async def run(rig: Rig, until: Instant) -> int:
    return await rig.clock.run(rig.host.run(until))


# ---------------------------------------------------------------- own calendars


@pytest.mark.asyncio
async def test_an_account_flat_deadline_of_1555_opens_without_a_fault(
    world: World, tmp_path: Path
) -> None:
    day = MON.session
    cfg = strategy(flat_deadline="15:55")
    until = ny(day, 9, 45)
    rig = make_rig(world, tmp_path, cfg, ny(day, 9, 20), bar_script(MON, until))
    assert await run(rig, until) == 0
    assert rig.host.faults() == {}
    session = rig.plugin.session
    assert session is not None
    assert session.window.deadline == ny(day, 15, 55)
    assert session.decision_log.read_text(encoding="utf-8").count("\n") >= 10
    assert rig.plugin_log() == []


# ---------------------------------------------------------------- per-day errors


def _first_call_raises(real: Callable[..., Any], exc: BaseException) -> Callable[..., Any]:
    calls: list[int] = []

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise exc
        return real(*args, **kwargs)

    return wrapper


ERRORS: Final = {
    "calendar": (
        "load_calendars",
        CalendarError([CalendarProblem(Path("exchange_calendar.yaml"), None, None, "bad")]),
    ),
    "live_start": ("session_inputs", LiveStartError("the bar feed cannot start: MES")),
    "session_window": ("session_window", NotASessionError("2026-03-02 is not a session")),
    "state_decode": ("LiveSession", StateDecodeError("engine_state.json is unreadable")),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(ERRORS))
async def test_a_per_day_error_skips_the_session_and_the_next_day_opens(
    world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    attr, exc = ERRORS[name]
    monkeypatch.setattr(
        playbook_module, attr, _first_call_raises(getattr(playbook_module, attr), exc)
    )
    until = ny(TUE.session, 9, 45)
    script = [*bar_script(MON, ny(MON.session, 9, 45)), *bar_script(TUE, until)]
    rig = make_rig(world, tmp_path, strategy(), ny(MON.session, 9, 20), script)
    assert await run(rig, until) == 0
    assert rig.host.faults() == {}
    skipped = [e for e in rig.plugin_log() if e["kind"] == "session_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["session"] == MON.session.isoformat()
    assert skipped[0]["level"] == "error"
    assert str(skipped[0]["cause"]).startswith(type(exc).__name__)
    session = rig.plugin.session
    assert session is not None
    assert session.session == TUE.session
    assert session.decision_log.read_text(encoding="utf-8").count("\n") >= 10
    monday_runs = list((tmp_path / "runs").glob(f"live-{MON.session.isoformat()}-*"))
    assert all(not (d / "decision_log.jsonl").exists() for d in monday_runs)


# ---------------------------------------------------------------- paper only


@dataclass
class StubSession:
    """A session whose routing is not the paper broker's."""

    recording: Path
    safety: object | None
    routing: Any
    calls: list[str] = field(default_factory=list)

    def open(self, now: Instant) -> None:
        self.calls.append("open")

    def close(self) -> None:
        self.calls.append("close")

    def decide(self, t: Instant) -> None:
        self.calls.append("decide")
        raise AssertionError("a session that is not paper-routed must never decide")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("safety", "mode"), [(object(), "paper"), (None, "practice")], ids=["safety", "mode"]
)
async def test_a_session_that_is_not_paper_routed_faults_before_any_decision(
    world: World, tmp_path: Path, safety: object | None, mode: str
) -> None:
    stubs: list[StubSession] = []

    class Unsafe(PlaybookPlugin):
        def build_session(self, **kwargs: Any) -> LiveSession:
            stub = StubSession(tmp_path / "none.jsonl.gz", safety, SimpleNamespace(mode=mode))
            stubs.append(stub)
            return stub  # type: ignore[return-value]

    day = MON.session
    until = ny(day, 9, 45)
    rig = make_rig(
        world,
        tmp_path,
        strategy(),
        ny(day, 9, 20),
        bar_script(MON, until),
        cls=pinned_plugin(Unsafe),
    )
    assert await run(rig, until) == 1
    assert rig.host.faults() == {"playbook": "exception"}
    assert [s.calls for s in stubs] == [["open", "close"]]
    faulted = [e for e in rig.host_log() if e["kind"] == "plugin_faulted"]
    assert len(faulted) == 1
    assert str(faulted[0]["error"]).startswith("PaperOnlyViolation")


# ---------------------------------------------------------------- NIT 3: input wakes


@pytest.mark.asyncio
async def test_a_bar_batch_wakes_the_plugin_once_on_the_next_drain(
    world: World, tmp_path: Path
) -> None:
    wakes: list[Instant] = []

    class Counting(PlaybookPlugin):
        def on_wake(self, now: Instant, ctx: PluginContext) -> Sequence[Any]:
            wakes.append(now)
            return super().on_wake(now, ctx)

    day = MON.session
    received = ny(day, 9, 31, 2)  # between the 09:31 and 09:32 grid ticks
    bar = next(b for b in MON.bars if b.open_ns == ny(day, 9, 30) and b.instrument == "MES")
    rig = make_rig(
        world,
        tmp_path,
        strategy(),
        ny(day, 9, 20),
        [(received, BarsEvent((bar,), received))],
        cls=pinned_plugin(Counting),
    )
    assert await run(rig, ny(day, 9, 33)) == 0
    assert wakes.count(received) == 1
    session = rig.plugin.session
    assert session is not None
    times = [
        e["t"]
        for e in read_jsonl(session.decision_log)
        if ny(day, 9, 31) <= e["t"] < ny(day, 9, 32)
    ]
    assert times == [ny(day, 9, 31), received]
    sched = rig.plugin.scheduler
    assert sched is not None
    assert not sched.input_pending


# ---------------------------------------------------------------- config and start checks


def services(world: World, tmp_path: Path, window: tuple[time, time] | None) -> PluginServices:
    from tests.fakes.dataplane import MemoryBarStore

    class NoTasks:
        def run_blocking(self, name: str, fn: Callable[..., Any], *args: object) -> Any:
            raise AssertionError

        def spawn(self, name: str, coro: Any) -> Any:
            raise AssertionError

    return PluginServices(
        tasks=NoTasks(),
        calendars=lambda: load_calendars(world.calendar_dir, MON.session, MON.session),
        writer=LogWriter(Redactor(), stdout=io.StringIO(), stderr=io.StringIO()),
        clock=FakeClock(0),
        calendar_dir=world.calendar_dir,
        state_dir=tmp_path / "live-state",
        out_dir=tmp_path / "out",
        project_dir=PROJECT_DIR,
        bar_store=MemoryBarStore(),
        cache_dir=world.cache,
        env=EnvView({}, {}),
        skylit_window=window,
    )


def test_needs_are_self_brokered_with_the_data_plane_window_start(
    world: World, tmp_path: Path
) -> None:
    doc = plugin_doc(tmp_path, strategy())
    plugin = PlaybookPlugin(
        "playbook", doc["config"], services(world, tmp_path, (time(9, 8), time(16, 15)))
    )
    needs = plugin.needs()
    assert needs.brokerage == "self"
    assert not needs.hold_through_break
    assert [b.instrument for b in needs.bars] == ["MES", "MNQ"]
    assert needs.skylit is not None
    assert needs.skylit.window == (time(9, 8), time(9, 40))
    assert needs.skylit.symbols == ("SPX", "QQQ")
    assert needs.skylit.dark_pool_tickers == ()


@pytest.mark.parametrize(
    ("config", "window", "message"),
    [
        ({"open_lead_s": 4000}, (time(9, 0), time(16, 15)), "open_lead_s"),
        ({"extra": 1}, (time(9, 0), time(16, 15)), "extra"),
        ({}, (time(9, 29), time(16, 15)), "after live.run_window.start"),
        ({}, (time(9, 27, 58), time(16, 15)), "whole number of refresh intervals"),
    ],
    ids=["range", "unknown_key", "window_late", "poll_phase"],
)
def test_bad_config_or_window_refuses_to_load(
    world: World,
    tmp_path: Path,
    config: dict[str, Any],
    window: tuple[time, time],
    message: str,
) -> None:
    doc = plugin_doc(tmp_path, strategy(), **config)
    with pytest.raises(ValueError, match=message):
        PlaybookPlugin("playbook", doc["config"], services(world, tmp_path, window))


def test_the_shipped_module_path_loads_through_the_loader() -> None:
    cls = resolve_plugin_class(
        "playbook", "fse.live.plugins.playbook:PlaybookPlugin", project_dir=PROJECT_DIR
    )
    assert cls is PlaybookPlugin


def test_a_load_failure_is_a_plugin_load_error_through_the_host(
    world: World, tmp_path: Path
) -> None:
    doc = plugin_doc(tmp_path, strategy())
    doc["config"]["strategy_config"] = str(tmp_path / "missing.yaml")
    rc = parse_runner_config(
        {"version": 1, "data_plane": DATA_PLANE, "plugins": [doc]}, tmp_path / "runner.yaml"
    )
    with pytest.raises(PluginLoadError) as info:
        PluginHost(
            rc,
            {"playbook": PlaybookPlugin},
            dataplane=FakeDataPlane(FakeClock(0)),
            clock=FakeClock(0),
            writer=LogWriter(Redactor(), stdout=io.StringIO(), stderr=io.StringIO()),
            paths=HostPaths(tmp_path / "s", tmp_path / "r", world.cache, world.calendar_dir),
            calendars=load_calendars(world.calendar_dir, MON.session, MON.session),
            resources=HostResources(env=EnvView({}, {})),
        )
    assert info.value.exit_code == 2
    assert "playbook" in str(info.value.problems)
