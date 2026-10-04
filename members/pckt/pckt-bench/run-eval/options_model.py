"""Simulated 0DTE option pricing for the headline score. Black-Scholes, r = q = 0, calendar-year time, IV = VIX/100 * k.
Known simplification: 0DTE IV is not VIX (it is usually higher late in the day and skews by strike). Absolute dollars are NOT real fills;
the model is identical for every agent so rankings are comparable. Adjust k / spread in rules via the CLI flags of score.py."""
from __future__ import annotations
import math

YEAR = 365 * 24 * 3600
IV_FLOOR = 0.10


def _n(x: float) -> float: return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs(spot: float, strike: float, secs: float, iv: float, call: bool) -> tuple[float, float]:
    """(price, delta). At/after expiry: intrinsic."""
    if secs <= 0:
        v = max(spot - strike, 0) if call else max(strike - spot, 0)
        return v, (1.0 if v > 0 else 0.0) * (1 if call else -1)
    s = iv * math.sqrt(secs / YEAR); d1 = (math.log(spot / strike) + 0.5 * s * s) / s; d2 = d1 - s
    return ((spot * _n(d1) - strike * _n(d2), _n(d1)) if call else (strike * _n(-d2) - spot * _n(-d1), _n(d1) - 1))


def iv_from_vix(vix: float, k: float = 1.0) -> float: return max(IV_FLOOR, vix / 100.0 * k)


def half_spread(mid: float, floor: float = 0.03, pct: float = 0.03) -> float: return max(floor, pct * mid)


def pick_strike(spot: float, secs: float, iv: float, call: bool, target_delta: float = 0.30, step: float = 1.0) -> float:
    """Strike on a `step` grid whose |delta| is closest to the target."""
    c = round(spot / step) * step
    grid = [c + i * step for i in range(-40, 41)]
    return min(grid, key=lambda k: abs(abs(bs(spot, k, secs, iv, call)[1]) - target_delta))


def quote(spot: float, strike: float, secs: float, iv: float, call: bool, floor: float = 0.03, pct: float = 0.03) -> dict:
    mid, delta = bs(spot, strike, secs, iv, call); h = half_spread(mid, floor, pct)
    return {"mid": mid, "bid": max(0.0, mid - h), "ask": mid + h, "delta": delta}
