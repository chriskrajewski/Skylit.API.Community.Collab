"""Property 24: Conversion formulas and pairing.

*For any* Snapshot, bar stream and per-symbol method configuration, each
converted level equals strike + (futures - spot) for the offset method or
strike x (futures / spot) for the ratio method, using the close of the latest
bar of the session's contract that closed at or before the Snapshot's `asOf`;
bands equal L ± h for ES and L ± h_qqq x (NQ / QQQ spot) for NQ; and a
missing, non-positive or stale price yields a missing-price result naming the
symbol or contract, never a reused factor.

One session's inputs go through :class:`~fse.pit.market_view.HistoricalInputs`
and are viewed at several Decision_Times, in drawn (not sorted) order, so a
factor cached from one view would show up at another. Each case draws:

- the configured symbols (any non-empty subset of SPX, SPY, QQQ, NDX and NDXP,
  in any order) and metrics, the SPY, NDX and NDXP methods, both band
  half-widths, the maximum price gap and the ES and NQ instruments;
- 1-minute bars of all four of ES, MES, NQ and MNQ (only the configured one
  of each family may be paired): a run of minute bars with holes plus a few
  off-grid closes, unique per instrument, with closes that may be 0, negative
  or absent and :class:`~fse.pit.asof.Received` wrappers that delay
  availability; 5-minute bars as noise;
- Snapshots of every symbol and metric, configured or not, with spots that may
  be 0, -0.0, negative, NaN or infinite, ``asOf`` values biased to each bar
  close and to the gap boundary (-1, 0, +1 ns), and strikes on a 1/8 grid so
  offset levels land exactly on half ticks.

The oracle rescans every bar for the pairing (Req 8.4), recomputes the factor
in the formula's float order, rounds half up in exact rational arithmetic
(Req 8.5) and builds each band from the spec (Req 8.6-8.7). Where several
prices are missing at once, a MissingPrice naming any of them is accepted
(Req 8.10). ``pair`` is checked for every Snapshot with ``asOf <= t``;
``convert`` and ``convert_map`` for every Map_State entry.

**Validates: Requirements 8.1, 8.2, 8.3, 8.4, 8.6, 8.7, 8.10**
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, time
from fractions import Fraction
from typing import Final

from hypothesis import example, given
from hypothesis import strategies as st

from fse.config.schema.data import EsInstrument, NqInstrument
from fse.config.schema.levels import (
    ES_HALF_WIDTH_MAX_PTS,
    ES_HALF_WIDTH_MIN_PTS,
    MAX_PRICE_GAP_MAX_S,
    MAX_PRICE_GAP_MIN_S,
    QQQ_HALF_WIDTH_MAX_USD,
    QQQ_HALF_WIDTH_MIN_USD,
    LevelMethodsConfig,
    LevelsConfig,
)
from fse.engine.levels import (
    Conversion,
    ConvertedMap,
    LevelFamily,
    LevelParams,
    convert,
    convert_map,
    pair,
)
from fse.engine.types import Bar, ConversionMethod, Metric, MissingPrice, Snapshot, Ticks
from fse.pit.asof import Received
from fse.pit.market_view import HistoricalInputs, HistoricalMarketView
from fse.timekit import NS_PER_SECOND, Instant, ny_instant

type BarItem = Bar | Received[Bar]
type Pairing = Conversion | frozenset[str]  # frozenset: the names a MissingPrice may carry
type Converted = ConvertedMap | frozenset[str]

S: Final = NS_PER_SECOND
MINUTE: Final = 60 * S
T0: Final[Instant] = ny_instant(date(2026, 3, 5), time(10, 0))
VIEW_ID: Final = "view-p24"

SYMBOLS: Final = ("SPX", "SPY", "QQQ", "NDX", "NDXP")
METRICS: Final[tuple[Metric, ...]] = ("gamma", "vanna")
FAMILY: Final[dict[str, LevelFamily]] = {
    "SPX": "ES",
    "SPY": "ES",
    "QQQ": "NQ",
    "NDX": "NQ",
    "NDXP": "NQ",
}
SPOT_BASE: Final = {"SPX": 5800.0, "SPY": 580.0, "QQQ": 500.0, "NDX": 20000.0, "NDXP": 20000.0}
INSTRUMENTS: Final = ("ES", "MES", "NQ", "MNQ")
FUTURES_BASE: Final = {"ES": 5810.0, "MES": 5810.0, "NQ": 20050.0, "MNQ": 20050.0}
METHODS: Final[tuple[ConversionMethod, ...]] = ("offset", "ratio")
ES_INSTRUMENTS: Final[tuple[EsInstrument, ...]] = ("ES", "MES")
NQ_INSTRUMENTS: Final[tuple[NqInstrument, ...]] = ("NQ", "MNQ")
FIELDS: Final = (
    "symbol",
    "metric",
    "as_of_ns",
    "conversion",
    "band_half_width_pts",
    "strikes",
    "levels",
    "bands",
)

# ---------------------------------------------------------------- inputs


def snap(
    symbol: str,
    metric: Metric,
    as_of_ns: Instant,
    spot: float,
    strikes: tuple[float, ...],
    uid: int,
) -> Snapshot:
    """A Snapshot made unique by ``uid`` (in ``as_of_raw``)."""
    return Snapshot(
        symbol=symbol,
        metric=metric,
        view_id=VIEW_ID,
        as_of_ns=as_of_ns,
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


def make_bar(instrument: str, close_ns: Instant, c_t: Ticks | None, interval_s: int = 60) -> Bar:
    """A futures bar of the session's contract; ``c_t=None`` is a bar with no tick prices."""
    px = FUTURES_BASE[instrument] if c_t is None else c_t / 4
    return Bar(
        instrument, f"{instrument}H6", interval_s, close_ns - interval_s * S, close_ns,
        px, px, px, px, 1.0, c_t, c_t, c_t, c_t, "atlas",
    )  # fmt: skip


@dataclass(frozen=True, slots=True)
class Case:
    """One session's inputs, the converter parameters and the Decision_Times to evaluate."""

    symbols: tuple[str, ...]
    metrics: tuple[Metric, ...]
    params: LevelParams
    snapshots: tuple[Snapshot, ...]
    bars: tuple[BarItem, ...]
    times: tuple[Instant, ...]

    def bar_items(self) -> list[tuple[Bar, Instant]]:
        """Each bar with its availability per D8, computed without the code under test."""
        out: list[tuple[Bar, Instant]] = []
        for item in self.bars:
            if isinstance(item, Received):
                out.append((item.record, max(item.record.close_ns, item.received_ns)))
            else:
                out.append((item, item.close_ns))
        return out


# ---------------------------------------------------------------- oracle


def half_up_ticks(x: float) -> Ticks:
    """``x`` points as 0.25-point ticks, nearest with halves up, in exact arithmetic."""
    return math.floor(Fraction(x) * 4 + Fraction(1, 2))


def expected_method(symbol: str, levels: LevelsConfig) -> ConversionMethod:
    """SPX offset, QQQ ratio, SPY, NDX and NDXP as configured (Req 8.1-8.3)."""
    methods: dict[str, ConversionMethod] = {
        "SPX": "offset",
        "QQQ": "ratio",
        "SPY": levels.methods.SPY,
        "NDX": levels.methods.NDX,
        "NDXP": levels.methods.NDXP,
    }
    return methods[symbol]


def expected_pairing(case: Case, s: Snapshot, t: Instant) -> Pairing:
    """Req 8.4 and 8.10 for ``s`` in the view at ``t``."""
    family = FAMILY[s.symbol]
    instrument = case.params.es_instrument if family == "ES" else case.params.nq_instrument
    names: set[str] = set()
    if not (math.isfinite(s.spot) and s.spot > 0):
        names.add(s.symbol)
    closed = [
        b
        for b, available in case.bar_items()
        if b.instrument == instrument
        and b.interval_s == 60
        and available <= t
        and b.close_ns <= s.as_of_ns
    ]
    if not closed:
        return frozenset({*names, instrument})
    latest = max(closed, key=lambda b: b.close_ns)  # close times are unique per instrument
    c_t = latest.c_t
    gap_ns = case.params.levels.max_price_gap_s * S
    if c_t is None or c_t <= 0 or s.as_of_ns - latest.close_ns > gap_ns:
        return frozenset({*names, latest.contract})
    if names:
        return frozenset(names)
    futures = c_t / 4
    method = expected_method(s.symbol, case.params.levels)
    return Conversion(
        symbol=s.symbol,
        family=family,
        instrument=instrument,
        contract=latest.contract,
        method=method,
        factor=futures - s.spot if method == "offset" else futures / s.spot,
        futures_close=futures,
        spot=s.spot,
        as_of_ns=s.as_of_ns,
        paired_bar_close_ns=latest.close_ns,
    )


def _names(p: Pairing) -> frozenset[str]:
    return p if isinstance(p, frozenset) else frozenset()


def expected_convert(case: Case, view: HistoricalMarketView, s: Snapshot) -> Converted:
    """Req 8.1-8.4, 8.6-8.7 and 8.10 for a Map_State Snapshot ``s``."""
    own = expected_pairing(case, s, view.t)
    band_source: Pairing
    if FAMILY[s.symbol] == "ES" or s.symbol == "QQQ":
        band_source = own
    else:  # an NQ band scales by the QQQ Snapshot of the same metric (Req 8.7)
        qqq = view.map_state().get("QQQ", s.metric)
        band_source = (
            expected_pairing(case, qqq, view.t) if isinstance(qqq, Snapshot) else frozenset({"QQQ"})
        )
    missing = _names(own) | _names(band_source)
    if missing:
        return missing
    assert isinstance(own, Conversion)
    assert isinstance(band_source, Conversion)
    cfg = case.params.levels
    if own.family == "ES":
        h = cfg.es_half_width_pts
    else:
        h = cfg.qqq_half_width_usd * (band_source.futures_close / band_source.spot)
    raw = [k + own.factor if own.method == "offset" else k * own.factor for k in s.strikes]
    levels = tuple(half_up_ticks(x) for x in raw)
    return ConvertedMap(
        symbol=s.symbol,
        metric=s.metric,
        as_of_ns=s.as_of_ns,
        conversion=own,
        band_half_width_pts=h,
        strikes=s.strikes,
        levels=levels,
        bands=tuple((level / 4 - h, level / 4 + h) for level in levels),
    )


def check_missing(got: object, want: frozenset[str], where: str) -> None:
    assert isinstance(got, MissingPrice), f"{where}: got {got!r}, want a MissingPrice"
    assert got.symbol_or_contract in want, (
        f"{where}: got {got!r}, want a MissingPrice naming one of {sorted(want)}"
    )


def check_pairing(got: Conversion | MissingPrice, want: Pairing, where: str) -> None:
    if isinstance(want, frozenset):
        check_missing(got, want, where)
    else:
        assert got == want, f"{where}: got {got!r}, want {want!r}"


def check_converted(got: ConvertedMap | MissingPrice, want: Converted, where: str) -> None:
    if isinstance(want, frozenset):
        check_missing(got, want, where)
        return
    assert isinstance(got, ConvertedMap), f"{where}: got {got!r}, want converted levels"
    for name in FIELDS:
        g, w = getattr(got, name), getattr(want, name)
        assert g == w, f"{where}: {name} is {g!r}, want {w!r}"


# ---------------------------------------------------------------- generators

BAR_TIME = st.integers(T0 - 20 * MINUTE, T0 + 10 * MINUTE)
SECOND_GRID = st.integers(-15 * 60, 20 * 60).map(lambda k: T0 + k * S)
SNAPSHOT_TIME = st.integers(T0 - 15 * MINUTE, T0 + 20 * MINUTE)
DECISION_TIME = st.integers(T0 - 15 * MINUTE, T0 + 25 * MINUTE)
KEEP = st.integers(0, 5).map(bool)  # a minute bar is present 5 times in 6
BAD_SPOTS = (0.0, -0.0, -1.0, -5800.0, math.nan, math.inf, -math.inf)


def weighted[T](*options: tuple[int, st.SearchStrategy[T]]) -> st.SearchStrategy[T]:
    """Pick a strategy in proportion to its weight (``st.one_of`` drops repeated branches)."""
    pool = [strategy for weight, strategy in options for _ in range(weight)]
    return st.sampled_from(pool).flatmap(lambda strategy: strategy)


# None: a plain historical bar. Otherwise the receipt delay, which may be negative.
DELAYS: st.SearchStrategy[int | None] = weighted((3, st.none()), (1, st.integers(-5 * S, 180 * S)))


def close_ticks(instrument: str) -> st.SearchStrategy[Ticks | None]:
    """Mostly realistic tick closes; sometimes anywhere positive, 0, negative or absent."""
    base = int(FUTURES_BASE[instrument] * 4)
    near: st.SearchStrategy[Ticks | None] = st.integers(base - 800, base + 800)
    wide: st.SearchStrategy[Ticks | None] = st.integers(1, 400_000)
    bad: st.SearchStrategy[Ticks | None] = st.sampled_from((0, -1, -base, None))
    return weighted((12, near), (2, wide), (1, bad))


def spots(symbol: str) -> st.SearchStrategy[float]:
    """Spots on a 1/8 grid, near the symbol's price, anywhere positive, or bad."""
    base = SPOT_BASE[symbol]
    eighths = st.integers(int(base * 8 * 0.98), int(base * 8 * 1.02)).map(lambda k: k / 8)
    near = st.floats(base * 0.95, base * 1.05)
    return weighted(
        (6, eighths), (6, near), (2, st.floats(0.01, 1.0e5)), (1, st.sampled_from(BAD_SPOTS))
    )


def strikes_near(symbol: str) -> st.SearchStrategy[float]:
    """Strikes on a 5-point grid, on a 1/8 grid, or any positive value."""
    base = SPOT_BASE[symbol]
    return st.one_of(
        st.integers(int(base * 0.9 / 5), int(base * 1.1 / 5)).map(lambda k: k * 5.0),
        st.integers(int(base * 8 * 0.9), int(base * 8 * 1.1)).map(lambda k: k / 8),
        st.floats(0.01, 1.0e5),
    )


@st.composite
def level_params(draw: st.DrawFn) -> LevelParams:
    levels = LevelsConfig(
        methods=LevelMethodsConfig(
            SPY=draw(st.sampled_from(METHODS)),
            NDX=draw(st.sampled_from(METHODS)),
            NDXP=draw(st.sampled_from(METHODS)),
        ),
        es_half_width_pts=draw(st.floats(ES_HALF_WIDTH_MIN_PTS, ES_HALF_WIDTH_MAX_PTS)),
        qqq_half_width_usd=draw(st.floats(QQQ_HALF_WIDTH_MIN_USD, QQQ_HALF_WIDTH_MAX_USD)),
        max_price_gap_s=draw(st.integers(MAX_PRICE_GAP_MIN_S, MAX_PRICE_GAP_MAX_S)),
    )
    es = draw(st.sampled_from(ES_INSTRUMENTS))
    nq = draw(st.sampled_from(NQ_INSTRUMENTS))
    return LevelParams(levels, es, nq)


@st.composite
def cases(draw: st.DrawFn) -> Case:
    symbols = tuple(draw(st.lists(st.sampled_from(SYMBOLS), min_size=1, unique=True)))
    metrics = tuple(draw(st.lists(st.sampled_from(METRICS), min_size=1, unique=True)))
    params = draw(level_params())
    gap_ns = params.levels.max_price_gap_s * S

    bars: list[BarItem] = []
    for instrument in INSTRUMENTS:
        # A run of minute bars with holes, plus a few off-grid closes; unique close times.
        first, count = draw(st.integers(-20, 5)), draw(st.integers(0, 25))
        closes = {T0 + (first + i) * MINUTE for i in range(count) if draw(KEEP)}
        closes.update(draw(st.lists(BAR_TIME, max_size=2)))
        for close in sorted(closes):
            b = make_bar(instrument, close, draw(close_ticks(instrument)))
            delay = draw(DELAYS)
            bars.append(b if delay is None else Received(b, close + delay))
        for close in draw(st.lists(BAR_TIME, max_size=2)):  # 5-minute bars: never paired
            bars.append(make_bar(instrument, close, draw(close_ticks(instrument)), 300))
    partial = Case(symbols, metrics, params, (), tuple(bars), ())
    items = partial.bar_items()

    # asOf values sit on the paired instrument's bar closes, 30 s after them, or at the gap.
    offsets = (-1, 0, 1, 30 * S, gap_ns - 1, gap_ns, gap_ns + 1)
    as_of_by_family: dict[LevelFamily, st.SearchStrategy[Instant]] = {}
    paired: tuple[tuple[LevelFamily, str], ...] = (
        ("ES", params.es_instrument),
        ("NQ", params.nq_instrument),
    )
    for family, instrument in paired:
        anchors = sorted(
            {
                b.close_ns + d
                for b, _ in items
                if (b.instrument, b.interval_s) == (instrument, 60)
                for d in offsets
            }
        )
        loose = st.one_of(SECOND_GRID, SNAPSHOT_TIME)
        as_of_by_family[family] = (
            weighted((3, st.sampled_from(anchors)), (1, loose)) if anchors else loose
        )
    which = st.sampled_from((*symbols, *symbols, *symbols, *SYMBOLS))  # mostly configured
    specs = draw(st.lists(st.tuples(which, st.sampled_from(METRICS)), max_size=16))
    snapshots = tuple(
        snap(
            symbol,
            metric,
            draw(as_of_by_family[FAMILY[symbol]]),
            draw(spots(symbol)),
            tuple(draw(st.lists(strikes_near(symbol), max_size=6))),
            uid,
        )
        for uid, (symbol, metric) in enumerate(specs)
    )

    # Decision_Times at or just after an asOf or a bar's availability, or anywhere.
    t_anchors = sorted(
        {s.as_of_ns + d for s in snapshots for d in (0, 1, 45 * S)}
        | {available + d for _, available in items for d in (-1, 0)}
    )
    t_any = (
        weighted((3, st.sampled_from(t_anchors)), (1, DECISION_TIME))
        if t_anchors
        else DECISION_TIME
    )
    times = tuple(draw(st.lists(t_any, min_size=1, max_size=4)))
    return Case(symbols, metrics, params, snapshots, tuple(bars), times)


# ---------------------------------------------------------------- explicit examples

_HALF_TICKS_AND_GAP = Case(  # defaults: MES, 120 s gap, SPY ratio
    symbols=("SPX", "SPY"),
    metrics=METRICS,
    params=LevelParams(),
    snapshots=(
        snap("SPX", "gamma", T0, 5800.25, (5800.875, 5780.0, 5825.0), 0),  # 5811.125: up
        snap("SPX", "vanna", T0 + 120 * S, 5800.25, (5800.0,), 1),  # exactly the gap: priced
        snap("SPY", "gamma", T0 + 120 * S + 1, 581.0, (580.0,), 2),  # 1 ns past it: stale
        snap("SPX", "gamma", T0 + 300 * S, 5801.0, (5800.0,), 3),  # no fresh bar: no reuse
        snap("SPY", "vanna", T0, 0.0, (580.0,), 4),  # zero spot
    ),
    bars=(
        make_bar("MES", T0 - MINUTE, 23220),
        make_bar("MES", T0, 23242),  # 5810.5
        make_bar("ES", T0 + MINUTE, 23600),  # the other ES-family instrument
        make_bar("MES", T0 + MINUTE, 24000, 300),  # a 5-minute bar
    ),
    times=(T0 + 300 * S, T0 + 121 * S, T0),
)
_NQ_BANDS = Case(  # NQ bars; NDX offset; QQQ half-width $0.25
    symbols=("QQQ", "NDX", "NDXP"),
    metrics=METRICS,
    params=LevelParams(
        LevelsConfig(methods=LevelMethodsConfig(NDX="offset"), qqq_half_width_usd=0.25),
        "MES",
        "NQ",
    ),
    snapshots=(
        snap("QQQ", "gamma", T0, 500.0, (500.0, 505.5), 0),
        snap("QQQ", "vanna", T0 - 30 * S, 400.0, (400.0,), 1),  # pairs with T0 - 60 s
        snap("NDX", "gamma", T0 + 75 * S, 16000.0, (16000.0, 16100.0), 2),
        snap("NDX", "vanna", T0, 16000.0, (16000.0,), 3),  # band from QQQ vanna
        snap("NDXP", "gamma", T0 - 40 * S, 16000.0, (16000.0,), 4),  # no QQQ at T0 - 35 s
    ),
    bars=(
        make_bar("NQ", T0 - MINUTE, 79960),  # 19990
        make_bar("NQ", T0, 80000),  # 20000
        make_bar("MNQ", T0, 84000),  # not the configured instrument
        Received(make_bar("NQ", T0 + MINUTE, 80040), T0 + 10 * MINUTE),  # late arrival
    ),
    times=(T0 + 90 * S, T0 - 35 * S, T0 + 10 * MINUTE),
)


# ---------------------------------------------------------------- property


# Feature: skylit-futures-strategy-engine, Property 24: Conversion formulas and pairing
@example(case=_HALF_TICKS_AND_GAP)
@example(case=_NQ_BANDS)
@given(case=cases())
def test_conversion_formulas_and_pairing(case: Case) -> None:
    inputs = HistoricalInputs(
        symbols=case.symbols,
        view_id=VIEW_ID,
        metrics=case.metrics,
        snapshots=case.snapshots,
        bars=case.bars,
    )
    for t in case.times:
        view = inputs.view(t)
        # Pairing for every Snapshot visible by asOf, configured or not (Req 8.4, 8.10).
        for s in case.snapshots:
            if s.as_of_ns <= t:
                where = f"t={t} pair({s.symbol} {s.metric} {s.as_of_raw})"
                check_pairing(pair(s, view, case.params), expected_pairing(case, s, t), where)

        # Levels and bands for every Map_State entry (Req 8.1-8.3, 8.6, 8.7, 8.10).
        state = view.map_state()
        out = convert_map(view, case.params)
        assert list(out) == list(state)
        for key, entry in state.entries.items():
            where = f"t={t} {key[0]} {key[1]}"
            if not isinstance(entry, Snapshot):  # no Snapshot: its spot is absent
                assert out[key] == MissingPrice(key[0]), f"{where}: got {out[key]!r}"
                continue
            want = expected_convert(case, view, entry)
            check_converted(convert(entry, view, case.params), want, f"{where} convert")
            check_converted(out[key], want, f"{where} convert_map")
