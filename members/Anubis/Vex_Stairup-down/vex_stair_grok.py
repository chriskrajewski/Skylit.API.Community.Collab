"""
VEX STAIR-STEP DETECTOR — for Grok
==================================

INSTRUCTIONS FOR THE BOT
------------------------
You are given this file as a tool. Use it to tell whether a ticker's VEX (dealer vanna exposure)
book forms a STAIR-STEP UP, a STAIR-STEP DOWN, or NO STAIR across its option expiries.

  1. Get the ticker's vanna book by calling load_vanna_book(symbol). It returns a VannaBook:
       spot         – the underlying price the book was built at
       session      – the trading date of the book, 'YYYY-MM-DD'
       expirations  – expiry dates, nearest first, 'YYYY-MM-DD'
       strikes      – strike prices, ascending
       vanna        – vanna[i][j] = dollar vanna at strikes[i] for expirations[j] (un-summed, per expiry)
  2. Call detect_vex_stair(book).
  3. Report: the direction (UP / DOWN / NONE), then every step as "strike (expiry)", near → far,
     then the caveat below. Never turn the stair into a buy or sell call.

THE RULE
--------
  * Look only at strikes within ±50% of spot and expiries 2–120 calendar days after the session.
  * The board's vanna KING = the largest |vanna| cell in that window.
  * An expiry is MAJOR when its own biggest |vanna| cell is ≥ 25% of the King (thin expiries are dropped).
  * A STEP = the strike of each major expiry's dominant node, near expiry → far expiry.
  * UP   = Spearman rank correlation (expiry order vs step strike) ≥ +0.6 AND last step ≥ 3% of spot above the first.
    DOWN = ≤ −0.6 AND last step ≥ 3% of spot below the first. Fewer than 3 major expiries = no read.
  * Two reads: the dominant POSITIVE node (read first) and the dominant node of EITHER sign.
    If they point opposite ways, report NONE (conflict).

CAVEAT (always report it)
-------------------------
Detection is reliable; prediction is not. On 253 names over Jan–Sep 2026 (~2,700 stairs), price moved in
the stair's direction (vs the day's average) 49.3% of the time over 5 sessions and 48.0% over 10 — a coin
flip. The vanna map follows price, so a stair shows where positioning has been, not where price goes.
Only 41–47% of up-stairs and 31% of down-stairs still exist 5 sessions later.
"""
from dataclasses import dataclass
from datetime import date
from typing import List, Optional

RULE = {"window": 0.50, "dte_min": 2, "dte_max": 120, "major_share": 0.25, "rho": 0.6, "net": 0.03, "min_steps": 3}


@dataclass
class VannaBook:
    spot: float
    session: str
    expirations: List[str]
    strikes: List[float]
    vanna: List[List[float]]  # vanna[strike_index][expiry_index]


def load_vanna_book(symbol: str) -> VannaBook:
    """PLUG YOUR DATA SOURCE IN HERE. Return the per-expiry vanna book for `symbol` as a VannaBook."""
    raise NotImplementedError("connect load_vanna_book() to your own data source")


def _ranks(a):
    order = sorted(range(len(a)), key=lambda i: a[i])
    r = [0.0] * len(a)
    i = 0
    while i < len(a):
        j = i
        while j + 1 < len(a) and a[order[j + 1]] == a[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2
        i = j + 1
    return r


def _spearman(x, y):
    rx, ry = _ranks(x), _ranks(y)
    n = len(x)
    mx, my = sum(rx) / n, sum(ry) / n
    sx = sum((a - mx) ** 2 for a in rx) ** 0.5
    sy = sum((b - my) ** 2 for b in ry) ** 0.5
    if sx == 0 or sy == 0:
        return 0.0
    return sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / (sx * sy)


def _read(book, cols, rows, king, positive_only):
    steps = []
    for j, exp, dte in cols:
        best = None
        for i in rows:
            v = book.vanna[i][j] or 0.0
            if positive_only:
                if v > 0 and (best is None or v > best[1]):
                    best = (book.strikes[i], v)
            elif v and (best is None or abs(v) > abs(best[1])):
                best = (book.strikes[i], v)
        if best and abs(best[1]) >= RULE["major_share"] * king:
            steps.append({"strike": best[0], "expiry": exp, "dte": dte, "vanna": best[1],
                          "pct_of_king": round(abs(best[1]) / king * 100)})
    if len(steps) < RULE["min_steps"]:
        return {"direction": None, "steps": steps, "rho": None, "net_pct": None, "too_few": True}
    ks = [s["strike"] for s in steps]
    rho = _spearman(list(range(len(ks))), ks)
    net = (ks[-1] - ks[0]) / book.spot
    d = "UP" if rho >= RULE["rho"] and net >= RULE["net"] else "DOWN" if rho <= -RULE["rho"] and net <= -RULE["net"] else None
    return {"direction": d, "steps": steps, "rho": round(rho, 2), "net_pct": round(net * 100, 1), "too_few": False}


def detect_vex_stair(book: VannaBook) -> Optional[dict]:
    """Return {'direction': 'UP'|'DOWN'|'NONE', 'read': ..., 'steps': [...], 'path': 'strike (expiry) → ...', ...}."""
    if not book or not book.spot or book.spot <= 0 or not book.strikes or not book.expirations:
        return None
    d0 = date.fromisoformat(book.session)
    cols = []
    for j, e in enumerate(book.expirations):
        dte = (date.fromisoformat(e) - d0).days
        if RULE["dte_min"] <= dte <= RULE["dte_max"]:
            cols.append((j, e, dte))
    rows = [i for i, k in enumerate(book.strikes) if abs(k / book.spot - 1) <= RULE["window"]]
    if not cols or not rows:
        return None
    king = max(abs(book.vanna[i][j] or 0.0) for j, _, _ in cols for i in rows)
    if king <= 0:
        return None
    pos, anyr = _read(book, cols, rows, king, True), _read(book, cols, rows, king, False)
    conflict = bool(pos["direction"] and anyr["direction"] and pos["direction"] != anyr["direction"])
    main = None if conflict else pos if pos["direction"] else anyr if anyr["direction"] else None
    shown = main or (pos if not pos["too_few"] else anyr)
    return {
        "direction": main["direction"] if main else "NONE",
        "read": ("positive node" if main is pos else "either-sign node") if main else ("conflict" if conflict else "no stair"),
        "rho": shown["rho"], "net_pct": shown["net_pct"], "steps": shown["steps"],
        "path": " → ".join(f'{s["strike"]:g} ({s["expiry"]})' for s in shown["steps"]),
        "positive_read": pos["direction"] or "NONE", "either_sign_read": anyr["direction"] or "NONE",
        "king_vanna": king,
        "caveat": "Display only: a stair did not predict direction (49.3% / 48.0% at 5 / 10 sessions, 253 names, 2026).",
    }


def detect_for_symbol(symbol: str) -> Optional[dict]:
    return detect_vex_stair(load_vanna_book(symbol))


if __name__ == "__main__":
    # Self-test on an ILLUSTRATIVE book (made-up numbers): the dominant +vanna node climbs 100 → 105 → 110 → 115.
    demo = VannaBook(spot=100.0, session="2026-10-01",
                     expirations=["2026-10-09", "2026-10-16", "2026-11-20", "2026-12-18"],
                     strikes=[95, 100, 105, 110, 115],
                     vanna=[[1e6, 0, 0, 0], [9e6, 1e6, 0, 0], [0, 9e6, 1e6, 0], [0, 0, 9e6, 1e6], [0, 0, 0, 9e6]])
    r = detect_vex_stair(demo)
    print(r["direction"], "|", r["path"], "| rho", r["rho"], "| net", r["net_pct"], "%")
