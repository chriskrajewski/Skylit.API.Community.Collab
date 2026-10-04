"""Fetch 1-minute VIX for the days in a slice (needed ONLY for options-mode scoring) and merge it into bars/bars_YYYY-MM-DD.json.
  1. copy pckt-bench/.env.example to pckt-bench/.env and put your Skylit API key in SKYLIT_API_KEY (the file is git-ignored; never commit a key)
  2. python fetch_vix.py --signals ../slices/001_.../signals.json --out bars/            # shows the credit cost and stops
  3. python fetch_vix.py --signals ../slices/001_.../signals.json --out bars/ --yes      # spends the credits
Cost: one Skylit Atlas call per day = 1 credit ($0.001) per day; slice 001 is 22 days = 22 credits (about $0.02). Cached days are free.
No Skylit key? Any VIX source works: put 1-minute (or even one value per day, then score with --vix-daily) VIX rows into bars/bars_<day>.json under "VIX",
or load a CSV with: python fetch_bars.py --signals ... --csv VIX=vix.csv --out bars/. Equity-mode scoring never needs VIX or a key."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from bench_common import pick_signals
from fetch_bars import HERE, atlas_day, credit_gate, merge_write


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--signals", required=True); ap.add_argument("--out", default="bars"); ap.add_argument("--yes", action="store_true"); ap.add_argument("--signal", action="append", metavar="N|ID", help="only this signal's day (1-based number or id prefix)"); a = ap.parse_args()
    days = sorted({s["date"] for s in pick_signals(json.loads(Path(a.signals).read_text(encoding="utf-8")), a.signal)})
    cache = HERE.parent / "cache" / "atlas"; todo = [d for d in days if not (cache / f"VIX_{d}.json").exists()]
    credit_gate(len(todo), f"VIX on {len(todo)} of {len(days)} days (the rest are cached)", a.yes) if todo else print("all days cached: no credits needed")
    from fetch_skylit import _key
    key = _key() if todo else ""; cache.mkdir(parents=True, exist_ok=True); out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    for d in days:
        rows = atlas_day("VIX", d, cache, key)
        if not rows: print(f"WARNING {d}: Atlas returned no VIX bars")
        merge_write(out, d, {"VIX": rows})
    print(f"VIX merged into {len(days)} day files in {out}")


if __name__ == "__main__": main()
