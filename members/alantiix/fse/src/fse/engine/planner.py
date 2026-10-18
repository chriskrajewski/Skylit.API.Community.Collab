"""The Order_Planner: entry brackets and the order lifecycle (design §12, Req 12, 25.9).

Three pure functions keep the planner's :class:`OrderBook` and return order
intents for the Fill_Simulator or the order router. None reads a clock or
changes its inputs; each returns a new book.

- :func:`place` (setup phase): one entry bracket per accepted A_Plus
  Candidate_Setup.
- :func:`manage` (management phase, every Decision_Time): Cancel_Triggers and
  max age on resting entries, invalidation triggers on open positions, and
  Flatten_Time.
- :func:`on_bar` (bar phase, every closed base bar): the bar's fills, then the
  stop updates (breakeven, TP1 to Breakeven_Price, trailing).

**Lifecycle.** A placed Setup_Key is a :class:`Plan`, ``resting`` until its
first entry fill and ``open`` after it. It leaves the book when it is
cancelled or when its last open contract exits. A Setup_Key is never placed
twice: :attr:`OrderBook.placed` keeps every key of the current session, and a
new Tap gives a new key.

**Placement** (:func:`place`). The bracket is a limit entry at the setup's
entry, a protective stop at its stop for every contract, and the targets that
:func:`fse.engine.targets.plan_targets` gives from the source Snapshot at the
setup's Decision_Time (Req 12.1, 12.3): one ``tp1`` order for every contract,
or for TP1_Partial_BE ``tp1`` for ``max(1, floor(f x qty))`` contracts and
``tp2`` for the rest (none for a 1-contract position, Req 12.7); Trailing has
no target order. Every order is placed at the Decision_Time ``t``, so it fills
only on bars that open at or after ``t``. A setup is declined, with a
:class:`PlacementRejection`, by the first of:

1. ``not_a_plus``: its Grade is not A_Plus. Alert_2R and Pass setups never get
   an order (Req 25.9).
2. ``already_placed``: its Setup_Key was placed before.
3. ``flatten_time``: ``t`` is at or after Flatten_Time (Req 12.17).
4. ``kill_switch_lockout``: a Lockout is active (Req 16.8); ``detail`` names
   the rules.
5. ``exit_mode_disabled``: the Exit_Mode selected for its Regime has its
   enabled flag off. The planner places nothing rather than trade a rule the
   Operator switched off or substitute another mode.
6. ``no_target_node`` or ``tp2_not_beyond_tp1`` (Req 12.12).
7. ``max_open``: resting entries plus open positions already equal
   ``orders.max_open``; a position with a TP2 remainder is one (Req 12.18).

Placements count toward ``max_open`` immediately, so a later setup in the same
call sees them. The Position_Sizer's Micro_Equivalent cap is not re-checked
here: size each setup against :meth:`OrderBook.micro_equivalents` after the
previous placement.

**Stops only tighten** (Req 12.14). Every stop change goes through
:func:`tighten_only`: a long's stop never decreases and a short's never
increases. :func:`on_bar` moves stops at a bar's close, effective for bars that
open at or after that close, so each move takes effect from the next bar
(Req 12.8, 12.10-12.13):

- breakeven rule (``exits.breakeven``): once the most favorable price since
  the entry fill is at least ``trigger_r`` x the initial entry-to-stop distance
  from the entry fill, to the Breakeven_Price;
- TP1_Partial_BE: once TP1 has filled and contracts remain, to the
  Breakeven_Price;
- Trailing, node-to-node: when the most favorable price reaches a Node level
  beyond the entry fill, to the next Node level back toward entry, or to the
  source Node's level when the reached Node is the first beyond entry. The
  Nodes are those of the source Snapshot at the setup's Decision_Time, the
  same Nodes the targets come from (Req 12.3);
- Trailing, fixed ticks: to ``ticks`` behind the most favorable price.

The Breakeven_Price is the entry fill price plus ``exits.breakeven.offset_ticks``
in the trade's favor (Glossary). The most favorable price since the entry fill
counts, on the entry fill bar, only the fill price and the bar's close (the
bar's extreme may have traded before the fill), and from the next bar on each
bar's high (long) or low (short).

**Flatten_Time** (Req 12.15-12.17). At the first Decision_Time at or after
:attr:`PlannerContext.flatten_at` (see :func:`flatten_time`), :func:`manage`
cancels every resting entry and closes every open position at market;
:func:`place` declines every later setup of the session. A plan left from an
earlier session is treated the same way.

**Cancel_Triggers** (Req 12.19-12.20) compare the Map_State at the current
Decision_Time with the values at the setup's Decision_Time:

- ``king_flip``: the source Snapshot's King is a different strike;
- ``source_node_gone``: the source strike is no longer a Node;
- ``sign_flip``: the source strike's value has the opposite sign;
- ``stdev_leg_dropped``: the BOS_Leg the setup used is no longer active. A
  setup used a leg only when the stdev_fib_zone Gate is enabled: it is the leg
  whose zone was nearest entry, within the band half-width (the Gate's pass
  condition);
- ``opposition_in_target``: the opposition_inside_target Gate condition holds
  now: another Node with at least ``fraction`` of the source Node's absolute
  value at the setup's Decision_Time, with a converted level beyond entry by
  at most ``window_r`` x the setup's entry-to-stop distance.

A trigger needs data to fire: no source Snapshot, no conversion price or no
chart features for the instrument leaves it unfired. A resting entry with an
enabled trigger, its max age reached (Req 12.23) or an active Lockout is
cancelled with every cause recorded; otherwise it keeps its original prices.

**Invalidation** (Req 12.21-12.22). For an open position, each trigger with an
``orders.invalidation`` action that fires is recorded; the strongest action
applies: exit at market, then breakeven, then hold. A breakeven action whose
latest closed bar's close is at or past the Breakeven_Price against the trade
becomes an exit at market.

Thresholds read config numbers as the decimals they were written as
(``Fraction(repr(x))``, as in :mod:`fse.engine.targets`), so every boundary is
exact on integer ticks.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from fractions import Fraction
from types import MappingProxyType
from typing import Final, Literal

from fse.config.schema.exits import ExitModeName, ExitsConfig
from fse.config.schema.gates import GatesConfig
from fse.config.schema.orders import (
    CANCEL_TRIGGERS,
    INVALIDATION_ACTIONS,
    CancelTrigger,
    InvalidationAction,
    OrdersConfig,
)
from fse.engine.chart import BosLeg, ChartFeatures
from fse.engine.gates.registry import GateEvaluation
from fse.engine.levels import TICKS_PER_POINT, ConvertedMap
from fse.engine.nodes import NodeLabels
from fse.engine.risk import LockoutRule, RiskState, active_lockouts
from fse.engine.sizing import SizedSetup, micro_equivalents
from fse.engine.targets import (
    NodeLevel,
    TargetContext,
    TargetRejection,
    Targets,
    exit_mode_for,
    plan_targets,
)
from fse.engine.types import (
    Bar,
    CandidateSetup,
    Direction,
    Fill,
    MissingPrice,
    NoTarget,
    Order,
    SetupKey,
    Side,
    Snapshot,
    SourceNodeRef,
    Ticks,
    direction_sign,
)
from fse.pit.protocols import SymMetric
from fse.timekit import NS_PER_MINUTE, Instant, SessionCalendar, ny_instant

__all__ = [
    "PLACEMENT_REJECTION_REASONS",
    "Accepted",
    "CancelCause",
    "CancelOrder",
    "CancelReference",
    "EntryCancelled",
    "Flattened",
    "Invalidated",
    "ModifyOrder",
    "OrderBook",
    "OrderFill",
    "OrderIntent",
    "PlaceBracket",
    "PlacementRejection",
    "PlacementRejectionReason",
    "Plan",
    "PlanStatus",
    "PlannerConfig",
    "PlannerContext",
    "PlannerEvent",
    "SourceView",
    "StopMoved",
    "StopRuleName",
    "SubmitExit",
    "breakeven_price",
    "chart_legs",
    "drop_rejected",
    "flatten_time",
    "manage",
    "on_bar",
    "place",
    "tighten_only",
]

type PlanStatus = Literal["resting", "open"]
type CancelCause = CancelTrigger | LockoutRule | Literal["max_age", "flatten_time"]
"""Why a resting entry was cancelled: a Cancel_Trigger, a Lockout rule, max age or Flatten_Time."""
type PlacementRejectionReason = Literal[
    "not_a_plus",
    "already_placed",
    "flatten_time",
    "kill_switch_lockout",
    "exit_mode_disabled",
    "no_target_node",
    "tp2_not_beyond_tp1",
    "max_open",
]
type StopRuleName = Literal[
    "breakeven", "tp1_breakeven", "trail_node", "trail_ticks", "invalidation_breakeven"
]

PLACEMENT_REJECTION_REASONS: Final[tuple[PlacementRejectionReason, ...]] = (
    "not_a_plus",
    "already_placed",
    "flatten_time",
    "kill_switch_lockout",
    "exit_mode_disabled",
    "no_target_node",
    "tp2_not_beyond_tp1",
    "max_open",
)
"""The reasons :func:`place` declines a setup, in the order it checks them."""

_ID_PREFIX: Final = "fse-"
_PRECEDENCE: Final[Mapping[InvalidationAction, int]] = MappingProxyType(
    {action: len(INVALIDATION_ACTIONS) - i for i, action in enumerate(INVALIDATION_ACTIONS)}
)


def _exact(x: float) -> Fraction:
    """The decimal value config number ``x`` was written as: ``0.85`` is 17/20."""
    return Fraction(repr(x))


def _require_int(name: str, value: object, minimum: int | None = None) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer, got {value!r}")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}, got {value}")


def _sides(direction: Direction) -> tuple[Side, Side]:
    """``(entry side, exit side)``."""
    return ("buy", "sell") if direction == "long" else ("sell", "buy")


def _first_values(snapshot: Snapshot) -> dict[float, float]:
    """Each strike's signed value, first listing kept (as the Gates read it)."""
    out: dict[float, float] = {}
    for strike, value in zip(snapshot.strikes, snapshot.values, strict=True):
        out.setdefault(strike, value)
    return out


# ---------------------------------------------------------------- rules


def tighten_only(old: Ticks, new: Ticks, direction: Direction) -> Ticks:
    """The stop after a proposed move: ``max`` for a long, ``min`` for a short (Req 12.14)."""
    return max(old, new) if direction_sign(direction) > 0 else min(old, new)


def breakeven_price(entry_fill: Ticks, direction: Direction, offset_ticks: int) -> Ticks:
    """The entry fill price moved ``offset_ticks`` in the trade's favor (Glossary)."""
    _require_int("offset_ticks", offset_ticks, minimum=0)
    return entry_fill + direction_sign(direction) * offset_ticks


def flatten_time(cfg: OrdersConfig, cal: SessionCalendar, session: date) -> Instant:
    """The session's Flatten_Time: ``flatten_time``, or the lead before an early close.

    Raises like :meth:`SessionCalendar.is_early_close` for a non-session date.
    """
    if cal.is_early_close(session):
        return cal.rth_close(session) - cfg.early_close_flatten_lead_min * NS_PER_MINUTE
    return ny_instant(session, cfg.flatten_time)


def chart_legs(chart: ChartFeatures) -> dict[str, tuple[BosLeg, ...]]:
    """The active BOS_Legs per instrument, for :attr:`PlannerContext.legs`."""
    return {f.instrument: f.bos_legs for f in chart.instruments}


# ---------------------------------------------------------------- inputs


def _default_orders() -> OrdersConfig:
    return OrdersConfig()


def _default_exits() -> ExitsConfig:
    return ExitsConfig()


def _default_gates() -> GatesConfig:
    return GatesConfig()


@dataclass(frozen=True, slots=True)
class PlannerConfig:
    """The config sections the Order_Planner reads.

    ``exits`` must be the section the detectors and the min_reward_risk Gate
    used, so all three plan the same targets. ``gates`` gives the
    opposition_inside_target and stdev_fib_zone parameters of two triggers.
    """

    orders: OrdersConfig = field(default_factory=_default_orders)
    exits: ExitsConfig = field(default_factory=_default_exits)
    gates: GatesConfig = field(default_factory=_default_gates)


@dataclass(frozen=True, slots=True)
class SourceView:
    """One Map_State Snapshot at a Decision_Time with its Node labels and converted map.

    ``labels`` must be the Node_Classifier labels of ``snapshot``; ``converted``
    is its converted map, or ``MissingPrice``.
    """

    snapshot: Snapshot
    labels: NodeLabels
    converted: ConvertedMap | MissingPrice

    def __post_init__(self) -> None:
        s, cm = self.snapshot, self.converted
        if isinstance(cm, ConvertedMap) and (cm.symbol, cm.metric, cm.as_of_ns) != (
            s.symbol,
            s.metric,
            s.as_of_ns,
        ):
            raise ValueError(
                f"SourceView: the converted map of {cm.symbol} {cm.metric} at {cm.as_of_ns} "
                f"is not of the {s.symbol} {s.metric} Snapshot at {s.as_of_ns}"
            )


@dataclass(frozen=True, slots=True)
class PlannerContext:
    """What the Order_Planner reads at Decision_Time ``t`` of ``session``.

    - ``flatten_at``: the session's Flatten_Time (:func:`flatten_time`).
    - ``sources``: the Map_State Snapshots by (symbol, metric); an absent key
      has no Snapshot at ``t``.
    - ``legs``: the active BOS_Legs per instrument at ``t``
      (:func:`chart_legs`); an absent instrument has no chart features.
    - ``last_close``: the close in ticks of the latest bar that closed at or
      before ``t``, per instrument.
    - ``risk``: the Risk_Manager state (Lockouts).
    """

    t: Instant
    session: date
    flatten_at: Instant
    sources: Mapping[SymMetric, SourceView] = field(default_factory=dict)
    legs: Mapping[str, tuple[BosLeg, ...]] = field(default_factory=dict)
    last_close: Mapping[str, Ticks] = field(default_factory=dict)
    risk: RiskState = field(default_factory=RiskState)

    def __post_init__(self) -> None:
        _require_int("PlannerContext.t", self.t)
        _require_int("PlannerContext.flatten_at", self.flatten_at)
        for (symbol, metric), view in self.sources.items():
            if (view.snapshot.symbol, view.snapshot.metric) != (symbol, metric):
                raise ValueError(f"PlannerContext.sources[{symbol!r}, {metric!r}] is another map")
            if view.snapshot.as_of_ns > self.t:
                raise ValueError(f"the {symbol} {metric} Snapshot is after t {self.t}")
        for instrument, close in self.last_close.items():
            _require_int(f"PlannerContext.last_close[{instrument!r}]", close)
        object.__setattr__(self, "sources", MappingProxyType(dict(self.sources)))
        object.__setattr__(
            self, "legs", MappingProxyType({k: tuple(v) for k, v in self.legs.items()})
        )
        object.__setattr__(self, "last_close", MappingProxyType(dict(self.last_close)))


@dataclass(frozen=True, slots=True)
class Accepted:
    """A sized Candidate_Setup with its Gate evaluation at the same Decision_Time."""

    sized: SizedSetup
    evaluation: GateEvaluation

    def __post_init__(self) -> None:
        c = self.sized.setup
        if (self.evaluation.setup_key, self.evaluation.t) != (c.key, c.t):
            raise ValueError(f"the Gate evaluation is not of Candidate_Setup {c.key} at {c.t}")
        if self.evaluation.grade == "A_Plus" and not c.priced:
            raise ValueError(f"unpriced Candidate_Setup {c.key} cannot be A_Plus")

    @property
    def setup(self) -> CandidateSetup:
        return self.sized.setup


@dataclass(frozen=True, slots=True)
class OrderFill:
    """One fill with the order version that filled (``FillEvent.order`` in the simulator)."""

    order: Order
    fill: Fill

    def __post_init__(self) -> None:
        if self.order.client_id != self.fill.client_id:
            raise ValueError(
                f"fill of {self.fill.client_id!r} paired with order {self.order.client_id!r}"
            )


# ---------------------------------------------------------------- outputs


@dataclass(frozen=True, slots=True)
class PlaceBracket:
    """Place an entry with its protective stop and targets (``SimBook.submit_bracket``)."""

    entry: Order
    stop: Order
    targets: tuple[Order, ...]


@dataclass(frozen=True, slots=True)
class ModifyOrder:
    """Change an order's price for bars opening at or after ``at`` (``SimBook.modify``)."""

    client_id: str
    price: Ticks
    at: Instant


@dataclass(frozen=True, slots=True)
class CancelOrder:
    """Cancel an order for bars opening at or after ``at`` (``SimBook.cancel``)."""

    client_id: str
    at: Instant


@dataclass(frozen=True, slots=True)
class SubmitExit:
    """A market exit of an open position (``SimBook.submit_exit``)."""

    order: Order


type OrderIntent = PlaceBracket | ModifyOrder | CancelOrder | SubmitExit


@dataclass(frozen=True, slots=True)
class PlacementRejection:
    """An accepted setup :func:`place` declined; ``detail`` names Lockout rules."""

    key: SetupKey
    t: Instant
    reason: PlacementRejectionReason
    detail: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EntryCancelled:
    """A resting entry cancelled at ``t`` with every cause (Req 12.19, 12.23, 16.8, 12.15)."""

    key: SetupKey
    t: Instant
    causes: tuple[CancelCause, ...]


@dataclass(frozen=True, slots=True)
class Invalidated:
    """Invalidation triggers on an open position (Req 12.21-12.22).

    ``action`` is the strongest configured action of ``triggers``; ``applied``
    is what was done: the same, or ``exit_market`` for a breakeven action with
    the close at or past the Breakeven_Price.
    """

    key: SetupKey
    t: Instant
    triggers: tuple[CancelTrigger, ...]
    action: InvalidationAction
    applied: InvalidationAction


@dataclass(frozen=True, slots=True)
class Flattened:
    """An open position closed at market at Flatten_Time (Req 12.16)."""

    key: SetupKey
    t: Instant
    qty: int


@dataclass(frozen=True, slots=True)
class StopMoved:
    """A stop move effective for bars opening at or after ``at``; ``rules`` gave ``new``."""

    key: SetupKey
    at: Instant
    old: Ticks
    new: Ticks
    rules: tuple[StopRuleName, ...]


type PlannerEvent = EntryCancelled | Invalidated | Flattened | StopMoved


# ---------------------------------------------------------------- the book


@dataclass(frozen=True, slots=True)
class CancelReference:
    """Values at the setup's Decision_Time that the Cancel_Triggers compare against.

    ``king`` is the source Snapshot's King strike; ``leg`` the BOS_Leg the
    setup used, or ``None``.
    """

    king: float | None
    leg: BosLeg | None


@dataclass(frozen=True, slots=True)
class Plan:
    """One placed Setup_Key while it rests or is open.

    - ``source``: the source Node at the setup's Decision_Time (priced).
    - ``planned_stop``: the setup's initial stop; ``stop`` is the latest
      version of the stop order.
    - ``nodes``: the source Snapshot's Nodes at the setup's Decision_Time.
    - ``entry_price``, ``entry_bar_open``: the first entry fill.
    - ``filled_qty``, ``open_qty``: contracts entered, and still open.
    - ``favorable``: the most favorable price since the entry fill.
    - ``exit_pending``: a market exit was sent.
    """

    key: SetupKey
    sid: str
    source: SourceNodeRef
    mode: ExitModeName
    entry: Order
    stop: Order
    planned_stop: Ticks
    targets: tuple[Order, ...]
    nodes: tuple[NodeLevel, ...]
    reference: CancelReference
    status: PlanStatus = "resting"
    entry_price: Ticks | None = None
    entry_bar_open: Instant | None = None
    filled_qty: int = 0
    open_qty: int = 0
    favorable: Ticks | None = None
    tp1_filled: bool = False
    exit_pending: bool = False

    @property
    def direction(self) -> Direction:
        return self.key.direction

    @property
    def instrument(self) -> str:
        return self.key.instrument

    @property
    def sign(self) -> int:
        return direction_sign(self.key.direction)

    @property
    def stop_price(self) -> Ticks:
        price = self.stop.price
        assert price is not None  # a stop order always has a price
        return price

    @property
    def entry_limit(self) -> Ticks:
        price = self.entry.price
        assert price is not None  # a limit entry always has a price
        return price


@dataclass(frozen=True, slots=True)
class OrderBook:
    """The planner's resting entries and open positions; never changed in place.

    - ``plans``: resting and open plans, in placement order;
    - ``placed``: every Setup_Key placed this session (never placed again);
    - ``next_seq``: the next client-id sequence number.
    """

    plans: tuple[Plan, ...] = ()
    placed: frozenset[SetupKey] = frozenset()
    next_seq: int = 1

    def __post_init__(self) -> None:
        _require_int("OrderBook.next_seq", self.next_seq, minimum=1)
        keys = [p.key for p in self.plans]
        if len(set(keys)) != len(keys) or not set(keys) <= self.placed:
            raise ValueError("OrderBook.plans must have distinct, placed Setup_Keys")

    def plan(self, key: SetupKey) -> Plan | None:
        """The plan of ``key`` while it rests or is open."""
        return next((p for p in self.plans if p.key == key), None)

    @property
    def resting(self) -> tuple[Plan, ...]:
        return tuple(p for p in self.plans if p.status == "resting")

    @property
    def open_positions(self) -> tuple[Plan, ...]:
        return tuple(p for p in self.plans if p.status == "open")

    def exposure(self) -> int:
        """Resting entries plus open positions: what ``max_open`` counts (Req 12.18)."""
        return len(self.plans)

    def micro_equivalents(self) -> int:
        """Micro_Equivalents of open contracts plus unfilled entry contracts (Req 14.4)."""
        return sum(
            micro_equivalents(p.instrument, p.open_qty + p.entry.qty - p.filled_qty)
            for p in self.plans
        )


def drop_rejected(book: OrderBook, key: SetupKey) -> OrderBook:
    """Remove a resting plan the order router or account rejected; the key stays placed."""
    plan = book.plan(key)
    if plan is None or plan.status != "resting":
        raise ValueError(f"Setup_Key {key} has no resting entry to drop")
    return replace(book, plans=tuple(p for p in book.plans if p.key != key))


# ---------------------------------------------------------------- placement


def _source_view(c: CandidateSetup, ctx: PlannerContext) -> tuple[SourceView, ConvertedMap]:
    """The setup's source Snapshot at its Decision_Time; ``ValueError`` if ``ctx`` is another."""
    key = (c.source.symbol, c.source.metric)
    view = ctx.sources.get(key)
    if view is None or not isinstance(view.converted, ConvertedMap):
        raise ValueError(f"the context has no priced {key[0]} {key[1]} map for {c.key}")
    as_of = {(a.symbol, a.metric): a.as_of_ns for a in c.inputs.map_as_of}
    if as_of.get(key, view.snapshot.as_of_ns) != view.snapshot.as_of_ns:
        raise ValueError(f"the context's {key[0]} {key[1]} Snapshot is not the one of {c.key}")
    if view.converted.level_of(c.source.strike) != c.source.level:
        raise ValueError(f"Candidate_Setup {c.key} has a level the context does not give")
    return view, view.converted


def _used_leg(c: CandidateSetup, ctx: PlannerContext, gates: GatesConfig) -> BosLeg | None:
    """The BOS_Leg whose zone was nearest entry within the band half-width (stdev_fib_zone)."""
    cfg = gates.stdev_fib_zone
    legs = ctx.legs.get(c.key.instrument)
    half_width = c.inputs.band_half_width_pts
    if not cfg.enabled or not legs or c.entry is None or isinstance(half_width, MissingPrice):
        return None
    entry = c.entry / TICKS_PER_POINT
    best: BosLeg | None = None
    best_distance = math.inf
    for leg in legs:
        span = leg.origin - leg.terminal
        for zone in cfg.zones:
            a = leg.terminal + zone.near * span
            b = leg.terminal + zone.far * span
            lo, hi = min(a, b), max(a, b)
            distance = lo - entry if entry < lo else entry - hi if entry > hi else 0.0
            if distance < best_distance:
                best, best_distance = leg, distance
    return best if not best_distance > half_width else None


type _BracketRole = Literal["entry", "stop", "tp1", "tp2"]


def _order(
    key: SetupKey, sid: str, role: _BracketRole, side: Side, qty: int, price: Ticks, t: Instant
) -> Order:
    kind: Literal["limit", "stop"] = "stop" if role == "stop" else "limit"
    return Order(f"{sid}-{role}", key, key.instrument, side, kind, qty, price, t, role)


def _bracket(
    c: CandidateSetup, contracts: int, planned: Targets | NoTarget, sid: str, t: Instant
) -> tuple[Order, Order, tuple[Order, ...]]:
    assert c.entry is not None  # priced: an A_Plus setup
    assert c.stop is not None
    key = c.key
    entry_side, exit_side = _sides(key.direction)
    entry = _order(key, sid, "entry", entry_side, contracts, c.entry, t)
    stop = _order(key, sid, "stop", exit_side, contracts, c.stop, t)
    if isinstance(planned, NoTarget):
        return entry, stop, ()
    tp1_qty, tp2_qty = planned.quantities(contracts)
    targets = [_order(key, sid, "tp1", exit_side, tp1_qty, planned.tp1, t)]
    if planned.tp2 is not None and tp2_qty > 0:
        targets.append(_order(key, sid, "tp2", exit_side, tp2_qty, planned.tp2, t))
    return entry, stop, tuple(targets)


type _Decision = tuple[ExitModeName, Targets | NoTarget, TargetContext, SourceView]


def _decide(
    a: Accepted,
    ctx: PlannerContext,
    cfg: PlannerConfig,
    placed: set[SetupKey],
    exposure: int,
    lockouts: tuple[LockoutRule, ...],
) -> _Decision | PlacementRejection:
    """The checks of :func:`place`, in order: the plan, or the first reason to decline."""
    c = a.setup
    if (c.t, c.key.session) != (ctx.t, ctx.session):
        raise ValueError(f"Candidate_Setup {c.key} at {c.t} placed in a context at {ctx.t}")
    if a.evaluation.grade != "A_Plus":
        return PlacementRejection(c.key, ctx.t, "not_a_plus")
    if c.key in placed:
        return PlacementRejection(c.key, ctx.t, "already_placed")
    if ctx.t >= ctx.flatten_at:
        return PlacementRejection(c.key, ctx.t, "flatten_time")
    if lockouts:
        return PlacementRejection(c.key, ctx.t, "kill_switch_lockout", lockouts)
    mode = exit_mode_for(cfg.exits, c.inputs.regime)
    if mode.mode != c.exit_mode:
        raise ValueError(
            f"Candidate_Setup {c.key} has Exit_Mode {c.exit_mode!r} but the exits "
            f"setting for its Regime is {mode.mode!r}"
        )
    if not cfg.exits.modes.enabled(mode.mode):
        return PlacementRejection(c.key, ctx.t, "exit_mode_disabled")
    view, converted = _source_view(c, ctx)
    targets_ctx = TargetContext.from_snapshot(view.snapshot, view.labels, converted)
    planned = plan_targets(c, mode, targets_ctx)
    if isinstance(planned, TargetRejection):
        return PlacementRejection(c.key, ctx.t, planned.reason)
    if exposure >= cfg.orders.max_open:
        return PlacementRejection(c.key, ctx.t, "max_open")
    return mode.mode, planned, targets_ctx, view


def place(
    book: OrderBook, accepted: Iterable[Accepted], ctx: PlannerContext, cfg: PlannerConfig
) -> tuple[OrderBook, list[OrderIntent], list[PlacementRejection]]:
    """Entry brackets for the accepted setups at ``ctx.t``, in order (see the module notes).

    Every setup must be of ``ctx.t`` and ``ctx.session``, with its source
    Snapshot in ``ctx.sources``; ``ValueError`` otherwise, or when its
    Exit_Mode is not the one ``cfg.exits`` selects for its Regime.
    """
    plans = list(book.plans)
    placed = set(book.placed)
    seq = book.next_seq
    intents: list[OrderIntent] = []
    rejections: list[PlacementRejection] = []
    lockouts = tuple(dict.fromkeys(lo.rule for lo in active_lockouts(ctx.risk, ctx.session)))
    for a in accepted:
        decision = _decide(a, ctx, cfg, placed, len(plans), lockouts)
        if isinstance(decision, PlacementRejection):
            rejections.append(decision)
            continue
        mode, planned, targets_ctx, view = decision
        c = a.setup
        assert c.stop is not None  # priced: an A_Plus setup
        sid = f"{_ID_PREFIX}{seq:06d}"
        entry, stop, targets = _bracket(c, a.sized.contracts, planned, sid, ctx.t)
        plans.append(
            Plan(
                key=c.key,
                sid=sid,
                source=c.source,
                mode=mode,
                entry=entry,
                stop=stop,
                planned_stop=c.stop,
                targets=targets,
                nodes=targets_ctx.nodes,
                reference=CancelReference(view.labels.king, _used_leg(c, ctx, cfg.gates)),
            )
        )
        placed.add(c.key)
        seq += 1
        intents.append(PlaceBracket(entry, stop, targets))
    return OrderBook(tuple(plans), frozenset(placed), seq), intents, rejections


# ---------------------------------------------------------------- triggers


def _king_flip(plan: Plan, view: SourceView | None) -> bool:
    if view is None or view.labels.king is None or plan.reference.king is None:
        return False
    return view.labels.king != plan.reference.king


def _source_node_gone(plan: Plan, view: SourceView | None) -> bool:
    return view is not None and plan.source.strike not in view.labels.nodes


def _sign_flip(plan: Plan, view: SourceView | None) -> bool:
    if view is None:
        return False
    value = _first_values(view.snapshot).get(plan.source.strike)
    then = plan.source.value
    return value is not None and ((value > 0 > then) or (value < 0 < then))


def _stdev_leg_dropped(plan: Plan, ctx: PlannerContext) -> bool:
    leg = plan.reference.leg
    active = ctx.legs.get(plan.instrument)
    return leg is not None and active is not None and leg not in active


def _opposition_in_target(plan: Plan, view: SourceView | None, gates: GatesConfig) -> bool:
    if view is None or not isinstance(view.converted, ConvertedMap):
        return False
    cfg = gates.opposition_inside_target
    entry = plan.entry_limit
    window = _exact(cfg.window_r) * abs(entry - plan.planned_stop)
    min_abs = _exact(cfg.fraction) * _exact(abs(plan.source.value))
    values = _first_values(view.snapshot)
    for strike in view.labels.nodes:
        if strike == plan.source.strike or not _exact(abs(values[strike])) >= min_abs:
            continue
        beyond = plan.sign * (view.converted.level_of(strike) - entry)
        if 0 < beyond <= window:
            return True
    return False


def _fired(
    plan: Plan, ctx: PlannerContext, gates: GatesConfig, enabled: Callable[[CancelTrigger], bool]
) -> tuple[CancelTrigger, ...]:
    """The enabled Cancel_Triggers that fire for ``plan`` at ``ctx.t``, in trigger order."""
    view = ctx.sources.get((plan.source.symbol, plan.source.metric))
    checks: dict[CancelTrigger, Callable[[], bool]] = {
        "king_flip": lambda: _king_flip(plan, view),
        "source_node_gone": lambda: _source_node_gone(plan, view),
        "sign_flip": lambda: _sign_flip(plan, view),
        "stdev_leg_dropped": lambda: _stdev_leg_dropped(plan, ctx),
        "opposition_in_target": lambda: _opposition_in_target(plan, view, gates),
    }
    return tuple(t for t in CANCEL_TRIGGERS if enabled(t) and checks[t]())


# ---------------------------------------------------------------- management


def _exit_order(plan: Plan, t: Instant) -> Order:
    _, exit_side = _sides(plan.direction)
    return Order(
        f"{plan.sid}-exit", plan.key, plan.instrument, exit_side, "market", plan.open_qty, None, t,
        "exit",
    )  # fmt: skip


def _close_at_market(plan: Plan, t: Instant, intents: list[OrderIntent]) -> Plan:
    """Send a market exit for the open contracts and cancel any unfilled entry remainder."""
    intents.append(SubmitExit(_exit_order(plan, t)))
    if plan.filled_qty < plan.entry.qty:
        intents.append(CancelOrder(plan.entry.client_id, t))
    return replace(plan, exit_pending=True)


def _move_stop(
    plan: Plan,
    new: Ticks,
    at: Instant,
    rules: tuple[StopRuleName, ...],
    intents: list[OrderIntent],
    events: list[PlannerEvent],
) -> Plan:
    old = plan.stop_price
    if new == old:
        return plan
    if tighten_only(old, new, plan.direction) != new:
        raise ValueError(f"stop of {plan.key} would move against the trade: {old} to {new}")
    intents.append(ModifyOrder(plan.stop.client_id, new, at))
    events.append(StopMoved(plan.key, at, old, new, rules))
    return replace(plan, stop=replace(plan.stop, price=new))


def _invalidate(
    plan: Plan,
    ctx: PlannerContext,
    cfg: PlannerConfig,
    intents: list[OrderIntent],
    events: list[PlannerEvent],
) -> Plan:
    actions = cfg.orders.invalidation
    fired = _fired(plan, ctx, cfg.gates, lambda t: actions.action(t) is not None)
    if not fired:
        return plan
    chosen: InvalidationAction = max(
        (a for a in (actions.action(t) for t in fired) if a is not None),
        key=lambda a: _PRECEDENCE[a],
    )
    applied: InvalidationAction = chosen
    if chosen == "breakeven":
        assert plan.entry_price is not None  # open
        be = breakeven_price(plan.entry_price, plan.direction, cfg.exits.breakeven.offset_ticks)
        close = ctx.last_close.get(plan.instrument)
        if close is not None and plan.sign * (close - be) <= 0:
            applied = "exit_market"
        else:
            new = tighten_only(plan.stop_price, be, plan.direction)
            plan = _move_stop(plan, new, ctx.t, ("invalidation_breakeven",), intents, events)
    if applied == "exit_market":
        plan = _close_at_market(plan, ctx.t, intents)
    events.append(Invalidated(plan.key, ctx.t, fired, chosen, applied))
    return plan


def manage(
    book: OrderBook, ctx: PlannerContext, cfg: PlannerConfig
) -> tuple[OrderBook, list[OrderIntent], list[PlannerEvent]]:
    """Cancel_Triggers, max age, Lockouts, invalidation and Flatten_Time at ``ctx.t``.

    Run after the bar phase (:func:`on_bar`) has applied every fill on bars
    that closed at or before ``ctx.t``.
    """
    intents: list[OrderIntent] = []
    events: list[PlannerEvent] = []
    orders = cfg.orders
    lockouts = tuple(dict.fromkeys(lo.rule for lo in active_lockouts(ctx.risk, ctx.session)))
    max_age_ns = None if orders.max_age_min is None else orders.max_age_min * NS_PER_MINUTE
    kept: list[Plan] = []
    for plan in book.plans:
        flatten = ctx.t >= ctx.flatten_at or plan.key.session < ctx.session
        if plan.status == "resting":
            causes: list[CancelCause] = list(
                _fired(plan, ctx, cfg.gates, orders.cancel_triggers.enabled)
            )
            if max_age_ns is not None and ctx.t - plan.entry.placed_at >= max_age_ns:
                causes.append("max_age")
            causes.extend(lockouts)
            if flatten:
                causes.append("flatten_time")
            if causes:
                intents.append(CancelOrder(plan.entry.client_id, ctx.t))
                events.append(EntryCancelled(plan.key, ctx.t, tuple(causes)))
                continue
        elif not plan.exit_pending:
            if flatten:
                events.append(Flattened(plan.key, ctx.t, plan.open_qty))
                plan = _close_at_market(plan, ctx.t, intents)
            else:
                plan = _invalidate(plan, ctx, cfg, intents, events)
        kept.append(plan)
    placed = frozenset(k for k in book.placed if k.session >= ctx.session)
    placed |= {p.key for p in kept}
    return OrderBook(tuple(kept), placed, book.next_seq), intents, events


# ---------------------------------------------------------------- bar phase


def _apply_fill(plan: Plan, f: OrderFill) -> Plan:
    role, qty, price = f.order.role, f.fill.qty, f.fill.price
    if role == "entry":
        if plan.filled_qty + qty > plan.entry.qty:
            raise ValueError(f"entry fills of {plan.key} exceed its {plan.entry.qty} contracts")
        first = plan.entry_price is None
        return replace(
            plan,
            status="open",
            entry_price=price if first else plan.entry_price,
            entry_bar_open=f.fill.bar_open_ns if first else plan.entry_bar_open,
            favorable=price if first else plan.favorable,
            filled_qty=plan.filled_qty + qty,
            open_qty=plan.open_qty + qty,
        )
    if qty > plan.open_qty:
        raise ValueError(f"{role} fill of {qty} for {plan.key} exceeds {plan.open_qty} open")
    return replace(plan, open_qty=plan.open_qty - qty, tp1_filled=plan.tp1_filled or role == "tp1")


def _better(plan: Plan, a: Ticks, b: Ticks) -> Ticks:
    return max(a, b) if plan.sign > 0 else min(a, b)


def _trail_node(plan: Plan, favorable: Ticks, entry: Ticks) -> Ticks | None:
    """Node-to-node: the Node back toward entry from the farthest Node reached (Req 12.10)."""
    sign = plan.sign
    ladder = sorted(
        {
            n.level
            for n in plan.nodes
            if n.strike != plan.source.strike and sign * (n.level - entry) > 0
        },
        key=lambda level: sign * level,
    )
    reached = [i for i, level in enumerate(ladder) if sign * (favorable - level) >= 0]
    if not reached:
        return None
    k = reached[-1]
    return plan.source.level if k == 0 else ladder[k - 1]


def _bar_close_update(
    plan: Plan,
    bar: Bar,
    high: Ticks,
    low: Ticks,
    close: Ticks,
    cfg: PlannerConfig,
    intents: list[OrderIntent],
    events: list[PlannerEvent],
) -> Plan:
    entry = plan.entry_price
    assert entry is not None  # open
    assert plan.favorable is not None
    # On the fill bar, only the close is known to trade after the fill.
    on_fill_bar = plan.entry_bar_open == bar.open_ns
    extreme = close if on_fill_bar else high if plan.sign > 0 else low
    favorable = _better(plan, plan.favorable, extreme)
    plan = replace(plan, favorable=favorable)

    exits = cfg.exits
    be = breakeven_price(entry, plan.direction, exits.breakeven.offset_ticks)
    risk = abs(entry - plan.planned_stop)
    candidates: list[tuple[Ticks, StopRuleName]] = []
    if exits.breakeven.enabled and plan.sign * (favorable - entry) >= (
        _exact(exits.breakeven.trigger_r) * risk
    ):
        candidates.append((be, "breakeven"))
    if plan.mode == "tp1_partial_be" and plan.tp1_filled:
        candidates.append((be, "tp1_breakeven"))
    if plan.mode == "trailing":
        trailing = exits.modes.trailing
        if trailing.mode == "node_to_node":
            level = _trail_node(plan, favorable, entry)
            if level is not None:
                candidates.append((level, "trail_node"))
        else:
            candidates.append((favorable - plan.sign * trailing.ticks, "trail_ticks"))
    new = plan.stop_price
    for price, _ in candidates:
        new = tighten_only(new, price, plan.direction)
    rules = tuple(rule for price, rule in candidates if price == new)
    return _move_stop(plan, new, bar.close_ns, rules, intents, events)


def on_bar(
    book: OrderBook, bar: Bar, fills: Iterable[OrderFill], cfg: PlannerConfig
) -> tuple[OrderBook, list[OrderIntent], list[PlannerEvent]]:
    """Apply one closed bar's fills, then move stops at its close (see the module notes).

    ``fills`` are the fills on ``bar``, in the order they happened. A fill of
    a Setup_Key not in the book (a Shadow_Trade, for example) is skipped.
    Stop moves take effect for bars that open at or after ``bar.close_ns``.
    """
    o, high, low, close = bar.o_t, bar.h_t, bar.l_t, bar.c_t
    if o is None or high is None or low is None or close is None:
        raise ValueError(f"bar of {bar.instrument!r} at {bar.open_ns} has no tick prices")
    plans = {p.key: p for p in book.plans}
    intents: list[OrderIntent] = []
    events: list[PlannerEvent] = []
    for f in fills:
        key = f.order.setup_key
        plan = None if key is None else plans.get(key)
        if plan is None:
            continue
        if f.order.instrument != bar.instrument or f.fill.bar_open_ns != bar.open_ns:
            raise ValueError(f"fill {f.fill.client_id!r} is not on the {bar.instrument} bar")
        plans[plan.key] = _apply_fill(plan, f)
    kept: list[Plan] = []
    for plan in plans.values():
        if plan.status == "open" and plan.open_qty == 0:
            if plan.filled_qty < plan.entry.qty:
                intents.append(CancelOrder(plan.entry.client_id, bar.close_ns))
            continue  # closed: its last open contract exited
        if plan.status == "open" and not plan.exit_pending and plan.instrument == bar.instrument:
            plan = _bar_close_update(plan, bar, high, low, close, cfg, intents, events)
        kept.append(plan)
    return replace(book, plans=tuple(kept)), intents, events
