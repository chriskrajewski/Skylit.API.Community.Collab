"""Property 27: Conversion is monotonic.

*For any* Snapshot and conversion factor, any two strikes a < b map to
rounded levels with L(a) <= L(b).

Two views of the property:

- **One conversion, any factor.** A :class:`~fse.engine.levels.Conversion`
  of either method, paired from a spot and a futures close drawn near real
  prices or anywhere from 1e-6 to 1e150 (so the offset runs from about -1e150
  to 4e18 and the ratio from about 1e-151 to 4e24), maps two strikes a < b.
  Strike a sits on a half tick in level space (where rounding halves up),
  near +-2**50 (where :func:`~fse.engine.levels.round_to_tick` switches to
  exact rationals), near the spot or anywhere finite. Strike b is the next
  float above a, a + k/8, or a + up to 1e6.
- **Every Map_State Snapshot.** One session with all five symbols and both
  metrics, fresh positive bars for ES, MES, NQ and MNQ, and drawn SPY, NDX
  and NDXP methods goes through ``convert_map``. Each Snapshot's strikes are
  unsorted and include duplicates and adjacent floats. For every pair of
  strikes a < b the parallel levels satisfy L(a) <= L(b), and ``level_of``
  is non-decreasing over the sorted strikes.

**Validates: Requirements 8.9**
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from datetime import date, time
from typing import Final

from hypothesis import example, given
from hypothesis import strategies as st

from fse.config.schema.data import EsInstrument, NqInstrument
from fse.config.schema.levels import LevelMethodsConfig, LevelsConfig
from fse.engine.levels import Conversion, ConvertedMap, LevelParams, convert_map
from fse.engine.types import Bar, ConversionMethod, Metric, Snapshot, Ticks
from fse.pit.market_view import HistoricalInputs
from fse.timekit import NS_PER_SECOND, Instant, ny_instant

S: Final = NS_PER_SECOND
T0: Final[Instant] = ny_instant(date(2026, 3, 5), time(10, 0))
VIEW_ID: Final = "view-p27"

SYMBOLS: Final = ("SPX", "SPY", "QQQ", "NDX", "NDXP")
METRICS: Final[tuple[Metric, ...]] = ("gamma", "vanna")
SPOT_BASE: Final = {"SPX": 5800.0, "SPY": 580.0, "QQQ": 500.0, "NDX": 20000.0, "NDXP": 20000.0}
INSTRUMENTS: Final = ("ES", "MES", "NQ", "MNQ")
FUTURES_BASE: Final = {"ES": 5810.0, "MES": 5810.0, "NQ": 20050.0, "MNQ": 20050.0}
METHODS: Final[tuple[ConversionMethod, ...]] = ("offset", "ratio")
ES_INSTRUMENTS: Final[tuple[EsInstrument, ...]] = ("ES", "MES")
NQ_INSTRUMENTS: Final[tuple[NqInstrument, ...]] = ("NQ", "MNQ")

WIDE: Final = 1.0e150
BOUNDARY: Final = 2.0**50  # round_to_tick computes in exact rationals from this magnitude

# ---------------------------------------------------------------- inputs


def conversion(method: ConversionMethod, spot: float, futures: float) -> Conversion:
    """The Conversion ``pair`` would build from ``spot`` and a futures close in points."""
    factor = futures - spot if method == "offset" else futures / spot
    return Conversion(
        symbol="SPX" if method == "offset" else "QQQ",
        family="ES" if method == "offset" else "NQ",
        instrument="MES" if method == "offset" else "MNQ",
        contract="MESH6" if method == "offset" else "MNQH6",
        method=method,
        factor=factor,
        futures_close=futures,
        spot=spot,
        as_of_ns=T0,
        paired_bar_close_ns=T0,
    )


def snap(
    symbol: str, metric: Metric, spot: float, strikes: tuple[float, ...], uid: int
) -> Snapshot:
    """A Snapshot at ``T0`` made unique by ``uid`` (in ``as_of_raw``)."""
    return Snapshot(
        symbol=symbol,
        metric=metric,
        view_id=VIEW_ID,
        as_of_ns=T0,
        as_of_raw=f"raw-{uid}",
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


def make_bar(instrument: str, close_ns: Instant, c_t: Ticks) -> Bar:
    """A 1-minute bar of the session's contract closing at ``close_ns``."""
    px = c_t / 4
    return Bar(
        instrument, f"{instrument}H6", 60, close_ns - 60 * S, close_ns,
        px, px, px, px, 1.0, c_t, c_t, c_t, c_t, "atlas",
    )  # fmt: skip


@dataclass(frozen=True, slots=True)
class LevelCase:
    """One conversion and two strikes ``a < b``."""

    conversion: Conversion
    a: float
    b: float


@dataclass(frozen=True, slots=True)
class Session:
    """One session's Snapshots and bars, all at ``T0``, and the converter parameters."""

    symbols: tuple[str, ...]
    params: LevelParams
    bars: tuple[Bar, ...]
    snapshots: tuple[Snapshot, ...]


# ---------------------------------------------------------------- generators


def weighted[T](*options: tuple[int, st.SearchStrategy[T]]) -> st.SearchStrategy[T]:
    """Pick a strategy in proportion to its weight (``st.one_of`` drops repeated branches)."""
    pool = [strategy for weight, strategy in options for _ in range(weight)]
    return st.sampled_from(pool).flatmap(lambda strategy: strategy)


def _near_float(base: float) -> st.SearchStrategy[float]:
    return st.floats(base * 0.98, base * 1.02)


def _near_ticks(base: float) -> st.SearchStrategy[float]:
    return st.integers(int(base * 4 * 0.98), int(base * 4 * 1.02)).map(lambda t: t / 4)


ANY_SPOT: Final = weighted(
    (3, st.sampled_from(sorted(set(SPOT_BASE.values()))).flatmap(_near_float)),
    (1, st.floats(1.0e-6, WIDE)),
)
ANY_FUTURES: Final = weighted(  # a futures close is a whole number of ticks
    (3, st.sampled_from(sorted(set(FUTURES_BASE.values()))).flatmap(_near_ticks)),
    (1, st.integers(1, 2**64).map(lambda t: t / 4)),
)
HALF_TICK_LEVELS: Final = st.integers(-4 * 10**7, 4 * 10**7).map(lambda k: k / 4 + 0.125)
BOUNDARY_LEVELS: Final = st.sampled_from((BOUNDARY, -BOUNDARY)).flatmap(
    lambda x: st.floats(x - 64.0, x + 64.0)
)
STEPS: Final = weighted(  # 0: b is the next float above a
    (3, st.just(0.0)),
    (2, st.integers(1, 80).map(lambda k: k / 8)),
    (1, st.floats(0.0, 1.0e6)),
)


@st.composite
def level_cases(draw: st.DrawFn) -> LevelCase:
    method = draw(st.sampled_from(METHODS))
    spot = draw(ANY_SPOT)
    conv = conversion(method, spot, draw(ANY_FUTURES))
    a = draw(
        weighted(
            (3, HALF_TICK_LEVELS.map(conv.to_strike)),
            (1, BOUNDARY_LEVELS.map(conv.to_strike)),
            (2, st.floats(spot * 0.5, spot * 1.5)),
            (2, st.floats(-WIDE, WIDE)),
        )
    )
    b = max(a + draw(STEPS), math.nextafter(a, math.inf))
    return LevelCase(conv, a, b)


def strikes_near(symbol: str) -> st.SearchStrategy[float]:
    """Strikes on a 5-point grid, on a 1/8 grid, or any positive value."""
    base = SPOT_BASE[symbol]
    return st.one_of(
        st.integers(int(base * 0.9 / 5), int(base * 1.1 / 5)).map(lambda k: k * 5.0),
        st.integers(int(base * 8 * 0.9), int(base * 8 * 1.1)).map(lambda k: k / 8),
        st.floats(0.01, 1.0e5),
    )


@st.composite
def strike_lists(draw: st.DrawFn, symbol: str) -> tuple[float, ...]:
    """Unsorted strikes with duplicates (step 0) and adjacent floats (step +-1)."""
    base = draw(st.lists(strikes_near(symbol), min_size=2, max_size=8))
    near = st.tuples(st.sampled_from(base), st.sampled_from((-1, 0, 1)))
    extra = [
        k if step == 0 else math.nextafter(k, step * math.inf)
        for k, step in draw(st.lists(near, max_size=4))
    ]
    return tuple(draw(st.permutations(base + extra)))


def spots(symbol: str) -> st.SearchStrategy[float]:
    """Positive spots on a 1/8 grid, near the symbol's price, or anywhere."""
    base = SPOT_BASE[symbol]
    eighths = st.integers(int(base * 8 * 0.98), int(base * 8 * 1.02)).map(lambda k: k / 8)
    return weighted((3, eighths), (3, _near_float(base)), (1, st.floats(0.01, 1.0e5)))


def close_ticks(instrument: str) -> st.SearchStrategy[Ticks]:
    """Positive tick closes, mostly near the instrument's price."""
    base = int(FUTURES_BASE[instrument] * 4)
    return weighted((4, st.integers(base - 800, base + 800)), (1, st.integers(1, 400_000)))


@st.composite
def sessions(draw: st.DrawFn) -> Session:
    symbols = tuple(draw(st.permutations(SYMBOLS)))
    levels = LevelsConfig(
        methods=LevelMethodsConfig(
            SPY=draw(st.sampled_from(METHODS)),
            NDX=draw(st.sampled_from(METHODS)),
            NDXP=draw(st.sampled_from(METHODS)),
        )
    )
    params = LevelParams(
        levels, draw(st.sampled_from(ES_INSTRUMENTS)), draw(st.sampled_from(NQ_INSTRUMENTS))
    )
    bars = tuple(make_bar(i, T0, draw(close_ticks(i))) for i in INSTRUMENTS)
    snapshots = tuple(
        snap(symbol, metric, draw(spots(symbol)), draw(strike_lists(symbol)), uid)
        for uid, (symbol, metric) in enumerate(itertools.product(symbols, METRICS))
    )
    return Session(symbols, params, bars, snapshots)


# ---------------------------------------------------------------- explicit examples

_BELOW_HALF: Final = math.nextafter(5800.875, -math.inf)  # 5811.125 - ulp: rounds down
_HALF_TICK_SESSION = Session(  # defaults: MES, MNQ, SPY/NDX/NDXP ratio
    symbols=SYMBOLS,
    params=LevelParams(),
    bars=(make_bar("MES", T0, 23242), make_bar("MNQ", T0, 80000)),  # 5810.5, 20000
    snapshots=tuple(
        snap(
            symbol,
            metric,
            5800.25 if symbol == "SPX" else SPOT_BASE[symbol],  # SPX offset 10.25
            (5825.0, 5800.875, _BELOW_HALF, 5800.75, 5800.875)
            if symbol == "SPX"
            else (SPOT_BASE[symbol] * 1.01, SPOT_BASE[symbol], SPOT_BASE[symbol] * 0.99),
            uid,
        )
        for uid, (symbol, metric) in enumerate(itertools.product(SYMBOLS, METRICS))
    ),
)


# ---------------------------------------------------------------- properties


# Feature: skylit-futures-strategy-engine, Property 27: Conversion is monotonic
@example(case=LevelCase(conversion("offset", 5800.25, 5810.5), _BELOW_HALF, 5800.875))
@example(  # ratio 2: 10000.0625 -> 20000.125, a half tick
    case=LevelCase(
        conversion("ratio", 10000.0, 20000.0), math.nextafter(10000.0625, -math.inf), 10000.0625
    )
)
@example(case=LevelCase(conversion("offset", 5810.5, 5810.5), BOUNDARY - 0.125, BOUNDARY))
@given(case=level_cases())
def test_conversion_level_is_monotonic(case: LevelCase) -> None:
    conv, a, b = case.conversion, case.a, case.b
    assert a < b
    la, lb = conv.level(a), conv.level(b)
    assert la <= lb, (
        f"{conv.method} factor {conv.factor!r}: strike {a!r} -> {la}, strike {b!r} -> {lb}"
    )


# Feature: skylit-futures-strategy-engine, Property 27: Conversion is monotonic
@example(session=_HALF_TICK_SESSION)
@given(session=sessions())
def test_converted_map_levels_are_monotonic(session: Session) -> None:
    inputs = HistoricalInputs(
        symbols=session.symbols,
        view_id=VIEW_ID,
        metrics=METRICS,
        snapshots=session.snapshots,
        bars=session.bars,
    )
    out = convert_map(inputs.view(T0), session.params)
    assert len(out) == len(SYMBOLS) * len(METRICS)
    for key, got in out.items():
        assert isinstance(got, ConvertedMap), f"{key}: got {got!r}, want converted levels"
        pairs = list(zip(got.strikes, got.levels, strict=True))
        for (a, la), (b, lb) in itertools.product(pairs, pairs):
            if a < b:
                assert la <= lb, f"{key}: strike {a!r} -> {la}, strike {b!r} -> {lb}"
        ordered = [got.level_of(k) for k in sorted(set(got.strikes))]
        assert ordered == sorted(ordered), f"{key}: level_of over sorted strikes is {ordered}"
