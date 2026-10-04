"""Property 20: Clusters partition the Nodes.

*For any* set of converted Node levels and cluster width, with clustering
enabled, the clusters partition the Nodes, consecutive levels inside a cluster
are at most the width apart, consecutive clusters are more than the width
apart, ranks order clusters by summed absolute value, and the King, Floor,
Ceiling and Gatekeeper labels equal those computed with clustering disabled.

One Map_State goes through the real pipeline: :class:`HistoricalInputs`, then
``convert_map`` (the Level_Converter), then ``clusters_at``. Each case draws:

- the configured symbols (any non-empty subset of SPX, SPY, QQQ, NDX and NDXP)
  and metrics, the SPY, NDX and NDXP methods and the ES and NQ instruments;
- the ES cluster width: tick multiples (so a gap can equal the width exactly)
  or any positive float;
- 1-minute bars of ES, MES, NQ and MNQ on both sides of the Snapshots'
  ``asOf``, so the NQ width (ES width x NQ / ES at ``t``) can use later prices
  than the level pairing, and a family may have no price at all;
- per (symbol, metric) at most one Snapshot, with distinct strikes on a grid
  near spot plus a few loose strikes, in random order, and values that tie,
  are comparable (many Nodes) or spread widely.

The oracle recomputes the Nodes from Req 6.1 (``|value| >= node_fraction x
max|value|``), takes each Node's level from the converter output, and
recomputes the width per family from the bars. Labels with clustering
disabled come from a second run on copies of the Snapshots that differ only
in ``as_of_raw``, so they are computed apart from the clustering run.

**Validates: Requirements 6.12, 6.13**
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, time
from itertools import pairwise
from typing import Final

from hypothesis import example, given
from hypothesis import strategies as st

from fse.config.schema.data import EsInstrument, NqInstrument
from fse.config.schema.levels import LevelMethodsConfig, LevelsConfig
from fse.config.schema.nodes import (
    FRACTION_MIN,
    GATEKEEPER_FRACTION_MAX,
    PCT_MAX,
    ClusteringConfig,
)
from fse.engine.clusters import Cluster, clusters_at
from fse.engine.levels import ConvertedMap, LevelFamily, LevelParams, convert_map
from fse.engine.nodes import NodeLabels, NodeParams, classify
from fse.engine.types import Bar, ConversionMethod, Metric, MissingPrice, Snapshot, Ticks
from fse.pit.market_view import HistoricalInputs
from fse.pit.protocols import SymMetric
from fse.timekit import NS_PER_SECOND, Instant, ny_instant

S: Final = NS_PER_SECOND
MINUTE: Final = 60 * S
T0: Final[Instant] = ny_instant(date(2026, 3, 5), time(10, 0))
VIEW_ID: Final = "view-p20"

SYMBOLS: Final = ("SPX", "SPY", "QQQ", "NDX", "NDXP")
METRICS: Final[tuple[Metric, ...]] = ("gamma", "vanna")
SPOT_BASE: Final = {"SPX": 5800.0, "SPY": 580.0, "QQQ": 500.0, "NDX": 20000.0, "NDXP": 20000.0}
# Strike steps whose converted gaps straddle the cluster widths drawn below.
STRIKE_STEPS: Final[dict[str, tuple[float, ...]]] = {
    "SPX": (0.25, 1.0, 2.5, 5.0),
    "SPY": (0.125, 0.5, 1.0),
    "QQQ": (0.0625, 0.25, 0.5),
    "NDX": (1.0, 5.0, 10.0),
    "NDXP": (1.0, 5.0, 10.0),
}
INSTRUMENTS: Final = ("ES", "MES", "NQ", "MNQ")
FUTURES_BASE_TICKS: Final = {"ES": 23240, "MES": 23240, "NQ": 80200, "MNQ": 80200}
BAR_MINUTES: Final = (-2, -1, 0, 1, 2)  # bar closes relative to the Snapshots' asOf (T0)
DECISION_TIMES: Final[tuple[Instant, ...]] = (
    T0,
    T0 + 30 * S,
    T0 + MINUTE,
    T0 + 2 * MINUTE,
    T0 + 150 * S,
)
METHODS: Final[tuple[ConversionMethod, ...]] = ("offset", "ratio")
ES_INSTRUMENTS: Final[tuple[EsInstrument, ...]] = ("ES", "MES")
NQ_INSTRUMENTS: Final[tuple[NqInstrument, ...]] = ("NQ", "MNQ")
DEFAULT_NODES: Final = NodeParams(
    node_fraction=0.20, gatekeeper_fraction=0.30, air_pocket_min_width_pct=0.5, lookout_pct=1.0
)

type Labels = tuple[float | None, float | None, float | None, tuple[float, ...]]

# ---------------------------------------------------------------- inputs


def snap(
    symbol: str,
    metric: Metric,
    spot: float,
    strikes: tuple[float, ...],
    values: tuple[float, ...],
) -> Snapshot:
    return Snapshot(
        symbol=symbol,
        metric=metric,
        view_id=VIEW_ID,
        as_of_ns=T0,
        as_of_raw=f"raw-{symbol}-{metric}",
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


def make_bar(instrument: str, close_ns: Instant, c_t: Ticks) -> Bar:
    px = c_t / 4
    return Bar(
        instrument, f"{instrument}H6", 60, close_ns - MINUTE, close_ns,
        px, px, px, px, 1.0, c_t, c_t, c_t, c_t, "atlas",
    )  # fmt: skip


@dataclass(frozen=True, slots=True)
class Case:
    """One Map_State's inputs, the parameters and the Decision_Time."""

    symbols: tuple[str, ...]
    metrics: tuple[Metric, ...]
    level_params: LevelParams
    node_params: NodeParams
    width_pts: float
    snapshots: tuple[Snapshot, ...]
    bars: tuple[Bar, ...]
    t: Instant

    def inputs(self, snapshots: tuple[Snapshot, ...]) -> HistoricalInputs:
        return HistoricalInputs(
            symbols=self.symbols,
            view_id=VIEW_ID,
            metrics=self.metrics,
            snapshots=snapshots,
            bars=self.bars,
        )


# ---------------------------------------------------------------- oracle


def futures_points(case: Case, instrument: str) -> float | None:
    """The close of the latest 1-minute bar of ``instrument`` closed at or before ``t``."""
    closed = [b for b in case.bars if b.instrument == instrument and b.close_ns <= case.t]
    if not closed:
        return None
    latest = max(closed, key=lambda b: b.close_ns)  # close times are unique per instrument
    assert latest.c_t is not None
    return latest.c_t / 4


def expected_width(case: Case, family: LevelFamily) -> float | MissingPrice:
    """ES: the configured width. NQ: width x NQ / ES at ``t`` (Req 6.12)."""
    if family == "ES":
        return case.width_pts
    nq_instrument = case.level_params.nq_instrument
    es_instrument = case.level_params.es_instrument
    nq = futures_points(case, nq_instrument)
    es = futures_points(case, es_instrument)
    if nq is None:
        return MissingPrice(nq_instrument)
    if es is None:
        return MissingPrice(es_instrument)
    return case.width_pts * nq / es


def expected_nodes(snapshot: Snapshot, params: NodeParams) -> list[float]:
    """Req 6.1: strikes with ``|value| >= node_fraction x max|value|``, ascending."""
    a = [abs(v) for v in snapshot.values]
    a_max = max(a, default=0.0)
    if a_max == 0.0:
        return []
    return sorted(
        k for k, v in zip(snapshot.strikes, a, strict=True) if v >= params.node_fraction * a_max
    )


def label_set(labels: NodeLabels) -> Labels:
    return (labels.king, labels.floor, labels.ceiling, labels.gatekeepers)


def check_clusters(
    snapshot: Snapshot,
    conv: ConvertedMap,
    got: tuple[Cluster, ...],
    width: float,
    params: NodeParams,
    where: str,
) -> None:
    a_of = {k: abs(v) for k, v in zip(snapshot.strikes, snapshot.values, strict=True)}
    level_of = dict(zip(conv.strikes, conv.levels, strict=True))

    # Req 6.12: every Node is in exactly one cluster, and nothing else is.
    members = sorted(k for c in got for k in c.strikes)
    nodes = expected_nodes(snapshot, params)
    assert members == nodes, f"{where}: cluster members {members!r}, Nodes {nodes!r}"

    # Req 6.12: inside a cluster, levels ascend and each gap is at most the width.
    for c in got:
        assert c.levels == tuple(level_of[k] for k in c.strikes), (
            f"{where}: cluster {c!r} levels differ from the converted levels"
        )
        pairs = list(zip(c.levels, c.strikes, strict=True))
        assert pairs == sorted(pairs), f"{where}: cluster {c!r} is not in level order"
        for lo, hi in pairwise(c.levels):
            assert (hi - lo) / 4 <= width, (
                f"{where}: gap {(hi - lo) / 4} pts inside {c!r} exceeds width {width!r}"
            )

    # Req 6.12: consecutive clusters, in level order, are more than the width apart.
    for prev, nxt in pairwise(sorted(got, key=lambda c: c.lo)):
        gap = (nxt.lo - prev.hi) / 4
        assert gap > width, (
            f"{where}: clusters {prev!r} and {nxt!r} are {gap} pts apart, width {width!r}"
        )

    # Req 6.13: summed absolute value per cluster; ranks 1..n, rank 1 the largest sum.
    for c in got:
        want = math.fsum(a_of[k] for k in c.strikes)
        assert c.abs_sum == want, f"{where}: {c!r} abs_sum, want {want!r}"
    ranks = sorted(c.rank for c in got)
    assert ranks == list(range(1, len(got) + 1)), f"{where}: ranks {ranks!r}"
    by_rank = sorted(got, key=lambda c: c.rank)
    for higher, lower in pairwise(by_rank):
        assert higher.abs_sum >= lower.abs_sum, (
            f"{where}: rank {higher.rank} sums {higher.abs_sum!r} < "
            f"rank {lower.rank} sums {lower.abs_sum!r}"
        )


# ---------------------------------------------------------------- generators

TIE_MAGNITUDES = (0.0, 1.0e9, 2.0e9, 3.0e9)
TIED_VALUES = tuple(sign * m for m in TIE_MAGNITUDES for sign in (1.0, -1.0))  # has -0.0
SIMILAR = st.tuples(st.floats(1e8, 1e10), st.sampled_from((1.0, -1.0))).map(lambda t: t[0] * t[1])
VALUE_STYLES = (
    st.sampled_from(TIED_VALUES),  # ties and zeros: equal cluster sums
    SIMILAR,  # comparable magnitudes: many Nodes, so many multi-Node clusters
    st.floats(-1e12, 1e12),  # wide spread: few Nodes
)
WIDTHS = st.one_of(
    st.sampled_from((5.0, 0.25, 1.0, 2.5, 10.0)),
    st.integers(1, 120).map(lambda k: k / 4),  # tick multiples: gap == width happens
    st.floats(0.01, 60.0),
)
KEEP = st.integers(0, 4).map(bool)  # present 4 times in 5
NODE_FRACTIONS = st.one_of(
    st.sampled_from((FRACTION_MIN, 0.20, 0.5, 1.0)), st.floats(FRACTION_MIN, 1.0)
)
GATEKEEPER_FRACTIONS = st.one_of(
    st.sampled_from((FRACTION_MIN, 0.30, GATEKEEPER_FRACTION_MAX)),
    st.floats(FRACTION_MIN, GATEKEEPER_FRACTION_MAX),
)
PCTS = st.one_of(st.sampled_from((0.5, 1.0, PCT_MAX)), st.floats(0.0, PCT_MAX, exclude_min=True))
NODE_PARAMS = st.builds(
    NodeParams,
    node_fraction=NODE_FRACTIONS,
    gatekeeper_fraction=GATEKEEPER_FRACTIONS,
    air_pocket_min_width_pct=PCTS,
    lookout_pct=PCTS,
)


@st.composite
def strikes_near(draw: st.DrawFn, symbol: str, spot: float) -> tuple[float, ...]:
    """Distinct strikes on a grid around spot plus a few loose ones, in random order."""
    step = draw(st.sampled_from(STRIKE_STEPS[symbol]))
    centre = round(spot / step) * step
    offsets = draw(st.lists(st.integers(-40, 40), unique=True, max_size=20))
    loose = draw(st.lists(st.floats(spot * 0.98, spot * 1.02), max_size=3))
    strikes = list(dict.fromkeys([*(centre + i * step for i in offsets), *loose]))
    return tuple(draw(st.permutations(strikes)))


@st.composite
def snapshots_for(draw: st.DrawFn, symbol: str, metric: Metric) -> Snapshot:
    base = SPOT_BASE[symbol]
    spot = draw(st.integers(int(base * 8 * 0.99), int(base * 8 * 1.01)).map(lambda k: k / 8))
    strikes = draw(strikes_near(symbol, spot))
    style = draw(st.sampled_from(VALUE_STYLES))
    values = tuple(draw(st.lists(style, min_size=len(strikes), max_size=len(strikes))))
    return snap(symbol, metric, spot, strikes, values)


@st.composite
def cases(draw: st.DrawFn) -> Case:
    symbols = tuple(draw(st.lists(st.sampled_from(SYMBOLS), min_size=1, unique=True)))
    metrics = tuple(draw(st.lists(st.sampled_from(METRICS), min_size=1, unique=True)))
    level_params = LevelParams(
        LevelsConfig(
            methods=LevelMethodsConfig(
                SPY=draw(st.sampled_from(METHODS)),
                NDX=draw(st.sampled_from(METHODS)),
                NDXP=draw(st.sampled_from(METHODS)),
            )
        ),
        draw(st.sampled_from(ES_INSTRUMENTS)),
        draw(st.sampled_from(NQ_INSTRUMENTS)),
    )
    bars = tuple(
        make_bar(
            instrument,
            T0 + m * MINUTE,
            draw(
                st.integers(
                    FUTURES_BASE_TICKS[instrument] - 2000, FUTURES_BASE_TICKS[instrument] + 2000
                )
            ),
        )
        for instrument in INSTRUMENTS
        for m in BAR_MINUTES
        if draw(KEEP)
    )
    snapshots = tuple(
        draw(snapshots_for(symbol, metric))
        for symbol in symbols
        for metric in metrics
        if draw(KEEP)
    )
    return Case(
        symbols=symbols,
        metrics=metrics,
        level_params=level_params,
        node_params=draw(NODE_PARAMS),
        width_pts=draw(WIDTHS),
        snapshots=snapshots,
        bars=bars,
        t=draw(st.sampled_from(DECISION_TIMES)),
    )


# ---------------------------------------------------------------- explicit examples

# MES 5810, so the SPX offset is +10 points.
_ES_EXAMPLE = Case(
    symbols=("SPX", "SPY"),
    metrics=METRICS,
    level_params=LevelParams(),
    node_params=DEFAULT_NODES,
    width_pts=5.0,
    snapshots=(
        # Levels 5800, 5805 | 5810.25 | 5825: a gap of exactly 5 merges, 5.25 splits.
        snap("SPX", "gamma", 5800.0, (5815.0, 5790.0, 5800.25, 5795.0), (1e9, 1e9, -1e9, 1e9)),
        # The King (5820) sits in the rank-2 cluster; the King label does not move.
        snap("SPX", "vanna", 5800.0, (5790.0, 5795.0, 5820.0), (3e9, -3e9, 4e9)),
        # All values zero: no Nodes, no clusters. SPY vanna has no Snapshot.
        snap("SPY", "gamma", 580.0, (579.0, 581.0), (0.0, -0.0)),
    ),
    bars=(make_bar("MES", T0, 23240),),
    t=T0,
)
# Levels pair at T0 (QQQ ratio 40, NDX ratio 1); the NQ width uses the bars at
# t = T0 + 60 s: 5 x 20000 / 4000 = 25 points (it would be 20 at T0).
_NQ_EXAMPLE = Case(
    symbols=("QQQ", "NDX"),
    metrics=("gamma",),
    level_params=LevelParams(),
    node_params=DEFAULT_NODES,
    width_pts=5.0,
    snapshots=(
        # Levels 20000, 20025, 20050 | 20080.
        snap("QQQ", "gamma", 500.0, (500.0, 500.625, 501.25, 502.0), (1e9, 1e9, 1e9, 1e9)),
        # Levels 20000, 20025 | 20060; the lone Node outranks the pair.
        snap("NDX", "gamma", 20000.0, (20000.0, 20025.0, 20060.0), (1e9, 2e9, 5e9)),
    ),
    bars=(
        make_bar("MES", T0, 20000),
        make_bar("MNQ", T0, 80000),
        make_bar("MES", T0 + MINUTE, 16000),
        make_bar("MNQ", T0 + MINUTE, 80000),
    ),
    t=T0 + MINUTE,
)

# ---------------------------------------------------------------- property


# Feature: skylit-futures-strategy-engine, Property 20: Clusters partition the Nodes
@example(case=_ES_EXAMPLE)
@example(case=_NQ_EXAMPLE)
@given(case=cases())
def test_clusters_partition_the_nodes(case: Case) -> None:
    lp, np_ = case.level_params, case.node_params
    enabled = ClusteringConfig(enabled=True, es_width_pts=case.width_pts)
    disabled = ClusteringConfig(enabled=False, es_width_pts=case.width_pts)

    # Clustering disabled, on copies of the Snapshots (a separate label memo entry).
    copies = tuple(replace(s, as_of_raw=f"{s.as_of_raw}-off") for s in case.snapshots)
    view_off = case.inputs(copies).view(case.t)
    assert clusters_at(view_off, convert_map(view_off, lp), np_, disabled, lp) is None
    labels_off: Mapping[SymMetric, Labels] = {
        key: label_set(classify(s, np_))
        for key, s in view_off.map_state().entries.items()
        if isinstance(s, Snapshot)
    }

    # Clustering enabled.
    view = case.inputs(case.snapshots).view(case.t)
    converted = convert_map(view, lp)
    out = clusters_at(view, converted, np_, enabled, lp)
    assert out is not None
    assert list(out) == list(converted)
    state = view.map_state()
    for key, conv in converted.items():
        where = f"t={case.t} {key[0]} {key[1]}"
        got = out[key]
        if isinstance(conv, MissingPrice):  # the converter's missing price passes through
            assert got == conv, f"{where}: got {got!r}, want {conv!r}"
            continue
        snapshot = state.entries[key]
        assert isinstance(snapshot, Snapshot)
        width = expected_width(case, conv.family)
        if isinstance(width, MissingPrice):  # no NQ width without both futures prices
            assert got == width, f"{where}: got {got!r}, want {width!r}"
            continue
        assert isinstance(got, tuple), f"{where}: got {got!r}, want clusters"
        check_clusters(snapshot, conv, got, width, np_, where)

        # Req 6.13: clustering leaves King, Floor, Ceiling and Gatekeepers unchanged.
        labels_on = label_set(classify(snapshot, np_))
        assert labels_on == labels_off[key], (
            f"{where}: labels {labels_on!r} with clustering, {labels_off[key]!r} without"
        )
