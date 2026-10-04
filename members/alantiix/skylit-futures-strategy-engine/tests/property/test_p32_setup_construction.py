"""Property 32: Setup construction.

*For any* detection context and detector parameters, every Candidate_Setup's
source Node comes from the configured source symbol for its level type; it is
emitted only when |Futures_Price - level| <= the arming distance; its entry is
the level moved by the offset, capped at the band edge and rounded toward the
level; its stop follows the active stop rule (one-Node-beyond with fixed-ticks
fallback, or fixed ticks rounded away from entry); its Tap sequence number is
1 + Taps of the source Node ended before the Decision_Time; and its attached
inputs equal the context values.

The reference model restates Requirement 10 criteria 5-12 on whole ticks and
exact rationals. A config number is the decimal it was written as (``0.35``
is 7/20, as in ``fse.engine.targets``); a context number (a band edge, a
conversion factor, a paired futures close) is its exact binary value.

- sources (10.5): ES levels only from ``data.es_source_symbol`` and NQ levels
  only from ``data.nq_source_symbol``, in the detector's metric, ES first;
- arming (10.6): |F - L| <= ``es_pts`` x 4 ticks for ES levels and
  ``nq_qqq_usd`` x (the QQQ ratio of the same metric) x 4 ticks for NQ levels,
  F the Futures_Price of the configured instrument; no Futures_Price, or no
  QQQ ratio for an NQ level, means no setup;
- entry (10.7): L + sign x offset, clamped to the band [lo, hi] and then
  rounded to a tick toward L;
- stop rule: the detector's ``stop_rule``, else the ``exits`` setting of the
  setup's Regime (Req 12.2);
- one-Node-beyond (10.8, 10.10): the level of the nearest other Node of the
  source Snapshot strictly beyond the invalidation level (L, or the band edge
  on the stop side) and at most ``stop_lookout_pct`` percent of the paired
  futures close from it; with none, fixed ticks;
- fixed ticks (10.9): lo - n for a long, hi + n for a short, rounded to a
  tick away from entry;
- a stop not on the risk side of entry gives ``DetectionSkip(key, t,
  "price_order")`` in place of the setup (10.14);
- Setup_Key (10.11): (instrument, pattern, strike, direction, session,
  1 + the source Node's session Taps that ended before t); the Empty_Basement
  variant's pattern is ``floor_ceiling_bounce`` (10.2);
- inputs (10.12): the detector id, every Snapshot ``asOf`` sorted by (symbol,
  metric), the source spot, the Futures_Price, the source Node's metric,
  strike, value, level and band, the conversion method and factor, the band
  half-width, the Regime, the Map_Grade and the stop rule applied; no
  detector parameter references a chart level;
- an unpriced source (``MissingPrice``, Req 8.11) gives an unpriced
  Candidate_Setup for every pick, armed or not.

Two checks per detector and example: :func:`construct` on every Node of every
source in both directions, and the detector's own output, which must be the
reference outcome of each of its picks that is emitted, in source then pick
order (Property 31 covers which Nodes a detector picks).

Generators: every (symbol, metric) of SPX, SPY, QQQ, NDX and NDXP in gamma
and vanna is absent, unavailable or a Snapshot of 2 to 8 strikes whose
converted map is priced (7 in 8) or ``MissingPrice``. Converted levels are
drawn directly in ticks, non-decreasing in strike order, with gaps that are
anywhere, tiny, or on (or a tick past) the shared detector configuration's
one-Node-beyond lookout from the level and from the band edge. The paired
futures close is a round hundred or any tick; the half-width a quarter
point, a short decimal or any float. The source symbols, instruments,
SPY/NDX/NDXP methods, Regime, Map_Grade and the per-Regime stop rules vary;
each detector takes the shared configuration (3 in 4) or its own. Futures
prices sit on, a tick inside, a tick outside or anywhere around the arming
distance of a source level, and the other instrument of each family gets a
decoy price. Taps (1 to 3 per Node, the last one ended or not) land on
random (symbol, metric, strike) triples, so other metrics and symbols of the
same strike carry Taps too. Exit_Modes are Fixed_R and Opposition_Or_Fixed_R,
which always plan a first target beyond entry, so a Detection_Skip here
comes only from the stop (Property 33 covers the target-side skips and
Property 37 the target prices). An explicit example puts a Node exactly on
the lookout distance, 0.35% of a 6000.00 paired close.

**Validates: Requirements 10.5, 10.6, 10.7, 10.8, 10.9, 10.10, 10.11, 10.12**
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, time
from fractions import Fraction
from typing import Any, Final

from hypothesis import event, example, given
from hypothesis import strategies as st

from fse.config.schema.data import DataConfig
from fse.config.schema.exits import ExitsConfig
from fse.config.schema.levels import LevelsConfig
from fse.config.schema.nodes import NodesConfig
from fse.config.schema.patterns import DETECTOR_IDS, DetectorConfig, PatternsConfig
from fse.engine.chart import ChartFeatures
from fse.engine.levels import Conversion, ConvertedMap, band, conversion_method, level_family
from fse.engine.nodes import NodeLabels, NodeParams, classify
from fse.engine.regime import RegimeResult
from fse.engine.setups.registry import (
    REGISTRY,
    DetectContext,
    Detection,
    DetectorParams,
    PatternDetector,
    Source,
    construct,
    sources,
)
from fse.engine.taps import Tap, TapView
from fse.engine.types import (
    ConversionMethod,
    DetectionSkip,
    Direction,
    MapGrade,
    Metric,
    MissingInput,
    MissingPrice,
    Regime,
    SetupInputs,
    SetupKey,
    SkipCondition,
    Snapshot,
    SnapshotAsOf,
    SourceNodeRef,
    StopRule,
    Ticks,
    Unavailable,
)
from fse.pit.protocols import MapState, SymMetric
from fse.timekit import NS_PER_SECOND, Instant, ny_instant

SESSION: Final = date(2026, 3, 5)
T0: Final = ny_instant(SESSION, time(10, 0))
MINUTE: Final = 60 * NS_PER_SECOND
NODE_PARAMS: Final = NodeParams.from_config(NodesConfig())
TICKS: Final = 4  # ticks per point

DIRECTIONS: Final[tuple[Direction, ...]] = ("long", "short")
METRICS: Final[tuple[Metric, ...]] = ("gamma", "vanna")
ES_SYMBOLS: Final = ("SPX", "SPY")
NQ_SYMBOLS: Final = ("QQQ", "NDX", "NDXP")
REGIMES: Final[tuple[Regime, ...]] = (
    "Positive_Gamma",
    "Negative_Gamma",
    "Vanna_Dominant",
    "Whipsaw",
    "Structureless",
)
MAP_GRADES: Final[tuple[MapGrade, ...]] = ("A_Plus_Map", "Neutral_Map", "F_Map")
STOP_RULES: Final[tuple[StopRule, ...]] = ("one_node_beyond", "fixed_ticks")
TARGET_MODES: Final = ("fixed_r", "opposition_or_fixed_r")  # always a target beyond entry
FIXED_METHODS: Final[Mapping[str, ConversionMethod]] = {"SPX": "offset", "QQQ": "ratio"}
EMPTY_BASEMENT_ID: Final = "floor_ceiling_bounce_empty_basement"

# Per symbol: the base spot and the strike step, in symbol units.
SCALE: Final[Mapping[str, tuple[float, float]]] = {
    "SPX": (5800.0, 5.0),
    "SPY": (580.0, 1.0),
    "QQQ": (500.0, 1.0),
    "NDX": (20000.0, 25.0),
    "NDXP": (20000.0, 25.0),
}
CENTER_PTS: Final[Mapping[str, int]] = {"ES": 5800, "NQ": 20000}
MISSING_REGIME: Final = MissingInput(("SPX vanna Snapshot",))
MISSING_GRADE: Final = MissingInput(("SPX gamma Snapshot",))


def dec(x: float) -> Fraction:
    """A config number as the decimal it was written as: ``0.35`` is 7/20."""
    return Fraction(repr(x))


# ---------------------------------------------------------------- scenario


@dataclass(frozen=True, slots=True)
class MapSpec:
    """One Snapshot and its converted map; ``close`` is ``None`` for ``MissingPrice``.

    ``levels`` (ticks) are parallel to ``strikes``; ``close`` is the paired
    futures close in points.
    """

    symbol: str
    metric: Metric
    as_of_ns: Instant
    spot: float
    strikes: tuple[float, ...]
    values: tuple[float, ...]
    levels: tuple[Ticks, ...]
    close: float | None
    half_width: float


@dataclass(frozen=True, slots=True)
class Scenario:
    ctx: DetectContext
    p: DetectorParams


def snapshot_of(m: MapSpec) -> Snapshot:
    return Snapshot(
        symbol=m.symbol,
        metric=m.metric,
        view_id="v",
        as_of_ns=m.as_of_ns,
        as_of_raw="2026-03-05T14:59:00Z",
        spot=m.spot,
        previous_close=None,
        strikes=m.strikes,
        values=m.values,
        node_types=None,
        expirations=("2026-03-05",),
        resolution="1s",
        source_endpoint="range",
        extra_json="{}",
    )


def converted_of(m: MapSpec, p: DetectorParams) -> ConvertedMap | MissingPrice:
    """The Level_Converter output for ``m``, built from its tick levels."""
    if m.close is None:
        return MissingPrice(m.symbol)
    family = level_family(m.symbol)
    method = conversion_method(m.symbol, p.level_params)
    conv = Conversion(
        symbol=m.symbol,
        family=family,
        instrument=p.level_params.instrument(family),
        contract="H6",
        method=method,
        factor=m.close - m.spot if method == "offset" else m.close / m.spot,
        futures_close=m.close,
        spot=m.spot,
        as_of_ns=m.as_of_ns,
        paired_bar_close_ns=m.as_of_ns,
    )
    return ConvertedMap(
        symbol=m.symbol,
        metric=m.metric,
        as_of_ns=m.as_of_ns,
        conversion=conv,
        band_half_width_pts=m.half_width,
        strikes=m.strikes,
        levels=m.levels,
        bands=tuple(band(level, m.half_width) for level in m.levels),
    )


def params_of(
    patterns: Mapping[str, Any],
    exits: Mapping[str, Any],
    data: Mapping[str, Any],
    methods: Mapping[str, str],
) -> DetectorParams:
    """The ``patterns``, ``exits``, ``data`` and ``levels`` sections as a config file gives them."""
    return DetectorParams(
        patterns=PatternsConfig.model_validate(patterns),
        data=DataConfig.model_validate(data),
        levels=LevelsConfig.model_validate({"methods": methods}),
        exits=ExitsConfig.model_validate(exits),
    )


def build(
    p: DetectorParams,
    maps: tuple[MapSpec, ...],
    unavailable: tuple[SymMetric, ...],
    regime: Regime | None,
    grade: MapGrade | None,
    futures: Mapping[str, Ticks],
    taps: tuple[Tap, ...],
) -> Scenario:
    entries: dict[SymMetric, Snapshot | Unavailable] = {
        key: Unavailable("not fetched") for key in unavailable
    }
    snaps = {(m.symbol, m.metric): (snapshot_of(m), converted_of(m, p)) for m in maps}
    entries.update({key: s for key, (s, _) in snaps.items()})
    ordered = tuple(sorted(taps, key=lambda tap: (tap.first_open_ns, tap.node)))
    ctx = DetectContext(
        t=T0,
        map_state=MapState(T0, entries),
        labels={key: classify(s, NODE_PARAMS) for key, (s, _) in snaps.items()},
        converted={key: c for key, (_, c) in snaps.items()},
        regime=(
            MISSING_REGIME
            if regime is None
            else RegimeResult(regime, 1.0, 0.5, 1.0, 0.5, -1.0, False)
        ),
        grade=MISSING_GRADE if grade is None else grade,
        chart=ChartFeatures(T0, ()),
        taps=TapView(T0, SESSION, ordered, {}, {}),
        futures_price=futures,
    )
    return Scenario(ctx, p)


# ---------------------------------------------------------------- reference model


def arming_reach(
    cfg: DetectorConfig, family: str, qqq: ConvertedMap | MissingPrice | None
) -> Fraction | None:
    """The arming distance in ticks (Req 10.6); ``None`` for NQ with no QQQ ratio."""
    if family == "ES":
        return dec(cfg.arming.es_pts) * TICKS
    if not isinstance(qqq, ConvertedMap):
        return None
    return dec(cfg.arming.nq_qqq_usd) * Fraction(qqq.conversion.factor) * TICKS


def lookout_reach(pct: float, close: float) -> Fraction:
    """The one-Node-beyond lookout in ticks: ``pct`` percent of the paired close (Req 10.10)."""
    return dec(pct) / 100 * Fraction(close) * TICKS


def regime_of(ctx: DetectContext) -> Regime | MissingInput:
    return ctx.regime.regime if isinstance(ctx.regime, RegimeResult) else ctx.regime


def requested_rule(
    cfg: DetectorConfig, p: DetectorParams, regime: Regime | MissingInput
) -> StopRule:
    """The detector's stop rule, else the ``exits`` one for the Regime (Req 12.2)."""
    if cfg.stop_rule is not None:
        return cfg.stop_rule
    per = None if isinstance(regime, MissingInput) else p.exits.per_regime.get(regime)
    return (p.exits.global_ if per is None else per).stop_rule


def source_specs(p: DetectorParams) -> tuple[tuple[str, str, str], ...]:
    """(family, source symbol, instrument), ES first (Req 10.5-10.6)."""
    d = p.data
    return (
        ("ES", d.es_source_symbol, d.instruments.es_levels),
        ("NQ", d.nq_source_symbol, d.instruments.nq_levels),
    )


type SourceView = tuple[str, str, Metric, str, Snapshot, NodeLabels, ConvertedMap | MissingPrice]


def expected_sources(ctx: DetectContext, p: DetectorParams, metric: Metric) -> list[SourceView]:
    out: list[SourceView] = []
    for family, symbol, instrument in source_specs(p):
        snap = ctx.map_state.entries.get((symbol, metric))
        if isinstance(snap, Snapshot):
            key = (symbol, metric)
            out.append(
                (family, symbol, metric, instrument, snap, ctx.labels[key], ctx.converted[key])
            )
    return out


def view(s: Source) -> SourceView:
    return (s.family, s.symbol, s.metric, s.instrument, s.snapshot, s.labels, s.converted)


@dataclass(frozen=True, slots=True)
class Outcome:
    """What Req 10.5-10.12 fix about one detection; ``skip`` is set for a Detection_Skip."""

    key: SetupKey
    t: Instant
    skip: SkipCondition | None = None
    detector_id: str | None = None
    entry: Ticks | None = None
    stop: Ticks | None = None
    source: SourceNodeRef | None = None
    inputs: SetupInputs | None = None


def outcome(x: Detection | None) -> Outcome | None:
    if x is None:
        return None
    if isinstance(x, DetectionSkip):
        return Outcome(x.key, x.t, skip=x.condition)
    return Outcome(x.key, x.t, None, x.detector_id, x.entry, x.stop, x.source, x.inputs)


def expect(
    detector_id: str,
    cfg: DetectorConfig,
    ctx: DetectContext,
    p: DetectorParams,
    family: str,
    pick: tuple[float, Direction],
) -> Outcome | None:
    """The detection Req 10.5-10.12 (and 8.11, 10.14) give for one pick; ``None``: not armed."""
    symbol, instrument = next((s, i) for f, s, i in source_specs(p) if f == family)
    metric = cfg.metric
    snap = ctx.map_state.entries[(symbol, metric)]
    assert isinstance(snap, Snapshot)
    labels = ctx.labels[(symbol, metric)]
    strike, direction = pick
    sign = 1 if direction == "long" else -1
    value = snap.values[snap.strikes.index(strike)]

    # 10.11: the Setup_Key.
    node = (symbol, metric, strike)
    ended = sum(
        1
        for tap in ctx.taps.taps
        if tap.node == node and tap.session == ctx.taps.session and tap.ended and tap.end_ns < ctx.t
    )
    pattern = "floor_ceiling_bounce" if detector_id == EMPTY_BASEMENT_ID else detector_id
    key = SetupKey(instrument, pattern, strike, direction, ctx.taps.session, 1 + ended)

    # 10.12: the context values every setup carries.
    regime = regime_of(ctx)
    as_of = tuple(
        sorted(
            (SnapshotAsOf(s.symbol, s.metric, s.as_of_ns) for s in ctx.map_state.snapshots()),
            key=lambda a: (a.symbol, a.metric),
        )
    )
    price = ctx.futures_price.get(instrument)
    cm = ctx.converted[(symbol, metric)]

    if isinstance(cm, MissingPrice):  # Req 8.11: unpriced, whatever the price
        method = FIXED_METHODS.get(symbol) or getattr(p.levels.methods, symbol)
        unpriced = SetupInputs(
            map_as_of=as_of,
            source_spot=snap.spot,
            futures_price=MissingPrice(instrument) if price is None else price,
            conversion_method=method,
            conversion_factor=cm,
            band_half_width_pts=cm,
            regime=regime,
            map_grade=ctx.grade,
            stop_rule=None,
        )
        ref = SourceNodeRef(symbol, metric, strike, value, None, None)
        return Outcome(key, ctx.t, None, detector_id, None, None, ref, unpriced)

    # 10.6: arming.
    reach = arming_reach(cfg, family, ctx.converted.get(("QQQ", metric)))
    if price is None or reach is None:
        return None
    i = cm.strikes.index(strike)
    level = cm.levels[i]
    if abs(price - level) > reach:
        return None
    band_pts = cm.bands[i]
    lo, hi = Fraction(band_pts[0]) * TICKS, Fraction(band_pts[1]) * TICKS

    # 10.7: the entry.
    capped = min(max(Fraction(level + sign * cfg.entry_offset_ticks), lo), hi)
    entry = math.floor(capped) if capped > level else math.ceil(capped)

    # 10.8-10.10: the stop.
    rule = requested_rule(cfg, p, regime)
    stop: Ticks | None = None
    if rule == "one_node_beyond":
        inv = Fraction(level) if cfg.invalidation == "level" else (lo if sign > 0 else hi)
        lookout = lookout_reach(cfg.stop_lookout_pct, cm.conversion.futures_close)
        nearest: Fraction | None = None
        for n in labels.nodes:
            other = cm.levels[cm.strikes.index(n)]
            distance = sign * (inv - other)  # > 0: beyond the invalidation level
            if n != strike and 0 < distance <= lookout and (nearest is None or distance < nearest):
                nearest, stop = distance, other
        if stop is None:
            rule = "fixed_ticks"
    if stop is None:
        n_ticks = cfg.fixed_stop_ticks
        stop = math.floor(lo - n_ticks) if sign > 0 else math.ceil(hi + n_ticks)

    # 10.14: a stop not on the risk side of entry.
    if not sign * stop < sign * entry:
        return Outcome(key, ctx.t, skip="price_order")

    inputs = SetupInputs(
        map_as_of=as_of,
        source_spot=snap.spot,
        futures_price=price,
        conversion_method=cm.conversion.method,
        conversion_factor=cm.conversion.factor,
        band_half_width_pts=cm.band_half_width_pts,
        regime=regime,
        map_grade=ctx.grade,
        stop_rule=rule,
    )
    ref = SourceNodeRef(symbol, metric, strike, value, level, band_pts)
    return Outcome(key, ctx.t, None, detector_id, entry, stop, ref, inputs)


def kinds_of(
    o: Outcome | None, cfg: DetectorConfig, ctx: DetectContext, p: DetectorParams
) -> set[str]:
    """Event labels for one reference outcome."""
    if o is None:
        return {"not armed"}
    if o.skip is not None:
        return {f"skip: {o.skip}"}
    out = {"tap_seq > 1"} if o.key.tap_seq > 1 else set()
    if o.inputs is None or o.inputs.stop_rule is None or o.source is None:
        return out | {"unpriced"}
    applied: str = o.inputs.stop_rule
    if applied != requested_rule(cfg, p, regime_of(ctx)):
        applied = "fixed_ticks (one_node_beyond fallback)"
    out.add(f"setup: {applied}")
    sign = 1 if o.key.direction == "long" else -1
    level = o.source.level
    if level is not None and o.entry != level + sign * cfg.entry_offset_ticks:
        out.add("entry capped at the band")
    return out


# ---------------------------------------------------------------- generators


def decimals(lo: int, hi: int, places: int) -> st.SearchStrategy[float]:
    """``lo`` to ``hi`` units of ``10 ** -places``, as the float a config file gives."""
    return st.integers(lo, hi).map(lambda n: float(Fraction(n, 10**places)))


SIGNS: Final = st.sampled_from((1, -1))
MAGNITUDES: Final = st.sampled_from((3.0e9, 2.0e9, 1.5e9, 1.0e9, 7.0e8, 4.0e8, 1.0e8, 5.0e6))
HALF_WIDTHS: Final = st.one_of(
    st.just(5.0),
    st.integers(1, 100).map(lambda k: k / 4),
    decimals(25, 2500, 2),
    st.floats(0.25, 50.0, allow_nan=False, allow_infinity=False),
)


@st.composite
def detector_configs(draw: st.DrawFn) -> dict[str, Any]:
    """The shared ``patterns`` keys of one detector, anywhere in their valid ranges."""
    return {
        "metric": draw(st.sampled_from(METRICS)),
        "entry_offset_ticks": draw(
            st.one_of(st.just(0), st.integers(-12, 12), st.integers(-80, 80))
        ),
        "stop_rule": draw(st.sampled_from((None, *STOP_RULES))),
        "fixed_stop_ticks": draw(st.one_of(st.just(8), st.integers(1, 200))),
        "invalidation": draw(st.sampled_from(("level", "band_edge"))),
        "stop_lookout_pct": draw(
            st.one_of(st.just(1.0), decimals(1, 300, 2), decimals(1, 1000, 1))
        ),
        "arming": {
            "es_pts": draw(st.one_of(st.just(10.0), decimals(1, 10_000, 2))),
            "nq_qqq_usd": draw(st.one_of(st.just(1.0), decimals(1, 1000, 2))),
        },
    }


@st.composite
def map_specs(draw: st.DrawFn, symbol: str, metric: Metric, ref: DetectorConfig) -> MapSpec:
    family = level_family(symbol)
    base, step = SCALE[symbol]
    index = sorted(draw(st.lists(st.integers(-30, 30), min_size=2, max_size=8, unique=True)))
    strikes = tuple(base + step * i for i in index)
    spot = base + step * draw(st.integers(-300, 300)) / 10
    values = tuple(float(draw(SIGNS)) * draw(MAGNITUDES) for _ in strikes)
    center = CENTER_PTS[family]
    close = draw(
        st.one_of(
            st.integers(-20, 20).map(lambda n: float(center + 100 * n)),
            st.integers(-2000, 2000).map(lambda n: center + n / 4),
        )
    )
    half_width = draw(HALF_WIDTHS)

    # Gaps on (or a tick past) the lookout from the level and from the band edge.
    reach = lookout_reach(ref.stop_lookout_pct, close)
    edge = Fraction(half_width) * TICKS
    marks = sorted({math.floor(reach), math.floor(reach + edge)})
    gaps = st.one_of(
        st.integers(1, 3),
        st.integers(0, 160),
        st.sampled_from(marks).flatmap(lambda m: st.integers(m, m + 1)),
    )
    levels = [round(close * TICKS) + draw(st.integers(-300, 300))]
    for _ in strikes[1:]:
        levels.append(levels[-1] + draw(gaps))
    priced = draw(st.integers(0, 7)) > 0
    return MapSpec(
        symbol=symbol,
        metric=metric,
        as_of_ns=T0 - draw(st.integers(1, 300)) * NS_PER_SECOND,
        spot=spot,
        strikes=strikes,
        values=values,
        levels=tuple(levels),
        close=close if priced else None,
        half_width=half_width,
    )


@st.composite
def scenarios(draw: st.DrawFn) -> Scenario:
    # Configuration: sources, instruments, methods, detectors and exits.
    es_symbol = draw(st.sampled_from(ES_SYMBOLS))
    nq_symbol = draw(st.sampled_from(NQ_SYMBOLS))
    es_instrument, es_decoy = draw(st.sampled_from((("MES", "ES"), ("ES", "MES"))))
    nq_instrument, nq_decoy = draw(st.sampled_from((("MNQ", "NQ"), ("NQ", "MNQ"))))
    shared = draw(detector_configs())
    patterns = {
        d: shared if draw(st.integers(0, 3)) else draw(detector_configs()) for d in DETECTOR_IDS
    }
    setting = st.fixed_dictionaries(
        {"mode": st.sampled_from(TARGET_MODES), "stop_rule": st.sampled_from(STOP_RULES)}
    )
    exits = {
        "global": draw(setting),
        "per_regime": {r: draw(st.none() | setting) for r in REGIMES},
    }
    data = {
        "es_source_symbol": es_symbol,
        "nq_source_symbol": nq_symbol,
        "instruments": {"es_levels": es_instrument, "nq_levels": nq_instrument},
    }
    methods = {s: draw(st.sampled_from(("offset", "ratio"))) for s in ("SPY", "NDX", "NDXP")}
    p = params_of(patterns, exits, data, methods)
    ref = DetectorConfig.model_validate(shared)

    # Map_State: each (symbol, metric) absent, unavailable or a Snapshot.
    maps: list[MapSpec] = []
    unavailable: list[SymMetric] = []
    for symbol in (*ES_SYMBOLS, *NQ_SYMBOLS):
        for metric in METRICS:
            state = draw(st.integers(0, 9))
            if state == 1:
                unavailable.append((symbol, metric))
            elif state > 1:
                maps.append(draw(map_specs(symbol, metric, ref)))
    converted = {(m.symbol, m.metric): converted_of(m, p) for m in maps}

    # Futures prices around the arming distance of a source level, plus decoys.
    futures: dict[str, Ticks] = {}
    for family, symbol, instrument, decoy in (
        ("ES", es_symbol, es_instrument, es_decoy),
        ("NQ", nq_symbol, nq_instrument, nq_decoy),
    ):
        cm = converted.get((symbol, ref.metric))
        center = CENTER_PTS[family] * TICKS
        anywhere = st.integers(center - 2000, center + 2000)
        reach = arming_reach(ref, family, converted.get(("QQQ", ref.metric)))
        on_node = anywhere
        if isinstance(cm, ConvertedMap):
            spec = next(m for m in maps if (m.symbol, m.metric) == (symbol, ref.metric))
            nodes = classify(snapshot_of(spec), NODE_PARAMS).nodes
            on_node = st.sampled_from([cm.level_of(k) for k in nodes] or list(cm.levels))
        if draw(st.integers(0, 9)) > 0:
            if isinstance(cm, ConvertedMap) and reach is not None:
                n = math.floor(reach)
                level = draw(on_node)
                delta = draw(st.one_of(st.just(n), st.just(n + 1), st.integers(0, n + 40)))
                futures[instrument] = level + draw(SIGNS) * delta
            else:
                futures[instrument] = draw(anywhere)
        if draw(st.booleans()):
            futures[decoy] = draw(on_node)

    # Taps on random (symbol, metric, strike) triples of the Map_State.
    instruments = {"ES": es_instrument, "NQ": nq_instrument}
    pool = sorted({(m.symbol, m.metric, k) for m in maps for k in m.strikes})
    tapped = draw(st.lists(st.sampled_from(pool), max_size=16, unique=True)) if pool else []
    taps: list[Tap] = []
    for symbol, metric, strike in tapped:
        count = draw(st.integers(1, 3))
        last_open = draw(st.booleans())
        for seq in range(1, count + 1):
            first_open = T0 - (count - seq + 1) * 20 * MINUTE
            first_close = first_open + MINUTE
            taps.append(
                Tap(
                    symbol=symbol,
                    metric=metric,
                    strike=strike,
                    instrument=instruments[level_family(symbol)],
                    session=SESSION,
                    seq=seq,
                    first_open_ns=first_open,
                    first_close_ns=first_close,
                    end_ns=first_close + draw(st.integers(0, 10)) * MINUTE,
                    ended=not (seq == count and last_open),
                )
            )

    return build(
        p,
        tuple(maps),
        tuple(unavailable),
        draw(st.none() | st.sampled_from(REGIMES)),
        draw(st.none() | st.sampled_from(MAP_GRADES)),
        futures,
        tuple(taps),
    )


# ---------------------------------------------------------------- explicit examples

# A Node exactly 0.35% of a 6000.00 paired close (21 points, 84 ticks) below a
# long's level is within the one-Node-beyond lookout (Req 10.10), so it is the stop.
LOOKOUT_BOUNDARY: Final = build(
    params_of(
        {d: {"stop_rule": "one_node_beyond", "stop_lookout_pct": 0.35} for d in DETECTOR_IDS},
        {"global": {"mode": "fixed_r", "stop_rule": "one_node_beyond"}},
        {},
        {},
    ),
    (
        MapSpec(
            symbol="SPX",
            metric="gamma",
            as_of_ns=T0 - 30 * NS_PER_SECOND,
            spot=6000.0,
            strikes=(5979.0, 6000.0),
            values=(3.0e9, 3.0e9),
            levels=(23916, 24000),
            close=6000.0,
            half_width=5.0,
        ),
    ),
    (),
    "Negative_Gamma",
    "Neutral_Map",
    {"MES": 24000},
    (),
)


# ---------------------------------------------------------------- property


# Feature: skylit-futures-strategy-engine, Property 32: Setup construction
@example(s=LOOKOUT_BOUNDARY)
@given(s=scenarios())
def test_setups_follow_the_requirement_10_construction_rules(s: Scenario) -> None:
    ctx, p = s.ctx, s.p
    kinds: set[str] = set()
    for d in REGISTRY:
        assert isinstance(d, PatternDetector)
        cfg = d.config_of(p.patterns)

        found = sources(ctx, p, cfg.metric)
        assert [view(x) for x in found] == expected_sources(ctx, p, cfg.metric)  # 10.5

        # Every Node of every source, in both directions.
        for src in found:
            for strike in src.labels.nodes:
                for direction in DIRECTIONS:
                    pick = (strike, direction)
                    want = expect(d.id, cfg, ctx, p, src.family, pick)
                    got = construct(d.id, d.pattern, cfg, ctx, p, src, pick)
                    assert outcome(got) == want, (d.id, src.symbol, pick)
                    kinds |= kinds_of(want, cfg, ctx, p)

        # The detector's own output: each emitted pick, in source then pick order.
        wanted = [
            want
            for src in found
            for pick in d.select(ctx, src, cfg)
            if (want := expect(d.id, cfg, ctx, p, src.family, pick)) is not None
        ]
        assert [outcome(x) for x in d.detect(ctx, p)] == wanted, d.id

    for k in sorted(kinds):
        event(k)
