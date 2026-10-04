"""Optional Node clusters over converted levels (design §6 rule 9, Req 6.12-6.13).

Clusters need converted levels, so they run after the Level_Converter:

- **Grouping** (Req 6.12). A Snapshot's Nodes (``fse.engine.nodes.classify``)
  are sorted by converted level, then by strike. A cluster is a maximal run in
  which every gap between consecutive levels is at most the cluster width, so
  a Node with no neighbor within the width is a cluster of one.
- **Width** (Req 6.12). ES levels use ``nodes.clustering.es_width_pts``. NQ
  levels use ``es_width_pts x NQ / ES``, evaluated left to right, with NQ and
  ES the Futures_Price of the configured instruments (``data.instruments``):
  the tick close of the latest 1-minute bar closed at or before ``t``. No bar
  gives ``MissingPrice`` naming the instrument; a close at or below 0 names
  the bar's contract. ES clusters never need a price.
- **Rank** (Req 6.13). Each cluster sums the absolute values of its Nodes
  (``math.fsum``, so the sum does not depend on order). Rank 1 is the largest
  sum; equal sums rank by level, lower first, so ranks are ``1..n`` with no
  repeats.
- **Labels** (Req 6.13). Clustering reads the labels and never changes them:
  King, Floor, Ceiling and Gatekeeper stay exactly as ``classify`` returns.

Gaps are compared in points (``ticks / 4``), which is exact for tick levels.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise
from types import MappingProxyType

from fse.config.schema.nodes import ClusteringConfig
from fse.engine.levels import ConvertedMap, LevelFamily, LevelParams, ticks_to_points
from fse.engine.nodes import NodeLabels, NodeParams, classify
from fse.engine.types import MissingPrice, Snapshot, Ticks
from fse.pit.protocols import MarketView, SymMetric

__all__ = [
    "Cluster",
    "ClusterWidths",
    "MapClusters",
    "cluster_nodes",
    "cluster_widths",
    "clusters_at",
]

type MapClusters = Mapping[SymMetric, tuple[Cluster, ...] | MissingPrice]
"""Clusters per Map_State (symbol, metric), or the price that is missing."""


@dataclass(frozen=True, slots=True)
class Cluster:
    """One run of Nodes whose consecutive converted levels are within the width.

    ``strikes`` and ``levels`` are parallel, in ascending level order (then
    strike). ``abs_sum`` is the summed absolute value of the Nodes, and
    ``rank`` is 1 for the largest ``abs_sum`` in the Snapshot.
    """

    rank: int
    strikes: tuple[float, ...]
    levels: tuple[Ticks, ...]
    abs_sum: float

    def __post_init__(self) -> None:
        if isinstance(self.rank, bool) or not isinstance(self.rank, int) or self.rank < 1:
            raise ValueError(f"Cluster.rank must be an integer of at least 1, got {self.rank!r}")
        if not self.strikes or len(self.strikes) != len(self.levels):
            raise ValueError("a Cluster needs at least one Node, with parallel strikes and levels")
        if any(a > b for a, b in pairwise(self.levels)):
            raise ValueError("Cluster.levels must be in ascending order")

    @property
    def lo(self) -> Ticks:
        """The lowest converted level in the cluster."""
        return self.levels[0]

    @property
    def hi(self) -> Ticks:
        """The highest converted level in the cluster."""
        return self.levels[-1]


@dataclass(frozen=True, slots=True)
class ClusterWidths:
    """The cluster width in points per level family at one Decision_Time."""

    es_pts: float
    nq_pts: float | MissingPrice

    def for_family(self, family: LevelFamily) -> float | MissingPrice:
        return self.es_pts if family == "ES" else self.nq_pts


def _require_width(width_pts: float) -> None:
    if not width_pts > 0:
        raise ValueError(f"a cluster width must be above 0 points, got {width_pts!r}")


def _futures_points(view: MarketView, instrument: str) -> float | MissingPrice:
    """Futures_Price of ``instrument`` at ``view.t`` in points, or the missing price."""
    bar = view.last_bar_closed_at_or_before(instrument, view.t)
    if bar is None:
        return MissingPrice(instrument)
    if bar.c_t is None or bar.c_t <= 0:
        return MissingPrice(bar.contract)
    return ticks_to_points(bar.c_t)


def cluster_widths(
    view: MarketView, clustering: ClusteringConfig, levels: LevelParams
) -> ClusterWidths:
    """The ES width and the NQ width ``es_width_pts x NQ / ES`` at ``view.t`` (Req 6.12)."""
    es_width = clustering.es_width_pts
    _require_width(es_width)
    nq = _futures_points(view, levels.instrument("NQ"))
    es = _futures_points(view, levels.instrument("ES"))
    if isinstance(nq, MissingPrice):
        return ClusterWidths(es_width, nq)
    if isinstance(es, MissingPrice):
        return ClusterWidths(es_width, es)
    return ClusterWidths(es_width, es_width * nq / es)


def cluster_nodes(
    snapshot: Snapshot, labels: NodeLabels, converted: ConvertedMap, width_pts: float
) -> tuple[Cluster, ...]:
    """The clusters of ``labels.nodes`` by converted level, in ascending level order.

    ``labels`` must be ``classify(snapshot, ...)`` and ``converted`` the
    Level_Converter output for ``snapshot``. A Snapshot with no Nodes has no
    clusters. Raises ``ValueError`` for a width that is not above 0 or a
    ``converted`` of another Snapshot, and ``KeyError`` for a Node strike the
    Snapshot does not list.
    """
    _require_width(width_pts)
    if (converted.symbol, converted.metric, converted.as_of_ns, converted.strikes) != (
        snapshot.symbol,
        snapshot.metric,
        snapshot.as_of_ns,
        snapshot.strikes,
    ):
        raise ValueError(
            f"the converted levels of {converted.symbol} {converted.metric} at "
            f"{converted.as_of_ns} do not belong to the {snapshot.symbol} {snapshot.metric} "
            f"Snapshot at {snapshot.as_of_ns}"
        )
    if not labels.nodes:
        return ()
    abs_by_strike: dict[float, float] = {}
    for strike, value in zip(snapshot.strikes, snapshot.values, strict=True):
        abs_by_strike.setdefault(strike, abs(value))
    nodes = sorted((converted.level_of(k), k) for k in labels.nodes)

    runs: list[list[tuple[Ticks, float]]] = [[nodes[0]]]
    for prev, node in pairwise(nodes):
        if ticks_to_points(node[0] - prev[0]) <= width_pts:
            runs[-1].append(node)
        else:
            runs.append([node])

    sums = [math.fsum(abs_by_strike[k] for _, k in run) for run in runs]
    by_rank = sorted(range(len(runs)), key=lambda i: (-sums[i], i))
    rank_of = {i: r for r, i in enumerate(by_rank, start=1)}
    return tuple(
        Cluster(
            rank=rank_of[i],
            strikes=tuple(k for _, k in run),
            levels=tuple(level for level, _ in run),
            abs_sum=sums[i],
        )
        for i, run in enumerate(runs)
    )


def clusters_at(
    view: MarketView,
    converted: Mapping[SymMetric, ConvertedMap | MissingPrice],
    node_params: NodeParams,
    clustering: ClusteringConfig,
    levels: LevelParams,
) -> MapClusters | None:
    """Clusters for every entry of ``converted`` (``convert_map(view, levels)``) at ``view.t``.

    ``None`` when clustering is disabled. An entry is the Level_Converter's
    ``MissingPrice`` when it had none, the NQ width's ``MissingPrice`` when an
    NQ entry has no width, and the entry's clusters otherwise. Labels come
    from ``classify``, unchanged. Raises ``ValueError`` for a converted entry
    with no Map_State Snapshot.
    """
    if not clustering.enabled:
        return None
    widths = cluster_widths(view, clustering, levels)
    state = view.map_state()
    out: dict[SymMetric, tuple[Cluster, ...] | MissingPrice] = {}
    for key, conv in converted.items():
        if isinstance(conv, MissingPrice):
            out[key] = conv
            continue
        snapshot = state.entries.get(key)
        if not isinstance(snapshot, Snapshot):
            raise ValueError(f"converted levels for {key[0]} {key[1]} have no Map_State Snapshot")
        width = widths.for_family(conv.family)
        if isinstance(width, MissingPrice):
            out[key] = width
            continue
        out[key] = cluster_nodes(snapshot, classify(snapshot, node_params), conv, width)
    return MappingProxyType(out)
