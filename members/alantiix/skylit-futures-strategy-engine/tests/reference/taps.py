"""A reference Tap count over a synthetic session's stored bars (Properties 55 and 56).

The bands come from the production :func:`fse.engine.taps.node_bands` at each
bar's close, read from a :class:`~fse.pit.market_view.HistoricalInputs` view
at that close. The run detection and the 300 s window test are written here
independently of :class:`fse.engine.taps.TapState`:

- a bar counts when it opens from 09:30 to before 16:00 New York time and
  closes at or before the session's Flat_Deadline (the Backtester feeds no
  later bar);
- a Tap of a Node starts at the open of a bar whose range meets the Node's
  band, when the previous counted bar of the instrument did not; it ends at
  the close of the last bar of that run;
- a Tap is between two 300 s Decision_Times when some consecutive pair
  ``(lo, hi)`` of 09:30:00 + k x 300 s before 16:00 has ``lo < start`` and
  ``end < hi``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from typing import Final

from fse.config.schema import StrategyConfig
from fse.engine.step import EngineParams
from fse.engine.taps import NodeId, node_bands
from fse.engine.types import Snapshot
from fse.pit.market_view import HistoricalInputs
from fse.timekit import NS_PER_SECOND, RTH_CLOSE, RTH_OPEN, SessionCalendar, ny_datetime, ny_instant
from tests.strategies.backtest_inputs import INSTRUMENTS, VIEW, SessionInputs

__all__ = ["RefTap", "reference_inter_decision", "reference_taps"]

_TICKS_PER_POINT: Final = 4
_GRID_S: Final = 300


@dataclass(frozen=True, slots=True)
class RefTap:
    """One reference Tap: its Node, its sequence number in the session, start and end."""

    node: NodeId
    instrument: str
    seq: int
    start: int
    end: int


def reference_taps(
    inputs: SessionInputs, cfg: StrategyConfig, calendar: SessionCalendar
) -> list[RefTap]:
    """Every Tap of the session on its stored 1-minute bars, in start order."""
    params = EngineParams.from_sections(cfg)
    # The Backtester keeps the first Snapshot stored per (symbol, metric, asOf).
    first: dict[tuple[str, str, int], Snapshot] = {}
    for snap in inputs.snapshots:
        first.setdefault((snap.symbol, snap.metric, snap.as_of_ns), snap)
    view_inputs = HistoricalInputs(
        symbols=tuple(cfg.data.symbols),
        view_id=VIEW.view_id(),
        snapshots=list(first.values()),
        bars=inputs.bars,
    )
    deadline = calendar.flat_deadline(inputs.session)
    out: list[RefTap] = []
    for instrument in INSTRUMENTS:
        open_runs: dict[NodeId, list[int]] = {}
        counts: dict[NodeId, int] = {}
        for bar in sorted(inputs.bars_of(instrument), key=lambda b: b.open_ns):
            local = ny_datetime(bar.open_ns)
            if not RTH_OPEN <= local.time() < RTH_CLOSE or bar.close_ns > deadline:
                continue
            assert bar.l_t is not None
            assert bar.h_t is not None
            low, high = bar.l_t / _TICKS_PER_POINT, bar.h_t / _TICKS_PER_POINT
            view = view_inputs.view(bar.close_ns)
            bands = node_bands(view, bar.close_ns, params.node_params, params.level_params)
            mine = [b for b in bands if b.instrument == instrument]
            hit = {b.node for b in mine if b.lo <= high and low <= b.hi}
            for node in list(open_runs):
                if node not in hit:
                    start, end = open_runs.pop(node)
                    counts[node] = counts.get(node, 0) + 1
                    out.append(RefTap(node, instrument, counts[node], start, end))
            for node in hit:
                if node in open_runs:
                    open_runs[node][1] = bar.close_ns
                else:
                    open_runs[node] = [bar.open_ns, bar.close_ns]
        for node, (start, end) in open_runs.items():
            counts[node] = counts.get(node, 0) + 1
            out.append(RefTap(node, instrument, counts[node], start, end))
    return sorted(out, key=lambda t: (t.start, t.node))


def reference_inter_decision(taps: list[RefTap], session: date) -> int:
    """How many of ``taps`` start and end strictly between two consecutive 300 s times."""
    first = ny_instant(session, RTH_OPEN)
    stop = ny_instant(session, RTH_CLOSE)
    grid = list(range(first, stop, _GRID_S * NS_PER_SECOND))
    return sum(
        1 for tap in taps if any(lo < tap.start and tap.end < hi for lo, hi in pairwise(grid))
    )
