"""Property 25: Tick rounding.

*For any* finite level x, the rounded level is a multiple of 0.25 within 0.125
of x, and when x lies exactly halfway between two ticks it is the higher tick.

The oracle works in exact rational arithmetic: a rounded level is an integer
tick count ``n`` (so ``n / 4`` points is a multiple of 0.25), ``|n/4 - x| <=
1/8``, and ``n/4 - x`` is never ``-1/8`` (a half rounds up). Those three facts
fix ``n`` for every finite ``x``.

Two tests:

- :func:`~fse.engine.levels.round_to_tick` on any finite float, plus floats
  biased to exact half ticks, to a few ulps either side of a tick or a half
  tick, and to the 2**50 magnitude where every float is on the tick grid;
- the levels of :func:`~fse.engine.levels.convert_map` for one Snapshot and one
  paired bar, checked against the unrounded level recomputed from the formula
  (Req 8.1-8.3), with spots and strikes on a 1/8 grid and power-of-two ratios
  so that converted levels often land exactly on half ticks.

**Validates: Requirements 8.5**
"""

from __future__ import annotations

import math
from datetime import date, time
from fractions import Fraction
from typing import Final

from hypothesis import example, given
from hypothesis import strategies as st

from fse.config.schema.levels import LevelMethodsConfig, LevelsConfig
from fse.engine.levels import ConvertedMap, LevelParams, convert_map, round_to_tick
from fse.engine.types import Bar, ConversionMethod, Snapshot, Ticks
from fse.pit.market_view import HistoricalInputs
from fse.timekit import NS_PER_SECOND, Instant, ny_instant

EIGHTH: Final = Fraction(1, 8)
T0: Final[Instant] = ny_instant(date(2026, 3, 5), time(10, 0))
VIEW_ID: Final = "view-p25"
ON_GRID_ABOVE: Final = 2.0**50  # every float this large or larger has an ulp >= 0.25
MAX_EXACT_EIGHTHS: Final = 2**53 - 1  # m / 8 is an exact float for |m| <= this

SPOT_BASE: Final = {"SPX": 5800.0, "SPY": 580.0, "QQQ": 500.0}
INSTRUMENT: Final = {"SPX": "MES", "SPY": "MES", "QQQ": "MNQ"}  # LevelParams defaults
FUTURES_BASE_TICKS: Final = {"MES": 5810 * 4, "MNQ": 20050 * 4}
METHODS: Final[tuple[ConversionMethod, ...]] = ("offset", "ratio")

# ---------------------------------------------------------------- oracle


def check_rounded(x: float, got: Ticks, where: str) -> None:
    """``got`` ticks is the nearest 0.25-point tick to ``x``, a half rounded up (Req 8.5)."""
    assert type(got) is int, f"{where}: {x!r} gave {got!r}, not an integer tick count"
    exact = Fraction(x)
    diff = Fraction(got, 4) - exact
    assert abs(diff) <= EIGHTH, f"{where}: {x!r} gave {got} ticks, {float(diff)} points away"
    if (exact * 8).denominator == 1 and (exact * 8).numerator % 2 == 1:  # exactly halfway
        assert diff == EIGHTH, f"{where}: halfway {x!r} gave {got} ticks, not the higher tick"


# ---------------------------------------------------------------- generators


def ulps(x: float, n: int) -> float:
    """``x`` moved ``n`` floats up (``n > 0``) or down (``n < 0``)."""
    toward = math.inf if n > 0 else -math.inf
    for _ in range(abs(n)):
        x = math.nextafter(x, toward)
    return x


def signed[N: (int, float)](values: st.SearchStrategy[N]) -> st.SearchStrategy[N]:
    """``values`` with either sign."""
    return values.flatmap(lambda v: st.sampled_from((v, -v)))


EIGHTHS: Final = st.one_of(  # m for x = m / 8: small, anywhere exact, or next to 2**50
    st.integers(-4000, 4000),
    st.integers(-MAX_EXACT_EIGHTHS, MAX_EXACT_EIGHTHS),
    signed(st.integers(MAX_EXACT_EIGHTHS - 2000, MAX_EXACT_EIGHTHS)),
)
HALF_TICKS: Final = EIGHTHS.map(lambda m: (m | 1) / 8)  # m odd: exactly between two ticks
NEAR_GRID: Final = st.builds(lambda m, n: ulps(m / 8, n), EIGHTHS, st.integers(-3, 3))
NEAR_ON_GRID_ABOVE: Final = signed(
    st.integers(-8, 8).map(lambda n: ulps(ON_GRID_ABOVE, n))  # either side of 2**50
)
LEVELS: Final = st.one_of(
    st.floats(allow_nan=False, allow_infinity=False),
    HALF_TICKS,
    NEAR_GRID,
    NEAR_ON_GRID_ABOVE,
    signed(st.floats(2.0**48, 2.0**52)),
)


def snap(symbol: str, spot: float, strikes: tuple[float, ...]) -> Snapshot:
    return Snapshot(
        symbol=symbol,
        metric="gamma",
        view_id=VIEW_ID,
        as_of_ns=T0,
        as_of_raw="raw-0",
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
    """A 1-minute bar of the session's contract closing at ``T0``, the Snapshot's ``asOf``."""
    px = c_t / 4
    return Bar(
        instrument, f"{instrument}H6", 60, T0 - 60 * NS_PER_SECOND, T0,
        px, px, px, px, 1.0, c_t, c_t, c_t, c_t, "atlas",
    )  # fmt: skip


@st.composite
def conversions(draw: st.DrawFn) -> tuple[str, LevelParams, Snapshot, Bar]:
    """One Snapshot and its paired bar, with spots and strikes that make half ticks likely."""
    symbol = draw(st.sampled_from(tuple(SPOT_BASE)))
    spy_method = draw(st.sampled_from(METHODS))
    params = LevelParams(LevelsConfig(methods=LevelMethodsConfig(SPY=spy_method)))
    instrument = INSTRUMENT[symbol]
    base_ticks = FUTURES_BASE_TICKS[instrument]
    c_t = draw(st.one_of(st.integers(base_ticks - 800, base_ticks + 800), st.integers(1, 400_000)))
    base = SPOT_BASE[symbol]
    spot = draw(
        st.one_of(
            st.integers(int(base * 8 * 0.98), int(base * 8 * 1.02)).map(lambda k: k / 8),
            st.sampled_from((0.25, 0.5, 1.0, 2.0, 4.0)).map(lambda r: c_t / 4 / r),  # ratio 2**j
            st.floats(0.01, 1.0e5),
        )
    )
    strike = st.one_of(
        st.integers(int(base * 8 * 0.9), int(base * 8 * 1.1)).map(lambda k: k / 8),
        st.integers(1, 8 * 10**5).map(lambda k: k / 8),
        st.floats(0.01, 1.0e5),
    )
    strikes = tuple(draw(st.lists(strike, min_size=1, max_size=8)))
    return symbol, params, snap(symbol, spot, strikes), make_bar(instrument, c_t)


# ---------------------------------------------------------------- properties


# Feature: skylit-futures-strategy-engine, Property 25: Tick rounding
@example(x=0.125)  # halfway: up to 1 tick
@example(x=-0.125)  # halfway: up to 0 ticks
@example(x=-0.375)  # halfway: up to -1 tick
@example(x=5811.125)
@example(x=-0.0)
@example(x=5e-324)
@example(x=-5e-324)
@example(x=ON_GRID_ABOVE - 0.125)  # the largest halfway float
@example(x=-(ON_GRID_ABOVE - 0.125))
@example(x=1.7976931348623157e308)
@given(x=LEVELS)
def test_round_to_tick_is_nearest_tick_with_halves_up(x: float) -> None:
    check_rounded(x, round_to_tick(x), "round_to_tick")


# Feature: skylit-futures-strategy-engine, Property 25: Tick rounding
@given(case=conversions())
def test_converted_levels_are_tick_rounded(case: tuple[str, LevelParams, Snapshot, Bar]) -> None:
    symbol, params, s, b = case
    inputs = HistoricalInputs(
        symbols=(symbol,), view_id=VIEW_ID, metrics=("gamma",), snapshots=(s,), bars=(b,)
    )
    out = convert_map(inputs.view(T0), params)[(symbol, "gamma")]
    assert isinstance(out, ConvertedMap), f"{symbol}: got {out!r}, want converted levels"
    assert b.c_t is not None
    futures = b.c_t / 4
    method = out.conversion.method
    assert len(out.levels) == len(s.strikes)
    for strike, level in zip(s.strikes, out.levels, strict=True):
        x = strike + (futures - s.spot) if method == "offset" else strike * (futures / s.spot)
        check_rounded(x, level, f"{symbol} {method} strike {strike!r}")
