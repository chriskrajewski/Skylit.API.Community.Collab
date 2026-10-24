"""Day plot: three stacked charts (SPX, SPY, QQQ): 3-min candles + GEX nodes (yellow +, purple -; opacity/size = strength vs that
snapshot's King Node) + entry arrows: UP triangle = calls (LONG), DOWN triangle = puts (SHORT); GREEN = profitable, RED = not.
  python plots/plot_day.py --day 2026-07-29 [--trades replay/2026-07-29/state.json] [--out plots/out/2026-07-29.png]
Trades file: replay `state.json` (key 'trades') or a paper ledger `trades.json` (list). Fields: sym, side, entry_time, exit_time, entry, exit,
and pnl (ledger) or R (replay) to decide win/loss.  Data: data_skylit NPZ (nodes) + ibkr_data 1-min bars (candles)."""
from __future__ import annotations
import argparse, json
from pathlib import Path
import numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import to_rgba
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT.parent / "data"
TZ = "America/New_York"
HM = {"SPX": "SPXW", "SPY": "SPY", "QQQ": "QQQ"}
BG, PANEL, FG = "#0b0b0b", "#141414", "#b9bec6"
WIN, LOSS = "#2ecc71", "#e74c3c"
WIN_PART = "#a3e4bc"          # a winning trade that was not a full win (v4: +30% on half, the rest at breakeven): lighter green


NODES_OFF = False
LIVE_DIR = None   # paper/live/<day> for a live day: snap_HHMMSS.json (Skylit heatmaps) + bars_<SYM>.json (raw IBKR MCP 1-min history)
BARS_DIR = None   # where bars_<SYM>.json live when not in LIVE_DIR (v3 keeps its bars in v3/live/<day>/)


_BARS1 = {}


def exit_underlying(sym, day, tr):
    """Underlying level at a LEDGER exit (the ledger stores option prices only). Uses the native (1-min) bars:
    TP exit (time = bar END of the minute the option hit its TP) -> that minute's favourable extreme (call: high, put: low);
    manual / EOD exit (time = the wake) -> mid of the open and close of the minute containing it. Accurate to about a minute."""
    d = _BARS1.get((sym, day))
    if d is None or d.empty: return None
    et = pd.Timestamp(f"{day} {tr['exit_time']}", tz=TZ)
    long = tr.get("side") == "LONG"
    if tr.get("why") == "tp":
        r = d[d.index == et - pd.Timedelta(minutes=1)]
        if len(r): return float(r.high.iloc[0] if long else r.low.iloc[0])
    r = d[d.index == et]
    if not len(r): r = d[d.index <= et].tail(1)
    return float((r.open.iloc[0] + r.close.iloc[0]) / 2) if len(r) else None


def bars(sym, day):
    live = Path(BARS_DIR or LIVE_DIR) / f"bars_{sym}.json" if (BARS_DIR or LIVE_DIR) else None
    if live is not None and live.exists():
        j = json.loads(live.read_text())
        j = j.get("data", j)
        df = pd.DataFrame({k: j[k] for k in ("open", "high", "low", "close")}, index=pd.to_datetime(j["time"], utc=True).tz_convert(TZ))
        df.index.name = "ts"
    else:
        df = pd.read_parquet(DATA / "ibkr_data" / f"{sym}_1min.parquet")
        df["ts"] = df["ts"].dt.tz_convert(TZ)
        df = df.set_index("ts").sort_index()
    d = df[(df.index.strftime("%Y-%m-%d") == day)].between_time("09:30", "15:59")
    _BARS1[(sym, day)] = d   # keep the native bars: exit markers use the exit minute, not the 3-min close
    return d.resample("3min").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


def snapshots(sym, day, data_dir):
    if NODES_OFF:
        return pd.DatetimeIndex([]), [], []
    f = Path(data_dir) / HM[sym] / f"{day.replace('-', '')}.npz"
    if not f.exists() and LIVE_DIR:                       # live day: build the node series from the saved live snapshots
        ts, ks, gs = [], [], []
        for sf in sorted(Path(LIVE_DIR).glob("snap_*.json")):
            snap = json.loads(sf.read_text()).get(HM[sym])
            if not snap: continue
            rows = sorted((int(float(k)), v["gex0"]) for k, v in snap["strikes"].items())
            ts.append(pd.Timestamp(f"{day} {sf.stem[5:7]}:{sf.stem[7:9]}:{sf.stem[9:11]}", tz=TZ))
            ks.append(np.array([r[0] for r in rows])); g = np.zeros((len(rows), 5)); g[:, 0] = [r[1] for r in rows]; gs.append(g)
        return pd.DatetimeIndex(ts), ks, gs                  # may be empty: the panel then says "no node data"
    if not f.exists():
        return pd.DatetimeIndex([]), [], []                   # no heatmap data for this day: candles and trades only
    d = np.load(Path(data_dir) / HM[sym] / f"{day.replace('-', '')}.npz", allow_pickle=True)
    return pd.to_datetime(d["timestamps"], utc=True).tz_convert(TZ), d["strikes"], d["gex"]


def load_trades(path):
    if not path: return []
    j = json.loads(Path(path).read_text())
    return j["trades"] if isinstance(j, dict) else j


def load_unfilled(path):
    """Orders that were placed but never filled (expired, cancelled, or cancelled by the other side of a bracket).
    Replay: <folder>/log.jsonl next to state.json.  Live ledger: events.jsonl next to trades.json."""
    if not path: return []
    d = Path(path).parent
    out = []
    if (d / "log.jsonl").exists():                       # replay harness
        state = json.loads(Path(path).read_text())
        filled = {t["id"] for t in state["trades"]}
        import re
        for line in (d / "log.jsonl").read_text().splitlines():
            r = json.loads(line)
            if r.get("plan", {}).get("decision") != "PLACE_LIMIT" or not r.get("result", {}).get("accepted"): continue
            m = re.search(r"order (O\d+)", " ".join(r["result"].get("messages", [])))
            tp = r["plan"]["trade_plan"]
            if m and m.group(1) in filled: continue
            ttl = min(int(tp.get("order_ttl_minutes", 12)), 12)
            out.append(dict(sym=tp["instrument"], side=tp["direction"], time=r["t"], price=tp["entry_limit_underlying"], ttl=ttl))
    elif (d / "events.jsonl").exists():                  # live ledger
        evs = [json.loads(l) for l in (d / "events.jsonl").read_text().splitlines() if l.strip()]
        filled = {e["id"] for e in evs if e["kind"] == "fill"}
        for e in evs:
            if e["kind"] == "order" and e["id"] not in filled:
                pl = e["plan"]
                h, m = map(int, e["t"].split(":")); h2, m2 = map(int, e["ttl_end"].split(":"))
                out.append(dict(sym=e["sym"], side="LONG" if e["type"] == "CALL" else "SHORT", time=e["t"], price=pl["und_entry"], ttl=(h2 * 60 + m2) - (h * 60 + m),
                                 id=e["id"]))
    return out


def is_win(tr):
    v = tr.get("pnl", tr.get("R", 0))
    return (v or 0) > 0


NEUTRAL = "#aeb4bd"


def panel(ax, sym, day, trades, data_dir, unfilled=()):
    c = bars(sym, day)
    if c.empty:
        ax.set_title(f"{sym}: no bars"); return
    t, strikes, gex = snapshots(sym, day, data_dir)
    idx = {ts: i for i, ts in enumerate(c.index)}
    lo, hi = c.low.min(), c.high.max()
    pad = (hi - lo) * 0.12 + 1e-9
    ylo, yhi = lo - pad, hi + pad
    px, py, pc, ps = [], [], [], []
    ti = 0
    covered = 0
    for ts, i in idx.items():
        while ti + 1 < len(t) and t[ti + 1] <= ts: ti += 1
        if NODES_OFF or t[ti] > ts or (ts - t[ti]) > pd.Timedelta(minutes=5):   # draw nodes ONLY where heatmap data was actually recorded
            continue
        covered += 1
        k = np.asarray(strikes[ti]); g = np.asarray(gex[ti], float)[:, 0]
        king = np.abs(g).max() or 1.0
        m = (k >= ylo) & (k <= yhi) & (np.abs(g) / king > 0.05)
        for kk, gg in zip(k[m], g[m]):
            r = abs(gg) / king
            px.append(i); py.append(kk); pc.append(to_rgba("#f2c230" if gg > 0 else "#8a2be2", min(1.0, 0.10 + 0.85 * r ** 1.2))); ps.append(5 + 26 * r)
    ax.scatter(px, py, c=pc, s=ps, marker="o", linewidths=0, zorder=1)
    cov = covered / max(len(idx), 1)
    if cov < 0.97:
        ax.text(0.995, 0.015, "no node data" if covered == 0 else f"nodes shown where heatmap data was recorded ({cov * 100:.0f}% of the session)",
                transform=ax.transAxes, ha="right", va="bottom", color="#9aa0a6", fontsize=8, zorder=10)
    for i, (ts, r) in enumerate(c.iterrows()):
        up = r.close >= r.open
        col = "#8fa8d0" if up else "#2c3e5c"
        ax.vlines(i, r.low, r.high, color=col, lw=0.9, zorder=3)
        ax.add_patch(Rectangle((i - 0.33, min(r.open, r.close)), 0.66, max(abs(r.close - r.open), (hi - lo) * 0.0008), color=col, zorder=4))
    for tr in trades:
        et = pd.Timestamp(f"{day} {tr['entry_time']}", tz=TZ).floor("3min")
        xt = idx.get(et) if et in idx else min(idx.values(), key=lambda j: abs(c.index[j] - et))
        if tr.get("sym") == sym:
            long = tr["side"] == "LONG"
            clr = {"win": WIN, "partial": WIN_PART, "loss": LOSS}.get(tr.get("result")) or (WIN if is_win(tr) else LOSS)
            ledger = "und_entry" in tr                       # live ledger trade: entry/exit are OPTION prices; und_entry is the underlying level
            ent = tr["und_entry"] if ledger else tr["entry"]
            et2 = pd.Timestamp(f"{day} {tr['exit_time']}", tz=TZ).floor("3min")
            xt2 = idx.get(et2, xt)
            ext = (exit_underlying(sym, day, tr) or float(c.close.iloc[xt2])) if ledger else tr["exit"]
            ax.scatter([xt], [ent], marker="^" if long else "v", s=230, color=clr, edgecolor="white", linewidths=0.8, zorder=8)
            ax.plot([xt, xt2], [ent, ext], color=clr, lw=1.0, ls="--", alpha=0.8, zorder=6)
            ax.scatter([xt2], [ext], marker="x", s=55, color=clr, linewidths=1.6, zorder=7)
            if ledger:
                lab = (f"{'CALL' if long else 'PUT'} {tr['strike']} x{tr['qty']} @ {tr['entry']:.2f} ({tr['entry_time']})\n"
                       f"-> {tr['exit']:.2f} ({tr['exit_time']}, {tr.get('why', '')})\n{tr['pnl']:+.2f} USD ({tr['pnl_pct']:+.0f}%)")
            else:
                pct = tr.get("option_pct_est_remaining_leg")
                lab = f"{'CALL' if long else 'PUT'} {tr['entry']:.2f} @ {tr['entry_time']}\n-> {tr['exit']:.2f} @ {tr['exit_time']} ({tr.get('why', '')})"
                lab += f"\n{tr['R']:+.2f}R" if tr.get("R") is not None else ""
                lab += f"  option {pct:+.0f}%" if pct is not None else ""
            ax.annotate(lab, (xt, ent), xytext=(14, 26 if not long else -44), textcoords="offset points", color=clr, fontsize=7.5,
                        bbox=dict(boxstyle="round,pad=0.25", fc=PANEL, ec=clr, lw=0.8), arrowprops=dict(arrowstyle="-", color=clr, lw=0.6), zorder=9)
        else:
            ax.axvline(xt, color="#4a90d9", lw=0.6, ls=":", alpha=0.7, zorder=2)
    for u in unfilled:
        if u["sym"] != sym: continue
        st = pd.Timestamp(f"{day} {u['time']}", tz=TZ)
        x0 = min(idx.values(), key=lambda j: abs(c.index[j] - st.floor("3min")))
        x1 = x0 + max(u["ttl"], 3) / 3.0
        long = u["side"] == "LONG"
        ax.plot([x0, x1], [u["price"], u["price"]], color="#d1d5db", lw=1.2, ls=":", alpha=0.9, zorder=5)
        ax.scatter([x0], [u["price"]], marker="^" if long else "v", s=170, facecolor="white", edgecolor="#6b7280", linewidths=1.8, zorder=7)
    ax.set_ylim(ylo, yhi)
    ax.set_xlim(-1, len(c) + 1)
    ticks = [i for i, ts in enumerate(c.index) if (ts.minute == 0 or ts.minute == 30) and ts.second == 0]
    ax.set_xticks(ticks)
    ax.set_xticklabels([c.index[i].strftime("%H:%M") for i in ticks], color=FG, fontsize=8)
    ax.tick_params(axis="y", colors=FG, labelsize=8)
    ax.set_facecolor(PANEL)
    ax.grid(color="#222", lw=0.4, zorder=0)
    for s in ax.spines.values(): s.set_color("#2a2a2a")
    ax.set_ylabel(sym, color=FG)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True); ap.add_argument("--trades"); ap.add_argument("--out")
    ap.add_argument("--data-dir", default=str(DATA / "data_skylit")); ap.add_argument("--symbols", default="SPX,SPY,QQQ")
    ap.add_argument("--title", default="")
    ap.add_argument("--no-nodes", action="store_true", help="candles and trades only")
    ap.add_argument("--live-dir", help="paper/live/<day> (live snapshots + bars_<SYM>.json) for a live day")
    ap.add_argument("--bars-dir", help="folder with bars_<SYM>.json when it is not --live-dir (v3)")
    a = ap.parse_args()
    global LIVE_DIR, NODES_OFF, BARS_DIR
    LIVE_DIR, BARS_DIR = a.live_dir, a.bars_dir
    NODES_OFF = a.no_nodes
    syms = a.symbols.split(",")
    trades = load_trades(a.trades)
    unfilled = load_unfilled(a.trades)
    fig, axes = plt.subplots(len(syms), 1, figsize=(20, 5.1 * len(syms)), facecolor=BG)
    for ax, s in zip(np.atleast_1d(axes), syms): panel(ax, s, a.day, trades, a.data_dir, unfilled)
    n_win = sum(is_win(t) for t in trades)
    n_unf = len({u.get('id', i) for i, u in enumerate(unfilled)})
    res = {t["id"]: t["result"] for t in trades if t.get("result") and t.get("id")}      # v4: one result per trade, not per piece
    if res:
        n_full, n_part = sum(r == "win" for r in res.values()), sum(r == "partial" for r in res.values())
        head = f"{len(res)} trade(s), {n_full + n_part} winners ({n_part} smaller: +30% then breakeven), {len(res) - n_full - n_part} losers"
    else:
        head = f"{len(trades)} filled trade(s), {n_win} profitable"
    fig.suptitle(f"{a.day}  {a.title}  |  {head}, {n_unf} order(s) not filled", color="white", fontsize=14)
    leg = [Line2D([0], [0], marker="o", color="none", markerfacecolor="#f2c230", label="Positive GEX node (0DTE)"),
           Line2D([0], [0], marker="o", color="none", markerfacecolor="#8a2be2", label="Negative GEX node (0DTE)"),
           Line2D([0], [0], marker="^", color="none", markerfacecolor=WIN, markeredgecolor="white", markersize=11, label="Call entry, profitable"),
           Line2D([0], [0], marker="^", color="none", markerfacecolor=LOSS, markeredgecolor="white", markersize=11, label="Call entry, loss"),
           Line2D([0], [0], marker="v", color="none", markerfacecolor=WIN, markeredgecolor="white", markersize=11, label="Put entry, profitable"),
           Line2D([0], [0], marker="v", color="none", markerfacecolor=LOSS, markeredgecolor="white", markersize=11, label="Put entry, loss"),
           Line2D([0], [0], marker="o", color="none", markerfacecolor=WIN_PART, markeredgecolor="white", markersize=10, label="Smaller win (+30% on half, rest at breakeven)"),
           Line2D([0], [0], marker="x", color=FG, linestyle="none", label="Exit"),
           Line2D([0], [0], marker="^", color="none", markerfacecolor="white", markeredgecolor="#6b7280", markeredgewidth=1.6, markersize=11, label="Order not filled (price it waited for; up = call, down = put)")]
    fig.legend(handles=leg, loc="lower center", ncol=4, facecolor=PANEL, edgecolor="#333", labelcolor=FG, fontsize=9)
    fig.tight_layout(rect=(0, 0.06, 1, 0.97))
    out = Path(a.out or ROOT / "plots" / "out" / f"{a.day}.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".tmp.png")            # write then replace: avoids Windows file-lock errors when the old image is open in a viewer
    fig.savefig(tmp, dpi=100, facecolor=BG)
    import os, time
    for _ in range(5):
        try:
            os.replace(tmp, out); break
        except OSError:
            time.sleep(1)
    else:
        out = tmp
    print(out)


if __name__ == "__main__":
    main()
