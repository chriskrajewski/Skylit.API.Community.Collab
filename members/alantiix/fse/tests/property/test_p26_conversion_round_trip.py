"""Property 26: Conversion round-trip.

*For any* strike greater than 0 and any offset or positive ratio, converting
to a rounded futures level and back with the same offset or ratio yields a
value within 0.25 strike units (offset) or 0.25 / ratio strike units (ratio)
of the original strike.

The levels come from :func:`~fse.engine.levels.convert_map`, so the offset or
ratio is the one the Level_Converter pairs from a Snapshot's spot and a futures
close (Req 8.4) and the rounding is its own (Req 8.5). Each case converts one
Snapshot of every symbol (SPX and SPY to ES; QQQ, NDX and NDXP to NQ), so both
methods run in every case. Each case draws:

- the SPY, NDX and NDXP methods (SPX is always offset, QQQ always ratio);
- one ES-family and one NQ-family 1-minute close, near a realistic price or
  anywhere from 0.25 to 10^6 points;
- each symbol's spot, near its price, on a 1/8 grid (so offset levels land on
  half ticks) or anywhere from 0.01 to 10^6;
- each symbol's strikes, on a 5-point or 1/8 grid near its price, or any value
  in (0, 10^6].

The offset then spans about ±10^6 points (so a level may be negative) and the
ratio 2.5e-7 to 10^8. Every unrounded level stays below 10^14 points, well
under 2^49: past that, a float cannot hold a level to a small fraction of a
tick, and the bound becomes a matter of float precision, not of the converter.

Each level L (in points) goes back by the requirement's formulas, L - offset or
L / ratio, which must also match ``Conversion.to_strike``.

**Validates: Requirements 8.8**
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, time
from typing import Final

from hypothesis import example, given
from hypothesis import strategies as st

from fse.config.schema.levels import LevelMethodsConfig, LevelsConfig
from fse.engine.levels import ConvertedMap, LevelFamily, LevelParams, convert_map
from fse.engine.types import Bar, ConversionMethod, Metric, Snapshot, Ticks
from fse.pit.market_view import HistoricalInputs
from fse.timekit import NS_PER_SECOND, Instant, ny_instant

T0: Final[Instant] = ny_instant(date(2026, 3, 5), time(10, 0))
VIEW_ID: Final = "view-p26"
METRIC: Final[Metric] = "gamma"

SYMBOLS: Final = ("SPX", "SPY", "QQQ", "NDX", "NDXP")
FAMILIES: Final[tuple[LevelFamily, ...]] = ("ES", "NQ")
SPOT_BASE: Final = {"SPX": 5800.0, "SPY": 580.0, "QQQ": 500.0, "NDX": 20000.0, "NDXP": 20000.0}
FUTURES_BASE_TICKS: Final[Mapping[LevelFamily, Ticks]] = {"ES": 5810 * 4, "NQ": 20050 * 4}
METHODS: Final[tuple[ConversionMethod, ...]] = ("offset", "ratio")

TICK: Final = 0.25
PRICE_MIN: Final = 0.01  # the smallest spot drawn
PRICE_MAX: Final = 1.0e6  # the largest spot, futures close and strike drawn
TINY: Final = math.ulp(0.0)  # the smallest positive float

# ---------------------------------------------------------------- inputs


def snap(symbol: str, spot: float, strikes: tuple[float, ...]) -> Snapshot:
    """``symbol``'s Snapshot at T0."""
    return Snapshot(
        symbol=symbol,
        metric=METRIC,
        view_id=VIEW_ID,
        as_of_ns=T0,
        as_of_raw=f"raw-{symbol}",
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


def make_bar(instrument: str, c_t: Ticks) -> Bar:
    """A 1-minute bar of the session's contract that closes at T0, the Snapshots' ``asOf``."""
    px = c_t / 4
    return Bar(
        instrument, f"{instrument}H6", 60, T0 - 60 * NS_PER_SECOND, T0,
        px, px, px, px, 1.0, c_t, c_t, c_t, c_t, "atlas",
    )  # fmt: skip


@dataclass(frozen=True, slots=True)
class Case:
    """The converter parameters, each family's paired close and each symbol's Snapshot."""

    params: LevelParams
    futures: Mapping[LevelFamily, Ticks]
    spots: Mapping[str, float]
    strikes: Mapping[str, tuple[float, ...]]

    def inputs(self) -> HistoricalInputs:
        instrument = {"ES": self.params.es_instrument, "NQ": self.params.nq_instrument}
        return HistoricalInputs(
            symbols=SYMBOLS,
            view_id=VIEW_ID,
            metrics=(METRIC,),
            snapshots=[snap(s, self.spots[s], self.strikes[s]) for s in SYMBOLS],
            bars=[make_bar(instrument[f], c_t) for f, c_t in self.futures.items()],
        )


# ---------------------------------------------------------------- generators


def futures_ticks(family: LevelFamily) -> st.SearchStrategy[Ticks]:
    """A close near the family's price, or anywhere from 0.25 to 10^6 points."""
    base = FUTURES_BASE_TICKS[family]
    return st.one_of(st.integers(base - 800, base + 800), st.integers(1, int(PRICE_MAX * 4)))


def spots(symbol: str) -> st.SearchStrategy[float]:
    """A spot on a 1/8 grid near the symbol's price, near it, or anywhere in range."""
    base = SPOT_BASE[symbol]
    return st.one_of(
        st.integers(int(base * 8 * 0.98), int(base * 8 * 1.02)).map(lambda k: k / 8),
        st.floats(base * 0.95, base * 1.05),
        st.floats(PRICE_MIN, PRICE_MAX),
    )


def strike_values(symbol: str) -> st.SearchStrategy[float]:
    """A strike on a 5-point or 1/8 grid near the symbol's price, or any value in (0, 10^6]."""
    base = SPOT_BASE[symbol]
    return st.one_of(
        st.integers(int(base * 0.9 / 5), int(base * 1.1 / 5)).map(lambda k: k * 5.0),
        st.integers(int(base * 8 * 0.9), int(base * 8 * 1.1)).map(lambda k: k / 8),
        st.floats(0.0, PRICE_MAX, exclude_min=True),
    )


@st.composite
def cases(draw: st.DrawFn) -> Case:
    methods = LevelMethodsConfig(
        SPY=draw(st.sampled_from(METHODS)),
        NDX=draw(st.sampled_from(METHODS)),
        NDXP=draw(st.sampled_from(METHODS)),
    )
    return Case(
        params=LevelParams(LevelsConfig(methods=methods)),
        futures={f: draw(futures_ticks(f)) for f in FAMILIES},
        spots={s: draw(spots(s)) for s in SYMBOLS},
        strikes={
            s: tuple(draw(st.lists(strike_values(s), min_size=1, max_size=8))) for s in SYMBOLS
        },
    )


# ---------------------------------------------------------------- explicit examples

_HALF_TICKS = Case(  # every level below is exactly half a tick from its neighbours, or on one
    params=LevelParams(
        LevelsConfig(methods=LevelMethodsConfig(SPY="offset", NDX="offset", NDXP="ratio"))
    ),
    futures={"ES": 23242, "NQ": 80000},  # 5810.5 and 20000.0
    spots={"SPX": 5800.25, "SPY": 580.125, "QQQ": 500.0, "NDX": 19990.125, "NDXP": 20000.0},
    strikes={
        "SPX": (5800.875, 5800.625, 5780.0),  # offset 10.25: 5811.125 and 5810.875 round up
        "SPY": (580.0, 580.25),  # offset 5230.375: 5810.375 and 5810.625 round up
        "QQQ": (500.0, 505.5, 500.003125),  # ratio 40
        "NDX": (19990.0,),  # offset 9.875: 19999.875 rounds up
        "NDXP": (20000.125,),  # ratio 1: 20000.125 rounds up
    },
)
_DOMAIN_CORNERS = Case(  # the extremes of the drawn prices
    params=LevelParams(
        LevelsConfig(methods=LevelMethodsConfig(SPY="ratio", NDX="ratio", NDXP="offset"))
    ),
    futures={"ES": 1, "NQ": int(PRICE_MAX * 4)},  # 0.25 and 10^6 points
    spots={
        "SPX": PRICE_MAX,
        "SPY": PRICE_MIN,
        "QQQ": PRICE_MIN,
        "NDX": PRICE_MAX,
        "NDXP": PRICE_MIN,
    },
    strikes={
        "SPX": (PRICE_MAX, PRICE_MIN, TINY),  # offset about -10^6: levels near 0.25 and -10^6
        "SPY": (PRICE_MAX, TINY),  # ratio 25
        "QQQ": (PRICE_MAX, PRICE_MIN, TINY),  # ratio 10^8: a level of 10^14 points
        "NDX": (PRICE_MAX, TINY),  # ratio 1
        "NDXP": (PRICE_MAX, TINY),  # offset about 10^6
    },
)


# ---------------------------------------------------------------- property


# Feature: skylit-futures-strategy-engine, Property 26: Conversion round-trip
@example(case=_HALF_TICKS)
@example(case=_DOMAIN_CORNERS)
@given(case=cases())
def test_conversion_round_trip(case: Case) -> None:
    out = convert_map(case.inputs().view(T0), case.params)
    assert list(out) == [(symbol, METRIC) for symbol in SYMBOLS]
    for (symbol, _), converted in out.items():
        # Every spot and close is a positive price paired at asOf: nothing is missing.
        assert isinstance(converted, ConvertedMap), f"{symbol}: got {converted!r}"
        assert converted.strikes == case.strikes[symbol]
        conv = converted.conversion
        for strike, level in zip(converted.strikes, converted.levels, strict=True):
            level_pts = level / 4
            if conv.method == "offset":
                back, tolerance = level_pts - conv.factor, TICK
            else:
                assert conv.factor > 0, f"{symbol}: the ratio {conv.factor!r} is not positive"
                back, tolerance = level_pts / conv.factor, TICK / conv.factor
            where = (
                f"{symbol} {conv.method} {conv.factor!r}: strike {strike!r}"
                f" -> L {level_pts!r} -> {back!r}"
            )
            assert abs(back - strike) <= tolerance, f"{where} is off by more than {tolerance!r}"
            to_strike = conv.to_strike(level_pts)
            assert to_strike == back, f"{where}, but to_strike gives {to_strike!r}"
