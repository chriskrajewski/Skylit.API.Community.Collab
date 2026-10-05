"""Integration tests: broker flows against a mocked ProjectX (task 35.9).

Every request goes to :class:`tests.fakes.projectx_gateway.FakeGateway`
through respx (the suite's ``assert_all_mocked=True`` guard); the account and
contract ids and the credentials are fake. Time is virtual (``FakeClock``).

Covered: the Combine_Opt_In and Practice matrix; a bracket rejected with
``errorCode 2`` and a fill left without a stop; outage and recovery;
reconciliation differences; ignored MGC and SIL positions; a contract
mismatch; a halt within one Decision_Cadence and a restart with persisted
blocks (a Practice live session over the ``funnel_case`` fixture).

**Validates: Requirements 24.1, 24.3, 24.9, 24.10, 24.12, 24.18, 24.19, 24.21, 24.22,
24.26, 24.27**
"""

from __future__ import annotations

import argparse
import io
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import respx

from fse.commands import CommandContext
from fse.commands.halt import run_clear_command, run_halt_command
from fse.config.schema import StrategyConfig
from fse.config.schema.live import LiveConfig
from fse.engine.step import ExternalBlock
from fse.engine.types import Order, SetupKey
from fse.live.broker_safety import (
    CONTRACT_BLOCK,
    OUTAGE_BLOCK,
    BrokerSafety,
    ClientIds,
    Expectation,
    OrderJournal,
)
from fse.live.order_router import (
    COMBINE,
    PAPER,
    PRACTICE,
    CancelAction,
    MirrorBroker,
    ModeDecision,
    ModifyAction,
    PlaceAction,
    resolve_order_mode,
)
from fse.live.runner import LiveSession
from fse.live.state_store import BlockRecord, BlockStore, halt_file
from fse.logio import LogWriter, Redactor
from fse.projectx.broker import BrokerAdapter, BrokerFailure
from fse.projectx.session import API_KEY_VARIABLE, USERNAME_VARIABLE, ProjectXSession
from fse.secrets.env import EnvView
from fse.sim.fills import OpenTrade
from fse.timekit import NS_PER_SECOND, Instant, parse_rfc3339
from tests.fakes.clock import FakeClock
from tests.fakes.projectx_gateway import BASE, FakeGateway
from tests.strategies.backtest_inputs import funnel_case
from tests.strategies.live_sessions import Driver, LivePlan

S = NS_PER_SECOND
T0 = parse_rfc3339("2026-03-02T14:35:00Z")
CREDS = {USERNAME_VARIABLE: "fake-projectx-user-0000", API_KEY_VARIABLE: "fake-projectx-key-0000"}
MES = "CON.F.US.MES.Z26"
MNQ = "CON.F.US.MNQ.Z26"


def env(**ids: str) -> EnvView:
    return EnvView({**CREDS, **ids}, {})


@pytest.fixture
def gateway(respx_router: respx.MockRouter) -> FakeGateway:
    gw = FakeGateway()
    gw.install(respx_router)
    return gw


def writer() -> LogWriter:
    return LogWriter(Redactor(list(CREDS.values())), stdout=io.StringIO(), stderr=io.StringIO())


# ---------------------------------------------------------------- the opt-in matrix


@pytest.mark.parametrize(
    ("requested", "ids", "flag", "mode", "failures"),
    [
        (COMBINE, {"COMBINE_ACCOUNT_ID": "202"}, True, COMBINE, 0),
        (COMBINE, {"COMBINE_ACCOUNT_ID": "202"}, False, PAPER, 1),
        (COMBINE, {"COMBINE_ACCOUNT_ID": "303"}, True, PAPER, 1),
        (COMBINE, {}, False, PAPER, 2),
        (PRACTICE, {"PRACTICE_ACCOUNT_ID": "101"}, False, PRACTICE, 0),
        (PRACTICE, {"PRACTICE_ACCOUNT_ID": "101", "COMBINE_ACCOUNT_ID": "101"}, True, PAPER, 1),
        (PRACTICE, {"PRACTICE_ACCOUNT_ID": "999"}, True, PAPER, 1),
        (PRACTICE, {}, True, PAPER, 1),
        (PAPER, {"COMBINE_ACCOUNT_ID": "202"}, True, PAPER, 0),
    ],
)
@pytest.mark.asyncio
async def test_the_opt_in_matrix(
    gateway: FakeGateway,
    requested: str,
    ids: dict[str, str],
    flag: bool,
    mode: str,
    failures: int,
) -> None:
    clock = FakeClock(T0)
    e = env(**ids)
    async with httpx.AsyncClient() as http:
        adapter = BrokerAdapter(
            ProjectXSession(e, Redactor(), http, clock, base_url=BASE), e, clock
        )
        accounts = await clock.run(adapter.resolve_accounts())
    decision = resolve_order_mode(requested, e, accounts, live_orders=flag)
    assert decision.mode == mode
    assert len(decision.failures) == failures
    note = decision.note or ""
    for value in ids.values():  # no account id in the first card's text
        assert value not in note
    assert not gateway.order_paths()


@pytest.mark.asyncio
async def test_an_account_search_failure_runs_paper(gateway: FakeGateway) -> None:
    gateway.down = True
    clock = FakeClock(T0)
    e = env(COMBINE_ACCOUNT_ID="202")
    async with httpx.AsyncClient() as http:
        adapter = BrokerAdapter(
            ProjectXSession(e, Redactor(), http, clock, base_url=BASE), e, clock
        )
        accounts = await clock.run(adapter.resolve_accounts())
    assert isinstance(accounts, BrokerFailure)
    decision = resolve_order_mode(COMBINE, e, accounts, live_orders=True)
    assert decision.mode == PAPER
    assert "could not resolve the accounts" in (decision.note or "")


# ---------------------------------------------------------------- a safety harness


KEY = SetupKey("MES", "fade", 6000.0, "long", date(2026, 3, 2), 1)


@dataclass
class StubMirror:
    """The MirrorBroker calls BrokerSafety makes: known orders and the book."""

    orders: dict[str, Order] = field(default_factory=dict)
    trades: dict[SetupKey, OpenTrade] = field(default_factory=dict)

    def known_order(self, client_id: str) -> Order | None:
        return self.orders.get(client_id)

    def working_orders(self) -> tuple[Order, ...]:
        return tuple(self.orders.values())


def bracket(
    key: SetupKey,
    *,
    entry: int = 24_000,
    stop: int = 23_980,
    tp1: int = 24_040,
    tp2: int | None = None,
) -> tuple[Order, Order, tuple[Order, ...]]:
    """An MES long bracket at 6000.00 (ticks), stop 5995.00, TP1 6010.00."""
    qty = 2 if tp2 is not None else 1
    sid = "e000001"
    e = Order(f"{sid}-entry", key, "MES", "buy", "limit", qty, entry, T0, "entry")
    s = Order(f"{sid}-stop", key, "MES", "sell", "stop", qty, stop, T0, "stop")
    targets = [Order(f"{sid}-tp1", key, "MES", "sell", "limit", 1 if tp2 else qty, tp1, T0, "tp1")]
    if tp2 is not None:
        targets.append(Order(f"{sid}-tp2", key, "MES", "sell", "limit", 1, tp2, T0, "tp2"))
    return e, s, tuple(targets)


@dataclass
class Harness:
    clock: FakeClock
    safety: BrokerSafety
    mirror: StubMirror
    notes: list[str]
    blocks: BlockStore
    state_dir: Path

    async def run(self, coro: Any) -> Any:
        return await self.clock.run(coro)

    def expect(self) -> Expectation:
        return Expectation(self.mirror.working_orders(), dict(self.mirror.trades))


async def harness(
    tmp: Path,
    http: httpx.AsyncClient,
    *,
    live: LiveConfig | None = None,
    expected: dict[str, str | None] | None = None,
    instruments: tuple[str, ...] = ("MES", "MNQ"),
) -> Harness:
    clock = FakeClock(T0)
    e = env(PRACTICE_ACCOUNT_ID="101")
    w = writer()
    state = tmp / "live-state"
    adapter = BrokerAdapter(ProjectXSession(e, w.redactor, http, clock, base_url=BASE), e, clock)
    journal = OrderJournal(w, state)
    blocks = BlockStore(w, state)
    safety = BrokerSafety(
        adapter=adapter.for_mode(PRACTICE),
        live=live or LiveConfig(),
        instruments=instruments,
        expected_contracts=expected if expected is not None else {"MES": MES, "MNQ": MNQ},
        blocks=blocks,
        journal=journal,
        ids=ClientIds(w, state, journal, "0a1b2c3d"),
        clock=clock,
        session_start_ns=T0 - 3600 * S,
    )
    mirror = StubMirror()
    notes: list[str] = []
    safety.bind(cast(MirrorBroker, mirror), notes.append)
    await clock.run(safety.start())
    return Harness(clock, safety, mirror, notes, blocks, state)


def outage(h: Harness) -> ExternalBlock | None:
    return h.safety.outage


def block_kinds(blocks: BlockStore) -> list[str]:
    found = blocks.read()
    assert isinstance(found, tuple)
    return [b.kind for b in found]


async def place(h: Harness, key: SetupKey, **kw: Any) -> tuple[Order, Order, tuple[Order, ...]]:
    e, s, targets = bracket(key, **kw)
    for o in (e, s, *targets):
        h.mirror.orders[o.client_id] = o
    h.safety.submit(T0, [PlaceAction(e, s, targets)], h.expect())
    await h.run(h.safety.step())
    return e, s, targets


# ---------------------------------------------------------------- brackets


@pytest.mark.asyncio
async def test_a_bracket_rejected_with_error_code_2_closes_and_blocks(
    gateway: FakeGateway, tmp_path: Path
) -> None:
    gateway.auto_oco = False  # Position Brackets mode: brackets are rejected
    async with httpx.AsyncClient() as http:
        h = await harness(tmp_path, http)
        await place(h, KEY)
        rejected = gateway.place_bodies()
        # A position left open in MES (say the reject came after a partial fill).
        gateway.positions[MES] = 1
        await h.run(
            h.safety._bracket_failure("MES", "the broker rejected the bracket (errorCode 2)")
        )
    assert rejected[0]["stopLossBracket"] == {"ticks": 20, "type": 4}
    assert rejected[0]["takeProfitBracket"] == {"ticks": 40, "type": 1}
    assert rejected[0]["accountId"] == 101
    assert len(rejected) == 1  # flat: nothing to close
    close = gateway.place_bodies()[-1]
    assert (close["type"], close["side"], close["size"]) == (2, 1, 1)  # market sell 1
    assert gateway.positions[MES] == 0
    assert "bracket_failure" in block_kinds(h.blocks)
    assert any("MES" in n and "rejected the bracket (errorCode 2)" in n for n in h.notes)


@pytest.mark.asyncio
async def test_a_fill_without_a_working_stop_is_closed_after_the_confirmation_time(
    gateway: FakeGateway, tmp_path: Path
) -> None:
    async with httpx.AsyncClient() as http:
        h = await harness(tmp_path, http, live=LiveConfig(stop_confirm_s=5))
        await place(h, KEY)
        entry_id = next(o["id"] for o in gateway.working())
        gateway.fill(entry_id, 6000.0, legs=False)
        h.clock.advance_to(T0 + 1 * S)
        await h.run(h.safety.sync())
        assert len(gateway.place_bodies()) == 1  # still inside the 5 s
        h.clock.advance_to(T0 + 6 * S)
        await h.run(h.safety.sync())
    close = gateway.place_bodies()[-1]
    assert (close["type"], close["side"], close["size"]) == (2, 1, 1)
    assert gateway.positions[MES] == 0
    assert "bracket_failure" in block_kinds(h.blocks)
    assert any("no working stop-loss" in n for n in h.notes)


@pytest.mark.asyncio
async def test_bracket_legs_are_moved_to_the_planned_prices_after_a_fill(
    gateway: FakeGateway, tmp_path: Path
) -> None:
    async with httpx.AsyncClient() as http:
        h = await harness(tmp_path, http)
        await place(h, KEY, tp2=24_080)
        entry_id = next(o["id"] for o in gateway.working())
        gateway.fill(entry_id, 5999.0)  # a better fill: legs priced from 5999.00
        h.clock.advance_to(T0 + 1 * S)
        await h.run(h.safety.sync())
        legs = {(o["type"], o["size"]): o for o in gateway.working()}
        assert legs[(4, 2)]["stopPrice"] == 5995.0  # the planned stop
        assert legs[(1, 1)]["limitPrice"] in (6010.0, 6020.0)
        assert sorted(o["limitPrice"] for o in gateway.working() if o["type"] == 1) == [
            6010.0,
            6020.0,
        ]
        assert not block_kinds(h.blocks), h.notes


# ---------------------------------------------------------------- outage


@pytest.mark.asyncio
async def test_an_outage_blocks_entries_leaves_brackets_and_lifts_after_a_clean_sync(
    gateway: FakeGateway, tmp_path: Path
) -> None:
    async with httpx.AsyncClient() as http:
        h = await harness(tmp_path, http, live=LiveConfig(outage_s=30))
        gateway.down = True
        h.clock.advance_to(T0 + 10 * S)
        await h.run(h.safety.sync())
        assert outage(h) is None
        h.clock.advance_to(T0 + 31 * S)
        await h.run(h.safety.sync())
        block = outage(h)
        assert block is not None
        assert block.kind == OUTAGE_BLOCK
        assert h.safety.entry_block("MES") is not None
        before = len(gateway.order_paths())
        h.safety.submit(
            T0 + 31 * S,
            [ModifyAction("x-stop", "MES", "stop", 1), CancelAction("x-tp1", "MES", "tp1", None)],
            h.expect(),
        )
        await h.run(h.safety.step())
        assert len(gateway.order_paths()) == before  # no modify or cancel in an outage
        gateway.down = False
        h.clock.advance_to(T0 + 40 * S)
        await h.run(h.safety.sync())
    assert h.safety.outage is None
    assert any("outage block is lifted" in n for n in h.notes)


@pytest.mark.asyncio
async def test_recovery_with_a_difference_keeps_entries_blocked(
    gateway: FakeGateway, tmp_path: Path
) -> None:
    async with httpx.AsyncClient() as http:
        h = await harness(tmp_path, http, live=LiveConfig(outage_s=5))
        gateway.down = True
        h.clock.advance_to(T0 + 6 * S)
        await h.run(h.safety.sync())
        assert h.safety.outage is not None
        gateway.down = False
        gateway.positions[MES] = 1
        gateway._new(MES, 4, 1, 1, None, 5990.0, None)
        h.clock.advance_to(T0 + 7 * S)
        await h.run(h.safety.sync())
    assert h.safety.outage is not None
    assert "reconciliation" in block_kinds(h.blocks)


# ---------------------------------------------------------------- reconciliation


@pytest.mark.asyncio
async def test_a_foreign_working_order_is_a_difference(
    gateway: FakeGateway, tmp_path: Path
) -> None:
    gateway.add_foreign(MNQ)
    async with httpx.AsyncClient() as http:
        h = await harness(tmp_path, http)
    assert "reconciliation" in block_kinds(h.blocks)
    assert any("carries no Project client id" in n for n in h.notes)


@pytest.mark.asyncio
async def test_ignored_mgc_and_sil_positions_are_listed_and_never_touched(
    gateway: FakeGateway, tmp_path: Path
) -> None:
    gateway.positions["CON.F.US.MGC.J26"] = 2
    gateway.positions["CON.F.US.SIL.K26"] = -1
    gateway.add_foreign("CON.F.US.MGC.J26")
    async with httpx.AsyncClient() as http:
        h = await harness(tmp_path, http)
        h.clock.advance_to(T0 + 30 * S)
        await h.run(h.safety.sync())
    assert not block_kinds(h.blocks)
    assert any("MGC long 2" in n and "SIL short 1" in n for n in h.notes)
    assert not gateway.order_paths()


@pytest.mark.asyncio
async def test_a_contract_mismatch_blocks_that_instrument_for_the_session(
    gateway: FakeGateway, tmp_path: Path
) -> None:
    async with httpx.AsyncClient() as http:
        h = await harness(tmp_path, http, expected={"MES": "CON.F.US.MES.H27", "MNQ": MNQ})
        await place(h, KEY)
    blocks = {b.instrument: b for b in h.safety.external_blocks() if b.kind == CONTRACT_BLOCK}
    assert set(blocks) == {"MES"}
    assert "CON.F.US.MES.H27" in blocks["MES"].detail
    assert MES in blocks["MES"].detail
    assert h.safety.entry_block("MES") is not None
    assert h.safety.entry_block("MNQ") is None
    assert not gateway.place_bodies()


@pytest.mark.asyncio
async def test_an_unresolved_contract_blocks_that_instrument(
    gateway: FakeGateway, tmp_path: Path
) -> None:
    gateway.contracts = [c for c in gateway.contracts if "MNQ" not in c["id"]]
    async with httpx.AsyncClient() as http:
        h = await harness(tmp_path, http)
    blocks = [b for b in h.safety.external_blocks() if b.kind == CONTRACT_BLOCK]
    assert [b.instrument for b in blocks] == ["MNQ"]
    assert "resolution failed" in blocks[0].detail


# ---------------------------------------------------------------- halt and restart


def _practice_session(
    driver: Driver, day: date, http: httpx.AsyncClient, clock: FakeClock, run: str
) -> tuple[LiveSession, BrokerSafety]:
    e = env(PRACTICE_ACCOUNT_ID="101")
    w = driver.writer
    adapter = BrokerAdapter(ProjectXSession(e, w.redactor, http, clock, base_url=BASE), e, clock)
    journal = OrderJournal(w, driver.state_dir)
    safety = BrokerSafety(
        adapter=adapter.for_mode(PRACTICE),
        live=driver.cfg.live,
        instruments=driver.engine.params.instruments,
        expected_contracts={"MES": MES, "MNQ": MNQ},
        blocks=BlockStore(w, driver.state_dir),
        journal=journal,
        ids=ClientIds(w, driver.state_dir, journal),
        clock=clock,
        session_start_ns=driver.calendar.trading_day_start(day),
    )
    live = driver.new_session(day, run=run, mode=ModeDecision(PRACTICE, PRACTICE), safety=safety)
    return live, safety


def _tagged_entries(gateway: FakeGateway) -> list[dict[str, Any]]:
    return [o for o in gateway.working() if o["customTag"] and o.get("_sl")]


@pytest.mark.asyncio
async def test_a_halt_cancels_the_resting_entry_within_one_cadence_and_survives_restarts(
    gateway: FakeGateway, tmp_path: Path
) -> None:
    market, base = funnel_case()
    data = base.model_dump(mode="python", by_alias=True)
    data["live"]["live_max_snapshot_age_s"] = 120  # maps refresh every 60 s
    driver = Driver(tmp_path, market, StrategyConfig.model_validate(data))
    s = market.evaluated[0]
    cal = driver.calendar
    # An entry is placed at 09:32:01 (the P72 pinned example's plan).
    plan = LivePlan(seed=1, refresh_s=60, max_delay_s=5, extra_dt_share=0.3)
    halt = halt_file(driver.state_dir)
    w = io.StringIO()
    out = LogWriter(Redactor(), stdout=w, stderr=io.StringIO())
    ctx = CommandContext(env=EnvView({}, {}), writer=out, project_dir=None)
    async with httpx.AsyncClient() as http:
        clock = FakeClock(cal.trading_day_start(s.session))
        live, safety = _practice_session(driver, s.session, http, clock, "practice")
        live.open(clock.now())
        await clock.run(safety.start())
        resting: dict[str, Any] | None = None
        halted_at: Instant | None = None
        for kind, item in driver.schedule(s, plan):
            if kind == "input":
                Driver._deliver(live, item)
                continue
            t = item if live.last_t is None or item > live.last_t else live.last_t + 1
            clock.advance_to(max(clock.now(), t))
            decision = live.decide(t)
            await clock.run(safety.step())
            if halted_at is not None:
                assert "halt_file" in [b.kind for b in decision.blocks]
                assert resting is not None
                assert gateway.orders[resting["id"]]["status"] == 3  # cancelled
                break
            entries = _tagged_entries(gateway)
            if entries:
                resting = entries[0]
                halt.parent.mkdir(parents=True, exist_ok=True)
                halt.write_text("halt\n", encoding="utf-8")
                assert (
                    run_halt_command(
                        argparse.Namespace(state_dir=driver.state_dir, reason="t"), ctx
                    )
                    == 0
                )
                halted_at = t
        assert halted_at is not None, "no entry reached the broker"
        modified = [b for p, b in gateway.calls if p in ("Order/modify",)]
        assert not modified  # stops and targets untouched
        live.close()
        # A restart: the halt command's block and the halt file are still in force.
        again, safety2 = _practice_session(driver, s.session, http, clock, "restart")
        again.open(clock.now())
        await clock.run(safety2.start())
        assert "halt_command" in block_kinds(BlockStore(driver.writer, driver.state_dir))
        assert safety2.entry_block("MES") is not None
        halt.unlink()
        assert run_clear_command(argparse.Namespace(state_dir=driver.state_dir), ctx) == 0
        assert block_kinds(BlockStore(driver.writer, driver.state_dir)) == []
        again.close()


def test_persisted_blocks_survive_a_restart_until_clear(tmp_path: Path) -> None:
    w = writer()
    state = tmp_path / "live-state"
    BlockStore(w, state).add(BlockRecord("bracket_failure", None, "MES: rejected", T0))
    for _ in range(3):  # restarts: a new store reads the same file
        assert block_kinds(BlockStore(w, state)) == ["bracket_failure"]
    ctx = CommandContext(env=EnvView({}, {}), writer=w, project_dir=None)
    assert run_clear_command(argparse.Namespace(state_dir=state), ctx) == 0
    assert block_kinds(BlockStore(w, state)) == []
    halt_file(state).write_text("halt\n", encoding="utf-8")
    assert halt_file(state).exists()
