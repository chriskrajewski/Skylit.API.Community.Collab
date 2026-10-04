"""Property 39: Flatten_Time.

*For any* session (full or early close) and order book, at the first
Decision_Time at or after Flatten_Time every resting entry is cancelled and
every open position gets a market close, and no entry order is placed at any
later Decision_Time of that session.

Each example plays one session through the Order_Planner at every
Decision_Time, in the engine's phase order: the bar phase (:func:`on_bar`
with the fills of the 1-minute bar that closes at the Decision_Time), the
management phase (:func:`manage`), then the setup phase (:func:`place`). The
planner reads Flatten_Time from ``PlannerContext.flatten_at``, wired from
:func:`flatten_time` as the engine does.

The reference reads the Glossary and Requirement 12 criteria 15-17 directly:

- Flatten_Time is ``orders.flatten_time`` on the session date in New York,
  or ``orders.early_close_flatten_lead_min`` minutes before the early close,
  turned into an Instant with ``zoneinfo`` so the date's own EST or EDT
  offset applies. :func:`flatten_time` and ``SessionCalendar.flatten_time``
  (with the same settings) both give it;
- before Flatten_Time nothing is flattened: no Flattened event and no
  ``flatten_time`` cancel cause;
- at the first Decision_Time at or after it (12.15, 12.16), every resting
  entry gets a CancelOrder and an EntryCancelled naming ``flatten_time``;
  every open position gets one market SubmitExit for all its open contracts,
  a Flattened event and a cancel of any unfilled entry remainder, unless its
  market exit is already pending from an earlier invalidation exit, in which
  case that exit stands and no second one is sent. Afterwards the book holds
  only the open positions, each with its market exit pending;
- at that and every later Decision_Time of the session (12.17), no entry
  bracket is placed: each A_Plus setup offered, plus one fresh A_Plus probe,
  is declined with ``flatten_time`` (a non-A_Plus setup with ``not_a_plus``,
  checked first), and the book is unchanged.

Generators:

- session: the Friday before or the Monday after each DST change in the
  exchange calendar's cover, a real early-close date, or any weekday from
  2023-03-28 to 2026-12-31. Each session is full or has an early close at
  13:15, 13:00 or any whole minute from 09:31 to 15:59 (so DST-adjacent dates
  get early closes too), and a lead long enough can put Flatten_Time before
  09:30;
- ``orders``: Flatten_Time 15:55, 09:30, 16:00 or any minute between; lead 15,
  30, 120 or any value between; ``max_open`` 1-10; max age off or 1-390
  minutes; each Cancel_Trigger on or off; the King-flip invalidation action
  any of the four;
- Decision_Times: the backtest grid at 60 s to 1 h cadence, or none; one live
  Decision_Time 0 ns, 1 ns, 1 s, 1 min or up to 2 h after Flatten_Time (so a
  Decision_Time at or after it always exists, clamped to 16:00 or the
  Flat_Deadline, whichever is later); up to 5 more within 30 minutes either
  side of it (1 ns and 1 min either side are favoured); and up to 3 anywhere
  in the trading day, which starts 18:00 the day before;
- up to 12 steps at chosen Decision_Times: up to 2 offered setups (long or
  short from any Node, 1-80 ticks of risk, 1-3 contracts, mostly A_Plus),
  full or one-contract entry fills and stop fills on the bar closing at that
  Decision_Time, and a King flip in the Map_State (which can cancel entries
  and, for ``exit_market`` or a failed ``breakeven``, send invalidation exits
  before Flatten_Time). The book at Flatten_Time therefore mixes resting
  entries, full and partial positions, and positions with an exit pending.

Two explicit examples: a full session on the first EDT Monday with a grid
Decision_Time exactly at 15:55, and an early close on the last EDT Friday
with live Decision_Times 1 ns either side of Flatten_Time.

**Validates: Requirements 12.15, 12.16, 12.17**
"""

from __future__ import annotations

from calendar import timegm
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from types import MappingProxyType
from typing import Final, Literal
from zoneinfo import ZoneInfo

from hypothesis import event, example, given
from hypothesis import strategies as st

from fse.config.schema.exits import ExitsConfig
from fse.config.schema.gates import GatesConfig
from fse.config.schema.nodes import NodesConfig
from fse.config.schema.orders import CANCEL_TRIGGERS, InvalidationAction, OrdersConfig
from fse.engine.gates.registry import GateEvaluation, Grade
from fse.engine.levels import Conversion, ConvertedMap, band
from fse.engine.nodes import NodeParams, classify
from fse.engine.planner import (
    Accepted,
    CancelOrder,
    EntryCancelled,
    Flattened,
    OrderBook,
    OrderFill,
    OrderIntent,
    PlaceBracket,
    Plan,
    PlannerConfig,
    PlannerContext,
    PlannerEvent,
    SourceView,
    SubmitExit,
    flatten_time,
    manage,
    on_bar,
    place,
)
from fse.engine.sizing import SIZING_STEPS, SizedSetup, StepOutcome
from fse.engine.targets import exit_mode_for
from fse.engine.types import (
    Bar,
    CandidateSetup,
    Direction,
    Fill,
    Order,
    Regime,
    SetupInputs,
    SetupKey,
    Side,
    Snapshot,
    SnapshotAsOf,
    SourceNodeRef,
)
from fse.pit.protocols import SymMetric
from fse.timekit import (
    NS_PER_MINUTE,
    NS_PER_SECOND,
    RTH_CLOSE,
    Instant,
    SessionCalendar,
    SessionTimes,
    ny_instant,
)

NEW_YORK: Final = ZoneInfo("America/New_York")

COVER_FIRST: Final = date(2023, 3, 28)
COVER_LAST: Final = date(2026, 12, 31)
DST_ADJACENT: Final[tuple[date, ...]] = (
    date(2023, 11, 3), date(2023, 11, 6),
    date(2024, 3, 8), date(2024, 3, 11), date(2024, 11, 1), date(2024, 11, 4),
    date(2025, 3, 7), date(2025, 3, 10), date(2025, 10, 31), date(2025, 11, 3),
    date(2026, 3, 6), date(2026, 3, 9), date(2026, 10, 30), date(2026, 11, 2),
)  # fmt: skip
"""The Friday before and the Monday after each DST change from 2023-03-28 to 2026-12-31."""
EARLY_CLOSE_DATES: Final[tuple[date, ...]] = (
    date(2023, 7, 3), date(2023, 11, 24), date(2024, 7, 3), date(2024, 11, 29),
    date(2024, 12, 24), date(2025, 7, 3), date(2025, 11, 28), date(2025, 12, 24),
    date(2026, 11, 27), date(2026, 12, 24),
)  # fmt: skip
"""The early closes in ``calendars/exchange_calendar.yaml``."""

# One SPX gamma Map with an offset-0 conversion: a strike's MES level is the
# strike in points (5780 -> 23120 ticks). With node_fraction 0.2 every strike
# but 5850 is a Node, flipped or not.
SOURCE_KEY: Final[SymMetric] = ("SPX", "gamma")
SPOT: Final = 5791.25
HALF_WIDTH: Final = 5.0
BASE_VALUES: Final[Mapping[float, float]] = MappingProxyType(
    {
        5760.0: 1.0e9,
        5770.0: -2.2e9,
        5780.0: 2.5e9,
        5790.0: 1.0e9,
        5800.0: -2.2e9,
        5825.0: 3.0e9,  # King
        5850.0: 1.0e8,
    }
)
FLIP_STRIKE: Final = 5760.0
FLIP_VALUE: Final = 4.0e9  # makes 5760 the King: a King flip
NODE_STRIKES: Final[tuple[float, ...]] = (5760.0, 5770.0, 5780.0, 5790.0, 5800.0, 5825.0)
NODE_PARAMS: Final = NodeParams.from_config(NodesConfig())

EXITS: Final = ExitsConfig()
GATES: Final = GatesConfig()
REGIME: Final[Regime] = "Negative_Gamma"
EXIT_MODE: Final = exit_mode_for(EXITS, REGIME).mode
BAR_NS: Final = 60 * NS_PER_SECOND
FEES: Final = Decimal("0.74")

type FillChoice = Literal["none", "full", "one"]

DIRECTIONS: Final[tuple[Direction, ...]] = ("long", "short")
GRADES: Final[tuple[Grade, ...]] = ("A_Plus", "A_Plus", "A_Plus", "Alert_2R", "Pass")
FILL_CHOICES: Final[tuple[FillChoice, ...]] = ("none", "full", "one")
KING_FLIP_ACTIONS: Final[tuple[InvalidationAction | None, ...]] = (
    "exit_market",
    "breakeven",
    "hold",
    None,
)


# ---------------------------------------------------------------- scenario


@dataclass(frozen=True, slots=True)
class Offer:
    """One accepted, sized Candidate_Setup offered to :func:`place`."""

    direction: Direction
    strike: float
    risk: int  # entry-to-stop ticks
    contracts: int
    grade: Grade


@dataclass(frozen=True, slots=True)
class Step:
    """What happens at one Decision_Time besides management.

    ``entry_fills`` and ``stop_fills`` pair, in placement order, with the
    plans whose entry or stop can fill on the bar closing at the Decision_Time.
    """

    flip: bool = False
    entry_fills: tuple[FillChoice, ...] = ()
    stop_fills: tuple[bool, ...] = ()
    offers: tuple[Offer, ...] = ()


QUIET: Final = Step()
PROBE: Final = Offer("long", 5780.0, 20, 1, "A_Plus")


@dataclass(frozen=True, slots=True)
class Scenario:
    """One session: its early close, the ``orders`` section, Decision_Times and steps."""

    session: date
    early_close: time | None
    orders: OrdersConfig
    decision_times: tuple[Instant, ...]  # strictly increasing
    steps: tuple[tuple[int, Step], ...]  # (index into decision_times, step)


def wall(minutes_after_open: int) -> time:
    """The New York wall time ``minutes_after_open`` after 09:30."""
    total = 9 * 60 + 30 + minutes_after_open
    return time(total // 60, total % 60)


def session_calendar(
    session: date, early_close: time | None, orders: OrdersConfig
) -> SessionCalendar:
    times = SessionTimes(
        flatten_time=orders.flatten_time,
        flatten_early_close_lead_min=orders.early_close_flatten_lead_min,
    )
    closes = {} if early_close is None else {session: early_close}
    return SessionCalendar(session, session, early_closes=closes, times=times)


def expected_flatten(session: date, early_close: time | None, orders: OrdersConfig) -> Instant:
    """Flatten_Time by the Glossary, through ``zoneinfo`` (the date's own UTC offset)."""
    if early_close is None:
        local = datetime.combine(session, orders.flatten_time, tzinfo=NEW_YORK)
    else:
        close = datetime.combine(session, early_close, tzinfo=NEW_YORK)
        local = close - timedelta(minutes=orders.early_close_flatten_lead_min)
    return timegm(local.utctimetuple()) * NS_PER_SECOND


def day_bounds(cal: SessionCalendar, session: date) -> tuple[Instant, Instant]:
    """The trading-day start, and 16:00 or the Flat_Deadline, whichever is later."""
    end = max(cal.flat_deadline(session), ny_instant(session, RTH_CLOSE))
    return cal.trading_day_start(session), end


def build(
    session: date,
    early_close: time | None,
    orders: OrdersConfig,
    *,
    cadence_s: int | None,
    after: int,
    near: Sequence[int] = (),
    extra: Sequence[Instant] = (),
    steps: Sequence[tuple[int, Step]] = (),
) -> Scenario:
    """The scenario with the grid, one Decision_Time ``after`` Flatten_Time, and the others."""
    cal = session_calendar(session, early_close, orders)
    flat = expected_flatten(session, early_close, orders)
    start, end = day_bounds(cal, session)
    grid = () if cadence_s is None else cal.decision_times(session, cadence_s)
    live = [flat + min(after, end - flat), *(flat + d for d in near), *extra]
    dts = tuple(sorted({*grid, *(min(max(t, start), end) for t in live)}))
    return Scenario(session, early_close, orders, dts, tuple(steps))


# ---------------------------------------------------------------- generators

SESSIONS: Final = st.one_of(
    st.sampled_from(DST_ADJACENT + EARLY_CLOSE_DATES),
    st.dates(COVER_FIRST, COVER_LAST).filter(lambda d: d.weekday() < 5),
)
EARLY_CLOSES: Final = st.one_of(
    st.none(),
    st.none(),
    st.sampled_from((time(13, 15), time(13, 0))),
    st.integers(1, 389).map(wall),
)
NEAR_OFFSETS: Final = st.one_of(
    st.sampled_from((-NS_PER_MINUTE, -1, 1, NS_PER_MINUTE)),
    st.integers(-30 * NS_PER_MINUTE, 30 * NS_PER_MINUTE),
)
AFTER: Final = st.one_of(
    st.sampled_from((0, 1, NS_PER_SECOND, NS_PER_MINUTE)), st.integers(0, 120 * NS_PER_MINUTE)
)
OFFERS: Final = st.builds(
    Offer,
    direction=st.sampled_from(DIRECTIONS),
    strike=st.sampled_from(NODE_STRIKES),
    risk=st.integers(1, 80),
    contracts=st.integers(1, 3),
    grade=st.sampled_from(GRADES),
)
STEPS: Final = st.builds(
    Step,
    flip=st.sampled_from((False, False, False, True)),
    entry_fills=st.lists(st.sampled_from(FILL_CHOICES), max_size=4).map(tuple),
    stop_fills=st.lists(st.sampled_from((False, False, True)), max_size=3).map(tuple),
    offers=st.lists(OFFERS, max_size=2).map(tuple),
)


@st.composite
def orders_configs(draw: st.DrawFn) -> OrdersConfig:
    flat = draw(st.one_of(st.sampled_from((time(15, 55), time(9, 30), time(16, 0))),
                          st.integers(0, 390).map(wall)))  # fmt: skip
    return OrdersConfig.model_validate(
        {
            "flatten_time": f"{flat.hour:02d}:{flat.minute:02d}",
            "early_close_flatten_lead_min": draw(
                st.one_of(st.sampled_from((15, 30, 120)), st.integers(15, 120))
            ),
            "max_open": draw(st.integers(1, 10)),
            "max_age_min": draw(st.one_of(st.none(), st.integers(1, 390))),
            "cancel_triggers": {t: draw(st.booleans()) for t in CANCEL_TRIGGERS},
            "invalidation": {"king_flip": draw(st.sampled_from(KING_FLIP_ACTIONS))},
        }
    )


@st.composite
def scenarios(draw: st.DrawFn) -> Scenario:
    session = draw(SESSIONS)
    early_close = draw(EARLY_CLOSES)
    orders = draw(orders_configs())
    start, end = day_bounds(session_calendar(session, early_close, orders), session)
    s = build(
        session,
        early_close,
        orders,
        cadence_s=draw(st.sampled_from((None, 60, 300, 900, 1800, 3600))),
        after=draw(AFTER),
        near=draw(st.lists(NEAR_OFFSETS, max_size=5)),
        extra=draw(st.lists(st.integers(start, end), max_size=3)),
    )
    # Most steps build the book before Flatten_Time; a few offer setups after it.
    flat = expected_flatten(session, early_close, orders)
    pre = [i for i, t in enumerate(s.decision_times) if t < flat]
    post = [i for i, t in enumerate(s.decision_times) if t >= flat]
    steps: list[tuple[int, Step]] = []
    if pre:
        steps += draw(st.lists(st.tuples(st.sampled_from(pre), STEPS), min_size=1, max_size=10))
    steps += draw(st.lists(st.tuples(st.sampled_from(post), STEPS), max_size=3))
    return Scenario(s.session, s.early_close, s.orders, s.decision_times, tuple(steps))


# ---------------------------------------------------------------- the planner's inputs


def map_view(as_of: Instant, flipped: bool) -> SourceView:
    """The SPX gamma Map at ``as_of``; ``flipped`` makes 5760 the King."""
    values = dict(BASE_VALUES)
    if flipped:
        values[FLIP_STRIKE] = FLIP_VALUE
    strikes = tuple(values)
    s = Snapshot(
        symbol="SPX", metric="gamma", view_id="view-p39", as_of_ns=as_of, as_of_raw="raw",
        spot=SPOT, previous_close=None, strikes=strikes, values=tuple(values.values()),
        node_types=None, expirations=("2026-03-05",), resolution="1s", source_endpoint="range",
        extra_json="{}",
    )  # fmt: skip
    conv = Conversion(
        symbol="SPX", family="ES", instrument="MES", contract="MESH6", method="offset",
        factor=0.0, futures_close=SPOT, spot=SPOT, as_of_ns=as_of, paired_bar_close_ns=as_of,
    )  # fmt: skip
    levels = tuple(conv.level(k) for k in strikes)
    cm = ConvertedMap(
        symbol="SPX", metric="gamma", as_of_ns=as_of, conversion=conv,
        band_half_width_pts=HALF_WIDTH, strikes=strikes, levels=levels,
        bands=tuple(band(lv, HALF_WIDTH) for lv in levels),
    )  # fmt: skip
    return SourceView(s, classify(s, NODE_PARAMS), cm)


def accept(offer: Offer, tap_seq: int, session: date, t: Instant, view: SourceView) -> Accepted:
    """``offer`` as a sized Candidate_Setup of the Map ``view`` at ``t``, graded ``offer.grade``."""
    assert isinstance(view.converted, ConvertedMap)
    sign = 1 if offer.direction == "long" else -1
    level = view.converted.level_of(offer.strike)
    value = dict(zip(view.snapshot.strikes, view.snapshot.values, strict=True))[offer.strike]
    key = SetupKey("MES", "floor_ceiling_bounce", offer.strike, offer.direction, session, tap_seq)
    src = SourceNodeRef("SPX", "gamma", offer.strike, value, level, band(level, HALF_WIDTH))
    inputs = SetupInputs(
        map_as_of=(SnapshotAsOf("SPX", "gamma", view.snapshot.as_of_ns),), source_spot=SPOT,
        futures_price=level, conversion_method="offset", conversion_factor=0.0,
        band_half_width_pts=HALF_WIDTH, regime=REGIME, map_grade="Neutral_Map",
        stop_rule="one_node_beyond",
    )  # fmt: skip
    c = CandidateSetup(
        key, "floor_ceiling_bounce", t, level, level - sign * offer.risk,
        (level + sign * 3 * offer.risk,), EXIT_MODE, src, inputs,
    )  # fmt: skip
    outcomes = tuple(StepOutcome(s, offer.contracts, s == "base_contracts") for s in SIZING_STEPS)
    return Accepted(
        SizedSetup(c, offer.contracts, outcomes), GateEvaluation(key, t, (), (), offer.grade)
    )


def bar_phase(
    book: OrderBook, step: Step, t: Instant, cfg: PlannerConfig
) -> tuple[OrderBook, int | None]:
    """Apply the fills ``step`` asks for on the MES bar closing at ``t``; the bar's close.

    An entry fills only if it was placed by the bar's open; neither an entry
    nor a stop fills once the plan's market exit is pending.
    """
    bar_open = t - BAR_NS
    fills: list[OrderFill] = []
    fillable = [
        p for p in book.plans
        if not p.exit_pending and p.filled_qty < p.entry.qty and p.entry.placed_at <= bar_open
    ]  # fmt: skip
    for plan, choice in zip(fillable, step.entry_fills, strict=False):
        remaining = plan.entry.qty - plan.filled_qty
        qty = {"none": 0, "full": remaining, "one": 1}[choice]
        if qty:
            fill = Fill(plan.entry.client_id, bar_open, plan.entry_limit, qty, FEES)
            fills.append(OrderFill(plan.entry, fill))
    entered = {f.order.setup_key for f in fills}
    stoppable = [
        p for p in book.open_positions
        if not p.exit_pending and p.key not in entered and p.stop.placed_at <= bar_open
    ]  # fmt: skip
    for plan, hit in zip(stoppable, step.stop_fills, strict=False):
        if hit:
            fill = Fill(plan.stop.client_id, bar_open, plan.stop_price, plan.open_qty, FEES)
            fills.append(OrderFill(plan.stop, fill))
    if not fills:
        return book, None
    prices = [f.fill.price for f in fills]
    lo, hi, close = min(prices) - 1, max(prices) + 1, prices[-1]
    bar = Bar(
        instrument="MES", contract="MESH6", interval_s=60, open_ns=bar_open, close_ns=t,
        o=close / 4, h=hi / 4, l=lo / 4, c=close / 4, v=100.0,
        o_t=close, h_t=hi, l_t=lo, c_t=close, source="atlas",
    )  # fmt: skip
    out, _, _ = on_bar(book, bar, fills, cfg)
    return out, close


# ---------------------------------------------------------------- checks


def exit_side(plan: Plan) -> Side:
    return "sell" if plan.direction == "long" else "buy"


def check_flatten(
    before: OrderBook,
    after: OrderBook,
    intents: Sequence[OrderIntent],
    events: Sequence[PlannerEvent],
    pending_exits: Mapping[SetupKey | None, Order],
    t: Instant,
) -> None:
    """Req 12.15-12.16 at the first Decision_Time ``t`` at or after Flatten_Time."""
    exits: dict[SetupKey | None, list[Order]] = {}
    for intent in intents:
        if isinstance(intent, SubmitExit):
            exits.setdefault(intent.order.setup_key, []).append(intent.order)
    cancels = {i.client_id for i in intents if isinstance(i, CancelOrder) and i.at == t}
    cancelled = {e.key: e for e in events if isinstance(e, EntryCancelled)}
    flattened = [e for e in events if isinstance(e, Flattened)]

    for plan in before.resting:
        assert plan.entry.client_id in cancels, f"resting entry {plan.key} not cancelled"
        record = cancelled.get(plan.key)
        assert record is not None
        assert record.t == t
        assert "flatten_time" in record.causes
    for plan in before.open_positions:
        sent = exits.get(plan.key, [])
        if plan.exit_pending:
            # An earlier invalidation exit already closes every open contract at market.
            assert sent == []
            earlier = pending_exits[plan.key]
            assert (earlier.kind, earlier.side, earlier.qty) == (
                "market", exit_side(plan), plan.open_qty,
            )  # fmt: skip
            continue
        assert len(sent) == 1, f"open position {plan.key} got {len(sent)} market exits"
        (order,) = sent
        assert (order.kind, order.side, order.qty, order.price, order.placed_at, order.role) == (
            "market", exit_side(plan), plan.open_qty, None, t, "exit",
        )  # fmt: skip
        assert flattened.count(Flattened(plan.key, t, plan.open_qty)) == 1
        if plan.filled_qty < plan.entry.qty:
            assert plan.entry.client_id in cancels, f"entry remainder of {plan.key} not cancelled"
    assert after.resting == ()
    assert [p.key for p in after.plans] == [p.key for p in before.open_positions]
    assert all(p.exit_pending for p in after.plans)


def play(s: Scenario) -> None:
    cal = session_calendar(s.session, s.early_close, s.orders)
    flat = expected_flatten(s.session, s.early_close, s.orders)
    flatten_at = flatten_time(s.orders, cal, s.session)
    assert flatten_at == flat
    assert cal.flatten_time(s.session) == flat
    cfg = PlannerConfig(orders=s.orders, exits=EXITS, gates=GATES)

    steps = dict(s.steps)
    book = OrderBook()
    flipped = False
    view = map_view(s.decision_times[0], flipped)
    last_close: dict[str, int] = {}
    pending_exits: dict[SetupKey | None, Order] = {}
    tap_seq = 0
    prev_t: Instant | None = None
    first_after: Instant | None = None
    declined_offers = 0

    for i, t in enumerate(s.decision_times):
        step = steps.get(i, QUIET)
        # Bar phase: the bar closing at t, if it opens at or after the previous Decision_Time.
        if prev_t is not None and t - BAR_NS >= prev_t:
            book, close = bar_phase(book, step, t, cfg)
            if close is not None:
                last_close["MES"] = close
        # Map phase.
        if step.flip:
            flipped = not flipped
            view = map_view(t, flipped)
        ctx = PlannerContext(
            t=t, session=s.session, flatten_at=flatten_at, sources={SOURCE_KEY: view},
            last_close=last_close,
        )  # fmt: skip

        # Management phase.
        before = book
        book, intents, events = manage(before, ctx, cfg)
        if t < flat:
            assert not any(isinstance(e, Flattened) for e in events)
            assert not any(
                isinstance(e, EntryCancelled) and "flatten_time" in e.causes for e in events
            )
        elif first_after is None:
            first_after = t
            check_flatten(before, book, intents, events, pending_exits, t)
            event("Flatten_Time: resting entry" if before.resting else "Flatten_Time: no resting")
            event("Flatten_Time: open position" if before.open_positions else "Flatten_Time: flat")
            if any(p.filled_qty < p.entry.qty for p in before.open_positions):
                event("Flatten_Time: partial fill")
            if any(p.exit_pending for p in before.open_positions):
                event("Flatten_Time: exit already pending")
            lag = {0: "at", 1: "1 ns after"}.get(t - flat, "later than 1 ns after")
            event(f"first Decision_Time {lag} Flatten_Time")
        else:
            assert book.resting == ()
        assert not any(isinstance(x, PlaceBracket) for x in intents)
        for intent in intents:
            if isinstance(intent, SubmitExit):
                pending_exits[intent.order.setup_key] = intent.order

        # Setup phase.
        accepted: list[Accepted] = []
        for offer in (*step.offers, *((PROBE,) if t >= flat else ())):
            tap_seq += 1
            accepted.append(accept(offer, tap_seq, s.session, t, view))
        placed, placements, rejections = place(book, accepted, ctx, cfg)
        if t >= flat:
            assert placements == []
            assert placed.plans == book.plans
            expected = ["flatten_time" if a.evaluation.grade == "A_Plus" else "not_a_plus"
                        for a in accepted]  # fmt: skip
            assert [r.reason for r in rejections] == expected
            declined_offers += len(step.offers)
        book = placed
        prev_t = t

    assert first_after is not None  # build() always adds a Decision_Time at or after it
    event("early-close session" if s.early_close is not None else "full session")
    if s.session in DST_ADJACENT:
        event("DST-adjacent session")
    if declined_offers:
        event("offered setups after Flatten_Time")


# ---------------------------------------------------------------- examples

EXAMPLE_ORDERS: Final = OrdersConfig.model_validate(
    {"max_open": 4, "cancel_triggers": {"opposition_in_target": False}}
)
FIRST_EDT_MONDAY: Final = build(
    date(2026, 3, 9), None, EXAMPLE_ORDERS, cadence_s=300, after=0, near=(NS_PER_MINUTE,),
    steps=(
        (0, Step(offers=(Offer("long", 5780.0, 20, 2, "A_Plus"),
                         Offer("short", 5800.0, 16, 3, "A_Plus")))),
        (1, Step(entry_fills=("one",))),  # 1 of the 2 long contracts fills
        (2, Step(offers=(Offer("long", 5770.0, 12, 1, "A_Plus"),))),
        (78, Step(offers=(Offer("short", 5790.0, 8, 1, "A_Plus"),))),  # 15:56
    ),
)  # fmt: skip
"""Grid Decision_Time 77 is 15:55 EDT exactly: one partial position and two resting entries."""
_LAST_EDT_FRIDAY: Final = date(2026, 10, 30)
LAST_EDT_FRIDAY_EARLY_CLOSE: Final = build(
    _LAST_EDT_FRIDAY, time(13, 15), EXAMPLE_ORDERS, cadence_s=None, after=1,
    near=(-1, NS_PER_MINUTE),
    extra=(ny_instant(_LAST_EDT_FRIDAY, time(10, 0)), ny_instant(_LAST_EDT_FRIDAY, time(10, 5))),
    steps=(
        (0, Step(offers=(Offer("long", 5780.0, 20, 1, "A_Plus"),
                         Offer("short", 5825.0, 12, 2, "A_Plus")))),
        (1, Step(entry_fills=("full",))),
        (2, Step(offers=(Offer("long", 5760.0, 10, 1, "A_Plus"),))),  # 12:44:59.999999999
        (3, Step(offers=(Offer("long", 5790.0, 10, 1, "A_Plus"),))),  # 12:45:00.000000001
        (4, Step(offers=(Offer("short", 5800.0, 10, 1, "Alert_2R"),))),  # 12:46
    ),
)  # fmt: skip
"""Flatten_Time 12:45 EDT (13:15 close, 30-minute lead), with Decision_Times 1 ns either side."""


@given(scenarios())
@example(FIRST_EDT_MONDAY)
@example(LAST_EDT_FRIDAY_EARLY_CLOSE)
def test_flatten_time_cancels_closes_and_blocks_entries(s: Scenario) -> None:
    """**Validates: Requirements 12.15, 12.16, 12.17**"""
    play(s)


def test_generator_fixtures() -> None:
    """The fixed inputs mean what the generators assume."""
    for friday, monday in zip(DST_ADJACENT[::2], DST_ADJACENT[1::2], strict=True):
        noon = time(12, 0)
        assert friday.weekday() == 4
        assert monday.weekday() == 0
        assert (monday - friday).days == 3
        before = datetime.combine(friday, noon, tzinfo=NEW_YORK).utcoffset()
        after = datetime.combine(monday, noon, tzinfo=NEW_YORK).utcoffset()
        assert before != after
    for flipped, king in ((False, 5825.0), (True, FLIP_STRIKE)):
        labels = map_view(0, flipped).labels
        assert (labels.king, labels.nodes) == (king, NODE_STRIKES)
    assert FIRST_EDT_MONDAY.decision_times[77] == ny_instant(date(2026, 3, 9), time(15, 55))
    assert LAST_EDT_FRIDAY_EARLY_CLOSE.decision_times[2:4] == (
        ny_instant(_LAST_EDT_FRIDAY, time(12, 45)) - 1,
        ny_instant(_LAST_EDT_FRIDAY, time(12, 45)) + 1,
    )
