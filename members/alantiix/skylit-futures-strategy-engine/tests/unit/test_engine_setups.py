"""Unit tests for the Setup_Detector (design §10).

Synthetic SPX and QQQ gamma Maps with hand-built conversions: SPX with offset
0, so a strike's MES level is the strike in points (5780 is 23120 ticks), and
QQQ with ratio 40, so NQ levels are 40 x the strike. Properties 31-33 (tasks
13.5-13.7) cover the general case.

**Validates: Requirements 8.11, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8,
10.9, 10.10, 10.11, 10.12, 10.13, 10.14**
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import date, time
from typing import Any

import pytest

from fse.config.schema.data import DataConfig
from fse.config.schema.exits import ExitsConfig
from fse.config.schema.nodes import NodesConfig
from fse.config.schema.patterns import DETECTOR_IDS, PatternsConfig
from fse.engine.chart import ChartFeatures
from fse.engine.levels import Conversion, ConvertedMap, band, level_family
from fse.engine.nodes import NodeParams, classify
from fse.engine.regime import RegimeResult
from fse.engine.setups.base import Source
from fse.engine.setups.beach_ball import select_beach_ball
from fse.engine.setups.floor_ceiling import select_empty_basement, select_floor_ceiling
from fse.engine.setups.registry import (
    DETECTORS,
    REGISTRY,
    DetectContext,
    DetectorParams,
    construct,
    detect_setups,
    detector,
    sources,
)
from fse.engine.setups.rug import select_reverse_rug, select_rug
from fse.engine.setups.trend import select_trend_follow
from fse.engine.setups.whipsaw import select_whipsaw_fade
from fse.engine.taps import Tap, TapView
from fse.engine.types import (
    CandidateSetup,
    ConversionMethod,
    DetectionSkip,
    MapGrade,
    MissingInput,
    MissingPrice,
    Regime,
    SetupInputs,
    SetupKey,
    Snapshot,
    SnapshotAsOf,
    SourceNodeRef,
)
from fse.pit.protocols import MapState, SymMetric
from fse.timekit import NS_PER_SECOND, ny_instant

SESSION = date(2026, 3, 5)
T0 = ny_instant(SESSION, time(10, 0))
AS_OF = T0 - 30 * NS_PER_SECOND
NODE_PARAMS = NodeParams.from_config(NodesConfig())
SPX: SymMetric = ("SPX", "gamma")
QQQ: SymMetric = ("QQQ", "gamma")

# Spot 5800; every strike is a Node (King 3e9, node_fraction 0.2). Floor 5750
# (not Empty_Basement: 5700 is within the 58-point lookout), Ceiling and King
# 5850, Gatekeepers 5780 and 5820 (both Pikas).
SPX_PAIRS: tuple[tuple[float, float], ...] = (
    (5700.0, -1.0e9),
    (5750.0, 2.0e9),
    (5780.0, 1.0e9),
    (5820.0, 1.5e9),
    (5850.0, -3.0e9),
)
# QQQ spot 500: the SPX Map divided by 11.6, so 498 is a Pika Gatekeeper below spot.
QQQ_PAIRS: tuple[tuple[float, float], ...] = (
    (490.0, -1.0e9),
    (495.0, 2.0e9),
    (498.0, 1.0e9),
    (502.0, 1.5e9),
    (505.0, -3.0e9),
)


def snapshot(symbol: str, spot: float, pairs: tuple[tuple[float, float], ...]) -> Snapshot:
    return Snapshot(
        symbol=symbol,
        metric="gamma",
        view_id="v",
        as_of_ns=AS_OF,
        as_of_raw="2026-03-05T14:59:30Z",
        spot=spot,
        previous_close=None,
        strikes=tuple(k for k, _ in pairs),
        values=tuple(v for _, v in pairs),
        node_types=None,
        expirations=("2026-03-05",),
        resolution="1s",
        source_endpoint="range",
        extra_json="{}",
    )


def converted(
    s: Snapshot, method: ConversionMethod, factor: float, half_width: float
) -> ConvertedMap:
    family = level_family(s.symbol)
    instrument = "MES" if family == "ES" else "MNQ"
    conv = Conversion(
        symbol=s.symbol,
        family=family,
        instrument=instrument,
        contract=f"{instrument}H6",
        method=method,
        factor=factor,
        futures_close=s.spot + factor if method == "offset" else s.spot * factor,
        spot=s.spot,
        as_of_ns=s.as_of_ns,
        paired_bar_close_ns=s.as_of_ns,
    )
    levels = tuple(conv.level(k) for k in s.strikes)
    return ConvertedMap(
        symbol=s.symbol,
        metric=s.metric,
        as_of_ns=s.as_of_ns,
        conversion=conv,
        band_half_width_pts=half_width,
        strikes=s.strikes,
        levels=levels,
        bands=tuple(band(level, half_width) for level in levels),
    )


SPX_SNAP = snapshot("SPX", 5800.0, SPX_PAIRS)
QQQ_SNAP = snapshot("QQQ", 500.0, QQQ_PAIRS)
SPX_MAP = converted(SPX_SNAP, "offset", 0.0, 5.0)
QQQ_MAP = converted(QQQ_SNAP, "ratio", 40.0, 20.0)


def pts(points: float) -> int:
    """A price in points as ticks; exact for quarter points."""
    return round(points * 4)


def regime_result(regime: Regime) -> RegimeResult:
    return RegimeResult(regime, 1.0, 0.5, 1.0, 0.5, -1.0, False)


def context(
    *,
    futures: Mapping[str, int],
    snapshots: tuple[Snapshot, ...] = (SPX_SNAP,),
    maps: Mapping[SymMetric, ConvertedMap | MissingPrice] | None = None,
    regime: Regime | MissingInput = "Negative_Gamma",
    grade: MapGrade | MissingInput = "Neutral_Map",
    taps: tuple[Tap, ...] = (),
) -> DetectContext:
    entries = {(s.symbol, s.metric): s for s in snapshots}
    default_maps: dict[SymMetric, ConvertedMap | MissingPrice] = {SPX: SPX_MAP, QQQ: QQQ_MAP}
    chosen = default_maps if maps is None else maps
    return DetectContext(
        t=T0,
        map_state=MapState(T0, entries),
        labels={k: classify(s, NODE_PARAMS) for k, s in entries.items()},
        converted={k: chosen[k] for k in entries},
        regime=regime if isinstance(regime, MissingInput) else regime_result(regime),
        grade=grade,
        chart=ChartFeatures(T0, ()),
        taps=TapView(T0, SESSION, taps, {}, {}),
        futures_price=futures,
    )


def params(**patterns: dict[str, Any]) -> DetectorParams:
    return DetectorParams(patterns=PatternsConfig.model_validate(patterns))


def only(*enabled: str, **overrides: dict[str, Any]) -> DetectorParams:
    """Parameters with only ``enabled`` detectors on; ``overrides`` add keys per detector."""
    cfg: dict[str, dict[str, Any]] = {d: {"enabled": d in enabled} for d in DETECTOR_IDS}
    for d, extra in overrides.items():
        cfg[d] = {**cfg[d], **extra}
    return params(**cfg)


def with_exits(p: DetectorParams, exits: dict[str, Any]) -> DetectorParams:
    return replace(p, exits=ExitsConfig.model_validate(exits))


GK_LONG_CTX = context(futures={"MES": pts(5781.0)})


def spx_inputs(futures: int, stop_rule: Any) -> SetupInputs:
    return SetupInputs(
        map_as_of=(SnapshotAsOf("SPX", "gamma", AS_OF),),
        source_spot=5800.0,
        futures_price=futures,
        conversion_method="offset",
        conversion_factor=0.0,
        band_half_width_pts=5.0,
        regime="Negative_Gamma",
        map_grade="Neutral_Map",
        stop_rule=stop_rule,
    )


# ---------------------------------------------------------------- the registry


def test_registry_holds_the_eight_detectors_in_schema_order() -> None:
    assert tuple(d.id for d in REGISTRY) == DETECTOR_IDS
    assert tuple(DETECTORS) == DETECTOR_IDS
    assert detector("rug").pattern == "rug"
    assert detector("floor_ceiling_bounce_empty_basement").pattern == "floor_ceiling_bounce"
    with pytest.raises(ValueError, match="vwap_fade"):
        detector("vwap_fade")


def test_no_detector_enabled_yields_nothing() -> None:
    assert detect_setups(GK_LONG_CTX, only()) == ()


def test_output_is_each_enabled_detectors_own_output_in_registry_order() -> None:
    # Whipsaw with price at the Floor: floor_ceiling_bounce and whipsaw_fade both emit.
    ctx = context(futures={"MES": pts(5752.0)}, regime="Whipsaw")
    everything = detect_setups(ctx, DetectorParams())
    solo = [detect_setups(ctx, only(d)) for d in DETECTOR_IDS]
    assert everything == tuple(x for out in solo for x in out)
    assert [x.detector_id for x in everything if isinstance(x, CandidateSetup)] == [
        "floor_ceiling_bounce",
        "whipsaw_fade",
    ]
    for d in DETECTOR_IDS:
        assert detect_setups(ctx, only(d)) == DETECTORS[d].detect(ctx, DetectorParams())


# ---------------------------------------------------------------- construction


def test_gatekeeper_fade_long_setup() -> None:
    out = detect_setups(GK_LONG_CTX, DetectorParams())
    # Stop: the next Node below, 5750. Target: the 5820 opposition Node (1.5e9 >= 85%
    # of 1e9) is nearer than 3R (23480).
    assert out == (
        CandidateSetup(
            key=SetupKey("MES", "gatekeeper_fade", 5780.0, "long", SESSION, 1),
            detector_id="gatekeeper_fade",
            t=T0,
            entry=pts(5780.0),
            stop=pts(5750.0),
            targets=(pts(5820.0),),
            exit_mode="opposition_or_fixed_r",
            source=SourceNodeRef("SPX", "gamma", 5780.0, 1.0e9, pts(5780.0), (5775.0, 5785.0)),
            inputs=spx_inputs(pts(5781.0), "one_node_beyond"),
        ),
    )


def test_gatekeeper_fade_short_setup_uses_the_node_above() -> None:
    ctx = context(futures={"MES": pts(5818.0)})
    (setup,) = detect_setups(ctx, only("gatekeeper_fade"))
    assert isinstance(setup, CandidateSetup)
    assert setup.key == SetupKey("MES", "gatekeeper_fade", 5820.0, "short", SESSION, 1)
    # Risk 120 ticks; the 5750 opposition Node (2e9) is nearer than 3R (22920).
    assert (setup.entry, setup.stop, setup.targets) == (pts(5820), pts(5850), (pts(5750),))


@pytest.mark.parametrize(
    ("price", "armed"),
    [(5790.0, True), (5770.0, True), (5790.25, False), (5769.75, False)],
)
def test_arming_distance_is_inclusive(price: float, armed: bool) -> None:
    out = detect_setups(context(futures={"MES": pts(price)}), only("gatekeeper_fade"))
    assert [x.key.source_strike for x in out] == ([5780.0] if armed else [])


def test_no_futures_price_means_no_setup() -> None:
    assert detect_setups(context(futures={"MNQ": pts(5781.0)}), DetectorParams()) == ()


def test_nq_arming_scales_by_the_qqq_ratio() -> None:
    snaps = (SPX_SNAP, QQQ_SNAP)
    # 498 x 40 = 19920; arming $1.00 x 40 = 40 points.
    near = context(futures={"MNQ": pts(19920.0 + 40.0)}, snapshots=snaps)
    far = context(futures={"MNQ": pts(19920.0 + 40.25)}, snapshots=snaps)
    (setup,) = detect_setups(near, only("gatekeeper_fade"))
    assert detect_setups(far, only("gatekeeper_fade")) == ()
    assert isinstance(setup, CandidateSetup)
    assert setup.key == SetupKey("MNQ", "gatekeeper_fade", 498.0, "long", SESSION, 1)
    assert (setup.entry, setup.stop) == (pts(19920.0), pts(19800.0))
    assert setup.targets == (pts(20080.0),)
    assert (setup.inputs.conversion_method, setup.inputs.conversion_factor) == ("ratio", 40.0)
    assert setup.inputs.band_half_width_pts == 20.0
    assert setup.source.band == (19900.0, 19940.0)


def test_nq_arming_distance_is_exact_at_the_boundary() -> None:
    # $0.29 x a ratio of 50 is 14.5 points (58 ticks) exactly; in floats,
    # 0.29 x 50 x 4 is 57.99999999999999.
    maps: dict[SymMetric, ConvertedMap | MissingPrice] = {
        SPX: SPX_MAP,
        QQQ: converted(QQQ_SNAP, "ratio", 50.0, 20.0),
    }
    p = only("gatekeeper_fade", gatekeeper_fade={"arming": {"nq_qqq_usd": 0.29}})
    level = pts(498.0 * 50)
    snaps = (SPX_SNAP, QQQ_SNAP)
    near = context(futures={"MNQ": level + 58}, snapshots=snaps, maps=maps)
    far = context(futures={"MNQ": level + 59}, snapshots=snaps, maps=maps)
    assert [x.key for x in detect_setups(near, p)] == [
        SetupKey("MNQ", "gatekeeper_fade", 498.0, "long", SESSION, 1)
    ]
    assert detect_setups(far, p) == ()


@pytest.mark.parametrize(
    ("below", "stop", "rule"),
    [(5979.0, pts(5979.0), "one_node_beyond"), (5978.75, pts(5995.0) - 8, "fixed_ticks")],
)
def test_one_node_beyond_lookout_is_exact_at_the_boundary(
    below: float, stop: int, rule: str
) -> None:
    # 0.35% of the 6000.00 paired close is 21 points (84 ticks) exactly; in floats,
    # 0.35 / 100 x 6000 x 4 is 83.99999999999999. A Node one tick farther is out.
    p = only("gatekeeper_fade", gatekeeper_fade={"stop_lookout_pct": 0.35})
    cfg = p.patterns.gatekeeper_fade
    snap = snapshot("SPX", 6000.0, ((below, 3.0e9), (6000.0, 3.0e9)))
    ctx = context(
        futures={"MES": pts(6000.0)},
        snapshots=(snap,),
        maps={SPX: converted(snap, "offset", 0.0, 5.0)},
    )
    (src,) = sources(ctx, p, "gamma")
    setup = construct("gatekeeper_fade", "gatekeeper_fade", cfg, ctx, p, src, (6000.0, "long"))
    assert isinstance(setup, CandidateSetup)
    assert (setup.stop, setup.inputs.stop_rule) == (stop, rule)


def test_es_levels_come_only_from_the_es_source_symbol() -> None:
    spy = replace(SPX_SNAP, symbol="SPY")
    ctx = context(
        futures={"MES": pts(5781.0)},
        snapshots=(spy,),
        maps={("SPY", "gamma"): replace(SPX_MAP, symbol="SPY")},
    )
    assert detect_setups(ctx, DetectorParams()) == ()


def test_entry_offset_is_capped_at_the_band_edge_and_rounded_toward_the_level() -> None:
    # Half-width 5.1: the long band top 5785.1 caps +30 ticks at 23140 (5785.0).
    maps: dict[SymMetric, ConvertedMap | MissingPrice] = {
        SPX: converted(SPX_SNAP, "offset", 0, 5.1)
    }
    p = only(
        "gatekeeper_fade", gatekeeper_fade={"entry_offset_ticks": 30, "stop_rule": "fixed_ticks"}
    )
    (long,) = detect_setups(context(futures={"MES": pts(5781.0)}, maps=maps), p)
    assert isinstance(long, CandidateSetup)
    # Fixed stop: the band bottom 5774.9 rounds down to 5774.75, then 8 ticks below.
    assert (long.entry, long.stop) == (pts(5785.0), pts(5774.75) - 8)
    assert long.inputs.stop_rule == "fixed_ticks"
    # Short: the band bottom 5814.9 caps -30 ticks at 23260 (5815.0); the stop is 8 ticks
    # above the band top 5825.1, rounded up to 5825.25.
    (short,) = detect_setups(context(futures={"MES": pts(5818.0)}, maps=maps), p)
    assert isinstance(short, CandidateSetup)
    assert (short.entry, short.stop) == (pts(5815.0), pts(5825.25) + 8)
    # Risk 49 ticks: 3R (147 ticks) is nearer than the 5750 opposition Node.
    assert short.targets == (pts(5815.0) - 147,)


def test_small_entry_offset_moves_the_entry_inside_the_band() -> None:
    p = only("gatekeeper_fade", gatekeeper_fade={"entry_offset_ticks": -3})
    (setup,) = detect_setups(GK_LONG_CTX, p)
    assert isinstance(setup, CandidateSetup)
    assert setup.entry == pts(5780.0) - 3


def test_one_node_beyond_falls_back_to_fixed_ticks_outside_the_lookout() -> None:
    # 0.5% of 5800 is 29 points: the 5750 Node is 30 points below 5780.
    p = only("gatekeeper_fade", gatekeeper_fade={"stop_lookout_pct": 0.5})
    (setup,) = detect_setups(GK_LONG_CTX, p)
    assert isinstance(setup, CandidateSetup)
    assert setup.stop == pts(5775.0) - 8
    assert setup.inputs.stop_rule == "fixed_ticks"


def test_band_edge_invalidation_measures_the_lookout_from_the_band() -> None:
    # From the band bottom 5775 the 5750 Node is 25 points away, inside 29.
    p = only(
        "gatekeeper_fade",
        gatekeeper_fade={"stop_lookout_pct": 0.5, "invalidation": "band_edge"},
    )
    (setup,) = detect_setups(GK_LONG_CTX, p)
    assert isinstance(setup, CandidateSetup)
    assert (setup.stop, setup.inputs.stop_rule) == (pts(5750.0), "one_node_beyond")


def test_the_exits_stop_rule_applies_unless_the_detector_sets_one() -> None:
    exits = {"global": {"mode": "opposition_or_fixed_r", "stop_rule": "fixed_ticks"}}
    inherited = with_exits(only("gatekeeper_fade"), exits)
    (setup,) = detect_setups(GK_LONG_CTX, inherited)
    assert isinstance(setup, CandidateSetup)
    assert (setup.stop, setup.inputs.stop_rule) == (pts(5775.0) - 8, "fixed_ticks")
    own = with_exits(
        only("gatekeeper_fade", gatekeeper_fade={"stop_rule": "one_node_beyond"}), exits
    )
    (setup,) = detect_setups(GK_LONG_CTX, own)
    assert isinstance(setup, CandidateSetup)
    assert (setup.stop, setup.inputs.stop_rule) == (pts(5750.0), "one_node_beyond")


def test_the_exit_mode_follows_the_regime() -> None:
    ctx = context(futures={"MES": pts(5781.0)}, regime="Positive_Gamma")
    (setup,) = detect_setups(ctx, only("gatekeeper_fade"))
    assert isinstance(setup, CandidateSetup)
    assert (setup.exit_mode, setup.targets) == ("next_node", (pts(5820.0),))
    assert setup.inputs.regime == "Positive_Gamma"


def test_tap_seq_counts_the_source_nodes_ended_taps() -> None:
    def tap(seq: int, ended: bool) -> Tap:
        start = T0 - (40 - 10 * seq) * 60 * NS_PER_SECOND
        close = start + 60 * NS_PER_SECOND
        return Tap("SPX", "gamma", 5780.0, "MES", SESSION, seq, start, close, close, ended)

    ctx = context(futures={"MES": pts(5781.0)}, taps=(tap(1, True), tap(2, False)))
    (setup,) = detect_setups(ctx, only("gatekeeper_fade"))
    assert setup.key.tap_seq == 2


# ---------------------------------------------------------------- unpriced and skips


def test_missing_price_emits_unpriced_setups_for_every_selected_node() -> None:
    maps: dict[SymMetric, ConvertedMap | MissingPrice] = {SPX: MissingPrice("MES")}
    ctx = context(futures={}, maps=maps, regime=MissingInput(("SPX gamma Snapshot",)))
    out = detect_setups(ctx, only("gatekeeper_fade"))
    assert [(x.key.source_strike, x.key.direction) for x in out] == [
        (5780.0, "long"),
        (5820.0, "short"),
    ]
    setup = out[0]
    assert isinstance(setup, CandidateSetup)
    assert not setup.priced
    assert (setup.entry, setup.stop, setup.targets) == (None, None, ())
    assert setup.source == SourceNodeRef("SPX", "gamma", 5780.0, 1.0e9, None, None)
    assert setup.exit_mode == "opposition_or_fixed_r"
    assert setup.inputs == SetupInputs(
        map_as_of=(SnapshotAsOf("SPX", "gamma", AS_OF),),
        source_spot=5800.0,
        futures_price=MissingPrice("MES"),
        conversion_method="offset",
        conversion_factor=MissingPrice("MES"),
        band_half_width_pts=MissingPrice("MES"),
        regime=MissingInput(("SPX gamma Snapshot",)),
        map_grade="Neutral_Map",
        stop_rule=None,
    )


def test_trailing_has_no_target_price_so_the_setup_is_skipped() -> None:
    p = with_exits(only("gatekeeper_fade"), {"global": {"mode": "trailing"}})
    key = SetupKey("MES", "gatekeeper_fade", 5780.0, "long", SESSION, 1)
    assert detect_setups(GK_LONG_CTX, p) == (DetectionSkip(key, T0, "no_target"),)


def test_next_node_with_no_node_beyond_entry_is_skipped() -> None:
    # Every Node is below spot: a long at the Floor 5750 has no Node above it, and
    # Positive_Gamma selects Next_Node.
    snap = snapshot("SPX", 5800.0, ((5700.0, -1.0e9), (5750.0, 3.0e9)))
    ctx = context(
        futures={"MES": pts(5751.0)},
        snapshots=(snap,),
        maps={SPX: converted(snap, "offset", 0.0, 5.0)},
        regime="Positive_Gamma",
    )
    key = SetupKey("MES", "floor_ceiling_bounce", 5750.0, "long", SESSION, 1)
    assert detect_setups(ctx, only("floor_ceiling_bounce")) == (
        DetectionSkip(key, T0, "no_target"),
    )


def test_tp2_not_beyond_tp1_is_a_price_order_skip() -> None:
    exits = {
        "global": {"mode": "tp1_partial_be"},
        "modes": {
            "tp1_partial_be": {"tp1": {"rule": "node"}, "tp2": {"rule": "r", "r_multiple": 0.5}}
        },
    }
    p = with_exits(only("gatekeeper_fade"), exits)
    key = SetupKey("MES", "gatekeeper_fade", 5780.0, "long", SESSION, 1)
    assert detect_setups(GK_LONG_CTX, p) == (DetectionSkip(key, T0, "price_order"),)


def test_a_stop_on_the_wrong_side_of_entry_is_a_price_order_skip() -> None:
    # A Node at 5777, inside the 5780 band, is the one-Node-beyond stop; a -16 tick
    # entry offset (5776) puts the entry below it.
    pairs = (*SPX_PAIRS[:2], (5777.0, -0.7e9), *SPX_PAIRS[2:])
    snap = snapshot("SPX", 5800.0, pairs)
    ctx = context(
        futures={"MES": pts(5781.0)},
        snapshots=(snap,),
        maps={SPX: converted(snap, "offset", 0.0, 5.0)},
    )
    p = only("gatekeeper_fade", gatekeeper_fade={"entry_offset_ticks": -16})
    key = SetupKey("MES", "gatekeeper_fade", 5780.0, "long", SESSION, 1)
    assert detect_setups(ctx, p) == (DetectionSkip(key, T0, "price_order"),)


# ---------------------------------------------------------------- source Node selection


def source_of(pairs: tuple[tuple[float, float], ...], spot: float = 5800.0) -> Source:
    snap = snapshot("SPX", spot, pairs)
    return Source("ES", "MES", snap, classify(snap, NODE_PARAMS), MissingPrice("MES"))


def test_floor_ceiling_bounce_and_the_empty_basement_variant_split_floors() -> None:
    cfg = PatternsConfig()
    plain = source_of(SPX_PAIRS)
    assert select_floor_ceiling(GK_LONG_CTX, plain, cfg.floor_ceiling_bounce) == (
        (5750.0, "long"),
        (5850.0, "short"),
    )
    assert select_empty_basement(GK_LONG_CTX, plain, cfg.floor_ceiling_bounce_empty_basement) == ()
    # Moving 5700 to 5680 leaves nothing within 58 points under the Floor.
    basement = source_of(((5680.0, -1.0e9), *SPX_PAIRS[1:]))
    assert basement.labels.empty_basement
    assert select_floor_ceiling(GK_LONG_CTX, basement, cfg.floor_ceiling_bounce) == (
        (5850.0, "short"),
    )
    assert select_empty_basement(
        GK_LONG_CTX, basement, cfg.floor_ceiling_bounce_empty_basement
    ) == ((5750.0, "long"),)


def test_beach_ball_selects_major_barneys_below_spot() -> None:
    pairs = ((5700.0, -2.0e9), (5750.0, -0.8e9), (5850.0, 3.0e9), (5900.0, -2.5e9))
    cfg = PatternsConfig().beach_ball
    assert select_beach_ball(GK_LONG_CTX, source_of(pairs), cfg) == ((5700.0, "long"),)
    looser = cfg.model_copy(update={"major_fraction": 0.25})
    assert select_beach_ball(GK_LONG_CTX, source_of(pairs), looser) == (
        (5700.0, "long"),
        (5750.0, "long"),
    )


def test_rug_needs_a_stacked_barney_and_no_pika_floor() -> None:
    cfg = PatternsConfig().rug
    rug = ((5790.0, -1.0e9), (5810.0, 1.0e9), (5830.0, 1.2e9), (5900.0, 3.0e9))
    assert select_rug(GK_LONG_CTX, source_of(rug), cfg) == ((5810.0, "short"),)
    pika_floor = ((5700.0, 2.0e9), *rug)
    assert select_rug(GK_LONG_CTX, source_of(pika_floor), cfg) == ()
    # 0.5% of 5800 is 29 points: a Barney 30 points under the Pika is not stacked.
    loose = ((5780.0, -1.0e9), *rug[1:])
    assert select_rug(GK_LONG_CTX, source_of(loose), cfg) == ()


def test_reverse_rug_needs_a_barney_stacked_above_the_pika() -> None:
    cfg = PatternsConfig().reverse_rug
    pairs = ((5700.0, 3.0e9), (5790.0, 1.0e9), (5810.0, -1.0e9))
    assert select_reverse_rug(GK_LONG_CTX, source_of(pairs), cfg) == ((5790.0, "long"),)
    flipped = ((5700.0, 3.0e9), (5790.0, 1.0e9), (5810.0, 1.0e9))
    assert select_reverse_rug(GK_LONG_CTX, source_of(flipped), cfg) == ()


def test_whipsaw_fade_needs_the_whipsaw_regime() -> None:
    cfg = PatternsConfig().whipsaw_fade
    src = source_of(SPX_PAIRS)
    whipsaw = context(futures={}, regime="Whipsaw")
    assert select_whipsaw_fade(whipsaw, src, cfg) == ((5750.0, "long"), (5850.0, "short"))
    assert select_whipsaw_fade(GK_LONG_CTX, src, cfg) == ()
    missing = context(futures={}, regime=MissingInput(("SPX vanna Snapshot",)))
    assert select_whipsaw_fade(missing, src, cfg) == ()


def test_trend_follow_trades_toward_a_distant_king() -> None:
    cfg = PatternsConfig().trend_follow
    up = ((5760.0, 1.0e9), (5780.0, 1.0e9), (5850.0, 1.0e9), (5900.0, 3.0e9))
    assert select_trend_follow(GK_LONG_CTX, source_of(up), cfg) == ((5780.0, "long"),)
    strict = cfg.model_copy(update={"max_intermediate": 0})
    assert select_trend_follow(GK_LONG_CTX, source_of(up), strict) == ()
    far = cfg.model_copy(update={"trend_min_pct": 2.0})  # 116 points > 100
    assert select_trend_follow(GK_LONG_CTX, source_of(up), far) == ()
    down = ((5700.0, -3.0e9), (5820.0, 1.0e9), (5840.0, 1.0e9))
    assert select_trend_follow(GK_LONG_CTX, source_of(down), cfg) == ((5820.0, "short"),)


# ---------------------------------------------------------------- context checks


def test_context_rejects_inputs_not_at_t() -> None:
    with pytest.raises(ValueError, match="taps"):
        replace(GK_LONG_CTX, taps=TapView(T0 + 1, SESSION, (), {}, {}))
    with pytest.raises(ValueError, match="chart"):
        replace(GK_LONG_CTX, chart=ChartFeatures(T0 - 1, ()))


def test_context_needs_labels_and_a_map_for_every_snapshot() -> None:
    with pytest.raises(ValueError, match="labels"):
        replace(GK_LONG_CTX, labels={})
    with pytest.raises(ValueError, match="converted"):
        replace(GK_LONG_CTX, converted={})
    other = converted(replace(SPX_SNAP, as_of_ns=AS_OF - 1), "offset", 0.0, 5.0)
    with pytest.raises(ValueError, match="not of its Snapshot"):
        replace(GK_LONG_CTX, converted={SPX: other})


def test_params_reject_a_source_symbol_of_the_other_family() -> None:
    with pytest.raises(ValueError, match="es_source_symbol"):
        DetectorParams(data=DataConfig(es_source_symbol="QQQ"))
