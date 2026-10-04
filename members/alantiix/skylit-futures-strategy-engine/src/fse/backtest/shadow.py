"""The ShadowBook: Shadow_Trades and the final status of every Setup_Key (design §19, Req 19).

The Backtester feeds one :class:`ShadowBook` the same bar stream and
decision-log payloads as the account: :meth:`ShadowBook.on_bar` in the bar
phase, :meth:`ShadowBook.on_main_fills` with the account's fills, and
:meth:`ShadowBook.on_decision` in the log phase of each Decision_Time. At the
Flat_Deadline :meth:`ShadowBook.flat_close` closes every open Shadow_Trade, and
:meth:`ShadowBook.finish_session` returns one :class:`SetupRecord` per
Setup_Key of the session.

**Governing evaluation** of a Setup_Key: the Gate results from the latest
Decision_Time at or before the start of the key's first Tap at which the key
was emitted; if the key was first emitted during that Tap, its first
emission. The first Tap is the Tap of the source Node (symbol, metric, strike)
whose sequence number is the key's ``tap_seq``, and it starts at the open of
its first bar. A gap-through entry fill with no Tap counts as the first Tap:
the bar the key's entry (or its governing Shadow_Trade entry) filled on.

**Final status** (exactly one, Req 19.1-19.5):

- ``untapped``: no first Tap and no entry fill before the session ended;
- ``rejected``: the governing evaluation has a failing enabled Gate; the
  record lists every failing Gate, in Gate order (Req 19.3);
- ``filled``: any part of the key's entry filled (Req 19.4);
- ``cancelled``: otherwise, with the latest cause that cancelled or blocked
  the entry: a Cancel_Trigger, max age, a Lockout rule (Kill_Switch id),
  Flatten_Time, ``max_open``, ``size_zero``, ``no_target_node``, an account
  rejection, an external block, or ``end_of_rth`` when nothing else applies.

**Shadow_Trades** (Req 19.8-19.9). The governing evaluation is only known once
the first Tap is seen, so every rejected emission that could still govern gets
a speculative shadow placed at its own Decision_Time: one bracket with the
Candidate_Setup's entry, stop and Order_Planner targets at the base contracts
(``fse.engine.sizing.base_contracts``), in its own Order_Planner book and its
own Fill_Simulator book. A candidate is dropped once a later emission is at
least one bar old with no first Tap seen (the Tap would have to start after
that later emission). When the first Tap is seen the governing candidate is
kept and the others are dropped, so each rejected key ends with exactly one
:class:`ShadowOutcome`.

A shadow follows the Order_Planner lifecycle (Cancel_Triggers, max age,
invalidation, stop moves, Flatten_Time) with a Risk_Manager state that has no
Lockout. It never reaches the account, the Risk_Manager, the Kill_Switches,
``max_open`` or sizing, and nothing here changes the engine state.

``mode`` (for tests): ``"off"`` simulates no Shadow_Trade, and
``"every_setup"`` also simulates a shadow for every emission that is not
rejected, so a run can show that Shadow_Trades leave the account unchanged
(Property 57).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Final, Literal

from fse.config.schema.fills import FillsConfig
from fse.config.schema.gates import GateId
from fse.config.schema.sizing import SizingConfig
from fse.engine.planner import (
    Accepted,
    CancelOrder,
    EntryCancelled,
    ModifyOrder,
    OrderBook,
    OrderFill,
    OrderIntent,
    PlaceBracket,
    PlacementRejection,
    Plan,
    PlannerConfig,
    PlannerContext,
    SubmitExit,
    manage,
    place,
)
from fse.engine.planner import on_bar as planner_on_bar
from fse.engine.risk import RiskState
from fse.engine.sizing import SIZING_STEPS, SizedSetup, SizingRejection, StepOutcome, base_contracts
from fse.engine.step import (
    BlockCancelled,
    BlockedEntry,
    DecisionPayload,
    EntryRejected,
    SetupDecision,
)
from fse.engine.taps import BASE_INTERVAL_S, NodeId, TapState
from fse.engine.types import Bar, MissingInput, Order, SetupKey, Trade
from fse.sim.fills import FillEvent, SimBook, close_all
from fse.sim.fills import on_bar as fill_bar
from fse.timekit import NS_PER_SECOND, Instant

__all__ = [
    "END_OF_RTH",
    "FINAL_STATUSES",
    "SHADOW_MODES",
    "FinalStatus",
    "SetupRecord",
    "ShadowBook",
    "ShadowMode",
    "ShadowOutcome",
]

type FinalStatus = Literal["filled", "cancelled", "rejected", "untapped"]
type ShadowMode = Literal["rejected", "off", "every_setup"]

FINAL_STATUSES: Final[tuple[FinalStatus, ...]] = ("filled", "cancelled", "rejected", "untapped")
SHADOW_MODES: Final[tuple[ShadowMode, ...]] = ("rejected", "off", "every_setup")
END_OF_RTH: Final = "end_of_rth"
"""The cause of a cancelled key whose entry no event cancelled or blocked."""

_BAR_NS: Final = BASE_INTERVAL_S * NS_PER_SECOND
_ID_PREFIX: Final = "shadow-"
_NOT_BLOCKING: Final = frozenset({"already_placed", "not_a_plus"})


# ---------------------------------------------------------------- records


@dataclass(frozen=True, slots=True)
class ShadowOutcome:
    """The Shadow_Trade of one rejected Setup_Key (Req 19.8).

    ``placed_at`` is the governing Decision_Time. ``trade`` is the closed
    Shadow_Trade when its entry filled, else ``None``. ``not_placed`` names
    why no shadow entry could be placed (``no_conversion_price``,
    ``size_zero``, ``flat_deadline`` or an Order_Planner placement
    rejection), else ``None``.
    """

    placed_at: Instant
    trade: Trade | None
    not_placed: str | None = None

    @property
    def filled(self) -> bool:
        return self.trade is not None


@dataclass(frozen=True, slots=True)
class SetupRecord:
    """One Setup_Key's final status in the Gate_Funnel (see the module notes).

    ``failing`` lists the governing evaluation's failing Gates for a rejected
    key and is empty otherwise; ``cause`` is set for a cancelled key only.
    ``governing_at`` and ``first_tap_at`` are ``None`` for an untapped key;
    ``shadow`` is set for a rejected key only (``None`` when the run simulated
    no Shadow_Trade).
    """

    key: SetupKey
    status: FinalStatus
    failing: tuple[GateId, ...]
    cause: tuple[str, ...]
    first_emitted_at: Instant
    governing_at: Instant | None
    first_tap_at: Instant | None
    shadow: ShadowOutcome | None


# ---------------------------------------------------------------- one shadow


def _renamed(plan: Plan, sid: str) -> Plan:
    def rename(order: Order) -> Order:
        return replace(order, client_id=f"{sid}-{order.role}")

    return replace(
        plan,
        sid=sid,
        entry=rename(plan.entry),
        stop=rename(plan.stop),
        targets=tuple(rename(o) for o in plan.targets),
    )


def _route(sim: SimBook, intents: Iterable[OrderIntent]) -> SimBook:
    """Planner intents to a shadow's Fill_Simulator book, as the account router does."""
    for intent in intents:
        if isinstance(intent, ModifyOrder):
            if sim.order(intent.client_id) is not None:
                sim = sim.modify(intent.client_id, intent.price, intent.at)
        elif isinstance(intent, CancelOrder):
            if intent.client_id in sim.orders:
                sim = sim.cancel(intent.client_id, intent.at)
        elif isinstance(intent, SubmitExit):
            order = intent.order
            if order.setup_key in sim.trades and order.client_id not in sim.orders:
                sim = sim.submit_exit(order)
        else:
            raise ValueError("the Order_Planner placed a bracket outside its setup phase")
    return sim


class _Shadow:
    """One simulated shadow bracket: its own planner book and Fill_Simulator book."""

    __slots__ = ("book", "fill_ns", "instrument", "not_placed", "seq", "sim", "trade")

    def __init__(
        self,
        instrument: str,
        seq: int = 0,
        book: OrderBook | None = None,
        sim: SimBook | None = None,
        not_placed: str | None = None,
    ) -> None:
        self.instrument = instrument
        self.seq = seq
        self.book = book
        self.sim = sim
        self.not_placed = not_placed
        self.fill_ns: Instant | None = None
        self.trade: Trade | None = None

    @property
    def live(self) -> bool:
        """Whether an order can still fill or a position is open."""
        return self.sim is not None and bool(self.sim.orders or self.sim.trades)

    def on_bar(
        self, bar: Bar, fills: FillsConfig, planner: PlannerConfig, rth: tuple[Instant, Instant]
    ) -> None:
        assert self.sim is not None  # only live shadows get bars
        sim, events = fill_bar(self.sim, bar, fills, rth=rth)
        for event in events:
            if event.order.role == "entry" and self.fill_ns is None:
                self.fill_ns = event.fill.bar_open_ns
            if event.closed is not None:
                self.trade = event.closed
        if self.book is not None:
            given = [OrderFill(e.order, e.fill) for e in events]
            self.book, intents, _ = planner_on_bar(self.book, bar, given, planner)
            sim = _route(sim, intents)
        self.sim = sim

    def on_decision(self, ctx: PlannerContext, planner: PlannerConfig) -> None:
        """The Order_Planner's management phase at ``ctx.t`` (no Lockout in ``ctx``)."""
        if self.book is None or self.sim is None:
            return
        self.book, intents, _ = manage(self.book, ctx, planner)
        self.sim = _route(self.sim, intents)

    def close(self, bar_open_ns: Instant, price: int, fills: FillsConfig) -> None:
        """Close the open position at ``price`` and cancel every order (the Flat_Deadline)."""
        assert self.sim is not None
        self.sim, events = close_all(
            self.sim, bar_open_ns, {self.instrument: price}, fills, reason="flat_deadline"
        )
        for event in events:
            if event.closed is not None:
                self.trade = event.closed
        self.book = None


@dataclass(slots=True)
class _Candidate:
    """One emission of a Setup_Key that may still govern it."""

    t: Instant
    decision: SetupDecision
    rejected: bool
    shadow: _Shadow | None


@dataclass(slots=True)
class _Track:
    """One Setup_Key of the current session."""

    key: SetupKey
    node: NodeId
    first_t: Instant
    candidates: list[_Candidate]
    governing: _Candidate | None = None
    tap_at: Instant | None = None


def _tap_starts(taps: TapState, session: date) -> dict[tuple[NodeId, int], Instant]:
    """The start (first bar open) of each Tap of ``session``, by (Node, sequence number)."""
    if taps.session != session:
        return {}
    return {(tap.node, tap.seq): tap.first_open_ns for tap in (*taps.ended_taps, *taps.open_taps)}


def _decision_cause(d: SetupDecision) -> tuple[str, ...] | None:
    """What blocked the entry of ``d`` at its Decision_Time, if anything.

    Only for a key whose entry was never placed: a later emission of a placed
    key blocks nothing, since the key is never placed twice.
    """
    if isinstance(d.sizing, SizingRejection):
        return (d.sizing.reason, d.sizing.step)
    p = d.placement
    if isinstance(p, PlacementRejection) and p.reason not in _NOT_BLOCKING:
        return (p.reason, *p.detail)
    if isinstance(p, BlockedEntry):
        return ("external_block", *p.blocks)
    if d.sizing is None and not d.evaluation.failing and not d.setup.priced:
        return ("no_conversion_price",)
    return None


# ---------------------------------------------------------------- the book


class ShadowBook:
    """Shadow_Trades and final statuses for one run (see the module notes)."""

    def __init__(
        self,
        fills: FillsConfig,
        planner: PlannerConfig,
        sizing: SizingConfig,
        mode: ShadowMode = "rejected",
    ) -> None:
        if mode not in SHADOW_MODES:
            raise ValueError(f"the shadow mode must be one of {SHADOW_MODES}, got {mode!r}")
        self._fills = fills
        self._planner = planner
        self._sizing = sizing
        self._mode: ShadowMode = mode
        self._seq = 0
        self._records: list[SetupRecord] = []
        self._session: date | None = None
        self._rth: tuple[Instant, Instant] = (0, 1)
        self._flat = False
        self._tracks: dict[SetupKey, _Track] = {}
        self._open: dict[SetupKey, _Track] = {}
        self._live: dict[int, _Shadow] = {}
        self._entry_fill: dict[SetupKey, Instant] = {}
        self._causes: dict[SetupKey, tuple[str, ...]] = {}
        self._placed: set[SetupKey] = set()
        self._last_bar: dict[str, Bar] = {}

    @property
    def records(self) -> tuple[SetupRecord, ...]:
        """Every finished session's records, in session and first-emission order."""
        return tuple(self._records)

    @property
    def shadow_trades(self) -> tuple[Trade, ...]:
        """The filled Shadow_Trades of :attr:`records`, in record order."""
        return tuple(
            r.shadow.trade for r in self._records if r.shadow is not None and r.shadow.trade
        )

    # ------------------------------------------------------------ session

    def start_session(self, session: date, rth: tuple[Instant, Instant]) -> None:
        if self._session is not None:
            raise ValueError(f"session {self._session} has not finished")
        self._session = session
        self._rth = rth
        self._flat = False

    def _require_session(self) -> date:
        if self._session is None:
            raise ValueError("no session has started")
        return self._session

    # ------------------------------------------------------------ inputs

    def on_bar(self, bar: Bar) -> None:
        """One closed 1-minute bar into every live shadow of its instrument."""
        self._require_session()
        self._last_bar[bar.instrument] = bar
        for seq, shadow in list(self._live.items()):
            if shadow.instrument != bar.instrument:
                continue
            shadow.on_bar(bar, self._fills, self._planner, self._rth)
            if not shadow.live:
                del self._live[seq]

    def on_main_fills(self, events: Iterable[FillEvent]) -> None:
        """The account's fills: the first entry fill of each key marks it filled."""
        session = self._require_session()
        for event in events:
            key = event.order.setup_key
            if event.order.role == "entry" and key is not None and key.session == session:
                self._entry_fill.setdefault(key, event.fill.bar_open_ns)

    def on_decision(
        self, t: Instant, payload: DecisionPayload, context: PlannerContext, taps: TapState
    ) -> None:
        """The log phase of Decision_Time ``t``: manage, record causes, add and resolve."""
        session = self._require_session()
        if payload.session != session or payload.t != t:
            raise ValueError(f"the payload of {payload.session} at {payload.t} is not at {t}")
        ctx = replace(context, risk=RiskState())
        for seq, live in list(self._live.items()):
            live.on_decision(ctx, self._planner)
            if not live.live:
                del self._live[seq]
        self._record_causes(payload, session)
        seen: set[SetupKey] = set()
        for d in payload.setups:
            key = d.setup.key
            if key in seen:
                continue
            seen.add(key)
            track = self._tracks.get(key)
            if track is None:
                src = d.setup.source
                track = _Track(key, (src.symbol, src.metric, src.strike), t, [])
                self._tracks[key] = track
                self._open[key] = track
            if track.governing is not None:
                continue
            rejected = bool(d.evaluation.failing)
            shadow: _Shadow | None = None
            if self._mode == "every_setup" or (self._mode == "rejected" and rejected):
                shadow = self._place(d, ctx)
            track.candidates.append(_Candidate(t, d, rejected, shadow))
        starts = _tap_starts(taps, session)
        for key, track in list(self._open.items()):
            first = self._first_tap(track, starts)
            if first is not None:
                self._resolve(track, first)
                del self._open[key]
            else:
                self._prune(track, t)

    def flat_close(self, closing: Mapping[str, Bar], deadline: Instant) -> None:
        """Close every open Shadow_Trade at the open of the bar opening at the Flat_Deadline.

        ``closing`` holds the first bar opening at or after the deadline per
        instrument; without one, the last bar's close is used.
        """
        self._require_session()
        for shadow in self._live.values():
            assert shadow.sim is not None
            if shadow.sim.trades:
                bar = closing.get(shadow.instrument)
                if bar is not None and bar.o_t is not None:
                    shadow.close(bar.open_ns, bar.o_t, self._fills)
                else:
                    last = self._last_bar[shadow.instrument]
                    assert last.c_t is not None  # futures bars carry tick prices
                    shadow.close(deadline, last.c_t, self._fills)
            else:
                shadow.sim, shadow.book = SimBook(), None
        self._live.clear()
        self._flat = True

    def finish_session(self, taps: TapState) -> tuple[SetupRecord, ...]:
        """The session's records, in first-emission order; the session's state is cleared."""
        session = self._require_session()
        starts = _tap_starts(taps, session)
        out: list[SetupRecord] = []
        for track in self._tracks.values():
            if track.governing is None:
                first = self._first_tap(track, starts)
                if first is not None:
                    self._resolve(track, first)
            out.append(self._record(track))
        self._records.extend(out)
        self._session = None
        self._tracks, self._open, self._live = {}, {}, {}
        self._entry_fill, self._causes, self._last_bar = {}, {}, {}
        self._placed = set()
        return tuple(out)

    # ------------------------------------------------------------ internals

    def _record_causes(self, payload: DecisionPayload, session: date) -> None:
        causes = self._causes
        for d in payload.setups:
            key = d.setup.key
            if isinstance(d.placement, PlaceBracket):
                self._placed.add(key)
            if key in self._placed:
                continue
            cause = _decision_cause(d)
            if cause is not None:
                causes[key] = cause
        for e in payload.management:
            if isinstance(e, EntryCancelled) and e.key.session == session:
                causes[e.key] = tuple(e.causes)
            elif isinstance(e, BlockCancelled) and e.key.session == session:
                causes[e.key] = ("external_block", *e.blocks)
        for b in payload.bar_events:
            if isinstance(b, EntryRejected) and b.key.session == session:
                causes[b.key] = ("account_rejection", b.reason)

    def _place(self, d: SetupDecision, ctx: PlannerContext) -> _Shadow:
        """A shadow bracket for ``d`` at its Decision_Time, at the base contracts."""
        c = d.setup
        instrument = c.key.instrument
        if self._flat:  # after the Flat_Deadline, as the account refuses entries
            return _Shadow(instrument, not_placed="flat_deadline")
        if not c.priced:
            return _Shadow(instrument, not_placed="no_conversion_price")
        q = base_contracts(c, self._sizing)
        if isinstance(q, MissingInput) or q < 1:
            return _Shadow(instrument, not_placed="size_zero")
        steps = (
            StepOutcome(SIZING_STEPS[0], q, True),
            *(StepOutcome(step, q, False) for step in SIZING_STEPS[1:]),
        )
        evaluation = replace(d.evaluation, grade="A_Plus")
        book, intents, rejections = place(
            OrderBook(), [Accepted(SizedSetup(c, q, steps), evaluation)], ctx, self._planner
        )
        if rejections:
            return _Shadow(instrument, not_placed=rejections[0].reason)
        assert len(intents) == 1  # one accepted setup, not rejected: one bracket
        self._seq += 1
        plan = _renamed(book.plans[0], f"{_ID_PREFIX}{self._seq:06d}")
        sim = SimBook().submit_bracket(plan.entry, plan.stop, plan.targets, shadow=True)
        shadow = _Shadow(instrument, self._seq, OrderBook((plan,), book.placed, book.next_seq), sim)
        self._live[self._seq] = shadow
        return shadow

    def _first_tap(
        self, track: _Track, starts: Mapping[tuple[NodeId, int], Instant]
    ) -> Instant | None:
        """The start of the key's first Tap seen so far, gap-through entry fills included."""
        found: list[Instant] = []
        tap = starts.get((track.node, track.key.tap_seq))
        if tap is not None:
            found.append(tap)
        fill = self._entry_fill.get(track.key)
        if fill is not None:
            found.append(fill)
        cands = track.candidates
        for i, c in enumerate(cands):
            fill_ns = None if c.shadow is None or not c.rejected else c.shadow.fill_ns
            if fill_ns is not None and (i + 1 == len(cands) or fill_ns < cands[i + 1].t):
                found.append(fill_ns)
        return min(found) if found else None

    def _drop(self, candidates: Sequence[_Candidate]) -> None:
        for c in candidates:
            if c.shadow is not None:
                self._live.pop(c.shadow.seq, None)
                c.shadow = None

    def _resolve(self, track: _Track, first_tap: Instant) -> None:
        cands = track.candidates
        governing = cands[0]
        for c in cands:
            if c.t <= first_tap:
                governing = c
        self._drop([c for c in cands if c is not governing])
        if not governing.rejected:
            self._drop([governing])
        track.candidates = [governing]
        track.governing = governing
        track.tap_at = first_tap

    def _prune(self, track: _Track, t: Instant) -> None:
        """Drop candidates a later emission at least one bar old supersedes."""
        cands = track.candidates
        horizon = t - _BAR_NS
        keep = 0
        for i in range(len(cands) - 1):
            if cands[i + 1].t <= horizon:
                keep = i + 1
        if keep:
            self._drop(cands[:keep])
            track.candidates = cands[keep:]

    def _record(self, track: _Track) -> SetupRecord:
        key = track.key
        governing = track.governing
        if governing is None:
            return SetupRecord(key, "untapped", (), (), track.first_t, None, None, None)
        common = (track.first_t, governing.t, track.tap_at)
        if governing.rejected:
            outcome: ShadowOutcome | None = None
            shadow = governing.shadow
            if shadow is not None:
                if shadow.live or (shadow.sim is not None and shadow.sim.trades):
                    raise ValueError(f"the Shadow_Trade of {key} is still open at session end")
                outcome = ShadowOutcome(governing.t, shadow.trade, shadow.not_placed)
            failing = governing.decision.evaluation.failing
            return SetupRecord(key, "rejected", failing, (), *common, outcome)
        if key in self._entry_fill:
            return SetupRecord(key, "filled", (), (), *common, None)
        cause = self._causes.get(key, (END_OF_RTH,))
        return SetupRecord(key, "cancelled", (), cause, *common, None)
