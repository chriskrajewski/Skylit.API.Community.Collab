"""Property 31: Detector isolation.

*For any* Decision_Time context and any combination of detector enabled flags,
the Candidate_Setups and Detection_Skips attributed to an enabled detector are
identical to that detector's output when it is the only one enabled; disabled
detectors contribute nothing (zero output when none is enabled); and
Floor_Ceiling_Bounce outputs whose source Floor is Empty_Basement come only
from the Empty_Basement variant.

Per example (one context, one configuration of every detector, one set of
enabled flags), with a detector's *solo* output being the Setup_Detector
output when only that detector is enabled:

- the output under the flags equals the registry-order concatenation of the
  enabled detectors' solo outputs, so each enabled detector contributes
  exactly its solo output and a disabled one nothing (Req 10.3-10.4);
- each detector's own ``detect`` output under the flags equals its solo
  output, whether the flags enable it or not (Req 10.4);
- with every detector disabled the output is empty (Req 10.3);
- every Candidate_Setup in a solo output names that detector;
- a ``floor_ceiling_bounce`` setup or skip whose source Node is the Floor of
  an Empty_Basement Snapshot is in the solo output of
  ``floor_ceiling_bounce_empty_basement`` only (Req 10.2). A skip names no
  source symbol or metric, so they come from its instrument (default MES for
  SPX, MNQ for QQQ) and its detector's ``metric``.

Generators: SPX and QQQ entries in gamma and vanna, each priced (a hand-built
offset or ratio conversion), unpriced (``MissingPrice``), ``Unavailable`` or
absent. A Snapshot has up to 8 distinct strikes on a 5-point (SPX) or $1
(QQQ) grid around spot, each with a signed value of up to 3e9 or 0, so
Floors with and without Empty_Basement, empty and all-zero Snapshots occur.
Futures_Price per instrument is absent or near (or far from) a converted
level. Taps on drawn strikes, any Regime or none, any Map_Grade or none, any
global Exit_Mode and stop rule, and every detector's parameters anywhere in
their valid ranges complete the input. Two explicit examples: an
Empty_Basement Floor under the price, and the Whipsaw Regime with the price
at a plain Floor.

**Validates: Requirements 10.2, 10.3, 10.4**
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import date, time
from typing import Any, Final

from hypothesis import event, example, given
from hypothesis import strategies as st

from fse.config.schema.data import DataConfig
from fse.config.schema.exits import ExitsConfig
from fse.config.schema.nodes import NodesConfig
from fse.config.schema.patterns import DETECTOR_IDS, PatternsConfig
from fse.engine.chart import ChartFeatures
from fse.engine.levels import Conversion, ConvertedMap, band, level_family
from fse.engine.nodes import NodeParams, classify
from fse.engine.regime import RegimeResult
from fse.engine.setups.floor_ceiling import PATTERN as FLOOR_CEILING_PATTERN
from fse.engine.setups.registry import (
    DETECTORS,
    DetectContext,
    Detection,
    DetectorParams,
    detect_setups,
)
from fse.engine.taps import Tap, TapView
from fse.engine.types import (
    CandidateSetup,
    ConversionMethod,
    MapGrade,
    Metric,
    MissingInput,
    MissingPrice,
    Regime,
    Snapshot,
    Unavailable,
)
from fse.pit.protocols import MapState, SymMetric
from fse.timekit import NS_PER_SECOND, ny_instant

SESSION: Final = date(2026, 3, 5)
T0: Final = ny_instant(SESSION, time(10, 0))
AS_OF: Final = T0 - 30 * NS_PER_SECOND
NODE_PARAMS: Final = NodeParams.from_config(NodesConfig())
DATA: Final = DataConfig()
SOURCE_OF_INSTRUMENT: Final[Mapping[str, str]] = {
    DATA.instruments.es_levels: DATA.es_source_symbol,
    DATA.instruments.nq_levels: DATA.nq_source_symbol,
}
EMPTY_BASEMENT_VARIANT: Final = "floor_ceiling_bounce_empty_basement"

METRICS: Final[tuple[Metric, ...]] = ("gamma", "vanna")
REGIMES: Final[tuple[Regime, ...]] = (
    "Positive_Gamma",
    "Negative_Gamma",
    "Vanna_Dominant",
    "Whipsaw",
    "Structureless",
)
GRADES: Final[tuple[MapGrade, ...]] = ("A_Plus_Map", "Neutral_Map", "F_Map")
EXIT_MODES: Final = ("fixed_r", "next_node", "tp1_partial_be", "opposition_or_fixed_r", "trailing")
STOP_RULES: Final = ("one_node_beyond", "fixed_ticks")
ENTRY_KINDS: Final = ("priced", "priced", "priced", "unpriced", "unavailable", "absent")


# ---------------------------------------------------------------- source families


@dataclass(frozen=True, slots=True)
class Family:
    """One source symbol: its strike grid, spot range and hand-built conversion."""

    symbol: str
    instrument: str
    center: float  # strike grid centre and middle spot
    step: float  # strike spacing
    reach: int  # grid steps either side of the centre
    spot_step: float
    spot_reach: int
    method: ConversionMethod
    factors: st.SearchStrategy[float]
    half_widths: tuple[float, ...]
    base_ticks: int  # a Futures_Price when no level is converted


SPX_FAMILY: Final = Family(
    symbol="SPX",
    instrument="MES",
    center=5800.0,
    step=5.0,
    reach=30,  # strikes 5650 to 5950
    spot_step=0.5,
    spot_reach=20,  # spot 5790 to 5810
    method="offset",
    factors=st.integers(-200, 200).map(lambda n: n / 4),  # -50 to 50 points
    half_widths=(2.5, 5.0, 7.5),
    base_ticks=23_200,
)
QQQ_FAMILY: Final = Family(
    symbol="QQQ",
    instrument="MNQ",
    center=500.0,
    step=1.0,
    reach=15,  # strikes 485 to 515
    spot_step=0.25,
    spot_reach=4,  # spot 499 to 501
    method="ratio",
    factors=st.sampled_from((39.75, 40.0, 41.5)),
    half_widths=(10.0, 20.0, 30.0),
    base_ticks=80_000,
)
FAMILIES: Final = (SPX_FAMILY, QQQ_FAMILY)
FAMILY_OF: Final[Mapping[str, Family]] = {f.symbol: f for f in FAMILIES}

VALUES: Final = st.integers(-30, 30).map(lambda n: n * 1.0e8)


def snapshot(
    symbol: str, metric: Metric, spot: float, pairs: tuple[tuple[float, float], ...]
) -> Snapshot:
    return Snapshot(
        symbol=symbol,
        metric=metric,
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


def converted(s: Snapshot, factor: float, half_width: float) -> ConvertedMap:
    fam = FAMILY_OF[s.symbol]
    conv = Conversion(
        symbol=s.symbol,
        family=level_family(s.symbol),
        instrument=fam.instrument,
        contract=f"{fam.instrument}H6",
        method=fam.method,
        factor=factor,
        futures_close=s.spot + factor if fam.method == "offset" else s.spot * factor,
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


def regime_result(regime: Regime) -> RegimeResult:
    return RegimeResult(regime, 1.0, 0.5, 1.0, 0.5, -1.0, False)


def context(
    entries: Mapping[SymMetric, Snapshot | Unavailable],
    maps: Mapping[SymMetric, ConvertedMap | MissingPrice],
    futures: Mapping[str, int],
    regime: RegimeResult | MissingInput,
    grade: MapGrade | MissingInput,
    taps: tuple[Tap, ...] = (),
) -> DetectContext:
    present = [e for e in entries.values() if isinstance(e, Snapshot)]
    return DetectContext(
        t=T0,
        map_state=MapState(T0, entries),
        labels={(s.symbol, s.metric): classify(s, NODE_PARAMS) for s in present},
        converted=maps,
        regime=regime,
        grade=grade,
        chart=ChartFeatures(T0, ()),
        taps=TapView(T0, SESSION, taps, {}, {}),
        futures_price=futures,
    )


# ---------------------------------------------------------------- scenario


@dataclass(frozen=True, slots=True)
class Scenario:
    """A context, every detector's ``patterns`` entry (flag aside), exits and the flags."""

    ctx: DetectContext
    configs: Mapping[str, Mapping[str, Any]]
    exits: ExitsConfig
    flags: Mapping[str, bool]

    @property
    def enabled(self) -> tuple[str, ...]:
        return tuple(d for d in DETECTOR_IDS if self.flags[d])

    def params(self, enabled: Collection[str]) -> DetectorParams:
        """The detector parameters with exactly ``enabled`` switched on."""
        patterns = PatternsConfig.model_validate(
            {d: {**self.configs.get(d, {}), "enabled": d in enabled} for d in DETECTOR_IDS}
        )
        return DetectorParams(patterns=patterns, data=DATA, exits=self.exits)


@st.composite
def snapshots(draw: st.DrawFn, fam: Family, metric: Metric) -> Snapshot:
    spot = fam.center + fam.spot_step * draw(st.integers(-fam.spot_reach, fam.spot_reach))
    steps = draw(st.lists(st.integers(-fam.reach, fam.reach), max_size=8, unique=True))
    pairs = tuple((fam.center + fam.step * k, draw(VALUES)) for k in steps)
    return snapshot(fam.symbol, metric, spot, pairs)


def _shares(lo: int, hi: int, scale: int) -> st.SearchStrategy[float]:
    return st.integers(lo, hi).map(lambda n: n / scale)


COMMON_KEYS: Final[Mapping[str, st.SearchStrategy[Any]]] = {
    "metric": st.sampled_from(("gamma", "gamma", "gamma", "vanna")),
    "entry_offset_ticks": st.one_of(st.just(0), st.integers(-80, 80)),
    "stop_rule": st.sampled_from((None, *STOP_RULES)),
    "fixed_stop_ticks": st.one_of(st.just(8), st.integers(1, 200)),
    "invalidation": st.sampled_from(("level", "band_edge")),
    "stop_lookout_pct": st.sampled_from((0.1, 0.5, 1.0, 2.0, 5.0)),
    "arming": st.fixed_dictionaries(
        {
            "es_pts": st.one_of(st.just(10.0), _shares(1, 400, 4)),  # 0.25 to 100 points
            "nq_qqq_usd": st.one_of(st.just(1.0), _shares(1, 1000, 100)),  # $0.01 to $10
        }
    ),
}
EXTRA_KEYS: Final[Mapping[str, Mapping[str, st.SearchStrategy[Any]]]] = {
    "beach_ball": {"major_fraction": _shares(1, 100, 100)},
    "rug": {"stack_pct": st.sampled_from((0.1, 0.5, 1.0, 3.0))},
    "reverse_rug": {"stack_pct": st.sampled_from((0.1, 0.5, 1.0, 3.0))},
    "trend_follow": {
        "trend_min_pct": st.sampled_from((0.1, 0.5, 1.0, 2.0)),
        "max_intermediate": st.integers(0, 3),
    },
}


@st.composite
def scenarios(draw: st.DrawFn) -> Scenario:
    # Map_State entries and their conversions.
    entries: dict[SymMetric, Snapshot | Unavailable] = {}
    maps: dict[SymMetric, ConvertedMap | MissingPrice] = {}
    for fam in FAMILIES:
        for metric in METRICS:
            kind = draw(st.sampled_from(ENTRY_KINDS))
            key = (fam.symbol, metric)
            if kind == "absent":
                continue
            if kind == "unavailable":
                entries[key] = Unavailable(f"no {fam.symbol} {metric} Snapshot")
                continue
            snap = draw(snapshots(fam, metric))
            entries[key] = snap
            if kind == "unpriced":
                maps[key] = MissingPrice(fam.instrument)
            else:
                factor = draw(fam.factors)
                maps[key] = converted(snap, factor, draw(st.sampled_from(fam.half_widths)))

    # Futures_Price per instrument: absent, or near or far from a converted level.
    futures: dict[str, int] = {}
    for fam in FAMILIES:
        if draw(st.integers(0, 4)) == 0:
            continue
        levels = sorted(
            {
                level
                for (symbol, _), m in maps.items()
                if symbol == fam.symbol and isinstance(m, ConvertedMap)
                for level in m.levels
            }
        )
        base = draw(st.sampled_from(levels)) if levels else fam.base_ticks
        futures[fam.instrument] = base + draw(
            st.one_of(st.integers(-48, 48), st.integers(-400, 400))
        )

    # Taps on drawn strikes: some ended, the last one maybe still open.
    taps: list[Tap] = []
    for s in (e for e in entries.values() if isinstance(e, Snapshot)):
        if not s.strikes:
            continue
        for strike in draw(st.lists(st.sampled_from(s.strikes), max_size=2, unique=True)):
            count = draw(st.integers(1, 3))
            still_open = draw(st.booleans())
            for seq in range(1, count + 1):
                start = T0 - (40 - 10 * seq) * 60 * NS_PER_SECOND
                close = start + 60 * NS_PER_SECOND
                ended = not (still_open and seq == count)
                instrument = FAMILY_OF[s.symbol].instrument
                taps.append(
                    Tap(
                        s.symbol,
                        s.metric,
                        strike,
                        instrument,
                        SESSION,
                        seq,
                        start,
                        close,
                        close,
                        ended,
                    )
                )

    regime = draw(
        st.one_of(
            st.sampled_from(REGIMES).map(regime_result),
            st.just(MissingInput(("SPX vanna Snapshot",))),
        )
    )
    grade = draw(st.one_of(st.sampled_from(GRADES), st.just(MissingInput(("map_grade",)))))
    ctx = context(entries, maps, futures, regime, grade, tuple(taps))

    configs = {
        d: draw(st.fixed_dictionaries({**COMMON_KEYS, **EXTRA_KEYS.get(d, {})}))
        for d in DETECTOR_IDS
    }
    exits = ExitsConfig.model_validate(
        {
            "global": {
                "mode": draw(st.sampled_from(EXIT_MODES)),
                "stop_rule": draw(st.sampled_from(STOP_RULES)),
            }
        }
    )
    mask = draw(st.integers(0, 2 ** len(DETECTOR_IDS) - 1))  # every flag combination alike
    flags = {d: bool(mask >> i & 1) for i, d in enumerate(DETECTOR_IDS)}
    return Scenario(ctx, configs, exits, flags)


# ---------------------------------------------------------------- explicit examples

# Spot 5800. Floor 5750; Ceiling and King 5850; Gatekeepers 5780 and 5820.
_PLAIN: Final = (
    (5700.0, -1.0e9),
    (5750.0, 2.0e9),
    (5780.0, 1.0e9),
    (5820.0, 1.5e9),
    (5850.0, -3.0e9),
)
# Moving 5700 to 5680 leaves no Node within the 58-point lookout under the Floor.
_BASEMENT: Final = ((5680.0, -1.0e9), *_PLAIN[1:])


def fixed(pairs: tuple[tuple[float, float], ...], price_pts: float, regime: Regime) -> Scenario:
    """Default configuration, every detector on, one SPX gamma Snapshot at offset 0."""
    snap = snapshot("SPX", "gamma", 5800.0, pairs)
    ctx = context(
        {("SPX", "gamma"): snap},
        {("SPX", "gamma"): converted(snap, 0.0, 5.0)},
        {"MES": round(price_pts * 4)},
        regime_result(regime),
        "Neutral_Map",
    )
    return Scenario(ctx, {}, ExitsConfig(), dict.fromkeys(DETECTOR_IDS, True))


EMPTY_BASEMENT_AT_FLOOR: Final = fixed(_BASEMENT, 5751.0, "Negative_Gamma")
WHIPSAW_AT_PLAIN_FLOOR: Final = fixed(_PLAIN, 5752.0, "Whipsaw")


# ---------------------------------------------------------------- property


def source_key(x: Detection, metric: Metric) -> SymMetric:
    """The (symbol, metric) of an output's source Snapshot."""
    if isinstance(x, CandidateSetup):
        return (x.source.symbol, x.source.metric)
    return (SOURCE_OF_INSTRUMENT[x.key.instrument], metric)


def on_empty_basement_floor(ctx: DetectContext, x: Detection, metric: Metric) -> bool:
    """Whether the output's source Node is the Floor of an Empty_Basement Snapshot."""
    labels = ctx.labels.get(source_key(x, metric))
    return labels is not None and labels.empty_basement and labels.floor == x.key.source_strike


# Feature: skylit-futures-strategy-engine, Property 31: Detector isolation
@example(s=EMPTY_BASEMENT_AT_FLOOR)
@example(s=WHIPSAW_AT_PLAIN_FLOOR)
@given(s=scenarios())
def test_each_enabled_detector_emits_exactly_its_solo_output(s: Scenario) -> None:
    ctx = s.ctx
    enabled = s.enabled
    p = s.params(enabled)
    solo = {d: detect_setups(ctx, s.params((d,))) for d in DETECTOR_IDS}

    # Req 10.3: no detector enabled, no output and no error.
    assert detect_setups(ctx, s.params(())) == ()
    # Req 10.3-10.4: enabled detectors contribute their solo output, in registry order.
    assert detect_setups(ctx, p) == tuple(x for d in enabled for x in solo[d])

    basement_floors = 0
    for d in DETECTOR_IDS:
        # Req 10.4: a detector's own output does not depend on any flag.
        assert DETECTORS[d].detect(ctx, p) == solo[d]
        assert all(x.detector_id == d for x in solo[d] if isinstance(x, CandidateSetup))
        metric = p.patterns.get(d).metric
        for x in solo[d]:
            if x.key.pattern == FLOOR_CEILING_PATTERN and on_empty_basement_floor(ctx, x, metric):
                assert d == EMPTY_BASEMENT_VARIANT  # Req 10.2
                basement_floors += 1

    outputs = sum(len(out) for out in solo.values())
    event(f"enabled detectors: {len(enabled)}")
    event("detector output: " + ("none" if outputs == 0 else "some"))
    event(f"Empty_Basement Floor outputs: {'some' if basement_floors else 'none'}")
