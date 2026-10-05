"""Finding_Cards, 2R alerts and the send schedule, built by code (design §25, Req 25.1-25.9).

**The card** (:func:`build_card`) of one completed Decision_Time holds:

- map fields (Req 25.1): the Decision_Time, the Order_Mode, per configured
  (symbol, metric) the Snapshot ``asOf`` and the King, Floor and Ceiling with
  their converted ES or NQ levels (plus Gatekeepers and Air_Pockets), spot per
  configured symbol, the King flips since the previous sent card (old and new
  strike), the Regime, the Map_Grade and the Trinity status (whether the gamma
  King of SPX, SPY and QQQ is above or below its spot);
- setups (Req 25.2): up to :data:`CARD_SETUP_LIMIT` Candidate_Setups ordered
  A_Plus, Alert_2R, Pass (detection order within a Grade), each with its
  Setup_Key, Grade and up to :data:`CARD_REASON_LIMIT` Rejection_Reasons in
  Gate order, and the count not listed;
- orders and positions (Req 25.3): the working orders, the orders armed and
  cancelled since the previous sent card, the open position per instrument
  (direction, contracts, entry, stop and unrealized R_Multiple) or ``flat``,
  and the next watch level above and below the futures price (the nearest
  converted Node levels).

A field without data is kept and shows :data:`UNAVAILABLE`, an empty list
:data:`NONE` (Req 25.4). Levels and prices are in futures points.

**The premarket card** (:func:`premarket_card`, Req 25.7) holds the Regime,
the Map_Grade and every (symbol, metric) with its Gatekeepers and
Air_Pockets; it has no setups and no orders.

**2R alerts** (:func:`alerts_for`, Req 25.8): one per Setup_Key the first
time it is graded Alert_2R, with instrument, direction, entry, stop, targets,
reward:risk and Pattern. No order is ever placed for an Alert_2R setup
(Req 25.9): the engine sizes and places A_Plus setups only.

**The schedule** (:class:`CardSchedule`, Req 25.5-25.6). A Decision_Time's
card is sent when its triggers fired (a Grade change, an order event or a King
flip, :class:`~fse.engine.step.CardTriggers`), when ``notify.interval_min``
has passed since the last sent card, or when no card was sent yet (the first
card carries the start notes). One card at most per Decision_Time.

Nothing here reads a clock, a secret or a broker account id.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Final, Literal

from fse.engine.gates.registry import Grade
from fse.engine.levels import ConvertedMap, LevelParams, convert_map, ticks_to_points
from fse.engine.nodes import NodeLabels, NodeParams, classify
from fse.engine.planner import CancelOrder, PlaceBracket
from fse.engine.regime import RegimeParams, RegimeResult, TrailingMedians, map_state_grade, regime
from fse.engine.state import EngineState
from fse.engine.step import CardTriggers, DecisionPayload, KingFlip, SetupDecision
from fse.engine.types import MissingInput, Order, SetupKey, Snapshot, Ticks, Unavailable
from fse.logio.canonical_json import JsonValue, ny_iso
from fse.pit.protocols import MarketView, SymMetric, futures_price
from fse.timekit import NS_PER_SECOND, Instant

__all__ = [
    "CARD_REASON_LIMIT",
    "CARD_SETUP_LIMIT",
    "FLAT",
    "NONE",
    "TRINITY_SYMBOLS",
    "UNAVAILABLE",
    "Alert2R",
    "CardKind",
    "CardLedger",
    "CardSchedule",
    "FindingCard",
    "LevelField",
    "MapField",
    "OrderField",
    "PositionField",
    "SetupField",
    "WatchField",
    "alerts_for",
    "build_card",
    "premarket_card",
    "render_alert",
    "render_card",
    "setup_key_text",
    "should_send",
]

UNAVAILABLE: Final = "unavailable"
NONE: Final = "none"
FLAT: Final = "flat"
CARD_SETUP_LIMIT: Final = 5
CARD_REASON_LIMIT: Final = 3
TRINITY_SYMBOLS: Final[tuple[str, ...]] = ("SPX", "SPY", "QQQ")
_TRINITY_METRIC: Final = "gamma"
_GRADE_RANK: Final[Mapping[Grade, int]] = {"A_Plus": 0, "Alert_2R": 1, "Pass": 2}

type CardKind = Literal["decision", "premarket"]
type Shown = float | int | str
"""A value, or :data:`UNAVAILABLE` when its data is missing."""


def setup_key_text(key: SetupKey) -> str:
    """A Setup_Key as one line: instrument, Pattern, direction, source strike, Tap number."""
    return (
        f"{key.instrument} {key.pattern} {key.direction} {key.source_strike:g} "
        f"tap {key.tap_seq} ({key.session.isoformat()})"
    )


def _points(level: Ticks | None) -> Shown:
    return UNAVAILABLE if level is None else ticks_to_points(level)


# ---------------------------------------------------------------- card fields


@dataclass(frozen=True, slots=True)
class LevelField:
    """A Snapshot strike and its converted futures level (points)."""

    strike: Shown
    level: Shown

    def to_jsonable(self) -> JsonValue:
        return {"strike": self.strike, "level": self.level}


_NO_LEVEL: Final = LevelField(UNAVAILABLE, UNAVAILABLE)


@dataclass(frozen=True, slots=True)
class MapField:
    """One configured (symbol, metric) of the Map_State."""

    symbol: str
    metric: str
    as_of: str
    king: LevelField
    floor: LevelField
    ceiling: LevelField
    gatekeepers: tuple[LevelField, ...] | str
    air_pockets: tuple[tuple[LevelField, LevelField], ...] | str

    def to_jsonable(self) -> JsonValue:
        return {
            "symbol": self.symbol,
            "metric": self.metric,
            "as_of": self.as_of,
            "king": self.king.to_jsonable(),
            "floor": self.floor.to_jsonable(),
            "ceiling": self.ceiling.to_jsonable(),
            "gatekeepers": _listed([g.to_jsonable() for g in self.gatekeepers])
            if isinstance(self.gatekeepers, tuple)
            else self.gatekeepers,
            "air_pockets": _listed(
                [[lo.to_jsonable(), hi.to_jsonable()] for lo, hi in self.air_pockets]
            )
            if isinstance(self.air_pockets, tuple)
            else self.air_pockets,
        }


@dataclass(frozen=True, slots=True)
class SetupField:
    """One listed Candidate_Setup: its key, Grade and up to 3 Rejection_Reasons."""

    key: str
    grade: Grade
    reasons: tuple[str, ...]

    def to_jsonable(self) -> JsonValue:
        return {"key": self.key, "grade": self.grade, "reasons": _listed(list(self.reasons))}


@dataclass(frozen=True, slots=True)
class OrderField:
    """One order: client id, instrument, side, type, contracts and price (points)."""

    client_id: str
    instrument: str
    side: str
    kind: str
    qty: int
    price: Shown
    role: str

    @classmethod
    def of(cls, order: Order) -> OrderField:
        price: Shown = "market" if order.price is None else ticks_to_points(order.price)
        return cls(
            order.client_id, order.instrument, order.side, order.kind, order.qty, price, order.role
        )

    def to_jsonable(self) -> JsonValue:
        return {
            "client_id": self.client_id,
            "instrument": self.instrument,
            "side": self.side,
            "kind": self.kind,
            "qty": self.qty,
            "price": self.price,
            "role": self.role,
        }


@dataclass(frozen=True, slots=True)
class PositionField:
    """One open position, or ``flat`` (``direction`` is :data:`FLAT`, the rest ``none``)."""

    instrument: str
    direction: str
    contracts: Shown
    entry: Shown
    stop: Shown
    unrealized_r: Shown

    def to_jsonable(self) -> JsonValue:
        return {
            "instrument": self.instrument,
            "direction": self.direction,
            "contracts": self.contracts,
            "entry": self.entry,
            "stop": self.stop,
            "unrealized_r": self.unrealized_r,
        }


@dataclass(frozen=True, slots=True)
class WatchField:
    """The nearest converted Node level above and below an instrument's futures price."""

    instrument: str
    price: Shown
    above: Shown
    below: Shown

    def to_jsonable(self) -> JsonValue:
        return {
            "instrument": self.instrument,
            "price": self.price,
            "above": self.above,
            "below": self.below,
        }


@dataclass(frozen=True, slots=True)
class FindingCard:
    """One Finding_Card (see the module notes); every field is always present."""

    kind: CardKind
    t: Instant
    order_mode: str
    map: tuple[MapField, ...]
    spots: tuple[tuple[str, Shown], ...]
    king_flips: tuple[KingFlip, ...]
    regime: str
    map_grade: str
    trinity: tuple[tuple[str, str], ...]
    setups: tuple[SetupField, ...]
    setups_not_listed: int
    working_orders: tuple[OrderField, ...]
    armed: tuple[OrderField, ...]
    cancelled: tuple[str, ...]
    positions: tuple[PositionField, ...]
    watch: tuple[WatchField, ...]
    notes: tuple[str, ...] = ()

    def to_jsonable(self) -> dict[str, JsonValue]:
        """The card as JSON values; an empty list shows :data:`NONE`."""
        return {
            "kind": self.kind,
            "decision_time": self.t,
            "decision_time_ny": ny_iso(self.t),
            "order_mode": self.order_mode,
            "map": [m.to_jsonable() for m in self.map],
            "spot": {s: v for s, v in self.spots},
            "king_flips": _listed(
                [
                    {"symbol": f.symbol, "metric": f.metric, "old": f.before, "new": f.after}
                    for f in self.king_flips
                ]
            ),
            "regime": self.regime,
            "map_grade": self.map_grade,
            "trinity": {s: v for s, v in self.trinity},
            "setups": _listed([s.to_jsonable() for s in self.setups]),
            "setups_not_listed": self.setups_not_listed,
            "working_orders": _listed([o.to_jsonable() for o in self.working_orders]),
            "armed": _listed([o.to_jsonable() for o in self.armed]),
            "cancelled": _listed(list(self.cancelled)),
            "positions": [p.to_jsonable() for p in self.positions],
            "watch": [w.to_jsonable() for w in self.watch],
            "notes": _listed(list(self.notes)),
        }


def _listed[T](items: list[T]) -> list[T] | str:
    return items if items else NONE


@dataclass(frozen=True, slots=True)
class Alert2R:
    """One 2R alert (Req 25.8)."""

    t: Instant
    key: SetupKey
    entry: Shown
    stop: Shown
    targets: tuple[float, ...] | str
    reward_risk: Shown

    def to_jsonable(self) -> dict[str, JsonValue]:
        return {
            "kind": "alert_2r",
            "decision_time": self.t,
            "decision_time_ny": ny_iso(self.t),
            "setup_key": setup_key_text(self.key),
            "instrument": self.key.instrument,
            "direction": self.key.direction,
            "pattern": self.key.pattern,
            "entry": self.entry,
            "stop": self.stop,
            "targets": list(self.targets) if isinstance(self.targets, tuple) else self.targets,
            "reward_risk": self.reward_risk,
        }


# ---------------------------------------------------------------- the map


@dataclass(frozen=True, slots=True)
class _MapView:
    fields: tuple[MapField, ...]
    spots: tuple[tuple[str, Shown], ...]
    trinity: tuple[tuple[str, str], ...]
    labels: Mapping[SymMetric, NodeLabels]
    converted: Mapping[SymMetric, ConvertedMap]


def _level(conv: ConvertedMap | None, strike: float | None) -> LevelField:
    if strike is None:
        return _NO_LEVEL
    if conv is None:
        return LevelField(strike, UNAVAILABLE)
    try:
        return LevelField(strike, ticks_to_points(conv.level_of(strike)))
    except KeyError:
        return LevelField(strike, UNAVAILABLE)


def _map_view(view: MarketView, node_params: NodeParams, level_params: LevelParams) -> _MapView:
    ms = view.map_state()
    converted_all = convert_map(view, level_params)
    labels: dict[SymMetric, NodeLabels] = {}
    converted: dict[SymMetric, ConvertedMap] = {}
    fields: list[MapField] = []
    spots: dict[str, Shown] = {}
    symbols: list[str] = []
    for key, entry in ms.entries.items():
        symbol, metric = key
        if symbol not in symbols:
            symbols.append(symbol)
        if not isinstance(entry, Snapshot):
            fields.append(
                MapField(
                    symbol,
                    metric,
                    UNAVAILABLE,
                    _NO_LEVEL,
                    _NO_LEVEL,
                    _NO_LEVEL,
                    UNAVAILABLE,
                    UNAVAILABLE,
                )
            )
            continue
        lab = classify(entry, node_params)
        labels[key] = lab
        conv_entry = converted_all.get(key)
        conv = conv_entry if isinstance(conv_entry, ConvertedMap) else None
        if conv is not None:
            converted[key] = conv
        spots.setdefault(symbol, entry.spot)
        fields.append(
            MapField(
                symbol=symbol,
                metric=metric,
                as_of=ny_iso(entry.as_of_ns),
                king=_level(conv, lab.king),
                floor=_level(conv, lab.floor),
                ceiling=_level(conv, lab.ceiling),
                gatekeepers=tuple(_level(conv, g) for g in lab.gatekeepers),
                air_pockets=tuple(
                    (_level(conv, lo), _level(conv, hi)) for lo, hi in lab.air_pockets
                ),
            )
        )
    trinity: list[tuple[str, str]] = []
    for symbol in TRINITY_SYMBOLS:
        snap = ms.entries.get((symbol, _TRINITY_METRIC))
        lab_t = labels.get((symbol, _TRINITY_METRIC))
        if not isinstance(snap, Snapshot) or lab_t is None or lab_t.king is None:
            trinity.append((symbol, UNAVAILABLE))
        elif lab_t.king > snap.spot:
            trinity.append((symbol, "above"))
        elif lab_t.king < snap.spot:
            trinity.append((symbol, "below"))
        else:
            trinity.append((symbol, "at"))
    return _MapView(
        tuple(fields),
        tuple((s, spots.get(s, UNAVAILABLE)) for s in symbols),
        tuple(trinity),
        labels,
        converted,
    )


def _regime_text(value: RegimeResult | MissingInput) -> str:
    if isinstance(value, MissingInput):
        return f"{UNAVAILABLE} (missing: {', '.join(value.names)})"
    return value.regime


def _grade_text(value: str | MissingInput) -> str:
    if isinstance(value, MissingInput):
        return f"{UNAVAILABLE} (missing: {', '.join(value.names)})"
    return value


# ---------------------------------------------------------------- the ledger


class CardLedger:
    """What happened since the previous sent card: King flips, armed and cancelled orders."""

    __slots__ = ("armed", "cancelled", "king_flips")

    def __init__(self) -> None:
        self.king_flips: list[KingFlip] = []
        self.armed: list[Order] = []
        self.cancelled: list[str] = []

    def record(self, payload: DecisionPayload) -> None:
        """Add one Decision_Time's King flips and order intents."""
        self.king_flips.extend(payload.cards.king_flips)
        for intent in payload.intents:
            if isinstance(intent, PlaceBracket):
                self.armed.append(intent.entry)
            elif isinstance(intent, CancelOrder):
                self.cancelled.append(intent.client_id)

    def reset(self) -> None:
        self.king_flips.clear()
        self.armed.clear()
        self.cancelled.clear()


# ---------------------------------------------------------------- building cards


def _setups(decisions: Sequence[SetupDecision]) -> tuple[tuple[SetupField, ...], int]:
    ranked = sorted(
        enumerate(decisions), key=lambda pair: (_GRADE_RANK[pair[1].evaluation.grade], pair[0])
    )
    listed = tuple(
        SetupField(
            setup_key_text(d.setup.key),
            d.evaluation.grade,
            tuple(r.reason for r in d.evaluation.rejections[:CARD_REASON_LIMIT]),
        )
        for _, d in ranked[:CARD_SETUP_LIMIT]
    )
    return listed, len(decisions) - len(listed)


def _positions(
    state: EngineState, instruments: Sequence[str], prices: Mapping[str, Ticks | Unavailable]
) -> tuple[PositionField, ...]:
    out: list[PositionField] = []
    for instrument in instruments:
        plans = [p for p in state.book.plans if p.status == "open" and p.instrument == instrument]
        if not plans:
            out.append(PositionField(instrument, FLAT, NONE, NONE, NONE, NONE))
            continue
        for plan in plans:
            entry = plan.entry_price
            price = prices.get(instrument)
            risk = None if entry is None else abs(entry - plan.planned_stop)
            r: Shown = UNAVAILABLE
            if (
                entry is not None
                and risk
                and price is not None
                and not isinstance(price, Unavailable)
            ):
                r = round(plan.sign * (price - entry) / risk, 4)
            out.append(
                PositionField(
                    instrument,
                    plan.direction,
                    plan.open_qty,
                    _points(entry),
                    _points(plan.stop_price),
                    r,
                )
            )
    return tuple(out)


def _watch(
    m: _MapView, instruments: Sequence[str], prices: Mapping[str, Ticks | Unavailable]
) -> tuple[WatchField, ...]:
    out: list[WatchField] = []
    for instrument in instruments:
        price = prices.get(instrument)
        if price is None or isinstance(price, Unavailable):
            out.append(WatchField(instrument, UNAVAILABLE, UNAVAILABLE, UNAVAILABLE))
            continue
        levels: set[Ticks] = set()
        for key, conv in m.converted.items():
            if conv.conversion.instrument != instrument:
                continue
            for node in m.labels[key].nodes:
                try:
                    levels.add(conv.level_of(node))
                except KeyError:
                    continue
        above = min((lv for lv in levels if lv > price), default=None)
        below = max((lv for lv in levels if lv < price), default=None)
        out.append(
            WatchField(
                instrument,
                ticks_to_points(price),
                NONE if above is None else ticks_to_points(above),
                NONE if below is None else ticks_to_points(below),
            )
        )
    return tuple(out)


def build_card(
    payload: DecisionPayload,
    view: MarketView,
    state: EngineState,
    *,
    instruments: Sequence[str],
    node_params: NodeParams,
    level_params: LevelParams,
    working: Iterable[Order],
    order_mode: str,
    since: CardLedger,
    notes: Iterable[str] = (),
) -> FindingCard:
    """The Finding_Card of the Decision_Time ``payload.t`` (see the module notes).

    ``view`` is the MarketView at ``payload.t``; ``state`` the EngineState the
    step returned; ``working`` the broker's working orders after the step's
    intents were routed; ``since`` what happened since the previous sent card
    (this Decision_Time included).
    """
    if view.t != payload.t:
        raise ValueError(f"the MarketView is at {view.t}, not at the Decision_Time {payload.t}")
    m = _map_view(view, node_params, level_params)
    prices = {f.instrument: f.price for f in payload.futures}
    listed, not_listed = _setups(payload.setups)
    return FindingCard(
        kind="decision",
        t=payload.t,
        order_mode=order_mode,
        map=m.fields,
        spots=m.spots,
        king_flips=tuple(since.king_flips),
        regime=_regime_text(payload.regime),
        map_grade=_grade_text(payload.map_grade),
        trinity=m.trinity,
        setups=listed,
        setups_not_listed=not_listed,
        working_orders=tuple(OrderField.of(o) for o in working),
        armed=tuple(OrderField.of(o) for o in since.armed),
        cancelled=tuple(since.cancelled),
        positions=_positions(state, instruments, prices),
        watch=_watch(m, instruments, prices),
        notes=tuple(notes),
    )


def premarket_card(
    view: MarketView,
    *,
    instruments: Sequence[str],
    node_params: NodeParams,
    level_params: LevelParams,
    regime_params: RegimeParams,
    medians: TrailingMedians,
    order_mode: str,
    notes: Iterable[str] = (),
) -> FindingCard:
    """The opening card at the premarket time (Req 25.7): Regime, Map_Grade and the map."""
    m = _map_view(view, node_params, level_params)
    ms = view.map_state()
    result = regime(ms, medians, view.vix(), regime_params)
    prices = {i: futures_price(view, i) for i in instruments}
    return FindingCard(
        kind="premarket",
        t=view.t,
        order_mode=order_mode,
        map=m.fields,
        spots=m.spots,
        king_flips=(),
        regime=_regime_text(result),
        map_grade=_grade_text(map_state_grade(ms, regime_params)),
        trinity=m.trinity,
        setups=(),
        setups_not_listed=0,
        working_orders=(),
        armed=(),
        cancelled=(),
        positions=tuple(PositionField(i, FLAT, NONE, NONE, NONE, NONE) for i in instruments),
        watch=_watch(m, instruments, prices),
        notes=tuple(notes),
    )


def alerts_for(payload: DecisionPayload, keys: Iterable[SetupKey]) -> tuple[Alert2R, ...]:
    """The 2R alert of each of ``keys`` from the setups of ``payload`` (Req 25.8)."""
    wanted = list(dict.fromkeys(keys))
    by_key: dict[SetupKey, SetupDecision] = {}
    for d in payload.setups:
        by_key.setdefault(d.setup.key, d)
    out: list[Alert2R] = []
    for key in wanted:
        found = by_key.get(key)
        if found is None:
            continue
        s = found.setup
        rr: Shown = UNAVAILABLE
        if s.entry is not None and s.stop is not None and s.targets and s.entry != s.stop:
            rr = round(abs(s.targets[0] - s.entry) / abs(s.entry - s.stop), 4)
        out.append(
            Alert2R(
                t=payload.t,
                key=key,
                entry=_points(s.entry),
                stop=_points(s.stop),
                targets=tuple(ticks_to_points(x) for x in s.targets) if s.targets else UNAVAILABLE,
                reward_risk=rr,
            )
        )
    return tuple(out)


# ---------------------------------------------------------------- the schedule


def should_send(
    triggers: CardTriggers, last_sent: Instant | None, now: Instant, interval_min: int
) -> bool:
    """Whether the card of the Decision_Time ``now`` is sent (Req 25.5-25.6).

    A trigger fired, no card was sent yet, or ``interval_min`` minutes have
    passed since the last sent card.
    """
    if triggers.fired or last_sent is None:
        return True
    return now - last_sent >= interval_min * 60 * NS_PER_SECOND


@dataclass(slots=True)
class CardSchedule:
    """Which Decision_Times send a card, and which Setup_Keys get a 2R alert (Req 25.5-25.8)."""

    interval_min: int
    alerts_2r: bool = True
    last_sent: Instant | None = None
    _alerted: set[SetupKey] = field(default_factory=set)
    _session: date | None = None

    def due(self, t: Instant, triggers: CardTriggers) -> bool:
        """Whether the card of Decision_Time ``t`` is sent (one card per Decision_Time)."""
        return should_send(triggers, self.last_sent, t, self.interval_min)

    def sent(self, t: Instant) -> None:
        """Record a card sent at ``t`` (a Decision_Time card or the premarket card)."""
        self.last_sent = t

    def alerts(self, session: date, triggers: CardTriggers) -> tuple[SetupKey, ...]:
        """The Setup_Keys to alert: each first Alert_2R once per session, when alerts are on."""
        if session != self._session:
            self._session = session
            self._alerted = set()
        if not self.alerts_2r:
            return ()
        out: list[SetupKey] = []
        for key in triggers.first_alerts:
            if key not in self._alerted:
                self._alerted.add(key)
                out.append(key)
        return tuple(out)


# ---------------------------------------------------------------- text


def _shown(value: Shown) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _lv(f: LevelField) -> str:
    if f.strike == UNAVAILABLE:
        return UNAVAILABLE
    return f"{_shown(f.strike)} (level {_shown(f.level)})"


def render_card(card: FindingCard) -> str:
    """The card as plain text for the console, file and webhook sinks."""
    title = "Premarket card" if card.kind == "premarket" else "Finding_Card"
    lines = [
        f"{title} {ny_iso(card.t)} | mode {card.order_mode} | Regime {card.regime} | "
        f"Map_Grade {card.map_grade}",
        "Spot: " + (", ".join(f"{s} {_shown(v)}" for s, v in card.spots) or NONE),
        "Trinity (gamma King vs spot): " + ", ".join(f"{s} {v}" for s, v in card.trinity),
        "Map:",
    ]
    for m in card.map:
        line = (
            f"  {m.symbol} {m.metric} asOf {m.as_of}: King {_lv(m.king)}, Floor {_lv(m.floor)}, "
            f"Ceiling {_lv(m.ceiling)}"
        )
        if card.kind == "premarket":
            gk = (
                m.gatekeepers
                if isinstance(m.gatekeepers, str)
                else ", ".join(_lv(g) for g in m.gatekeepers) or NONE
            )
            ap = (
                m.air_pockets
                if isinstance(m.air_pockets, str)
                else ", ".join(f"{_lv(lo)} to {_lv(hi)}" for lo, hi in m.air_pockets) or NONE
            )
            line += f"; Gatekeepers {gk}; Air_Pockets {ap}"
        lines.append(line)
    flips = ", ".join(f"{f.symbol} {f.metric} {f.before:g} -> {f.after:g}" for f in card.king_flips)
    lines.append("King flips: " + (flips or NONE))
    lines.append("Setups:" + ("" if card.setups else f" {NONE}"))
    for s in card.setups:
        reasons = ", ".join(s.reasons) or NONE
        lines.append(f"  {s.grade} {s.key}; rejections: {reasons}")
    lines.append(f"Setups not listed: {card.setups_not_listed}")

    def orders(items: Sequence[OrderField]) -> str:
        return (
            ", ".join(
                f"{o.client_id} {o.instrument} {o.side} {o.kind} {o.qty} @ {_shown(o.price)}"
                for o in items
            )
            or NONE
        )

    lines.append("Working orders: " + orders(card.working_orders))
    lines.append("Armed since last card: " + orders(card.armed))
    lines.append("Cancelled since last card: " + (", ".join(card.cancelled) or NONE))
    for p in card.positions:
        if p.direction == FLAT:
            lines.append(f"Position {p.instrument}: flat")
        else:
            lines.append(
                f"Position {p.instrument}: {p.direction} {_shown(p.contracts)} @ "
                f"{_shown(p.entry)}, stop {_shown(p.stop)}, unrealized R {_shown(p.unrealized_r)}"
            )
    for w in card.watch:
        lines.append(
            f"Watch {w.instrument} at {_shown(w.price)}: above {_shown(w.above)}, "
            f"below {_shown(w.below)}"
        )
    lines.append("Notes: " + ("; ".join(card.notes) or NONE))
    return "\n".join(lines)


def render_alert(alert: Alert2R) -> str:
    targets = (
        alert.targets
        if isinstance(alert.targets, str)
        else ", ".join(f"{x:g}" for x in alert.targets)
    )
    return (
        f"2R alert {ny_iso(alert.t)}: {alert.key.instrument} {alert.key.direction} "
        f"{alert.key.pattern}, entry {_shown(alert.entry)}, stop {_shown(alert.stop)}, "
        f"targets {targets}, reward:risk {_shown(alert.reward_risk)} (no order is placed)"
    )
