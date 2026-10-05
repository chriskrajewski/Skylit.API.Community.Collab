"""Broker safety in Practice and Combine Order_Mode (design §24, Req 24.9-24.22).

Everything persistent lives in the live-state directory, outside the
repository: ``client_seq.json`` (the client-id sequence), ``order_journal.jsonl``
(append-only) and ``blocks.json`` (:mod:`fse.live.state_store`).

**Client ids** (Req 24.13). ``fse-{run_uuid8}-{seq}``: a random 8-hex run id
per run and a sequence persisted before each send (:class:`ClientIds`). Each
id is checked against the journal before it is used, and journaled before the
request goes out, so no two orders sent to an account share one.

**Order journal** (:class:`OrderJournal`, OQ8). One JSON line per event,
fsynced: ``sent`` (client id, engine order id, instrument, role, the bracket
legs' engine ids), ``ack`` (client id → ProjectX ``orderId``) and ``adopt``
(a bracket leg's ``orderId`` → its engine order id). It maps every broker
order the Project sent back to the Strategy_Engine's order ids.

**Resubmission** (Req 24.14-24.15). A send with no answer is pending. At the
next sync its client id is looked up first; the order is sent again (same
client id) only when the lookup succeeds and finds no working or filled
order. A lookup that fails or takes longer than ``live.lookup_timeout_s``
skips that cycle. A market exit or close is resent cut to the broker's open
quantity on its closing side at that sync, and dropped (logged
``resubmit_dropped``) when nothing is left to close, so a resend can never
open a reverse position.

**After a fill** (Req 24.8-24.10). When the broker reports a Project entry
filled, its bracket legs (the one untagged stop and the one untagged limit on
the closing side of that contract) are adopted, then modified to the planned
stop and first target; for TP1_Partial_BE the target leg is resized to the
TP1 quantity and a TP2 limit is placed. Until those changes are accepted, or
``live.stop_confirm_s`` has passed, the legs are left out of the
reconciliation. If no working stop covers the full open quantity of an
instrument within ``live.stop_confirm_s`` of the broker first showing the
gap, or a bracket is rejected, a market order closes the full position, a
persistent ``bracket_failure`` block stops new entries on every instrument,
and a note names the instrument and the failure.

**Contracts** (Req 24.11-24.12). At start (one run is one session) each
instrument's front-month contract is resolved and logged. A resolution
failure or an id other than the expected one (the bar feed's contract id)
blocks that instrument for the session, and no order is sent for it.

**Reconciliation** (Req 24.16-24.19, :func:`reconcile`). At start and after
every Decision_Time, per configured instrument: the position (side and
quantity) and each working order (client id, side, quantity, price). The
expectation is the Strategy_Engine's book as the Paper_Broker side holds it
(:func:`expected_view`), with the fills the broker has already reported
applied, since the engine expects its working orders to fill. A difference, a
working order with no Project client id, or a Project order nobody expects
sets a persistent ``reconciliation`` block and a note listing each
difference. Ignored instruments are listed and never compared or touched.

**Outage** (Req 24.21-24.22). No successful ProjectX answer for
``live.outage_s`` sets an in-memory ``broker_outage`` block and a note; no
modify or cancel request is sent while it holds, so broker-side stops and
targets stay. The first successful sync reconciles; the block is lifted only
when that comparison is clean.

Every request goes through the Broker_Adapter bound to the mode's account.
The step never awaits any of this (design D9): the Live_Runner queues each
Decision_Time's actions and expectation, and :meth:`BrokerSafety.run`
processes them in its own task.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import Callable, Iterable, Mapping, Sequence, Set
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Final

from fse.clock import Clock
from fse.config.schema.live import LiveConfig
from fse.engine.step import ExternalBlock
from fse.engine.types import Order, SetupKey, Side
from fse.live._wait import TimedOut, within
from fse.live.order_router import (
    BrokerAction,
    CancelAction,
    ExitAction,
    MirrorBroker,
    ModifyAction,
    PlaceAction,
)
from fse.live.state_store import BlockRecord, BlockStore, BlocksUnreadable
from fse.logio import LogWriter
from fse.logio.canonical_json import JsonValue
from fse.projectx.broker import (
    CLIENT_ID_PREFIX,
    BrokerAdapter,
    BrokerFailure,
    BrokerOrder,
    BrokerState,
    EntryIntent,
    OrderRequest,
    PlaceResult,
    ResolveError,
)
from fse.sim.fills import OpenTrade
from fse.timekit import NS_PER_SECOND, Instant

__all__ = [
    "CLIENT_SEQ_FILE_NAME",
    "CONTRACT_BLOCK",
    "JOURNAL_FILE_NAME",
    "OUTAGE_BLOCK",
    "STOPPED_BLOCK",
    "SYNC_BLOCK",
    "BrokerSafety",
    "ClientIds",
    "Expectation",
    "InstrumentView",
    "JournalError",
    "OrderJournal",
    "OrderView",
    "Reconciliation",
    "broker_view",
    "describe_position",
    "expected_view",
    "new_run_uuid8",
    "reconcile",
]

JOURNAL_FILE_NAME: Final = "order_journal.jsonl"
CLIENT_SEQ_FILE_NAME: Final = "client_seq.json"
OUTAGE_BLOCK: Final = "broker_outage"
CONTRACT_BLOCK: Final = "contract_mismatch"
SYNC_BLOCK: Final = "broker_not_synced"
STOPPED_BLOCK: Final = "broker_task_stopped"
_LOOKUP_MARGIN_NS: Final = 60 * NS_PER_SECOND
_SEQ_FORMAT: Final = "fse.client_seq"
_LEG_ROLES: Final = frozenset({"stop", "tp1", "tp2"})
_ORDER_REQUESTS: Final = frozenset({"place", "place_bracketed", "modify", "cancel"})


def new_run_uuid8() -> str:
    """A random 8-hex run id for ``fse-{run_uuid8}-{seq}``."""
    return secrets.token_hex(4)


def describe_position(qty: int) -> str:
    """``long 2``, ``short 1`` or ``flat``."""
    if qty == 0:
        return "flat"
    return f"{'long' if qty > 0 else 'short'} {abs(qty)}"


def _sign(side: Side) -> int:
    return 1 if side == "buy" else -1


def _closing(position: int) -> Side:
    return "sell" if position > 0 else "buy"


def _closable(position: int, side: Side) -> int:
    """How much of ``position`` an order on ``side`` closes without reversing it."""
    if position == 0 or side != _closing(position):
        return 0
    return abs(position)


# ---------------------------------------------------------------- journal and ids


class JournalError(Exception):
    """The order journal cannot be read: broker orders stay off (exit 4)."""

    exit_code = 4


class OrderJournal:
    """The append-only order journal (see the module notes)."""

    __slots__ = (
        "_ack",
        "_adopted",
        "_client_engine",
        "_engine_client",
        "_entries",
        "_legs",
        "_order_client",
        "_path",
        "_sent_at",
        "_writer",
        "truncated",
    )

    def __init__(self, writer: LogWriter, state_dir: Path) -> None:
        self._writer = writer
        self._path = Path(state_dir) / JOURNAL_FILE_NAME
        self._sent_at: dict[str, Instant] = {}
        self._client_engine: dict[str, str] = {}
        self._engine_client: dict[str, str] = {}
        self._ack: dict[str, int] = {}
        self._order_client: dict[int, str] = {}
        self._adopted: dict[int, str] = {}
        self._legs: dict[str, dict[str, str]] = {}
        self._entries: set[str] = set()
        self.truncated = False
        self._load()

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> None:
        try:
            raw = self._path.read_bytes()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise JournalError(f"{self._path.name}: cannot read ({type(exc).__name__})") from None
        lines = raw.split(b"\n")
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                self._apply(entry)
            except ValueError, KeyError, TypeError:
                if i == len(lines) - 1:  # a cut-off last line: the send never completed
                    self.truncated = True
                    continue
                raise JournalError(
                    f"{self._path.name}: line {i + 1} is not a journal entry"
                ) from None

    def _apply(self, e: dict[str, object]) -> None:
        event = e["event"]
        if event == "sent":
            cid, eid = str(e["client_id"]), str(e["engine_id"])
            t = e["t"]
            if isinstance(t, bool) or not isinstance(t, int):
                raise TypeError("t")
            self._sent_at[cid] = t
            self._client_engine[cid] = eid
            self._engine_client[eid] = cid
            if e["role"] == "entry":
                self._entries.add(eid)
            legs = e.get("legs") or {}
            if not isinstance(legs, dict):
                raise TypeError("legs")
            self._legs[eid] = {str(k): str(v) for k, v in legs.items()}
            for leg in self._legs[eid].values():  # a reused engine id drops its old mapping
                self._engine_client.pop(leg, None)
                for oid in [o for o, x in self._adopted.items() if x == leg]:
                    del self._adopted[oid]
        elif event == "ack":
            cid = str(e["client_id"])
            order_id = e["order_id"]
            if isinstance(order_id, bool) or not isinstance(order_id, int):
                raise TypeError("order_id")
            self._ack[cid] = order_id
            self._order_client[order_id] = cid
        elif event == "adopt":
            self._adopted[int(str(e["order_id"]))] = str(e["engine_id"])
        else:
            raise ValueError(event)

    def _append(self, entry: dict[str, JsonValue]) -> None:
        self._writer.append_json(self._path, entry, fsync=True)
        self._apply(dict(entry))

    def sent(
        self,
        client_id: str,
        engine_id: str,
        instrument: str,
        role: str,
        t: Instant,
        legs: Mapping[str, str] | None = None,
    ) -> None:
        self._append(
            {
                "event": "sent",
                "client_id": client_id,
                "engine_id": engine_id,
                "instrument": instrument,
                "role": role,
                "t": t,
                "legs": dict(legs or {}),
            }
        )

    def ack(self, client_id: str, order_id: int) -> None:
        if self._ack.get(client_id) != order_id:
            self._append({"event": "ack", "client_id": client_id, "order_id": order_id})

    def adopt(self, order_id: int, engine_id: str, instrument: str, t: Instant) -> None:
        self._append(
            {
                "event": "adopt",
                "order_id": order_id,
                "engine_id": engine_id,
                "instrument": instrument,
                "t": t,
            }
        )

    # ------------------------------------------------------------ queries

    def contains(self, client_id: str) -> bool:
        return client_id in self._sent_at

    def client_ids(self) -> tuple[str, ...]:
        return tuple(self._sent_at)

    def sent_at(self, client_id: str) -> Instant | None:
        return self._sent_at.get(client_id)

    def order_id_for(self, client_id: str) -> int | None:
        return self._ack.get(client_id)

    def engine_for_client(self, client_id: str) -> str | None:
        return self._client_engine.get(client_id)

    def client_for_engine(self, engine_id: str) -> str | None:
        return self._engine_client.get(engine_id)

    def is_entry(self, engine_id: str) -> bool:
        """Whether ``engine_id`` was journaled as a bracketed entry."""
        return engine_id in self._entries

    def legs_of(self, engine_id: str) -> Mapping[str, str]:
        """The bracket legs (role → engine id) journaled with an entry."""
        return self._legs.get(engine_id, {})

    def engine_for_order(self, order: BrokerOrder) -> str | None:
        """The engine order id behind a broker order: adopted, acked, or by its tag."""
        adopted = self._adopted.get(order.order_id)
        if adopted is not None:
            return adopted
        cid = self._order_client.get(order.order_id)
        if cid is not None:
            return self._client_engine.get(cid)
        if order.tag is not None:
            return self._client_engine.get(order.tag)
        return None

    def order_for_engine(self, engine_id: str) -> int | None:
        """The ProjectX order id of an engine order (sent and acked, or an adopted leg)."""
        cid = self._engine_client.get(engine_id)
        if cid is not None and cid in self._ack:
            return self._ack[cid]
        for oid, eid in self._adopted.items():
            if eid == engine_id:
                return oid
        return None


class ClientIds:
    """``fse-{run_uuid8}-{seq}`` with a persisted sequence and a journal check (Req 24.13)."""

    __slots__ = ("_journal", "_path", "_run", "_seq", "_writer")

    def __init__(
        self,
        writer: LogWriter,
        state_dir: Path,
        journal: OrderJournal,
        run_uuid8: str | None = None,
    ) -> None:
        run = new_run_uuid8() if run_uuid8 is None else run_uuid8
        if len(run) != 8 or any(c not in "0123456789abcdef" for c in run):
            raise ValueError("run_uuid8 is 8 lower-case hex digits")
        self._writer = writer
        self._journal = journal
        self._run = run
        self._path = Path(state_dir) / CLIENT_SEQ_FILE_NAME
        self._seq = self._read()

    @property
    def run_uuid8(self) -> str:
        return self._run

    def _read(self) -> int:
        try:
            obj = json.loads(self._path.read_text(encoding="utf-8"))
        except OSError, ValueError:
            return 0
        if isinstance(obj, dict) and obj.get("format") == _SEQ_FORMAT:
            seq = obj.get("seq")
            if isinstance(seq, int) and not isinstance(seq, bool) and seq >= 0:
                return seq
        return 0

    def next(self) -> str:
        """A client id no journaled order carries; the sequence is saved before it is returned."""
        while True:
            self._seq += 1
            self._writer.write_json(self._path, {"format": _SEQ_FORMAT, "seq": self._seq})
            cid = f"{CLIENT_ID_PREFIX}{self._run}-{self._seq}"
            if not self._journal.contains(cid):
                return cid


# ---------------------------------------------------------------- reconciliation


@dataclass(frozen=True, slots=True)
class OrderView:
    """One working order as compared: ``key`` is the engine order id (``#<orderId>`` if none)."""

    key: str
    side: Side
    qty: int
    price: int | None
    foreign: bool = False


@dataclass(frozen=True, slots=True)
class InstrumentView:
    """One instrument as compared: the signed position and the working orders."""

    position: int = 0
    orders: tuple[OrderView, ...] = ()


@dataclass(frozen=True, slots=True)
class Reconciliation:
    """Every difference found, and the open positions of ignored instruments."""

    differences: tuple[str, ...]
    ignored_positions: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        return not self.differences


def reconcile(
    expected: Mapping[str, InstrumentView],
    broker: Mapping[str, InstrumentView],
    *,
    instruments: Iterable[str],
    ignored: Iterable[str],
) -> Reconciliation:
    """Compare per configured instrument (Req 24.16-24.19; see the module notes)."""
    skip = frozenset(ignored)
    diffs: list[str] = []
    for inst in sorted(set(instruments) - skip):
        want = expected.get(inst, InstrumentView())
        have = broker.get(inst, InstrumentView())
        if want.position != have.position:
            diffs.append(
                f"{inst} position: expected {describe_position(want.position)}, "
                f"broker {describe_position(have.position)}"
            )
        wanted = {o.key: o for o in want.orders}
        for o in have.orders:
            if o.foreign:
                diffs.append(f"{inst} working order {o.key} carries no Project client id")
                continue
            w = wanted.pop(o.key, None)
            if w is None:
                diffs.append(f"{inst} order {o.key}: working at the broker, not expected")
                continue
            for name, a, b in (
                ("side", w.side, o.side),
                ("qty", w.qty, o.qty),
                ("price", w.price, o.price),
            ):
                if a != b:
                    diffs.append(f"{inst} order {o.key} {name}: expected {a}, broker {b}")
        diffs.extend(
            f"{inst} order {key}: expected working, not at the broker" for key in sorted(wanted)
        )
    ignored_positions = tuple(
        f"{inst} {describe_position(view.position)}"
        for inst, view in sorted(broker.items())
        if inst in skip and view.position != 0
    )
    return Reconciliation(tuple(diffs), ignored_positions)


def expected_view(
    working: Sequence[Order],
    trades: Mapping[SetupKey, OpenTrade],
    *,
    broker_fills: Mapping[str, int],
    hidden: Set[str] = frozenset(),
) -> dict[str, InstrumentView]:
    """The engine's book as the broker should show it (see the module notes).

    ``broker_fills`` holds, per engine order id, the contracts the broker
    reports filled. An entry the broker has not filled shows alone (its legs
    exist only after the fill); a filled one moves the position and makes its
    legs live; a filled leg moves the position back; a setup left with no
    open contracts shows none of its remaining legs. Ids in ``hidden`` are
    left out.
    """
    position: dict[str, int] = {}
    for trade in trades.values():
        position[trade.instrument] = position.get(trade.instrument, 0) + trade.sign * trade.open_qty
    groups: dict[SetupKey | None, list[Order]] = {}
    for order in working:
        groups.setdefault(order.setup_key, []).append(order)
    orders: dict[str, list[OrderView]] = {}
    for key, legs in groups.items():
        entry = next((o for o in legs if o.role == "entry"), None)
        held = None if key is None else trades.get(key)
        open_qty = 0 if held is None else held.open_qty
        shown: list[Order] = []
        if entry is not None:
            filled = min(broker_fills.get(entry.client_id, 0), entry.qty)
            if filled == 0:
                if entry.client_id not in hidden:
                    orders.setdefault(entry.instrument, []).append(_view(entry, entry.qty))
                continue
            position[entry.instrument] = (
                position.get(entry.instrument, 0) + _sign(entry.side) * filled
            )
            open_qty += filled
            if filled < entry.qty:
                shown.append(_with_qty(entry, entry.qty - filled))
        for leg in legs:
            if leg.role == "entry":
                continue
            done = min(broker_fills.get(leg.client_id, 0), leg.qty)
            if done:
                position[leg.instrument] = position.get(leg.instrument, 0) + _sign(leg.side) * done
                open_qty -= done
            if done < leg.qty:
                shown.append(_with_qty(leg, leg.qty - done))
        for o in shown:
            if o.role != "entry" and open_qty <= 0:
                continue
            if o.client_id not in hidden:
                orders.setdefault(o.instrument, []).append(_view(o, o.qty))
    out: dict[str, InstrumentView] = {}
    for inst in sorted(set(position) | set(orders)):
        out[inst] = InstrumentView(position.get(inst, 0), tuple(orders.get(inst, ())))
    return out


def _with_qty(order: Order, qty: int) -> Order:
    return replace(order, qty=qty)


def _view(order: Order, qty: int) -> OrderView:
    return OrderView(order.client_id, order.side, qty, order.price)


def broker_view(
    states: Mapping[str, BrokerState],
    engine_of: Callable[[BrokerOrder], str | None],
    *,
    hidden: Set[str] = frozenset(),
) -> dict[str, InstrumentView]:
    """The broker's positions and working orders keyed by engine order id where known."""
    out: dict[str, InstrumentView] = {}
    for inst, state in states.items():
        views: list[OrderView] = []
        for o in state.orders:
            if not o.working:
                continue
            eid = engine_of(o)
            if eid is not None and eid in hidden:
                continue
            if eid is not None:
                views.append(OrderView(eid, o.side, o.qty - o.filled_qty, o.price))
            elif o.tag is not None and o.tag.startswith(CLIENT_ID_PREFIX):
                views.append(OrderView(o.tag, o.side, o.qty - o.filled_qty, o.price))
            else:
                views.append(OrderView(f"#{o.order_id}", o.side, o.qty, o.price, foreign=True))
        out[inst] = InstrumentView(state.position, tuple(views))
    return out


# ---------------------------------------------------------------- the safety task


@dataclass(frozen=True, slots=True)
class Expectation:
    """The Paper_Broker side's book right after a Decision_Time."""

    working: tuple[Order, ...] = ()
    trades: Mapping[SetupKey, OpenTrade] = field(default_factory=dict)

    @classmethod
    def of(cls, paper: MirrorBroker) -> Expectation:
        return cls(paper.working_orders(), dict(paper.trades))


@dataclass(slots=True)
class _Pending:
    """A send with no answer, waiting for a lookup before it is resent."""

    client_id: str
    engine_id: str
    instrument: str
    request: EntryIntent | OrderRequest
    sent_at: Instant
    cancel_wanted: bool = False


type _Note = Callable[[str], None]
type _Log = Callable[[dict[str, JsonValue]], None]


class BrokerSafety:
    """Dispatch, sync, reconciliation, stop confirmation and outages (see the module notes)."""

    def __init__(
        self,
        *,
        adapter: BrokerAdapter,
        live: LiveConfig,
        instruments: Sequence[str],
        expected_contracts: Mapping[str, str | None],
        blocks: BlockStore,
        journal: OrderJournal,
        ids: ClientIds,
        clock: Clock,
        session_start_ns: Instant,
        log: _Log | None = None,
    ) -> None:
        if adapter.account_id is None:
            raise ValueError("BrokerSafety needs a Broker_Adapter bound to an account")
        self.adapter = adapter
        self._live = live
        self._ignored = frozenset(live.ignored_instruments)
        self._instruments = tuple(i for i in instruments if i not in self._ignored)
        self._expected_contracts = dict(expected_contracts)
        self._blocks = blocks
        self.journal = journal
        self._ids = ids
        self._clock = clock
        self._session_start = session_start_ns
        self._sink: _Log = log or (lambda _e: None)
        self._note: _Note = lambda _t: None
        self._mirror: MirrorBroker | None = None
        self.contracts: dict[str, str] = {}
        self.contract_blocks: dict[str, ExternalBlock] = {}
        self.outage: ExternalBlock | None = None
        self.states: dict[str, BrokerState] | None = None
        self._fills: dict[str, int] = {}
        self._filled_entries: set[str] = set()
        self._settling: dict[str, Instant] = {}
        self._uncovered: dict[str, Instant] = {}
        self.pending: dict[str, _Pending] = {}
        self._unsent: set[str] = set()
        self._queue: list[tuple[Instant, list[BrokerAction]]] = []
        self._expect = Expectation()
        self._wake = asyncio.Event()
        self._last_diffs: tuple[str, ...] = ()
        self._last_ignored: tuple[str, ...] = ()
        self.synced = False
        self.stopped: str | None = None
        self.requests: list[str] = []

    def _log(self, entry: dict[str, JsonValue]) -> None:
        """One broker event, stamped with the current time, to the live log and the recording."""
        self._sink({"t": self._clock.now(), **entry})

    def bind(self, mirror: MirrorBroker, note: _Note, log: _Log | None = None) -> None:
        """Called by the Live_Runner when the session opens."""
        self._mirror = mirror
        self._note = note
        if log is not None:
            self._sink = log
        self._expect = Expectation.of(mirror)

    # ------------------------------------------------------------ the router's view

    def entry_block(self, instrument: str) -> str | None:
        if self.stopped is not None:
            return self.stopped
        if self.outage is not None:
            return self.outage.detail
        block = self.contract_blocks.get(instrument)
        if block is not None:
            return block.detail
        if instrument not in self.contracts:
            return f"no checked contract id for {instrument}"
        found = self._blocks.read()
        if isinstance(found, BlocksUnreadable):
            return found.reason
        if found:
            return f"persistent block in force: {found[0].kind}"
        return None

    def broker_positions(self) -> Mapping[str, int] | None:
        if self.states is None:
            return None
        return {i: s.position for i, s in self.states.items()}

    def broker_working_entries(self) -> Mapping[str, int]:
        out: dict[str, int] = {}
        for inst, state in (self.states or {}).items():
            for o in state.orders:
                eid = self.journal.engine_for_order(o)
                if o.working and eid is not None and self.journal.is_entry(eid):
                    out[inst] = out.get(inst, 0) + o.qty - o.filled_qty
        return out

    def external_blocks(self) -> tuple[ExternalBlock, ...]:
        """The in-memory broker blocks the guards add at each Decision_Time."""
        out: list[ExternalBlock] = []
        if self.stopped is not None:
            out.append(ExternalBlock(STOPPED_BLOCK, None, self.stopped))
        if not self.synced:
            out.append(
                ExternalBlock(SYNC_BLOCK, None, "the broker state has not been compared yet")
            )
        if self.outage is not None:
            out.append(self.outage)
        out.extend(self.contract_blocks[i] for i in sorted(self.contract_blocks))
        return tuple(out)

    # ------------------------------------------------------------ queueing

    def submit(self, t: Instant, actions: Sequence[BrokerAction], expectation: Expectation) -> None:
        """Queue one Decision_Time's actions and the book after it; never awaits."""
        if actions:
            self._queue.append((t, list(actions)))
        self._expect = expectation
        self._wake.set()

    # ------------------------------------------------------------ start and loop

    async def start(self) -> None:
        """The contract check, then the first comparison, before any order (Req 24.11, 24.16)."""
        await self.check_contracts()
        await self.sync()

    async def run(self, until_ns: Instant) -> None:
        """Process queued actions and sync after each Decision_Time until ``until_ns``.

        If it stops on an error, entries stay blocked for the rest of the run.
        """
        try:
            await self._loop(until_ns)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.stopped = f"the broker task stopped ({type(exc).__name__}); entries blocked"
            self._note(self.stopped)
            raise

    async def _loop(self, until_ns: Instant) -> None:
        clock = self._clock
        while True:
            now = clock.now()
            if now >= until_ns:
                return
            if not self._wake.is_set():
                wake = min([until_ns, *self._deadlines()])
                if wake > now:
                    await within(clock, self._wake.wait(), wake - now)
            self._wake.clear()
            if clock.now() >= until_ns:
                return
            await self.step()

    async def step(self) -> None:
        """Dispatch everything queued, then sync once."""
        while self._queue:
            t, actions = self._queue.pop(0)
            for action in actions:
                await self.dispatch(t, action)
        await self.sync()

    def _deadlines(self) -> list[Instant]:
        confirm = self._live.stop_confirm_s * NS_PER_SECOND
        out = [since + confirm for since in self._uncovered.values()]
        out.extend(self._settling.values())
        if self.outage is None:
            out.append(self.adapter.health.silent_since() + self._live.outage_s * NS_PER_SECOND)
        return out

    # ------------------------------------------------------------ contracts

    async def check_contracts(self) -> None:
        """Resolve and log each instrument's contract; block a mismatch for the session."""
        for inst in self._instruments:
            expected = self._expected_contracts.get(inst)
            found = await self.adapter.resolve_contract(inst)
            self.requests.append("resolve_contract")
            resolved = None if isinstance(found, ResolveError) else found.id
            self._log(
                {
                    "event": "contract_check",
                    "instrument": inst,
                    "expected": expected,
                    "resolved": resolved,
                    "failure": found.reason if isinstance(found, ResolveError) else None,
                }
            )
            if isinstance(found, ResolveError):
                detail = (
                    f"{inst}: expected contract {expected}, resolution failed ({found.reason}); "
                    "orders blocked for the session"
                )
            elif expected is None or found.id != expected:
                detail = (
                    f"{inst}: expected contract {expected}, resolved {found.id}; "
                    "orders blocked for the session"
                )
            else:
                self.contracts[inst] = found.id
                continue
            self.contract_blocks[inst] = ExternalBlock(CONTRACT_BLOCK, inst, detail)
            self._note(detail)

    # ------------------------------------------------------------ dispatch

    async def dispatch(self, t: Instant, action: BrokerAction) -> None:
        inst = action.instrument if not isinstance(action, ExitAction) else action.order.instrument
        if inst in self._ignored:
            return
        contract = self.contracts.get(inst)
        if contract is None:
            if isinstance(action, PlaceAction):
                self._unsent.update(
                    o.client_id for o in (action.entry, action.stop, *action.targets)
                )
            self._log({"event": "broker_skip", "instrument": inst, "reason": "no checked contract"})
            return
        if isinstance(action, PlaceAction):
            await self._place_entry(t, action, contract)
        elif isinstance(action, ExitAction):
            await self._exit(t, action, contract)
        elif self.outage is not None:
            self._log(
                {
                    "event": "broker_skip",
                    "instrument": inst,
                    "reason": "outage: no modify or cancel",
                }
            )
        elif isinstance(action, ModifyAction):
            await self._modify(action)
        else:
            await self._cancel(action)

    async def _place_entry(self, t: Instant, a: PlaceAction, contract: str) -> None:
        blocked = self.entry_block(a.instrument)
        if blocked is not None:
            self._unsent.update(o.client_id for o in (a.entry, a.stop, *a.targets))
            self._log({"event": "broker_skip", "instrument": a.instrument, "reason": blocked})
            return
        entry, stop = a.entry, a.stop
        if not a.targets or entry.price is None or stop.price is None:
            return
        tp1 = a.targets[0]
        assert tp1.price is not None
        cid = self._ids.next()
        legs: dict[str, str] = {o.role: o.client_id for o in (stop, *a.targets)}
        self.journal.sent(cid, entry.client_id, entry.instrument, "entry", t, legs)
        intent = EntryIntent(
            cid, contract, entry.side, entry.qty, entry.kind, entry.price, stop.price, tp1.price
        )
        result = await self.adapter.place_bracketed(intent)
        self.requests.append("place_bracketed")
        await self._placed(result, _Pending(cid, entry.client_id, entry.instrument, intent, t))

    async def _placed(self, result: PlaceResult, p: _Pending) -> None:
        self._log(
            {
                "event": "order_sent",
                "client_id": p.client_id,
                "engine_id": p.engine_id,
                "instrument": p.instrument,
                "outcome": result.outcome,
                "error_code": result.error_code,
            }
        )
        if result.order_id is not None:
            self.journal.ack(p.client_id, result.order_id)
        if result.outcome == "accepted":
            self.pending.pop(p.client_id, None)
            if p.cancel_wanted and result.order_id is not None:
                await self.adapter.cancel(result.order_id)
                self.requests.append("cancel")
            return
        if result.outcome == "no_response":
            self.pending[p.client_id] = p
            return
        self.pending.pop(p.client_id, None)
        if isinstance(p.request, EntryIntent):
            await self._bracket_failure(
                p.instrument, f"the broker rejected the bracket ({result.detail})"
            )
        else:
            self._note(f"{p.instrument}: order {p.client_id} rejected ({result.detail})")

    async def _exit(self, t: Instant, a: ExitAction, contract: str) -> None:
        cid = self._ids.next()
        order = a.order
        self.journal.sent(cid, order.client_id, order.instrument, "exit", t)
        request = OrderRequest(cid, contract, a.side, a.qty, "market")
        result = await self.adapter.place(request)
        self.requests.append("place")
        await self._placed(result, _Pending(cid, order.client_id, order.instrument, request, t))
        if result.outcome != "accepted" or order.setup_key is None or self._mirror is None:
            return
        # The bracket legs stay at the broker after a market exit: cancel them now.
        legs = {
            o.client_id
            for o in (*self._expect.working, *self._mirror.working_orders())
            if o.setup_key == order.setup_key and o.role in _LEG_ROLES
        }
        for leg in sorted(legs):
            oid = self.journal.order_for_engine(leg)
            if oid is not None:
                await self.adapter.cancel(oid)
                self.requests.append("cancel")

    async def _modify(self, a: ModifyAction) -> None:
        oid = self.journal.order_for_engine(a.client_id)
        if oid is None:
            return  # a leg not live yet: set to the planned price after the fill
        if a.kind == "stop":
            result = await self.adapter.modify(oid, stop_price=a.price)
        else:
            result = await self.adapter.modify(oid, limit_price=a.price)
        self.requests.append("modify")
        self._log({"event": "order_modify", "engine_id": a.client_id, "outcome": result.outcome})

    async def _cancel(self, a: CancelAction) -> None:
        oid = self.journal.order_for_engine(a.client_id)
        if oid is None:
            cid = self.journal.client_for_engine(a.client_id)
            if cid is not None and cid in self.pending:
                self.pending[cid].cancel_wanted = True
            return
        result = await self.adapter.cancel(oid)
        self.requests.append("cancel")
        self._log({"event": "order_cancel", "engine_id": a.client_id, "outcome": result.outcome})

    # ------------------------------------------------------------ protective close

    async def _bracket_failure(self, inst: str, reason: str) -> None:
        now = self._clock.now()
        self._blocks.add(BlockRecord("bracket_failure", None, f"{inst}: {reason}", now))
        states = await self.adapter.broker_state()
        self.requests.append("broker_state")
        position = 0
        if not isinstance(states, BrokerFailure):
            self.states = states
            state = states.get(inst)
            position = 0 if state is None else state.position
        closed = await self._close(inst, position, now) if position else "no open position"
        self._note(f"{inst}: {reason}; {closed}; new entries blocked until fse clear")

    async def _close(self, inst: str, position: int, now: Instant) -> str:
        contract = (
            self.contracts.get(inst)
            or (self.states or {}).get(inst, BrokerState(inst, "")).contract_id
        )
        if not contract:
            return "no contract id to close the position"
        cid = self._ids.next()
        self.journal.sent(cid, f"close-{cid}", inst, "close", now)
        request = OrderRequest(cid, contract, _closing(position), abs(position), "market")
        result = await self.adapter.place(request)
        self.requests.append("place")
        await self._placed(result, _Pending(cid, f"close-{cid}", inst, request, now))
        return f"sent a market order closing {describe_position(position)} ({result.outcome})"

    # ------------------------------------------------------------ sync

    async def sync(self) -> None:
        """Read the broker, apply fills, adjust legs, resend, protect, reconcile."""
        now = self._clock.now()
        states = await self.adapter.broker_state()
        self.requests.append("broker_state")
        if isinstance(states, BrokerFailure):
            self._failed(now, states)
            return
        orders = await self.adapter.search_orders(self._session_start)
        self.requests.append("search_orders")
        if isinstance(orders, BrokerFailure):
            self._failed(now, orders)
            return
        self.states = states
        self._apply_fills(orders, states, now)
        sent = len(self.requests)
        await self._adjust(now)
        await self._resubmit()
        await self._protect(now, states)
        if any(r in _ORDER_REQUESTS for r in self.requests[sent:]):
            fresh = await self.adapter.broker_state()  # compare what the changes left
            self.requests.append("broker_state")
            if isinstance(fresh, BrokerFailure):
                self._failed(now, fresh)
                return
            self.states = fresh
        self._reconcile(now)

    def _failed(self, now: Instant, failure: BrokerFailure) -> None:
        self._log(
            {"event": "broker_failure", "endpoint": failure.endpoint, "detail": failure.detail}
        )
        silent = now - self.adapter.health.silent_since()
        if self.outage is None and silent >= self._live.outage_s * NS_PER_SECOND:
            detail = (
                f"no successful ProjectX response for {self._live.outage_s} s ({failure}); "
                "new entries blocked, broker stops and targets left in place"
            )
            self.outage = ExternalBlock(OUTAGE_BLOCK, None, detail)
            self._note(detail)

    def _apply_fills(
        self, orders: Sequence[BrokerOrder], states: Mapping[str, BrokerState], now: Instant
    ) -> None:
        fills: dict[str, int] = {}
        for o in orders:
            eid = self.journal.engine_for_order(o)
            if eid is None:
                continue
            qty = o.qty if o.filled else o.filled_qty
            if qty:
                fills[eid] = max(fills.get(eid, 0), qty)
        self._fills = fills
        for eid in sorted(fills):
            if not self.journal.is_entry(eid) or eid in self._filled_entries:
                continue
            self._filled_entries.add(eid)
            self._adopt(eid, states, now)
            self._settling[eid] = now + self._live.stop_confirm_s * NS_PER_SECOND
            self._log({"event": "entry_filled", "engine_id": eid, "qty": fills[eid]})

    def _adopt(self, entry_id: str, states: Mapping[str, BrokerState], now: Instant) -> None:
        legs = self.journal.legs_of(entry_id)
        entry = self._mirror.known_order(entry_id) if self._mirror is not None else None
        if entry is None or not legs:
            return
        state = states.get(entry.instrument)
        if state is None:
            return
        closing: Side = "sell" if entry.side == "buy" else "buy"
        free = [
            o
            for o in state.orders
            if o.working
            and o.side == closing
            and self.journal.engine_for_order(o) is None
            and (o.tag is None or not o.tag.startswith(CLIENT_ID_PREFIX))
        ]
        for role, kind in (("stop", "stop"), ("tp1", "limit")):
            found = [o for o in free if o.kind == kind]
            if role in legs and len(found) == 1:
                self.journal.adopt(found[0].order_id, legs[role], entry.instrument, now)

    async def _adjust(self, now: Instant) -> None:
        """Move adopted legs to the planned prices; add TP2 (TP1_Partial_BE)."""
        mirror = self._mirror
        for eid, deadline in list(self._settling.items()):
            if mirror is None or now >= deadline or self.outage is not None:
                self._settling.pop(eid, None)
                continue
            legs = self.journal.legs_of(eid)
            done = True
            for role, leg in legs.items():
                planned = mirror.known_order(leg)
                if planned is None or planned.price is None:
                    continue
                if role == "tp2":
                    done = await self._place_tp2(planned, now) and done
                    continue
                oid = self.journal.order_for_engine(leg)
                current = self._broker_order(oid)
                if oid is None or current is None:
                    done = False
                    continue
                size = planned.qty if current.qty != planned.qty else None
                if current.price == planned.price and size is None:
                    continue
                if role == "stop":
                    result = await self.adapter.modify(oid, size=size, stop_price=planned.price)
                else:
                    result = await self.adapter.modify(oid, size=size, limit_price=planned.price)
                self.requests.append("modify")
                done = done and result.ok
            if done:
                self._settling.pop(eid, None)

    async def _place_tp2(self, planned: Order, now: Instant) -> bool:
        if self.journal.client_for_engine(planned.client_id) is not None:
            return True
        contract = self.contracts.get(planned.instrument)
        if contract is None or planned.price is None:
            return False
        cid = self._ids.next()
        self.journal.sent(cid, planned.client_id, planned.instrument, "tp2", now)
        request = OrderRequest(cid, contract, planned.side, planned.qty, "limit", planned.price)
        result = await self.adapter.place(request)
        self.requests.append("place")
        await self._placed(
            result, _Pending(cid, planned.client_id, planned.instrument, request, now)
        )
        return result.outcome == "accepted"

    def _broker_order(self, order_id: int | None) -> BrokerOrder | None:
        if order_id is None:
            return None
        for state in (self.states or {}).values():
            for o in state.orders:
                if o.order_id == order_id:
                    return o
        return None

    async def _resubmit(self) -> None:
        """Look up each pending send; resend only when nothing is found (Req 24.14-24.15).

        A market exit or close is cut to the open quantity on its closing side,
        counting the resends before it in this pass as filled, and dropped when
        nothing is left to close.
        """
        timeout_s = self._live.lookup_timeout_s
        positions = {i: s.position for i, s in (self.states or {}).items()}
        for cid, p in list(self.pending.items()):
            if self.outage is not None:
                return
            lookup = await within(
                self._clock,
                self.adapter.find_by_client_id(
                    cid, self.journal, start_ns=p.sent_at - _LOOKUP_MARGIN_NS, timeout_s=timeout_s
                ),
                timeout_s * NS_PER_SECOND,
            )
            self.requests.append("find_by_client_id")
            if isinstance(lookup, TimedOut) or lookup.outcome == "failed":
                self._log({"event": "resubmit_skipped", "client_id": cid})
                continue
            if lookup.outcome == "found":
                self.journal.ack(cid, lookup.order_ids[0])
                del self.pending[cid]
                if p.cancel_wanted:
                    await self.adapter.cancel(lookup.order_ids[0])
                    self.requests.append("cancel")
                continue
            if p.cancel_wanted:
                del self.pending[cid]
                continue
            if isinstance(p.request, EntryIntent):
                result = await self.adapter.place_bracketed(p.request)
                self.requests.append("place_bracketed")
            else:
                if p.request.kind == "market":
                    held = positions.get(p.instrument, 0)
                    qty = min(p.request.qty, _closable(held, p.request.side))
                    if qty == 0:
                        del self.pending[cid]
                        self._log(
                            {
                                "event": "resubmit_dropped",
                                "client_id": cid,
                                "position": describe_position(held),
                            }
                        )
                        continue
                    if qty != p.request.qty:
                        p.request = replace(p.request, qty=qty)
                    positions[p.instrument] = held + _sign(p.request.side) * qty
                result = await self.adapter.place(p.request)
                self.requests.append("place")
            self._log({"event": "resubmitted", "client_id": cid, "outcome": result.outcome})
            await self._placed(result, p)

    async def _protect(self, now: Instant, states: Mapping[str, BrokerState]) -> None:
        """Close a position no working stop covers within ``live.stop_confirm_s`` (Req 24.9)."""
        confirm = self._live.stop_confirm_s * NS_PER_SECOND
        for inst in self._instruments:
            state = states.get(inst)
            position = 0 if state is None else state.position
            if position == 0 or state is None or inst not in self.contracts:
                self._uncovered.pop(inst, None)
                continue
            closing = _closing(position)
            cover = sum(
                o.qty - o.filled_qty
                for o in state.orders
                if o.working and o.kind == "stop" and o.side == closing
            )
            if cover >= abs(position):
                self._uncovered.pop(inst, None)
                continue
            since = self._uncovered.setdefault(inst, now)
            if now - since < confirm:
                continue
            self._uncovered.pop(inst, None)
            reason = (
                f"no working stop-loss covers the open {describe_position(position)} "
                f"within {self._live.stop_confirm_s} s"
            )
            self._blocks.add(BlockRecord("bracket_failure", None, f"{inst}: {reason}", now))
            if any(
                p.instrument == inst and p.engine_id.startswith("close-")
                for p in self.pending.values()
            ):
                continue
            closed = await self._close(inst, position, now)
            self._note(f"{inst}: {reason}; {closed}; new entries blocked until fse clear")

    def _reconcile(self, now: Instant) -> None:
        hidden: set[str] = set(self._unsent)
        for eid in self._settling:
            hidden.update(self.journal.legs_of(eid).values())
        for p in self.pending.values():
            hidden.add(p.engine_id)
        expected = expected_view(
            self._expect.working, self._expect.trades, broker_fills=self._fills, hidden=hidden
        )
        seen = broker_view(self.states or {}, self.journal.engine_for_order, hidden=hidden)
        result = reconcile(expected, seen, instruments=self._instruments, ignored=self._ignored)
        if result.ignored_positions != self._last_ignored:
            self._last_ignored = result.ignored_positions
            if result.ignored_positions:
                self._note(
                    "ignored instruments, not compared or traded: "
                    + ", ".join(result.ignored_positions)
                )
        self._log(
            {
                "event": "reconciliation",
                "differences": list(result.differences),
                "ignored_positions": list(result.ignored_positions),
            }
        )
        recovering = self.outage is not None
        if result.differences:
            detail = "; ".join(result.differences)
            self._blocks.add(BlockRecord("reconciliation", None, detail, now))
            if result.differences != self._last_diffs:
                self._note(
                    "reconciliation differences, new entries blocked until fse clear: " + detail
                )
        elif recovering:
            self.outage = None
            self._note(
                "ProjectX answers again and the comparison is clean: the outage block is lifted"
            )
        self._last_diffs = result.differences
        self.synced = True

    @property
    def instruments(self) -> tuple[str, ...]:
        return self._instruments
