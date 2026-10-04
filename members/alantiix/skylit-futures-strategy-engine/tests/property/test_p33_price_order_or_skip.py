"""Property 33: Price order or skip.

*For any* detection context, every emitted long Candidate_Setup has
stop < entry < first target and every short has first target < entry < stop;
every case that would break this order or has no target produces a
Detection_Skip naming the Setup_Key, Decision_Time and failed condition
instead.

Each armed pick (a source Node and a direction) has a reference outcome built
from Requirement 10 criteria 6-11 and 13-14, on whole ticks:

- armed (10.6) when |Futures_Price - L| is at most the arming distance (ES
  points, or NQ ``nq_qqq_usd`` x the QQQ ratio), as an exact rational; an
  unarmed pick emits nothing;
- entry (10.7): ``L + sign x offset``, capped at the band edge rounded to a
  tick toward ``L`` (band edges taken exactly);
- stop (10.8-10.10): under the detector's stop rule, else the ``exits``
  setting's for the Regime; one-Node-beyond is the nearest other Node level
  strictly beyond the invalidation level on the stop side within the lookout
  (``stop_lookout_pct`` percent of the paired futures close, exactly), else
  fixed ticks beyond the band edge, rounded away from entry;
- Setup_Key (10.11): instrument, Pattern, strike, direction, session and
  1 + the Node's ended Taps the scenario put in the TapView;
- validation (10.13-10.14): a stop not strictly on the risk side of entry is a
  ``price_order`` skip. The targets come from ``plan_targets`` under the
  Regime's Exit_Mode, the one target rule design §10 step 4 names (Property 37
  checks it against Requirement 12). No target price (Trailing, or a missing
  Node target) is a ``no_target`` skip, a TP2 not beyond TP1 a
  ``price_order`` skip (the targets break plan order), and a first target not
  beyond entry a ``price_order`` skip. Otherwise the outcome is a
  Candidate_Setup with that stop, entry and targets.

A config number (an arming distance, a lookout percent) is the decimal it was
written as (``0.35`` is 7/20, as in ``fse.engine.targets``); a context number
(the QQQ ratio, the paired futures close, a band edge) is its exact binary
value.

Two properties. The first calls ``construct`` with any strike of a source
Snapshot and either direction, so picks the detectors would never make (a long
far above spot, a Node inside another's band) are covered. The second runs
``detect_setups`` and checks its whole output against the reference outcome
of each pick the enabled detectors' own source Node selection makes (Property
31, Property 32 and the unit tests cover that selection). Every emitted
Candidate_Setup is also checked directly for the price order, at least one
target, and a first target nearest to entry.

Generators: an SPX gamma Map (offset conversion, eighth-point offsets) and,
half the time, a QQQ gamma Map (ratio 35-45), each with 2 to 8 strikes on a
1, 2.5 or 5 point (SPX) or 0.5 or 1 dollar (QQQ) grid and band half-widths
from 0 to 15 (SPX) or 40 (NQ) points in tenths, so bands span neighbor Nodes
and band edges fall between ticks. The Futures_Price, when present, is drawn
near the level of a random strike. Detector entry offsets, stop rules,
fixed-stop ticks, invalidation levels, lookouts and arming distances, and the
``exits`` settings and mode parameters (global, per-Regime, Trailing and
TP1_Partial_BE with Node and R TP1/TP2 rules) are drawn over their schema
ranges; the Regime is any Regime or missing; some Nodes have ended Taps.

**Validates: Requirements 10.13, 10.14**
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, time
from fractions import Fraction
from typing import Any, Final

from hypothesis import event, given
from hypothesis import strategies as st

from fse.config.schema.exits import ExitsConfig
from fse.config.schema.nodes import NodesConfig
from fse.config.schema.patterns import DETECTOR_IDS, DetectorConfig, PatternsConfig
from fse.engine.chart import ChartFeatures
from fse.engine.levels import TICKS_PER_POINT, Conversion, ConvertedMap, band, level_family
from fse.engine.nodes import NodeParams, classify
from fse.engine.regime import RegimeResult
from fse.engine.setups.registry import (
    DETECTORS,
    REGISTRY,
    DetectContext,
    Detection,
    DetectorParams,
    PatternDetector,
    Pick,
    Source,
    construct,
    detect_setups,
    sources,
)
from fse.engine.taps import Tap, TapView
from fse.engine.targets import (
    TargetBasis,
    TargetContext,
    TargetRejection,
    exit_mode_for,
    plan_targets,
)
from fse.engine.types import (
    CandidateSetup,
    ConversionMethod,
    Direction,
    MissingInput,
    NoTarget,
    Regime,
    SetupKey,
    SkipCondition,
    Snapshot,
    Ticks,
)
from fse.pit.protocols import MapState, SymMetric
from fse.timekit import NS_PER_SECOND, Instant, ny_instant

SESSION: Final = date(2026, 3, 5)
T0: Final = ny_instant(SESSION, time(10, 0))
AS_OF: Final = T0 - 30 * NS_PER_SECOND
NODE_PARAMS: Final = NodeParams.from_config(NodesConfig())
NO_REGIME: Final = MissingInput(("SPX vanna Snapshot",))
DIRECTIONS: Final[tuple[Direction, ...]] = ("long", "short")
REGIMES: Final[tuple[Regime, ...]] = (
    "Positive_Gamma",
    "Negative_Gamma",
    "Vanna_Dominant",
    "Whipsaw",
    "Structureless",
)
INSTRUMENTS: Final[Mapping[str, str]] = {"SPX": "MES", "QQQ": "MNQ"}
"""The default instrument of each source symbol's levels (Req 10.6)."""


def dec(x: float) -> Fraction:
    """A config number as the decimal it was written as: ``0.35`` is 7/20."""
    return Fraction(repr(x))


# ---------------------------------------------------------------- generators


def scaled(lo: int, hi: int, places: int) -> st.SearchStrategy[float]:
    """Short decimals from ``lo`` to ``hi`` units of ``10 ** -places``, as floats."""
    return st.integers(lo, hi).map(lambda n: n / 10**places)


SIGNS: Final = st.sampled_from((1, -1))
R_MULTIPLES: Final = scaled(5, 100, 1)  # 0.5 to 10.0
TARGET_RULES: Final = st.sampled_from(("r", "node"))
SETTINGS: Final = st.fixed_dictionaries(
    {
        "mode": st.sampled_from(  # TP1_Partial_BE twice: its TP2 can fail
            (
                "fixed_r",
                "next_node",
                "tp1_partial_be",
                "tp1_partial_be",
                "opposition_or_fixed_r",
                "trailing",
            )
        ),
        "stop_rule": st.sampled_from(("one_node_beyond", "fixed_ticks")),
    }
)
EXITS: Final = st.fixed_dictionaries(
    {
        "global": SETTINGS,
        "per_regime": st.fixed_dictionaries({r: st.none() | SETTINGS for r in REGIMES}),
        "modes": st.fixed_dictionaries(
            {
                "fixed_r": st.fixed_dictionaries({"r_multiple": R_MULTIPLES}),
                "tp1_partial_be": st.fixed_dictionaries(
                    {
                        "tp1": st.fixed_dictionaries(
                            {"rule": TARGET_RULES, "r_multiple": R_MULTIPLES}
                        ),
                        "tp2": st.fixed_dictionaries(
                            {"rule": TARGET_RULES, "r_multiple": R_MULTIPLES}
                        ),
                        "tp1_fraction": scaled(10, 90, 2),
                    }
                ),
                "opposition_or_fixed_r": st.fixed_dictionaries(
                    {"r_multiple": R_MULTIPLES, "fraction": scaled(1, 100, 2)}
                ),
            }
        ),
    }
)
DETECTOR_CFGS: Final = st.fixed_dictionaries(
    {
        "enabled": st.integers(0, 3).map(lambda n: n > 0),
        "entry_offset_ticks": st.one_of(st.just(0), st.integers(-24, 24), st.integers(-80, 80)),
        "stop_rule": st.sampled_from((None, "one_node_beyond", "fixed_ticks")),
        "fixed_stop_ticks": st.one_of(st.integers(1, 12), st.integers(1, 200)),
        "invalidation": st.sampled_from(("level", "band_edge")),
        "stop_lookout_pct": st.one_of(st.just(1.0), scaled(1, 300, 2)),
        "arming": st.fixed_dictionaries(
            {
                "es_pts": st.one_of(st.just(10.0), scaled(1, 300, 1)),
                "nq_qqq_usd": st.one_of(st.just(1.0), scaled(1, 300, 2)),
            }
        ),
    }
)


@st.composite
def priced_maps(draw: st.DrawFn, symbol: str) -> tuple[Snapshot, ConvertedMap]:
    """A gamma Snapshot of ``symbol`` (SPX or QQQ) and its converted map."""
    method: ConversionMethod
    if symbol == "SPX":
        spot = draw(st.integers(16_000, 24_000)) / 4
        step = draw(st.sampled_from((1.0, 2.5, 5.0)))
        method, factor = "offset", draw(st.integers(-400, 400)) / 8
        half_width = draw(st.one_of(st.just(5.0), scaled(0, 150, 1)))
        futures_close = spot + factor
    else:
        spot = draw(st.integers(1_600, 2_400)) / 4
        step = draw(st.sampled_from((0.5, 1.0)))
        method, factor = "ratio", draw(scaled(3_500, 4_500, 2))
        half_width = draw(scaled(0, 400, 1))
        futures_close = spot * factor
    base = round(spot / step) * step
    grid = draw(st.lists(st.integers(-12, 12), min_size=2, max_size=8, unique=True))
    strikes = tuple(base + k * step for k in sorted(grid))
    values = tuple(draw(SIGNS) * draw(st.integers(1, 30)) * 1.0e8 for _ in strikes)
    snapshot = Snapshot(
        symbol=symbol,
        metric="gamma",
        view_id="v",
        as_of_ns=AS_OF,
        as_of_raw="2026-03-05T14:59:30Z",
        spot=spot,
        previous_close=None,
        strikes=strikes,
        values=values,
        node_types=None,
        expirations=("2026-03-05",),
        resolution="1s",
        source_endpoint="range",
        extra_json="{}",
    )
    instrument = INSTRUMENTS[symbol]
    conversion = Conversion(
        symbol=symbol,
        family=level_family(symbol),
        instrument=instrument,
        contract=f"{instrument}H6",
        method=method,
        factor=factor,
        futures_close=futures_close,
        spot=spot,
        as_of_ns=AS_OF,
        paired_bar_close_ns=AS_OF,
    )
    levels = tuple(conversion.level(k) for k in strikes)
    converted = ConvertedMap(
        symbol=symbol,
        metric="gamma",
        as_of_ns=AS_OF,
        conversion=conversion,
        band_half_width_pts=half_width,
        strikes=strikes,
        levels=levels,
        bands=tuple(band(level, half_width) for level in levels),
    )
    return snapshot, converted


def ended_tap(symbol: str, strike: float, seq: int) -> Tap:
    """The Node's ``seq``-th Tap of the session, ended before ``T0``."""
    start = T0 - (40 - 10 * seq) * 60 * NS_PER_SECOND
    close = start + 60 * NS_PER_SECOND
    return Tap(
        symbol, "gamma", strike, INSTRUMENTS[symbol], SESSION, seq, start, close, close, True
    )


@dataclass(frozen=True, slots=True)
class Scenario:
    """A detection context, the detector parameters, and what the generator chose.

    ``ended`` counts the ended Taps per (symbol, strike); ``focus`` is the
    strike per symbol whose level the Futures_Price was drawn near.
    """

    ctx: DetectContext
    params: DetectorParams
    regime: Regime | None
    ended: Mapping[tuple[str, float], int]
    focus: Mapping[str, float]


@st.composite
def scenarios(draw: st.DrawFn) -> Scenario:
    maps = [draw(priced_maps("SPX"))]
    if draw(st.booleans()):
        maps.append(draw(priced_maps("QQQ")))

    futures: dict[str, Ticks] = {}
    focus: dict[str, float] = {}
    ended: dict[tuple[str, float], int] = {}
    taps: list[Tap] = []
    for snap, cm in maps:
        focus[snap.symbol] = draw(st.sampled_from(snap.strikes))
        if draw(st.integers(0, 7)) > 0:  # no Futures_Price 1 time in 8
            near = draw(st.one_of(st.integers(-48, 48), st.integers(-400, 400)))
            futures[INSTRUMENTS[snap.symbol]] = cm.level_of(focus[snap.symbol]) + near
        for k in snap.strikes:
            ended[(snap.symbol, k)] = n = draw(st.sampled_from((0, 0, 0, 1, 2)))
            taps.extend(ended_tap(snap.symbol, k, seq) for seq in range(1, n + 1))

    regime = draw(st.none() | st.sampled_from(REGIMES))
    entries: dict[SymMetric, Snapshot] = {(s.symbol, s.metric): s for s, _ in maps}
    ctx = DetectContext(
        t=T0,
        map_state=MapState(T0, entries),
        labels={key: classify(s, NODE_PARAMS) for key, s in entries.items()},
        converted={(cm.symbol, cm.metric): cm for _, cm in maps},
        regime=(
            NO_REGIME if regime is None else RegimeResult(regime, 1.0, 0.5, 1.0, 0.5, -1.0, False)
        ),
        grade="Neutral_Map",
        chart=ChartFeatures(T0, ()),
        taps=TapView(T0, SESSION, tuple(sorted(taps, key=lambda t: t.first_open_ns)), {}, {}),
        futures_price=futures,
    )
    patterns: dict[str, Any] = {d: draw(DETECTOR_CFGS) for d in DETECTOR_IDS}
    params = DetectorParams(
        patterns=PatternsConfig.model_validate(patterns),
        exits=ExitsConfig.model_validate(draw(EXITS)),
    )
    return Scenario(ctx, params, regime, ended, focus)


# ---------------------------------------------------------------- reference model


@dataclass(frozen=True, slots=True)
class Outcome:
    """A Detection_Skip (``condition`` set) or a Candidate_Setup's ``(stop, entry, targets)``."""

    key: SetupKey
    t: Instant
    condition: SkipCondition | None
    prices: tuple[Ticks, Ticks, tuple[Ticks, ...]] | None


def observe(x: Detection) -> Outcome:
    if isinstance(x, CandidateSetup):
        assert x.entry is not None, "an unpriced Candidate_Setup"
        assert x.stop is not None, "an unpriced Candidate_Setup"
        return Outcome(x.key, x.t, None, (x.stop, x.entry, x.targets))
    return Outcome(x.key, x.t, x.condition, None)


def reference(
    s: Scenario, pattern: str, cfg: DetectorConfig, source: Source, pick: Pick
) -> Outcome | None:
    """The outcome of one pick by Req 10.6-10.11 and 10.13-10.14; ``None`` when unarmed."""
    strike, direction = pick
    sign = 1 if direction == "long" else -1
    cm = source.converted
    assert isinstance(cm, ConvertedMap)
    instrument = INSTRUMENTS[source.symbol]

    # Arming (10.6), exactly.
    price = s.ctx.futures_price.get(instrument)
    if price is None:
        return None
    level = cm.level_of(strike)
    if source.symbol == "SPX":
        arming = dec(cfg.arming.es_pts)
    else:
        arming = dec(cfg.arming.nq_qqq_usd) * Fraction(cm.conversion.factor)
    if abs(price - level) > arming * TICKS_PER_POINT:
        return None

    # Entry (10.7): band edges exactly, in ticks.
    lo, hi = cm.band_of(strike)
    lo_ticks, hi_ticks = Fraction(lo) * TICKS_PER_POINT, Fraction(hi) * TICKS_PER_POINT
    raw = level + sign * cfg.entry_offset_ticks
    if raw > level:
        entry = min(raw, math.floor(hi_ticks))
    elif raw < level:
        entry = max(raw, math.ceil(lo_ticks))
    else:
        entry = level

    # Stop (10.8-10.10, 12.2).
    rule = cfg.stop_rule or s.params.exits.setting_for(s.regime).stop_rule
    stop: Ticks | None = None
    if rule == "one_node_beyond":
        inv = Fraction(level)
        if cfg.invalidation == "band_edge":
            inv = lo_ticks if sign > 0 else hi_ticks
        close = Fraction(cm.conversion.futures_close)
        lookout = dec(cfg.stop_lookout_pct) / 100 * close * TICKS_PER_POINT
        beyond = [
            cm.level_of(k)
            for k in source.labels.nodes
            if k != strike and 0 < sign * (inv - cm.level_of(k)) <= lookout
        ]
        stop = max(beyond, key=lambda x: sign * x, default=None)
    if stop is None:
        if sign > 0:
            stop = math.floor(lo_ticks) - cfg.fixed_stop_ticks
        else:
            stop = math.ceil(hi_ticks) + cfg.fixed_stop_ticks

    # Setup_Key (10.11) and validation (10.13-10.14).
    tap_seq = 1 + s.ended[(source.symbol, strike)]
    key = SetupKey(instrument, pattern, strike, direction, SESSION, tap_seq)

    def skip(condition: SkipCondition) -> Outcome:
        return Outcome(key, T0, condition, None)

    if not sign * stop < sign * entry:
        return skip("price_order")
    planned = plan_targets(
        TargetBasis(direction, entry, stop, strike, source.value(strike)),
        exit_mode_for(s.params.exits, NO_REGIME if s.regime is None else s.regime),
        TargetContext.from_snapshot(source.snapshot, source.labels, cm),
    )
    if isinstance(planned, NoTarget):
        return skip("no_target")
    if isinstance(planned, TargetRejection):
        return skip("no_target" if planned.reason == "no_target_node" else "price_order")
    if not sign * entry < sign * planned.tp1:
        return skip("price_order")
    return Outcome(key, T0, None, (stop, entry, planned.prices))


def expected_outputs(s: Scenario) -> list[Outcome]:
    """The reference outcome of every armed pick, per enabled detector in registry order."""
    out: list[Outcome] = []
    for d in REGISTRY:
        if not s.params.patterns.enabled(d.id):
            continue
        assert isinstance(d, PatternDetector)
        cfg = d.config_of(s.params.patterns)
        for source in sources(s.ctx, s.params, cfg.metric):
            for pick in d.select(s.ctx, source, cfg):
                outcome = reference(s, d.pattern, cfg, source, pick)
                if outcome is not None:
                    out.append(outcome)
    return out


def assert_price_order(c: CandidateSetup) -> None:
    """Req 10.13-10.14 on an emitted Candidate_Setup itself."""
    assert c.priced
    assert c.entry is not None
    assert c.stop is not None
    assert c.targets, "a Candidate_Setup with no target"
    sign = 1 if c.key.direction == "long" else -1
    assert sign * c.stop < sign * c.entry < sign * c.targets[0]
    assert all(sign * c.targets[0] <= sign * t for t in c.targets), "TP1 is not the nearest"


def label(o: Outcome | None) -> str:
    if o is None:
        return "unarmed"
    return "setup" if o.condition is None else f"skip: {o.condition}"


# ---------------------------------------------------------------- properties


# Feature: skylit-futures-strategy-engine, Property 33: Price order or skip
@given(s=scenarios(), data=st.data())
def test_any_armed_pick_is_an_ordered_setup_or_a_named_skip(
    s: Scenario, data: st.DataObject
) -> None:
    detector_id = data.draw(st.sampled_from(DETECTOR_IDS), label="detector")
    cfg = s.params.patterns.get(detector_id)
    source = data.draw(st.sampled_from(sources(s.ctx, s.params, cfg.metric)), label="source")
    strike = data.draw(
        st.just(s.focus[source.symbol]) | st.sampled_from(source.snapshot.strikes), label="strike"
    )
    pick: Pick = (strike, data.draw(st.sampled_from(DIRECTIONS), label="direction"))
    pattern = DETECTORS[detector_id].pattern

    got = construct(detector_id, pattern, cfg, s.ctx, s.params, source, pick)
    expected = reference(s, pattern, cfg, source, pick)
    event(label(expected))
    if isinstance(got, CandidateSetup):
        assert_price_order(got)
    assert (None if got is None else observe(got)) == expected


# Feature: skylit-futures-strategy-engine, Property 33: Price order or skip
@given(s=scenarios())
def test_detect_setups_emits_ordered_setups_or_named_skips(s: Scenario) -> None:
    out = detect_setups(s.ctx, s.params)
    for x in out:
        if isinstance(x, CandidateSetup):
            assert_price_order(x)
        event(label(observe(x)))
    assert [observe(x) for x in out] == expected_outputs(s)
