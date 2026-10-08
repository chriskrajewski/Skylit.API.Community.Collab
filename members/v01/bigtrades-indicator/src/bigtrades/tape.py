# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 v01
"""Optional raw tape recorder (off by default) and offline replay.

Recording writes one JSON object per hub message to ``<dir>/YYYY-MM-DD.jsonl``:
    {"recv_ms": 1760000000123.4, "arguments": ["CON.F.US.EP.Z26", {...}]}
Replay feeds those lines back through the aggregator using ``recv_ms`` as the
arrival clock, so quiet closes behave exactly as they did live.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .aggregator import SweepAggregator
from .gateway import parse_gateway_trade
from .models import BigTrade


class TapeRecorder:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def record(self, arguments: list[Any], recv_ms: float) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        day = datetime.fromtimestamp(recv_ms / 1000, tz=UTC).strftime("%Y-%m-%d")
        line = json.dumps({"recv_ms": recv_ms, "arguments": arguments}, separators=(",", ":"))
        with (self.directory / f"{day}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def read_records(lines: Iterable[str]) -> Iterator[tuple[float | None, list[Any]]]:
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        record = json.loads(raw)
        if isinstance(record, dict) and isinstance(record.get("arguments"), list):
            yield record.get("recv_ms"), record["arguments"]
        elif isinstance(record, dict):  # a bare GatewayTrade object
            yield None, [str(record.get("contractId") or record.get("symbolId") or ""), record]


def replay(lines: Iterable[str], aggregator: SweepAggregator) -> list[BigTrade]:
    """Deterministic offline run. Without ``recv_ms`` the exchange time is the clock."""
    out: list[BigTrade] = []
    for recv_ms, arguments in read_records(lines):
        for p in parse_gateway_trade(arguments):
            now_ms = recv_ms if recv_ms is not None else p.ts_us / 1000
            out.extend(aggregator.flush(now_ms))
            out.extend(aggregator.on_print(p, now_ms))
    out.extend(aggregator.close_all())
    return out
