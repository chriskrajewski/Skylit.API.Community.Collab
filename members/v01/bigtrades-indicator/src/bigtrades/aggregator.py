# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 v01
"""Sweep aggregation: many small prints -> one big trade.

An aggressive order that eats through several resting orders shows up on the
tape as a burst of prints with the same side and (nearly) the same exchange
timestamp. Alerting on the first print that crosses the threshold reports a
partial size; we wait for the sweep to finish instead (``final`` mode).

A sweep for a root stays open while each new print:
  * has the same aggressor side, and
  * has an exchange timestamp within ``join_ms`` of the previous print
    (``join_ms = 0`` means "identical timestamp").

It closes on a side flip, a timestamp gap, or ``quiet_ms`` of wall-clock
silence for that root (so the last sweep before a pause still gets published).

Pure and deterministic: callers pass ``now_ms`` (arrival clock) explicitly.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .models import BigTrade, Side, TapePrint

PUBLISH_MODES = ("final", "first_qualify")


class Thresholds:
    """Minimum sweep size per root. ``*`` is an optional catch-all."""

    def __init__(self, sizes: Mapping[str, int]) -> None:
        self._sizes = {key.upper(): int(value) for key, value in sizes.items() if int(value) > 0}

    def for_root(self, root: str) -> int | None:
        return self._sizes.get(root.upper(), self._sizes.get("*"))

    @property
    def roots(self) -> list[str]:
        return sorted(key for key in self._sizes if key != "*")

    @classmethod
    def parse(cls, text: str) -> Thresholds:
        """``"ES:200,NQ:100"`` -> Thresholds. Blank items are ignored."""
        sizes: dict[str, int] = {}
        for item in (text or "").split(","):
            if not item.strip():
                continue
            root, _, value = item.partition(":")
            if not root.strip() or not value.strip():
                raise ValueError(f"bad threshold item {item!r}; expected ROOT:SIZE")
            sizes[root.strip()] = int(value)
        return cls(sizes)


@dataclass(slots=True)
class _Sweep:
    root: str
    contract_id: str
    side: Side
    first_price: float
    last_price: float
    low: float
    high: float
    size: int
    notional: float
    prints: int
    start_ts_us: int
    last_ts_us: int
    last_arrival_ms: float
    published: bool = field(default=False)

    @classmethod
    def start(cls, p: TapePrint, now_ms: float) -> _Sweep:
        return cls(
            root=p.root,
            contract_id=p.contract_id,
            side=p.side,
            first_price=p.price,
            last_price=p.price,
            low=p.price,
            high=p.price,
            size=p.size,
            notional=p.price * p.size,
            prints=1,
            start_ts_us=p.ts_us,
            last_ts_us=p.ts_us,
            last_arrival_ms=now_ms,
        )

    def add(self, p: TapePrint, now_ms: float) -> None:
        self.last_price = p.price
        self.low = min(self.low, p.price)
        self.high = max(self.high, p.price)
        self.size += p.size
        self.notional += p.price * p.size
        self.prints += 1
        self.last_ts_us = max(self.last_ts_us, p.ts_us)
        self.last_arrival_ms = now_ms

    def extends(self, p: TapePrint, join_us: int) -> bool:
        if p.side is not self.side or p.contract_id != self.contract_id:
            return False
        return abs(p.ts_us - self.last_ts_us) <= join_us

    def snapshot(self, mode: str) -> BigTrade:
        return BigTrade(
            root=self.root,
            contract_id=self.contract_id,
            side=self.side,
            size=self.size,
            first_price=self.first_price,
            last_price=self.last_price,
            low=self.low,
            high=self.high,
            avg_price=self.notional / self.size,
            prints=self.prints,
            start_ts_us=self.start_ts_us,
            end_ts_us=self.last_ts_us,
            mode=mode,
        )


class SweepAggregator:
    def __init__(
        self,
        thresholds: Thresholds,
        *,
        join_ms: int = 0,
        quiet_ms: int = 300,
        publish_mode: str = "final",
    ) -> None:
        if publish_mode not in PUBLISH_MODES:
            raise ValueError(f"publish_mode must be one of {PUBLISH_MODES}")
        if join_ms < 0 or quiet_ms <= 0:
            raise ValueError("join_ms must be >= 0 and quiet_ms > 0")
        self.thresholds = thresholds
        self.join_us = int(join_ms) * 1000
        self.quiet_ms = quiet_ms
        self.publish_mode = publish_mode
        self._open: dict[str, _Sweep] = {}

    def on_print(self, p: TapePrint, now_ms: float) -> list[BigTrade]:
        out: list[BigTrade] = []
        sweep = self._open.get(p.root)
        if sweep is not None and not sweep.extends(p, self.join_us):
            out.extend(self._close(sweep))
            sweep = None
        if sweep is None:
            sweep = _Sweep.start(p, now_ms)
            self._open[p.root] = sweep
        else:
            sweep.add(p, now_ms)
        if self.publish_mode == "first_qualify" and not sweep.published and self._qualifies(sweep):
            sweep.published = True
            out.append(sweep.snapshot("first_qualify"))
        return out

    def flush(self, now_ms: float) -> list[BigTrade]:
        """Close sweeps that have been quiet for ``quiet_ms``. Call this often (e.g. every 50 ms)."""
        out: list[BigTrade] = []
        for sweep in list(self._open.values()):
            if now_ms - sweep.last_arrival_ms >= self.quiet_ms:
                out.extend(self._close(sweep))
        return out

    def close_all(self) -> list[BigTrade]:
        """Shutdown: close every open sweep."""
        out: list[BigTrade] = []
        for sweep in list(self._open.values()):
            out.extend(self._close(sweep))
        return out

    def _qualifies(self, sweep: _Sweep) -> bool:
        threshold = self.thresholds.for_root(sweep.root)
        return threshold is not None and sweep.size >= threshold

    def _close(self, sweep: _Sweep) -> list[BigTrade]:
        self._open.pop(sweep.root, None)
        if self.publish_mode == "final" and self._qualifies(sweep):
            return [sweep.snapshot("final")]
        return []
