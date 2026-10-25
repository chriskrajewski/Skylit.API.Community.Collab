"""Example converter: Timmy v4's book log -> the two files plot_day.py reads (trades.json + events.jsonl).
This is how Timmy v4 feeds the chart. Your book will look different; keep the OUTPUT shapes (see README.md) and
replace the input side. lifecycle() and v2_ledger() are copied from Timmy v4's review.py (v2_ledger now takes the event list, not a folder).
    python v4_to_chart_inputs.py --book-events path/to/book_events.jsonl --out-dir path/to/eod/
v4's book_events.jsonl has one JSON object per line, with "kind" one of:
  place   {t, id, direction: long|short, vehicle: SPY|QQQ|SPX, legs: [{und, ...}], contract: {strike, ...}}
  reprice / keep / edit / rewind / restore   {t, id, legs: [...]}   (a resting order moved or kept)
  fill    {t, id, leg, qty, px (option price), und (underlying at the fill)}
  cancel  {t, id, why}
  sell    {t, id, qty, avg, exit (option price), time "HH:MM", why, pnl (USD, after commission), pnl_pct, strike}
"""
from __future__ import annotations
import argparse, json
from pathlib import Path


def _jsonl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []


def lifecycle(events: list[dict]) -> dict:
    """Per order: its place record, leg segments [(leg, und, start, end, filled_at)], fills, sells, how it ended.
    Legs are tracked by their UNDERLYING price, not their position in the order (2026-10-09: A6 went from 2 legs to 3 at
    11:20 and back by hand at 11:28; by position the lines crossed). `edit` / `rewind` (by-hand changes) count as a
    re-price; `restore` re-opens a cancelled order (A10, A8 on 10-09)."""
    orders = {}

    def relegs(r, legs, t):
        want = {}
        for i, l in enumerate(legs):
            if not l.get("filled"): want.setdefault(round(float(l["und"]), 2), i)
        for und, (i, t0) in list(r["cur"].items()):
            if und not in want: r["segs"].append((i, und, t0, t, None)); del r["cur"][und]
        for und, i in want.items():
            if und not in r["cur"]: r["cur"][und] = (i, t)
            else: r["cur"][und] = (i, r["cur"][und][1])

    for e in events:
        k, oid, t = e["kind"], e.get("id"), e["t"][:5]
        if k == "place":
            orders[oid] = dict(o=e, segs=[], cur={round(float(l["und"]), 2): (i, t) for i, l in enumerate(e["legs"])},
                               fills=[], sells=[], end=None, end_why=None)
        elif oid not in orders:
            continue
        elif k in ("keep", "reprice", "edit", "rewind", "restore"):
            r = orders[oid]
            if k == "restore" and r["end"]:                      # undo the cancel: the legs never stopped working
                n = len(r.pop("cancelled_segs", []))
                if n: del r["segs"][-n:]
                r["cur"], r["end"], r["end_why"] = r.pop("cancelled_cur", {}), None, None
            if e.get("legs"): relegs(r, e["legs"], t)
        elif k == "fill":
            r = orders[oid]; und = round(float(e["und"]), 2)
            i, t0 = r["cur"].pop(und, (e["leg"] - 1, t))
            r["segs"].append((i, und, t0, t, t)); r["fills"].append(e)
        elif k == "cancel":
            r = orders[oid]; r["end"], r["end_why"] = t, e.get("why", "")
            ended = [(i, und, t0, t, None) for und, (i, t0) in r["cur"].items()]
            r["segs"] += ended; r["cancelled_segs"], r["cancelled_cur"] = ended, dict(r["cur"])
            r["cur"] = {}
        elif k == "sell":
            orders[oid]["sells"].append(e)
    for r in orders.values():
        r.pop("cancelled_segs", None); r.pop("cancelled_cur", None)                                   # still open at the end of the log
        for und, (i, t0) in r["cur"].items(): r["segs"].append((i, und, t0, "16:00", None))
    return orders


def v2_ledger(events: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """v4's book in v2's ledger shapes: (chart trades, unfilled-order events, summary trades).
    Chart trades: one per (fill, sell) piece, matched first-in first-out, so each entry arrow sits at its own fill
    (time, underlying) and the pieces' P&L adds up to the sells'. Unfilled: every leg segment of an order that never
    filled, ending when it was repriced, cancelled, or the log ended. Summary trades: one per sell, as v2 listed them."""
    last = max((e["t"][:5] for e in events), default="16:00")
    end_of_log = last if last < "15:55" else "16:00"
    chart, unfilled, summary = [], [], []
    for oid, r in lifecycle(events).items():
        o, c = r["o"], r["o"].get("contract") or {}
        typ = "CALL" if o["direction"] == "long" else "PUT"
        base = dict(sym=o["vehicle"], strike=c.get("strike"), type=typ, side="LONG" if typ == "CALL" else "SHORT")
        if not r["fills"]:
            for i, und, t0, t1, _ in r["segs"]:
                unfilled.append(dict(kind="order", id=oid, sym=o["vehicle"], type=typ, t=t0,
                                     ttl_end=end_of_log if t1 == "16:00" else t1, plan=dict(und_entry=und)))
            continue
        fills = [dict(f, left=f["qty"]) for f in r["fills"]]
        n0 = len(chart)
        for sl in r["sells"]:
            why = str(sl.get("why", "")).split(":")[0].replace("engine", "agent_exit")
            comm = (sl["pnl"] - (sl["exit"] - sl["avg"]) * sl["qty"] * 100) / sl["qty"]   # per contract
            base = dict(base, strike=sl.get("strike", base["strike"]))             # a reprice can move the strike
            summary.append(dict(base, qty=sl["qty"], pnl=sl["pnl"], pnl_pct=sl["pnl_pct"], why=why))
            need = sl["qty"]
            for f in fills:
                q = min(need, f["left"])
                if q <= 0: continue
                f["left"] -= q; need -= q
                chart.append(dict(base, id=oid, qty=q, entry=f["px"], entry_time=f["t"][:5], und_entry=f["und"],
                                  exit=sl["exit"], exit_time=sl["time"], why=why,
                                  pnl=round((sl["exit"] - f["px"]) * q * 100 + comm * q, 2),
                                  pnl_pct=round((sl["exit"] / f["px"] - 1) * 100, 1)))
        # the whole trade's result colours every piece (user 2026-10-16): +30% on half then breakeven on the rest is a
        # smaller WIN ("partial", light green), not a red loser for its breakeven piece
        tot = sum(sl["pnl"] for sl in r["sells"])
        res = "loss" if tot <= 0 else "win" if all(sl["pnl"] > 0 for sl in r["sells"]) else "partial"
        for x in chart[n0:]: x.update(result=res, trade_pnl=round(tot, 2))
    return chart, unfilled, summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--book-events", required=True); ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    chart, unfilled, _ = v2_ledger(_jsonl(Path(a.book_events)))
    e = Path(a.out_dir); e.mkdir(parents=True, exist_ok=True)
    (e / "trades.json").write_text(json.dumps(chart, indent=1), encoding="utf-8")
    (e / "events.jsonl").write_text("".join(json.dumps(u) + "\n" for u in unfilled), encoding="utf-8")
    print(e / "trades.json")


if __name__ == "__main__":
    main()
