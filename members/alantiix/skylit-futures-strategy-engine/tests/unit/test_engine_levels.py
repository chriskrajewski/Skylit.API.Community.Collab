"""Unit tests for the Level_Converter (design §8).

Snapshots and bars are synthetic and read through the real historical
MarketView. Prices are chosen so offsets and ratios are exact in binary.

**Validates: Requirements 5.4, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.10**
"""

from __future__ import annotations

import math
from datetime import date, time
from typing import Any

import pytest

from fse.config.schema.data import DataConfig, InstrumentsConfig
from fse.config.schema.levels import LevelMethodsConfig, LevelsConfig
from fse.engine.levels import (
    Conversion,
    ConvertedMap,
    LevelParams,
    band,
    conversion_method,
    convert,
    convert_map,
    level_family,
    pair,
    round_to_tick,
    ticks_to_points,
)
from fse.engine.types import Bar, Metric, MissingPrice, Snapshot, SourceNodeRef
from fse.pit.market_view import HistoricalInputs, HistoricalMarketView
from fse.projectx.models import price_to_ticks
from fse.timekit import NS_PER_SECOND, ny_instant

S = NS_PER_SECOND
T0 = ny_instant(date(2026, 3, 5), time(10, 0))
VIEW_ID = "view-test"
P = LevelParams()


def snap(
    symbol: str, metric: Metric, as_of_ns: int, spot: float, strikes: tuple[float, ...]
) -> Snapshot:
    return Snapshot(
        symbol=symbol,
        metric=metric,
        view_id=VIEW_ID,
        as_of_ns=as_of_ns,
        as_of_raw=f"raw-{as_of_ns}",
        spot=spot,
        previous_close=None,
        strikes=strikes,
        values=tuple(1.0e9 for _ in strikes),
        node_types=None,
        expirations=("2026-03-05",),
        resolution="1s",
        source_endpoint="range",
        extra_json="{}",
    )


def bar(instrument: str, close_ns: int, close: float) -> Bar:
    """A 1-minute futures bar closing at ``close_ns`` with close ``close`` points."""
    c_t = price_to_ticks(close)
    return Bar(
        instrument, f"{instrument}H6", 60, close_ns - 60 * S, close_ns,
        close, close, close, close, 10.0, c_t, c_t, c_t, c_t, "atlas",
    )  # fmt: skip


def view(
    snapshots: list[Snapshot],
    bars: list[Bar],
    t: int = T0,
    symbols: tuple[str, ...] = ("SPX", "SPY", "QQQ", "NDX"),
) -> HistoricalMarketView:
    return HistoricalInputs(symbols=symbols, view_id=VIEW_ID, snapshots=snapshots, bars=bars).view(
        t
    )


SPX = snap("SPX", "gamma", T0, 5800.25, (5780.0, 5800.0, 5800.875, 5825.0))
QQQ_G = snap("QQQ", "gamma", T0, 500.0, (490.0, 500.0, 505.5))
QQQ_V = snap("QQQ", "vanna", T0 - 30 * S, 400.0, (400.0,))
NDX_G = snap("NDX", "gamma", T0, 16000.0, (16000.0, 16100.0))
NDX_V = snap("NDX", "vanna", T0, 16000.0, (16000.0,))
BARS = [
    bar("MES", T0 - 60 * S, 5805.0),
    bar("MES", T0, 5810.5),
    bar("MNQ", T0 - 60 * S, 19990.0),
    bar("MNQ", T0, 20000.0),
]


def priced(result: ConvertedMap | MissingPrice) -> ConvertedMap:
    assert isinstance(result, ConvertedMap), result
    return result


# ---------------------------------------------------------------- tick rounding


@pytest.mark.parametrize(
    ("x", "ticks"),
    [
        (5800.0, 23200),
        (5800.12, 23200),
        (5800.125, 23201),  # exactly halfway: up
        (5800.13, 23201),
        (5800.375, 23202),  # halfway: up
        (-0.125, 0),  # halfway below zero: up, toward the higher tick
        (-0.375, -1),
        (-0.13, -1),
        (0.125 - 2.0**-56, 0),  # just below a half; a rounded 4x + 0.5 would give 1
        (2.0**60 + 0.75 * 2**10, 2**62 + 3 * 2**10),  # far beyond the fast path
    ],
)
def test_round_to_tick_is_nearest_quarter_with_halves_up(x: float, ticks: int) -> None:
    assert round_to_tick(x) == ticks


def test_round_to_tick_agrees_with_the_bar_source_rule() -> None:
    for i in range(-4000, 4000):
        x = 5000.0 + i * 0.03125  # quarters, eighths (halves) and off-grid points
        assert round_to_tick(x) == price_to_ticks(x)


@pytest.mark.parametrize("x", [math.inf, -math.inf, math.nan])
def test_round_to_tick_rejects_non_finite(x: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        round_to_tick(x)


def test_band_is_level_plus_minus_half_width_in_points() -> None:
    assert band(23241, 5.0) == (5805.25, 5815.25)
    assert band(80000, 0.0) == (20000.0, 20000.0)
    assert ticks_to_points(23241) == 5810.25
    with pytest.raises(ValueError, match="half-width"):
        band(80000, -0.25)


# ---------------------------------------------------------------- methods


def test_symbol_families_and_methods() -> None:
    families = [level_family(s) for s in ("SPX", "SPY", "QQQ", "NDX", "NDXP")]
    assert families == ["ES", "ES", "NQ", "NQ", "NQ"]
    defaults = [conversion_method(s, P) for s in ("SPX", "SPY", "QQQ", "NDX", "NDXP")]
    assert defaults == ["offset", "ratio", "ratio", "ratio", "ratio"]
    custom = LevelParams(
        LevelsConfig(methods=LevelMethodsConfig(SPY="offset", NDX="offset", NDXP="offset"))
    )
    configured = [conversion_method(s, custom) for s in ("SPX", "SPY", "QQQ", "NDX", "NDXP")]
    assert configured == ["offset", "offset", "ratio", "offset", "offset"]
    with pytest.raises(ValueError, match="IWM"):
        level_family("IWM")


def test_spx_uses_the_offset_and_the_es_band() -> None:
    out = priced(convert(SPX, view([SPX], BARS), P))
    conv = out.conversion
    assert (conv.method, conv.factor, conv.futures_close, conv.spot) == (
        "offset",
        10.25,
        5810.5,
        5800.25,
    )
    assert (conv.instrument, conv.contract, conv.paired_bar_close_ns) == ("MES", "MESH6", T0)
    # 5790.25, 5810.25, 5811.125 (halfway, up to 5811.25), 5835.25
    assert out.levels == (23161, 23241, 23245, 23341)
    assert out.band_half_width_pts == 5.0
    assert out.band_of(5800.0) == (5805.25, 5815.25)
    assert out.level_of(5825.0) == 23341
    assert (out.symbol, out.metric, out.as_of_ns, out.family) == ("SPX", "gamma", T0, "ES")


def test_qqq_uses_the_ratio_and_the_scaled_nq_band() -> None:
    out = priced(convert(QQQ_G, view([QQQ_G], BARS), P))
    assert (out.conversion.method, out.conversion.factor) == ("ratio", 40.0)
    assert out.levels == (78400, 80000, 80880)  # 19600, 20000, 20220
    assert out.band_half_width_pts == 0.5 * 40.0
    assert out.bands[1] == (19980.0, 20020.0)


def test_spy_follows_its_configured_method() -> None:
    spy = snap("SPY", "gamma", T0, 581.0, (580.0,))
    v = view([spy], BARS)
    ratio = priced(convert(spy, v, P))
    assert ratio.conversion.factor == 5810.5 / 581.0
    assert ratio.levels == (round_to_tick(580.0 * (5810.5 / 581.0)),)
    offset_params = LevelParams(LevelsConfig(methods=LevelMethodsConfig(SPY="offset")))
    offset = priced(convert(spy, v, offset_params))
    assert (offset.conversion.method, offset.conversion.factor) == ("offset", 5229.5)
    assert offset.levels == (price_to_ticks(5809.5),)
    assert offset.band_half_width_pts == 5.0


def test_ndx_band_uses_the_qqq_snapshot_of_the_same_metric() -> None:
    v = view([QQQ_G, QQQ_V, NDX_G, NDX_V], BARS)
    gamma = priced(convert(NDX_G, v, P))
    assert (gamma.conversion.method, gamma.conversion.factor) == ("ratio", 1.25)
    assert gamma.levels == (80000, 80500)  # 20000, 20125
    assert gamma.band_half_width_pts == 0.5 * (20000.0 / 500.0)
    # QQQ vanna is older (asOf T0 - 30 s): it pairs with the MNQ bar closing at T0 - 60 s.
    vanna = priced(convert(NDX_V, v, P))
    assert vanna.band_half_width_pts == 0.5 * (19990.0 / 400.0)
    assert vanna.bands[0] == (20000.0 - 0.5 * 49.975, 20000.0 + 0.5 * 49.975)


def test_custom_half_widths_apply() -> None:
    params = LevelParams(LevelsConfig(es_half_width_pts=2.5, qqq_half_width_usd=0.1))
    v = view([SPX, QQQ_G, NDX_G], BARS)
    assert priced(convert(SPX, v, params)).band_half_width_pts == 2.5
    assert priced(convert(NDX_G, v, params)).band_half_width_pts == 0.1 * 40.0


def test_params_from_config_pick_the_traded_instruments() -> None:
    data = DataConfig(instruments=InstrumentsConfig(es_levels="ES", nq_levels="NQ"))
    params = LevelParams.from_config(LevelsConfig(), data)
    assert (params.es_instrument, params.nq_instrument) == ("ES", "NQ")
    es_bars = [bar("ES", T0, 5811.0), *BARS]
    conv = priced(convert(SPX, view([SPX], es_bars), params)).conversion
    assert (conv.instrument, conv.contract, conv.factor) == ("ES", "ESH6", 10.75)
    # No NQ bars: the NQ contract's price is missing.
    assert convert(QQQ_G, view([QQQ_G], es_bars), params) == MissingPrice("NQ")


def test_round_trip_and_source_node_ref_accept_converted_levels() -> None:
    out = priced(convert(SPX, view([SPX], BARS), P))
    for strike, level, lo_hi in zip(out.strikes, out.levels, out.bands, strict=True):
        assert abs(out.conversion.to_strike(ticks_to_points(level)) - strike) <= 0.25
        ref = SourceNodeRef("SPX", "gamma", strike, 1.0e9, level, lo_hi)
        assert ref.priced
    with pytest.raises(KeyError, match="5801"):
        out.level_of(5801.0)


# ---------------------------------------------------------------- pairing


def test_pairs_with_latest_bar_closed_at_or_before_as_of_not_t() -> None:
    early = snap("SPX", "gamma", T0 - 20 * S, 5800.0, (5800.0,))
    conv = pair(early, view([early], BARS), P)
    assert isinstance(conv, Conversion)
    # The MES bar closing at T0 closed after asOf: the T0 - 60 s bar is used.
    assert (conv.paired_bar_close_ns, conv.futures_close, conv.factor) == (T0 - 60 * S, 5805.0, 5.0)
    at_close = snap("SPX", "gamma", T0, 5800.0, (5800.0,))
    conv = pair(at_close, view([at_close], BARS), P)
    assert isinstance(conv, Conversion)
    assert conv.paired_bar_close_ns == T0


def test_max_price_gap_is_inclusive() -> None:
    bars = [bar("MES", T0, 5810.5)]
    at_gap = snap("SPX", "gamma", T0 + 120 * S, 5800.25, (5800.0,))
    over_gap = snap("SPX", "gamma", T0 + 121 * S, 5800.25, (5800.0,))
    t = T0 + 200 * S
    assert isinstance(convert(at_gap, view([at_gap], bars, t), P), ConvertedMap)
    assert convert(over_gap, view([over_gap], bars, t), P) == MissingPrice("MESH6")
    wide = LevelParams(LevelsConfig(max_price_gap_s=180))
    assert isinstance(convert(over_gap, view([over_gap], bars, t), wide), ConvertedMap)


# ---------------------------------------------------------------- missing prices


@pytest.mark.parametrize("spot", [0.0, -5800.0, math.nan, math.inf])
def test_bad_spot_is_missing_price_naming_the_symbol(spot: float) -> None:
    s = snap("SPX", "gamma", T0, spot, (5800.0,))
    assert convert(s, view([s], BARS), P) == MissingPrice("SPX")


def test_no_bar_names_the_instrument_and_bad_close_names_the_contract() -> None:
    assert convert(SPX, view([SPX], []), P) == MissingPrice("MES")
    zero = [bar("MES", T0, 0.0)]
    assert convert(SPX, view([SPX], zero), P) == MissingPrice("MESH6")
    negative = [bar("MES", T0, -1.0)]
    assert convert(SPX, view([SPX], negative), P) == MissingPrice("MESH6")


def test_nq_band_needs_a_priced_qqq_snapshot_of_the_same_metric() -> None:
    # No QQQ vanna Snapshot: the band's QQQ spot is absent.
    assert convert(NDX_V, view([QQQ_G, NDX_V], BARS), P) == MissingPrice("QQQ")
    # QQQ not configured at all.
    no_qqq = view([NDX_G], BARS, symbols=("NDX",))
    assert convert(NDX_G, no_qqq, P) == MissingPrice("QQQ")
    # QQQ gamma's own NQ pairing is stale (asOf 10 min after the last MNQ bar).
    late_qqq = snap("QQQ", "gamma", T0 + 600 * S, 500.0, (500.0,))
    fresh_bar = [bar("MNQ", T0 + 660 * S, 20000.0)]
    ndx_late = snap("NDX", "gamma", T0 + 660 * S, 16000.0, (16000.0,))
    v = view([late_qqq, ndx_late], [bar("MNQ", T0, 20000.0), *fresh_bar], T0 + 660 * S)
    assert convert(ndx_late, v, P) == MissingPrice("MNQH6")


def test_no_earlier_factor_is_reused() -> None:
    later = snap("SPX", "gamma", T0 + 300 * S, 5801.0, (5800.0,))
    inputs = HistoricalInputs(symbols=("SPX",), view_id=VIEW_ID, snapshots=[SPX, later], bars=BARS)
    first = inputs.view(T0)
    assert isinstance(convert_map(first, P)[("SPX", "gamma")], ConvertedMap)
    # Five minutes later the newest MES bar is 300 s old: missing, not the T0 offset.
    assert convert_map(inputs.view(T0 + 300 * S), P)[("SPX", "gamma")] == MissingPrice("MESH6")


def test_snapshot_after_t_and_unknown_symbol_raise() -> None:
    with pytest.raises(ValueError, match="after t"):
        convert(SPX, view([], BARS, T0 - S), P)
    iwm = snap("IWM", "gamma", T0, 200.0, (200.0,))
    with pytest.raises(ValueError, match="IWM"):
        convert(iwm, view([], BARS), P)


# ---------------------------------------------------------------- whole Map_State


def test_convert_map_covers_every_map_state_entry_in_order() -> None:
    v = view([SPX, QQQ_G, QQQ_V, NDX_G], BARS)
    out = convert_map(v, P)
    assert list(out) == list(v.map_state())
    for key, result in out.items():
        entry = v.map_state().entries[key]
        if isinstance(entry, Snapshot):
            assert result == convert(entry, v, P)
        else:
            assert result == MissingPrice(key[0])
    assert out[("SPX", "vanna")] == MissingPrice("SPX")
    assert out[("NDX", "vanna")] == MissingPrice("NDX")
    assert priced(out[("NDX", "gamma")]).band_half_width_pts == 20.0


@pytest.mark.parametrize(
    ("kw", "message"),
    [
        ({"es_instrument": "NQ"}, "es_instrument"),
        ({"nq_instrument": "MES"}, "nq_instrument"),
    ],
)
def test_level_params_reject_a_wrong_family_instrument(kw: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        LevelParams(**kw)
