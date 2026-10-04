"""Skylit heatmap data for benchmark runners (optional: use it only if your agent should look at Heatseeker heatmaps).
CLI (all costs are shown first; nothing is spent without --yes):
  python fetch_skylit.py balance                                          # your credit balance (free call)
  python fetch_skylit.py estimate --signals S [--signal 3] [--cadence 3] [--metrics gamma,vanna]     # credits per signal and in total, no API calls
  python fetch_skylit.py pull --signals S --signal 3 --cadence 3 --metrics gamma --out heatmaps/ [--yes]
      pulls every decision-point snapshot of ONE signal (window open ... signal end) into heatmaps/<signal_id>/<epoch>_<metric>.json.
      File <epoch>_<metric>.json is the latest snapshot at or before <epoch>, so a runner reading only files with epoch <= as_of cannot leak.
Library: AsOfClient(as_of=epoch).snapshot(symbols, at, metric) refuses any `at` later than as_of (LeakageError) and caches responses.
Key: SKYLIT_API_KEY in the environment or in pckt-bench/.env (never commit it). Docs: https://docs.skylit.ai/api-reference/introduction
Cost: 5 credits ($0.005) per call per metric (one call covers up to 10 symbols). Limits: 120 req/min, 2 historical requests in flight."""
from __future__ import annotations
import argparse, hashlib, json, os, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://api.skylit.ai"
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
CREDITS_PER_HISTORICAL_CALL = 5


class LeakageError(RuntimeError): pass


def _key() -> str:
    if os.environ.get("SKYLIT_API_KEY"): return os.environ["SKYLIT_API_KEY"]
    env = HERE.parent / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("SKYLIT_API_KEY=") and line.split("=", 1)[1].strip(): return line.split("=", 1)[1].strip().strip('"')
    raise RuntimeError("SKYLIT_API_KEY not set (environment or pckt-bench/.env)")


def iso(t: int) -> str: return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AsOfClient:
    def __init__(self, as_of: int, cache_dir: str | Path = HERE.parent / "cache", min_interval: float = 0.6):
        self.as_of, self.min_interval, self._last = int(as_of), min_interval, 0.0
        self.cache = Path(cache_dir); self.cache.mkdir(parents=True, exist_ok=True); self.credits_spent = 0

    def cache_file(self, symbols: list[str], at: int, metric: str, layout: str = "matrix", max_expirations: int = 5) -> Path:
        q = {"symbols": ",".join(symbols), "at": iso(at), "metric": metric, "layout": layout, "maxExpirations": max_expirations}
        return self.cache / (hashlib.sha1(json.dumps(q, sort_keys=True).encode()).hexdigest() + ".json")

    def snapshot(self, symbols: list[str], at: int, metric: str = "gamma", layout: str = "matrix", max_expirations: int = 5) -> dict | None:
        """Latest snapshot at/before `at`. Raises LeakageError if `at` is after this client's as_of. None if Skylit has no data (404 no_data)."""
        if int(at) > self.as_of: raise LeakageError(f"requested {iso(at)} but as_of is {iso(self.as_of)}")
        q = {"symbols": ",".join(symbols), "at": iso(at), "metric": metric, "layout": layout, "maxExpirations": max_expirations}
        f = self.cache_file(symbols, at, metric, layout, max_expirations)
        if f.exists(): body = json.loads(f.read_text(encoding="utf-8"))
        else:
            body = self._get("/v1/historical", q, CREDITS_PER_HISTORICAL_CALL)
            if body is None: return None
            f.write_text(json.dumps(body), encoding="utf-8")
        for s in body["data"]["symbols"]:                                 # belt and braces: the snapshot itself must predate as_of
            if datetime.fromisoformat(s["asOf"].replace("Z", "+00:00")).timestamp() > self.as_of + 1: raise LeakageError(f"{s['symbol']} snapshot asOf {s['asOf']} is after as_of")
        return body

    def _get(self, path: str, q: dict, cost: int = 0):
        url = BASE + path + ("?" + urllib.parse.urlencode(q) if q else "")
        for attempt in range(6):
            time.sleep(max(0.0, self._last + self.min_interval - time.monotonic())); self._last = time.monotonic()
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": "Bearer " + _key()}), timeout=40) as r:
                    self.credits_spent += cost; return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 404: return None
                if e.code in (429, 500, 502, 503, 504): time.sleep(2 ** attempt); continue
                raise
            except urllib.error.URLError: time.sleep(2 ** attempt)
        raise RuntimeError("Skylit request kept failing")


def decision_times(sig: dict, doc: dict, cadence: int) -> list[int]:
    from bench_common import window
    w = window(sig, doc["window"]["lead_min"], doc["window"]["close_after_min"])
    return list(range(w["open_t"], w["end_t"] + 1, cadence * 60))


def _setup(a):
    from bench_common import load_signals, pick_signals
    doc = load_signals(a.signals); return doc, pick_signals(doc, a.signal), [m.strip() for m in a.metrics.split(",") if m.strip()]


def estimate(a) -> None:
    doc, sigs, metrics = _setup(a); total = 0
    print(f"{'#':>3} {'date':10} {'window opens (ET)':18} {'decisions':>9} {'credits':>8}   ({len(metrics)} metric(s) x {CREDITS_PER_HISTORICAL_CALL} credits, cadence {a.cadence} min)")
    for s in sigs:
        n = len(decision_times(s, doc, a.cadence)); c = n * len(metrics) * CREDITS_PER_HISTORICAL_CALL; total += c
        idx = doc["signals"].index(s) + 1
        from bench_common import ET, window
        print(f"{idx:>3} {s['date']} {datetime.fromtimestamp(window(s, 20, 15)['open_t'], ET).strftime('%H:%M'):18} {n:>9} {c:>8,}")
    print(f"total: {total:,} credits (about ${total * 0.001:,.2f}); cached calls are free; the heatmap is optional for your agent")


def balance(a) -> None:
    body = AsOfClient(0)._get("/v1/account", {}); d = body["data"]
    print(f"credits balance: {d['creditsBalance']:,} (${d['balanceUsd']:,.2f}); this call is free")


def pull(a) -> None:
    from fetch_bars import credit_gate
    doc, sigs, metrics = _setup(a); syms = a.symbols.split(","); out = Path(a.out); todo = []
    for s in sigs:
        for t in decision_times(s, doc, a.cadence):
            for m in metrics:
                if not AsOfClient(t).cache_file(syms, t, m).exists(): todo.append((s, t, m))
    credit_gate(len(todo), f"{len(sigs)} signal(s), {len(metrics)} metric(s), cadence {a.cadence} min, symbols {a.symbols}", a.yes, CREDITS_PER_HISTORICAL_CALL, "heatmap (/v1/historical)")
    for s in sigs:
        d = out / s["id"]; d.mkdir(parents=True, exist_ok=True); miss = 0
        for t in decision_times(s, doc, a.cadence):
            for m in metrics:
                body = AsOfClient(t).snapshot(syms, t, m)                              # as_of = the decision minute: cannot fetch the future
                if body is None: miss += 1; continue
                (d / f"{t}_{m}.json").write_text(json.dumps(body), encoding="utf-8")
        print(f"signal {s['id'][:8]}: wrote {len(list(d.glob('*.json')))} snapshot file(s) to {d}" + (f" ({miss} no_data)" if miss else ""))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    def common(p):
        p.add_argument("--signals", required=True); p.add_argument("--signal", action="append", metavar="N|ID", help="1-based number or id prefix; repeatable; default all")
        p.add_argument("--cadence", type=int, default=3); p.add_argument("--metrics", default="gamma", help="gamma, vanna or gamma,vanna")
    sub.add_parser("balance")
    common(sub.add_parser("estimate")); p = sub.add_parser("pull"); common(p)
    p.add_argument("--symbols", default="SPXW,SPY,QQQ"); p.add_argument("--out", default="heatmaps"); p.add_argument("--yes", action="store_true")
    a = ap.parse_args(); {"balance": balance, "estimate": estimate, "pull": pull}[a.cmd](a)
