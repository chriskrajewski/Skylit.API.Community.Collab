# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 v01
"""Plain data types. No I/O here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class Side(StrEnum):
    """Aggressor side of a print (ProjectX TradeLogType: 0 Buy, 1 Sell)."""

    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True, slots=True)
class TapePrint:
    """One trade print from the market hub."""

    root: str  # "ES", "NQ", ...
    contract_id: str  # "CON.F.US.EP.Z26"
    price: float
    size: int
    side: Side
    ts_us: int  # exchange timestamp, microseconds since the Unix epoch (UTC)

    @property
    def timestamp(self) -> datetime:
        return datetime.fromtimestamp(self.ts_us / 1_000_000, tz=UTC)


@dataclass(frozen=True, slots=True)
class BigTrade:
    """One aggregated sweep that met its threshold."""

    root: str
    contract_id: str
    side: Side
    size: int
    first_price: float
    last_price: float
    low: float
    high: float
    avg_price: float
    prints: int
    start_ts_us: int
    end_ts_us: int
    mode: str  # "final" or "first_qualify"

    @property
    def start(self) -> datetime:
        return datetime.fromtimestamp(self.start_ts_us / 1_000_000, tz=UTC)

    @property
    def end(self) -> datetime:
        return datetime.fromtimestamp(self.end_ts_us / 1_000_000, tz=UTC)
