"""Property 38: Stops only tighten.

*For any* open position and any sequence of closed bars and stop updates
(breakeven trigger, TP1 fill, node-to-node or fixed-ticks trailing,
invalidation breakeven), a long's stop never decreases and a short's stop
never increases, and each applied move lands at the level its rule specifies
(Breakeven_Price, the next Node back toward entry, or the trail distance
behind the most favorable price).

The test places one A_Plus setup with :func:`place`, fills its entry in full
on the first bar, then drives :func:`on_bar` over a random 1-minute bar stream
and, at chosen bar closes, :func:`manage` with a King-flip invalidation set to
breakeven. A small fill model in the test plays the Fill_Simulator: a bar uses
the latest stop version effective at its open; a market exit fills at the
next bar's open; a bar that reaches the stop fills only the stop, for every
open contract; otherwise each target the bar reaches fills; nothing but the
entry fills on the entry fill bar, which never reaches the stop.

The reference model reads Requirement 12 criteria 8, 10, 11, 13, 14, 21 and
22 and the Breakeven_Price glossary entry directly, in whole ticks with exact
rationals for the decimals a Strategy_Config carries:

- for an open position "entry" is the entry fill price F; 1R is the distance
  from F to the initial stop; Breakeven_Price is F plus the offset in the
  trade's favor;
- the most favorable price since the entry fill: on the fill bar only F and
  the close are known to trade after the fill; from the next bar on, each
  bar's high (long) or low (short) counts too;
- breakeven (12.13): once the favorable move from F reaches trigger_r x 1R,
  Breakeven_Price;
- TP1 (12.8): from the close of the bar on which TP1 fills with contracts left,
  Breakeven_Price;
- node-to-node (12.10): among the Nodes strictly beyond F, the farthest one the
  favorable price has reached; the stop goes to the nearest Node strictly
  between F and it, or to the source Node's level when there is none;
- fixed ticks (12.11): ``ticks`` behind the favorable price, at every bar
  close from the fill bar on;
- invalidation breakeven (12.21-12.22): a King flip at a Decision_Time moves
  the stop to Breakeven_Price, or exits at market when the latest close is at
  or past it against the trade;
- 12.14: the stop after an update is the tightest of the current stop and
  every level a rule gives, so a long's never decreases, a short's never
  increases, and an update that would loosen it leaves it unchanged.

Every move must be one ``ModifyOrder`` of the stop order stamped at the bar's
close (the Decision_Time for :func:`manage`), the next bar's open, so it is
effective from the next bar, with one ``StopMoved`` naming the rules that gave
the new level. No other update may change the stop.

Generators: a long or short setup with 2 to 80 ticks of risk, an entry limit
0 to 8 ticks in the trade's favor from the source Node (so the source never
lies beyond the entry), a fill up to 2 ticks either side of the limit, 1 to 5
contracts, and up to 8 other Nodes on either side of entry, some exactly on
or a tick beyond the fill. Every strike's absolute value is at least a third
of the King's, so every strike is a Node. The Exit_Mode is Fixed_R,
TP1_Partial_BE (R-based TP1 and TP2), or Trailing in node-to-node or
fixed-ticks mode; the breakeven rule is on or off with any trigger and
offset. Up to 30 bars follow the fill bar, each with an optional Decision_Time
that shows the same map or a King flip. Three explicit examples pin a
breakeven exactly at 1R, a refused node-to-node loosening for a short, and a
TP1 fill.

**Validates: Requirements 12.8, 12.10, 12.11, 12.13, 12.14**
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date, time
from decimal import Decimal
from fractions import Fraction
from itertools import pairwise
from typing import Final, Literal

from hypothesis import event, example, given
from hypothesis import strategies as st

from fse.config.schema.exits import ExitModeName, ExitsConfig
from fse.config.schema.nodes import NodesConfig
from fse.config.schema.orders import CANCEL_TRIGGERS, OrdersConfig
from fse.engine.gates.registry import GateEvaluation
from fse.engine.levels import TICKS_PER_POINT, Conversion, ConvertedMap, band
from fse.engine.nodes import NodeParams, classify
from fse.engine.planner import (
    Accepted,
    ModifyOrder,
    OrderBook,
    OrderFill,
    OrderIntent,
    PlaceBracket,
    PlannerConfig,
    PlannerContext,
    PlannerEvent,
    SourceView,
    StopMoved,
    StopRuleName,
    SubmitExit,
    flatten_time,
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
    Order,
    SetupInputs,
    SetupKey,
    Snapshot,
    SnapshotAsOf,
    SourceNodeRef,
)
from fse.timekit import NS_PER_MINUTE, SessionCalendar, ny_instant

type Mode = Literal["fixed_r", "tp1_partial_be", "trail_node", "trail_ticks"]
type Decide = Literal["none", "steady", "flip"]
"""A Decision_Time at a bar's close: none, the placement map again, or a King flip."""

DIRECTIONS: Final[tuple[Direction, ...]] = ("long", "short")
MODES: Final[tuple[Mode, ...]] = ("fixed_r", "tp1_partial_be", "trail_node", "trail_ticks")
EXIT_MODE: Final[dict[Mode, ExitModeName]] = {
    "fixed_r": "fixed_r",
    "tp1_partial_be": "tp1_partial_be",
    "trail_node": "trailing",
    "trail_ticks": "trailing",
}
DECIDES: Final[tuple[Decide, ...]] = ("none", "none", "none", "steady", "flip")

SESSION: Final = date(2026, 3, 5)
T0: Final = ny_instant(SESSION, time(10, 0))
CAL: Final = SessionCalendar(date(2026, 1, 1), date(2026, 12, 31))
FLATTEN: Final = flatten_time(OrdersConfig(), CAL, SESSION)
SOURCE_LEVEL: Final = 23120  # ticks: SPX 5780 under an offset-0 conversion
BAND_PTS: Final = 5.0
VALUE_UNIT: Final = 1.0e8
FLIP_KING_UNITS: Final = 40  # 4e9: above every generated |value| (at most 3e9)
FLIP_KING_BEHIND: Final = 1000  # ticks behind the source Node: far from every price
FEE: Final = Decimal("0.74")
NODE_PARAMS: Final = NodeParams.from_config(NodesConfig())


# ---------------------------------------------------------------- scenario


@dataclass(frozen=True, slots=True)
class FirstBar:
    """The entry fill bar, in ticks in the trade's favor from the fill (negative: against)."""

    open: int
    favorable: int  # the bar's favorable extreme, at least max(0, open, close)
    adverse: int  # the bar's adverse extreme, at most min(0, open, close)
    close: int


@dataclass(frozen=True, slots=True)
class Move:
    """One bar after the fill bar, in ticks in the trade's favor (negative: against)."""

    gap: int  # open minus the previous close
    body: int  # close minus open
    up: int  # favorable extreme beyond the body
    down: int  # adverse extreme beyond the body
    decide: Decide = "none"


@dataclass(frozen=True, slots=True)
class Case:
    """One placed setup, its Map_State, its config and the bars after placement.

    Prices are whole ticks. ``nodes`` holds ``(d, units)`` for each Node other
    than the source: its level is ``d`` ticks in the trade direction from the
    source level, its value ``units`` x 1e8. Decimals are as written in the
    Strategy_Config.
    """

    direction: Direction
    mode: Mode
    risk: int  # |entry limit - initial stop|
    entry_offset: int  # entry limit minus the source level, in the trade direction
    fill_offset: int  # entry fill minus the entry limit, in the trade direction
    qty: int
    source_units: int
    nodes: tuple[tuple[int, int], ...]
    first: FirstBar
    moves: tuple[Move, ...]
    breakeven: bool = True
    trigger_r: Decimal = Decimal("1.0")
    offset_ticks: int = 1
    fixed_r: Decimal = Decimal("3.0")
    tp1_r: Decimal = Decimal("1.5")
    tp2_r: Decimal = Decimal("3.0")
    tp1_fraction: Decimal = Decimal("0.5")
    trail_ticks: int = 40

    @property
    def sign(self) -> int:
        return 1 if self.direction == "long" else -1

    @property
    def limit(self) -> int:
        return SOURCE_LEVEL + self.sign * self.entry_offset

    @property
    def fill(self) -> int:
        return self.limit + self.sign * self.fill_offset

    @property
    def stop(self) -> int:
        """The initial stop."""
        return self.limit - self.sign * self.risk

    @property
    def node_levels(self) -> tuple[int, ...]:
        return tuple(SOURCE_LEVEL + self.sign * d for d, _ in self.nodes)

    @property
    def breakeven_price(self) -> int:
        return self.fill + self.sign * self.offset_ticks

    @property
    def trigger_ticks(self) -> Fraction:
        """trigger_r x 1R, 1R being the distance from the entry fill to the initial stop."""
        return Fraction(self.trigger_r) * (self.sign * (self.fill - self.stop))


@dataclass(frozen=True, slots=True)
class Ohlc:
    open: int
    high: int
    low: int
    close: int

    def favorable(self, sign: int) -> int:
        return self.high if sign > 0 else self.low

    def adverse(self, sign: int) -> int:
        return self.low if sign > 0 else self.high


def stream(case: Case) -> list[tuple[Ohlc, Decide]]:
    """The fill bar, then one bar per move, with the Decision_Time at each close."""
    s, fill = case.sign, case.fill

    def ohlc(o: int, fav: int, adv: int, c: int) -> Ohlc:
        a, b = fill + s * fav, fill + s * adv
        return Ohlc(fill + s * o, max(a, b), min(a, b), fill + s * c)

    f = case.first
    out: list[tuple[Ohlc, Decide]] = [(ohlc(f.open, f.favorable, f.adverse, f.close), "none")]
    close = f.close
    for m in case.moves:
        o = close + m.gap
        c = o + m.body
        out.append((ohlc(o, max(o, c) + m.up, min(o, c) - m.down, c), m.decide))
        close = c
    return out


def to_bar(k: int, p: Ohlc) -> Bar:
    open_ns = T0 + k * NS_PER_MINUTE
    return Bar(
        instrument="MES", contract="MESH6", interval_s=60, open_ns=open_ns,
        close_ns=open_ns + NS_PER_MINUTE, o=p.open / 4, h=p.high / 4, l=p.low / 4,
        c=p.close / 4, v=100.0, o_t=p.open, h_t=p.high, l_t=p.low, c_t=p.close, source="atlas",
    )  # fmt: skip


# ---------------------------------------------------------------- planner inputs


def planner_config(case: Case) -> PlannerConfig:
    exits = ExitsConfig.model_validate(
        {
            "global": {"mode": EXIT_MODE[case.mode], "stop_rule": "fixed_ticks"},
            "modes": {
                "fixed_r": {"r_multiple": float(case.fixed_r)},
                "tp1_partial_be": {
                    "enabled": True,
                    "tp1": {"rule": "r", "r_multiple": float(case.tp1_r)},
                    "tp2": {"rule": "r", "r_multiple": float(case.tp2_r)},
                    "tp1_fraction": float(case.tp1_fraction),
                },
                "trailing": {
                    "enabled": True,
                    "mode": "fixed_ticks" if case.mode == "trail_ticks" else "node_to_node",
                    "ticks": case.trail_ticks,
                },
            },
            "breakeven": {
                "enabled": case.breakeven,
                "trigger_r": float(case.trigger_r),
                "offset_ticks": case.offset_ticks,
            },
        }
    )
    # Only a King flip acts on an open position, and its action is breakeven.
    invalidation = {t: "breakeven" if t == "king_flip" else None for t in CANCEL_TRIGGERS}
    orders = OrdersConfig.model_validate({"invalidation": invalidation})
    return PlannerConfig(orders=orders, exits=exits)


def source_view(case: Case, as_of: int, *, flip: bool) -> SourceView:
    """The SPX gamma Map at ``as_of``; ``flip`` adds a far strike that becomes King."""
    s = case.sign
    pairs = [(SOURCE_LEVEL, case.source_units), *((SOURCE_LEVEL + s * d, u) for d, u in case.nodes)]
    if flip:
        pairs.append((SOURCE_LEVEL - s * FLIP_KING_BEHIND, FLIP_KING_UNITS))
    strikes = tuple(level / TICKS_PER_POINT for level, _ in pairs)
    snapshot = Snapshot(
        symbol="SPX", metric="gamma", view_id="view-p38", as_of_ns=as_of, as_of_raw="raw",
        spot=SOURCE_LEVEL / TICKS_PER_POINT, previous_close=None, strikes=strikes,
        values=tuple(units * VALUE_UNIT for _, units in pairs), node_types=None,
        expirations=("2026-03-05",), resolution="1s", source_endpoint="range", extra_json="{}",
    )  # fmt: skip
    conversion = Conversion(
        symbol="SPX", family="ES", instrument="MES", contract="MESH6", method="offset",
        factor=0.0, futures_close=snapshot.spot, spot=snapshot.spot, as_of_ns=as_of,
        paired_bar_close_ns=as_of,
    )  # fmt: skip
    levels = tuple(conversion.level(k) for k in strikes)
    assert levels == tuple(level for level, _ in pairs)  # offset 0: a strike's level is itself
    converted = ConvertedMap(
        symbol="SPX", metric="gamma", as_of_ns=as_of, conversion=conversion,
        band_half_width_pts=BAND_PTS, strikes=strikes, levels=levels,
        bands=tuple(band(level, BAND_PTS) for level in levels),
    )  # fmt: skip
    return SourceView(snapshot, classify(snapshot, NODE_PARAMS), converted)


def candidate(case: Case) -> CandidateSetup:
    s = case.sign
    strike = SOURCE_LEVEL / TICKS_PER_POINT
    key = SetupKey("MES", "floor_ceiling_bounce", strike, case.direction, SESSION, 1)
    source = SourceNodeRef(
        "SPX", "gamma", strike, case.source_units * VALUE_UNIT, SOURCE_LEVEL,
        band(SOURCE_LEVEL, BAND_PTS),
    )  # fmt: skip
    inputs = SetupInputs(
        map_as_of=(SnapshotAsOf("SPX", "gamma", T0),), source_spot=strike,
        futures_price=case.limit, conversion_method="offset", conversion_factor=0.0,
        band_half_width_pts=BAND_PTS, regime="Negative_Gamma", map_grade="Neutral_Map",
        stop_rule="fixed_ticks",
    )  # fmt: skip
    return CandidateSetup(
        key, "floor_ceiling_bounce", T0, case.limit, case.stop,
        (case.limit + s * 3 * case.risk,), EXIT_MODE[case.mode], source, inputs,
    )  # fmt: skip


def accepted(c: CandidateSetup, contracts: int) -> Accepted:
    steps = tuple(StepOutcome(s, contracts, s == "base_contracts") for s in SIZING_STEPS)
    return Accepted(SizedSetup(c, contracts, steps), GateEvaluation(c.key, c.t, (), (), "A_Plus"))


def context(t: int, view: SourceView, last_close: int | None = None) -> PlannerContext:
    return PlannerContext(
        t=t,
        session=SESSION,
        flatten_at=FLATTEN,
        sources={("SPX", "gamma"): view},
        last_close={} if last_close is None else {"MES": last_close},
    )


# ---------------------------------------------------------------- reference model


def node_trail(case: Case, favorable: int) -> int | None:
    """Req 12.10: the stop level the farthest Node reached beyond the fill gives, if any."""
    s = case.sign
    beyond = [level for level in case.node_levels if s * (level - case.fill) > 0]
    reached = [level for level in beyond if s * (favorable - level) >= 0]
    if not reached:
        return None
    farthest = max(reached, key=lambda level: s * level)
    nearer = [level for level in beyond if s * (level - farthest) < 0]
    return max(nearer, key=lambda level: s * level) if nearer else SOURCE_LEVEL


@dataclass(slots=True)
class Reference:
    """The stop Requirement 12 gives after each update, from the case alone."""

    case: Case
    stop: int
    favorable: int | None = None
    tp1_filled: bool = False
    tags: set[str] = field(default_factory=set)

    def apply(self, levels: Sequence[tuple[int, StopRuleName]]) -> tuple[int, frozenset[str]]:
        """Req 12.14: the tightest of the stop and ``levels``, and the rules that give it."""
        s = self.case.sign
        new = self.stop
        for level, _ in levels:
            if s * (level - new) > 0:
                new = level
        if any(s * (level - self.stop) < 0 for level, _ in levels):
            self.tags.add("a looser level was refused")
        rules: frozenset[str] = frozenset()
        if new != self.stop:
            rules = frozenset(r for level, r in levels if level == new)
        self.stop = new
        return new, rules

    def bar_close(self, p: Ohlc, fill_bar: bool) -> tuple[int, frozenset[str]]:
        """The stop after a bar on which the position stays open closes."""
        c, s = self.case, self.case.sign
        extreme = p.close if fill_bar else p.favorable(s)
        prev = c.fill if self.favorable is None else self.favorable
        fav = extreme if s * (extreme - prev) > 0 else prev
        self.favorable = fav
        levels: list[tuple[int, StopRuleName]] = []
        if c.breakeven and s * (fav - c.fill) >= c.trigger_ticks:
            levels.append((c.breakeven_price, "breakeven"))
        if c.mode == "tp1_partial_be" and self.tp1_filled:
            levels.append((c.breakeven_price, "tp1_breakeven"))
        if c.mode == "trail_node":
            level = node_trail(c, fav)
            if level is not None:
                levels.append((level, "trail_node"))
        if c.mode == "trail_ticks":
            levels.append((fav - s * c.trail_ticks, "trail_ticks"))
        return self.apply(levels)


# ---------------------------------------------------------------- driver


class Run:
    """The planner, a small fill model and the reference, stepped bar by bar."""

    def __init__(self, case: Case) -> None:
        self.case = case
        self.sign = case.sign
        self.cfg = planner_config(case)
        view = source_view(case, T0, flip=False)
        # Every strike is a Node (each |value| is at least a third of the King's).
        assert len(view.labels.nodes) == len(case.nodes) + 1
        setup = candidate(case)
        self.key = setup.key
        book, intents, rejections = place(
            OrderBook(), [accepted(setup, case.qty)], context(T0, view), self.cfg
        )
        assert rejections == []
        (bracket,) = intents
        assert isinstance(bracket, PlaceBracket)
        assert (bracket.entry.price, bracket.stop.price) == (case.limit, case.stop)
        self.book = book
        self.bracket = bracket
        self.targets = list(bracket.targets)
        self.versions: list[tuple[int, int]] = [(T0, case.stop)]  # (effective from, price)
        self.history = [case.stop]
        self.open_qty = 0
        self.exit_order: Order | None = None
        self.ref = Reference(case, case.stop)

    # -- fill model

    def stop_at(self, open_ns: int) -> int:
        """The latest stop version effective for a bar opening at ``open_ns``."""
        return [price for at, price in self.versions if at <= open_ns][-1]

    def fills(self, k: int, b: Bar, p: Ohlc) -> list[OrderFill]:
        s = self.sign
        if k == 0:  # the entry fills in full; the fill bar never reaches the stop
            e = self.bracket.entry
            return [OrderFill(e, Fill(e.client_id, b.open_ns, self.case.fill, e.qty, FEE))]
        if self.exit_order is not None:
            x = self.exit_order
            return [OrderFill(x, Fill(x.client_id, b.open_ns, p.open, self.open_qty, FEE))]
        stop = self.stop_at(b.open_ns)
        if s * (p.adverse(s) - stop) <= 0:
            price = p.open if s * (p.open - stop) <= 0 else stop
            order = replace(self.bracket.stop, price=stop)
            self.ref.tags.add("closed by the stop")
            return [OrderFill(order, Fill(order.client_id, b.open_ns, price, self.open_qty, FEE))]
        out: list[OrderFill] = []
        for t in list(self.targets):
            assert t.price is not None
            if s * (p.favorable(s) - t.price) >= 0:
                out.append(OrderFill(t, Fill(t.client_id, b.open_ns, t.price, t.qty, FEE)))
                self.targets.remove(t)
                self.ref.tags.add(f"{t.role} filled")
        return out

    # -- checks

    def check_update(
        self,
        intents: list[OrderIntent],
        events: list[PlannerEvent],
        old: int,
        new: int,
        rules: frozenset[str],
        at: int,
    ) -> None:
        """The planner moved the stop exactly as the reference did, effective from ``at``."""
        moves = [i for i in intents if isinstance(i, ModifyOrder)]
        moved = [e for e in events if isinstance(e, StopMoved)]
        plan = self.book.plan(self.key)
        assert plan is not None
        assert plan.stop_price == new, f"stop {plan.stop_price}, reference {new} (from {old})"
        if new == old:
            assert (moves, moved) == ([], []), "a stop update left the stop unchanged but was sent"
            return
        assert moves == [ModifyOrder(self.bracket.stop.client_id, new, at)]
        (e,) = moved
        assert (e.key, e.at, e.old, e.new) == (self.key, at, old, new)
        assert frozenset(e.rules) == rules, f"rules {e.rules}, reference {sorted(rules)}"
        self.versions.append((at, new))
        for rule in rules:
            self.ref.tags.add(f"moved by {rule}")

    def record(self) -> None:
        """Req 12.14: the stop never moves against the trade."""
        plan = self.book.plan(self.key)
        assert plan is not None
        self.history.append(plan.stop_price)
        assert self.sign * (self.history[-1] - self.history[-2]) >= 0, self.history

    # -- steps

    def bar(self, k: int, p: Ohlc) -> bool:
        """Bar ``k`` with its fills; whether the position is still open after it."""
        b = to_bar(k, p)
        fills = self.fills(k, b, p)
        self.book, intents, events = on_bar(self.book, b, fills, self.cfg)
        for f in fills:
            qty = f.fill.qty
            self.open_qty += qty if f.order.role == "entry" else -qty
            self.ref.tp1_filled = self.ref.tp1_filled or f.order.role == "tp1"
        if self.open_qty == 0:
            assert self.book.plan(self.key) is None
            assert (intents, events) == ([], [])
            return False
        old = self.ref.stop
        if self.exit_order is None:
            new, rules = self.ref.bar_close(p, fill_bar=k == 0)
        else:
            new, rules = old, frozenset()  # a market exit is pending: no stop updates
        # Effective from the next bar: stamped at this bar's close, the next bar's open.
        self.check_update(intents, events, old, new, rules, b.close_ns)
        assert intents == [i for i in intents if isinstance(i, ModifyOrder)]
        self.record()
        return True

    def decision(self, k: int, p: Ohlc, flip: bool) -> None:
        """A Decision_Time at bar ``k``'s close showing the placement map or a King flip."""
        t = to_bar(k, p).close_ns
        view = source_view(self.case, t, flip=flip)
        self.book, intents, events = manage(self.book, context(t, view, p.close), self.cfg)
        old = self.ref.stop
        new, rules = old, frozenset[str]()
        if not flip:
            assert (intents, events) == ([], [])
        elif self.sign * (p.close - self.case.breakeven_price) <= 0:
            # Req 12.22: the close is at or past Breakeven_Price against the trade.
            (x,) = intents
            assert isinstance(x, SubmitExit)
            self.exit_order = x.order
            self.ref.tags.add("invalidation exit at market")
        else:
            new, rules = self.ref.apply([(self.case.breakeven_price, "invalidation_breakeven")])
            assert intents == [i for i in intents if isinstance(i, ModifyOrder)]
        self.check_update(intents, events, old, new, rules, t)
        self.record()

    def run(self) -> None:
        for k, (p, decide) in enumerate(stream(self.case)):
            if not self.bar(k, p):
                break
            if decide != "none" and self.exit_order is None:
                self.decision(k, p, flip=decide == "flip")


# ---------------------------------------------------------------- generators

UNITS: Final = st.one_of(st.integers(10, 30), st.integers(-30, -10))


def tenths(lo: int, hi: int) -> st.SearchStrategy[Decimal]:
    return st.integers(lo, hi).map(lambda n: Decimal(n) / 10)


def nonzero(lo: int, hi: int) -> st.SearchStrategy[int]:
    """An integer from ``lo`` to ``hi`` other than 0 (``lo < 0 < hi``)."""
    return st.integers(lo, hi - 1).map(lambda d: d if d < 0 else d + 1)


MOVES: Final = st.builds(
    Move,
    gap=st.integers(-2, 2),
    body=st.integers(-8, 16),
    up=st.integers(0, 6),
    down=st.integers(0, 6),
    decide=st.sampled_from(DECIDES),
)
TRIGGER_R: Final = st.one_of(
    st.sampled_from(("0.25", "0.5", "1.0", "1.5", "2.0")).map(Decimal),
    st.integers(25, 1000).map(lambda n: Decimal(n) / 100),
)


@st.composite
def cases(draw: st.DrawFn) -> Case:
    risk = draw(st.one_of(st.integers(2, 20), st.integers(2, 80)))
    entry_offset = draw(st.integers(0, 8))
    # The source never lies beyond the fill, and the fill stays beyond the stop.
    fill_offset = max(draw(st.integers(-2, 2)), -entry_offset, 1 - risk)
    at_fill = entry_offset + fill_offset  # the fill's offset from the source level
    to_stop = risk + fill_offset
    on_fill = tuple(d for d in (at_fill, at_fill + 1) if d != 0)  # on, or a tick beyond, the fill
    n_nodes = draw(st.integers(0, 8))
    offsets = draw(
        st.lists(
            st.one_of(nonzero(-20, 60), nonzero(-80, 200), st.sampled_from(on_fill)),
            min_size=n_nodes,
            max_size=n_nodes,
            unique=True,
        )
    )
    adverse = -draw(st.integers(0, min(8, to_stop - 1)))
    favorable = draw(st.integers(0, 12))
    first = FirstBar(
        draw(st.integers(adverse, favorable)),
        favorable,
        adverse,
        draw(st.integers(adverse, favorable)),
    )
    tp1_r = draw(tenths(5, 20))
    return Case(
        direction=draw(st.sampled_from(DIRECTIONS)),
        mode=draw(st.sampled_from(MODES)),
        risk=risk,
        entry_offset=entry_offset,
        fill_offset=fill_offset,
        qty=draw(st.integers(1, 5)),
        source_units=draw(UNITS),
        nodes=tuple((d, draw(UNITS)) for d in offsets),
        first=first,
        moves=tuple(draw(MOVES) for _ in range(draw(st.integers(1, 30)))),
        breakeven=draw(st.booleans()),
        trigger_r=draw(TRIGGER_R),
        offset_ticks=draw(st.one_of(st.integers(0, 4), st.integers(0, 20))),
        fixed_r=draw(tenths(5, 100)),
        tp1_r=tp1_r,
        tp2_r=tp1_r + draw(tenths(5, 30)),  # TP2 at least 1 tick beyond TP1 (risk >= 2)
        tp1_fraction=draw(tenths(1, 9)),
        trail_ticks=draw(st.one_of(st.integers(1, 20), st.integers(1, 400))),
    )


# Long, 1R = 20 ticks: the favorable move reaches 19, then exactly 20 (breakeven to
# 23121); a King flip with the close above it changes nothing; the stop then fills.
BREAKEVEN_AT_1R: Final = Case(
    direction="long", mode="fixed_r", risk=20, entry_offset=0, fill_offset=0, qty=2,
    source_units=25, nodes=((40, -22), (80, 30)), first=FirstBar(2, 6, -3, 4),
    moves=(Move(0, 10, 5, 0), Move(0, 1, 5, 0), Move(-1, -10, 0, 0, "flip"), Move(0, -5, 0, 0)),
)  # fmt: skip
# Short, source 6 ticks behind the fill and 2 behind the stop: reaching the first Node
# beyond the fill proposes the source level (looser, refused); reaching the second moves
# the stop to the first; a King flip proposes a looser Breakeven_Price (refused).
NODE_STEP_SHORT: Final = Case(
    direction="short", mode="trail_node", risk=4, entry_offset=6, fill_offset=0, qty=1,
    source_units=-20, nodes=((-6, 15), (6, -12), (16, 25), (31, -30)),
    first=FirstBar(2, 3, -2, 1), moves=(Move(0, 10, 0, 0), Move(0, 14, 1, 0),
    Move(0, -5, 0, 0, "flip")), breakeven=False,
)  # fmt: skip
# Long, 3 contracts: TP1 (1.5R = 23150) fills 1 contract and the other 2 move to the
# Breakeven_Price 23122 from the next bar, where the stop then fills.
TP1_TO_BREAKEVEN: Final = Case(
    direction="long", mode="tp1_partial_be", risk=20, entry_offset=0, fill_offset=0, qty=3,
    source_units=25, nodes=((40, -22),), first=FirstBar(1, 3, -1, 2),
    moves=(Move(0, 20, 9, 0), Move(0, -15, 0, 0), Move(0, -6, 0, 0)), breakeven=False,
    offset_ticks=2,
)  # fmt: skip


# ---------------------------------------------------------------- property


# Feature: skylit-futures-strategy-engine, Property 38: Stops only tighten
@given(case=cases())
@example(case=BREAKEVEN_AT_1R)
@example(case=NODE_STEP_SHORT)
@example(case=TP1_TO_BREAKEVEN)
def test_stops_only_tighten(case: Case) -> None:
    run = Run(case)
    run.run()
    event(f"mode {case.mode}")
    for tag in sorted(run.ref.tags):
        event(tag)
    # The whole sequence: a long's stop never decreases, a short's never increases.
    assert all(case.sign * (b - a) >= 0 for a, b in pairwise(run.history))


def test_examples_exercise_each_rule() -> None:
    """The explicit examples reach the outcomes their comments describe."""
    expected = {
        BREAKEVEN_AT_1R: ([23100, 23121], {"moved by breakeven", "closed by the stop"}),
        NODE_STEP_SHORT: ([23118, 23104], {"moved by trail_node", "a looser level was refused"}),
        TP1_TO_BREAKEVEN: ([23100, 23122], {"moved by tp1_breakeven", "tp1 filled"}),
    }
    for case, (stops, tags) in expected.items():
        run = Run(case)
        run.run()
        assert [price for _, price in run.versions] == stops
        assert tags <= run.ref.tags
