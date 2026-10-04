"""Check your trades.json before scoring it.   python validate_trades.py --signals ../slices/001_.../signals.json --trades results/trades.json
Reports: unknown/duplicate signal ids, signals with no run, malformed trades, trades outside the window, close before open. Exit code 1 if anything is wrong."""
import argparse, json, sys
from collections import Counter
from pathlib import Path
from bench_common import load_signals, window
from score import structural


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--signals", required=True); ap.add_argument("--trades", required=True); a = ap.parse_args()
    doc = load_signals(a.signals); sigs = {s["id"]: s for s in doc["signals"]}
    try: runs = json.loads(Path(a.trades).read_text(encoding="utf-8"))["runs"]
    except (OSError, ValueError, KeyError) as e: print("cannot read trades file:", e); return 1
    errs, seen, rej, n = [], Counter(), Counter(), 0
    for r in runs:
        sid = r.get("signal_id")
        if sid not in sigs: errs.append(f"unknown signal_id {sid!r}"); continue
        seen[sid] += 1
        for t in r.get("trades", []):
            if not all(k in t for k in ("symbol", "side", "open_time")): errs.append(f"{sid}: trade missing symbol/side/open_time: {t}")
        ok, rj = structural(r, sigs[sid], window(sigs[sid], doc["window"]["lead_min"], doc["window"]["close_after_min"])); rej.update(rj); n += len(ok)
    errs += [f"signal_id {k} appears {v} times" for k, v in seen.items() if v > 1]
    missing = [k for k in sigs if k not in seen]
    print(f"{len(runs)} runs for {len(sigs)} signals; {n} valid trades; rejected by the scorer: {dict(rej) or 'none'}; signals with no run (scored as no-trade): {len(missing)}")
    for e in errs[:20]: print("  ERROR", e)
    return 1 if errs or rej else 0


if __name__ == "__main__": sys.exit(main())
