"""Explain the realistic-cost stop-move mismatches (verification scratch, not shipped).

For each sim c1b trade (slip 1, tt 1, $1.48) whose stop moved, check whether the paper broker's
target (measured from the UNslipped fill-bar open, 1 tick closer than the sim's) is touched
(+ trade-through) on a bar f..j0, where j0 is the sim's first move bar. If so the plugin exits at
its target before any move can be sent.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from verify_parity import IS_FIRST, IS_LAST, load, recorded_sim, research_cfg  # noqa: E402

from research.ts4.sim import SimData  # noqa: E402

m = load(IS_FIRST, IS_LAST)
data = SimData(m.bars, market=m)
cfg = research_cfg("c1b", True)
tt = cfg.exits.target_trade_through_ticks
res, seq = recorded_sim(cfg, data)
b = data.bars
idx = {int(t): i for i, t in enumerate(b.open_ns.tolist())}
early = 0
for t in res.trades:
    if t.entry_open_ns not in seq:
        continue
    f = t.fill_idx
    j0 = idx[seq[t.entry_open_ns][0][0] - 60 * 10**9]  # the bar whose close moved the stop
    ptarget = int(b.o[f]) + t.side * t.target_ticks
    hit = None
    for j in range(f, j0 + 1):
        if (t.side == 1 and b.h[j] >= ptarget + tt) or (t.side == -1 and b.l[j] <= ptarget - tt):
            hit = j
            break
    if hit is not None:
        early += 1
        print(
            f"entry_open_ns={t.entry_open_ns} side={t.side} sim_reason={t.reason} "
            f"sim_target={t.target_t} plugin_target={ptarget} move_bar=f+{j0 - f} "
            f"plugin_target_bar=f+{hit - f} bar_extreme={int(b.h[hit] if t.side == 1 else b.l[hit])}"
        )
print("moved sim trades:", len(seq), "plugin target first:", early)
