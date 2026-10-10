"""Independent IS-year parity check for ts4 / ts4_v2 / ts4_v2_be (verification step, not shipped).

Same harness as realdata.py (replay through PluginHost + PluginPaperBroker on the Atlas 1m
market), but:
- the research configs are built from research.ts4.variants (baseline / c1b_nobe / c1b on the
  is1m dataset patch: ema 346, stop_first, 1m bars), not from the plugin's declared config, and
  asserted equal to it;
- the sim's per-trade stop-move sequence is recorded by wrapping the Counter that simulate()
  passes to _scan_exit (sim.py is not modified), and compared with the plugin's
  ts4_stop_move log entries per trade: (effective bar open ns, stop price adjusted).

Run: PYTHONPATH=$E/src:$E $V/python verify_parity.py --set zero|real --out FILE.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import tempfile
import time as _time
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Any

from tests.fakes.dataplane import PROJECT_DIR, read_jsonl

import research.ts4.sim as simmod
from fse.live.host.host import HOST_LOG_FILE_NAME, PLUGIN_LOG_FILE_NAME
from research.ts4 import paths
from research.ts4.config import Ts4Config, apply_overrides, load_config
from research.ts4.plugin.rules import resolve
from research.ts4.plugin.tests.realdata import (
    IS_FIRST,
    IS_LAST,
    Case,
    _entry,
    engine_bars,
    load,
)
from research.ts4.plugin.tests.replay import compare, cut_points, host_calendars, ny_ns, replay
from research.ts4.research import REALISTIC
from research.ts4.sim import SimData, simulate
from research.ts4.variants import DATASETS, VARIANTS

MIN = 60 * 10**9
_MOVES: list[tuple[int, int, int]] = []  # (fill idx f, move bar idx j, new stop)


class _RecCounter(Counter):  # type: ignore[type-arg]
    def __setitem__(self, key: Any, value: Any) -> None:
        if key == "stop_moves":
            fr = sys._getframe(1)
            if fr.f_code.co_name == "_scan_exit":
                loc = fr.f_locals
                _MOVES.append((int(loc["f"]), int(loc["j"]), int(loc["new"])))
        super().__setitem__(key, value)


def research_cfg(variant: str, realistic: bool) -> Ts4Config:
    """variants.Runner.cfg(VARIANTS[variant], DATASETS['is1m']), base = frozen (+ny) (+costs)."""
    base = load_config(paths.DEFAULT_CONFIG)
    if variant != "baseline":
        base = apply_overrides(base, {"clock": "ny"})
    if realistic:
        base = apply_overrides(base, REALISTIC)
    ds = DATASETS["is1m"]
    cfg = apply_overrides(base, ds.patch)
    cfg = apply_overrides(cfg, {"data": {"bars": "1m"}})
    return VARIANTS[variant].apply(cfg)


def recorded_sim(cfg: Ts4Config, data: SimData) -> tuple[Any, dict[int, list[tuple[int, int]]]]:
    _MOVES.clear()
    orig = simmod.Counter
    simmod.Counter = _RecCounter  # type: ignore[misc]
    try:
        res = simulate(cfg, data, IS_FIRST - timedelta(days=1), IS_LAST)
    finally:
        simmod.Counter = orig  # type: ignore[misc]
    open_ns = data.bars.open_ns
    seq: dict[int, list[tuple[int, int]]] = {}
    for f, j, new in _MOVES:
        # the sim applies the move from bar j+1; the broker's effective_ns = bar j open + 60 s
        seq.setdefault(int(open_ns[f]), []).append((int(open_ns[j]) + MIN, new))
    return res, seq


def money(nets: list[int]) -> dict[str, Any]:
    n = len(nets)
    return {
        "trades": n,
        "win_rate": round(sum(x > 0 for x in nets) / n, 4) if n else None,
        "net_usd": round(sum(nets) / 100, 2),
        "per_trade_usd": round(sum(nets) / 100 / n, 2) if n else None,
        "worst_usd": round(min(nets) / 100, 2) if n else None,
        "best_usd": round(max(nets) / 100, 2) if n else None,
    }


async def main_async(which: str) -> dict[str, Any]:
    m = load(IS_FIRST, IS_LAST)
    data = SimData(m.bars, market=m)
    bars = engine_bars(m, IS_FIRST, IS_LAST)
    offsets = m.series.offsets
    if which == "zero":
        plan = [
            (Case("ts4", "ts4"), "baseline", False),
            (Case("ts4_v2", "ts4_v2"), "c1b_nobe", False),
            (Case("ts4_v2_be", "ts4_v2_be"), "c1b", False),
        ]
    else:
        plan = [(Case("ts4_v2_be_real", "ts4_v2_be", 1, "1.48", 1), "c1b", True)]
    resolved = {c.name: resolve({"ts4_config": c.preset}, PROJECT_DIR) for c, _, _ in plan}
    cuts = cut_points([r.cfg for r in resolved.values()], IS_FIRST, IS_LAST)
    cals = host_calendars(IS_FIRST, IS_LAST)
    start, until = ny_ns(IS_FIRST - timedelta(days=1), 18), ny_ns(IS_LAST, 18) + MIN // 6
    out: dict[str, Any] = {"bars": len(bars)}
    with tempfile.TemporaryDirectory() as work:
        for case, variant, realistic in plan:
            r = resolved[case.name]
            rcfg = research_cfg(variant, realistic)
            # the plugin's declared research config (+ host fills) must be the research variant
            d = r.declared
            if realistic:
                d = apply_overrides(d, REALISTIC)
            cfg_equal = d.model_dump(exclude={"data"}) == rcfg.model_dump(exclude={"data"})
            t0 = _time.perf_counter()
            tmp = Path(tempfile.mkdtemp(dir=work))
            rep = await replay(
                tmp,
                bars,
                [_entry(case, 60)],
                start_ns=start,
                until_ns=until,
                cuts=cuts,
                calendars=cals,
            )
            elapsed = round(_time.perf_counter() - t0, 1)
            sim, sim_seq = recorded_sim(rcfg, data)
            rows = rep.trades(case.name)
            cmp = compare(sim.trades, rows, offsets)
            plog = read_jsonl(rep.out[case.name] / PLUGIN_LOG_FILE_NAME)
            hlog = read_jsonl(tmp / "run" / HOST_LOG_FILE_NAME)
            tag_ns = {row["tag"]: int(row["entry_open_ns"]) for row in rows}
            tag_contract = {row["tag"]: row["contract"] for row in rows}
            plug_seq: dict[int, list[tuple[int, int]]] = {}
            for e in plog:
                if e.get("kind") == "ts4_stop_move":
                    tag = e["tag"]
                    k = tag_ns[tag]
                    adj = int(e["stop_price"]) + offsets.get(tag_contract[tag], 0)
                    plug_seq.setdefault(k, []).append((int(e["effective_ns"]), adj))
            trade_moves = {
                tag_ns[e["tag"]]: int(e["stop_moves"]) for e in plog if e.get("kind") == "ts4_trade"
            }
            sim_by = {t.entry_open_ns: t for t in sim.trades}
            row_by = {int(row["entry_open_ns"]): row for row in rows}
            keys = sorted(set(sim_by) | set(row_by))
            seq_equal = sum(1 for k in keys if sim_seq.get(k, []) == plug_seq.get(k, []))
            seq_bad = [
                {"entry_open_ns": k, "sim": sim_seq.get(k, []), "plugin": plug_seq.get(k, [])}
                for k in keys
                if sim_seq.get(k, []) != plug_seq.get(k, [])
            ]
            moved_flag_ok = all(
                sim_by[k].stop_moved == bool(sim_seq.get(k)) for k in sim_by
            )
            log_count_ok = all(trade_moves.get(k, 0) == len(plug_seq.get(k, [])) for k in row_by)
            kinds = Counter(str(e.get("kind")) for e in plog)
            res: dict[str, Any] = {
                "research_variant": variant,
                "declared_equals_research_cfg": cfg_equal,
                "be_trail_live": r.be_trail_live,
                "inert_rewrite": r.inert_rewrite,
                "sim_trades": cmp.sim,
                "plugin_trades": cmp.plugin,
                "matched": cmp.matched,
                "exact": cmp.exact,
                "elapsed_s": elapsed,
                "exit_code": rep.exit_code,
                "faults": rep.host.faults(),
                "backstops": sum(1 for e in hlog if e.get("kind") == "host_break_backstop"),
                "stop_moves_sim_counter": sim.counters.get("stop_moves", 0),
                "stop_moves_sim_recorded": sum(len(v) for v in sim_seq.values()),
                "stop_moves_plugin": sum(len(v) for v in plug_seq.values()),
                "trades_moved_sim": len(sim_seq),
                "trades_moved_plugin": len(plug_seq),
                "moves_per_trade_hist_sim": dict(Counter(len(v) for v in sim_seq.values())),
                "moves_per_trade_hist_plugin": dict(Counter(len(v) for v in plug_seq.values())),
                "seq_equal_trades": seq_equal,
                "seq_mismatch_trades": len(seq_bad),
                "seq_mismatch_examples": seq_bad[:8],
                "sim_stop_moved_flag_consistent": moved_flag_ok,
                "ts4_trade_stop_moves_consistent": log_count_ok,
                "plugin_log_stop_kinds": {
                    k: kinds[k]
                    for k in ("ts4_stop_move", "stop_move_skipped", "stop_move_rejected", "stop_desync")
                },
                "sim": money([t.net_cents for t in sim.trades]),
                "plugin": money([int(row["net_cents"]) for row in rows]),
            }
            moved_keys = [k for k in keys if k in sim_seq or k in plug_seq]
            res["moved_trades_rows_exact"] = sum(
                1
                for k in moved_keys
                if k in sim_by and k in row_by and compare([sim_by[k]], [row_by[k]], offsets).exact
            )
            res["moved_trades_reasons_sim"] = dict(
                Counter(sim_by[k].reason for k in moved_keys if k in sim_by)
            )
            res["moved_trades_money_sim"] = money(
                [sim_by[k].net_cents for k in moved_keys if k in sim_by]
            )
            res["moved_trades_money_plugin"] = money(
                [int(row_by[k]["net_cents"]) for k in moved_keys if k in row_by]
            )
            if realistic:
                pairs = [(sim_by[k], row_by[k]) for k in keys if k in sim_by and k in row_by]
                diffs = [int(b["net_cents"]) - a.net_cents for a, b in pairs]
                fields_off: Counter[str] = Counter()
                for a, b in cmp.mismatched:
                    if a is not None and b is not None:
                        fields_off.update(x for x in a if a[x] != b[x])
                res["per_trade"] = {
                    "paired": len(pairs),
                    "sim_only": len(set(sim_by) - set(row_by)),
                    "plugin_only": len(set(row_by) - set(sim_by)),
                    "net_diff_cents_mean": round(statistics.mean(diffs), 2) if diffs else None,
                    "net_diff_cents_median": statistics.median(diffs) if diffs else None,
                    "net_diff_zero": sum(x == 0 for x in diffs),
                    "net_diff_pos": sum(x > 0 for x in diffs),
                    "net_diff_neg": sum(x < 0 for x in diffs),
                    "net_diff_min_cents": min(diffs) if diffs else None,
                    "net_diff_max_cents": max(diffs) if diffs else None,
                    "reason_equal": sum(a.reason == b["reason"] for a, b in pairs),
                    "reason_flips": dict(
                        Counter(f"{a.reason}->{b['reason']}" for a, b in pairs if a.reason != b["reason"])
                    ),
                    "win_flips": sum((a.net_cents > 0) != (int(b["net_cents"]) > 0) for a, b in pairs),
                    "mismatch_fields": dict(fields_off),
                }
            out[case.name] = res
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=("zero", "real"), default="zero")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    res = asyncio.run(main_async(a.set))
    a.out.write_text(json.dumps(res, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(res, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
