"""Runs and analytics: every output folder under ``runs/``, read through an ArtifactStore.

Metrics come from the engine itself: ``report.json`` when ``fse report`` ran,
else ``fse.analytics.metrics.summarize`` over the run's ``trades.json``. The
equity and drawdown curves (no engine file holds them) are cumulative net
dollars and R of the non-shadow trades in exit order, the order ``summarize``
uses for drawdown.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from fse_ui.db import Db

MAX_READ_BYTES = 32 * 1024 * 1024
FILE_RE = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$")
RUN_RE = re.compile(r"^[A-Za-z0-9_.:+-]{1,128}$")
EXPERIMENT_KINDS = ("ablation", "sweep", "montecarlo", "holdout", "walkforward", "cadence")
EXPERIMENT_FILES = (
    "ablation.json",
    "frontier.json",
    "holdout.json",
    "walkforward.json",
    "cadence.json",
    "pass_estimate.json",
    "comparison.json",
    "drafts.json",
)
DISCLAIMER = (
    "These figures are measured backtest results on cached data. "
    "They are not a forecast of future results."
)


class RunError(KeyError):
    pass


class ArtifactStore(Protocol):
    def runs(self) -> list[tuple[str, float]]:
        """``(name, mtime)`` of every run folder."""
        ...

    def files(self, run: str) -> list[dict[str, Any]]: ...

    def read_bytes(self, run: str, name: str) -> bytes: ...

    def exists(self, run: str, name: str) -> bool: ...


def check_names(run: str, name: str | None = None) -> None:
    if not RUN_RE.fullmatch(run) or run in (".", ".."):
        raise RunError(f"invalid run name {run!r}")
    if name is not None and (not FILE_RE.fullmatch(name) or ".." in name.split("/")):
        raise RunError(f"invalid file name {name!r}")


def _run_mtime(run: Path) -> float:
    """The run folder's mtime, or its ``report/`` folder's when newer.

    ``fse report`` writes into ``<run>/report/``, which changes only that folder's
    mtime, so the ``RunIndex`` cache key has to include it.
    """
    mtime = run.stat().st_mtime
    try:
        return max(mtime, (run / "report").stat().st_mtime)
    except OSError:
        return mtime


class LocalArtifactStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, run: str, name: str | None = None) -> Path:
        check_names(run, name)
        base = (self._root / run).resolve()
        if base.parent != self._root.resolve():
            raise RunError(f"invalid run name {run!r}")
        if name is None:
            return base
        path = (base / name).resolve()
        if not path.is_relative_to(base):
            raise RunError(f"invalid file name {name!r}")
        return path

    def has_run(self, run: str) -> bool:
        try:
            return self._path(run).is_dir()
        except RunError:
            return False

    def runs(self) -> list[tuple[str, float]]:
        try:
            return [
                (p.name, _run_mtime(p))
                for p in self._root.iterdir()
                if p.is_dir() and RUN_RE.fullmatch(p.name)
            ]
        except OSError:
            return []

    def files(self, run: str) -> list[dict[str, Any]]:
        base = self._path(run)
        if not base.is_dir():
            raise RunError(f"no run {run!r}")
        out = []
        for p in sorted(base.rglob("*")):
            if p.is_file() and not p.is_symlink():
                rel = p.relative_to(base).as_posix()
                if FILE_RE.fullmatch(rel):
                    out.append({"name": rel, "size": p.stat().st_size})
        return out

    def read_bytes(self, run: str, name: str) -> bytes:
        path = self._path(run, name)
        try:
            if path.stat().st_size > MAX_READ_BYTES:
                raise RunError(f"{name} is larger than {MAX_READ_BYTES} bytes")
            return path.read_bytes()
        except FileNotFoundError:
            raise RunError(f"no file {name!r} in run {run!r}") from None

    def exists(self, run: str, name: str) -> bool:
        try:
            return self._path(run, name).is_file()
        except RunError:
            return False


# ---------------------------------------------------------------- reading


REPORT_DIRS = ("", "report/")


def _report_name(files: Iterable[str], base: str) -> str | None:
    """The first of ``<base>`` and ``report/<base>`` among ``files`` (the root wins)."""
    names = set(files)
    return next((d + base for d in REPORT_DIRS if d + base in names), None)


def _json(store: ArtifactStore, run: str, name: str) -> Any:
    try:
        return json.loads(store.read_bytes(run, name))
    except RunError:
        return None
    except ValueError:
        return None


def read_csv(
    store: ArtifactStore, run: str, name: str, *, offset: int = 0, limit: int = 200
) -> dict[str, Any]:
    text = store.read_bytes(run, name).decode("utf-8", "replace")
    rows = list(csv.DictReader(io.StringIO(text)))
    return {
        "total": len(rows),
        "columns": list(rows[0]) if rows else [],
        "rows": rows[offset : offset + limit],
    }


def infer_kind(name: str, manifest: Any, files: Iterable[str]) -> str:
    if isinstance(manifest, dict) and isinstance(manifest.get("kind"), str):
        return str(manifest["kind"])
    names = set(files)
    if name.startswith("pull_"):
        return "pull"
    if name.startswith("live-"):
        return "session"
    if name.startswith("run-") and "host_log.jsonl" in names:
        return "runner"
    if name.startswith("drafts-"):
        return "drafts"
    for kind in EXPERIMENT_KINDS:
        if name.startswith(f"{kind}-"):
            return kind
    if "trades.json" in names:
        return "backtest"
    return "other"


def summarize_run(store: ArtifactStore, name: str) -> dict[str, Any]:
    files = [f["name"] for f in store.files(name)]
    manifest = _json(store, name, "run_manifest.json") if "run_manifest.json" in files else None
    m = manifest if isinstance(manifest, dict) else {}
    return {
        "name": name,
        "kind": infer_kind(name, manifest, files),
        "run_id": m.get("run_id", name),
        "config_hash": m.get("config_hash"),
        "config_path": m.get("config_path"),
        "data_range": m.get("data_range"),
        "seed": m.get("seed"),
        "status": m.get("status"),
        "has_report": _report_name(files, "report.json") is not None,
        "has_trades": "trades.json" in files,
        "file_count": len(files),
    }


class RunIndex:
    """Run summaries cached in SQLite by folder mtime."""

    def __init__(self, db: Db, store: ArtifactStore) -> None:
        self._db = db
        self.store = store

    def list_runs(
        self, *, kind: str | None = None, config: str | None = None, text: str | None = None
    ) -> list[dict[str, Any]]:
        out = []
        for name, mtime in self.store.runs():
            row = self._db.one("SELECT mtime, summary FROM runs_index WHERE name = ?", (name,))
            if row is not None and row["mtime"] == mtime:
                summary = json.loads(row["summary"])
            else:
                try:
                    summary = summarize_run(self.store, name)
                except RunError:
                    continue
                self._db.execute(
                    "INSERT INTO runs_index (name, mtime, summary) VALUES (?, ?, ?) "
                    "ON CONFLICT(name) DO UPDATE SET mtime = excluded.mtime, "
                    "summary = excluded.summary",
                    (name, mtime, json.dumps(summary)),
                )
            summary["mtime"] = mtime
            if kind and summary["kind"] != kind:
                continue
            if (
                config
                and config not in (summary.get("config_path") or "")
                and config != (summary.get("config_hash") or "")
            ):
                continue
            if text and text.lower() not in name.lower():
                continue
            out.append(summary)
        out.sort(key=lambda s: s["mtime"], reverse=True)
        return out


# ---------------------------------------------------------------- analytics


def _exit_ns(t: dict[str, Any]) -> int:
    return max(int(f["bar_open_ns"]) for f in t["exits"])


def ordered_trades(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    real = [t for t in trades if not t.get("shadow")]
    return sorted(real, key=lambda t: (_exit_ns(t), int(t["entry_fill"]["bar_open_ns"])))


def equity_curve(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Cumulative net $ and R with drawdown from the running peak, per closed trade."""
    cum_usd = cum_r = Decimal(0)
    peak_usd = peak_r = Decimal(0)
    max_dd_usd = max_dd_r = Decimal(0)
    points = []
    for t in ordered_trades(trades):
        cum_usd += Decimal(str(t["net"]))
        cum_r += Decimal(str(t["r_multiple"]))
        peak_usd, peak_r = max(peak_usd, cum_usd), max(peak_r, cum_r)
        dd_usd, dd_r = cum_usd - peak_usd, cum_r - peak_r
        max_dd_usd, max_dd_r = min(max_dd_usd, dd_usd), min(max_dd_r, dd_r)
        key = t["setup_key"]
        points.append(
            {
                "t_ns": _exit_ns(t),
                "session": key.get("session"),
                "instrument": key.get("instrument"),
                "net": str(t["net"]),
                "r": str(t["r_multiple"]),
                "cum_usd": str(cum_usd),
                "cum_r": str(cum_r),
                "dd_usd": str(dd_usd),
                "dd_r": str(dd_r),
            }
        )
    return {
        "points": points,
        "max_drawdown_usd": str(max_dd_usd),
        "max_drawdown_r": str(max_dd_r),
        "net_usd": str(cum_usd),
        "net_r": str(cum_r),
    }


def r_histogram(
    trades: list[dict[str, Any]], *, width: Decimal = Decimal("0.5")
) -> list[dict[str, Any]]:
    rs = [Decimal(str(t["r_multiple"])) for t in ordered_trades(trades)]
    if not rs:
        return []
    lo = (min(rs) / width).to_integral_value(rounding="ROUND_FLOOR") * width
    hi = (max(rs) / width).to_integral_value(rounding="ROUND_FLOOR") * width
    bins: dict[Decimal, int] = {}
    edge = lo
    while edge <= hi:
        bins[edge] = 0
        edge += width
    for r in rs:
        bins[(r / width).to_integral_value(rounding="ROUND_FLOOR") * width] += 1
    return [{"from": str(k), "to": str(k + width), "count": v} for k, v in bins.items()]


def trades_from_json(items: list[dict[str, Any]]) -> list[Any]:
    from fse.engine.types import Fill, SetupKey, Trade

    def fill(f: dict[str, Any]) -> Fill:
        return Fill(
            str(f["client_id"]),
            int(f["bar_open_ns"]),
            int(f["price"]),
            int(f["qty"]),
            Decimal(str(f["fees"])),
        )

    out = []
    for t in items:
        k = t["setup_key"]
        out.append(
            Trade(
                SetupKey(
                    k["instrument"],
                    k["pattern"],
                    float(k["source_strike"]),
                    k["direction"],
                    date.fromisoformat(k["session"]),
                    int(k["tap_seq"]),
                ),
                fill(t["entry_fill"]),
                int(t["initial_stop"]),
                int(t["qty_at_entry"]),
                tuple(fill(f) for f in t["exits"]),
                Decimal(str(t["net"])),
                Decimal(str(t["r"])),
                Decimal(str(t["r_multiple"])),
                bool(t["reached_tp1"]),
                Decimal(str(t["mae_r"])),
                Decimal(str(t["mfe_r"])),
                int(t["missing_bars"]),
                bool(t["shadow"]),
            )
        )
    return out


def run_metrics(store: ArtifactStore, run: str) -> dict[str, Any]:
    """``{"source", "metrics"}``: report.json (at the run root or under ``report/``),
    else the engine's summarize over trades.json."""
    try:
        files = [f["name"] for f in store.files(run)]
    except RunError:
        files = []  # a missing run keeps its old answer: no source, no metrics
    name = _report_name(files, "report.json")
    report = None if name is None else _json(store, run, name)
    if isinstance(report, dict) and isinstance(report.get("metrics"), dict):
        return {"source": "report.json", "metrics": report["metrics"]}
    trades = _json(store, run, "trades.json")
    if not isinstance(trades, list):
        return {"source": None, "metrics": None}
    from fse.analytics.metrics import MetricsCfg, metrics_to_jsonable, summarize

    manifest = _json(store, run, "run_manifest.json") or {}
    sessions = {date.fromisoformat(s) for s in manifest.get("sessions_evaluated", [])}
    real = [t for t in trades_from_json(trades) if not t.shadow]
    sessions |= {t.setup_key.session for t in real}
    metrics = metrics_to_jsonable(summarize(real, sorted(sessions), MetricsCfg()), places=6)
    return {"source": "trades.json", "metrics": metrics}


def _number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None or isinstance(value, dict | list):
        return None
    try:
        return Decimal(str(value))
    except ArithmeticError:
        return None


def compare(store: ArtifactStore, runs: list[str]) -> dict[str, Any]:
    if not 1 <= len(runs) <= 4:
        raise RunError("compare takes 1 to 4 runs")
    columns = [{"run": r, **run_metrics(store, r)} for r in runs]
    base = columns[0]["metrics"] or {}
    keys = sorted({k for c in columns for k in (c["metrics"] or {})})
    rows = []
    for key in keys:
        cells = []
        b = _number(base.get(key))
        for c in columns:
            v = (c["metrics"] or {}).get(key)
            n = _number(v)
            cells.append({"value": v, "delta": None if b is None or n is None else str(n - b)})
        rows.append({"metric": key, "cells": cells})
    return {"runs": runs, "rows": rows}
