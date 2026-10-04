"""Property 41: Cancel and invalidation triggers.

*For any* resting entry or open position and any sequence of Map_States, a
resting entry is cancelled exactly at the first Decision_Time where an
enabled Cancel_Trigger fires (all fired triggers recorded) or its max age is
reached, and otherwise keeps its original entry, stop and target prices; for
open positions the applied action follows exit-at-market > breakeven > hold
precedence, and a breakeven action with the last close at or past
Breakeven_Price against the trade becomes a market close.

The reference model reads Requirement 12 criteria 19-23 and design §12, and
compares every trigger with the values at the setup's Decision_Time:

- King flip: both Snapshots have a King and it is another strike;
- source Node gone: the source strike is not a Node of the current Snapshot;
- sign flip: the source strike's current value (first listing) is strictly
  of the opposite sign to its value at the setup's Decision_Time;
- stdev leg dropped: the setup used a BOS_Leg (the stdev_fib_zone Gate is on
  and the leg's nearest zone is within the band half-width of entry) and the
  instrument's chart features no longer list it;
- opposition in target: another current Node with |value| at least
  ``fraction`` x |source value at the setup's Decision_Time| has a converted
  level beyond entry by more than 0 and at most ``window_r`` x the setup's
  entry-to-stop distance;
- a trigger needs its data: no Snapshot leaves the four Map triggers unfired,
  no conversion price leaves opposition in target unfired, and no chart
  features leaves stdev leg dropped unfired.

A resting entry with an enabled trigger or its max age reached (elapsed time
since placement at least ``max_age_min``) is cancelled with every cause, in
the documented order (the Cancel_Triggers, then ``max_age``); otherwise the
book keeps it unchanged. An open position records every trigger with an
invalidation action and applies the strongest: exit at market (a market exit
for the open contracts), breakeven (the stop tightened to the
Breakeven_Price, or a market exit when the latest close is at or past it
against the trade) or hold (nothing). After a market exit nothing more
happens; after a cancel the book is empty and the key stays placed.

Generators: a long or short MES setup fading the 5780 SPX gamma Node with 1
to 200 ticks of risk, placed at 10:00 and either left resting or filled in
full on the bar at 10:00; 1 to 6 later Decision_Times, whole minutes or
seconds apart, each with or without a Snapshot (values kept, redrawn,
dropped, a new King, the source negated, shrunk, zeroed or dropped, and a
Node on or just past the opposition window edge at or just below the value
threshold), a conversion offset that moves, a missing price, chart features
with or without the used leg (contained, within, on or just past the
half-width), and a last close on or near the Breakeven_Price. Every trigger
flag, invalidation action, max age and Breakeven_Price offset varies. Two
explicit examples put a King flip with a breakeven action against a close on
the Breakeven_Price (exit at market) and a tick better (stop moved).

**Validates: Requirements 12.19, 12.20, 12.21, 12.22, 12.23**
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
from fractions import Fraction
from typing import Final

from hypothesis import event, example, given
from hypothesis import strategies as st

from fse.config.schema.exits import ExitsConfig
from fse.config.schema.gates import GatesConfig
from fse.config.schema.nodes import NodesConfig
from fse.config.schema.orders import (
    CANCEL_TRIGGERS,
    CancelTrigger,
    InvalidationAction,
    OrdersConfig,
)
from fse.engine.chart import BosLeg
from fse.engine.gates.registry import GateEvaluation
from fse.engine.levels import Conversion, ConvertedMap, band
from fse.engine.nodes import NodeLabels, NodeParams, classify
from fse.engine.planner import (
    Accepted,
    CancelCause,
    CancelOrder,
    EntryCancelled,
    Invalidated,
    ModifyOrder,
    OrderBook,
    OrderFill,
    OrderIntent,
    PlaceBracket,
    Plan,
    PlannerConfig,
    PlannerContext,
    PlannerEvent,
    SourceView,
    StopMoved,
    SubmitExit,
    manage,
    on_bar,
    place,
)
from fse.engine.sizing import SIZING_STEPS, SizedSetup, StepOutcome
from fse.engine.types import (
    Bar,
    CandidateSetup,
    Direction,
    Fill,
    MissingPrice,
    SetupInputs,
    SetupKey,
    Side,
    Snapshot,
    SnapshotAsOf,
    SourceNodeRef,
)
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, ny_instant

DIRECTIONS: Final[tuple[Direction, ...]] = ("long", "short")
EXIT_SIDE: Final[Mapping[Direction, Side]] = {"long": "sell", "short": "buy"}
ACTIONS: Final[tuple[InvalidationAction | None, ...]] = (None, "exit_market", "breakeven", "hold")
STRONGEST_FIRST: Final[tuple[InvalidationAction, ...]] = ("exit_market", "breakeven", "hold")

SESSION: Final = date(2026, 3, 5)
T0: Final = ny_instant(SESSION, time(10, 0))  # the setup's Decision_Time and placement
FLATTEN_AT: Final = ny_instant(SESSION, time(15, 55))  # after every generated Decision_Time
SOURCE_K: Final = 23_120  # the source strike 5780 in quarter points
SOURCE_STRIKE: Final = SOURCE_K / 4
SPOT: Final = 5791.25
HALF_WIDTH_PTS: Final = 5.0
NODE_PARAMS: Final = NodeParams.from_config(NodesConfig())
ZONES: Final = ((-2.0, -2.5), (-3.5, -4.5))  # (near, far) Fibonacci ratios, written out
KING_PEAK: Final = Decimal("2e10")  # above every generated magnitude: a new King

type Listing = tuple[tuple[int, Decimal], ...]
"""A Snapshot as written: (strike in quarter points, signed value), in listing order."""


# ---------------------------------------------------------------- scenario


@dataclass(frozen=True, slots=True)
class Step:
    """One later Decision_Time ``gap_ns`` after the previous one and what it shows."""

    gap_ns: int
    listing: Listing | None  # None: no source Snapshot
    offset: int  # conversion offset in ticks
    priced: bool  # False: no conversion price
    legs: tuple[BosLeg, ...] | None  # None: no chart features for MES
    last_close: int | None  # None: no closed bar yet


@dataclass(frozen=True, slots=True)
class Scenario:
    direction: Direction
    entry: int
    risk: int  # |entry - stop| ticks
    contracts: int
    fill_close: int | None  # None: the entry stays resting
    listing0: Listing  # the source Snapshot at the setup's Decision_Time
    offset0: int
    legs0: tuple[BosLeg, ...] | None
    used_leg: BosLeg | None  # the leg the setup used, by construction
    fraction: Decimal
    window_r: Decimal
    flags: Mapping[CancelTrigger, bool]
    actions: Mapping[CancelTrigger, InvalidationAction | None]
    max_age_min: int | None
    stdev_enabled: bool
    breakeven_enabled: bool
    offset_ticks: int
    steps: tuple[Step, ...]

    @property
    def sign(self) -> int:
        return 1 if self.direction == "long" else -1

    @property
    def source_value(self) -> Decimal:
        return first_values(self.listing0)[SOURCE_K]

    @property
    def breakeven(self) -> int:
        """The Breakeven_Price: the entry fill (the limit) moved ``offset_ticks`` in favor."""
        return self.entry + self.sign * self.offset_ticks

    def config(self) -> PlannerConfig:
        return PlannerConfig(
            orders=OrdersConfig.model_validate(
                {
                    "max_age_min": self.max_age_min,
                    "cancel_triggers": dict(self.flags),
                    "invalidation": dict(self.actions),
                }
            ),
            exits=ExitsConfig.model_validate(
                {
                    "global": {"mode": "fixed_r"},
                    "breakeven": {
                        "enabled": self.breakeven_enabled,
                        "offset_ticks": self.offset_ticks,
                    },
                }
            ),
            gates=GatesConfig.model_validate(
                {
                    "stdev_fib_zone": {
                        "enabled": self.stdev_enabled,
                        "zones": [{"near": n, "far": f} for n, f in ZONES],
                    },
                    "opposition_inside_target": {
                        "fraction": float(self.fraction),
                        "window_r": float(self.window_r),
                    },
                }
            ),
        )


def first_values(listing: Listing) -> dict[int, Decimal]:
    out: dict[int, Decimal] = {}
    for k, v in listing:
        out.setdefault(k, v)
    return out


def view_at(t: int, listing: Listing, offset: int, priced: bool) -> SourceView:
    """The SPX gamma Snapshot at ``t`` with its labels and an offset conversion (or none)."""
    strikes = tuple(k / 4 for k, _ in listing)
    snapshot = Snapshot(
        symbol="SPX", metric="gamma", view_id="view-p41", as_of_ns=t, as_of_raw="raw",
        spot=SPOT, previous_close=None, strikes=strikes,
        values=tuple(float(v) for _, v in listing), node_types=None,
        expirations=("2026-03-05",), resolution="1s", source_endpoint="range", extra_json="{}",
    )  # fmt: skip
    labels = classify(snapshot, NODE_PARAMS)
    if not priced:
        return SourceView(snapshot, labels, MissingPrice("SPX"))
    conv = Conversion(
        symbol="SPX", family="ES", instrument="MES", contract="MESH6", method="offset",
        factor=offset / 4, futures_close=SPOT + offset / 4, spot=SPOT, as_of_ns=t,
        paired_bar_close_ns=t,
    )  # fmt: skip
    levels = tuple(conv.level(k) for k in strikes)
    cm = ConvertedMap(
        symbol="SPX", metric="gamma", as_of_ns=t, conversion=conv,
        band_half_width_pts=HALF_WIDTH_PTS, strikes=strikes, levels=levels,
        bands=tuple(band(lv, HALF_WIDTH_PTS) for lv in levels),
    )  # fmt: skip
    return SourceView(snapshot, labels, cm)


def context(
    t: int, view: SourceView | None, legs: tuple[BosLeg, ...] | None, last_close: int | None
) -> PlannerContext:
    return PlannerContext(
        t=t,
        session=SESSION,
        flatten_at=FLATTEN_AT,
        sources={} if view is None else {("SPX", "gamma"): view},
        legs={} if legs is None else {"MES": legs},
        last_close={} if last_close is None else {"MES": last_close},
    )


def leg(terminal: float, span: float, n: int) -> BosLeg:
    """A bearish BOS_Leg: origin ``span`` points above ``terminal``."""
    close = T0 - (n + 1) * NS_PER_MINUTE
    return BosLeg(
        instrument="MES", timeframe_s=60, direction="bearish", swing_price=terminal + span,
        swing_open_ns=close - 10 * NS_PER_MINUTE, break_open_ns=close - NS_PER_MINUTE,
        break_close_ns=close, origin=terminal + span, terminal=terminal,
    )  # fmt: skip


def setup_of(sc: Scenario) -> CandidateSetup:
    level0 = SOURCE_K + sc.offset0
    key = SetupKey("MES", "floor_ceiling_bounce", SOURCE_STRIKE, sc.direction, SESSION, 1)
    source = SourceNodeRef(
        "SPX", "gamma", SOURCE_STRIKE, float(sc.source_value), level0, band(level0, HALF_WIDTH_PTS)
    )
    inputs = SetupInputs(
        map_as_of=(SnapshotAsOf("SPX", "gamma", T0),), source_spot=SPOT,
        futures_price=sc.entry, conversion_method="offset", conversion_factor=sc.offset0 / 4,
        band_half_width_pts=HALF_WIDTH_PTS, regime="Negative_Gamma", map_grade="Neutral_Map",
        stop_rule="one_node_beyond",
    )  # fmt: skip
    sign = sc.sign
    return CandidateSetup(
        key, "floor_ceiling_bounce", T0, sc.entry, sc.entry - sign * sc.risk,
        (sc.entry + sign * 3 * sc.risk,), "fixed_r", source, inputs,
    )  # fmt: skip


def accepted(c: CandidateSetup, contracts: int) -> Accepted:
    steps = tuple(StepOutcome(s, contracts, s == "base_contracts") for s in SIZING_STEPS)
    return Accepted(SizedSetup(c, contracts, steps), GateEvaluation(c.key, c.t, (), (), "A_Plus"))


def filled(book: OrderBook, sc: Scenario, cfg: PlannerConfig, close: int) -> OrderBook:
    """The entry fills in full at its limit on the bar opening at T0, which closes at ``close``."""
    (plan,) = book.plans
    e = sc.entry
    hi, lo = max(e, close), min(e, close)
    b = Bar(
        instrument="MES", contract="MESH6", interval_s=60, open_ns=T0,
        close_ns=T0 + 60 * NS_PER_SECOND, o=e / 4, h=hi / 4, l=lo / 4, c=close / 4, v=100.0,
        o_t=e, h_t=hi, l_t=lo, c_t=close, source="atlas",
    )  # fmt: skip
    f = OrderFill(plan.entry, Fill(plan.entry.client_id, T0, e, sc.contracts, Decimal("0.74")))
    out, _, _ = on_bar(book, b, [f], cfg)
    (opened,) = out.plans
    assert (opened.status, opened.open_qty, opened.entry_price) == ("open", sc.contracts, e)
    return out


# ---------------------------------------------------------------- generators


def decimals(lo: int, hi: int, places: int) -> st.SearchStrategy[Decimal]:
    """Decimals from ``lo`` to ``hi`` units of ``10 ** -places``, written with ``places`` digits."""
    return st.integers(lo, hi).map(lambda n: Decimal(n).scaleb(-places))


def signed(sign: int, magnitude: Decimal) -> Decimal:
    return sign * magnitude


def signed_offset(sign: int, ticks: int) -> int:
    return sign * ticks


SIGNS: Final = st.sampled_from((1, -1))
MAGNITUDES: Final = st.one_of(
    st.sampled_from(
        tuple(Decimal(x) for x in ("0", "1e8", "5e8", "1e9", "2e9", "2.5e9", "3e9", "4e9"))
    ),
    st.builds(lambda n, e: Decimal(n).scaleb(e), st.integers(1, 99), st.integers(6, 8)),
)
VALUES: Final = st.builds(signed, SIGNS, MAGNITUDES)
# At least 2.5e9 against at most 9.9e9 elsewhere: the source is a Node at T0.
SOURCE_MAGNITUDES: Final = tuple(Decimal(x) for x in ("2.5e9", "3e9", "4e9"))
SOURCE_MOVES: Final = ("keep", "keep", "negate", "shrink", "zero", "drop", "redraw")
OFFSETS: Final = st.integers(-8, 8)
NEW_STRIKES: Final = st.builds(lambda s, n: SOURCE_K + s * n, SIGNS, st.integers(1, 160))
NEAR_DISTANCES: Final = (0.0, 1.0, 2.5, 5.0, 5.25)  # points from entry to the leg's nearest zone
SPANS: Final = st.sampled_from((1.0, 2.0, 4.0))
GAPS: Final = st.one_of(
    st.integers(1, 12).map(lambda m: m * NS_PER_MINUTE),
    st.integers(60, 900).map(lambda s: s * NS_PER_SECOND),
)


@dataclass(frozen=True, slots=True)
class Frame:
    """What the Decision_Time generator reads from the placed setup."""

    entry: int
    sign: int
    risk: int
    offset0: int
    base: Listing  # the Snapshot at the setup's Decision_Time
    source_value: Decimal
    window: Fraction  # window_r x risk, in ticks
    threshold: Decimal  # fraction x |source value|
    breakeven: int
    pool: tuple[BosLeg, ...]


def draw_listing(draw: st.DrawFn, f: Frame, offset: int) -> Listing:
    """A Snapshot changed from the setup's, read with conversion ``offset``."""
    values: dict[int, Decimal] = {}
    for k, v in f.base:
        if k == SOURCE_K:
            continue
        roll = draw(st.integers(0, 9))
        if roll:  # 1 in 10 dropped, else kept or redrawn
            values[k] = v if roll < 6 else draw(VALUES)
    match draw(st.sampled_from(SOURCE_MOVES)):
        case "keep":
            values[SOURCE_K] = f.source_value
        case "negate":
            values[SOURCE_K] = -f.source_value
        case "shrink":
            values[SOURCE_K] = f.source_value / 100
        case "zero":
            values[SOURCE_K] = Decimal(0)
        case "redraw":
            values[SOURCE_K] = draw(VALUES)
        case _:  # drop: the source strike is absent
            pass
    for _ in range(draw(st.integers(0, 2))):
        values[draw(NEW_STRIKES)] = draw(VALUES)
    if draw(st.integers(0, 3)) == 0:  # a new King
        others = sorted(k for k in values if k != SOURCE_K)
        existing = bool(others) and draw(st.booleans())
        k = draw(st.sampled_from(others)) if existing else draw(NEW_STRIKES)
        values[k] = draw(SIGNS) * KING_PEAK
    if draw(st.integers(0, 2)) == 0:  # a Node on, inside or just past the window's edges
        reach = math.floor(f.window)
        d = draw(st.sampled_from((reach, reach + 1, 1, 0)) | st.integers(1, max(1, reach)))
        k = f.entry + f.sign * d - offset  # its converted level is entry + sign x d
        if k != SOURCE_K:
            just_below = f.threshold - Decimal(1).scaleb(f.threshold.adjusted() - 6)
            magnitude = draw(st.sampled_from((f.threshold, just_below)) | MAGNITUDES)
            values[k] = draw(SIGNS) * magnitude
    return tuple(draw(st.permutations(list(values.items()))))


def draw_step(draw: st.DrawFn, f: Frame) -> Step:
    gap = draw(GAPS)
    offset = draw(st.just(f.offset0) | OFFSETS)
    listing = draw_listing(draw, f, offset) if draw(st.integers(0, 9)) else None
    priced = draw(st.integers(0, 9)) > 0
    legs: tuple[BosLeg, ...] | None = None
    if draw(st.integers(0, 4)):
        legs = tuple(draw(st.lists(st.sampled_from(f.pool), unique=True))) if f.pool else ()
    be, sign = f.breakeven, f.sign
    last_close = draw(
        st.none()
        | st.just(be)
        | st.integers(-3, 3).map(lambda d: be + sign * d)
        | st.integers(f.entry - 2 * f.risk, f.entry + 2 * f.risk)
    )
    return Step(gap, listing, offset, priced, legs, last_close)


@st.composite
def scenarios(draw: st.DrawFn) -> Scenario:
    # The setup's prices.
    direction = draw(st.sampled_from(DIRECTIONS))
    sign = 1 if direction == "long" else -1
    risk = draw(st.one_of(st.integers(1, 8), st.integers(9, 200)))
    offset0 = draw(OFFSETS)
    entry = SOURCE_K + offset0 + draw(st.integers(-8, 8))
    contracts = draw(st.integers(1, 3))

    # The source Snapshot at the setup's Decision_Time.
    source_value = signed(draw(SIGNS), draw(st.sampled_from(SOURCE_MAGNITUDES)))
    others = draw(
        st.lists(
            st.builds(signed_offset, SIGNS, st.integers(1, 120)),
            min_size=1,
            max_size=7,
            unique=True,
        )
    )
    items = [(SOURCE_K, source_value), *((SOURCE_K + o, draw(VALUES)) for o in others)]
    listing0 = tuple(draw(st.permutations(items)))

    # BOS_Legs: at most one near entry, the others far past the band half-width.
    stdev_enabled = draw(st.booleans())
    entry_pts = entry / 4
    pool: list[BosLeg] = []
    near: BosLeg | None = None
    near_distance = draw(st.none() | st.sampled_from(NEAR_DISTANCES))
    if near_distance is not None:
        span = draw(SPANS)
        # Entry inside the first zone, or near_distance above its top (the second is farther).
        terminal = (
            entry_pts + 2.25 * span if near_distance == 0 else entry_pts - near_distance + 2 * span
        )
        near = leg(terminal, span, 0)
        pool.append(near)
    for j in range(draw(st.integers(0, 2))):
        pool.append(leg(entry_pts + 60 + 10 * j, draw(SPANS), j + 1))
    legs0 = tuple(draw(st.permutations(pool))) if draw(st.integers(0, 4)) else None
    used = (
        near
        if stdev_enabled
        and legs0 is not None
        and near_distance is not None
        and near_distance <= HALF_WIDTH_PTS
        else None
    )

    # The orders, exits and Gate settings the triggers read.
    fraction = draw(st.one_of(st.just(Decimal("0.85")), decimals(1, 100, 2)))
    window_r = draw(st.one_of(st.just(Decimal("3.0")), decimals(1, 200, 1), decimals(1, 2000, 2)))
    flags = {trig: draw(st.integers(0, 3)) > 0 for trig in CANCEL_TRIGGERS}
    actions = {trig: draw(st.sampled_from(ACTIONS)) for trig in CANCEL_TRIGGERS}
    max_age = draw(st.none() | st.integers(1, 20) | st.integers(21, 390))
    offset_ticks = draw(st.one_of(st.integers(0, 4), st.integers(5, 20)))
    breakeven_enabled = draw(st.booleans())

    # Resting, or filled in full with a close strictly inside the stop.
    opened = draw(st.booleans())
    fill_close = entry + sign * draw(st.integers(-(risk - 1), 2 * risk)) if opened else None

    frame = Frame(
        entry=entry,
        sign=sign,
        risk=risk,
        offset0=offset0,
        base=listing0,
        source_value=source_value,
        window=Fraction(window_r) * risk,
        threshold=fraction * abs(source_value),
        breakeven=entry + sign * offset_ticks,
        pool=tuple(pool),
    )
    steps = tuple(draw_step(draw, frame) for _ in range(draw(st.integers(1, 6))))
    return Scenario(
        direction=direction,
        entry=entry,
        risk=risk,
        contracts=contracts,
        fill_close=fill_close,
        listing0=listing0,
        offset0=offset0,
        legs0=legs0,
        used_leg=used,
        fraction=fraction,
        window_r=window_r,
        flags=flags,
        actions=actions,
        max_age_min=max_age,
        stdev_enabled=stdev_enabled,
        breakeven_enabled=breakeven_enabled,
        offset_ticks=offset_ticks,
        steps=steps,
    )


# ---------------------------------------------------------------- reference model


def fires(
    sc: Scenario, step: Step, labels: NodeLabels | None, king0: float | None
) -> dict[CancelTrigger, bool]:
    """Which Cancel_Triggers ``step`` shows, against the values at the setup's Decision_Time."""
    out: dict[CancelTrigger, bool] = dict.fromkeys(CANCEL_TRIGGERS, False)
    used = sc.used_leg
    out["stdev_leg_dropped"] = used is not None and step.legs is not None and used not in step.legs
    if step.listing is None or labels is None:
        return out
    nodes = set(labels.nodes)
    now = first_values(step.listing)
    then = sc.source_value
    king = labels.king
    out["king_flip"] = king is not None and king0 is not None and king != king0
    out["source_node_gone"] = SOURCE_STRIKE not in nodes
    value = now.get(SOURCE_K)
    out["sign_flip"] = value is not None and (value > 0 > then or value < 0 < then)
    if step.priced:
        min_abs = Fraction(sc.fraction) * abs(Fraction(then))
        window = Fraction(sc.window_r) * sc.risk
        beyond = {
            sc.sign * (k + step.offset - sc.entry)
            for k, v in now.items()
            if k != SOURCE_K and k / 4 in nodes and abs(Fraction(v)) >= min_abs
        }
        out["opposition_in_target"] = any(0 < d <= window for d in beyond)
        if window in beyond:
            event("opposition Node on the window edge")
    return out


def check_resting(
    sc: Scenario,
    plan: Plan,
    t: int,
    fired: Mapping[CancelTrigger, bool],
    out: tuple[OrderBook, list[OrderIntent], list[PlannerEvent]],
    original: tuple[int | None, int | None, tuple[int | None, ...]],
) -> str:
    book, intents, events = out
    causes: list[CancelCause] = [trig for trig in CANCEL_TRIGGERS if sc.flags[trig] and fired[trig]]
    if sc.max_age_min is not None and t - T0 >= sc.max_age_min * NS_PER_MINUTE:
        causes.append("max_age")
    if causes:  # Req 12.19, 12.23
        assert events == [EntryCancelled(plan.key, t, tuple(causes))]
        assert intents == [CancelOrder(plan.entry.client_id, t)]
        assert book.plans == ()
        assert plan.key in book.placed
        if sc.max_age_min is not None and t - T0 == sc.max_age_min * NS_PER_MINUTE:
            event("resting: max age reached exactly")
        return f"resting: cancelled{' (max_age)' if 'max_age' in causes else ''}"
    # Req 12.20: kept working at the original prices.
    assert (intents, events) == ([], [])
    assert book.plans == (plan,)
    kept = book.plans[0]
    assert (kept.entry.price, kept.stop.price, tuple(o.price for o in kept.targets)) == original
    return "resting: kept"


def check_open(
    sc: Scenario,
    plan: Plan,
    step: Step,
    t: int,
    fired: Mapping[CancelTrigger, bool],
    out: tuple[OrderBook, list[OrderIntent], list[PlannerEvent]],
) -> str:
    book, intents, events = out
    triggers = tuple(
        trig for trig in CANCEL_TRIGGERS if sc.actions[trig] is not None and fired[trig]
    )
    if not triggers:
        assert (book.plans, intents, events) == ((plan,), [], [])
        return "open: no trigger"
    # Req 12.21: the strongest configured action of the fired triggers.
    configured = {sc.actions[trig] for trig in triggers}
    chosen = next(a for a in STRONGEST_FIRST if a in configured)
    close = step.last_close
    be = sc.breakeven
    past = close is not None and sc.sign * (close - be) <= 0
    if chosen == "breakeven" and close == be:
        event("open: breakeven with the close on the Breakeven_Price")
    applied: InvalidationAction = "exit_market" if chosen == "breakeven" and past else chosen
    assert [e for e in events if isinstance(e, Invalidated)] == [
        Invalidated(plan.key, t, triggers, chosen, applied)
    ]
    moves = [e for e in events if isinstance(e, StopMoved)]
    assert len(events) == 1 + len(moves)
    (after,) = book.plans
    old = plan.stop_price
    if applied == "exit_market":  # Req 12.21, and 12.22 for a breakeven action
        (intent,) = intents
        assert isinstance(intent, SubmitExit)
        o = intent.order
        assert (o.setup_key, o.instrument, o.side, o.kind, o.qty, o.price, o.placed_at, o.role) == (
            plan.key, "MES", EXIT_SIDE[sc.direction], "market", plan.open_qty, None, t, "exit",
        )  # fmt: skip
        assert after.exit_pending
        assert (after.stop_price, moves) == (old, [])
    elif applied == "breakeven":
        new = max(old, be) if sc.sign > 0 else min(old, be)  # never against the trade
        if new == old:
            assert (intents, moves) == ([], [])
        else:
            assert intents == [ModifyOrder(plan.stop.client_id, new, t)]
            assert moves == [StopMoved(plan.key, t, old, new, ("invalidation_breakeven",))]
        assert after.stop_price == new
        assert not after.exit_pending
    else:  # hold: record only
        assert (intents, moves) == ([], [])
        assert after == plan
    return f"open: {chosen} -> {applied}"


# ---------------------------------------------------------------- explicit examples


def king_flip_breakeven(close: int) -> Scenario:
    """A filled long from 23120 (1R = 20) whose King flips with a breakeven action.

    The Breakeven_Price is 23121: a close on it exits at market (Req 12.22), a
    close a tick better moves the stop to it.
    """
    base: Listing = (
        (23_040, Decimal("1e9")),
        (23_080, Decimal("-2.2e9")),
        (SOURCE_K, Decimal("2.5e9")),
        (23_160, Decimal("1e9")),
        (23_300, Decimal("3e9")),  # King
    )
    flipped: Listing = ((23_040, Decimal("4e9")), *base[1:])
    return Scenario(
        direction="long",
        entry=SOURCE_K,
        risk=20,
        contracts=2,
        fill_close=23_124,
        listing0=base,
        offset0=0,
        legs0=None,
        used_leg=None,
        fraction=Decimal("0.85"),
        window_r=Decimal("3.0"),
        flags=dict.fromkeys(CANCEL_TRIGGERS, True),
        actions={**dict.fromkeys(CANCEL_TRIGGERS, None), "king_flip": "breakeven"},
        max_age_min=None,
        stdev_enabled=False,
        breakeven_enabled=False,
        offset_ticks=1,
        steps=(Step(NS_PER_MINUTE, flipped, 0, True, None, close),),
    )


# ---------------------------------------------------------------- property


# Feature: skylit-futures-strategy-engine, Property 41: Cancel and invalidation triggers
@example(sc=king_flip_breakeven(23_121))
@example(sc=king_flip_breakeven(23_122))
@given(sc=scenarios())
def test_cancel_and_invalidation_triggers(sc: Scenario) -> None:
    cfg = sc.config()
    view0 = view_at(T0, sc.listing0, sc.offset0, priced=True)
    king0 = view0.labels.king
    c = setup_of(sc)
    book, intents, rejections = place(
        OrderBook(), [accepted(c, sc.contracts)], context(T0, view0, sc.legs0, None), cfg
    )
    assert rejections == []
    (bracket,) = intents
    assert isinstance(bracket, PlaceBracket)
    assert book.plans[0].reference.leg == sc.used_leg
    original = (bracket.entry.price, bracket.stop.price, tuple(o.price for o in bracket.targets))
    if sc.fill_close is not None:
        book = filled(book, sc, cfg, sc.fill_close)

    t = T0
    for step in sc.steps:
        t += step.gap_ns
        view = None if step.listing is None else view_at(t, step.listing, step.offset, step.priced)
        before = book
        out = manage(book, context(t, view, step.legs, step.last_close), cfg)
        book = out[0]
        fired = fires(sc, step, None if view is None else view.labels, king0)
        for trig in CANCEL_TRIGGERS:
            if fired[trig]:
                event(f"shows {trig}")
        if not before.plans:  # cancelled at an earlier Decision_Time
            assert (out[0].plans, out[1], out[2]) == ((), [], [])
            continue
        (plan,) = before.plans
        if plan.status == "resting":
            event(check_resting(sc, plan, t, fired, out, original))
        elif plan.exit_pending:  # a market exit was sent: nothing more
            assert (out[0].plans, out[1], out[2]) == (before.plans, [], [])
        else:
            event(check_open(sc, plan, step, t, fired, out))
