"""Trial report for one recorded day: what a recorded day costs and contains.

Feeds the keep / reduce / stop decision in docs/build-order.md ("Recorder trial").
Writes report.json and report.md next to the recording and prints the markdown.

Run:
  PYTHONPATH=src python3 -m agentic_trading.logging.trial_report 2026-10-05 \
      [--out runtime/recordings] [--historical-credits-per-board 1]
"""
from __future__ import annotations

import argparse
import gzip
import json
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator
from zoneinfo import ZoneInfo

from agentic_trading.logging.recorder import RUN_FILE, STREAM_FILE

CT = ZoneInfo("America/Chicago")
PROJECTED_CREDITS_PER_DAY = 1580  # docs/architecture.md section 8, 4-symbol stream 08:25–15:00 CT
GAP_FACTOR = 3.0                  # a gap is > 3x the median board cadence


def read_records(day_dir: Path) -> Iterator[dict]:
    for name in (STREAM_FILE + ".gz", STREAM_FILE):
        p = day_dir / name
        if not p.exists():
            continue
        opener = gzip.open if name.endswith(".gz") else open
        with opener(p, "rt", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def parse_ts(v) -> datetime | None:
    """asOf as ISO 8601 string or epoch milliseconds/seconds -> aware UTC datetime."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        secs = v / 1000.0 if v > 1e11 else float(v)
        return datetime.fromtimestamp(secs, tz=timezone.utc)
    if isinstance(v, str):
        s = v.strip()
        if s.isdigit():
            return parse_ts(int(s))
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def cursor_asof(event_id: str | None, symbol: str) -> datetime | None:
    """Skylit v2 event id is 'SPY:<asOfMs>,QQQ:<asOfMs>'."""
    if not event_id:
        return None
    for part in event_id.split(","):
        sym, _, ms = part.partition(":")
        if sym.strip().upper() == symbol and ms.strip().isdigit():
            return parse_ts(int(ms))
    return None


def _pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _r(x, n=1):
    return None if x is None else round(x, n)


def analyze(records: Iterable[dict], trading_day: str, run: dict | None = None,
            historical_credits_per_board: float | None = None) -> dict:
    day = date.fromisoformat(trading_day)
    by_type: Counter = Counter()
    sym_boards: dict[str, list[tuple[datetime, datetime, bool, int]]] = defaultdict(list)
    sym_expired: Counter = Counter()
    velocity: Counter = Counter()
    reconnects: Counter = Counter()
    closed: list[str] = []
    unavailable: list[dict] = []
    conn_errors: Counter = Counter()
    conn_opens = 0
    comments = 0
    raw_bytes = 0
    credits_first = credits_last = None
    charged_sum = 0.0
    first_recv = last_recv = None

    for rec in records:
        recv = parse_ts(rec.get("recv_utc"))
        kind = rec.get("kind")
        if kind == "meta":
            what = rec.get("what")
            if what == "conn_open":
                conn_opens += 1
            elif what == "conn_error":
                d = rec.get("detail") or {}
                conn_errors[str(d.get("status") or d.get("error", "error")).split(":")[0]] += 1
            continue
        if kind == "comment":
            comments += 1
            continue
        if kind != "event":
            continue
        ev, raw = rec.get("event"), rec.get("data") or ""
        raw_bytes += len(raw.encode("utf-8"))
        by_type[ev] += 1
        if recv:
            first_recv = first_recv or recv
            last_recv = recv
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        if ev == "snapshot":
            sym = str(data.get("symbol", "?")).upper()
            asof = parse_ts(data.get("asOf")) or cursor_asof(rec.get("id"), sym)
            if asof and recv:
                sym_boards[sym].append((asof, recv, bool(data.get("lagged")), len(raw.encode("utf-8"))))
            exp = data.get("expiration")
            try:
                if exp and date.fromisoformat(str(exp)[:10]) < day:
                    sym_expired[sym] += 1
            except ValueError:
                pass
        elif ev == "velocity":
            velocity[str(data.get("symbol", "?")).upper()] += 1
        elif ev == "reconnect":
            reconnects[str(data.get("reason", "?"))] += 1
        elif ev == "closed":
            closed.append(str(data.get("reason", "?")))
        elif ev == "symbol_unavailable":
            unavailable.append({k: data.get(k) for k in ("symbol", "reason", "message")})
        elif ev in ("connected", "credits"):
            rem = data.get("creditsRemaining") if ev == "connected" else data.get("remaining")
            if ev == "credits" and isinstance(data.get("charged"), (int, float)):
                charged_sum += data["charged"]
            if isinstance(rem, (int, float)):
                credits_first = rem if credits_first is None else credits_first
                credits_last = rem

    symbols = {}
    total_boards = 0
    for sym, boards in sorted(sym_boards.items()):
        seen, distinct = set(), []
        for b in boards:
            if b[0] in seen:
                continue  # same board re-sent after a resume
            seen.add(b[0])
            distinct.append(b)
        distinct.sort(key=lambda b: b[0])
        total_boards += len(distinct)
        gaps = [(b2[0] - b1[0]).total_seconds() for b1, b2 in zip(distinct, distinct[1:])]
        med = statistics.median(gaps) if gaps else None
        big = [g for g in gaps if med and g > GAP_FACTOR * med]
        lags = [(b[1] - b[0]).total_seconds() for b in distinct]
        symbols[sym] = {
            "boards": len(distinct),
            "duplicate_boards": len(boards) - len(distinct),
            "first_asof_ct": distinct[0][0].astimezone(CT).strftime("%H:%M:%S") if distinct else None,
            "last_asof_ct": distinct[-1][0].astimezone(CT).strftime("%H:%M:%S") if distinct else None,
            "cadence_median_s": _r(med), "cadence_p90_s": _r(_pct(gaps, 0.9)), "cadence_max_s": _r(max(gaps) if gaps else None),
            "gaps": len(big), "gap_minutes": _r(sum(big) / 60 if big else 0.0),
            "recv_lag_median_s": _r(statistics.median(lags) if lags else None, 2),
            "recv_lag_p90_s": _r(_pct(lags, 0.9), 2), "recv_lag_max_s": _r(max(lags) if lags else None, 2),
            "lagged_boards": sum(1 for b in distinct if b[2]),
            "passed_expiry_boards": sym_expired.get(sym, 0),
            "velocity_events": velocity.get(sym, 0),
            "mb": _r(sum(b[3] for b in boards) / 1e6, 2),
        }

    credits_used = (credits_first - credits_last) if credits_first is not None and credits_last is not None else (charged_sum or None)
    if run and credits_used is None:
        credits_used = run.get("credits_used")
    minutes = (last_recv - first_recv).total_seconds() / 60 if first_recv and last_recv else 0.0
    n_sym = len(symbols) or len((run or {}).get("symbols", [])) or 1
    report = {
        "trading_day": trading_day,
        "window_ct": [first_recv.astimezone(CT).strftime("%H:%M") if first_recv else None,
                      last_recv.astimezone(CT).strftime("%H:%M") if last_recv else None],
        "minutes_recorded": _r(minutes),
        "stop_reason": (run or {}).get("stop_reason"),
        "connections": conn_opens, "conn_errors": dict(conn_errors),
        "reconnects": dict(reconnects), "closed": closed, "symbol_unavailable": unavailable,
        "events_by_type": dict(by_type), "pings": comments,
        "symbols": symbols, "boards_total": total_boards,
        "raw_mb": _r(raw_bytes / 1e6, 2),
        "credits_used": _r(credits_used, 0),
        "credits_projected": PROJECTED_CREDITS_PER_DAY,
        "credits_per_symbol_minute": _r(credits_used / (n_sym * minutes), 3) if credits_used and minutes else None,
    }
    if historical_credits_per_board:
        buy_later = total_boards * historical_credits_per_board
        report["historical_equivalent_credits"] = _r(buy_later, 0)
        report["recording_vs_historical"] = _r(credits_used / buy_later, 2) if credits_used and buy_later else None
    report["flags"] = _flags(report)
    return report


def _flags(r: dict) -> list[str]:
    f = []
    for sym, s in r["symbols"].items():
        if s["gaps"]:
            f.append(f"{sym}: {s['gaps']} gaps totalling {s['gap_minutes']} min (> {GAP_FACTOR:g}x median cadence)")
        if s["lagged_boards"]:
            f.append(f"{sym}: {s['lagged_boards']} lagged boards (reader slower than publish)")
        if s["passed_expiry_boards"]:
            f.append(f"{sym}: {s['passed_expiry_boards']} boards with a passed expiry (guard must reject)")
    if r["closed"]:
        f.append(f"stream closed by server: {', '.join(r['closed'])}")
    if r["symbol_unavailable"]:
        f.append(f"symbol_unavailable: {', '.join(str(u.get('symbol')) for u in r['symbol_unavailable'])}")
    if r["conn_errors"]:
        f.append(f"connection errors: {_kv(r['conn_errors'])}")
    cu = r.get("credits_used")
    if cu and cu > 1.2 * r["credits_projected"]:
        f.append(f"credits {cu:.0f} > 1.2x projected {r['credits_projected']}")
    ratio = r.get("recording_vs_historical")
    if ratio is not None:
        if ratio < 0.8:
            f.append(f"recording is cheaper than buying the same boards later (ratio {ratio})")
        elif ratio > 1.2:
            f.append(f"historical heatmap is cheaper than recording (ratio {ratio})")
        else:
            f.append(f"recording and historical heatmap are near break-even (ratio {ratio})")
    return f


def _kv(d: dict) -> str:
    return ", ".join(f"{k} {v}" for k, v in d.items()) if d else "0"


def to_markdown(r: dict) -> str:
    L = [f"# Recorder trial report — {r['trading_day']}", "",
         f"Window {r['window_ct'][0]}–{r['window_ct'][1]} CT ({r['minutes_recorded']} min) · stop: {r['stop_reason']} · "
         f"connections {r['connections']} · reconnects {_kv(r['reconnects'])} · pings {r['pings']}", "",
         f"**Credits used:** {r['credits_used']} (projected ≈ {r['credits_projected']}) · "
         f"per symbol-minute: {r['credits_per_symbol_minute']}"]
    if "historical_equivalent_credits" in r:
        L.append(f"**Same boards from the historical heatmap:** {r['historical_equivalent_credits']} credits "
                 f"(recording / historical = {r['recording_vs_historical']})")
    L += ["", f"**Data:** {r['boards_total']} boards, {r['raw_mb']} MB raw", "",
          "| Symbol | Boards | Cadence med / p90 / max (s) | Gaps (min) | Recv lag med / p90 (s) | Lagged | Passed expiry | First–last asOf CT | MB |",
          "|---|---|---|---|---|---|---|---|---|"]
    for sym, s in r["symbols"].items():
        L.append(f"| {sym} | {s['boards']} | {s['cadence_median_s']} / {s['cadence_p90_s']} / {s['cadence_max_s']} | "
                 f"{s['gaps']} ({s['gap_minutes']}) | {s['recv_lag_median_s']} / {s['recv_lag_p90_s']} | {s['lagged_boards']} | "
                 f"{s['passed_expiry_boards']} | {s['first_asof_ct']}–{s['last_asof_ct']} | {s['mb']} |")
    L += ["", "## Flags", ""] + ([f"- {x}" for x in r["flags"]] or ["- none"])
    L += ["", "## Decide (operator)", "",
          "- **Keep:** a recorded day costs less than the same boards from the historical heatmap, and no unexplained gaps.",
          "- **Reduce:** near break-even, or one symbol / window carries most of the value (drop SPX, shorter window).",
          "- **Stop:** the historical heatmap is cheaper, or recorded days have gaps it does not.",
          "", "Record the decision in `specs/decisions/`.", ""]
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Report on one recorded day (recorder trial).")
    p.add_argument("trading_day", help="YYYY-MM-DD")
    p.add_argument("--out", default="runtime/recordings")
    p.add_argument("--historical-credits-per-board", type=float, default=None,
                   help="credits to buy one board later from the historical heatmap (see Skylit account_usage prices)")
    a = p.parse_args(argv)
    day_dir = Path(a.out) / a.trading_day
    if not day_dir.exists():
        raise SystemExit(f"no recording at {day_dir}")
    run_p = day_dir / RUN_FILE
    run = json.loads(run_p.read_text()) if run_p.exists() else None
    r = analyze(read_records(day_dir), a.trading_day, run, a.historical_credits_per_board)
    (day_dir / "report.json").write_text(json.dumps(r, indent=2) + "\n")
    md = to_markdown(r)
    (day_dir / "report.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
