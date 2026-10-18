"""Rich ``trade`` payload for Finding_Cards (A+/2R/Pass) and Pass why templates.

Builds the nested ``trade`` object the console FindingCard expects from a
priced SetupDecision, live bars, GateEvaluation, map levels and the versioned
``configs/pattern_odds.json`` table. Pass “why” lines are deterministic
code→English maps; the Narrator may polish them when enabled.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Final

from fse.config.schema.gates import GATE_IDS
from fse.engine.gates.registry import GateEvaluation, RejectionReason
from fse.engine.levels import TICKS_PER_POINT, ticks_to_points
from fse.engine.planner import PlaceBracket
from fse.engine.step import SetupDecision, Sized
from fse.engine.types import Bar, CandidateSetup
from fse.logio.canonical_json import JsonValue, to_jsonable
from fse.sim.fills import TICK_VALUE_USD
from fse.timekit import Instant

__all__ = [
    "TRADE_BAR_LIMIT",
    "build_trade",
    "gates_passed_text",
    "load_pattern_odds",
    "odds_for_pattern",
    "premarket_summary",
    "reason_to_english",
    "reasons_to_why",
    "rejection_detail",
    "session_day_range_pct",
    "trade_bars",
]

TRADE_BAR_LIMIT: Final = 40
TICK_SIZE: Final = 1.0 / TICKS_PER_POINT

_INSTRUMENT_NAME: Final[Mapping[str, str]] = {
    "MES": "Micro E-mini S&P",
    "MNQ": "Micro E-mini Nasdaq",
    "ES": "E-mini S&P",
    "NQ": "E-mini Nasdaq",
}
_DEFAULT_QTY: Final[Mapping[str, int]] = {"MES": 5, "MNQ": 3, "ES": 1, "NQ": 1}
_PATTERN_TAG: Final[Mapping[str, str]] = {
    "gatekeeper_fade": "gatekeeper",
    "floor_ceiling_bounce": "floor/ceiling",
    "floor_ceiling_bounce_empty_basement": "empty basement",
    "beach_ball": "beach ball",
    "rug": "rug",
    "reverse_rug": "reverse rug",
    "whipsaw_fade": "whipsaw",
    "trend_follow": "trend",
}

_REASON_ENGLISH: Final[Mapping[str, str]] = {
    "stale_map": "Map snapshot is too old for a fresh entry",
    "map_grade": "Map grade is below the allowed minimum",
    "midpoint": "Price has not cleared the midpoint of the move",
    "deflection_band": "Price is outside the deflection band for this node",
    "chart_confluence": "Chart confluence does not support the setup",
    "stdev_fib_zone": "Price is outside the stdev / fib zone",
    "dark_pool_confluence": "Dark-pool confluence is missing",
    "trinity_agreement": "Gamma maps do not agree on direction",
    "candle_color": "Candle color does not match the setup direction",
    "tap_count": "Already used the allowed taps at this node",
    "third_gatekeeper_test": "Third gatekeeper test is not allowed",
    "weekly_node_tests": "Weekly node test limit reached",
    "sloppy_seconds": "Entry window is in the sloppy-seconds period",
    "air_pocket_fade": "Air-pocket fade conditions are not met",
    "min_reward_risk": "Reward:risk is below the minimum gate",
    "opposition_inside_target": "An opposing node sits inside the target path",
    "fomo_travel": "Price has already traveled too far (FOMO)",
    "open_shuffle": "Still inside the open-shuffle window",
    "entry_cutoff": "Past the entry cutoff for this session",
    "late_session_chase": "Late-session chase is blocked",
    "news_window": "Inside a news blackout window",
    "vix_gap": "VIX gap blocks this size or entry",
    "regime_match": "Setup does not match the current regime",
    "dormant_node": "Source node is dormant",
    "gatekeepers_on_path": "Gatekeepers sit on the target path",
    "node_growth_divergence": "Node growth divergence fails the gate",
    "kill_switch_lockout": "Kill-switch lockout is in force",
    "no_conversion_price": "No futures conversion price for this node",
}

# Keep every current gate id covered; unknown codes fall back to a title case.
assert all(g in _REASON_ENGLISH for g in GATE_IDS)


def reason_to_english(code: str) -> str:
    """One Rejection_Reason / gate id as an operator sentence."""
    if code in _REASON_ENGLISH:
        return _REASON_ENGLISH[code]
    words = code.replace("-", "_").split("_")
    return " ".join(w.capitalize() if i == 0 else w for i, w in enumerate(words) if w) or code


def reasons_to_why(reasons: Sequence[str]) -> tuple[str, ...]:
    return tuple(reason_to_english(r) for r in reasons)


def rejection_detail(r: RejectionReason) -> dict[str, JsonValue]:
    """Reason plus measured/threshold for Narrator and analytics."""
    return {
        "reason": r.reason,
        "measured": to_jsonable(r.measured),
        "threshold": to_jsonable(r.threshold),
    }


def gates_passed_text(evaluation: GateEvaluation) -> str:
    """``passed/evaluated`` among enabled (non-disabled) gates."""
    enabled = [r for r in evaluation.results if r.result != "disabled"]
    if not enabled:
        return "0/0"
    passed = sum(1 for r in enabled if r.result == "pass")
    return f"{passed}/{len(enabled)}"


@lru_cache(maxsize=1)
def load_pattern_odds(path: str | None = None) -> Mapping[str, JsonValue]:
    """Load the versioned pattern odds table (empty mapping when missing/invalid)."""
    root = Path(__file__).resolve().parents[3]
    odds_path = Path(path) if path else root / "configs" / "pattern_odds.json"
    try:
        data = json.loads(odds_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    patterns = data.get("patterns") if isinstance(data, dict) else None
    return patterns if isinstance(patterns, dict) else {}


def odds_for_pattern(pattern: str) -> dict[str, JsonValue] | None:
    """``trade.odds`` for ``pattern``, or ``None`` when the table has no entry."""
    entry = load_pattern_odds().get(pattern)
    if not isinstance(entry, dict):
        return None
    items = entry.get("items")
    if not isinstance(items, list) or not items:
        return None
    n = entry.get("n")
    label = f"{pattern} · n={n}" if isinstance(n, int) else pattern
    cleaned: list[JsonValue] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        pct, lab = item.get("pct"), item.get("label")
        if isinstance(pct, (int, float)) and isinstance(lab, str):
            cleaned.append({"pct": int(pct), "label": lab})
    if not cleaned:
        return None
    return {"label": label, "items": cleaned}


def trade_bars(
    bars: Sequence[Bar] | None, *, limit: int = TRADE_BAR_LIMIT
) -> list[dict[str, float]]:
    """Last ``limit`` 1-min OHLC dicts for the rich chart."""
    if not bars:
        return []
    slice_ = list(bars[-limit:])
    return [{"o": b.o, "h": b.h, "l": b.l, "c": b.c} for b in slice_]


def session_day_range_pct(
    bars: Sequence[Bar] | None, spot: float
) -> tuple[float | None, float | None]:
    """``(dayPct, rangePct)`` from session bars vs ``spot``; ``None`` when unknown."""
    if not bars or spot <= 0:
        return None, None
    open_px = bars[0].o
    if open_px <= 0:
        return None, None
    hi = max(b.h for b in bars)
    lo = min(b.l for b in bars)
    day_pct = round((spot - open_px) / open_px * 100.0, 2)
    range_pct = round((hi - lo) / open_px * 100.0, 2)
    return day_pct, range_pct


def premarket_summary(
    *,
    regime: str,
    map_grade: str,
    setups_listed: int,
) -> str:
    """Plain-English premarket line (no fake trade)."""
    if setups_listed <= 0:
        return (
            f"Premarket map grade {map_grade} · {regime} — "
            "no A+ / 2R setup yet; waiting for the open."
        )
    return f"Premarket map grade {map_grade} · {regime} — {setups_listed} setup(s) listed."


def _qty(decision: SetupDecision) -> int:
    c = decision.setup
    if isinstance(decision.placement, PlaceBracket):
        return decision.placement.entry.qty
    if isinstance(decision.sizing, Sized):
        return decision.sizing.contracts
    return _DEFAULT_QTY.get(c.key.instrument, 1)


def _target_rows(
    c: CandidateSetup, qty: int
) -> list[dict[str, JsonValue]]:
    prices = [ticks_to_points(t) for t in c.targets]
    if not prices:
        return []
    if len(prices) == 1:
        qtys = [qty]
    else:
        tp1 = max(1, qty // 2)
        qtys = [tp1, qty - tp1] + [0] * max(0, len(prices) - 2)
        if qtys[1] <= 0 and qty > 1:
            qtys[1] = qty - tp1
    out: list[dict[str, JsonValue]] = []
    for i, price in enumerate(prices):
        q = qtys[i] if i < len(qtys) else 0
        if q <= 0 and i > 0:
            continue
        out.append({"label": f"TP{i + 1}", "price": price, "qty": max(q, 1)})
    return out


def _levels_from_maps(map_fields: Sequence[Mapping[str, JsonValue]]) -> list[dict[str, JsonValue]]:
    """Flatten first futures-facing map rows into chart levels."""
    levels: list[dict[str, JsonValue]] = []
    seen: set[tuple[str, float]] = set()

    def add(kind: str, level: object, label: str | None = None, to: object = None) -> None:
        if not isinstance(level, (int, float)):
            return
        key = (kind, float(level))
        if key in seen:
            return
        seen.add(key)
        row: dict[str, JsonValue] = {"kind": kind, "price": float(level)}
        if label:
            row["label"] = label
        if isinstance(to, (int, float)):
            row["to"] = float(to)
        levels.append(row)

    for m in map_fields:
        for kind, label in (("king", "King"), ("ceiling", "Ceiling"), ("floor", "Floor")):
            field = m.get(kind)
            if isinstance(field, dict):
                add(kind, field.get("level"), label)
        gks = m.get("gatekeepers")
        if isinstance(gks, list):
            for g in gks:
                if isinstance(g, dict):
                    add("gatekeeper", g.get("level"), "Gatekeeper")
        air = m.get("air_pockets")
        if isinstance(air, list):
            for pair in air:
                if (
                    isinstance(pair, list)
                    and len(pair) == 2
                    and isinstance(pair[0], dict)
                    and isinstance(pair[1], dict)
                ):
                    lo, hi = pair[0].get("level"), pair[1].get("level")
                    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
                        add("air", min(lo, hi), "Air pocket", to=max(lo, hi))
    return levels


def _plan_prose(
    c: CandidateSetup,
    *,
    entry: float,
    stop: float,
    targets: Sequence[Mapping[str, JsonValue]],
    tag: str,
    grade: str,
) -> dict[str, str]:
    short = c.key.direction == "short"
    verb = "sell" if short else "buy"
    approach = "Rallies into" if short else "Pulls into"
    if_line = f"{approach} the {entry:g} {tag} — {verb} limit"
    if grade == "Pass":
        if_line += " (not armed · Pass)"
    else:
        if_line += " armed" if grade == "A_Plus" else " watched (2R · no order)"
    out_line = f"Stop {stop:g} · moves to breakeven at TP1"
    if targets:
        parts = []
        risk = abs(entry - stop) or 1.0
        for t in targets:
            price = float(t["price"])
            r = abs(price - entry) / risk
            parts.append(f"{price:g} ({r:.1f}R)")
        qparts = " / ".join(str(t["qty"]) for t in targets)
        tgt_line = f"{', then '.join(parts)} — scale out {qparts}"
    else:
        tgt_line = "No targets priced"
    return {"if": if_line, "out": out_line, "tgt": tgt_line}


def _context(
    *,
    king: float | None,
    ceiling: float | None,
    floor: float | None,
    stop: float,
    entry: float,
    spot: float,
    flip: str | None,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if king is not None:
        text = f"King {king:g}"
        if flip:
            text += f" · flipped {flip}"
        rows.append({"icon": "crown", "text": text})
    cap = ceiling if stop >= entry else floor
    if cap is not None:
        dist = abs(cap - stop)
        label = "Ceiling" if stop >= entry else "Floor"
        rows.append({"icon": "shield", "text": f"{label} {cap:g} vs stop · {dist:.2f} pts"})
    rows.append(
        {
            "icon": "ruler",
            "text": f"entry is {abs(entry - spot):.2f} pts from spot",
        }
    )
    return rows


def _action(
    c: CandidateSetup,
    *,
    grade: str,
    entry: float,
    stop: float,
    qty: int,
    targets: Sequence[Mapping[str, JsonValue]],
) -> tuple[str, str]:
    if grade == "Pass":
        return "PASS · no order", "Pass"
    short = c.key.direction == "short"
    side = "SELL" if short else "BUY"
    tp = " / ".join(f"{float(t['price']):.2f} x{t['qty']}" for t in targets) or "—"
    if grade == "Alert_2R":
        return (
            f"{side} LIMIT {entry:.2f} x{qty} {c.key.instrument} · stop {stop:.2f} · "
            f"TP {tp} (alert only · no order placed)",
            "2R alert",
        )
    return (
        f"{side} LIMIT {entry:.2f} x{qty} {c.key.instrument} · stop {stop:.2f} · TP {tp}",
        "Armed",
    )


def build_trade(
    decision: SetupDecision,
    *,
    spot: float,
    map_json: Sequence[Mapping[str, JsonValue]],
    bars: Sequence[Bar] | None = None,
    flip: str | None = None,
    t: Instant | None = None,
    session_day: date | None = None,
) -> dict[str, JsonValue] | None:
    """FindingTrade-shaped dict, or ``None`` when the setup is unpriced."""
    del t, session_day  # reserved for future bar-day resolution at call sites
    c = decision.setup
    grade = decision.evaluation.grade
    if grade not in ("A_Plus", "Alert_2R", "Pass"):
        return None
    if c.entry is None or c.stop is None:
        return None
    entry = ticks_to_points(c.entry)
    stop = ticks_to_points(c.stop)
    qty = _qty(decision)
    targets = _target_rows(c, qty)
    risk = abs(entry - stop)
    rr = 0.0
    if risk and targets:
        # Position-weighted R from target quantities.
        total_q = sum(int(t["qty"]) for t in targets) or qty
        weighted = sum(abs(float(t["price"]) - entry) / risk * int(t["qty"]) for t in targets)
        rr = round(weighted / total_q, 2)
    elif risk and c.targets:
        rr = round(abs(ticks_to_points(c.targets[0]) - entry) / risk, 2)
    tag = _PATTERN_TAG.get(c.key.pattern, "node")
    node = f"{c.source.symbol} {c.key.source_strike:g} {tag}"
    day_pct, range_pct = session_day_range_pct(bars, spot)
    tick_value = float(TICK_VALUE_USD.get(c.key.instrument, 1.25))
    king = ceiling = floor = None
    if map_json:
        first = map_json[0]
        for name, holder in (("king", "king"), ("ceiling", "ceiling"), ("floor", "floor")):
            field = first.get(holder)
            if isinstance(field, dict) and isinstance(field.get("level"), (int, float)):
                val = float(field["level"])
                if name == "king":
                    king = val
                elif name == "ceiling":
                    ceiling = val
                else:
                    floor = val
    action, action_label = _action(c, grade=grade, entry=entry, stop=stop, qty=qty, targets=targets)
    trade: dict[str, JsonValue] = {
        "instrument": c.key.instrument,
        "name": _INSTRUMENT_NAME.get(c.key.instrument, c.key.instrument),
        "direction": c.key.direction,
        "grade": grade,
        "pattern": c.key.pattern,
        "node": node,
        "tap": c.key.tap_seq,
        "spot": spot,
        "dayPct": day_pct,
        "rangePct": range_pct,
        "tick": TICK_SIZE,
        "tickValue": tick_value,
        "qty": qty,
        "rr": rr,
        "gates": gates_passed_text(decision.evaluation),
        "entry": entry,
        "entryTag": tag,
        "stop": stop,
        "targets": targets,
        "levels": _levels_from_maps(map_json),
        "plan": _plan_prose(c, entry=entry, stop=stop, targets=targets, tag=tag, grade=grade),
        "context": _context(
            king=king, ceiling=ceiling, floor=floor, stop=stop, entry=entry, spot=spot, flip=flip
        ),
        "odds": odds_for_pattern(c.key.pattern),
        "action": action,
        "actionLabel": action_label,
        "bars": trade_bars(bars),
    }
    return trade
