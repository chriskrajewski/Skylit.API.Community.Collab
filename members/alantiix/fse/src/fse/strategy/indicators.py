"""Incremental indicators for the rules language. No look-ahead: each update sees one bar."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Literal, assert_never

from fse.strategy.spec import IndicatorSpec, StrategySpec

Source = Literal["close", "hlc3", "ohlc4"]


def price_of(source: Source, o: float, h: float, low: float, c: float) -> float:
    """The moving-average input."""
    if source == "hlc3":
        return (h + low + c) / 3
    if source == "ohlc4":
        return (o + h + low + c) / 4
    return c


@dataclass
class _Ema:
    length: int
    source: Source
    value: float | None = None
    seed: list[float] = field(default_factory=list)

    def update(self, price: float) -> float | None:
        if self.value is None:
            self.seed.append(price)
            if len(self.seed) < self.length:
                return None
            self.value = sum(self.seed) / self.length
            return self.value
        alpha = 2 / (self.length + 1)
        self.value = alpha * price + (1 - alpha) * self.value
        return self.value


@dataclass
class _Dema:
    length: int
    source: Source
    ema: _Ema = field(init=False)
    ema_of: _Ema = field(init=False)

    def __post_init__(self) -> None:
        self.ema = _Ema(self.length, self.source)
        self.ema_of = _Ema(self.length, self.source)

    def update(self, price: float) -> float | None:
        first = self.ema.update(price)
        if first is None:
            return None
        second = self.ema_of.update(first)
        if second is None:
            return None
        return 2 * first - second


@dataclass
class _Sma:
    length: int
    source: Source
    window: deque[float] = field(init=False)

    def __post_init__(self) -> None:
        self.window = deque(maxlen=self.length)

    def update(self, price: float) -> float | None:
        self.window.append(price)
        if len(self.window) < self.length:
            return None
        return sum(self.window) / self.length


@dataclass
class _Atr:
    length: int
    value: float | None = None
    prev_close: float | None = None
    seed: list[float] = field(default_factory=list)

    def update(self, h: float, low: float, c: float) -> float | None:
        prev = self.prev_close
        tr = h - low if prev is None else max(h - low, abs(h - prev), abs(low - prev))
        self.prev_close = c
        if self.value is None:
            self.seed.append(tr)
            if len(self.seed) < self.length:
                return None
            self.value = sum(self.seed) / self.length
            return self.value
        self.value = (self.value * (self.length - 1) + tr) / self.length
        return self.value


@dataclass
class _Supertrend:
    length: int
    multiplier: float
    atr: _Atr = field(init=False)
    final_up: float | None = None
    final_dn: float | None = None
    trend: int = 1

    def __post_init__(self) -> None:
        self.atr = _Atr(self.length)

    def update(self, h: float, low: float, c: float) -> tuple[float | None, int | None]:
        """``(line, direction)``. Direction is +1 up, -1 down, ``None`` until warm."""
        prev_close = self.atr.prev_close
        atr = self.atr.update(h, low, c)
        if atr is None:
            return None, None
        hl2 = (h + low) / 2
        basic_up = hl2 - self.multiplier * atr
        basic_dn = hl2 + self.multiplier * atr
        prev_up, prev_dn = self.final_up, self.final_dn
        if prev_up is None or prev_dn is None or prev_close is None:
            self.final_up = basic_up
            self.final_dn = basic_dn
            self.trend = 1 if c >= hl2 else -1
        else:
            self.final_up = max(basic_up, prev_up) if prev_close > prev_up else basic_up
            self.final_dn = min(basic_dn, prev_dn) if prev_close < prev_dn else basic_dn
            if self.trend == -1 and c > prev_dn:
                self.trend = 1
            elif self.trend == 1 and c < prev_up:
                self.trend = -1
        line = self.final_up if self.trend == 1 else self.final_dn
        return line, self.trend


@dataclass
class _SessionVwap:
    """Session VWAP. ``k`` set means also publish upper and lower sigma bands."""

    k: float | None = None
    session: object | None = None
    pv: float = 0.0
    p2: float = 0.0
    vol: float = 0.0

    def reset(self, session: object) -> None:
        self.session = session
        self.pv = 0.0
        self.p2 = 0.0
        self.vol = 0.0

    def update(
        self, h: float, low: float, c: float, v: float, session: object
    ) -> tuple[float, float | None, float | None]:
        if session != self.session:
            self.reset(session)
        typical = (h + low + c) / 3
        volume = v if v > 0 else 1.0
        self.pv += typical * volume
        self.p2 += typical * typical * volume
        self.vol += volume
        mid = self.pv / self.vol
        if self.k is None:
            return mid, None, None
        var = max(self.p2 / self.vol - mid * mid, 0.0)
        sigma = var**0.5
        return mid, mid + self.k * sigma, mid - self.k * sigma


class IndicatorEngine:
    """The spec's indicators, updated one completed bar at a time."""

    def __init__(self, spec: StrategySpec) -> None:
        self._specs = list(spec.indicators)
        self._emas: dict[str, _Ema] = {}
        self._demas: dict[str, _Dema] = {}
        self._smas: dict[str, _Sma] = {}
        self._atrs: dict[str, _Atr] = {}
        self._sts: dict[str, _Supertrend] = {}
        self._vwaps: dict[str, _SessionVwap] = {}
        for item in self._specs:
            self._bind(item)
        self.values: dict[str, float | None] = {}

    def _bind(self, item: IndicatorSpec) -> None:
        if item.kind == "ema":
            assert item.length is not None
            self._emas[item.id] = _Ema(item.length, item.source)
        elif item.kind == "dema":
            assert item.length is not None
            self._demas[item.id] = _Dema(item.length, item.source)
        elif item.kind == "sma":
            assert item.length is not None
            self._smas[item.id] = _Sma(item.length, item.source)
        elif item.kind == "atr":
            assert item.length is not None
            self._atrs[item.id] = _Atr(item.length)
        elif item.kind == "supertrend":
            assert item.length is not None
            assert item.multiplier is not None
            self._sts[item.id] = _Supertrend(item.length, item.multiplier)
        elif item.kind == "vwap":
            self._vwaps[item.id] = _SessionVwap()
        elif item.kind == "sigma_bands":
            assert item.k is not None
            self._vwaps[item.id] = _SessionVwap(k=item.k)
        else:
            assert_never(item.kind)

    def update(
        self,
        o: float,
        h: float,
        low: float,
        c: float,
        v: float,
        session: object,
    ) -> dict[str, float | None]:
        """New values keyed by id. Sigma bands also set ``id.upper`` and ``id.lower``."""
        values: dict[str, float | None] = {}
        for item in self._specs:
            if item.kind == "ema":
                values[item.id] = self._emas[item.id].update(price_of(item.source, o, h, low, c))
            elif item.kind == "dema":
                values[item.id] = self._demas[item.id].update(price_of(item.source, o, h, low, c))
            elif item.kind == "sma":
                values[item.id] = self._smas[item.id].update(price_of(item.source, o, h, low, c))
            elif item.kind == "atr":
                values[item.id] = self._atrs[item.id].update(h, low, c)
            elif item.kind == "supertrend":
                line, trend = self._sts[item.id].update(h, low, c)
                values[item.id] = line
                values[f"{item.id}.dir"] = None if trend is None else float(trend)
            elif item.kind == "vwap":
                mid, _upper, _lower = self._vwaps[item.id].update(h, low, c, v, session)
                values[item.id] = mid
            elif item.kind == "sigma_bands":
                mid, upper, lower = self._vwaps[item.id].update(h, low, c, v, session)
                values[item.id] = mid
                values[f"{item.id}.upper"] = upper
                values[f"{item.id}.lower"] = lower
            else:
                assert_never(item.kind)
        self.values = values
        return values
