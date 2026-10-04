"""Shared helpers: signal loading, the evaluation window, bar files. Standard library only."""
from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
MIN = 60


def session(date: str) -> tuple[int, int]:
    """(09:30 ET, 16:00 ET) of `date` as UTC epoch seconds."""
    y, m, d = map(int, date.split("-"))
    f = lambda h, mi: int(datetime(y, m, d, h, mi, tzinfo=ET).timestamp())
    return f(9, 30), f(16, 0)


def window(sig: dict, lead_min: int = 20, close_after_min: int = 15) -> dict:
    """open_t: first minute the agent may act; end_t: last minute it may OPEN a trade; force_t: everything still open is closed here."""
    o, c = session(sig["date"])
    return {"open_t": max(sig["start"] - lead_min * MIN, o), "end_t": sig["end"], "force_t": min(sig["end"] + close_after_min * MIN, c)}


def load_signals(path: str | Path, verify: bool = True) -> dict:
    p = Path(path); text = p.read_text(encoding="utf-8"); doc = json.loads(text)
    man = p.with_name("manifest.json")
    if verify and man.exists():
        want = json.loads(man.read_text(encoding="utf-8"))["signals_sha256"]
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != want:
            raise ValueError(f"{p} does not match manifest.json sha256: the slice was modified")
    return doc


class Bars:
    """1-minute bars for one day: {SYMBOL: [[t_start, o, h, l, c], ...]}, t_start = UTC epoch seconds of the bar's first second."""
    def __init__(self, bars: dict):
        self.b = {s: sorted(r, key=lambda x: x[0]) for s, r in bars.items()}
        self.idx = {s: {r[0]: i for i, r in enumerate(rows)} for s, rows in self.b.items()}

    @classmethod
    def load(cls, bars_dir: str | Path, date: str) -> "Bars":
        p = Path(bars_dir) / f"bars_{date}.json"
        if not p.exists(): raise FileNotFoundError(f"missing {p}: run fetch_bars.py for {date}")
        return cls(json.loads(p.read_text(encoding="utf-8"))["bars"])

    def has(self, sym: str) -> bool: return bool(self.b.get(sym))

    def price_at(self, sym: str, t: int) -> float | None:
        """Fill price for an order decided at minute `t`: OPEN of the bar starting at t. After the last bar, the last close."""
        rows = self.b.get(sym)
        if not rows: return None
        t = t - t % MIN
        i = self.idx[sym].get(t)
        if i is not None: return rows[i][1]
        if t > rows[-1][0]: return rows[-1][4]
        nxt = next((r for r in rows if r[0] >= t), None)       # missing minute (illiquid): next available open
        return nxt[1] if nxt else None

    def between(self, sym: str, t0: int, t1: int) -> list[list[float]]:
        return [r for r in self.b.get(sym, []) if t0 <= r[0] < t1]


def pick_signals(doc: dict, specs: list[str] | None) -> list[dict]:
    """Select signals by 1-based position ("3") or id / id prefix. None or empty = all."""
    sigs = doc["signals"]
    if not specs: return sigs
    out = []
    for sp in specs:
        if sp.isdigit() and 1 <= int(sp) <= len(sigs): out.append(sigs[int(sp) - 1]); continue
        m = [s for s in sigs if s["id"].startswith(sp)]
        if len(m) != 1: raise SystemExit(f"--signal {sp!r}: matches {len(m)} signals (use a 1-based number or a unique id prefix)")
        out.append(m[0])
    return out
