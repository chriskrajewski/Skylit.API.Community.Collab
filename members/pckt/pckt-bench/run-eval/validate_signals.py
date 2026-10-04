"""Check a slice before running it: manifest hash, schema-level sanity, window arithmetic.   python validate_signals.py ../slices/001_.../signals.json"""
import math, sys
from bench_common import load_signals, session, window


def main(path: str) -> int:
    doc = load_signals(path); errs = []
    for s in doc["signals"]:
        o, c = session(s["date"])
        if s["direction"] not in ("long", "short"): errs.append((s["id"], "direction"))
        if not (o <= s["start"] < s["end"] <= c): errs.append((s["id"], "start/end outside RTH or not ordered"))
        if not s["legs"]: errs.append((s["id"], "no legs"))
        for sym, l in s["legs"].items():
            if sym not in ("SPX", "SPY", "QQQ") or not all(math.isfinite(l[k]) for k in ("t0", "p0", "t1", "p1")) or l["t1"] <= l["t0"] and l["t1"] != l["t0"]: errs.append((s["id"], f"bad leg {sym}"))
        w = window(s, doc["window"]["lead_min"], doc["window"]["close_after_min"])
        if not (w["open_t"] < w["end_t"] <= w["force_t"]): errs.append((s["id"], "bad window"))
    ids = [s["id"] for s in doc["signals"]]
    if len(ids) != len(set(ids)): errs.append(("-", "duplicate ids"))
    print(f"{doc['slice']}: {len(ids)} signals, {len({s['date'] for s in doc['signals']})} days, {len(errs)} problems")
    for e in errs: print("  ", e)
    return 1 if errs else 0


if __name__ == "__main__": sys.exit(main(sys.argv[1]))
