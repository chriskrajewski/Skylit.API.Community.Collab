"""Property 77: Client-id uniqueness and safe resubmission.

*For any* sequence of order sends, timeouts, lookups and restarts, no two
orders sent to an account share a client id, and a resubmission is sent only
when the client-id lookup succeeds and finds no working or filled order with
that id.

Each example runs a drawn sequence against one live-state directory: sends
whose answer is accepted, lost (the order exists, no answer), dropped (no
order, no answer) or rejected; syncs whose client-id lookups are truthful,
fail or time out; and restarts (a new journal, sequence and BrokerSafety from
the same directory, with a new run id). The stand-in adapter
(:class:`tests.fakes.broker.FakeAdapter`) records every call in order.

**Validates: Requirements 24.13, 24.14, 24.15**
"""

from __future__ import annotations

import asyncio
import io
from collections import deque
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from hypothesis import event, example, given
from hypothesis import strategies as st

from fse.config.schema.live import LiveConfig
from fse.engine.types import Order
from fse.live.broker_safety import BrokerSafety, ClientIds, OrderJournal
from fse.live.order_router import ExitAction, MirrorBroker
from fse.live.state_store import BlockStore
from fse.logio import LogWriter, Redactor
from fse.projectx.broker import BrokerAdapter
from fse.timekit import NS_PER_SECOND
from tests.fakes.broker import CONTRACTS, FakeAdapter
from tests.fakes.clock import FakeClock

S = NS_PER_SECOND
T0 = 1_772_462_000 * S


class _Mirror:
    def known_order(self, client_id: str) -> Order | None:
        return None

    def working_orders(self) -> tuple[Order, ...]:
        return ()

    @property
    def trades(self) -> dict[object, object]:
        return {}


_OPS = st.lists(
    st.one_of(
        st.tuples(
            st.just("send"), st.sampled_from(["accepted", "lost", "dropped", "dropped", "rejected"])
        ),
        st.tuples(
            st.just("sync"),
            st.lists(st.sampled_from(["truthful", "fail", "timeout"]), max_size=4),
        ),
        st.tuples(st.just("restart"), st.none()),
    ),
    min_size=1,
    max_size=30,
)


@given(ops=_OPS)
@example(  # a dropped send: a failed and a late lookup skip, then a clean lookup resends
    ops=[
        ("send", "dropped"),
        ("sync", ["fail"]),
        ("sync", ["timeout"]),
        ("sync", ["truthful"]),
        ("send", "lost"),
        ("sync", ["truthful"]),
        ("restart", None),
        ("send", "dropped"),
        ("sync", []),
    ]
)
def test_client_id_uniqueness_and_safe_resubmission(ops: list[tuple[str, object]]) -> None:
    async def main(state: Path) -> FakeAdapter:
        clock = FakeClock(T0)
        fake = FakeAdapter(clock)
        writer = LogWriter(Redactor(), stdout=io.StringIO(), stderr=io.StringIO())

        def fresh() -> BrokerSafety:
            journal = OrderJournal(writer, state)
            safety = BrokerSafety(
                adapter=cast(BrokerAdapter, fake),
                live=LiveConfig(lookup_timeout_s=5),
                instruments=sorted(CONTRACTS),
                expected_contracts=CONTRACTS,
                blocks=BlockStore(writer, state),
                journal=journal,
                ids=ClientIds(writer, state, journal),
                clock=clock,
                session_start_ns=T0,
            )
            safety.bind(cast(MirrorBroker, _Mirror()), lambda _n: None)
            return safety

        safety = fresh()
        await clock.run(safety.start())
        for i, (op, arg) in enumerate(ops):
            clock.advance_to(clock.now() + S)
            if op == "send":
                fake.place_outcomes.append(str(arg))
                order = Order(
                    f"fse-{i:06d}-exit", None, "MES", "sell", "market", 1, None, clock.now(), "exit"
                )
                await clock.run(safety.dispatch(clock.now(), ExitAction(order, "sell", 1)))
            elif op == "sync":
                assert isinstance(arg, list)
                fake.lookup_modes = deque(arg)
                await clock.run(safety.sync())
            else:
                safety = fresh()
                await clock.run(safety.start())
        return fake

    with TemporaryDirectory() as tmp:
        fake = asyncio.run(main(Path(tmp)))

    # No two orders at the broker carry one client id; no send repeated a live id.
    tags = [o.tag for o in fake.orders.values() if o.tag]
    assert len(tags) == len(set(tags))
    assert fake.duplicate_attempts == 0
    # A send of an id already sent is a resubmission: only after a successful
    # lookup of that id found no working or filled order.
    seen: set[str] = set()
    last_lookup: dict[str, tuple[str, bool]] = {}
    resent = 0
    for name, payload in fake.calls:
        if name == "lookup":
            cid, mode, held = cast(tuple[str, str, bool], payload)
            last_lookup[cid] = (mode, held)
        elif name in ("place", "place_bracketed"):
            cid = payload.client_id
            if cid in seen:
                resent += 1
                mode, held = last_lookup.pop(cid, ("none", True))
                assert mode == "truthful"
                assert not held
            seen.add(cid)
    event(f"resubmissions: {min(resent, 3)}")
