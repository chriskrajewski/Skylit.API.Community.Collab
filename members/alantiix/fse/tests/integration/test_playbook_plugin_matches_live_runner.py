"""``PlaybookPlugin`` under ``fse run`` behaves like ``fse paper`` (design 9.3).

Each case runs one fixture session twice, in virtual time from its open
instant: once through ``LiveRunner`` (``fse paper``'s runner, the session
prepared by ``session_inputs``), and once through ``PluginHost`` +
``PlaybookPlugin`` behind the real ``DataPlane`` with the same fakes behind
it. Receipt times are equal by construction: the clock starts at
``open_at = run_start`` (``open_lead_s: 0``, ``notify.premarket ==
run_window.start``), the bar poll grids share their phase (``transport:
polling``, ``poll_interval_s == bar_poll_s``, only the Playbook enabled), the
data plane's Skylit window starts at ``run_window.start`` with the same mode
and interval, and the host runs the preparation inline. Both sessions use the
same fake ``perf_ns`` and code version.

Cases:

1. dark pool off, 2. dark pool on (the funnel session, 2026-03-02):
   byte-equal decision log, ``engine_state.json``, ``paper_state.json``,
   Finding_Cards and live log (start entry, decisions, LevelsCompare lines,
   session end); equal recordings as decoded entries.
3. early close (2026-03-06, close 13:15, Flat_Deadline 13:00): ``dt_end``
   (13:00) is before ``map_end`` (13:15), so the same equalities fail if the
   plugin forwards a snapshot, a print or a LevelsCompare call after the
   last step (inputs received at 13:00 itself go in, as ``LiveRunner``'s
   feeds deliver them before its loop cancels them). LevelsCompare runs every
   70 s here so no comparison is in flight at 13:00.
4. ``transport: stream`` (the fake market hub): receipt times differ from
   ``fse paper``'s, so the case checks that the plugin's recording replayed
   through the Backtester gives its decision log byte for byte.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path
from typing import Any, Final

import pytest
import yaml

from fse.backtest.decision_log import DECISION_LOG_FILE_NAME
from fse.backtest.manifest import DataRange
from fse.backtest.runner import run_backtest
from fse.calendars import Calendars, load_calendars
from fse.commands.paper import session_inputs
from fse.config.schema import StrategyConfig
from fse.data.cache import DataCache
from fse.engine.types import Bar
from fse.live.darkpool_feed import feed_tickers
from fse.live.host.config import RunnerConfig, parse_runner_config
from fse.live.host.dataplane import DataPlane, DataPlanePaths
from fse.live.host.dayclock import globex_day_at
from fse.live.host.host import HostPaths, HostResources, InlineBlockingRunner, PluginHost
from fse.live.plugins.playbook import PlaybookPlugin
from fse.live.recorder import RecordedInput, read_recording
from fse.live.runner import (
    LIVE_LOG_FILE_NAME,
    LiveClients,
    LiveRunner,
    LiveSession,
    session_window,
)
from fse.live.state_store import ENGINE_STATE_FILE_NAME, PAPER_STATE_FILE_NAME
from fse.notify.notifier import CARDS_FILE_NAME, Notifier
from fse.projectx.realtime import MarketHubClient
from fse.secrets.env import EnvView
from fse.timekit import NS_PER_SECOND, Instant, ny_instant
from tests.fakes.clock import FakeClock
from tests.fakes.dataplane import PROJECT_DIR
from tests.fakes.market_hub import FakeMarketHub, FakeTokens
from tests.fakes.playbook import (
    BASE_CFG,
    EARLY,
    FUNNEL,
    FakeBars,
    FakeSkylit,
    World,
    make_world,
    own_calendars,
    perf_counter,
    pinned_plugin,
    writer,
)
from tests.strategies.backtest_inputs import (
    CALENDAR,
    CODE_VERSION,
    EARLY_CLOSE_DAY,
    SEED,
    STARTED,
    SessionInputs,
)

S: Final = NS_PER_SECOND
BAR_POLL_S: Final = 5


@dataclass(frozen=True, slots=True)
class Case:
    name: str
    market: SessionInputs
    run_end: str
    dark_pool: bool
    levels_s: int = 60


CASES: Final = (
    Case("dark_pool_off", FUNNEL.sessions[0], "09:40", False),
    Case("dark_pool_on", FUNNEL.sessions[0], "09:40", True),
    # LevelsCompare every 70 s, so no comparison falls on the last step (13:00) itself:
    # LiveRunner's cancel of a comparison in flight at that instant is a race.
    Case("early_close", EARLY, "14:00", True, levels_s=70),
)


def case_config(case: Case) -> StrategyConfig:
    """The funnel config at a 60 s cadence; the clock starts at run_start = premarket."""
    data = BASE_CFG.model_dump(mode="python", by_alias=True)
    data["time"]["decision_cadence_s"] = 60
    data["live"]["run_window"] = {"start": "09:28", "end": case.run_end}
    data["live"]["levels_compare_interval_s"] = case.levels_s
    data["notify"]["sinks"] = ["file"]
    data["notify"]["premarket"] = "09:28"
    data["gates"]["dark_pool_confluence"]["enabled"] = case.dark_pool
    return StrategyConfig.model_validate(data)


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    return make_world(tmp_path_factory.mktemp("playbook-equivalence"))


@dataclass(frozen=True, slots=True)
class Outputs:
    decision_log: bytes
    engine_state: bytes
    paper_state: bytes
    cards: bytes
    live_log: bytes
    recording: list[RecordedInput]


def outputs(session: LiveSession, run_dir: Path, state_dir: Path) -> Outputs:
    return Outputs(
        decision_log=session.decision_log.read_bytes(),
        engine_state=(state_dir / ENGINE_STATE_FILE_NAME).read_bytes(),
        paper_state=(state_dir / PAPER_STATE_FILE_NAME).read_bytes(),
        cards=(run_dir / CARDS_FILE_NAME).read_bytes(),
        live_log=(run_dir / LIVE_LOG_FILE_NAME).read_bytes(),
        recording=list(read_recording(session.recording).entries),
    )


def open_at(cfg: StrategyConfig, cals: Calendars, day: date) -> Instant:
    w = session_window(cfg, cals.exchange.sessions, day)
    return min(w.run_start, w.premarket)


@dataclass(frozen=True, slots=True)
class _ClockBarStore:
    """Bars closed by ``clock.now()`` — mirrors the data plane store ``fse paper`` lacks.

    Playbook under ``fse run`` fills ``LiveSession.bar_store`` from the data plane;
    ``run_live_runner`` has no plane, so this view keeps Finding_Card ``trade.bars`` /
    dayPct / rangePct in lockstep with the plugin path without changing production paper.
    """

    clock: FakeClock
    market_bars: tuple[Bar, ...]

    def bars(self, instrument: str, day: date) -> tuple[Bar, ...]:
        now = self.clock.now()
        out: list[Bar] = []
        for bar in self.market_bars:
            if bar.instrument != instrument or bar.close_ns > now:
                continue
            gday = globex_day_at(bar.open_ns)
            if gday is None or gday.day != day:
                continue
            out.append(bar)
        return tuple(out)


async def run_live_runner(world: World, case: Case, root: Path) -> Outputs:
    """``fse paper`` started at the open instant."""
    cfg = case_config(case)
    day = case.market.session
    cals = own_calendars(world, cfg, day)
    clock = FakeClock(open_at(cfg, cals, day))
    inputs = session_inputs(cfg, cals, day, world.cache)
    w = writer()
    run_dir = root / "run"
    notifier = Notifier(cfg.notify, EnvView({}, {}), w, clock, run_dir)
    session = LiveSession(
        cfg=cfg,
        engine=inputs.engine,
        calendar=cals.exchange.sessions,
        session=day,
        writer=w,
        run_dir=run_dir,
        state_dir=root / "live-state",
        recordings_root=root / "rec",
        outbox=notifier,
        events=inputs.events,
        vix_daily=inputs.vix,
        perf_ns=perf_counter(),
        bar_store=_ClockBarStore(clock, case.market.bars),
    )
    skylit = FakeSkylit(clock, case.market)
    clients = LiveClients(
        map=skylit,
        bars=FakeBars(clock, case.market),
        instruments=inputs.feeds,
        levels=skylit,
        dark_pool=skylit,
        bar_poll_s=BAR_POLL_S,
    )
    runner = LiveRunner(session, clients, clock, notifier, code_version=CODE_VERSION)
    await clock.run(runner.run(cfg))
    assert not runner.feed_errors
    return outputs(session, run_dir, root / "live-state")


def runner_config(world: World, case: Case, cfg: StrategyConfig, transport: str) -> RunnerConfig:
    day = case.market.session
    cals = own_calendars(world, cfg, day)
    feeds = session_inputs(cfg, cals, day, world.cache).feeds
    strategy = world.root / f"{case.name}-{transport}-strategy.yaml"
    strategy.write_text(
        yaml.safe_dump(cfg.model_dump(mode="json", by_alias=True)), encoding="utf-8"
    )
    tickers = list(feed_tickers(cfg.gates.dark_pool_confluence))
    doc = {
        "version": 1,
        "data_plane": {
            "instruments": [f.instrument for f in feeds],
            "projectx": {
                "transport": transport,
                "subscription": "sim",
                "poll_interval_s": BAR_POLL_S,
                "hot_poll_interval_s": 1,
                "record_quotes": "off",
                "backfill_sessions": 0,
                "expected_contracts": {f.instrument: f.contract_id for f in feeds},
            },
            "skylit": {
                "mode": cfg.live.mode,
                "refresh_interval_s": cfg.live.refresh_interval_s,
                "symbols": list(cfg.data.symbols),
                "window": {"start": "09:28", "end": case.run_end},
                "dark_pool": {"enabled": bool(tickers), "tickers": tickers},
            },
        },
        "plugins": [
            {
                "id": "playbook",
                "module": "fse.live.plugins.playbook:PlaybookPlugin",
                "config": {
                    "strategy_config": str(strategy),
                    "out_root": str(world.root / f"{case.name}-{transport}-runs"),
                    "open_lead_s": 0,
                    "prep_lead_s": 0,
                },
            }
        ],
    }
    return parse_runner_config(doc, strategy)


async def run_plugin(
    world: World, case: Case, root: Path, *, transport: str = "polling"
) -> tuple[Outputs, PlaybookPlugin, list[dict[str, Any]]]:
    """``fse run`` with only the Playbook plugin, started at the open instant."""
    cfg = case_config(case)
    day = case.market.session
    cals = own_calendars(world, cfg, day)
    clock = FakeClock(open_at(cfg, cals, day))
    rc = runner_config(world, case, cfg, transport)
    w = writer()
    host_cals = load_calendars(world.calendar_dir, day, day)
    skylit = FakeSkylit(clock, case.market)
    # Under the stream transport bars publish 700 ms after their close: the hub's trigger
    # (close + 1.5 s) fetches them before the 5 s safety grid would.
    bars = FakeBars(clock, case.market, 700_000_000 if transport == "stream" else 0)
    logs: list[dict[str, Any]] = []
    hub: Any = None
    if transport == "stream":
        fake_hub = FakeMarketHub(clock=clock)
        tokens = FakeTokens()

        def hub(
            cids: tuple[str, ...],
            on_quote: Callable[..., None],
            on_trade: Callable[..., None],
            on_state: Callable[[bool, str], None],
        ) -> MarketHubClient:
            return MarketHubClient(
                tokens,
                clock,
                rtc_url="wss://rtc.example.invalid/hubs/market",
                contract_ids=cids,
                connect=fake_hub.connect,
                on_quote=on_quote,
                on_trade=on_trade,
                on_state=on_state,
            )

    plane = DataPlane(
        rc.data_plane,
        clock=clock,
        writer=w,
        paths=DataPlanePaths(root / "rec", root / "run", root / "bars"),
        calendars=host_cals,
        bars=lambda live: bars,
        log=lambda e: logs.append(dict(e)),
        on_fatal=lambda code, detail: logs.append({"kind": "fatal", "detail": detail}),
        hub=hub,
        skylit=lambda fetch_log: skylit,
    )
    host = PluginHost(
        rc,
        {"playbook": pinned_plugin()},
        dataplane=plane,
        clock=clock,
        writer=w,
        paths=HostPaths(
            state_root=root / "live-state",
            run_dir=root / "run",
            cache_dir=world.cache,
            calendar_dir=world.calendar_dir,
            project_dir=PROJECT_DIR,
            recordings_root=root / "rec",
        ),
        calendars=host_cals,
        resources=HostResources(env=EnvView({}, {}), skylit_levels=plane.levels),
        blocking=InlineBlockingRunner(),
    )
    window = session_window(cfg, cals.exchange.sessions, day)
    code = await clock.run(host.run(window.bars_until + 60 * S))
    assert code == 0, host.faults()
    plugin = host.plugin("playbook")
    assert isinstance(plugin, PlaybookPlugin)
    session, run_dir = plugin.session, plugin.run_dir
    assert session is not None
    assert run_dir is not None
    return outputs(session, run_dir, root / "live-state"), plugin, logs


def plugin_log(plugin: PlaybookPlugin) -> list[str]:
    path = plugin.services.out_dir / "plugin_log.jsonl"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


# ---------------------------------------------------------------- the cases


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
async def test_playbook_plugin_matches_live_runner(
    world: World, case: Case, tmp_path: Path
) -> None:
    paper = await run_live_runner(world, case, tmp_path / "paper")
    run, plugin, _ = await run_plugin(world, case, tmp_path / "run")
    assert not plugin_log(plugin)  # no skip, no late open
    assert run.decision_log == paper.decision_log
    assert run.engine_state == paper.engine_state
    assert run.paper_state == paper.paper_state
    assert run.cards == paper.cards
    assert run.live_log == paper.live_log
    assert run.recording == paper.recording
    # Not vacuous: decisions, cards and LevelsCompare lines were written.
    assert paper.decision_log.count(b"\n") >= 9
    assert b"levels_compare" in paper.live_log
    assert paper.cards
    kinds = {e.kind for e in paper.recording}
    assert {"snapshot", "bar", "decision_time"} <= kinds
    if case.dark_pool:
        assert "dark_pool" in kinds


def test_the_early_close_case_ends_deciding_before_the_map_window() -> None:
    cfg = case_config(CASES[2])
    w = session_window(cfg, CALENDAR, EARLY_CLOSE_DAY)
    assert w.dt_end == ny_instant(EARLY_CLOSE_DAY, time(13, 0))
    assert w.map_end == ny_instant(EARLY_CLOSE_DAY, time(13, 15))
    assert w.dt_end < w.map_end


@pytest.mark.asyncio
async def test_stream_transport_replays_to_the_same_decision_log(
    world: World, tmp_path: Path
) -> None:
    case = CASES[1]
    root = tmp_path / "stream"
    run, plugin, logs = await run_plugin(world, case, root, transport="stream")
    assert any(e.get("kind") == "feed_status" and "stream" in str(e.get("detail")) for e in logs)
    cfg = case_config(case)
    day = case.market.session
    out = root / "replay"
    with DataCache(world.cache, calendar=CALENDAR) as cache:
        run_backtest(
            cfg,
            DataRange(day, day),
            cache,
            out,
            SEED,
            "replay",
            writer=writer(),
            calendar_dir=world.calendar_dir,
            clock=lambda: STARTED,
            code_version=CODE_VERSION,
            recordings=root / "rec" / "recordings",
        )
    assert (out / DECISION_LOG_FILE_NAME).read_bytes() == run.decision_log
    assert run.decision_log.count(b"\n") >= 9
    # Bars arrived on the stream trigger (about 1.5 s after the minute), not the 5 s grid.
    received = [e.received_ns for e in run.recording if e.kind == "bar"]
    assert any(r % (5 * S) != 0 for r in received)
    assert plugin.session is not None
