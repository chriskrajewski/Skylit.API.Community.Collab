"""
VEX STAIR-STEP DETECTOR — for an OpenAI assistant (function calling / custom GPT action code)
=============================================================================================

HOW THE ASSISTANT USES THIS
---------------------------
TOOL_SPEC below is the function definition to register with the model. When the user asks whether a
ticker's VEX (dealer vanna exposure) forms a stair-step, the model calls `detect_vex_stair` with
{"symbol": "..."}; your app runs handle_tool_call(), which loads the book through load_vanna_book()
and returns the result for the model to explain.

The model must answer with: the direction (UP / DOWN / NONE), every step as "strike (expiry)" near → far,
and the caveat. It must never turn the stair into a buy or sell call.

THE RULE
--------
  * Strikes within ±50% of spot; expiries 2–120 calendar days after the book's session date.
  * Vanna KING = the largest |vanna| cell in that window.
  * MAJOR expiry = its own biggest |vanna| cell is ≥ 25% of the King.
  * STEP = the strike of each major expiry's dominant node, nearest expiry first.
  * UP = Spearman ρ(expiry order, step strike) ≥ +0.6 and last step ≥ 3% of spot above the first;
    DOWN mirrors it; fewer than 3 major expiries = no read.
  * Read the dominant POSITIVE node first, then the dominant node of EITHER sign; if they disagree → NONE.

CAVEAT
------
Detection is reliable; prediction is not: 253 names, Jan–Sep 2026 (~2,700 stairs) — price went the stair's
way (vs the day's average) 49.3% over 5 sessions and 48.0% over 10. A stair shows where positioning has
been, not where price goes; most stairs dissolve within a week.
"""
from datetime import date

RULE = {"window": 0.50, "dte_min": 2, "dte_max": 120, "major_share": 0.25, "rho": 0.6, "net": 0.03, "min_steps": 3}

TOOL_SPEC = {
    "type": "function",
    "function": {
        "name": "detect_vex_stair",
        "description": ("Detect whether a ticker's dealer vanna (VEX) book forms a stair-step UP or DOWN across option "
                        "expiries. Returns the direction, every step as strike + expiry, and a caveat that the pattern "
                        "is descriptive and has not predicted price direction."),
        "parameters": {
            "type": "object",
            "properties": {"symbol": {"type": "string", "description": "Ticker symbol, e.g. NVDA"}},
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
}


def load_vanna_book(symbol):
    """PLUG YOUR DATA SOURCE IN HERE.
    Return a dict: spot (float), session ('YYYY-MM-DD'), expirations (list of 'YYYY-MM-DD', nearest first),
    strikes (ascending floats), vanna (vanna[i][j] = dollar vanna at strikes[i] for expirations[j])."""
    raise NotImplementedError("connect load_vanna_book() to your own data source")


def _spearman(x, y):
    def ranks(a):
        o = sorted(range(len(a)), key=lambda i: a[i]); r = [0.0] * len(a); i = 0
        while i < len(a):
            j = i
            while j + 1 < len(a) and a[o[j + 1]] == a[o[i]]:
                j += 1
            for k in range(i, j + 1):
                r[o[k]] = (i + j) / 2
            i = j + 1
        return r
    rx, ry = ranks(x), ranks(y); n = len(x)
    mx, my = sum(rx) / n, sum(ry) / n
    sx = sum((a - mx) ** 2 for a in rx) ** 0.5; sy = sum((b - my) ** 2 for b in ry) ** 0.5
    return 0.0 if sx == 0 or sy == 0 else sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / (sx * sy)


def detect_vex_stair(book):
    spot, strikes, exps, V = book["spot"], book["strikes"], book["expirations"], book["vanna"]
    if not spot or spot <= 0 or not strikes or not exps:
        return {"direction": "NONE", "read": "no book"}
    d0 = date.fromisoformat(book["session"])
    cols = [(j, e, (date.fromisoformat(e) - d0).days) for j, e in enumerate(exps)]
    cols = [c for c in cols if RULE["dte_min"] <= c[2] <= RULE["dte_max"]]
    rows = [i for i, k in enumerate(strikes) if abs(k / spot - 1) <= RULE["window"]]
    if not cols or not rows:
        return {"direction": "NONE", "read": "no expiries or strikes in the window"}
    king = max(abs(V[i][j] or 0.0) for j, _, _ in cols for i in rows)
    if king <= 0:
        return {"direction": "NONE", "read": "empty book"}

    def read(positive_only):
        steps = []
        for j, e, dte in cols:
            best = None
            for i in rows:
                v = V[i][j] or 0.0
                if positive_only:
                    if v > 0 and (best is None or v > best[1]):
                        best = (strikes[i], v)
                elif v and (best is None or abs(v) > abs(best[1])):
                    best = (strikes[i], v)
            if best and abs(best[1]) >= RULE["major_share"] * king:
                steps.append({"strike": best[0], "expiry": e, "dte": dte, "vanna": best[1],
                              "pct_of_king": round(abs(best[1]) / king * 100)})
        if len(steps) < RULE["min_steps"]:
            return {"direction": None, "steps": steps, "rho": None, "net_pct": None, "too_few": True}
        ks = [s["strike"] for s in steps]
        rho = _spearman(list(range(len(ks))), ks); net = (ks[-1] - ks[0]) / spot
        d = "UP" if rho >= RULE["rho"] and net >= RULE["net"] else "DOWN" if rho <= -RULE["rho"] and net <= -RULE["net"] else None
        return {"direction": d, "steps": steps, "rho": round(rho, 2), "net_pct": round(net * 100, 1), "too_few": False}

    pos, anyr = read(True), read(False)
    conflict = bool(pos["direction"] and anyr["direction"] and pos["direction"] != anyr["direction"])
    main = None if conflict else pos if pos["direction"] else anyr if anyr["direction"] else None
    shown = main or (pos if not pos["too_few"] else anyr)
    return {
        "direction": main["direction"] if main else "NONE",
        "read": ("positive node" if main is pos else "either-sign node") if main else ("conflict" if conflict else "no stair"),
        "rho": shown["rho"], "net_pct": shown["net_pct"], "steps": shown["steps"],
        "path": " → ".join(f'{s["strike"]:g} ({s["expiry"]})' for s in shown["steps"]),
        "positive_read": pos["direction"] or "NONE", "either_sign_read": anyr["direction"] or "NONE",
        "caveat": "Descriptive only: a stair did not predict direction (49.3% / 48.0% at 5 / 10 sessions, 253 names, 2026).",
    }


def handle_tool_call(name, arguments):
    """Dispatch a model tool call. `arguments` is the already-parsed arguments mapping."""
    if name != "detect_vex_stair":
        return {"error": f"unknown tool {name}"}
    sym = str(arguments.get("symbol", "")).upper().strip()
    if not sym:
        return {"error": "symbol required"}
    out = detect_vex_stair(load_vanna_book(sym))
    out["symbol"] = sym
    return out


if __name__ == "__main__":
    # Self-test on an ILLUSTRATIVE book (made-up numbers): the dominant +vanna node steps DOWN 115 → 110 → 105 → 100.
    demo = {"spot": 110.0, "session": "2026-10-01",
            "expirations": ["2026-10-09", "2026-10-16", "2026-11-20", "2026-12-18"],
            "strikes": [100, 105, 110, 115],
            "vanna": [[0, 0, 0, 9e6], [0, 0, 9e6, 0], [0, 9e6, 0, 0], [9e6, 0, 0, 0]]}
    r = detect_vex_stair(demo)
    print(r["direction"], "|", r["path"], "| rho", r["rho"], "| net", r["net_pct"], "%")
