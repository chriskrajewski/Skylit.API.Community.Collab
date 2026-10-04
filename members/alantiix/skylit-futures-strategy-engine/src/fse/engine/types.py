"""Core value types shared by the engine, the data layer and the simulators.

Design "Data Models". Every type is a frozen, slotted dataclass. Instants are
``int`` ns UTC (``fse.timekit.Instant``); futures prices are integer ticks of
0.25 point.

Missing data is a value, not ``None`` and not an exception (design "Error
Handling" 2). ``Unavailable``, ``MissingInput``, ``MissingPrice``,
``NotApplicable`` and ``NoTarget`` are distinct sentinel types, so code cannot
confuse "missing" with "zero" or with one another.

The decision types (``SetupKey`` through ``Trade``) encode with
``fse.logio.canonical_json.to_jsonable``. Every union field stays decodable by
its JSON shape: a sentinel encodes as an object, a price as a number, a label
as a string, money (``Decimal``) as a string.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from itertools import pairwise
from typing import Literal

from fse.timekit import NS_PER_SECOND, Instant

type Ticks = int
"""A futures price in ticks: 1 tick = 0.25 point."""

type Metric = Literal["gamma", "vanna"]
type Resolution = Literal["1s", "1m"]
type SourceEndpoint = Literal["range", "historical", "heatmap", "stream"]
type BarSourceName = Literal["atlas", "projectx"]

METRICS: frozenset[str] = frozenset({"gamma", "vanna"})
RESOLUTIONS: frozenset[str] = frozenset({"1s", "1m"})
SOURCE_ENDPOINTS: frozenset[str] = frozenset({"range", "historical", "heatmap", "stream"})
BAR_SOURCES: frozenset[str] = frozenset({"atlas", "projectx"})


def _require_member(name: str, value: str, allowed: frozenset[str]) -> None:
    if value not in allowed:
        raise ValueError(f"{name} must be one of {sorted(allowed)}, got {value!r}")


def _require_text(name: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be blank")


# ---------------------------------------------------------------- sentinels


@dataclass(frozen=True, slots=True)
class Unavailable:
    """A value that could not be observed at the Decision_Time, with why."""

    reason: str

    def __post_init__(self) -> None:
        _require_text("Unavailable.reason", self.reason)


@dataclass(frozen=True, slots=True)
class MissingInput:
    """A computation that could not run; ``names`` lists the absent inputs."""

    names: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.names:
            raise ValueError("MissingInput.names must name at least one input")
        for name in self.names:
            _require_text("MissingInput.names entry", name)


@dataclass(frozen=True, slots=True)
class MissingPrice:
    """No usable conversion price for a source symbol or futures contract (Req 8.10)."""

    symbol_or_contract: str

    def __post_init__(self) -> None:
        _require_text("MissingPrice.symbol_or_contract", self.symbol_or_contract)


@dataclass(frozen=True, slots=True)
class NotApplicable:
    """A metric that is undefined for its inputs, rendered "not applicable" (Req 20.16)."""


@dataclass(frozen=True, slots=True)
class NoTarget:
    """An exit plan with no target price (for example, the Trailing Exit_Mode)."""


# ---------------------------------------------------------------- market data


@dataclass(frozen=True, slots=True)
class Snapshot:
    """One Heatseeker per-strike heatmap for one symbol, metric and ``asOf``.

    ``strikes``, ``values`` and (when present) ``node_types`` are parallel.
    ``node_types`` is ``None`` for range frames, which carry no labels.
    """

    symbol: str
    metric: Metric
    view_id: str
    as_of_ns: Instant
    as_of_raw: str  # raw string kept for exact round-trip (Req 3.10)
    spot: float
    previous_close: float | None
    strikes: tuple[float, ...]
    values: tuple[float, ...]  # float64, as returned
    node_types: tuple[str | None, ...] | None
    expirations: tuple[str, ...]
    resolution: Resolution
    source_endpoint: SourceEndpoint
    extra_json: str  # unrecognized response fields, canonical JSON

    def __post_init__(self) -> None:
        _require_member("Snapshot.metric", self.metric, METRICS)
        _require_member("Snapshot.resolution", self.resolution, RESOLUTIONS)
        _require_member("Snapshot.source_endpoint", self.source_endpoint, SOURCE_ENDPOINTS)
        if len(self.values) != len(self.strikes):
            raise ValueError(
                f"Snapshot has {len(self.strikes)} strikes but {len(self.values)} values"
            )
        if self.node_types is not None and len(self.node_types) != len(self.strikes):
            raise ValueError(
                f"Snapshot has {len(self.strikes)} strikes but {len(self.node_types)} node types"
            )


@dataclass(frozen=True, slots=True)
class Bar:
    """One OHLCV bar stamped with its open and close instants (Req 4.7).

    ``close_ns = open_ns + interval_s`` seconds: the 1-minute bar covering
    10:00-10:01 has ``open_ns`` at 10:00:00 and ``close_ns`` at 10:01:00. The
    ``*_t`` tick prices are set for futures bars and are all ``None`` for
    index bars such as VIX.
    """

    instrument: str
    contract: str
    interval_s: int
    open_ns: Instant
    close_ns: Instant
    o: float
    h: float
    l: float  # noqa: E741 - design field name
    c: float
    v: float
    o_t: Ticks | None
    h_t: Ticks | None
    l_t: Ticks | None
    c_t: Ticks | None
    source: BarSourceName

    def __post_init__(self) -> None:
        _require_member("Bar.source", self.source, BAR_SOURCES)
        if isinstance(self.interval_s, bool) or self.interval_s <= 0:
            raise ValueError(f"Bar.interval_s must be a positive integer, got {self.interval_s!r}")
        if self.close_ns != self.open_ns + self.interval_s * NS_PER_SECOND:
            raise ValueError("Bar.close_ns must equal open_ns plus interval_s")
        ticks = (self.o_t, self.h_t, self.l_t, self.c_t)
        if any(x is None for x in ticks) and not all(x is None for x in ticks):
            raise ValueError("Bar tick prices must be all set (futures) or all None")


@dataclass(frozen=True, slots=True)
class DarkPoolPrint:
    """One off-exchange print; ``ts_ns`` is its Observation_Time (no side is given)."""

    ticker: str
    ts_ns: Instant
    price: float
    size: int
    notional: float
    venue: str


@dataclass(frozen=True, slots=True)
class VixState:
    """VIX values usable at a Decision_Time (Req 4.9-4.11)."""

    daily_open: float | Unavailable
    prior_close: float | Unavailable
    last_1m_close: float | Unavailable


# ---------------------------------------------------------------- calendars


@dataclass(frozen=True, slots=True)
class EconomicEvent:
    """One scheduled release from the economic events calendar (Req 4.14).

    ``event_type`` is ``CPI``, ``NFP``, ``FOMC`` or an Operator-added type.
    ``release_date`` is the America/New_York date and ``release_ns`` the
    release instant. The schedule is published in advance, so an event is
    visible at every Decision_Time. ``fse.calendars`` builds these.
    """

    event_type: str
    release_date: date
    release_ns: Instant
    note: str | None = None

    def __post_init__(self) -> None:
        _require_text("EconomicEvent.event_type", self.event_type)


# ---------------------------------------------------------------- decisions

type Money = Decimal
"""Exact dollars. Never a float, so P&L sums are exact (Req 13.10)."""

type Direction = Literal["long", "short"]
type Side = Literal["buy", "sell"]
type OrderKind = Literal["limit", "stop", "market"]
type OrderRole = Literal["entry", "stop", "tp1", "tp2", "exit"]
type SkipCondition = Literal["price_order", "no_target"]
type StopRule = Literal["one_node_beyond", "fixed_ticks"]
type ConversionMethod = Literal["offset", "ratio"]
type Regime = Literal[
    "Positive_Gamma", "Negative_Gamma", "Vanna_Dominant", "Whipsaw", "Structureless"
]
type MapGrade = Literal["A_Plus_Map", "Neutral_Map", "F_Map"]

DIRECTIONS: frozenset[str] = frozenset({"long", "short"})
SIDES: frozenset[str] = frozenset({"buy", "sell"})
ORDER_KINDS: frozenset[str] = frozenset({"limit", "stop", "market"})
ORDER_ROLES: frozenset[str] = frozenset({"entry", "stop", "tp1", "tp2", "exit"})
SKIP_CONDITIONS: frozenset[str] = frozenset({"price_order", "no_target"})
STOP_RULES: frozenset[str] = frozenset({"one_node_beyond", "fixed_ticks"})
CONVERSION_METHODS: frozenset[str] = frozenset({"offset", "ratio"})
REGIMES: frozenset[str] = frozenset(
    {"Positive_Gamma", "Negative_Gamma", "Vanna_Dominant", "Whipsaw", "Structureless"}
)
MAP_GRADES: frozenset[str] = frozenset({"A_Plus_Map", "Neutral_Map", "F_Map"})

_TICKS_PER_POINT = 4


def direction_sign(direction: Direction) -> int:
    """``+1`` for a long, ``-1`` for a short: P&L ticks are ``(exit - entry) * sign``."""
    _require_member("direction", direction, DIRECTIONS)
    return 1 if direction == "long" else -1


def _require_int(name: str, value: object, minimum: int | None = None) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer, got {value!r}")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}, got {value}")


def _require_finite(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}")


def _require_decimal(name: str, value: object) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal, got {value!r}")


def _require_date(name: str, value: object) -> None:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise ValueError(f"{name} must be a date (not a datetime), got {value!r}")


@dataclass(frozen=True, slots=True)
class SetupKey:
    """The identity of one Candidate_Setup across Decision_Times (Req 10.11).

    ``tap_seq`` is 1 plus the source Node's Taps in the session that ended
    before the Decision_Time, so a new Tap gives a new key. Keys are hashable:
    the Order_Planner never places the same key twice.
    """

    instrument: str
    pattern: str
    source_strike: float
    direction: Direction
    session: date
    tap_seq: int

    def __post_init__(self) -> None:
        _require_text("SetupKey.instrument", self.instrument)
        _require_text("SetupKey.pattern", self.pattern)
        _require_finite("SetupKey.source_strike", self.source_strike)
        _require_member("SetupKey.direction", self.direction, DIRECTIONS)
        _require_date("SetupKey.session", self.session)
        _require_int("SetupKey.tap_seq", self.tap_seq, minimum=1)


@dataclass(frozen=True, slots=True)
class SourceNodeRef:
    """The source Node of a Candidate_Setup, in its Snapshot (Req 10.5, 10.12).

    ``level`` is the converted futures level in ticks and ``band`` its
    Deflection_Band ``(lo, hi)`` in points. Both are ``None`` exactly when the
    Level_Converter returned ``MissingPrice`` for ``(symbol, metric)``: the
    Candidate_Setup is still emitted, unpriced, so the Gate_Evaluator rejects
    it with ``no_conversion_price`` and the funnel counts it (Req 8.11).
    """

    symbol: str
    metric: Metric
    strike: float
    value: float
    level: Ticks | None
    band: tuple[float, float] | None

    def __post_init__(self) -> None:
        _require_text("SourceNodeRef.symbol", self.symbol)
        _require_member("SourceNodeRef.metric", self.metric, METRICS)
        _require_finite("SourceNodeRef.strike", self.strike)
        _require_finite("SourceNodeRef.value", self.value)
        if self.level is None or self.band is None:
            if self.level is not None or self.band is not None:
                raise ValueError("SourceNodeRef.level and band must both be set or both be None")
            return
        _require_int("SourceNodeRef.level", self.level)
        lo, hi = self.band
        _require_finite("SourceNodeRef.band lo", lo)
        _require_finite("SourceNodeRef.band hi", hi)
        if not lo <= self.level / _TICKS_PER_POINT <= hi:
            raise ValueError(
                f"SourceNodeRef.band ({lo}, {hi}) points must contain level {self.level} ticks"
            )

    @property
    def priced(self) -> bool:
        """Whether the Node has a converted level (no ``MissingPrice``)."""
        return self.level is not None


@dataclass(frozen=True, slots=True)
class SnapshotAsOf:
    """The ``asOf`` of one Snapshot in the Map_State (Req 10.12)."""

    symbol: str
    metric: Metric
    as_of_ns: Instant

    def __post_init__(self) -> None:
        _require_text("SnapshotAsOf.symbol", self.symbol)
        _require_member("SnapshotAsOf.metric", self.metric, METRICS)
        _require_int("SnapshotAsOf.as_of_ns", self.as_of_ns)


@dataclass(frozen=True, slots=True)
class ChartLevelRef:
    """One chart level a detector's parameters reference, in points (Req 10.12)."""

    name: str
    value: float | Unavailable

    def __post_init__(self) -> None:
        _require_text("ChartLevelRef.name", self.name)
        if not isinstance(self.value, Unavailable):
            _require_finite("ChartLevelRef.value", self.value)


@dataclass(frozen=True, slots=True)
class SetupInputs:
    """The inputs a detector used, other than the source Node (Req 10.12).

    Together with :class:`SourceNodeRef` (metric, strike, value, converted
    level) and ``CandidateSetup.detector_id`` this is the full Req 10.12 list.

    - ``map_as_of``: one entry per Snapshot in the Map_State, sorted by
      ``(symbol, metric)``.
    - ``conversion_factor``: the offset in points or the ratio, per
      ``conversion_method``; ``MissingPrice`` when the Level_Converter had no
      usable price. ``band_half_width_pts`` likewise (the NQ width needs prices).
    - ``stop_rule``: the rule applied, after any one-Node-beyond fallback to
      fixed ticks (Req 10.10). ``None`` only for an unpriced Candidate_Setup.
    - ``chart_levels``: the chart levels the detector's parameters reference,
      with distinct names.
    """

    map_as_of: tuple[SnapshotAsOf, ...]
    source_spot: float
    futures_price: Ticks | MissingPrice
    conversion_method: ConversionMethod
    conversion_factor: float | MissingPrice
    band_half_width_pts: float | MissingPrice
    regime: Regime | MissingInput
    map_grade: MapGrade | MissingInput
    stop_rule: StopRule | None
    chart_levels: tuple[ChartLevelRef, ...] = ()

    def __post_init__(self) -> None:
        keys = [(s.symbol, s.metric) for s in self.map_as_of]
        if any(a >= b for a, b in pairwise(keys)):
            raise ValueError("SetupInputs.map_as_of must be sorted by (symbol, metric), no repeats")
        _require_finite("SetupInputs.source_spot", self.source_spot)
        if not isinstance(self.futures_price, MissingPrice):
            _require_int("SetupInputs.futures_price", self.futures_price)
        _require_member("SetupInputs.conversion_method", self.conversion_method, CONVERSION_METHODS)
        if not isinstance(self.conversion_factor, MissingPrice):
            _require_finite("SetupInputs.conversion_factor", self.conversion_factor)
        if not isinstance(self.band_half_width_pts, MissingPrice):
            _require_finite("SetupInputs.band_half_width_pts", self.band_half_width_pts)
            if self.band_half_width_pts < 0:
                raise ValueError("SetupInputs.band_half_width_pts must not be negative")
        if not isinstance(self.regime, MissingInput):
            _require_member("SetupInputs.regime", self.regime, REGIMES)
        if not isinstance(self.map_grade, MissingInput):
            _require_member("SetupInputs.map_grade", self.map_grade, MAP_GRADES)
        if self.stop_rule is not None:
            _require_member("SetupInputs.stop_rule", self.stop_rule, STOP_RULES)
        names = [c.name for c in self.chart_levels]
        if len(set(names)) != len(names):
            raise ValueError("SetupInputs.chart_levels names must be distinct")


@dataclass(frozen=True, slots=True)
class CandidateSetup:
    """A proposed trade at Decision_Time ``t`` (Req 10.6-10.14).

    A priced setup has an ``entry``, a ``stop`` and at least one target in
    plan order (``targets[0]`` is the first target, TP1), with
    ``stop < entry < targets[0]`` for a long and ``targets[0] < entry < stop``
    for a short (Req 10.13). A detector emits a ``DetectionSkip`` in place of
    a setup that would break this order, so the constructor refuses one.

    An unpriced setup (``source.level is None``, Req 8.11) has no entry, stop,
    targets or stop rule.
    """

    key: SetupKey
    detector_id: str
    t: Instant
    entry: Ticks | None
    stop: Ticks | None
    targets: tuple[Ticks, ...]
    exit_mode: str
    source: SourceNodeRef
    inputs: SetupInputs

    def __post_init__(self) -> None:
        _require_text("CandidateSetup.detector_id", self.detector_id)
        _require_text("CandidateSetup.exit_mode", self.exit_mode)
        _require_int("CandidateSetup.t", self.t)
        if self.source.strike != self.key.source_strike:
            raise ValueError("CandidateSetup.source.strike must equal key.source_strike")
        if not self.source.priced:
            if (
                self.entry is not None
                or self.stop is not None
                or self.targets
                or self.inputs.stop_rule is not None
            ):
                raise ValueError(
                    "an unpriced CandidateSetup has no entry, stop, targets or stop rule"
                )
            return
        if self.entry is None or self.stop is None or self.inputs.stop_rule is None:
            raise ValueError("a priced CandidateSetup needs an entry, a stop and a stop rule")
        _require_int("CandidateSetup.entry", self.entry)
        _require_int("CandidateSetup.stop", self.stop)
        if not self.targets:
            raise ValueError("a priced CandidateSetup needs at least one target")
        for target in self.targets:
            _require_int("CandidateSetup.targets entry", target)
        sign = direction_sign(self.key.direction)
        if not sign * self.stop < sign * self.entry < sign * self.targets[0]:
            raise ValueError(
                f"CandidateSetup {self.key.direction} breaks the price order: "
                f"stop {self.stop}, entry {self.entry}, first target {self.targets[0]}"
            )

    @property
    def priced(self) -> bool:
        """Whether the source Node has a converted level (no ``MissingPrice``)."""
        return self.source.priced


@dataclass(frozen=True, slots=True)
class DetectionSkip:
    """Emitted in place of a Candidate_Setup that fails validation (Req 10.14)."""

    key: SetupKey
    t: Instant
    condition: SkipCondition

    def __post_init__(self) -> None:
        _require_int("DetectionSkip.t", self.t)
        _require_member("DetectionSkip.condition", self.condition, SKIP_CONDITIONS)


@dataclass(frozen=True, slots=True)
class Order:
    """One order (design §12-13).

    ``price`` is the limit or stop price in ticks, ``None`` exactly for a
    market order. ``placed_at`` is the Decision_Time it was issued: it fills
    only on bars that open at or after it (Req 5.5). ``setup_key`` is ``None``
    for an order no Candidate_Setup owns.
    """

    client_id: str
    setup_key: SetupKey | None
    instrument: str
    side: Side
    kind: OrderKind
    qty: int
    price: Ticks | None
    placed_at: Instant
    role: OrderRole

    def __post_init__(self) -> None:
        _require_text("Order.client_id", self.client_id)
        _require_text("Order.instrument", self.instrument)
        _require_member("Order.side", self.side, SIDES)
        _require_member("Order.kind", self.kind, ORDER_KINDS)
        _require_member("Order.role", self.role, ORDER_ROLES)
        _require_int("Order.qty", self.qty, minimum=1)
        _require_int("Order.placed_at", self.placed_at)
        if self.kind == "market":
            if self.price is not None:
                raise ValueError("a market Order has no price")
        elif self.price is None:
            raise ValueError(f"a {self.kind} Order needs a price")
        else:
            _require_int("Order.price", self.price)


@dataclass(frozen=True, slots=True)
class Fill:
    """One fill of order ``client_id`` on the bar that opens at ``bar_open_ns``.

    ``fees`` = contracts x (commission + exchange fee) for this fill (Req 13.7).
    """

    client_id: str
    bar_open_ns: Instant
    price: Ticks
    qty: int
    fees: Money

    def __post_init__(self) -> None:
        _require_text("Fill.client_id", self.client_id)
        _require_int("Fill.bar_open_ns", self.bar_open_ns)
        _require_int("Fill.price", self.price)
        _require_int("Fill.qty", self.qty, minimum=1)
        _require_decimal("Fill.fees", self.fees)
        if self.fees < 0:
            raise ValueError(f"Fill.fees must not be negative, got {self.fees}")


@dataclass(frozen=True, slots=True)
class Trade:
    """One closed trade; direction and instrument come from ``setup_key``.

    The Fill_Simulator computes the figures exactly; this type checks shape:

    - ``net`` = the sum over ``exits`` of ``(exit.price - entry_fill.price)``
      x direction sign x tick value x ``exit.qty``, minus the fees of the
      entry fill and every exit fill (Req 13.10).
    - ``r`` = ``|entry_fill.price - initial_stop|`` x tick value x
      ``qty_at_entry``, always above 0.
    - ``r_multiple`` = ``net / r``: a full exit at the initial stop with zero
      fees is exactly ``-1`` (Req 13.11).
    - ``mae_r`` and ``mfe_r``: maximum adverse and favorable excursion in R,
      both at least 0 (Req 20.6).
    - ``missing_bars``: 1-minute RTH bars missing while open (Req 13.12).
    - ``shadow``: a Shadow_Trade of a rejected Candidate_Setup.
    """

    setup_key: SetupKey
    entry_fill: Fill
    initial_stop: Ticks
    qty_at_entry: int
    exits: tuple[Fill, ...]
    net: Money
    r: Money
    r_multiple: Decimal
    reached_tp1: bool
    mae_r: Decimal
    mfe_r: Decimal
    missing_bars: int
    shadow: bool

    def __post_init__(self) -> None:
        _require_int("Trade.initial_stop", self.initial_stop)
        _require_int("Trade.qty_at_entry", self.qty_at_entry, minimum=1)
        if not self.exits:
            raise ValueError("a closed Trade needs at least one exit fill")
        exited = sum(f.qty for f in self.exits)
        if exited != self.qty_at_entry:
            raise ValueError(
                f"Trade exits total {exited} contracts but qty_at_entry is {self.qty_at_entry}"
            )
        for name in ("net", "r", "r_multiple", "mae_r", "mfe_r"):
            _require_decimal(f"Trade.{name}", getattr(self, name))
        if self.r <= 0:
            raise ValueError(f"Trade.r must be above 0, got {self.r}")
        if self.mae_r < 0 or self.mfe_r < 0:
            raise ValueError("Trade.mae_r and mfe_r must not be negative")
        _require_int("Trade.missing_bars", self.missing_bars, minimum=0)
