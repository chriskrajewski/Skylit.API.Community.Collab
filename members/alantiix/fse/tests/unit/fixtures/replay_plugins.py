"""Two scripted plugins for the BC-2 replay golden (design §12).

Both act on fixed bars of ``replay_bars_v1.jsonl``, keyed by the New York
wall time of the bar's open, so a plugin rebuilt after a restart issues the
same intents for the same bars. Neither keeps state beyond
:class:`~tests.fakes.plugins.RecordingPlugin`'s, and neither reads research
data or calendars.

``replay_a`` (MNQ, ``risk: {max_contracts: 2, max_daily_loss: "150.00"}``):

- Tue 09:35: market long 1, stop 40, target 20 (the target hits);
- Tue 09:50: market long 3, refused ``max_contracts``;
- Tue 09:59: limit long 2 twelve ticks under the close, stop 200 under it
  (fills on the 10:00s drop, then stops out: the day is down more than $150);
- Tue 10:40: market long 1, refused ``max_daily_loss``;
- Wed 09:59 (the last bar before the restart): market long 1, stop 40, target
  30; it fills on the first bar the host missed, so phase 2 replays the fill;
- Wed 10:30 (live after the restart): market long 1, stop 20, target 20.

``replay_b`` (MES, ``risk: {max_contracts: 4}``):

- Tue 09:40: market short 2, stop 40, target 200;
- Tue 09:50: ``Flatten(b1, "plugin_exit", "next_open")``;
- Tue 16:55: limit long 1 far under the market, still working at the 18:00
  rotation (the host's backstop cancels it);
- Wed 09:59: market long 1, stop 40, target 40 (fills on a replayed bar);
- Wed 10:10 (a replayed bar): ``Flatten(b3, "plugin_exit", "next_open")``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, ClassVar, Final

from fse.live.plugins import (
    BarNeed,
    DataNeeds,
    EnterMarket,
    EnterResting,
    Flatten,
    Intent,
    PluginServices,
    TargetLeg,
)
from fse.timekit import ny_datetime
from tests.fakes.plugins import RecordingPlugin

__all__ = ["DAY1", "DAY2", "ReplayA", "ReplayB"]

DAY1: Final = date(2026, 10, 13)
"""Globex day 1 (Tuesday)."""
DAY2: Final = date(2026, 10, 14)
"""Globex day 2 (Wednesday); it opens Tuesday 18:00."""

type Key = tuple[date, int, int]


def _key(open_ns: int) -> Key:
    local = ny_datetime(open_ns)
    return (local.date(), local.hour, local.minute)


class _Scripted(RecordingPlugin):
    """Runs ``script(bar)`` on every bar, after recording the call."""

    def __init__(
        self, plugin_id: str, config: Mapping[str, object], services: PluginServices
    ) -> None:
        super().__init__(plugin_id, config, services)
        self.handlers["on_bar"] = self._on_bar

    def _on_bar(self, bar: Any, received_ns: int, catch_up: bool, ctx: Any) -> Sequence[Intent]:
        return self.script(bar, _key(bar.open_ns))

    def script(self, bar: Any, key: Key) -> Sequence[Intent]:
        raise NotImplementedError


class ReplayA(_Scripted):
    NEEDS: ClassVar[DataNeeds] = DataNeeds(bars=(BarNeed("MNQ"),), hours="eth")

    def script(self, bar: Any, key: Key) -> Sequence[Intent]:
        placed = bar.close_ns
        if key == (DAY1, 9, 35):
            return [EnterMarket("a1", "MNQ", "long", 1, 40, (TargetLeg("tp1", 1, 20),), placed)]
        if key == (DAY1, 9, 50):
            return [EnterMarket("a2", "MNQ", "long", 3, 40, (TargetLeg("tp1", 3, 20),), placed)]
        if key == (DAY1, 9, 59):
            price = bar.c_t - 12
            return [
                EnterResting(
                    "a3", "MNQ", "long", 2, "limit", price, price - 200, (("tp1", 2, price + 400),)
                )
            ]
        if key == (DAY1, 10, 40):
            return [EnterMarket("a4", "MNQ", "long", 1, 40, (TargetLeg("tp1", 1, 20),), placed)]
        if key == (DAY2, 9, 59):
            return [EnterMarket("a5", "MNQ", "long", 1, 40, (TargetLeg("tp1", 1, 30),), placed)]
        if key == (DAY2, 10, 30):
            return [EnterMarket("a6", "MNQ", "long", 1, 20, (TargetLeg("tp1", 1, 20),), placed)]
        return []


class ReplayB(_Scripted):
    NEEDS: ClassVar[DataNeeds] = DataNeeds(bars=(BarNeed("MES"),), hours="eth")

    def script(self, bar: Any, key: Key) -> Sequence[Intent]:
        placed = bar.close_ns
        if key == (DAY1, 9, 40):
            return [EnterMarket("b1", "MES", "short", 2, 40, (TargetLeg("tp1", 2, 200),), placed)]
        if key == (DAY1, 9, 50):
            return [Flatten("b1", "plugin_exit", "next_open")]
        if key == (DAY1, 16, 55):
            price = bar.c_t - 100
            return [
                EnterResting(
                    "b2", "MES", "long", 1, "limit", price, price - 40, (("tp1", 1, price + 40),)
                )
            ]
        if key == (DAY2, 9, 59):
            return [EnterMarket("b3", "MES", "long", 1, 40, (TargetLeg("tp1", 1, 40),), placed)]
        if key == (DAY2, 10, 10):
            return [Flatten("b3", "plugin_exit", "next_open")]
        return []
