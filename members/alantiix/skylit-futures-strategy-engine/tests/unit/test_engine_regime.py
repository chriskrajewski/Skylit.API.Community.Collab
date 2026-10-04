"""Unit tests for the Regime_Classifier (``fse.engine.regime``).

Design §7: magnitudes, the check order Vanna_Dominant, Structureless, Whipsaw,
Negative_Gamma, Positive_Gamma, the VIX condition, Map_Grade and the
missing-input results. Snapshots are synthetic with spot 1000, so the default
1% regime distance keeps strikes 990 to 1010.

**Validates: Requirements 7.1-7.12**
"""

from __future__ import annotations

from typing import Any

import pytest

from fse.config.schema.data import DataConfig
from fse.config.schema.nodes import NodesConfig
from fse.config.schema.regime import GradeConfig, RegimeConfig
from fse.engine.nodes import NodeParams, classify
from fse.engine.regime import (
    TRAILING_SESSIONS,
    RegimeParams,
    RegimeResult,
    TrailingMedian,
    TrailingMedians,
    king_abs_value,
    map_grade,
    map_state_grade,
    net_gex,
    raw_magnitude,
    regime,
    vix_value,
)
from fse.engine.types import Metric, MissingInput, Snapshot, Unavailable, VixState
from fse.pit.protocols import MapState, SymMetric
from fse.timekit import NS_PER_SECOND

T0 = 1_772_721_000 * NS_PER_SECOND
NO_VIX = Unavailable("no VIX")


def snap(
    strikes: tuple[float, ...],
    values: tuple[float, ...],
    *,
    symbol: str = "SPX",
    metric: Metric = "gamma",
    spot: float = 1000.0,
) -> Snapshot:
    return Snapshot(
        symbol=symbol,
        metric=metric,
        view_id="view",
        as_of_ns=T0,
        as_of_raw="2026-03-05T14:30:00Z",
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


# King 990 (below spot), Floor 990, no Ceiling, net GEX +160: Positive_Gamma.
POSITIVE = snap((990.0, 1000.0, 1010.0), (100.0, 50.0, 10.0))
# raw VEX 400.
VANNA = snap((995.0, 1005.0), (300.0, -400.0), metric="vanna")


def state(*snapshots: Snapshot, missing: tuple[SymMetric, ...] = ()) -> MapState:
    entries: dict[SymMetric, Snapshot | Unavailable] = {(s.symbol, s.metric): s for s in snapshots}
    for key in missing:
        entries[key] = Unavailable("no Snapshot")
    return MapState(t=T0, entries=entries)


def medians(gamma: float = 100.0, vanna: float = 1000.0) -> TrailingMedians:
    return TrailingMedians(
        gamma=TrailingMedian(gamma, TRAILING_SESSIONS), vanna=TrailingMedian(vanna, 20)
    )


def vix(
    daily_open: float | Unavailable = NO_VIX,
    prior_close: float | Unavailable = 20.0,
    last_1m_close: float | Unavailable = NO_VIX,
) -> VixState:
    return VixState(daily_open=daily_open, prior_close=prior_close, last_1m_close=last_1m_close)


def params(**overrides: Any) -> RegimeParams:
    vix_condition = overrides.pop("vix_condition", {"enabled": False})
    cfg = RegimeConfig.model_validate(
        {"min_abs_value": 50.0, "vix_condition": vix_condition, **overrides}
    )
    return RegimeParams(cfg)


def classified(result: RegimeResult | MissingInput) -> str:
    assert isinstance(result, RegimeResult)
    return result.regime


# ---------------------------------------------------------------- measurements


def test_raw_magnitude_and_net_gex_read_strikes_within_the_distance() -> None:
    s = snap((980.0, 990.0, 1000.0, 1010.0, 1010.25), (-900.0, -30.0, 5.0, 20.0, 800.0))
    assert raw_magnitude(s, 1.0) == 30.0  # 990 and 1010 sit on the boundary
    assert net_gex(s, 1.0) == -5.0
    assert raw_magnitude(s, 2.0) == 900.0
    assert raw_magnitude(snap((900.0,), (5.0,)), 1.0) == 0.0
    assert net_gex(snap((), ()), 1.0) == 0.0
    assert king_abs_value(s) == 900.0
    assert king_abs_value(snap((), ())) == 0.0


def test_vix_value_reads_the_daily_open_or_the_intraday_close() -> None:
    v = vix(daily_open=21.0, last_1m_close=22.5)
    assert vix_value(v, intraday_source=False) == 21.0
    assert vix_value(v, intraday_source=True) == 22.5
    assert vix_value(vix(), intraday_source=False) == NO_VIX
    assert isinstance(vix_value(vix(daily_open=float("nan")), intraday_source=False), Unavailable)


def test_result_carries_the_measurements() -> None:
    result = regime(state(POSITIVE, VANNA), medians(gamma=50.0, vanna=800.0), vix(), params())
    assert result == RegimeResult(
        regime="Positive_Gamma",
        raw_gex=100.0,
        raw_vex=400.0,
        normalized_gex=2.0,
        normalized_vex=0.5,
        net_gex=160.0,
        vanna_withheld=False,
    )


def test_params_come_from_the_config_sections() -> None:
    cfg = RegimeConfig(min_abs_value=1.0)
    data = DataConfig.model_validate(
        {
            "symbols": ["SPY", "QQQ"],
            "regime_symbol": "SPY",
            "es_source_symbol": "SPY",
            "nq_sources": ["QQQ"],
        }
    )
    nodes = NodesConfig.model_validate({"node_fraction": 0.5})
    p = RegimeParams.from_config(cfg, data, nodes)
    assert (p.config, p.symbol, p.nodes) == (cfg, "SPY", NodeParams.from_config(nodes))
    assert RegimeParams(cfg).nodes == NodeParams.from_config(NodesConfig())


# ---------------------------------------------------------------- check order


def test_vanna_dominant_when_normalized_vex_reaches_the_multiple() -> None:
    # nGEX = 100 / 100 = 1.0; nVEX = 400 / 200 = 2.0 = 2.0 x nGEX.
    assert classified(regime(state(POSITIVE, VANNA), medians(vanna=200.0), vix(), params())) == (
        "Vanna_Dominant"
    )
    # nVEX = 400 / 201 is just below the multiple.
    assert classified(regime(state(POSITIVE, VANNA), medians(vanna=201.0), vix(), params())) == (
        "Positive_Gamma"
    )


def test_vanna_dominant_needs_a_positive_normalized_vex() -> None:
    flat_vanna = snap((995.0,), (0.0,), metric="vanna")
    flat_gamma = snap((990.0, 1020.0), (0.0, 100.0))  # raw GEX 0 within the distance
    result = regime(state(flat_gamma, flat_vanna), medians(), vix(), params())
    assert classified(result) == "Positive_Gamma"


@pytest.mark.parametrize(
    ("vix_state", "intraday", "expected"),
    [
        (vix(daily_open=21.0), False, "Vanna_Dominant"),  # 21.0 = 20.0 + 5% of 20.0
        (vix(daily_open=20.99), False, "Positive_Gamma"),
        (vix(), False, "Positive_Gamma"),  # before 09:31: no daily open
        (vix(daily_open=25.0, prior_close=NO_VIX), False, "Positive_Gamma"),
        (vix(daily_open=25.0, last_1m_close=20.5), True, "Positive_Gamma"),
        (vix(daily_open=19.0, last_1m_close=21.5), True, "Vanna_Dominant"),
        (vix(daily_open=25.0), True, "Positive_Gamma"),  # no intraday bar yet
    ],
)
def test_vix_condition_withholds_vanna_dominant(
    vix_state: VixState, intraday: bool, expected: str
) -> None:
    p = params(vix_condition={"enabled": True, "pct": 5.0, "intraday_source": intraday})
    result = regime(state(POSITIVE, VANNA), medians(vanna=200.0), vix_state, p)
    assert isinstance(result, RegimeResult)
    assert result.regime == expected
    assert result.vanna_withheld is (expected != "Vanna_Dominant")


def test_disabled_vix_condition_ignores_vix() -> None:
    result = regime(state(POSITIVE, VANNA), medians(vanna=200.0), vix(prior_close=NO_VIX), params())
    assert classified(result) == "Vanna_Dominant"


def test_structureless_when_no_gamma_strike_reaches_min_abs_value() -> None:
    # Floor 990 (100) and Ceiling 1010 (95) would be Whipsaw.
    gamma = snap((990.0, 1010.0), (100.0, -95.0))
    assert classified(regime(state(gamma, VANNA), medians(), vix(), params())) == "Whipsaw"
    assert classified(
        regime(state(gamma, VANNA), medians(), vix(), params(min_abs_value=100.0))
    ) == ("Whipsaw")
    assert classified(
        regime(state(gamma, VANNA), medians(), vix(), params(min_abs_value=100.5))
    ) == ("Structureless")


def test_vanna_dominant_is_checked_before_structureless() -> None:
    result = regime(state(POSITIVE, VANNA), medians(vanna=200.0), vix(), params(min_abs_value=1e6))
    assert classified(result) == "Vanna_Dominant"


@pytest.mark.parametrize(
    ("ceiling_value", "expected"),
    [(-85.0, "Whipsaw"), (-84.0, "Positive_Gamma"), (-100.0, "Whipsaw")],
)
def test_whipsaw_when_floor_and_ceiling_are_within_whipsaw_pct(
    ceiling_value: float, expected: str
) -> None:
    # Floor 990 (100); |100 - 85| = 15 = 15% of 100.
    gamma = snap((990.0, 1010.0), (100.0, ceiling_value))
    assert classified(regime(state(gamma, VANNA), medians(), vix(), params())) == expected


def test_whipsaw_when_trinity_kings_sit_on_opposite_sides_of_spot() -> None:
    qqq_above = snap((505.0, 495.0), (10.0, 1.0), symbol="QQQ", spot=500.0)
    qqq_at_spot = snap((500.0, 495.0), (10.0, 1.0), symbol="QQQ", spot=500.0)
    ndx_above = snap((21_100.0,), (10.0,), symbol="NDX", spot=21_000.0)
    p = params()
    assert classified(regime(state(POSITIVE, VANNA, qqq_above), medians(), vix(), p)) == "Whipsaw"
    assert classified(regime(state(POSITIVE, VANNA, qqq_at_spot), medians(), vix(), p)) == (
        "Positive_Gamma"
    )
    # NDX is not a Trinity symbol; an absent SPY entry is skipped.
    ms = state(POSITIVE, VANNA, ndx_above, missing=(("SPY", "gamma"),))
    assert classified(regime(ms, medians(), vix(), p)) == "Positive_Gamma"


def test_whipsaw_king_check_reads_trinity_even_for_another_regime_symbol() -> None:
    ndx = snap((21_000.0, 20_900.0), (100.0, 20.0), symbol="NDX", spot=21_000.0)
    ndx_vanna = snap((21_000.0,), (1.0,), symbol="NDX", metric="vanna", spot=21_000.0)
    spy_above = snap((101.0,), (5.0,), symbol="SPY", spot=100.0)
    qqq_below = snap((499.0,), (5.0,), symbol="QQQ", spot=500.0)
    p = RegimeParams(RegimeConfig(min_abs_value=50.0), symbol="NDX")
    ms = state(ndx, ndx_vanna, spy_above, qqq_below)
    assert classified(regime(ms, medians(), vix(), p)) == "Whipsaw"


def test_negative_gamma_when_net_gex_within_the_distance_is_below_zero() -> None:
    gamma = snap((980.0, 995.0, 1005.0), (500.0, -100.0, -20.0))  # net -120 within 1%
    result = regime(state(gamma, VANNA), medians(), vix(), params())
    assert isinstance(result, RegimeResult)
    assert (result.regime, result.net_gex) == ("Negative_Gamma", -120.0)


def test_positive_gamma_when_net_gex_is_zero() -> None:
    gamma = snap((995.0, 1005.0, 1020.0), (60.0, -60.0, 300.0))  # Ceiling 1020 (300)
    result = regime(state(gamma, VANNA), medians(), vix(), params())
    assert isinstance(result, RegimeResult)
    assert (result.regime, result.net_gex) == ("Positive_Gamma", 0.0)


# ---------------------------------------------------------------- missing inputs


def test_missing_inputs_are_all_named_in_order() -> None:
    absent = TrailingMedians(gamma=Unavailable("no earlier session"), vanna=TrailingMedian(0.0, 20))
    ms = state(missing=(("SPX", "gamma"), ("SPX", "vanna")))
    assert regime(ms, absent, vix(), params()) == MissingInput(
        (
            "SPX gamma Snapshot",
            "SPX vanna Snapshot",
            "SPX gamma trailing median",
            "SPX vanna trailing median",
        )
    )
    short = TrailingMedians(gamma=TrailingMedian(100.0, 19), vanna=TrailingMedian(100.0, 20))
    assert regime(state(POSITIVE, VANNA), short, vix(), params()) == MissingInput(
        ("SPX gamma trailing median",)
    )


def test_trailing_median_validation() -> None:
    assert TrailingMedian(1.5, 20).usable
    assert not TrailingMedian(1.5, 19).usable
    assert not TrailingMedian(0.0, 20).usable
    for median, sessions in [(-1.0, 20), (float("nan"), 20), (1.0, 0), (1.0, 21), (1.0, True)]:
        with pytest.raises(ValueError, match="TrailingMedian"):
            TrailingMedian(median, sessions)


# ---------------------------------------------------------------- Map_Grade

GRADE = GradeConfig()


def grade(strikes: tuple[float, ...], values: tuple[float, ...]) -> str:
    s = snap(strikes, values)
    return map_grade(s, classify(s, NodeParams.from_config(NodesConfig())), GRADE)


@pytest.mark.parametrize(
    ("strikes", "values", "expected"),
    [
        # 3 Nodes, 1 major (the King); Floor 100 >= 1.5 x Ceiling 30.
        ((990.0, 1010.0, 1020.0), (100.0, 30.0, 25.0), "A_Plus_Map"),
        # Floor 150 = 1.5 x Ceiling 100; 2 majors.
        ((990.0, 1010.0), (150.0, 100.0), "A_Plus_Map"),
        # No Ceiling counts as 0.
        ((990.0, 980.0), (100.0, 40.0), "A_Plus_Map"),
        # 3 majors (>= 50) and the ratio holds.
        ((980.0, 990.0, 1010.0), (100.0, 60.0, 55.0), "Neutral_Map"),
        # 5 Nodes.
        ((970.0, 980.0, 990.0, 1010.0, 1020.0), (100.0, 25.0, 25.0, 25.0, 25.0), "F_Map"),
        # Floor 100 < 1.5 x Ceiling 70.
        ((990.0, 1010.0), (100.0, -70.0), "F_Map"),
        # No Floor or Ceiling: the King sits at spot.
        ((1000.0,), (100.0,), "F_Map"),
        ((), (), "F_Map"),
        ((990.0, 1010.0), (0.0, 0.0), "F_Map"),
    ],
)
def test_map_grade(strikes: tuple[float, ...], values: tuple[float, ...], expected: str) -> None:
    assert grade(strikes, values) == expected


def test_map_grade_rejects_a_vanna_snapshot_and_foreign_labels() -> None:
    p = NodeParams.from_config(NodesConfig())
    with pytest.raises(ValueError, match="gamma Snapshot"):
        map_grade(VANNA, classify(VANNA, p), GRADE)
    with pytest.raises(ValueError, match="lacks"):
        map_grade(POSITIVE, classify(snap((900.0,), (1.0,)), p), GRADE)


def test_map_state_grade_needs_only_the_gamma_snapshot() -> None:
    p = params()
    assert map_state_grade(state(POSITIVE, missing=(("SPX", "vanna"),)), p) == "A_Plus_Map"
    assert map_state_grade(state(VANNA, missing=(("SPX", "gamma"),)), p) == MissingInput(
        ("SPX gamma Snapshot",)
    )
