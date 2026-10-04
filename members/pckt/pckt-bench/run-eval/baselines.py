"""Reference policies that calibrate a score. They write the same trades.json an agent would, so score.py treats them identically.
  python baselines.py --signals ../slices/001_.../signals.json --bars-dir bars/ --out results/baselines/
Policies: oracle (enter/exit at the arrow's own times: an upper bound on timing, uses the answer), always_long / always_short (open at the window start,
hold to the forced close), random (seeded random direction, entry and exit inside the window). Signals are hindsight-selected, so always_<direction of the day> can look good: compare agents to these."""
from __future__ import annotations
import argparse, json, random
from pathlib import Path
from bench_common import MIN, Bars, load_signals, window


def policies(sig: dict, w: dict, rng: random.Random) -> dict[str, list[dict]]:
    sym = "SPY" if "SPY" in sig["legs"] else "QQQ" if "QQQ" in sig["legs"] else "SPY"
    leg = sig["legs"].get(sym) or next(iter(sig["legs"].values()))
    o = max(leg["t0"] // MIN * MIN, w["open_t"]); c = max(leg["t1"] // MIN * MIN, o + MIN)
    ro = rng.randrange(w["open_t"] // MIN, w["end_t"] // MIN + 1) * MIN; rc = min(ro + rng.randrange(5, 31) * MIN, w["force_t"])
    return {"oracle": [{"symbol": sym, "side": sig["direction"], "open_time": o, "close_time": c}],
            "always_long": [{"symbol": sym, "side": "long", "open_time": w["open_t"], "close_time": None}],
            "always_short": [{"symbol": sym, "side": "short", "open_time": w["open_t"], "close_time": None}],
            "random": [{"symbol": rng.choice(["SPY", "QQQ"]), "side": rng.choice(["long", "short"]), "open_time": ro, "close_time": rc}]}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--signals", required=True); ap.add_argument("--bars-dir", required=True)
    ap.add_argument("--out", default="results/baselines"); ap.add_argument("--seed", type=int, default=7); a = ap.parse_args()
    doc = load_signals(a.signals); rng = random.Random(a.seed); out = {}
    for s in doc["signals"]:
        for name, trs in policies(s, window(s, doc["window"]["lead_min"], doc["window"]["close_after_min"]), rng).items():
            out.setdefault(name, []).append({"signal_id": s["id"], "trades": trs})
    Path(a.out).mkdir(parents=True, exist_ok=True)
    for name, runs in out.items(): (Path(a.out) / f"{name}.trades.json").write_text(json.dumps({"slice": doc["slice"], "runs": runs}), encoding="utf-8")
    print("wrote", ", ".join(f"{n}.trades.json" for n in out), "->", a.out)


if __name__ == "__main__": main()
