"""Put 1-minute price bars for the days in a slice into bars/bars_YYYY-MM-DD.json (what score.py reads). Skylit is NOT required.

Use any vendor you like: export CSVs (timestamp column + open/high/low/close) and normalize them (free):
  python fetch_bars.py --signals ../slices/001_.../signals.json --csv SPX=spx.csv SPY=spy.csv QQQ=qqq.csv --out bars/ [--tz America/New_York] [--bar-label end]
Optional convenience: pull the same bars from Skylit Atlas (https://atlas-api.skylit.ai/v1/history, 1 credit per call, needs SKYLIT_API_KEY):
  python fetch_bars.py --signals ... --atlas --out bars/            # SPX, SPY, QQQ, VIX: one call per symbol per day (88 credits for slice 001); add --yes to proceed
Options mode needs VIX: see fetch_vix.py. Existing day files are merged, never overwritten, so VIX and price bars can be fetched in any order.
Rows are [t_start_utc_epoch, open, high, low, close], regular hours only for Atlas. Atlas responses are cached in cache/atlas/."""
from __future__ import annotations
import argparse, csv, json, sys, time, urllib.error, urllib.parse, urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ATLAS = "https://atlas-api.skylit.ai"
TS = ("ts", "timestamp", "datetime", "time", "date", "t")
SYMS = ("SPX", "SPY", "QQQ", "VIX")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from bench_common import pick_signals


def parse_ts(v: str, tz: ZoneInfo) -> int:
    v = v.strip()
    try:
        x = float(v); return int(x / 1000 if x > 1e11 else x)
    except ValueError: pass
    d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    return int((d if d.tzinfo else d.replace(tzinfo=tz)).timestamp())


def read_csv(path: str, tz: ZoneInfo, label: str) -> list[list[float]]:
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        rd = csv.DictReader(f); cols = {c.lower().strip(): c for c in rd.fieldnames}
        tcol = next((cols[c] for c in TS if c in cols), None)
        if not tcol or not all(k in cols for k in ("open", "high", "low", "close")): raise SystemExit(f"{path}: need a timestamp column {TS} and open/high/low/close, got {rd.fieldnames}")
        for r in rd:
            t = parse_ts(r[tcol], tz) - (60 if label == "end" else 0)
            rows.append([t - t % 60] + [float(r[cols[k]]) for k in ("open", "high", "low", "close")])
    return rows


def atlas_day(sym: str, day: str, cache: Path, key: str) -> list[list[float]]:
    y, m, d = map(int, day.split("-")); et = ZoneInfo("America/New_York")
    a, b = int(datetime(y, m, d, 9, 30, tzinfo=et).timestamp()), int(datetime(y, m, d, 16, 0, tzinfo=et).timestamp())
    f = cache / f"{sym}_{day}.json"
    if f.exists(): body = json.loads(f.read_text(encoding="utf-8"))
    else:
        url = f"{ATLAS}/v1/history?" + urllib.parse.urlencode({"symbol": sym, "resolution": "1", "from": a, "to": b})
        for attempt in range(5):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": "Bearer " + key}), timeout=30) as r: body = json.load(r); break
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504): time.sleep(2 ** attempt); continue
                raise SystemExit(f"Atlas {sym} {day}: HTTP {e.code} {e.read().decode()[:200]}")
        else: raise SystemExit(f"Atlas {sym} {day}: kept failing")
        f.write_text(json.dumps(body), encoding="utf-8"); time.sleep(0.15)
    if body.get("s") != "ok": return []
    return [[t, o, h, l, c] for t, o, h, l, c in zip(body["t"], body["o"], body["h"], body["l"], body["c"])]


def credit_gate(n_calls: int, what: str, yes: bool, per_call: int = 1, api: str = "Atlas") -> None:
    """Spending credits needs an explicit --yes. Atlas costs 1 credit ($0.001) per call, heatmap /v1/historical 5; cached calls are free."""
    c = n_calls * per_call
    print(f"This will make up to {n_calls} Skylit {api} calls for {what}: {c:,} credits (about ${c * 0.001:,.3f}) from your Skylit balance. Cached calls cost nothing.")
    if not yes:
        print("Nothing fetched. Re-run with --yes to proceed."); raise SystemExit(0)


def atlas_context(sym: str, day: str, res: str, lookback: int, cache: Path, key: str) -> list[list[float]]:
    """Daily ("D") or hourly ("60") bars that ended BEFORE `day` 09:30 ET: safe inputs for support/resistance levels at the window open or later."""
    y, m, d = map(int, day.split("-")); et = ZoneInfo("America/New_York")
    open_t = int(datetime(y, m, d, 9, 30, tzinfo=et).timestamp()); a = open_t - int((lookback * 1.5 + 4) * 86400)
    f = cache / f"ctx{res}_{sym}_{day}_{lookback}.json"
    if f.exists(): body = json.loads(f.read_text(encoding="utf-8"))
    else:
        url = f"{ATLAS}/v1/history?" + urllib.parse.urlencode({"symbol": sym, "resolution": res, "from": a, "to": open_t})
        for attempt in range(5):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": "Bearer " + key}), timeout=30) as r: body = json.load(r); break
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504): time.sleep(2 ** attempt); continue
                raise SystemExit(f"Atlas {sym} context {day}: HTTP {e.code} {e.read().decode()[:200]}")
        else: raise SystemExit("Atlas kept failing")
        f.write_text(json.dumps(body), encoding="utf-8"); time.sleep(0.15)
    if body.get("s") != "ok": return []
    day0 = int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())          # daily bars are stamped 00:00 UTC of their date: today's bar must not leak in
    keep = (lambda t: t < day0) if res == "D" else (lambda t: t < open_t)
    rows = [[t, o, h, l, c] for t, o, h, l, c in zip(body["t"], body["o"], body["h"], body["l"], body["c"]) if keep(t)]
    return rows[-lookback * (7 if res == "60" else 1):]


def merge_write(out: Path, day: str, bars: dict) -> None:
    f = out / f"bars_{day}.json"; cur = json.loads(f.read_text(encoding="utf-8"))["bars"] if f.exists() else {}
    cur.update({k: v for k, v in bars.items() if v}); f.write_text(json.dumps({"date": day, "bars": cur}), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", required=True); ap.add_argument("--out", default="bars")
    ap.add_argument("--csv", nargs="*", metavar="SYM=path", help="your own CSVs (any vendor)")
    ap.add_argument("--atlas", action="store_true", help="fetch SPX, SPY, QQQ, VIX 1-minute bars from Skylit Atlas (costs credits)")
    ap.add_argument("--signal", action="append", metavar="N|ID", help="only the day of this signal (1-based number or id prefix); repeatable")
    ap.add_argument("--context", choices=("D", "60"), help="daily or hourly bars BEFORE each day's open, for support/resistance levels (Atlas; costs credits)")
    ap.add_argument("--lookback", type=int, default=20, help="trading days of context (with --context)"); ap.add_argument("--yes", action="store_true", help="confirm the credit spend")
    ap.add_argument("--tz", default="UTC", help="zone for naive CSV timestamps"); ap.add_argument("--bar-label", choices=("start", "end"), default="start"); a = ap.parse_args()
    doc = json.loads(Path(a.signals).read_text(encoding="utf-8")); days = sorted({s["date"] for s in pick_signals(doc, a.signal)})
    by = defaultdict(dict); out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    if a.context:
        credit_gate(len(days) * 3, f"{a.context} context bars, {len(days)} day(s) x SPX/SPY/QQQ, {a.lookback} trading days back", a.yes)
        from fetch_skylit import _key
        key, cache = _key(), HERE.parent / "cache" / "atlas"; cache.mkdir(parents=True, exist_ok=True)
        for d in days:
            for sym in ("SPX", "SPY", "QQQ"):
                rows = atlas_context(sym, d, a.context, a.lookback, cache, key)
                (out / f"context_{a.context}_{sym}_{d}.json").write_text(json.dumps({"date": d, "resolution": a.context, "symbol": sym, "bars": rows}), encoding="utf-8")
        print(f"wrote context files for {len(days)} day(s) to {out} (only bars that closed before the day's 09:30 ET open)"); return
    if not a.csv and not a.atlas: raise SystemExit("Give --csv SYM=path ... (any vendor) or --atlas (Skylit, costs credits).")
    if a.atlas: credit_gate(len(days) * len(SYMS), f"{len(days)} days x {len(SYMS)} symbols", a.yes)
    if a.csv:
        tz, et = ZoneInfo(a.tz), ZoneInfo("America/New_York"); tmp = defaultdict(lambda: defaultdict(list))
        for spec in a.csv:
            sym, path = spec.split("=", 1)
            for r in read_csv(path, tz, a.bar_label):
                d = datetime.fromtimestamp(r[0], et).strftime("%Y-%m-%d")
                if d in days: tmp[d][sym.upper()].append(r)
        for d in days: by[d] = {s: sorted(v) for s, v in tmp[d].items()}
    else:
        from fetch_skylit import _key
        key, cache = _key(), HERE.parent / "cache" / "atlas"; cache.mkdir(parents=True, exist_ok=True)
        for d in days:
            for s in SYMS: by[d][s] = atlas_day(s, d, cache, key)
    for d in days:
        merge_write(out, d, by[d])
        have = json.loads((out / f"bars_{d}.json").read_text(encoding="utf-8"))["bars"]
        miss = [s for s in SYMS if not have.get(s)]
        if miss: print(f"note {d}: no bars yet for {miss}" + ("  (options mode needs VIX: run fetch_vix.py)" if "VIX" in miss else ""))
    print(f"wrote {len(days)} day files to {out}")


if __name__ == "__main__": main()
