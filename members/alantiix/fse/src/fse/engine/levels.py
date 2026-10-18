"""The Level_Converter: strikes to ES and NQ levels with Deflection_Bands (design §8, Req 8).

- **Families.** SPX and SPY strikes become ES levels (used for ES and MES);
  QQQ, NDX and NDXP strikes become NQ levels (used for NQ and MNQ).
- **Methods** (Req 8.1-8.3). SPX uses the offset method, ``strike + (futures -
  spot)``; QQQ uses the ratio method, ``strike x (futures / spot)``; SPY, NDX
  and NDXP use the method in ``levels.methods`` (default ratio).
- **Pairing** (Req 5.4, 8.4). A Snapshot's spot is paired with the close of the
  latest 1-minute bar of the configured instrument (``data.instruments``) with
  ``close_ns <= asOf``. The MarketView holds one session's bars, so that is
  the session's recorded contract. The close is the bar's tick close, as for
  Futures_Price.
- **Rounding** (Req 8.5). :func:`round_to_tick` rounds to the nearest 0.25
  point, halves up: the rule of ``fse.projectx.models.price_to_ticks``, but
  exact for every finite float.
- **Bands** (Req 8.6-8.7). ES: ``L ± es_half_width_pts``. NQ: ``L ±
  qqq_half_width_usd x (NQ / QQQ spot)``, unrounded, from the QQQ Snapshot of
  the same metric and its own NQ pairing.
- **Missing prices** (Req 8.10). A spot that is not a positive finite number,
  no paired bar, a paired close at or below 0, or a paired bar that closed more
  than ``max_price_gap_s`` before ``asOf`` gives ``MissingPrice`` naming the
  symbol, the instrument (no bar) or the bar's contract. Nothing is cached, so
  no earlier offset or ratio is ever reused.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from fractions import Fraction
from types import MappingProxyType
from typing import Final, Literal

from fse.config.schema.data import DataConfig, EsInstrument, NqInstrument
from fse.config.schema.levels import LevelsConfig
from fse.engine.types import (
    ConversionMethod,
    Metric,
    MissingPrice,
    Snapshot,
    Ticks,
    Unavailable,
)
from fse.pit.protocols import MarketView, SymMetric
from fse.timekit import NS_PER_SECOND, Instant

__all__ = [
    "ES_SYMBOLS",
    "NQ_SYMBOLS",
    "TICKS_PER_POINT",
    "Conversion",
    "ConvertedMap",
    "LevelFamily",
    "LevelParams",
    "band",
    "conversion_method",
    "convert",
    "convert_map",
    "level_family",
    "pair",
    "round_to_tick",
    "ticks_to_points",
]

type LevelFamily = Literal["ES", "NQ"]
"""The futures family a symbol's levels belong to."""

TICKS_PER_POINT: Final = 4
"""ES, MES, NQ and MNQ all trade in 0.25-point ticks."""

ES_SYMBOLS: Final[frozenset[str]] = frozenset({"SPX", "SPY"})
NQ_SYMBOLS: Final[frozenset[str]] = frozenset({"QQQ", "NDX", "NDXP"})
NQ_BAND_SYMBOL: Final = "QQQ"
"""NQ Deflection_Bands scale the QQQ half-width by this symbol's ratio (Req 8.7)."""

_FIXED_METHODS: Final[Mapping[str, ConversionMethod]] = MappingProxyType(
    {"SPX": "offset", "QQQ": "ratio"}
)
_ES_INSTRUMENTS: Final = frozenset({"ES", "MES"})
_NQ_INSTRUMENTS: Final = frozenset({"NQ", "MNQ"})

# Every float of at least this magnitude is a multiple of 0.25 (its ulp is >= 0.25).
_ON_GRID_ABOVE: Final = 2.0**50
_HALF: Final = Fraction(1, 2)


# ---------------------------------------------------------------- ticks and bands


def round_to_tick(x: float) -> Ticks:
    """``x`` points rounded to the nearest 0.25-point tick, a half rounded up (Req 8.5).

    ``floor(4x + 0.5)``, computed exactly: ``x * 4`` is exact in binary, and
    the half is compared without a rounded sum. Raises ``ValueError`` for a
    non-finite ``x``.
    """
    if not math.isfinite(x):
        raise ValueError(f"a level must be a finite number, got {x!r}")
    if abs(x) >= _ON_GRID_ABOVE:
        return math.floor(Fraction(x) * TICKS_PER_POINT + _HALF)
    y = x * TICKS_PER_POINT
    f = math.floor(y)
    # |f| < 2**52 here, so f + 0.5 is exact.
    return f + 1 if y >= f + 0.5 else f


def ticks_to_points(level: Ticks) -> float:
    """A tick price in points."""
    return level / TICKS_PER_POINT


def band(level: Ticks, half_width_pts: float) -> tuple[float, float]:
    """The Deflection_Band ``(L - h, L + h)`` in points around a tick level ``L``."""
    if not math.isfinite(half_width_pts) or half_width_pts < 0:
        raise ValueError(f"a band half-width must be finite and at least 0, got {half_width_pts}")
    pts = ticks_to_points(level)
    return (pts - half_width_pts, pts + half_width_pts)


# ---------------------------------------------------------------- parameters


@dataclass(frozen=True, slots=True)
class LevelParams:
    """The ``levels`` section plus the instrument whose bars price each family."""

    levels: LevelsConfig = field(default_factory=LevelsConfig)
    es_instrument: EsInstrument = "MES"
    nq_instrument: NqInstrument = "MNQ"

    def __post_init__(self) -> None:
        if self.es_instrument not in _ES_INSTRUMENTS:
            raise ValueError(f"es_instrument must be ES or MES, got {self.es_instrument!r}")
        if self.nq_instrument not in _NQ_INSTRUMENTS:
            raise ValueError(f"nq_instrument must be NQ or MNQ, got {self.nq_instrument!r}")

    @classmethod
    def from_config(cls, levels: LevelsConfig, data: DataConfig) -> LevelParams:
        """Parameters from the ``levels`` and ``data`` sections."""
        return cls(levels, data.instruments.es_levels, data.instruments.nq_levels)

    def instrument(self, family: LevelFamily) -> str:
        """The instrument whose 1-minute bars are paired with a family's Snapshots."""
        return self.es_instrument if family == "ES" else self.nq_instrument

    @property
    def max_price_gap_ns(self) -> int:
        return self.levels.max_price_gap_s * NS_PER_SECOND


def level_family(symbol: str) -> LevelFamily:
    """``ES`` for SPX and SPY, ``NQ`` for QQQ, NDX and NDXP; ``ValueError`` otherwise."""
    if symbol in ES_SYMBOLS:
        return "ES"
    if symbol in NQ_SYMBOLS:
        return "NQ"
    raise ValueError(f"the Level_Converter has no futures family for symbol {symbol!r}")


def conversion_method(symbol: str, p: LevelParams) -> ConversionMethod:
    """The method for ``symbol``: fixed for SPX and QQQ, configured otherwise (Req 8.1-8.3)."""
    level_family(symbol)
    fixed = _FIXED_METHODS.get(symbol)
    if fixed is not None:
        return fixed
    methods = p.levels.methods
    configured: dict[str, ConversionMethod] = {
        "SPY": methods.SPY,
        "NDX": methods.NDX,
        "NDXP": methods.NDXP,
    }
    return configured[symbol]


# ---------------------------------------------------------------- conversion


@dataclass(frozen=True, slots=True)
class Conversion:
    """One Snapshot's offset or ratio, with the prices it came from.

    ``factor`` is the offset in points (offset method) or the ratio (ratio
    method). ``futures_close`` is the paired bar's tick close in points.
    """

    symbol: str
    family: LevelFamily
    instrument: str
    contract: str
    method: ConversionMethod
    factor: float
    futures_close: float
    spot: float
    as_of_ns: Instant
    paired_bar_close_ns: Instant

    def apply(self, strike: float) -> float:
        """The unrounded futures level of ``strike``."""
        return strike + self.factor if self.method == "offset" else strike * self.factor

    def level(self, strike: float) -> Ticks:
        """The rounded futures level of ``strike``, in ticks."""
        return round_to_tick(self.apply(strike))

    def to_strike(self, level_pts: float) -> float:
        """A futures level back in strike units: ``L - offset`` or ``L / ratio`` (Req 8.8)."""
        return level_pts - self.factor if self.method == "offset" else level_pts / self.factor


@dataclass(frozen=True, slots=True)
class ConvertedMap:
    """One Snapshot's strikes as rounded futures levels with their Deflection_Bands.

    ``strikes``, ``levels`` and ``bands`` are parallel and in the Snapshot's
    strike order. ``bands`` are ``(lo, hi)`` in points.
    """

    symbol: str
    metric: Metric
    as_of_ns: Instant
    conversion: Conversion
    band_half_width_pts: float
    strikes: tuple[float, ...]
    levels: tuple[Ticks, ...]
    bands: tuple[tuple[float, float], ...]
    _index: Mapping[float, int] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not len(self.strikes) == len(self.levels) == len(self.bands):
            raise ValueError("ConvertedMap strikes, levels and bands must be parallel")
        index = MappingProxyType({s: i for i, s in reversed(list(enumerate(self.strikes)))})
        object.__setattr__(self, "_index", index)

    @property
    def family(self) -> LevelFamily:
        return self.conversion.family

    def level_of(self, strike: float) -> Ticks:
        """The rounded level of one of the Snapshot's strikes; ``KeyError`` for another."""
        return self.levels[self._position(strike)]

    def band_of(self, strike: float) -> tuple[float, float]:
        """The Deflection_Band of one of the Snapshot's strikes; ``KeyError`` for another."""
        return self.bands[self._position(strike)]

    def _position(self, strike: float) -> int:
        i = self._index.get(strike)
        if i is None:
            raise KeyError(f"{strike} is not a strike of the {self.symbol} {self.metric} Snapshot")
        return i


def pair(s: Snapshot, view: MarketView, p: LevelParams) -> Conversion | MissingPrice:
    """``s``'s spot paired with the latest futures bar that closed at or before its ``asOf``.

    Raises ``ValueError`` for a Snapshot with ``asOf`` after the view's ``t``
    or a symbol with no futures family.
    """
    family = level_family(s.symbol)
    if s.as_of_ns > view.t:
        raise ValueError(
            f"the {s.symbol} {s.metric} Snapshot has asOf {s.as_of_ns}, after t {view.t}"
        )
    if not (math.isfinite(s.spot) and s.spot > 0):
        return MissingPrice(s.symbol)
    instrument = p.instrument(family)
    bar = view.last_bar_closed_at_or_before(instrument, s.as_of_ns)
    if bar is None:
        return MissingPrice(instrument)
    if bar.c_t is None or bar.c_t <= 0 or s.as_of_ns - bar.close_ns > p.max_price_gap_ns:
        return MissingPrice(bar.contract)
    futures = ticks_to_points(bar.c_t)
    method = conversion_method(s.symbol, p)
    factor = futures - s.spot if method == "offset" else futures / s.spot
    if not math.isfinite(factor):
        return MissingPrice(s.symbol)
    return Conversion(
        symbol=s.symbol,
        family=family,
        instrument=instrument,
        contract=bar.contract,
        method=method,
        factor=factor,
        futures_close=futures,
        spot=s.spot,
        as_of_ns=s.as_of_ns,
        paired_bar_close_ns=bar.close_ns,
    )


def _band_pairing(
    qqq: Snapshot | Unavailable, view: MarketView, p: LevelParams
) -> Conversion | MissingPrice:
    """The QQQ pairing an NQ band scales by; the QQQ spot is absent without a Snapshot."""
    if isinstance(qqq, Unavailable):
        return MissingPrice(NQ_BAND_SYMBOL)
    return pair(qqq, view, p)


def _build(
    s: Snapshot, conv: Conversion, band_conv: Conversion | MissingPrice, p: LevelParams
) -> ConvertedMap | MissingPrice:
    if conv.family == "ES":
        half_width = p.levels.es_half_width_pts
    elif isinstance(band_conv, MissingPrice):
        return band_conv
    else:
        half_width = p.levels.qqq_half_width_usd * band_conv.factor
    levels = tuple(conv.level(strike) for strike in s.strikes)
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


def convert(s: Snapshot, view: MarketView, p: LevelParams) -> ConvertedMap | MissingPrice:
    """``s``'s strikes as futures levels with bands, or the price that is missing.

    An NQ band reads the QQQ Snapshot of ``s``'s metric from the view's
    Map_State (``s`` itself for QQQ).
    """
    conv = pair(s, view, p)
    if isinstance(conv, MissingPrice):
        return conv
    if conv.family == "ES" or s.symbol == NQ_BAND_SYMBOL:
        return _build(s, conv, conv, p)
    qqq = view.map_state().get(NQ_BAND_SYMBOL, s.metric)
    return _build(s, conv, _band_pairing(qqq, view, p), p)


def convert_map(
    view: MarketView, p: LevelParams
) -> Mapping[SymMetric, ConvertedMap | MissingPrice]:
    """Every Map_State entry converted, in Map_State order (``convert`` per Snapshot).

    An entry with no Snapshot is ``MissingPrice`` naming its symbol (its spot
    is absent). Each Snapshot is paired with a futures bar once.
    """
    state = view.map_state()
    pairings: dict[SymMetric, Conversion | MissingPrice] = {}

    def pairing(key: SymMetric) -> Conversion | MissingPrice:
        found = pairings.get(key)
        if found is None:
            found = pairings[key] = _band_pairing(state.get(*key), view, p)
        return found

    out: dict[SymMetric, ConvertedMap | MissingPrice] = {}
    for key, entry in state.entries.items():
        symbol, metric = key
        if not isinstance(entry, Snapshot):
            level_family(symbol)
            out[key] = MissingPrice(symbol)
            continue
        conv = pairing(key)
        if isinstance(conv, MissingPrice):
            out[key] = conv
        elif conv.family == "ES":
            out[key] = _build(entry, conv, conv, p)
        else:
            out[key] = _build(entry, conv, pairing((NQ_BAND_SYMBOL, metric)), p)
    return MappingProxyType(out)
