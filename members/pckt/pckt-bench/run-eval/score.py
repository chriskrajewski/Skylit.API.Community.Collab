"""Score an agent's trade log on a pckt-bench slice.
  python score.py --signals ../slices/001_.../signals.json --trades results/trades.json --bars-dir bars/ [--out results/]
trades.json: {"runs": [{"signal_id": "...", "trades": [{"symbol": "SPY|QQQ", "side": "long|short", "open_time": epoch, "close_time": epoch|null}]}]}
 open_time / close_time are the minute the agent DECIDED (UTC epoch); the order fills at the OPEN of the bar starting at that minute.
 close_time null (or later than the forced close) = closed by the grader at the forced close. See rules.md for every rule."""
from __future__ import annotations
import argparse, json, statistics as st, sys
from collections import Counter
from pathlib import Path
from bench_common import MIN, Bars, load_signals, session, window
import options_model as om

TRADABLE = ("SPY", "QQQ")
DEFAULT_K = 1.3     # calibrated on slice 001 so random-direction trades in the windows have ~0 mean PnL (k=1.0 gave +$5.9k mean): see rules.md


def structural(run: dict, sig: dict, w: dict) -> tuple[list[dict], Counter]:
    """Validate trades against the window. Returns (valid trades with resolved times, rejection counts)."""
    ok, rej = [], Counter()
    for tr in sorted(run.get("trades", []), key=lambda x: x.get("open_time", 0)):
        sym, side = tr.get("symbol"), tr.get("side")
        if sym not in TRADABLE or side not in ("long", "short"): rej["bad_symbol_or_side"] += 1; continue
        o = int(tr["open_time"]) // MIN * MIN
        if not (w["open_t"] <= o <= w["end_t"]): rej["outside_window"] += 1; continue
        c = tr.get("close_time"); forced = c is None or int(c) >= w["force_t"]
        c = w["force_t"] if forced else int(c) // MIN * MIN
        if c <= o: rej["close_not_after_open"] += 1; continue
        ok.append({"symbol": sym, "side": side, "open_t": o, "close_t": c, "forced": forced})
    return ok, rej


def simulate(trades: list[dict], date: str, bars: Bars, mode: str, p: dict) -> tuple[list[dict], Counter]:
    """Fill trades in one mode. Enforces the per-trade $ cap and the concurrent-exposure cap."""
    done, rej, live = [], Counter(), []
    close_t = session(date)[1]
    for t in trades:
        live = [x for x in live if x[0] > t["open_t"]]                 # (close_t, cost) still open at this decision
        spot = bars.price_at(t["symbol"], t["open_t"]); xsp = bars.price_at(t["symbol"], t["close_t"])
        if spot is None or xsp is None: rej["no_bars"] += 1; continue
        long = t["side"] == "long"
        if mode == "equity":
            sl = p["slip"]; e = spot + sl if long else spot - sl; x = xsp - sl if long else xsp + sl
            n = p["cap_equity"] / e; cost = n * e                       # fractional shares: SPY/QQQ cost more than the $500 cap per share
            pnl = (x - e) * n if long else (e - x) * n
        else:
            vix_e, vix_x = ((bars.price_at("VIX", session(date)[0]),) * 2 if p["vix_daily"] else (bars.price_at("VIX", t["open_t"]), bars.price_at("VIX", t["close_t"])))
            secs = close_t - t["open_t"]; iv = om.iv_from_vix(vix_e, p["k"])
            k = om.pick_strike(spot, secs, iv, long, p["delta"])
            qe = om.quote(spot, k, secs, iv, long, p["spread_floor"], p["spread_pct"])
            qx = om.quote(xsp, k, close_t - t["close_t"], om.iv_from_vix(vix_x, p["k"]), long, p["spread_floor"], p["spread_pct"])
            n = int(p["cap"] // (qe["ask"] * 100)); cost = n * qe["ask"] * 100
            pnl = n * 100 * (qx["bid"] - qe["ask"]) - 2 * n * p["commission"]
        if (n < 1 if mode == "options" else n <= 0): rej["unaffordable"] += 1; continue
        if sum(c for _, c in live) + cost > (p["max_exposure_equity"] if mode == "equity" else p["max_exposure"]): rej["exposure_cap"] += 1; continue
        live.append((t["close_t"], cost))
        done.append({**t, "qty": round(n, 4), "cost": cost, "pnl": pnl, "underlying_in": spot, "underlying_out": xsp})
    return done, rej


def money_stats(fills: list[dict]) -> dict:
    if not fills: return {"trades": 0, "pnl": 0.0}
    pn = [f["pnl"] for f in fills]; w = [x for x in pn if x > 0]; l = [x for x in pn if x <= 0]
    cum, peak, dd = 0.0, 0.0, 0.0
    for f in sorted(fills, key=lambda f: (f["date"], f["close_t"])):
        cum += f["pnl"]; peak = max(peak, cum); dd = max(dd, peak - cum)
    return {"trades": len(fills), "pnl": round(sum(pn), 2), "win_rate": round(len(w) / len(pn), 3), "avg_win": round(st.mean(w), 2) if w else 0.0,
            "avg_loss": round(st.mean(l), 2) if l else 0.0, "profit_factor": round(sum(w) / -sum(l), 2) if l and sum(l) < 0 else None,
            "return_on_deployed": round(sum(pn) / sum(f["cost"] for f in fills), 4), "max_drawdown": round(dd, 2),
            "forced_close_rate": round(sum(f["forced"] for f in fills) / len(fills), 3),
            "avg_hold_min": round(st.mean((f["close_t"] - f["open_t"]) / MIN for f in fills), 1)}


def behaviour(sigs: list[dict], per: dict, bars_by_day: dict) -> dict:
    """Mode-independent metrics from the structurally valid trades: recall, bias, entry and exit quality."""
    cap = 0; tot = []; entry, exitq = {"vs_arrow_bps": [], "vs_window_best_bps": [], "lead_min": []}, {"capture": [], "mfe_bps": [], "mae_bps": []}
    for s in sigs:
        trs, w = per[s["id"]]["trades"], per[s["id"]]["w"]; bars = bars_by_day[s["date"]]
        if any(t["side"] == s["direction"] for t in trs): cap += 1
        for t in trs:
            tot.append((s, t))
            if t["side"] != s["direction"]: continue
            sym, long = t["symbol"], t["side"] == "long"
            e, x = bars.price_at(sym, t["open_t"]), bars.price_at(sym, t["close_t"])
            leg = s["legs"].get(sym)
            if leg: entry["vs_arrow_bps"].append(((e - leg["p0"]) if long else (leg["p0"] - e)) / leg["p0"] * 1e4)
            win = bars.between(sym, w["open_t"], w["end_t"] + MIN)
            if win:
                best = min(r[3] for r in win) if long else max(r[2] for r in win)
                entry["vs_window_best_bps"].append(((e - best) if long else (best - e)) / e * 1e4)
            entry["lead_min"].append((t["open_t"] - s["start"]) / MIN)
            hold = bars.between(sym, t["open_t"], t["close_t"])
            if hold:
                hi, lo = max(r[2] for r in hold), min(r[3] for r in hold)
                exitq["mfe_bps"].append(((hi - e) if long else (e - lo)) / e * 1e4); exitq["mae_bps"].append(((e - lo) if long else (hi - e)) / e * 1e4)
            if leg and leg["p1"] != leg["p0"]: exitq["capture"].append(((x - e) if long else (e - x)) / abs(leg["p1"] - leg["p0"]))
    med = lambda v: round(st.median(v), 2) if v else None
    n = len(tot); longs = sum(t["side"] == "long" for _, t in tot)
    return {"signals": len(sigs), "recall": round(cap / len(sigs), 3) if sigs else None, "signals_captured": cap, "structural_trades": n,
            "trades_per_signal": round(n / len(sigs), 2) if sigs else None, "signals_with_no_trade": sum(1 for s in sigs if not per[s["id"]]["trades"]),
            "bias": {"long_share": round(longs / n, 3) if n else None, "aligned_with_signal": round(sum(t["side"] == s["direction"] for s, t in tot) / n, 3) if n else None,
                     "wrong_way_trades": sum(t["side"] != s["direction"] for s, t in tot), "pre_signal_trades": sum(t["open_t"] < s["start"] for s, t in tot)},
            "entry_quality": {"median_vs_arrow_start_bps": med(entry["vs_arrow_bps"]), "median_better_entry_available_bps": med(entry["vs_window_best_bps"]),
                              "median_lead_min": med(entry["lead_min"])},
            "exit_quality": {"median_capture_ratio": med(exitq["capture"]), "median_mfe_bps": med(exitq["mfe_bps"]), "median_mae_bps": med(exitq["mae_bps"])}}


def score(doc: dict, runs: dict, bars_dir: str, modes=("options", "equity"), **kw) -> dict:
    p = {"cap": 500.0, "max_exposure": 1500.0, "cap_equity": 10000.0, "max_exposure_equity": 30000.0, "vix_daily": False, "slip": 0.01, "k": DEFAULT_K, "delta": 0.30, "spread_floor": 0.03, "spread_pct": 0.03, "commission": 0.0}; p.update(kw)
    lead, after = doc["window"]["lead_min"], doc["window"]["close_after_min"]
    sigs = doc["signals"]; bars_by_day = {d: Bars.load(bars_dir, d) for d in sorted({s["date"] for s in sigs})}
    if "options" in modes and not all(b.has("VIX") for b in bars_by_day.values()):
        sys.exit("VIX is missing for some day, and options mode will not guess an IV.\n"
                 "  - run: python fetch_vix.py --signals <signals.json> --out <bars-dir>   (needs SKYLIT_API_KEY in pckt-bench/.env; shows the credit cost first)\n"
                 "  - or add your own VIX rows to the bars files, or score without options: --modes equity")
    per, rej_struct = {}, Counter()
    for s in sigs:
        w = window(s, lead, after); trs, rj = structural(runs.get(s["id"], {}), s, w); per[s["id"]] = {"w": w, "trades": trs}; rej_struct.update(rj)
    out = {"slice": doc["slice"], "params": p, "runs_found": sum(1 for s in sigs if s["id"] in runs), "signals": len(sigs),
           "rejected_structural": dict(rej_struct), "behaviour": behaviour(sigs, per, bars_by_day), "modes": {}}
    for m in modes:
        fills, rej = [], Counter()
        for s in sigs:
            f, r = simulate(per[s["id"]]["trades"], s["date"], bars_by_day[s["date"]], m, p); rej.update(r)
            fills += [{**x, "date": s["date"], "signal": s["id"]} for x in f]
        out["modes"][m] = {**money_stats(fills), "rejected": dict(rej)}
    return out


def report(r: dict) -> str:
    b = r["behaviour"]; L = [f"# pckt-bench results: {r['slice']}", "", f"Signals {r['signals']} (runs found {r['runs_found']}). Headline = options; equity is the model-free cross-check.", ""]
    L += ["| mode | trades | PnL $ | win rate | avg win | avg loss | PF | return on deployed | max DD $ | forced-close | rejected |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for m, x in r["modes"].items():
        L.append(f"| {m} | {x['trades']} | {x['pnl']} | {x.get('win_rate','-')} | {x.get('avg_win','-')} | {x.get('avg_loss','-')} | {x.get('profit_factor','-')} | {x.get('return_on_deployed','-')} | {x.get('max_drawdown','-')} | {x.get('forced_close_rate','-')} | {x['rejected']} |")
    L += ["", f"- Recall: {b['signals_captured']}/{b['signals']} ({b['recall']}) | trades per signal {b['trades_per_signal']} | signals with no trade {b['signals_with_no_trade']}",
          f"- Bias: {b['bias']}", f"- Entry quality: {b['entry_quality']}", f"- Exit quality: {b['exit_quality']}", f"- Structural rejections: {r['rejected_structural']}"]
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--signals", required=True); ap.add_argument("--trades", required=True); ap.add_argument("--bars-dir", required=True)
    ap.add_argument("--out", default="results"); ap.add_argument("--modes", default="options,equity")
    ap.add_argument("--k", type=float, default=DEFAULT_K, help="IV = VIX/100 * k"); ap.add_argument("--max-exposure", type=float, default=1500.0, help="options mode, $ of entry cost open at once")
    ap.add_argument("--max-exposure-equity", type=float, default=30000.0)
    ap.add_argument("--vix-daily", action="store_true", help="use the day's opening VIX for every trade (no minute VIX needed)")
    ap.add_argument("--delta", type=float, default=0.30); ap.add_argument("--commission", type=float, default=0.0, help="$ per contract per side")
    a = ap.parse_args()
    doc = load_signals(a.signals); runs = {r["signal_id"]: r for r in json.loads(Path(a.trades).read_text(encoding="utf-8"))["runs"]}
    r = score(doc, runs, a.bars_dir, tuple(a.modes.split(",")), k=a.k, max_exposure=a.max_exposure, max_exposure_equity=a.max_exposure_equity, vix_daily=a.vix_daily, delta=a.delta, commission=a.commission)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    (out / "metrics.json").write_text(json.dumps(r, indent=1), encoding="utf-8"); (out / "report.md").write_text(report(r), encoding="utf-8")
    print(report(r))


if __name__ == "__main__": main()
