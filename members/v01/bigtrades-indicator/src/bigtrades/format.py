# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 v01
"""Human-readable alert text."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from .models import BigTrade


def fmt_price(value: float) -> str:
    """At least two decimals, more only when the price needs them (e.g. 0.005 ticks)."""
    text = f"{value:.6f}".rstrip("0")
    whole, _, frac = text.partition(".")
    return f"{whole}.{frac.ljust(2, '0')}"


def _decimals(value: float) -> int:
    return len(fmt_price(value).partition(".")[2])


def format_big_trade(trade: BigTrade, tz: str = "UTC") -> str:
    """``ES big SELL 501 @ 7830.00→7829.50 | avg fill 7829.81 | 37 prints | 14:32:05.123 UTC``."""
    if trade.first_price == trade.last_price:
        price = fmt_price(trade.first_price)
    else:
        price = f"{fmt_price(trade.first_price)}→{fmt_price(trade.last_price)}"
    decimals = max(_decimals(trade.first_price), _decimals(trade.last_price))
    zone = ZoneInfo(tz)
    when = trade.end.astimezone(zone)
    stamp = when.strftime("%H:%M:%S.") + f"{when.microsecond // 1000:03d} {when.tzname()}"
    prints = f"{trade.prints} print" + ("" if trade.prints == 1 else "s")
    parts = [
        f"{trade.root} big {trade.side.value} {trade.size:,} @ {price}",
        f"avg fill {trade.avg_price:.{decimals}f}",
        prints,
        stamp,
    ]
    text = " | ".join(parts)
    if trade.mode == "first_qualify":
        text += " | first qualify (may still be filling)"
    return text
