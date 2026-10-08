# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 v01
"""Parse ProjectX ``GatewayTrade`` hub messages into TapePrint objects.

The market hub invokes ``GatewayTrade`` with arguments ``[contractId, data]``.
``data`` is one trade object or a list of them:
    {"symbolId": "F.US.EP", "price": 2100.25, "timestamp": "...", "type": 0, "volume": 2}
``type`` is TradeLogType: 0 = Buy (aggressor lifted the offer), 1 = Sell.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from .models import Side, TapePrint
from .projectx.symbols import display_root

_FRACTION_RE = re.compile(r"(\.\d{6})\d+")


def parse_timestamp_us(value: Any) -> int | None:
    """ISO-8601 (any fraction length, Z or offset) -> microseconds since epoch."""
    if not isinstance(value, str) or not value:
        return None
    text = _FRACTION_RE.sub(r"\1", value.strip().replace("Z", "+00:00"))
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    delta = moment - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def _side(value: Any) -> Side | None:
    if value in (0, "0"):
        return Side.BUY
    if value in (1, "1"):
        return Side.SELL
    return None


def parse_gateway_trade(arguments: list[Any]) -> list[TapePrint]:
    """Hub ``arguments`` -> prints. Rows with unknown side, bad price or size are dropped."""
    if not arguments:
        return []
    contract_id = str(arguments[0]) if len(arguments) > 1 else ""
    data = arguments[-1]
    rows = data if isinstance(data, list) else [data]
    prints: list[TapePrint] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        side = _side(row.get("type"))
        ts_us = parse_timestamp_us(row.get("timestamp"))
        try:
            price = float(row.get("price"))
            size = int(float(row.get("volume")))
        except (TypeError, ValueError):
            continue
        if side is None or ts_us is None or price <= 0 or size <= 0:
            continue
        cid = contract_id or str(row.get("contractId") or row.get("symbolId") or "")
        root = display_root(cid) if cid else display_root(str(row.get("symbolId") or ""))
        if not root:
            continue
        prints.append(TapePrint(root=root, contract_id=cid, price=price, size=size, side=side, ts_us=ts_us))
    return prints
