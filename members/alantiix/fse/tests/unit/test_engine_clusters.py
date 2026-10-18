"""Unit tests for Node clustering over converted levels (design §6 rule 9).

Snapshots and bars are synthetic and read through the real historical
MarketView; levels come from the real Level_Converter. Prices are chosen so
offsets, ratios and widths are exact in binary.

**Validates: Requirements 6.12, 6.13**
"""

from __future__ import annotations

import math
from datetime import date, time

import pytest

from fse.config.schema.nodes import ClusteringConfig, NodesConfig
from fse.engine.clusters import Cluster, ClusterWidths, cluster_nodes, cluster_widths, clusters_at
from fse.engine.levels import ConvertedMap, LevelParams, convert, convert_map
from fse.engine.nodes import NodeParams, classify
from fse.engine.types import Bar, MissingPrice, Snapshot
from fse.pit.market_view import HistoricalInputs, HistoricalMarketView
from fse.pit.protocols import SymMetric
from fse.projectx.models import price_to_ticks
from fse.timekit import NS_PER_SECOND, ny_instant

S = NS_PER_SECOND
T0 = ny_instant(date(2026, 3, 5), time(10, 0))
VIEW_ID = "view-test"
LP = LevelParams()
NP = NodeParams.from_config(NodesConfig())
ON = ClusteringConfig(enabled=True)


def snap(
    symbol: str, spot: float, strikes: tuple[float, ...], values: tuple[float, ...]
) -> Snapshot:
    return Snapshot(
        symbol=symbol,
        metric="gamma",
        view_id=VIEW_ID,
        as_of_ns=T0,
        as_of_raw=f"raw-{T0}",
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


def bar(instrument: str, close: float, close_ns: int = T0) -> Bar:
    c_t = price_to_ticks(close)
    return Bar(
        instrument, f"{instrument}H6", 60, close_ns - 60 * S, close_ns,
        close, close, close, close, 10.0, c_t, c_t, c_t, c_t, "atlas",
    )  # fmt: skip


def view(
    snapshots: list[Snapshot], bars: list[Bar], symbols: tuple[str, ...] = ("SPX", "QQQ")
) -> HistoricalMarketView:
    return HistoricalInputs(symbols=symbols, view_id=VIEW_ID, snapshots=snapshots, bars=bars).view(
        T0
    )


# SPX offset is 5810 - 5800 = 10 points. Converted levels, in points:
#   5800, 5805, 5810 | 5825.25 | 5840 | 5845.25, and the non-Node at 5842.5.
SPX = snap(
    "SPX",
    5800.0,
    (5790.0, 5795.0, 5800.0, 5815.25, 5830.0, 5832.5, 5835.25),
    (2.0e9, -1.0e9, 1.0e9, 4.0e9, 3.0e9, 0.1e9, 1.0e9),
)
# QQQ ratio is 20000 / 500 = 40; levels 20000, 20020, 20040 | 20080.
QQQ = snap("QQQ", 500.0, (500.0, 500.5, 501.0, 502.0), (1.0e9, 1.0e9, -1.0e9, 1.0e9))
BARS = [bar("MES", 5810.0), bar("MNQ", 20000.0)]


def converted(s: Snapshot, v: HistoricalMarketView) -> ConvertedMap:
    out = convert(s, v, LP)
    assert isinstance(out, ConvertedMap), out
    return out


def shape(clusters: tuple[Cluster, ...]) -> list[tuple[int, tuple[float, ...], float]]:
    return [(c.rank, c.strikes, c.abs_sum) for c in clusters]


# ---------------------------------------------------------------- grouping and rank


def test_es_nodes_split_where_the_gap_exceeds_five_points() -> None:
    v = view([SPX], BARS)
    out = cluster_nodes(SPX, classify(SPX, NP), converted(SPX, v), 5.0)
    # Gaps of exactly 5 merge; 15.25, 14.75 and 5.25 split. The non-Node at
    # 5832.5 does not bridge 5830 and 5835.25. Equal sums rank lower level first.
    assert shape(out) == [
        (1, (5790.0, 5795.0, 5800.0), 4.0e9),
        (2, (5815.25,), 4.0e9),
        (3, (5830.0,), 3.0e9),
        (4, (5835.25,), 1.0e9),
    ]
    assert out[0].levels == (23200, 23220, 23240)
    assert (out[0].lo, out[0].hi) == (23200, 23240)
    assert [c.levels for c in out[1:]] == [(23301,), (23360,), (23381,)]


def test_a_wider_es_width_merges_more_nodes() -> None:
    v = view([SPX], BARS)
    out = cluster_nodes(SPX, classify(SPX, NP), converted(SPX, v), 5.25)
    assert shape(out) == [
        (1, (5790.0, 5795.0, 5800.0), 4.0e9),
        (2, (5815.25,), 4.0e9),
        (3, (5830.0, 5835.25), 4.0e9),
    ]


def test_ranks_follow_summed_absolute_value() -> None:
    s = snap("SPX", 5800.0, (5790.0, 5800.0, 5820.0), (1.0e9, -2.0e9, 4.0e9))
    out = cluster_nodes(s, classify(s, NP), converted(s, view([s], BARS)), 5.0)
    assert shape(out) == [(3, (5790.0,), 1.0e9), (2, (5800.0,), 2.0e9), (1, (5820.0,), 4.0e9)]


def test_no_nodes_give_no_clusters() -> None:
    s = snap("SPX", 5800.0, (5790.0, 5800.0), (0.0, 0.0))
    assert cluster_nodes(s, classify(s, NP), converted(s, view([s], BARS)), 5.0) == ()


def test_clustering_leaves_the_labels_unchanged() -> None:
    v = view([SPX], BARS)
    before = classify(SPX, NP)
    out = clusters_at(v, convert_map(v, LP), NP, ON, LP)
    assert out is not None
    after = classify(SPX, NP)
    assert after == before
    assert (after.king, after.floor, after.ceiling) == (5815.25, 5790.0, 5815.25)
    # The King's cluster ranks 2 on a tie, and the King label does not move.
    spx = out[("SPX", "gamma")]
    assert isinstance(spx, tuple)
    assert next(c.rank for c in spx if 5815.25 in c.strikes) == 2


# ---------------------------------------------------------------- widths


def test_nq_width_scales_the_es_width_by_nq_over_es_at_t() -> None:
    v = view([QQQ], [bar("MES", 5000.0), bar("MNQ", 20000.0)])
    assert cluster_widths(v, ON, LP) == ClusterWidths(5.0, 20.0)
    custom = ClusteringConfig(enabled=True, es_width_pts=2.5)
    assert cluster_widths(v, custom, LP) == ClusterWidths(2.5, 10.0)
    uneven = view([QQQ], BARS)
    assert cluster_widths(uneven, ON, LP).nq_pts == 5.0 * 20000.0 / 5810.0


def test_nq_levels_cluster_with_the_scaled_width() -> None:
    v = view([QQQ], [bar("MES", 5000.0), bar("MNQ", 20000.0)])
    out = clusters_at(v, convert_map(v, LP), NP, ON, LP)
    assert out is not None
    qqq = out[("QQQ", "gamma")]
    assert isinstance(qqq, tuple)
    # Width 20: gaps of 20 merge, the gap of 40 splits.
    assert shape(qqq) == [(1, (500.0, 500.5, 501.0), 3.0e9), (2, (502.0,), 1.0e9)]
    assert qqq[0].levels == (80000, 80080, 80160)


def test_width_uses_the_configured_instruments() -> None:
    params = LevelParams(es_instrument="ES", nq_instrument="NQ")
    v = view([QQQ], [*BARS, bar("ES", 4000.0), bar("NQ", 16000.0)])
    assert cluster_widths(v, ON, params) == ClusterWidths(5.0, 20.0)


# ---------------------------------------------------------------- whole Map_State


def test_disabled_clustering_returns_none() -> None:
    v = view([SPX, QQQ], BARS)
    assert clusters_at(v, convert_map(v, LP), NP, ClusteringConfig(), LP) is None


def test_clusters_at_covers_every_converted_entry_in_order() -> None:
    v = view([SPX, QQQ], BARS)
    conv = convert_map(v, LP)
    out = clusters_at(v, conv, NP, ON, LP)
    assert out is not None
    assert list(out) == list(conv)
    assert out[("SPX", "gamma")] == cluster_nodes(SPX, classify(SPX, NP), converted(SPX, v), 5.0)
    assert out[("SPX", "vanna")] == MissingPrice("SPX")  # no Snapshot: the converter's result
    with pytest.raises(TypeError):
        out[("SPX", "gamma")] = ()  # type: ignore[index]


def test_missing_es_price_leaves_nq_without_a_width() -> None:
    v = view([QQQ], [bar("MNQ", 20000.0)])
    conv = convert_map(v, LP)
    assert isinstance(conv[("QQQ", "gamma")], ConvertedMap)
    out = clusters_at(v, conv, NP, ON, LP)
    assert out is not None
    assert out[("QQQ", "gamma")] == MissingPrice("MES")


def test_bad_futures_closes_name_the_contract_or_instrument() -> None:
    zero_es = view([QQQ], [bar("MES", 0.0), bar("MNQ", 20000.0)])
    assert cluster_widths(zero_es, ON, LP) == ClusterWidths(5.0, MissingPrice("MESH6"))
    no_nq = view([SPX], [bar("MES", 5810.0)])
    assert cluster_widths(no_nq, ON, LP) == ClusterWidths(5.0, MissingPrice("MNQ"))
    # ES clusters need no price for their width.
    out = clusters_at(no_nq, convert_map(no_nq, LP), NP, ON, LP)
    assert out is not None
    assert isinstance(out[("SPX", "gamma")], tuple)
    assert out[("QQQ", "gamma")] == MissingPrice("QQQ")


def test_converted_entry_without_a_snapshot_raises() -> None:
    v = view([SPX], BARS)
    stray: dict[SymMetric, ConvertedMap | MissingPrice] = {("QQQ", "gamma"): converted(SPX, v)}
    with pytest.raises(ValueError, match="no Map_State Snapshot"):
        clusters_at(v, stray, NP, ON, LP)


# ---------------------------------------------------------------- validation


def test_cluster_nodes_rejects_another_snapshots_levels_and_bad_widths() -> None:
    v = view([SPX, QQQ], BARS)
    spx_levels = converted(SPX, v)
    with pytest.raises(ValueError, match="do not belong"):
        cluster_nodes(QQQ, classify(QQQ, NP), spx_levels, 5.0)
    for width in (0.0, -5.0, math.nan):
        with pytest.raises(ValueError, match="above 0"):
            cluster_nodes(SPX, classify(SPX, NP), spx_levels, width)


@pytest.mark.parametrize(
    ("kw", "message"),
    [
        ({"rank": 0}, "rank"),
        ({"rank": True}, "rank"),
        ({"strikes": (), "levels": ()}, "at least one Node"),
        ({"levels": (1, 2)}, "parallel"),
        ({"strikes": (2.0, 1.0), "levels": (8, 4)}, "ascending"),
    ],
)
def test_cluster_rejects_bad_shapes(kw: dict[str, object], message: str) -> None:
    fields: dict[str, object] = {"rank": 1, "strikes": (1.0,), "levels": (4,), "abs_sum": 1.0}
    with pytest.raises(ValueError, match=message):
        Cluster(**{**fields, **kw})  # type: ignore[arg-type]
