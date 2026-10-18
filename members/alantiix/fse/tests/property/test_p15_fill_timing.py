"""Property 15: Fill timing.

*For any* sequence of order placements, cancellations and price changes at
Decision_Times and any bar stream, every fill occurs on a bar that opens at or
after the order's placement time, a cancellation or price change at t affects
only bars opening at or after t, and fills on earlier bars stand.

Each case is a stream of 1-minute MES and MNQ bars (about 1 in 10 missing) and
a time-ordered list of operations on the ``SimBook``: brackets (limit or stop
entry, protective stop, 0 to 2 targets), price changes of any leg, cancels of
an entry or a target, and market exits. Operation times are biased to bar
opens, 1 ns after them and 1 ns before the next one, and to the placement time.
Leg prices stay in fixed bands (stop, entry, targets in the trade direction),
so every price change is valid whether or not the entry has filled.

Three checks:

1. Oracle: the order version of each fill is the placement with the last price
   change at or before the bar's open, and no fill comes on a bar opening
   before the placement or at or after a cancellation (Req 5.5, 5.6).
2. Interleaving: an operation at t may reach the book before or after the bars
   that open before t are processed. Applying every operation before any bar,
   or each one after all bars opening before it, or at a random point in
   between, gives the same fills and the same final book (Req 5.6).
3. Truncation: dropping every operation at or after a cutoff leaves the fills
   on bars opening before the cutoff unchanged, so later operations never undo
   earlier fills (Req 5.6).

**Validates: Requirements 5.5, 5.6**
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, time
from types import MappingProxyType
from typing import Final

from hypothesis import event, given
from hypothesis import strategies as st

from fse.config.schema.fills import FillsConfig
from fse.engine.types import (
    Bar,
    Direction,
    Order,
    OrderKind,
    OrderRole,
    SetupKey,
    Side,
    Ticks,
)
from fse.sim.fills import FillEvent, SimBook, on_bar
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, Instant, ny_instant

SESSION: Final = date(2026, 3, 5)
T0: Final = ny_instant(SESSION, time(9, 30))
RTH: Final = (T0, ny_instant(SESSION, time(16, 0)))

INSTRUMENTS: Final[tuple[str, ...]] = ("MES", "MNQ")
BASE: Final[Mapping[str, Ticks]] = MappingProxyType({"MES": 23_000, "MNQ": 84_000})
"""Opening price of each instrument's walk, in ticks."""
MAX_MINUTES: Final = 16
DIRECTIONS: Final[tuple[Direction, ...]] = ("long", "short")
ENTRY_KINDS: Final[tuple[OrderKind, ...]] = ("limit", "stop")
TARGET_ROLES: Final[tuple[OrderRole, ...]] = ("tp1", "tp2")
OFFSETS_NS: Final = (0, 1, 30 * NS_PER_SECOND, NS_PER_MINUTE - 1)
"""Operation times: at a bar open, 1 ns after, mid-bar, 1 ns before the next open."""

# Price bands around a setup's anchor, in ticks, signed by the trade direction.
# Every stop sits below every entry, and every entry below every target (for a
# long), so no price change can break a bracket's price order.
ENTRY_BAND: Final = (-8, 8)
STOP_BAND: Final = (13, 24)
TARGET_BAND: Final = (13, 30)

# ---------------------------------------------------------------- operations


@dataclass(frozen=True, slots=True)
class Place:
    """Submit a bracket for setup ``setup`` at ``t``."""

    t: Instant
    setup: int
    instrument: str
    direction: Direction
    anchor: Ticks
    entry_kind: OrderKind
    qty: int
    entry: Ticks
    stop: Ticks
    targets: tuple[tuple[OrderRole, Ticks, int], ...]

    @property
    def key(self) -> SetupKey:
        return SetupKey(
            self.instrument, "floor_ceiling_bounce", 5800.0, self.direction, SESSION, self.setup + 1
        )

    @property
    def entry_side(self) -> Side:
        return "buy" if self.direction == "long" else "sell"

    @property
    def exit_side(self) -> Side:
        return "sell" if self.direction == "long" else "buy"

    def leg(self, role: OrderRole, kind: OrderKind, qty: int, price: Ticks) -> Order:
        side = self.entry_side if role == "entry" else self.exit_side
        cid = leg_id(self.setup, role)
        return Order(cid, self.key, self.instrument, side, kind, qty, price, self.t, role)

    def orders(self) -> tuple[Order, Order, list[Order]]:
        entry = self.leg("entry", self.entry_kind, self.qty, self.entry)
        stop = self.leg("stop", "stop", self.qty, self.stop)
        targets = [self.leg(role, "limit", n, price) for role, price, n in self.targets]
        return entry, stop, targets


@dataclass(frozen=True, slots=True)
class Modify:
    """Change the price of leg ``role`` of setup ``setup`` at ``t``."""

    t: Instant
    setup: int
    role: OrderRole
    price: Ticks


@dataclass(frozen=True, slots=True)
class Cancel:
    """Cancel leg ``role`` (the entry or a target) of setup ``setup`` at ``t``."""

    t: Instant
    setup: int
    role: OrderRole


@dataclass(frozen=True, slots=True)
class Exit:
    """Submit market exit number ``seq`` of setup ``setup`` at ``t``."""

    t: Instant
    setup: int
    seq: int
    qty: int


type Op = Place | Modify | Cancel | Exit


def leg_id(setup: int, role: OrderRole) -> str:
    return f"s{setup}:{role}"


def exit_order(op: Exit, place: Place) -> Order:
    return Order(
        f"s{op.setup}:exit{op.seq}",
        place.key,
        place.instrument,
        place.exit_side,
        "market",
        op.qty,
        None,
        op.t,
        "exit",
    )


@dataclass(frozen=True, slots=True)
class Case:
    """Bars in processing order, operations in time order, and the check parameters."""

    bars: tuple[Bar, ...]
    ops: tuple[Op, ...]
    cfg: FillsConfig
    cutoff: Instant
    slack: tuple[int, ...]

    @property
    def places(self) -> dict[int, Place]:
        return {op.setup: op for op in self.ops if isinstance(op, Place)}


# ---------------------------------------------------------------- generators


def make_bar(instrument: str, minute: int, o: Ticks, h: Ticks, low: Ticks, c: Ticks) -> Bar:
    start = T0 + minute * NS_PER_MINUTE
    return Bar(
        instrument=instrument,
        contract=f"{instrument}H6",
        interval_s=60,
        open_ns=start,
        close_ns=start + NS_PER_MINUTE,
        o=o / 4,
        h=h / 4,
        l=low / 4,
        c=c / 4,
        v=100.0,
        o_t=o,
        h_t=h,
        l_t=low,
        c_t=c,
        source="atlas",
    )


def _instant(minute: int, offset: int) -> Instant:
    return T0 + minute * NS_PER_MINUTE + offset


def instants(last_minute: int) -> st.SearchStrategy[Instant]:
    return st.builds(_instant, st.integers(0, last_minute), st.sampled_from(OFFSETS_NS))


def banded(anchor: Ticks, sign: int, band: tuple[int, int]) -> st.SearchStrategy[Ticks]:
    return st.integers(*band).map(lambda d: anchor + sign * d)


def role_band(role: OrderRole) -> tuple[tuple[int, int], int]:
    """The band of ``role`` and whether it lies on the loss (-1) or profit (+1) side."""
    if role == "entry":
        return ENTRY_BAND, 1
    if role == "stop":
        return STOP_BAND, -1
    return TARGET_BAND, 1


def price_for(draw: st.DrawFn, place: Place, role: OrderRole) -> Ticks:
    band, side = role_band(role)
    sign = 1 if place.direction == "long" else -1
    return draw(banded(place.anchor, side * sign, band))


@st.composite
def bar_streams(draw: st.DrawFn, n_minutes: int) -> tuple[Bar, ...]:
    bars: list[Bar] = []
    for instrument in INSTRUMENTS:
        close = BASE[instrument]
        for minute in range(n_minutes):
            o = close + draw(st.integers(-4, 4))
            c = o + draw(st.integers(-8, 8))
            h = max(o, c) + draw(st.integers(0, 4))
            low = min(o, c) - draw(st.integers(0, 4))
            close = c
            if draw(st.integers(0, 9)):  # about 1 bar in 10 is missing
                bars.append(make_bar(instrument, minute, o, h, low, c))
    bars.sort(key=lambda b: (b.open_ns, INSTRUMENTS.index(b.instrument)))
    return tuple(bars)


@st.composite
def place_ops(draw: st.DrawFn, setup: int, last_minute: int) -> Place:
    instrument = draw(st.sampled_from(INSTRUMENTS))
    direction = draw(st.sampled_from(DIRECTIONS))
    qty = draw(st.integers(1, 4))
    n_targets = min(qty, draw(st.integers(0, 2)))
    left = qty
    target_qty: list[int] = []
    for i in range(n_targets):
        n = draw(st.integers(1, left - (n_targets - 1 - i)))
        target_qty.append(n)
        left -= n
    draft = Place(
        t=draw(instants(last_minute)),
        setup=setup,
        instrument=instrument,
        direction=direction,
        anchor=BASE[instrument] + draw(st.integers(-12, 12)),
        entry_kind=draw(st.sampled_from(ENTRY_KINDS)),
        qty=qty,
        entry=0,
        stop=0,
        targets=(),
    )
    return replace(
        draft,
        entry=price_for(draw, draft, "entry"),
        stop=price_for(draw, draft, "stop"),
        targets=tuple(
            (role, price_for(draw, draft, role), n)
            for role, n in zip(TARGET_ROLES, target_qty, strict=False)
        ),
    )


@st.composite
def change_ops(draw: st.DrawFn, place: Place, seq: int, last_minute: int) -> Op:
    """A price change, cancel or market exit for ``place``, never before its placement."""
    t = max(place.t, draw(instants(last_minute)))
    targets: list[OrderRole] = [role for role, _, _ in place.targets]
    kind = draw(st.sampled_from(("modify", "cancel", "exit")))
    if kind == "modify":
        modifiable: list[OrderRole] = ["entry", "stop", *targets]
        role = draw(st.sampled_from(modifiable))
        return Modify(t, place.setup, role, price_for(draw, place, role))
    if kind == "cancel":
        # The Order_Planner cancels entries and targets; a protective stop goes with its trade.
        cancellable: list[OrderRole] = ["entry", *targets]
        return Cancel(t, place.setup, draw(st.sampled_from(cancellable)))
    return Exit(t, place.setup, seq, draw(st.integers(1, place.qty)))


def _op_order(op: Op) -> tuple[Instant, int]:
    """Time order; at one instant a placement comes before the changes to it."""
    return op.t, 0 if isinstance(op, Place) else 1


@st.composite
def cases(draw: st.DrawFn) -> Case:
    n_minutes = draw(st.integers(1, MAX_MINUTES))
    bars = draw(bar_streams(n_minutes))
    setups = [draw(place_ops(i, n_minutes)) for i in range(draw(st.integers(1, 4)))]
    others = [
        draw(change_ops(draw(st.sampled_from(setups)), seq, n_minutes))
        for seq in range(draw(st.integers(0, 10)))
    ]
    ops = tuple(sorted([*setups, *others], key=_op_order))  # stable: ties keep draw order
    cfg = FillsConfig.model_validate(
        {
            "trade_through_ticks": draw(st.integers(0, 2)),
            "slippage_ticks": draw(st.integers(0, 2)),
            "costs": {name: {"commission": "0.37", "exchange_fee": "0.35"} for name in INSTRUMENTS},
        }
    )
    slack = draw(st.lists(st.integers(0, 2**16), min_size=len(ops), max_size=len(ops)))
    return Case(bars, ops, cfg, draw(instants(n_minutes)), tuple(slack))


# ---------------------------------------------------------------- simulation


def apply(book: SimBook, op: Op, places: Mapping[int, Place]) -> SimBook:
    """Apply one operation the way the Order_Planner would.

    A change to an order that already filled or was cancelled, or an exit for
    a Setup_Key no longer placed, is skipped: the planner only acts on what is
    still working.
    """
    if isinstance(op, Place):
        entry, stop, targets = op.orders()
        return book.submit_bracket(entry, stop, targets)
    if isinstance(op, Exit):
        place = places[op.setup]
        if place.key not in book.brackets:
            return book
        return book.submit_exit(exit_order(op, place))
    cid = leg_id(op.setup, op.role)
    if book.order(cid) is None:
        return book
    if isinstance(op, Cancel):
        return book.cancel(cid, op.t)
    return book.modify(cid, op.price, op.t)


def simulate(
    case: Case, ops: Sequence[Op], points: Sequence[int]
) -> tuple[SimBook, list[FillEvent]]:
    """Process the bars, applying ``ops[i]`` once ``points[i]`` bars are processed."""
    places = case.places
    book = SimBook()
    events: list[FillEvent] = []
    done = 0

    def process(upto: int) -> None:
        nonlocal book, done
        while done < upto:
            book, new = on_bar(book, case.bars[done], case.cfg, rth=RTH)
            events.extend(new)
            done += 1

    for op, point in zip(ops, points, strict=True):
        process(point)
        book = apply(book, op, places)
    process(len(case.bars))
    return book, events


def latest_points(case: Case) -> list[int]:
    """Each operation at t after every bar opening before t: the latest valid point."""
    opens = [b.open_ns for b in case.bars]
    return [bisect_left(opens, op.t) for op in case.ops]


def random_points(limits: Sequence[int], slack: Sequence[int]) -> list[int]:
    """Non-decreasing points, each at most its operation's latest valid point."""
    points: list[int] = []
    point = 0
    for limit, s in zip(limits, slack, strict=True):
        point += s % (limit - point + 1)
        points.append(point)
    return points


# ---------------------------------------------------------------- oracle


def expected_versions(case: Case) -> dict[str, list[tuple[Instant, Order | None]]]:
    """Per client id, ``(effective from, version)`` from the operations alone; ``None`` cancels."""
    places = case.places
    out: dict[str, list[tuple[Instant, Order | None]]] = {}
    for op in case.ops:
        if isinstance(op, Place):
            entry, stop, targets = op.orders()
            for order in (entry, stop, *targets):
                out[order.client_id] = [(op.t, order)]
        elif isinstance(op, Exit):
            order = exit_order(op, places[op.setup])
            out[order.client_id] = [(op.t, order)]
        else:
            versions = out[leg_id(op.setup, op.role)]
            current = versions[-1][1]
            if current is None:
                continue  # a cancelled order takes no further change
            changed = None if isinstance(op, Cancel) else replace(current, price=op.price)
            versions.append((op.t, changed))
    return out


def version_at(versions: Sequence[tuple[Instant, Order | None]], open_ns: Instant) -> Order | None:
    """The version a bar opening at ``open_ns`` sees; ``None`` before placement or if cancelled."""
    seen = [version for at, version in versions if at <= open_ns]
    return seen[-1] if seen else None


def before(events: Sequence[FillEvent], cutoff: Instant) -> list[FillEvent]:
    return [e for e in events if e.fill.bar_open_ns < cutoff]


# ---------------------------------------------------------------- property


# Feature: skylit-futures-strategy-engine, Property 15: Fill timing
@given(case=cases())
def test_fill_timing(case: Case) -> None:
    book, events = simulate(case, case.ops, [0] * len(case.ops))
    event("fills" if events else "no fills")

    # 1. Each fill is on a bar of its instrument opening at or after placement,
    #    with the version in effect at that open (Req 5.5, 5.6).
    versions = expected_versions(case)
    opens = {(b.instrument, b.open_ns) for b in case.bars}
    for e in events:
        cid, open_ns = e.fill.client_id, e.fill.bar_open_ns
        assert e.order.client_id == cid
        assert (e.order.instrument, open_ns) in opens, f"{cid} filled off the bar stream"
        placed_at = versions[cid][0][0]
        assert placed_at <= open_ns, f"{cid} placed at {placed_at} filled on bar {open_ns}"
        assert e.order.placed_at <= open_ns
        seen = version_at(versions[cid], open_ns)
        assert seen is not None, f"{cid} filled on bar {open_ns} after its cancellation"
        assert e.order == seen, f"{cid} filled on bar {open_ns} with the wrong version"

    # 2. When the operations reach the book does not matter, as long as each one
    #    comes before the bars opening at or after its time (Req 5.6).
    limits = latest_points(case)
    for points in (limits, random_points(limits, case.slack)):
        other_book, other = simulate(case, case.ops, points)
        assert other == events
        assert other_book.trades == book.trades
        assert other_book.working_orders() == book.working_orders()

    # 3. Operations at or after the cutoff leave earlier fills standing (Req 5.6).
    kept = [op for op in case.ops if op.t < case.cutoff]
    _, truncated = simulate(case, kept, [0] * len(kept))
    assert before(truncated, case.cutoff) == before(events, case.cutoff)
