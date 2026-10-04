"""``Engine.step`` and the bar-phase hooks the runners call (design "Decision loop").

The Backtester and the Live_Runner call the same code (Req 23.5). An
:class:`Engine` holds the config sections (:class:`EngineParams`), the session
calendar and the precomputed trailing Regime medians; every method is a pure
function of its arguments and returns a new :class:`~fse.engine.state.EngineState`.

**Bar phase** (the runner, for each base bar that closed in ``(t_prev, t]``, in
close-time order, after the Fill_Simulator and the Account_Simulator ran on it):

1. :meth:`Engine.count_fill` for each fill on the bar, in fill order: the
   Risk_Manager's Kill_Switch counters (Req 16.1-16.5) and the session's
   filled-trade totals for big-win sizing (Req 14.5).
2. :meth:`Engine.update_stops`: the Order_Planner applies the bar's fills to its
   book, then moves stops at the bar's close (breakeven, TP1 to breakeven,
   trailing), effective from the next bar (Req 12.8, 12.10-12.14).
3. :meth:`Engine.check_loss_stop` at each 1-minute close: the internal daily
   loss stop (Req 16.6). When it fires, every open plan of the closed
   instruments gets a market exit and the Lockout starts. It runs after the
   book holds the bar's fills, so the exits are for the contracts still open;
   a stop move from step 2 at the same close is superseded by the exit.
4. :meth:`Engine.consume_bar` for each closed 1-minute bar: the Tap tracker,
   the lifecycle deliveries and the Chart_Feature_Builder, with the Node bands
   of the Map_State at the bar's close.

Each hook returns the order intents to send and the events to pass to the next
``step`` as ``events``, so the decision log holds them (Req 18.7).

**Decision phase** (:meth:`Engine.step` at ``t``), in this fixed order:

1. **Events**: an :class:`EntryRejected` (the router or account refused an
   entry placed earlier) removes that resting plan; its Setup_Key stays placed.
2. **Map phase**: Map_State, Node labels, converted levels, Tap counts,
   lifecycle and Sloppy_Seconds, Regime, Map_Grade, Dormant Nodes,
   Node_Velocity, chart features and Futures_Price.
3. **Management phase**: Cancel_Triggers, max age, Lockouts, invalidation and
   Flatten_Time (``planner.manage``), then the external blocks: each resting
   entry of a blocked instrument is cancelled (:class:`BlockCancelled`).
4. **Setup phase**: the Setup_Detector, all 27 Gates and the Grade per
   Candidate_Setup, then, for each A_Plus setup in detection order, the
   Position_Sizer against the book's Micro_Equivalents after the previous
   placement (with the trinity_agreement count, Req 14.4, 14.6) and the
   Order_Planner. An A_Plus setup of an externally blocked instrument that was
   not placed before is withheld (:class:`BlockedEntry`).
5. **Log payload**: one :class:`DecisionPayload` with every Req 18.7 field,
   and the Finding_Card triggers against the previous Decision_Time
   (:class:`CardTriggers`).

**External blocks** (design "Engine state, restart and parity"): live-only
guards (stale map, halt, broker and restore blocks) run outside the engine and
arrive as :class:`ExternalBlock` values, recorded as ``guard_event`` inputs so
replay applies them at the same instants (Req 23.9). A block withholds new
entries and cancels resting entries of the instruments it covers; stops and
exits of open positions keep working (Req 23.7).

**Inputs not in the MarketView.** The Gates get the dark-pool prints from the
start of the trading day of the session ``lookback_sessions - 1`` sessions
before the current one (so the trailing window holds ``lookback_sessions``
sessions, the current one included), and each configured instrument's
1-minute bars since the session's trading-day start. The Position_Sizer's
previous session is the latest calendar session before the current one; its
filled trades come from :meth:`Engine.count_fill`.

Identical inputs and config give equal states, intents and payloads, field by
field and in the same order (Req 10.15).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from types import MappingProxyType
from typing import Final, Protocol

from fse.config.schema.chart import ChartConfig
from fse.config.schema.data import DataConfig
from fse.config.schema.exits import ExitsConfig
from fse.config.schema.gates import GatesConfig
from fse.config.schema.kill_switches import KillSwitchesConfig
from fse.config.schema.levels import LevelsConfig
from fse.config.schema.nodes import NodesConfig
from fse.config.schema.orders import OrdersConfig
from fse.config.schema.patterns import PatternsConfig
from fse.config.schema.regime import RegimeConfig
from fse.config.schema.sizing import SizingConfig
from fse.engine.chart import ChartFeatures
from fse.engine.gates.catalog import GateContext, GateParams
from fse.engine.gates.registry import GateEvaluation, Grade, evaluate, trinity_count
from fse.engine.levels import ConvertedMap, LevelParams, convert_map
from fse.engine.lifecycle import LifecycleParams, LifecycleState, NodeStates, dormant_nodes
from fse.engine.nodes import NodeLabels, NodeParams, Velocities, classify, velocity
from fse.engine.planner import (
    Accepted,
    CancelOrder,
    OrderBook,
    OrderFill,
    OrderIntent,
    PlaceBracket,
    PlacementRejection,
    Plan,
    PlannerConfig,
    PlannerContext,
    PlannerEvent,
    SourceView,
    SubmitExit,
    chart_legs,
    drop_rejected,
    flatten_time,
    manage,
    place,
)
from fse.engine.planner import on_bar as planner_on_bar
from fse.engine.regime import (
    RegimeParams,
    RegimeResult,
    TrailingMedians,
    map_state_grade,
    regime,
)
from fse.engine.risk import (
    CloseIntent,
    Lockout,
    LossStopHit,
    RiskFill,
    RiskState,
    active_lockouts,
    on_fill,
    on_minute_close,
)
from fse.engine.setups.base import DetectContext, DetectorParams
from fse.engine.setups.registry import detect_setups
from fse.engine.sizing import PriorSession, SizingContext, SizingRejection, StepOutcome, size
from fse.engine.state import CardMemory, EngineState
from fse.engine.taps import BASE_INTERVAL_S, NodeId, TapView, node_bands, session_of
from fse.engine.types import (
    Bar,
    CandidateSetup,
    DarkPoolPrint,
    DetectionSkip,
    MapGrade,
    Metric,
    MissingInput,
    MissingPrice,
    Money,
    Order,
    SetupKey,
    Side,
    Snapshot,
    Ticks,
    Trade,
    Unavailable,
    VixState,
)
from fse.pit.protocols import MapState, MarketView, SymMetric, futures_price
from fse.timekit import TRADING_DAY_START, Instant, SessionCalendar, ny_instant

__all__ = [
    "BarPhaseResult",
    "BlockCancelled",
    "BlockedEntry",
    "CardTriggers",
    "DecisionPayload",
    "Engine",
    "EngineParams",
    "EngineSections",
    "EntryRejected",
    "ExternalBlock",
    "FuturesLog",
    "GradeChange",
    "KingFlip",
    "LossStopFired",
    "ManagementEvent",
    "MapEntryLog",
    "PlacementOutcome",
    "SetupDecision",
    "Sized",
    "StepEvent",
    "StepResult",
]

_ONE_DAY: Final = timedelta(days=1)
_UNGRADED: Final[Grade] = "Pass"
"""The Grade an absent Setup_Key counts as for the Finding_Card triggers (Req 25.6)."""


# ---------------------------------------------------------------- parameters


class EngineSections(Protocol):
    """The Strategy_Config sections the engine reads (the root ``StrategyConfig`` has them)."""

    @property
    def data(self) -> DataConfig: ...
    @property
    def nodes(self) -> NodesConfig: ...
    @property
    def levels(self) -> LevelsConfig: ...
    @property
    def chart(self) -> ChartConfig: ...
    @property
    def regime(self) -> RegimeConfig: ...
    @property
    def patterns(self) -> PatternsConfig: ...
    @property
    def gates(self) -> GatesConfig: ...
    @property
    def exits(self) -> ExitsConfig: ...
    @property
    def sizing(self) -> SizingConfig: ...
    @property
    def kill_switches(self) -> KillSwitchesConfig: ...
    @property
    def orders(self) -> OrdersConfig: ...


@dataclass(frozen=True, slots=True)
class EngineParams:
    """The config sections the engine reads, and the per-module parameters built from them.

    ``regime`` has no default because ``regime.min_abs_value`` is required.
    """

    regime: RegimeConfig
    data: DataConfig = field(default_factory=DataConfig)
    nodes: NodesConfig = field(default_factory=NodesConfig)
    levels: LevelsConfig = field(default_factory=LevelsConfig)
    chart: ChartConfig = field(default_factory=ChartConfig)
    patterns: PatternsConfig = field(default_factory=PatternsConfig)
    gates: GatesConfig = field(default_factory=GatesConfig)
    exits: ExitsConfig = field(default_factory=ExitsConfig)
    sizing: SizingConfig = field(default_factory=SizingConfig)
    kill_switches: KillSwitchesConfig = field(default_factory=KillSwitchesConfig)
    orders: OrdersConfig = field(default_factory=OrdersConfig)
    node_params: NodeParams = field(init=False, repr=False, compare=False)
    lifecycle_params: LifecycleParams = field(init=False, repr=False, compare=False)
    level_params: LevelParams = field(init=False, repr=False, compare=False)
    regime_params: RegimeParams = field(init=False, repr=False, compare=False)
    detector_params: DetectorParams = field(init=False, repr=False, compare=False)
    gate_params: GateParams = field(init=False, repr=False, compare=False)
    planner: PlannerConfig = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        node_params = NodeParams.from_config(self.nodes)
        derived: dict[str, object] = {
            "node_params": node_params,
            "lifecycle_params": LifecycleParams.from_config(self.nodes),
            "level_params": LevelParams.from_config(self.levels, self.data),
            "regime_params": RegimeParams(self.regime, self.data.regime_symbol, node_params),
            "detector_params": DetectorParams(self.patterns, self.data, self.levels, self.exits),
            "gate_params": GateParams(self.gates, self.exits, self.data),
            "planner": PlannerConfig(self.orders, self.exits, self.gates),
        }
        for name, value in derived.items():
            object.__setattr__(self, name, value)

    @classmethod
    def from_sections(cls, cfg: EngineSections) -> EngineParams:
        """Parameters from a config holding every section (the root ``StrategyConfig``)."""
        return cls(
            regime=cfg.regime,
            data=cfg.data,
            nodes=cfg.nodes,
            levels=cfg.levels,
            chart=cfg.chart,
            patterns=cfg.patterns,
            gates=cfg.gates,
            exits=cfg.exits,
            sizing=cfg.sizing,
            kill_switches=cfg.kill_switches,
            orders=cfg.orders,
        )

    @property
    def instruments(self) -> tuple[str, ...]:
        """The configured instruments: the ES-level one, then the NQ-level one."""
        chosen = self.data.instruments
        return tuple(dict.fromkeys((chosen.es_levels, chosen.nq_levels)))


# ---------------------------------------------------------------- inputs


@dataclass(frozen=True, slots=True)
class ExternalBlock:
    """A live-only guard blocking entries: ``kind`` names it (stale map, halt, broker, ...).

    ``instrument`` ``None`` blocks every instrument.
    """

    kind: str
    instrument: str | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise ValueError("ExternalBlock.kind must not be blank")
        if self.instrument is not None and not self.instrument.strip():
            raise ValueError("ExternalBlock.instrument must not be blank")

    def covers(self, instrument: str) -> bool:
        return self.instrument is None or self.instrument == instrument


@dataclass(frozen=True, slots=True)
class EntryRejected:
    """The order router or the account refused the entry of ``key`` at ``at``."""

    key: SetupKey
    at: Instant
    reason: str


@dataclass(frozen=True, slots=True)
class LossStopFired:
    """The internal daily loss stop fired at a 1-minute close (Req 16.6, 24.24).

    ``closes`` are the Risk_Manager's per-instrument market closes; the book's
    open plans in those instruments were sent market exits.
    """

    hit: LossStopHit
    closes: tuple[CloseIntent, ...]


type StepEvent = OrderFill | PlannerEvent | LossStopFired | EntryRejected
"""A bar-phase event since the previous Decision_Time, passed to ``step`` for the log."""


# ---------------------------------------------------------------- outputs


@dataclass(frozen=True, slots=True)
class BarPhaseResult:
    """A bar-phase hook's new state, the intents to send, and the events for ``step``."""

    state: EngineState
    intents: tuple[OrderIntent, ...] = ()
    events: tuple[StepEvent, ...] = ()


@dataclass(frozen=True, slots=True)
class BlockedEntry:
    """An A_Plus setup's entry withheld at ``t`` by the external blocks named."""

    key: SetupKey
    t: Instant
    blocks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BlockCancelled:
    """A resting entry cancelled at ``t`` by the external blocks named."""

    key: SetupKey
    t: Instant
    blocks: tuple[str, ...]


type ManagementEvent = PlannerEvent | BlockCancelled
type PlacementOutcome = PlaceBracket | PlacementRejection | BlockedEntry


@dataclass(frozen=True, slots=True)
class Sized:
    """The Position_Sizer's contracts and the five steps (``SizedSetup`` without the setup)."""

    contracts: int
    steps: tuple[StepOutcome, ...]


@dataclass(frozen=True, slots=True)
class SetupDecision:
    """One Candidate_Setup at ``t``: its Gates and Grade, then sizing and placement.

    ``sizing`` and ``placement`` are ``None`` for a setup that is not A_Plus
    (no order can be placed for it, Req 25.9); ``placement`` is ``None`` when
    sizing refused it.
    """

    setup: CandidateSetup
    evaluation: GateEvaluation
    sizing: Sized | SizingRejection | None
    placement: PlacementOutcome | None


@dataclass(frozen=True, slots=True)
class MapEntryLog:
    """One configured (symbol, metric) of the Map_State; ``missing`` with no Snapshot."""

    symbol: str
    metric: Metric
    missing: bool
    as_of_ns: Instant | None
    king: float | None
    floor: float | None
    ceiling: float | None


@dataclass(frozen=True, slots=True)
class FuturesLog:
    """One configured instrument's Futures_Price at ``t``, in ticks."""

    instrument: str
    price: Ticks | Unavailable


@dataclass(frozen=True, slots=True)
class GradeChange:
    """A Setup_Key whose Grade differs from the previous Decision_Time (absent = Pass)."""

    key: SetupKey
    before: Grade
    after: Grade


@dataclass(frozen=True, slots=True)
class KingFlip:
    """A (symbol, metric) whose King strike changed since the previous Decision_Time."""

    symbol: str
    metric: Metric
    before: float
    after: float


@dataclass(frozen=True, slots=True)
class CardTriggers:
    """The Finding_Card triggers at ``t`` (Req 25.6, 25.8).

    ``order_event`` is true when an order was placed, modified, cancelled,
    filled or rejected since the previous Decision_Time. A King flip needs a
    King at both Decision_Times. ``first_alerts`` are the Setup_Keys graded
    Alert_2R for the first time this session.
    """

    grade_changes: tuple[GradeChange, ...]
    king_flips: tuple[KingFlip, ...]
    order_event: bool
    first_alerts: tuple[SetupKey, ...]

    @property
    def fired(self) -> bool:
        """Whether a Finding_Card is due for this Decision_Time (Req 25.6)."""
        return bool(self.grade_changes or self.king_flips or self.order_event)


@dataclass(frozen=True, slots=True)
class DecisionPayload:
    """Everything the decision-log entry of ``t`` records (Req 18.7).

    - ``map_entries``: each configured (symbol, metric) in Map_State order,
      with its ``asOf``, King, Floor and Ceiling, or a missing marker;
    - ``futures``: the Futures_Price of each configured instrument;
    - ``setups``: each Candidate_Setup with every Gate result, the
      Rejection_Reasons, the Grade, sizing and placement; ``skips`` the
      Detection_Skips, both in detection order;
    - ``intents``: the orders submitted, modified or cancelled at ``t``
      (management, then placement); ``management`` the planner events;
    - ``fills``: the fills since the previous Decision_Time, and
      ``bar_events`` the other bar-phase events (stop moves, the loss stop,
      rejected entries), as the runner passed them;
    - ``external_blocks`` and the ``lockouts`` active in the session;
    - ``cards``: the Finding_Card triggers.
    """

    session: date
    t: Instant
    map_entries: tuple[MapEntryLog, ...]
    snapshot_age_ns: int | Unavailable
    futures: tuple[FuturesLog, ...]
    regime: RegimeResult | MissingInput
    map_grade: MapGrade | MissingInput
    setups: tuple[SetupDecision, ...]
    skips: tuple[DetectionSkip, ...]
    intents: tuple[OrderIntent, ...]
    management: tuple[ManagementEvent, ...]
    fills: tuple[OrderFill, ...]
    bar_events: tuple[StepEvent, ...]
    external_blocks: tuple[ExternalBlock, ...]
    lockouts: tuple[Lockout, ...]
    cards: CardTriggers


@dataclass(frozen=True, slots=True)
class StepResult:
    """The next state, the order intents to send at ``t``, and the decision-log payload."""

    state: EngineState
    intents: tuple[OrderIntent, ...]
    payload: DecisionPayload


@dataclass(frozen=True, slots=True)
class _MapPhase:
    """The map phase's values at ``t``, read by the later phases."""

    map_state: MapState
    labels: Mapping[SymMetric, NodeLabels]
    converted: Mapping[SymMetric, ConvertedMap | MissingPrice]
    taps: TapView
    lifecycle: LifecycleState
    node_states: NodeStates
    vix: VixState
    regime: RegimeResult | MissingInput
    grade: MapGrade | MissingInput
    dormant: tuple[NodeId, ...]
    velocities: Mapping[SymMetric, Velocities]
    chart: ChartFeatures
    prices: Mapping[str, Ticks | Unavailable]

    @property
    def futures(self) -> dict[str, Ticks]:
        return {i: p for i, p in self.prices.items() if not isinstance(p, Unavailable)}


# ---------------------------------------------------------------- calendar helpers


def _previous_session(cal: SessionCalendar, session: date) -> date | None:
    """The latest calendar session before ``session``; ``None`` before the calendar starts."""
    d = session - _ONE_DAY
    while cal.covers(d):
        if cal.is_session(d):
            return d
        d -= _ONE_DAY
    return None


def _trading_day_start(session: date) -> Instant:
    return ny_instant(session - _ONE_DAY, TRADING_DAY_START)


def _lookback_start(cal: SessionCalendar, session: date, sessions: int) -> Instant:
    """The trading-day start of the earliest of the ``sessions`` sessions ending at ``session``."""
    first = session
    for _ in range(sessions - 1):
        prev = _previous_session(cal, first)
        if prev is None:
            break
        first = prev
    return _trading_day_start(first)


def _blocking(blocks: Sequence[ExternalBlock], instrument: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(b.kind for b in blocks if b.covers(instrument)))


# ---------------------------------------------------------------- the engine


@dataclass(frozen=True, slots=True)
class Engine:
    """The Strategy_Engine: config, session calendar and trailing Regime medians.

    ``medians`` holds the precomputed trailing medians per session
    (``fse.data.medians``); a session without an entry has none, so its Regime
    is a ``MissingInput`` (Req 7.12).
    """

    params: EngineParams
    calendar: SessionCalendar
    medians: Mapping[date, TrailingMedians] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "medians", MappingProxyType(dict(self.medians)))

    def initial_state(self) -> EngineState:
        """The state before the first input, with this engine's chart config."""
        return EngineState.initial(self.params.chart)

    def trailing_medians(self, session: date) -> TrailingMedians:
        found = self.medians.get(session)
        if found is not None:
            return found
        missing = Unavailable(f"no trailing medians were given for session {session}")
        return TrailingMedians(missing, missing)

    def prior_session(self, state: EngineState, session: date) -> PriorSession:
        """The filled trades of the session immediately before ``session`` (Req 14.5)."""
        prev = _previous_session(self.calendar, session)
        return PriorSession() if prev is None else state.trades_of(prev)

    # ---------------------------------------------------------------- bar phase

    def count_fill(
        self, state: EngineState, fill: RiskFill, trade_closed: Trade | None = None
    ) -> EngineState:
        """Bar phase 1: count one fill for the Kill_Switches and the big-win totals."""
        p = self.params
        rs = on_fill(state.risk, fill, trade_closed, p.kill_switches, self.calendar)
        nxt = replace(state, risk=rs)
        return nxt if trade_closed is None else nxt.with_trade(trade_closed)

    def update_stops(
        self, state: EngineState, bar: Bar, fills: Iterable[OrderFill] = ()
    ) -> BarPhaseResult:
        """Bar phase 2: the bar's fills into the book, then the bar-close stop updates.

        The events echo the fills of Setup_Keys in the book, then the stop moves.
        """
        given = tuple(fills)
        known = {plan.key for plan in state.book.plans}
        book, intents, events = planner_on_bar(state.book, bar, given, self.params.planner)
        echoed = tuple(f for f in given if f.order.setup_key in known)
        return BarPhaseResult(replace(state, book=book), tuple(intents), (*echoed, *events))

    def check_loss_stop(
        self,
        state: EngineState,
        t: Instant,
        positions: Mapping[str, int],
        unrealized_worst: Money,
    ) -> BarPhaseResult:
        """Bar phase 3: the internal daily loss stop at the 1-minute close ``t`` (Req 16.6).

        ``positions`` and ``unrealized_worst`` are as for
        :func:`fse.engine.risk.on_minute_close`. When the stop fires, every open
        plan of a closed instrument without a pending exit gets a market exit
        for its open contracts (and its unfilled entry remainder is cancelled),
        and a Lockout starts.
        """
        p = self.params
        rs, closes = on_minute_close(
            state.risk, t, positions, unrealized_worst, p.sizing.risk_usd, p.kill_switches,
            self.calendar,
        )  # fmt: skip
        if not closes or rs.loss_stop is None:
            return BarPhaseResult(replace(state, risk=rs))
        closed = {c.instrument for c in closes}
        intents: list[OrderIntent] = []
        plans: list[Plan] = []
        for plan in state.book.plans:
            if (
                plan.status == "open"
                and plan.instrument in closed
                and not plan.exit_pending
                and plan.open_qty > 0
            ):
                side: Side = "sell" if plan.direction == "long" else "buy"
                exit_order = Order(
                    f"{plan.sid}-exit", plan.key, plan.instrument, side, "market",
                    plan.open_qty, None, t, "exit",
                )  # fmt: skip
                intents.append(SubmitExit(exit_order))
                if plan.filled_qty < plan.entry.qty:
                    intents.append(CancelOrder(plan.entry.client_id, t))
                plan = replace(plan, exit_pending=True)
            plans.append(plan)
        book = replace(state.book, plans=tuple(plans))
        event = LossStopFired(rs.loss_stop, closes)
        return BarPhaseResult(replace(state, risk=rs, book=book), tuple(intents), (event,))

    def consume_bar(self, state: EngineState, bar: Bar, view: MarketView) -> EngineState:
        """Bar phase 4: one closed 1-minute bar into the Taps, lifecycle and chart state.

        The Node bands are those of the Map_State at the bar's close, read from
        ``view`` (whose ``t`` is at or after that close).
        """
        if bar.interval_s != BASE_INTERVAL_S:
            raise ValueError(f"consume_bar takes 1-minute bars, got a {bar.interval_s} s bar")
        if view.t < bar.close_ns:
            raise ValueError(f"bar closing at {bar.close_ns} given with a MarketView at {view.t}")
        p = self.params
        bands = node_bands(view, bar.close_ns, p.node_params, p.level_params)
        return replace(
            state,
            taps=state.taps.on_bar(bar, bands),
            lifecycle=state.lifecycle.on_bar(bar, bands),
            chart=state.chart.on_bar(bar),
        )

    # ---------------------------------------------------------------- the step

    def step(
        self,
        state: EngineState,
        view: MarketView,
        t: Instant,
        events: Iterable[StepEvent] = (),
        external_blocks: Iterable[ExternalBlock] = (),
    ) -> StepResult:
        """Evaluate Decision_Time ``t`` (see the module notes for the phase order).

        Raises ``ValueError`` when ``view`` is not at ``t``, ``t`` is not after
        the state's previous Decision_Time, or the state's chart config is not
        this engine's.
        """
        self._check(state, view, t)
        session = session_of(t)
        bar_events = tuple(events)
        blocks = tuple(external_blocks)
        book = state.book
        for event in bar_events:
            if isinstance(event, EntryRejected):
                plan = book.plan(event.key)
                if plan is not None and plan.status == "resting":
                    book = drop_rejected(book, event.key)

        m = self._map_phase(state, view, t, session)

        ctx = self._planner_context(view, t, session, m, book, state.risk)
        book, managed, management = manage(book, ctx, self.params.planner)
        book, cancels, cancelled = self._cancel_blocked(book, blocks, t)
        intents: list[OrderIntent] = [*managed, *cancels]

        detect = DetectContext(
            t, m.map_state, m.labels, m.converted, m.regime, m.grade, m.chart, m.taps, m.futures
        )
        gates = self._gate_context(view, session, detect, m, state.risk)
        prior = self.prior_session(state, session)
        decisions: list[SetupDecision] = []
        skips: list[DetectionSkip] = []
        for found in detect_setups(detect, self.params.detector_params):
            if isinstance(found, DetectionSkip):
                skips.append(found)
                continue
            book, decision, placed = self._decide(found, gates, ctx, book, prior, m.vix, blocks)
            decisions.append(decision)
            intents.extend(placed)

        fills = tuple(e for e in bar_events if isinstance(e, OrderFill))
        others = tuple(e for e in bar_events if not isinstance(e, OrderFill))
        cards, memory = _card_triggers(
            state.cards, session, m, decisions, order_event=bool(intents or bar_events)
        )
        payload = DecisionPayload(
            session=session,
            t=t,
            map_entries=_map_entries(m),
            snapshot_age_ns=m.map_state.snapshot_age_ns(),
            futures=tuple(FuturesLog(i, price) for i, price in m.prices.items()),
            regime=m.regime,
            map_grade=m.grade,
            setups=tuple(decisions),
            skips=tuple(skips),
            intents=tuple(intents),
            management=(*management, *cancelled),
            fills=fills,
            bar_events=others,
            external_blocks=blocks,
            lockouts=active_lockouts(state.risk, session),
            cards=cards,
        )
        nxt = replace(state, t=t, session=session, lifecycle=m.lifecycle, book=book, cards=memory)
        return StepResult(nxt, tuple(intents), payload)

    # ---------------------------------------------------------------- phases

    def _check(self, state: EngineState, view: MarketView, t: Instant) -> None:
        if isinstance(t, bool) or not isinstance(t, int):
            raise ValueError(f"t must be an integer Instant, got {t!r}")
        if view.t != t:
            raise ValueError(f"the MarketView is at {view.t}, not at the Decision_Time {t}")
        if state.t is not None and t <= state.t:
            raise ValueError(f"Decision_Time {t} is not after the state's {state.t}")
        if state.chart.config != self.params.chart:
            raise ValueError("the state's chart config is not this engine's chart config")

    def _map_phase(
        self, state: EngineState, view: MarketView, t: Instant, session: date
    ) -> _MapPhase:
        p = self.params
        ms = view.map_state()
        snapshots = ms.snapshots()
        labels = {(s.symbol, s.metric): classify(s, p.node_params) for s in snapshots}
        tap_view = state.taps.view(t)
        lifecycle, node_states = state.lifecycle.evaluate(view, tap_view, p.lifecycle_params)
        vix = view.vix()
        regime_result = regime(ms, self.trailing_medians(session), vix, p.regime_params)
        window = p.data.velocity_window_s
        return _MapPhase(
            map_state=ms,
            labels=MappingProxyType(labels),
            converted=convert_map(view, p.level_params),
            taps=tap_view,
            lifecycle=lifecycle,
            node_states=node_states,
            vix=vix,
            regime=regime_result,
            grade=map_state_grade(ms, p.regime_params),
            dormant=dormant_nodes(ms, regime_result, p.lifecycle_params),
            velocities=MappingProxyType(
                {(s.symbol, s.metric): velocity(view, s, window) for s in snapshots}
            ),
            chart=state.chart.features(t),
            prices=MappingProxyType({i: futures_price(view, i) for i in p.instruments}),
        )

    def _planner_context(
        self,
        view: MarketView,
        t: Instant,
        session: date,
        m: _MapPhase,
        book: OrderBook,
        risk: RiskState,
    ) -> PlannerContext:
        sources = {
            (s.symbol, s.metric): SourceView(
                s, m.labels[(s.symbol, s.metric)], m.converted[(s.symbol, s.metric)]
            )
            for s in m.map_state.snapshots()
        }
        last_close: dict[str, Ticks] = {}
        for instrument in sorted({*self.params.instruments, *(p.instrument for p in book.plans)}):
            bar = view.last_bar_closed_at_or_before(instrument, t)
            if bar is not None and bar.c_t is not None:
                last_close[instrument] = bar.c_t
        flatten_at = flatten_time(self.params.orders, self.calendar, session)
        return PlannerContext(
            t, session, flatten_at, sources, chart_legs(m.chart), last_close, risk
        )

    def _cancel_blocked(
        self, book: OrderBook, blocks: Sequence[ExternalBlock], t: Instant
    ) -> tuple[OrderBook, list[OrderIntent], list[BlockCancelled]]:
        intents: list[OrderIntent] = []
        cancelled: list[BlockCancelled] = []
        for plan in book.resting:
            kinds = _blocking(blocks, plan.instrument)
            if kinds:
                intents.append(CancelOrder(plan.entry.client_id, t))
                cancelled.append(BlockCancelled(plan.key, t, kinds))
                book = drop_rejected(book, plan.key)
        return book, intents, cancelled

    def _gate_context(
        self,
        view: MarketView,
        session: date,
        detect: DetectContext,
        m: _MapPhase,
        risk: RiskState,
    ) -> GateContext:
        cfg = self.params.gates.dark_pool_confluence
        since = _lookback_start(self.calendar, session, cfg.lookback_sessions)
        dark_pool: dict[str, tuple[DarkPoolPrint, ...] | Unavailable] = {}
        for ticker in dict.fromkeys((cfg.es_ticker, cfg.nq_ticker)):
            prints = view.dark_pool(ticker, since)
            dark_pool[ticker] = prints if isinstance(prints, Unavailable) else tuple(prints)
        day_start = _trading_day_start(session)
        minute_bars = {
            i: tuple(view.bars(i, BASE_INTERVAL_S, day_start)) for i in self.params.instruments
        }
        return GateContext(
            detect=detect,
            node_states=m.node_states,
            dormant=frozenset(m.dormant),
            velocities=m.velocities,
            vix=m.vix,
            events=tuple(view.events()),
            dark_pool=dark_pool,
            minute_bars=minute_bars,
            risk=risk,
        )

    def _decide(
        self,
        c: CandidateSetup,
        gates: GateContext,
        ctx: PlannerContext,
        book: OrderBook,
        prior: PriorSession,
        vix: VixState,
        blocks: Sequence[ExternalBlock],
    ) -> tuple[OrderBook, SetupDecision, list[OrderIntent]]:
        """Gates and Grade, then (A_Plus only) sizing and placement of one setup."""
        p = self.params
        ev = evaluate(c, gates, p.gate_params)
        if ev.grade != "A_Plus":
            return book, SetupDecision(c, ev, None, None), []
        sizing_ctx = SizingContext(prior, trinity_count(ev), vix, book.micro_equivalents())
        sized = size(c, sizing_ctx, p.sizing)
        if isinstance(sized, SizingRejection):
            return book, SetupDecision(c, ev, sized, None), []
        sized_log = Sized(sized.contracts, sized.steps)
        kinds = _blocking(blocks, c.key.instrument)
        if kinds and c.key not in book.placed:
            return book, SetupDecision(c, ev, sized_log, BlockedEntry(c.key, c.t, kinds)), []
        book, placed, rejected = place(book, [Accepted(sized, ev)], ctx, p.planner)
        outcome: PlacementOutcome
        if rejected:
            outcome = rejected[0]
        else:
            bracket = placed[0]
            assert isinstance(bracket, PlaceBracket)  # place only returns brackets
            outcome = bracket
        return book, SetupDecision(c, ev, sized_log, outcome), list(placed)


# ---------------------------------------------------------------- log helpers


def _map_entries(m: _MapPhase) -> tuple[MapEntryLog, ...]:
    out: list[MapEntryLog] = []
    for (symbol, metric), entry in m.map_state.entries.items():
        if isinstance(entry, Snapshot):
            labels = m.labels[(symbol, metric)]
            out.append(
                MapEntryLog(
                    symbol, metric, False, entry.as_of_ns, labels.king, labels.floor,
                    labels.ceiling,
                )
            )  # fmt: skip
        else:
            out.append(MapEntryLog(symbol, metric, True, None, None, None, None))
    return tuple(out)


def _card_triggers(
    memory: CardMemory,
    session: date,
    m: _MapPhase,
    decisions: Sequence[SetupDecision],
    *,
    order_event: bool,
) -> tuple[CardTriggers, CardMemory]:
    """The Finding_Card triggers at ``t`` and the memory for the next Decision_Time."""
    if memory.session != session:
        memory = CardMemory(session=session)
    grades: dict[SetupKey, Grade] = {}
    for d in decisions:
        grades.setdefault(d.setup.key, d.evaluation.grade)
    before = dict(memory.grades)
    changes = [
        GradeChange(key, before.get(key, _UNGRADED), grade)
        for key, grade in grades.items()
        if before.get(key, _UNGRADED) != grade
    ]
    changes.extend(
        GradeChange(key, grade, _UNGRADED)
        for key, grade in memory.grades
        if key not in grades and grade != _UNGRADED
    )
    kings = tuple(
        (key, m.labels[key].king if key in m.labels else None) for key in m.map_state.entries
    )
    previous = dict(memory.kings)
    flips: list[KingFlip] = []
    for (symbol, metric), king in kings:
        old = previous.get((symbol, metric))
        if old is not None and king is not None and old != king:
            flips.append(KingFlip(symbol, metric, old, king))
    alerted = set(memory.alerted)
    first = tuple(k for k, g in grades.items() if g == "Alert_2R" and k not in alerted)
    triggers = CardTriggers(tuple(changes), tuple(flips), order_event, first)
    nxt = CardMemory(session, tuple(grades.items()), kings, (*memory.alerted, *first))
    return triggers, nxt
