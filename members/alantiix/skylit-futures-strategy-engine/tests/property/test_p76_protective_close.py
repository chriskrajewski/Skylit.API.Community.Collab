"""Property 76: Protective close and persistent blocks.

*For any* sequence of broker events after a fill (bracket rejection, stop
missing, partial coverage, late confirmation), a market order closing the
full open quantity is sent exactly when the bracket is rejected or no working
stop covers the full quantity within the confirmation time; every block that
needs the clear command (bracket failure, reconciliation difference, halt
command, failed restore) survives any number of restarts and is lifted only
by the clear command, and halt-file blocks last exactly while the file exists.

Part one drives :class:`~fse.live.broker_safety.BrokerSafety` with a stand-in
adapter (:class:`tests.fakes.broker.FakeAdapter`): after a drawn fill, the
working stop quantity follows a drawn timeline (none, partial, full, late)
over syncs one second apart, or the bracket is rejected with a drawn open
position. Part two runs a drawn sequence of block, restart, clear and
halt-file operations against the live-state directory and checks
:class:`~fse.live.guards.Guards` after each.

**Validates: Requirements 24.9, 24.10, 24.27, 16.10**
"""

from __future__ import annotations

import asyncio
import io
from collections import deque
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from hypothesis import event, given
from hypothesis import strategies as st

from fse.config.schema.live import LiveConfig
from fse.engine.types import Order, SetupKey, Side
from fse.live.broker_safety import BrokerSafety, ClientIds, OrderJournal
from fse.live.guards import Guards, StaleMapGuard
from fse.live.order_router import MirrorBroker, PlaceAction
from fse.live.state_store import BLOCK_KINDS, BlockRecord, BlockStore, halt_file
from fse.logio import LogWriter, Redactor
from fse.pit.protocols import MapState
from fse.projectx.broker import BrokerAdapter, OrderRequest
from fse.timekit import NS_PER_SECOND
from tests.fakes.broker import CONTRACTS, FakeAdapter
from tests.fakes.clock import FakeClock

S = NS_PER_SECOND
T0 = 1_772_462_000 * S
KEY = SetupKey("MES", "fade", 6000.0, "long", date(2026, 3, 2), 1)


class _Mirror:
    def known_order(self, client_id: str) -> Order | None:
        return None

    def working_orders(self) -> tuple[Order, ...]:
        return ()

    @property
    def trades(self) -> dict[object, object]:
        return {}


def _writer() -> LogWriter:
    return LogWriter(Redactor(), stdout=io.StringIO(), stderr=io.StringIO())


def _safety(fake: FakeAdapter, state: Path, confirm_s: int) -> BrokerSafety:
    w = _writer()
    journal = OrderJournal(w, state)
    safety = BrokerSafety(
        adapter=cast(BrokerAdapter, fake),
        live=LiveConfig(stop_confirm_s=confirm_s),
        instruments=sorted(CONTRACTS),
        expected_contracts=CONTRACTS,
        blocks=BlockStore(w, state),
        journal=journal,
        ids=ClientIds(w, state, journal),
        clock=fake.clock,
        session_start_ns=T0,
    )
    safety.bind(cast(MirrorBroker, _Mirror()), lambda _n: None)
    return safety


def _closes(fake: FakeAdapter) -> list[OrderRequest]:
    return [r for name, r in fake.calls if name == "place" and r.kind == "market"]


@given(
    qty=st.integers(1, 5),
    side=st.sampled_from(["buy", "sell"]),
    confirm_s=st.integers(1, 30),
    rejected=st.booleans(),
    held_at_reject=st.integers(0, 5),
    cover=st.integers(0, 5),
    cover_at=st.none() | st.integers(0, 35),
)
def test_protective_close(
    qty: int,
    side: str,
    confirm_s: int,
    rejected: bool,
    held_at_reject: int,
    cover: int,
    cover_at: int | None,
) -> None:
    sign = 1 if side == "buy" else -1

    async def main(state: Path) -> tuple[FakeAdapter, list[str]]:
        clock = FakeClock(T0)
        fake = FakeAdapter(clock)
        safety = _safety(fake, state, confirm_s)
        await clock.run(safety.start())
        if rejected:
            fake.place_outcomes = deque(["rejected"])
            fake.positions["MES"] = sign * held_at_reject
            buy: Side = "buy" if side == "buy" else "sell"
            close: Side = "sell" if side == "buy" else "buy"
            e = Order("fse-000001-entry", KEY, "MES", buy, "limit", qty, 24_000, T0, "entry")
            s = Order(
                "fse-000001-stop", KEY, "MES", close, "stop", qty, 24_000 - sign * 8, T0, "stop"
            )
            t = Order(
                "fse-000001-tp1", KEY, "MES", close, "limit", qty, 24_000 + sign * 8, T0, "tp1"
            )
            await clock.run(safety.dispatch(T0, PlaceAction(e, s, (t,))))
        else:  # a fill the broker shows at T0 + 1 s, then the stop timeline
            fake.positions["MES"] = sign * qty
            for k in range(1, confirm_s + 6):
                clock.advance_to(T0 + k * S)
                covered = cover_at is not None and k - 1 >= cover_at
                if covered and fake.positions.get("MES"):
                    fake.stops["MES"] = cover
                await clock.run(safety.sync())
        found = BlockStore(_writer(), state).read()
        assert isinstance(found, tuple)
        return fake, [r.kind for r in found]

    with TemporaryDirectory() as tmp:
        fake, kinds = asyncio.run(main(Path(tmp)))
    closes = _closes(fake)
    if rejected:
        want = held_at_reject > 0
        event(f"rejected, close={want}")
        assert "bracket_failure" in kinds
    else:
        # Covered in time: full cover seen at a sync within confirm_s of the first gap.
        in_time = cover_at is not None and cover >= qty and cover_at <= confirm_s
        want = not in_time
        event(f"fill, close={want}")
        assert ("bracket_failure" in kinds) == want
    assert bool(closes) == want
    if want:
        held = held_at_reject if rejected else qty
        assert len(closes) == 1
        assert closes[0].qty == held  # the full open quantity
        assert closes[0].side == ("sell" if side == "buy" else "buy")
        assert fake.positions["MES"] == 0


class _Fresh:
    """A Map_State stand-in whose Snapshot_Age is 0 (the stale guard never fires)."""

    def snapshot_age_ns(self) -> int:
        return 0


_OPS = st.lists(
    st.one_of(
        st.tuples(st.just("block"), st.sampled_from(BLOCK_KINDS)),
        st.tuples(st.just("restart"), st.none()),
        st.tuples(st.just("clear"), st.none()),
        st.tuples(st.just("halt_on"), st.none()),
        st.tuples(st.just("halt_off"), st.none()),
    ),
    max_size=25,
)


@given(ops=_OPS)
def test_persistent_blocks_and_the_halt_file(ops: list[tuple[str, str | None]]) -> None:
    with TemporaryDirectory() as tmp:
        state = Path(tmp)
        state.mkdir(exist_ok=True)
        w = _writer()
        store = BlockStore(w, state)
        guards = Guards(StaleMapGuard(30), store, state)
        model: set[str] = set()
        halted = False
        for i, (op, kind) in enumerate(ops):
            if op == "block":
                assert kind is not None
                store.add(BlockRecord(kind, None, f"block {i}", T0 + i))  # type: ignore[arg-type]
                model.add(kind)
            elif op == "restart":
                store = BlockStore(w, state)
                guards = Guards(StaleMapGuard(30), store, state)
            elif op == "clear":
                store.clear()
                model.clear()
            elif op == "halt_on":
                halt_file(state).write_text("halt\n", encoding="utf-8")
                halted = True
            else:
                halt_file(state).unlink(missing_ok=True)
                halted = False
            found = [b.kind for b in guards.at(cast(MapState, _Fresh()))]
            assert ("halt_file" in found) == halted
            assert {k for k in found if k != "halt_file"} == model
