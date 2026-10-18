"""Property 23: Regime and Map_Grade.

*For any* Map_State, trailing medians and VIX state, the Regime_Classifier
returns exactly one of the five Regimes, equal to the first of Vanna_Dominant
(subject to the VIX condition), Structureless, Whipsaw, Negative_Gamma and
Positive_Gamma whose conditions hold in a reference implementation, or a
missing-input result naming the missing input; and returns exactly one
Map_Grade equal to the reference A_Plus_Map / F_Map / Neutral_Map rules, or the
missing-input result when the gamma Snapshot is absent.

The reference model restates design §7 and Requirement 7 in pure Python,
reading each criterion literally and sharing no code with ``fse.engine``:

- King, Nodes, Floor and Ceiling are recomputed from Requirement 6 criteria
  1-7 (one sort key ``(-a, |strike - spot|, strike)``), so a Whipsaw King or a
  Map_Grade count never comes from the Node_Classifier under test.
- Net GEX is summed exactly with ``Fraction``, so its sign is exact (Req 7.6).
- A_Plus_Map (Req 7.8) and F_Map (Req 7.9) are two separate predicates; the
  test asserts they never both hold and grades Neutral_Map when neither does
  (Req 7.10).
- Thresholds use the arithmetic form the requirement text states:
  ``distance_pct / 100 * spot``, ``whipsaw_pct / 100 * max(a_F, a_C)``,
  ``vanna_multiple * nGEX`` and ``prior + pct / 100 * prior`` (the prior VIX
  close plus pct percent of it), so float rounding can never separate the
  model from the code at a boundary.
- A missing input is named ``"{symbol} {metric} Snapshot"`` or ``"{symbol}
  {metric} trailing median"`` (the ``fse.engine.regime`` docstring). The names
  are compared as a set with no repeats; the requirement fixes no order.
- A trailing median is usable when it covers all 20 sessions and is above 0
  (Req 7.12); ``Unavailable`` is the ``fse.data.medians`` marker for a session
  with no earlier qualifying session.

Generators:

- the Regime_Symbol is SPX, SPY, QQQ or NDX (NDX is not a Trinity symbol);
  three cases in four have every input present and usable, the rest draw
  each Snapshot as present, ``Unavailable`` or not configured and each median
  as full, short (1-20 sessions, possibly 0) or ``Unavailable``;
- every other symbol of SPX, SPY, QQQ and NDX gets a gamma Snapshot, an
  ``Unavailable`` marker or nothing, so the Trinity King check sees 0 to 3
  Snapshots and Kings above, below or at spot;
- strikes sit on a grid anchored at spot (on a strike, half a tick off, or
  13 ticks to one side) with a tick of exactly the regime distance, half of
  it, 0.25, 1 or 5; or are free floats; or are empty. Values come from a
  small pool of powers of two two times in three, so ties, equal Floor and
  Ceiling values, exact ``n_vex == multiple * n_gex`` and a net GEX of exactly
  0 occur; one Snapshot in two has values of a single sign;
- the VIX value is unavailable, free, or exactly the threshold or one ulp to
  either side of it.

**Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 7.10, 7.11, 7.12**
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Final

import pytest
from hypothesis import event, given
from hypothesis import strategies as st

from fse.config.schema.regime import GradeConfig, RegimeConfig, VixConditionConfig
from fse.engine.nodes import NodeParams, classify
from fse.engine.regime import (
    RegimeParams,
    RegimeResult,
    TrailingMedian,
    TrailingMedians,
    map_grade,
    map_state_grade,
    regime,
)
from fse.engine.types import (
    MapGrade,
    Metric,
    MissingInput,
    Regime,
    Snapshot,
    Unavailable,
    VixState,
)
from fse.pit.protocols import MapState, SymMetric

T0: Final = 1_772_721_000 * 10**9
TRINITY_SYMBOLS: Final = ("SPX", "SPY", "QQQ")  # Glossary: the Trinity symbols
SYMBOLS: Final = ("SPX", "SPY", "QQQ", "NDX")
METRICS: Final[tuple[Metric, ...]] = ("gamma", "vanna")
FULL_WINDOW: Final = 20  # Req 7.2: the 20 most recent earlier sessions
F_MAP_NODES: Final = 5  # Req 7.8-7.9: 5 or more Nodes is an F_Map
NO_HISTORY: Final = Unavailable("no earlier session has RTH Snapshots of the metric")
NO_SNAPSHOT: Final = Unavailable("no Snapshot with asOf at or before the Decision_Time")
NO_VIX: Final = Unavailable("no VIX value at the Decision_Time")

type Missing = frozenset[str]
"""The names a missing-input result must carry."""


@dataclass(frozen=True, slots=True)
class Case:
    """Map_State entries, trailing medians, VIX state and parameters at one Decision_Time."""

    entries: Mapping[SymMetric, Snapshot | Unavailable]
    medians: TrailingMedians
    vix: VixState
    params: RegimeParams

    def map_state(self) -> MapState:
        return MapState(t=T0, entries=self.entries)

    def snapshot(self, symbol: str, metric: Metric) -> Snapshot | None:
        """The Snapshot present for (symbol, metric), or ``None`` when absent."""
        entry = self.entries.get((symbol, metric))
        return entry if isinstance(entry, Snapshot) else None


# ---------------------------------------------------------------- reference model

type Row = tuple[float, float]  # (strike, absolute value)


@dataclass(frozen=True, slots=True)
class RefLabels:
    """The Requirement 6 labels Requirement 7 reads."""

    king: float | None
    nodes: tuple[float, ...]
    floor: float | None
    ceiling: float | None


def ref_labels(s: Snapshot, node_fraction: float) -> RefLabels:
    """King, Nodes, Floor and Ceiling by Req 6.1-6.7, read literally."""
    a = [abs(v) for v in s.values]
    if not a or max(a) == 0.0:  # 6.4
        return RefLabels(None, (), None, None)
    rows: list[Row] = list(zip(s.strikes, a, strict=True))

    def top(candidates: Sequence[Row]) -> float:
        """Largest a; ties to the strike nearest spot, then the lower strike (6.3)."""
        return min(candidates, key=lambda r: (-r[1], abs(r[0] - s.spot), r[0]))[0]

    nodes = sorted(r for r in rows if r[1] >= node_fraction * max(a))  # 6.1
    below = [r for r in nodes if r[0] < s.spot]
    above = [r for r in nodes if r[0] > s.spot]
    return RefLabels(
        king=top(rows),  # 6.2
        nodes=tuple(k for k, _ in nodes),
        floor=top(below) if below else None,  # 6.5, 6.7
        ceiling=top(above) if above else None,  # 6.6, 6.7
    )


def a_at(s: Snapshot, strike: float | None) -> float:
    """The absolute value at ``strike``; a missing label counts as 0 (Req 7.8-7.9)."""
    if strike is None:
        return 0.0
    return next(abs(v) for k, v in zip(s.strikes, s.values, strict=True) if k == strike)


def near_spot(s: Snapshot, distance_pct: float) -> list[float]:
    """Signed values of the strikes at most the regime distance from spot (Req 7.2, 7.6)."""
    limit = distance_pct / 100 * s.spot
    return [v for k, v in zip(s.strikes, s.values, strict=True) if abs(k - s.spot) <= limit]


def raw_mag(s: Snapshot, distance_pct: float) -> float:
    """The largest absolute value near spot, or 0 when no strike is that close (Req 7.2)."""
    return max((abs(v) for v in near_spot(s, distance_pct)), default=0.0)


def usable(m: TrailingMedian | Unavailable) -> float | None:
    """The median when it covers 20 sessions and is above 0, else ``None`` (Req 7.12)."""
    if isinstance(m, TrailingMedian) and m.sessions == FULL_WINDOW and m.median > 0:
        return m.median
    return None


def vix_allows(vix: VixState, cfg: RegimeConfig) -> bool:
    """Whether the VIX condition lets Vanna_Dominant through (Req 7.3)."""
    cond = cfg.vix_condition
    if not cond.enabled:
        return True
    value = vix.last_1m_close if cond.intraday_source else vix.daily_open
    prior = vix.prior_close
    if isinstance(value, Unavailable) or isinstance(prior, Unavailable):
        return False
    return value >= prior + cond.pct / 100 * prior


def whipsaw(case: Case, gamma: Snapshot) -> bool:
    """Req 7.5: close Floor and Ceiling, or Trinity Kings on opposite sides of spot."""
    p = case.params
    labels = ref_labels(gamma, p.nodes.node_fraction)
    if labels.floor is not None and labels.ceiling is not None:
        a_f, a_c = a_at(gamma, labels.floor), a_at(gamma, labels.ceiling)
        if abs(a_f - a_c) <= p.config.whipsaw_pct / 100 * max(a_f, a_c):
            return True
    sides: set[bool] = set()  # True: a King above its spot; False: below
    for symbol in TRINITY_SYMBOLS:
        s = case.snapshot(symbol, "gamma")
        if s is None:
            continue  # only Trinity symbols present in the Map_State count
        king = ref_labels(s, p.nodes.node_fraction).king
        if king is not None and king != s.spot:  # a King at spot is on neither side
            sides.add(king > s.spot)
    return sides == {True, False}


def reference_regime(case: Case) -> Regime | Missing:
    """The Regime by Req 7.1-7.7, or the absent inputs (Req 7.11-7.12)."""
    cfg, symbol = case.params.config, case.params.symbol
    gamma = case.snapshot(symbol, "gamma")
    vanna = case.snapshot(symbol, "vanna")
    med_gex = usable(case.medians.gamma)
    med_vex = usable(case.medians.vanna)
    if gamma is None or vanna is None or med_gex is None or med_vex is None:
        names = {
            f"{symbol} gamma Snapshot": gamma is None,  # 7.11
            f"{symbol} vanna Snapshot": vanna is None,  # 7.12
            f"{symbol} gamma trailing median": med_gex is None,
            f"{symbol} vanna trailing median": med_vex is None,
        }
        return frozenset(name for name, absent in names.items() if absent)

    distance = cfg.regime_distance_pct
    n_gex = raw_mag(gamma, distance) / med_gex  # 7.2
    n_vex = raw_mag(vanna, distance) / med_vex
    if n_vex > 0 and n_vex >= cfg.vanna_multiple * n_gex and vix_allows(case.vix, cfg):
        return "Vanna_Dominant"  # 7.2, 7.3
    if not any(abs(v) >= cfg.min_abs_value for v in gamma.values):
        return "Structureless"  # 7.4
    if whipsaw(case, gamma):
        return "Whipsaw"  # 7.5
    if sum(Fraction(v) for v in near_spot(gamma, distance)) < 0:
        return "Negative_Gamma"  # 7.6
    return "Positive_Gamma"  # 7.7


def grade_of(gamma: Snapshot, p: RegimeParams) -> MapGrade:
    """The Map_Grade by Req 7.8-7.10."""
    g = p.config.grade
    labels = ref_labels(gamma, p.nodes.node_fraction)
    f, c = a_at(gamma, labels.floor), a_at(gamma, labels.ceiling)
    big, small = max(f, c), min(f, c)
    major = g.major_fraction * a_at(gamma, labels.king)
    majors = sum(1 for k in labels.nodes if a_at(gamma, k) >= major)  # the King included
    a_plus = (
        len(labels.nodes) < F_MAP_NODES
        and majors in (1, 2)
        and big > 0
        and big >= g.floor_ceiling_ratio * small
    )  # 7.8
    f_map = (
        len(labels.nodes) >= F_MAP_NODES or big == 0 or big < g.floor_ceiling_ratio * small
    )  # 7.9
    assert not (a_plus and f_map), "the A_Plus_Map and F_Map conditions overlap"
    if a_plus:
        return "A_Plus_Map"
    return "F_Map" if f_map else "Neutral_Map"  # 7.10


def reference_grade(case: Case) -> MapGrade | Missing:
    """The Map_Grade, or the absent gamma Snapshot (Req 7.11)."""
    symbol = case.params.symbol
    gamma = case.snapshot(symbol, "gamma")
    if gamma is None:
        return frozenset({f"{symbol} gamma Snapshot"})
    return grade_of(gamma, case.params)


# ---------------------------------------------------------------- the check


def _label(want: str | Missing) -> str:
    return want if isinstance(want, str) else "missing input"


def check(case: Case) -> tuple[str, str]:
    """The code under test agrees with the reference model on ``case``.

    Returns the expected Regime and Map_Grade labels ("missing input" for a
    missing-input result).
    """
    ms = case.map_state()
    want_regime = reference_regime(case)
    got_regime = regime(ms, case.medians, case.vix, case.params)
    if isinstance(want_regime, frozenset):
        assert isinstance(got_regime, MissingInput), f"got {got_regime!r}, want {want_regime}"
        assert len(set(got_regime.names)) == len(got_regime.names), got_regime
        assert frozenset(got_regime.names) == want_regime, got_regime
    else:
        assert isinstance(got_regime, RegimeResult), f"got {got_regime!r}, want {want_regime}"
        assert got_regime.regime == want_regime, f"got {got_regime!r}, want {want_regime}"

    want_grade = reference_grade(case)
    got_grade = map_state_grade(ms, case.params)
    if isinstance(want_grade, frozenset):
        assert isinstance(got_grade, MissingInput), f"got {got_grade!r}, want {want_grade}"
        assert len(set(got_grade.names)) == len(got_grade.names), got_grade
        assert frozenset(got_grade.names) == want_grade, got_grade
    else:
        assert got_grade == want_grade, f"got {got_grade!r}, want {want_grade}"
        gamma = ms.get(case.params.symbol, "gamma")
        assert isinstance(gamma, Snapshot)
        labels = classify(gamma, case.params.nodes)
        assert map_grade(gamma, labels, case.params.config.grade) == want_grade
    return _label(want_regime), _label(want_grade)


# ---------------------------------------------------------------- generators

SPOTS = st.one_of(
    st.sampled_from((100.0, 400.0, 1000.0, 5800.0, 21000.75)), st.floats(1.0, 50_000.0)
)
POOL = st.sampled_from((0.0, 1.0, 2.0, 3.0, 4.0, 8.0, 1.5e9, 3.0e9))
MAGNITUDES = st.one_of(POOL, POOL, st.floats(1e-3, 1e12))


def _signed(pair: tuple[float, bool]) -> float:
    magnitude, negative = pair
    return -magnitude if negative else magnitude


VALUES = st.tuples(MAGNITUDES, st.booleans()).map(_signed)
PCTS = st.floats(0.0, 100.0, exclude_min=True)
MEDIAN_VALUES = st.one_of(st.sampled_from((0.5, 1.0, 2.0, 4.0, 1e9)), st.floats(1e-3, 1e12))
FULL_MEDIANS = st.builds(TrailingMedian, MEDIAN_VALUES, st.just(FULL_WINDOW))
ANY_MEDIANS = st.one_of(
    FULL_MEDIANS,
    st.builds(TrailingMedian, st.one_of(MEDIAN_VALUES, st.just(0.0)), st.integers(1, FULL_WINDOW)),
    st.just(NO_HISTORY),
)


@st.composite
def snapshots(draw: st.DrawFn, symbol: str, metric: Metric, distance_pct: float) -> Snapshot:
    spot = draw(SPOTS)
    layout = draw(st.sampled_from(("grid", "grid", "grid", "grid", "free", "empty")))
    raw: list[float]
    if layout == "grid":
        limit = distance_pct / 100 * spot
        tick = draw(st.sampled_from((limit, limit / 2, 0.25, 1.0, 5.0)))
        shift = draw(st.sampled_from((0.0, 0.0, 0.5, 13.0, -13.0)))
        steps = draw(st.lists(st.integers(-6, 6), min_size=1, max_size=9, unique=True))
        raw = [spot + (shift + i) * tick for i in steps]
    elif layout == "free":
        raw = draw(st.lists(st.floats(1.0, 50_000.0), min_size=1, max_size=8))
        spot = draw(st.one_of(st.just(spot), st.sampled_from(raw)))
    else:
        raw = []
    strikes = draw(st.permutations(list(dict.fromkeys(raw))))  # distinct, shuffled
    values = draw(st.lists(VALUES, min_size=len(strikes), max_size=len(strikes)))
    signs = draw(st.sampled_from(("mixed", "mixed", "negative", "positive")))
    if signs != "mixed":  # one-signed maps make net GEX decisively negative or positive
        values = [-abs(v) if signs == "negative" else abs(v) for v in values]
    return snap(dict(zip(strikes, values, strict=True)), symbol=symbol, metric=metric, spot=spot)


@st.composite
def regime_params(draw: st.DrawFn) -> RegimeParams:
    cfg = RegimeConfig(
        regime_distance_pct=draw(st.one_of(st.sampled_from((0.5, 1.0, 2.0, 5.0)), PCTS)),
        vanna_multiple=draw(
            st.one_of(st.sampled_from((0.5, 1.0, 2.0, 4.0)), st.floats(1e-3, 100.0))
        ),
        min_abs_value=draw(
            st.one_of(st.sampled_from((0.5, 1.0, 3.0, 4.0, 5.0, 1e9)), st.floats(1e-3, 1e10))
        ),
        whipsaw_pct=draw(st.one_of(st.sampled_from((15.0, 25.0, 50.0, 100.0)), PCTS)),
        vix_condition=VixConditionConfig(
            enabled=draw(st.booleans()),
            pct=draw(st.one_of(st.sampled_from((0.0, 5.0, 10.0)), st.floats(0.0, 200.0))),
            intraday_source=draw(st.booleans()),
        ),
        grade=GradeConfig(
            major_fraction=draw(st.one_of(st.sampled_from((0.01, 0.5, 1.0)), st.floats(0.01, 1.0))),
            floor_ceiling_ratio=draw(
                st.one_of(st.sampled_from((1.0, 1.5, 2.0)), st.floats(1.0, 10.0))
            ),
        ),
    )
    nodes = NodeParams(
        node_fraction=draw(st.one_of(st.sampled_from((0.01, 0.2, 0.5, 1.0)), st.floats(0.01, 1.0))),
        gatekeeper_fraction=0.30,
        air_pocket_min_width_pct=0.5,
        lookout_pct=1.0,
    )
    symbol = draw(st.sampled_from(("SPX", "SPX", "SPY", "QQQ", "NDX")))
    return RegimeParams(config=cfg, symbol=symbol, nodes=nodes)


@st.composite
def vix_states(draw: st.DrawFn, pct: float) -> VixState:
    prior = draw(st.one_of(st.sampled_from((10.0, 16.0, 20.0)), st.floats(1.0, 100.0)))
    threshold = prior + pct / 100 * prior
    value = st.one_of(
        st.sampled_from(
            (
                threshold,
                math.nextafter(threshold, -math.inf),
                math.nextafter(threshold, math.inf),
            )
        ),
        st.floats(1.0, 150.0),
        st.just(NO_VIX),
    )
    prior_close = draw(st.sampled_from((prior,) * 7 + (NO_VIX,)))
    return VixState(daily_open=draw(value), prior_close=prior_close, last_1m_close=draw(value))


@st.composite
def cases(draw: st.DrawFn) -> Case:
    p = draw(regime_params())
    distance = p.config.regime_distance_pct
    complete = draw(st.sampled_from((True, True, True, False)))
    entries: dict[SymMetric, Snapshot | Unavailable] = {}
    for metric in METRICS:
        state = "present" if complete else draw(st.sampled_from(("present", "gone", "unset")))
        if state == "present":
            entries[(p.symbol, metric)] = draw(snapshots(p.symbol, metric, distance))
        elif state == "gone":
            entries[(p.symbol, metric)] = NO_SNAPSHOT
    for symbol in SYMBOLS:
        if symbol == p.symbol:
            continue
        state = draw(st.sampled_from(("present", "gone", "unset")))
        if state == "present":
            entries[(symbol, "gamma")] = draw(snapshots(symbol, "gamma", distance))
        elif state == "gone":
            entries[(symbol, "gamma")] = NO_SNAPSHOT
    medians: st.SearchStrategy[TrailingMedian | Unavailable] = (
        FULL_MEDIANS if complete else ANY_MEDIANS
    )
    return Case(
        entries=entries,
        medians=TrailingMedians(gamma=draw(medians), vanna=draw(medians)),
        vix=draw(vix_states(p.config.vix_condition.pct)),
        params=p,
    )


# ---------------------------------------------------------------- worked examples


def snap(
    values: Mapping[float, float],
    *,
    symbol: str = "SPX",
    metric: Metric = "gamma",
    spot: float = 1000.0,
) -> Snapshot:
    return Snapshot(
        symbol=symbol,
        metric=metric,
        view_id="test-view",
        as_of_ns=T0,
        as_of_raw="2026-03-05T14:30:00Z",
        spot=spot,
        previous_close=None,
        strikes=tuple(values),
        values=tuple(values.values()),
        node_types=None,
        expirations=("2026-03-05",),
        resolution="1s",
        source_endpoint="range",
        extra_json="{}",
    )


def worked(
    *snapshots: Snapshot,
    unavailable: tuple[SymMetric, ...] = (),
    medians: tuple[TrailingMedian | Unavailable, TrailingMedian | Unavailable] = (
        TrailingMedian(100.0, FULL_WINDOW),
        TrailingMedian(100.0, FULL_WINDOW),
    ),
    vix_open: float | Unavailable = NO_VIX,
    vix_on: bool = False,
) -> Case:
    """Spot 1000, 1% regime distance (990 to 1010), min_abs_value 50, other defaults."""
    entries: dict[SymMetric, Snapshot | Unavailable] = {(s.symbol, s.metric): s for s in snapshots}
    entries.update(dict.fromkeys(unavailable, NO_SNAPSHOT))
    cfg = RegimeConfig(min_abs_value=50.0, vix_condition=VixConditionConfig(enabled=vix_on))
    return Case(
        entries=entries,
        medians=TrailingMedians(gamma=medians[0], vanna=medians[1]),
        vix=VixState(daily_open=vix_open, prior_close=20.0, last_1m_close=NO_VIX),
        params=RegimeParams(config=cfg),
    )


POSITIVE = snap({990.0: 100.0, 1000.0: 50.0, 1010.0: 10.0})  # King and Floor 990, net +160
STRONG_VANNA = snap({995.0: 300.0, 1005.0: -400.0}, metric="vanna")  # nVEX 4 vs nGEX 1
WEAK_VANNA = snap({1000.0: 1.0}, metric="vanna")
SMALL_MEDIANS = (TrailingMedian(100.0, FULL_WINDOW), TrailingMedian(1000.0, FULL_WINDOW))

WORKED: Final[tuple[tuple[str, Case, Regime | Missing, MapGrade | Missing], ...]] = (
    ("vanna-dominant", worked(POSITIVE, STRONG_VANNA), "Vanna_Dominant", "A_Plus_Map"),
    (  # VIX 20.5 < 20 + 5% of 20: withheld, the remaining checks run
        "vix-withholds",
        worked(POSITIVE, STRONG_VANNA, vix_open=20.5, vix_on=True),
        "Positive_Gamma",
        "A_Plus_Map",
    ),
    (
        "vix-at-threshold",
        worked(POSITIVE, STRONG_VANNA, vix_open=21.0, vix_on=True),
        "Vanna_Dominant",
        "A_Plus_Map",
    ),
    (
        "vix-unavailable",
        worked(POSITIVE, STRONG_VANNA, vix_on=True),
        "Positive_Gamma",
        "A_Plus_Map",
    ),
    (
        "structureless",
        worked(snap({990.0: 10.0, 1010.0: -20.0}), WEAK_VANNA, medians=SMALL_MEDIANS),
        "Structureless",
        "A_Plus_Map",
    ),
    (  # |100 - 90| <= 15% of 100; 100 < 1.5 * 90
        "whipsaw-floor-ceiling",
        worked(snap({990.0: 100.0, 1010.0: -90.0}), WEAK_VANNA, medians=SMALL_MEDIANS),
        "Whipsaw",
        "F_Map",
    ),
    (  # SPX King 990 below spot, SPY King 101 above
        "whipsaw-trinity-kings",
        worked(
            POSITIVE,
            WEAK_VANNA,
            snap({99.0: 1.0, 101.0: 5.0}, symbol="SPY", spot=100.0),
            medians=SMALL_MEDIANS,
        ),
        "Whipsaw",
        "A_Plus_Map",
    ),
    (  # SPY King at spot and the NDX King (not Trinity) count on neither side
        "king-at-spot",
        worked(
            POSITIVE,
            WEAK_VANNA,
            snap({99.0: 1.0, 100.0: 5.0}, symbol="SPY", spot=100.0),
            snap({21010.0: 5.0}, symbol="NDX", spot=21000.0),
            medians=SMALL_MEDIANS,
        ),
        "Positive_Gamma",
        "A_Plus_Map",
    ),
    (
        "negative-gamma",
        worked(
            snap({990.0: -100.0, 1000.0: 50.0, 1010.0: 10.0}), WEAK_VANNA, medians=SMALL_MEDIANS
        ),
        "Negative_Gamma",
        "A_Plus_Map",
    ),
    (  # net GEX exactly 0; 3 major Nodes
        "net-zero",
        worked(
            snap({990.0: 100.0, 1000.0: -50.0, 1005.0: -50.0}), WEAK_VANNA, medians=SMALL_MEDIANS
        ),
        "Positive_Gamma",
        "Neutral_Map",
    ),
    (
        "five-nodes",
        worked(
            snap({980.0: 100.0, 990.0: 100.0, 1000.0: 100.0, 1010.0: -100.0, 1020.0: 100.0}),
            WEAK_VANNA,
            medians=SMALL_MEDIANS,
        ),
        "Whipsaw",
        "F_Map",
    ),
    (
        "no-strikes",
        worked(snap({}), snap({}, metric="vanna")),
        "Structureless",
        "F_Map",
    ),
    (
        "gamma-missing",
        worked(STRONG_VANNA, unavailable=(("SPX", "gamma"),)),
        frozenset({"SPX gamma Snapshot"}),
        frozenset({"SPX gamma Snapshot"}),
    ),
    (  # vanna not configured; the gamma median covers 19 sessions
        "vanna-missing-short-median",
        worked(POSITIVE, medians=(TrailingMedian(100.0, 19), TrailingMedian(100.0, FULL_WINDOW))),
        frozenset({"SPX vanna Snapshot", "SPX gamma trailing median"}),
        "A_Plus_Map",
    ),
    (
        "median-zero-and-no-history",
        worked(POSITIVE, STRONG_VANNA, medians=(NO_HISTORY, TrailingMedian(0.0, FULL_WINDOW))),
        frozenset({"SPX gamma trailing median", "SPX vanna trailing median"}),
        "A_Plus_Map",
    ),
)


# ---------------------------------------------------------------- tests


@pytest.mark.parametrize(
    ("case", "want_regime", "want_grade"),
    [w[1:] for w in WORKED],
    ids=[w[0] for w in WORKED],
)
def test_worked_examples(
    case: Case, want_regime: Regime | Missing, want_grade: MapGrade | Missing
) -> None:
    """The reference model gives the hand-worked answer, and the code agrees."""
    assert reference_regime(case) == want_regime
    assert reference_grade(case) == want_grade
    check(case)


# Feature: skylit-futures-strategy-engine, Property 23: Regime and Map_Grade
@given(case=cases())
def test_regime_and_map_grade_match_reference(case: Case) -> None:
    regime_label, grade_label = check(case)
    event(f"regime: {regime_label}")  # --hypothesis-show-statistics shows the mix
    event(f"grade: {grade_label}")
