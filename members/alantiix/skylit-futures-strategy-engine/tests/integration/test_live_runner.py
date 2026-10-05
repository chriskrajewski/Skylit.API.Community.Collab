"""Integration tests for the Live_Runner with a fake clock (task 33.11).

Every network call goes to a fake client or a respx mock with fake keys, and
time is virtual (:class:`~tests.fakes.clock.FakeClock`). The market is the
pinned ``funnel_case`` (2026-03-02 and 03-03): the fake map client serves its
Snapshots as live boards stamped with the request instant, and the fake bar
client its closed 1-minute bars.

Covered:

- polling: two multi-symbol ``GET /v1/heatmap`` requests per interval, and the
  start log (Order_Mode, config hash, code version, credit projection);
- stream mode through the real Skylit_Client: a connection that drops
  mid-body is reopened with the last event id (task 32 review);
- refresh errors and timeouts: logged, prior Map_State kept;
- the stale-map guard: a Decision_Time within 1 s of the age passing the
  maximum, with the block, until a fresh map;
- ``Engine.step`` latency under 1 s over a whole fixture session;
- Finding_Cards keep coming while a ProjectX request is pending;
- a restart from saved state continues the session as an uninterrupted run;
- an unreadable state blocks entries until ``fse clear``, and ``fse halt``
  blocks them again;
- the recording reads back (round trip), also when cut off.

**Validates: Requirements 16.7, 16.10, 23.2, 23.3, 23.4, 23.6, 23.11, 23.12, 23.14**
"""

from __future__ import annotations

import argparse
import io
import json
import random
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import date, time
from pathlib import Path
from typing import Any, Final

import httpx
import pytest
import respx

from fse.commands import CommandContext
from fse.commands.halt import run_clear_command, run_halt_command
from fse.config.schema import StrategyConfig
from fse.engine.types import Snapshot
from fse.live.bar_feed import FeedInstrument
from fse.live.guards import STALE_MAP_BLOCK
from fse.live.recorder import decode_snapshot, read_recording
from fse.live.runner import LIVE_LOG_FILE_NAME, LiveClients, LiveRunner, LiveSession
from fse.live.state_store import ENGINE_STATE_FILE_NAME
from fse.logio import LogWriter, Redactor
from fse.logio.canonical_json import ny_iso
from fse.notify.notifier import Notifier
from fse.projectx.models import BarHistory
from fse.secrets.env import EnvView
from fse.skylit.client import ClientConfig, Failed, SkylitClient
from fse.skylit.endpoints import Host
from fse.skylit.fetch_log import FetchLog
from fse.skylit.models import HeatmapResponse
from fse.timekit import NS_PER_SECOND, Instant, ny_instant
from tests.fakes.clock import FakeClock
from tests.strategies.backtest_inputs import CODE_VERSION, FAKE_SECRET, funnel_case
from tests.strategies.live_sessions import Driver, LivePlan

S: Final = NS_PER_SECOND
KEY: Final = "fake-skylit-key-0000"
MARKET, BASE_CFG = funnel_case()
DAY: Final[date] = MARKET.sessions[0].session


def config(**live: Any) -> StrategyConfig:
    """``funnel_case`` at a 60 s cadence with a short run window (09:28 to 09:40)."""
    data = BASE_CFG.model_dump(mode="python", by_alias=True)
    data["time"]["decision_cadence_s"] = 60
    data["live"].update({"run_window": {"start": "09:28", "end": "09:40"}, **live})
    data["notify"]["sinks"] = ["file"]
    return StrategyConfig.model_validate(data)


def board(snap: Snapshot, as_of: Instant) -> dict[str, Any]:
    return {
        "symbol": snap.symbol,
        "asOf": ny_iso(as_of),
        "spot": snap.spot,
        "previousClose": snap.previous_close,
        "expirations": list(snap.expirations),
        "strikes": [
            {"strike": k, "value": v, "nodeType": None}
            for k, v in zip(snap.strikes, snap.values, strict=True)
        ],
    }


class SessionMap:
    """A fake ``GET /v1/heatmap``: the session's latest Snapshots, stamped with now."""

    def __init__(self, clock: FakeClock, *, fail_from: Instant | None = None) -> None:
        self.clock = clock
        self.snaps = MARKET.sessions[0].snapshots
        self.calls: list[tuple[Instant, str, tuple[str, ...]]] = []
        self.fail_from = fail_from
        self.fail_calls: set[int] = set()
        self.hang_calls: set[int] = set()

    async def heatmap(self, symbols: Any, *, metric: str, view: Any) -> HeatmapResponse | Failed:
        i = len(self.calls)
        now = self.clock.now()
        self.calls.append((now, metric, tuple(symbols)))
        if i in self.hang_calls:
            await self.clock.sleep(7 * S)
        if i in self.fail_calls or (self.fail_from is not None and now >= self.fail_from):
            return Failed(Host.API, "/v1/heatmap", {}, "failed", 5, status=503)
        latest: dict[str, Snapshot] = {}
        for snap in self.snaps:
            if snap.metric == metric and snap.as_of_ns <= now:
                latest[snap.symbol] = snap
        boards = [board(latest[s], now) for s in symbols if s in latest]
        return HeatmapResponse.parse(
            {
                "data": {"symbols": boards},
                "meta": {"metric": metric, "resolution": "1s", "mode": "live", "cached": False},
            }
        )

    def stream(self, params: Any, *, last_event_id: str | None = None) -> Any:
        raise AssertionError("polling never streams")


class SessionBars:
    """A fake ProjectX ``retrieveBars``: the session's closed bars; the first call can hang."""

    def __init__(self, clock: FakeClock, *, hang_first_s: int = 0) -> None:
        self.clock = clock
        self.bars = MARKET.sessions[0].bars
        self.hang_first_s = hang_first_s
        self.calls: list[tuple[Instant, Instant | None]] = []

    async def retrieve(
        self,
        *,
        instrument: str,
        contract: str,
        contract_id: str,
        start_ns: Instant,
        end_ns: Instant,
        interval_s: int,
    ) -> BarHistory:
        self.calls.append((self.clock.now(), None))
        if self.hang_first_s and len(self.calls) == 1:
            await self.clock.sleep(self.hang_first_s * S)
        self.calls[-1] = (self.calls[-1][0], self.clock.now())
        got = tuple(
            b
            for b in self.bars
            if b.instrument == instrument and b.open_ns >= start_ns and b.close_ns <= end_ns
        )
        return BarHistory(
            instrument, contract, contract_id, interval_s, start_ns, end_ns, got, (), 1
        )


FEEDS: Final = (
    FeedInstrument("MES", "MESH6", "CON.F.US.MES.H26"),
    FeedInstrument("MNQ", "MNQH6", "CON.F.US.MNQ.H26"),
)


def restamp(snap: Snapshot, as_of: Instant) -> Snapshot:
    return replace(snap, as_of_ns=as_of, as_of_raw=ny_iso(as_of))


def lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


def make_runner(
    tmp: Path, cfg: StrategyConfig, clock: FakeClock, clients: LiveClients
) -> tuple[LiveRunner, LiveSession, Notifier]:
    driver = Driver(tmp, MARKET, cfg)
    writer = LogWriter(Redactor([FAKE_SECRET]), stdout=io.StringIO(), stderr=io.StringIO())
    run_dir = tmp / "runs" / "live"
    notifier = Notifier(cfg.notify, EnvView({}, {}), writer, clock, run_dir)
    session = driver.new_session(DAY, run="live", outbox=notifier)
    return (
        LiveRunner(session, clients, clock, notifier, code_version=CODE_VERSION),
        session,
        notifier,
    )


def at(hh: int, mm: int, ss: int = 0) -> Instant:
    return ny_instant(DAY, time(hh, mm, ss))


# ---------------------------------------------------------------- polling and the start log


@pytest.mark.asyncio
async def test_polling_sends_two_multi_symbol_requests_per_interval_and_logs_the_start(
    tmp_path: Path,
) -> None:
    clock = FakeClock(at(9, 27))
    cfg = config()
    maps = SessionMap(clock)
    runner, _session, notifier = make_runner(tmp_path, cfg, clock, LiveClients(map=maps))
    await clock.run(runner.run(cfg))
    ticks = sorted({c[0] for c in maps.calls})
    assert ticks[0] == at(9, 28)
    assert ticks == list(range(at(9, 28), at(9, 40), 5 * S))
    for t in ticks:
        assert sorted(c[1] for c in maps.calls if c[0] == t) == ["gamma", "vanna"]
    assert {c[2] for c in maps.calls} == {("SPX", "QQQ")}
    log = lines(tmp_path / "runs" / "live" / LIVE_LOG_FILE_NAME)
    start = log[0]
    assert start["event"] == "start"
    assert start["order_mode"] == "paper"
    assert start["code_version"] == CODE_VERSION
    assert len(start["config_hash"]) == 64
    credits = start["credit_projection"]
    # 720 s of polling at 5 s, two metrics, 1 credit each; levels at 60 s, 1 credit each.
    assert credits["map_credits"] == 144 * 2
    assert credits["levels_credits"] == 12
    assert credits["total_credits"] == 300
    decisions = [e for e in log if e["event"] == "decision"]
    assert decisions
    assert all(at(9, 30) <= e["t"] < at(9, 40) for e in decisions)
    assert all(e["latency_ms"] < 1000 for e in decisions)
    kinds = [m.kind for m in notifier.sent]
    assert kinds[0] == "card:premarket"
    assert "card:decision" in kinds


# ---------------------------------------------------------------- stream mode


def _stream_body(as_of: Instant, event_id: str) -> bytes:
    snap = next(s for s in MARKET.sessions[0].snapshots if s.symbol == "SPX")
    data = json.dumps(board(snap, as_of))
    connected = 'event: connected\ndata: {"symbols":["SPX","QQQ"]}\n\n'
    return f"{connected}id: {event_id}\nevent: snapshot\ndata: {data}\n\n".encode()


class _Dropping(httpx.AsyncByteStream):
    """One snapshot event, then the peer drops the connection mid-body."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._body
        raise httpx.RemoteProtocolError("peer closed connection without a complete body")

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_stream_mode_reopens_a_dropped_connection_with_the_last_event_id(
    tmp_path: Path, respx_router: respx.MockRouter
) -> None:
    clock = FakeClock(at(9, 29, 50))
    cfg = config(mode="stream", run_window={"start": "09:30", "end": "09:31"})
    respx_router.get("https://api.skylit.ai/v1/account").respond(
        200,
        json={"data": {"customerId": "fake-customer-0000", "status": "active",
                       "apiEligible": True, "unlimited": False, "creditsBalance": 1,
                       "limits": {}}},
    )  # fmt: skip
    seen: list[str | None] = []
    count = 0

    def answer(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        seen.append(request.headers.get("Last-Event-ID"))
        body = _stream_body(clock.now(), f"SPX:{1_772_461_800_000 + count}")
        return httpx.Response(
            200, stream=_Dropping(body), headers={"content-type": "text/event-stream"}
        )

    respx_router.get("https://api.skylit.ai/v1/stream").mock(side_effect=answer)
    with FetchLog(LogWriter(Redactor([KEY])), tmp_path / "fetch.jsonl") as flog:
        client = SkylitClient(
            EnvView({"SKYLIT_API_KEY": KEY}, {}), ClientConfig(), flog, clock, random.Random(7)
        )
        runner, _session, _ = make_runner(tmp_path, cfg, clock, LiveClients(map=client))
        await clock.run(runner.run(cfg))
        await client.aclose()
    log = lines(tmp_path / "runs" / "live" / LIVE_LOG_FILE_NAME)
    reconnects = [e for e in log if e["event"] == "stream_reconnect"]
    assert reconnects
    assert all(e["reason"].startswith("connection dropped") for e in reconnects)
    # Each reopen sends the id of the last event the previous connection delivered.
    assert seen[0] is None
    assert all(x is not None and x.startswith("SPX:") for x in seen[2:])
    assert not runner.feed_errors
    assert any(e["event"] == "decision" for e in log)


# ---------------------------------------------------------------- refresh errors


@pytest.mark.asyncio
async def test_refresh_errors_are_logged_and_the_prior_map_state_is_kept(tmp_path: Path) -> None:
    clock = FakeClock(at(9, 27))
    cfg = config(run_window={"start": "09:28", "end": "09:33"}, live_max_snapshot_age_s=120)
    maps = SessionMap(clock)
    first_after_open = (at(9, 30) - at(9, 28)) // (5 * S) * 2
    maps.fail_calls = {first_after_open, first_after_open + 1}  # both metrics at 09:30:00
    maps.hang_calls = {first_after_open + 2}  # gamma at 09:30:05: no answer in 5 s
    runner, session, _ = make_runner(tmp_path, cfg, clock, LiveClients(map=maps))
    await clock.run(runner.run(cfg))
    log = lines(tmp_path / "runs" / "live" / LIVE_LOG_FILE_NAME)
    failed = [e for e in log if e["event"] == "map_refresh_failed"]
    assert [e["cause"] for e in failed] == ["HTTP 503", "HTTP 503", "no response within 5 s"]
    assert all(e["kept"] == "prior Map_State" for e in failed)
    dlog = lines(session.decision_log)
    first = dlog[0]  # 09:30:00, after the failed refresh
    assert first["t"] == at(9, 30)
    assert first["map"]["SPX/gamma"]["asOf"] == at(9, 29, 55)
    assert first["external_blocks"] == []


# ---------------------------------------------------------------- the stale-map guard


@pytest.mark.asyncio
async def test_a_stale_map_blocks_entries_within_one_second(tmp_path: Path) -> None:
    clock = FakeClock(at(9, 27))
    cfg = config(run_window={"start": "09:28", "end": "09:36"})
    maps = SessionMap(clock, fail_from=at(9, 32))
    runner, session, _ = make_runner(tmp_path, cfg, clock, LiveClients(map=maps))
    await clock.run(runner.run(cfg))
    dlog = lines(session.decision_log)
    last_fresh = at(9, 31, 55)  # the last refresh before the outage
    crossing = last_fresh + 30 * S
    stale = [e for e in dlog if any(b["kind"] == STALE_MAP_BLOCK for b in e["external_blocks"])]
    assert stale
    assert crossing < stale[0]["t"] <= crossing + S
    for e in dlog:
        blocked = any(b["kind"] == STALE_MAP_BLOCK for b in e["external_blocks"])
        assert blocked == (e["t"] > crossing)
        if blocked:
            assert not any(i["type"] == "PlaceBracket" for i in e["orders"])


# ---------------------------------------------------------------- latency and pending requests


def test_engine_step_latency_is_under_one_second_on_a_fixture_session(tmp_path: Path) -> None:
    driver = Driver(tmp_path, MARKET, BASE_CFG)
    driver.run_market([LivePlan(seed=1, refresh_s=60, max_delay_s=5, extra_dt_share=0.3)])
    latencies = [x for s in driver.sessions for x in s.latencies]
    assert len(latencies) > 1000
    assert max(latencies) < S


@pytest.mark.asyncio
async def test_cards_keep_coming_while_a_projectx_request_is_pending(tmp_path: Path) -> None:
    clock = FakeClock(at(9, 27))
    cfg = config(run_window={"start": "09:28", "end": "09:38"})
    data = cfg.model_dump(mode="python", by_alias=True)
    data["notify"]["interval_min"] = 1
    cfg = StrategyConfig.model_validate(data)
    bars = SessionBars(clock, hang_first_s=8 * 3600)  # pending past the session's end
    clients = LiveClients(map=SessionMap(clock), bars=bars, instruments=FEEDS, bar_poll_s=600)
    runner, _session, notifier = make_runner(tmp_path, cfg, clock, clients)
    await clock.run(runner.run(cfg))
    answered = bars.calls[0][1]
    assert answered is not None
    cards = [m for m in notifier.sent if m.kind == "card:decision"]
    before = [m for m in cards if int(str(m.body["decision_time"])) < answered]
    assert len(before) >= 8  # one card a minute from 09:30 to 09:38 at least
    log = lines(tmp_path / "runs" / "live" / LIVE_LOG_FILE_NAME)
    assert all(e["latency_ms"] < 1000 for e in log if e["event"] == "decision")


# ---------------------------------------------------------------- restart and blocks


def test_a_restart_from_saved_state_continues_like_an_uninterrupted_run(tmp_path: Path) -> None:
    plan = LivePlan(seed=1, refresh_s=60, max_delay_s=5, extra_dt_share=0.3)
    s = MARKET.sessions[0]
    whole = Driver(tmp_path / "whole", MARKET, BASE_CFG)
    whole.run_session(s, plan)
    broken = Driver(tmp_path / "broken", MARKET, BASE_CFG)
    broken.run_session(s, plan, stop_after=200)
    restarted = broken.run_session(s, plan, resume_at=broken.crash_at)
    a = whole.sessions[0].decision_log.read_bytes().splitlines()
    first = broken.sessions[0].decision_log.read_bytes().splitlines()
    rest = restarted.decision_log.read_bytes().splitlines()
    assert len(first) == 200
    assert first + rest == a
    assert [t for t in restarted.loop.trades] == whole.sessions[0].loop.trades[
        len(broken.sessions[0].loop.trades) :
    ]
    acct_a, acct_b = whole.sessions[0].routing.paper.account, restarted.routing.paper.account
    assert (acct_b.balance, acct_b.day_pnl) == (acct_a.balance, acct_a.day_pnl)


def _ctx(writer: LogWriter) -> CommandContext:
    return CommandContext(env=EnvView({}, {}), writer=writer, project_dir=None)


def test_an_unreadable_state_blocks_entries_until_clear(tmp_path: Path) -> None:
    plan = LivePlan(seed=1, refresh_s=60, max_delay_s=5, extra_dt_share=0.0)
    s = MARKET.sessions[0]
    driver = Driver(tmp_path, MARKET, BASE_CFG)
    driver.run_session(s, plan, stop_after=10)
    (driver.state_dir / ENGINE_STATE_FILE_NAME).write_text("{not json", encoding="utf-8")
    live = driver.new_session(s.session, run="after-crash")
    live.open(at(9, 45))

    def fresh(t: Instant) -> None:
        latest = {(x.symbol, x.metric): x for x in s.snapshots}
        live.on_snapshots([restamp(x, t - S) for x in latest.values()], t - S)

    fresh(at(9, 46))
    first = live.decide(at(9, 46))
    assert [b.kind for b in first.blocks] == ["restore_failed"]
    assert first.card is not None
    assert any("restore failed" in n for n in first.card.notes)
    assert first.result.state.book.plans == ()
    out = io.StringIO()
    writer = LogWriter(Redactor([FAKE_SECRET]), stdout=out, stderr=io.StringIO())
    args = argparse.Namespace(state_dir=driver.state_dir)
    assert run_clear_command(args, _ctx(writer)) == 0
    assert "restore_failed" in out.getvalue()
    fresh(at(9, 47))
    assert live.decide(at(9, 47)).blocks == ()
    halt = argparse.Namespace(state_dir=driver.state_dir, reason="test halt")
    assert run_halt_command(halt, _ctx(writer)) == 0
    fresh(at(9, 48))
    assert [b.kind for b in live.decide(at(9, 48)).blocks] == ["halt_command"]
    live.close()


# ---------------------------------------------------------------- the recording


def test_the_recording_reads_back_and_a_cut_off_file_reads_to_its_last_whole_line(
    tmp_path: Path,
) -> None:
    driver = Driver(tmp_path, MARKET, BASE_CFG)
    s = MARKET.sessions[0]
    plan = LivePlan(seed=4, refresh_s=0, max_delay_s=0, extra_dt_share=0.0)
    live = driver.run_session(s, plan)
    rec = read_recording(live.recording)
    assert not rec.truncated
    snaps = [decode_snapshot(e.payload) for e in rec.entries if e.kind == "snapshot"]
    assert snaps == sorted(s.snapshots, key=lambda x: x.as_of_ns)
    times = [e.received_ns for e in rec.entries if e.kind == "decision_time"]
    assert times == [d.result.payload.t for d in driver.decisions]
    data = live.recording.read_bytes()
    live.recording.write_bytes(data[: len(data) * 2 // 3])
    cut = read_recording(live.recording)
    assert cut.truncated
    assert 0 < len(cut.entries) < len(rec.entries)
    assert cut.entries == rec.entries[: len(cut.entries)]
