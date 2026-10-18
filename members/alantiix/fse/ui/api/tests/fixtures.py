"""Run folders built with the engine's own types and writers (no hand-typed JSON shapes)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from fse.engine.types import Fill, SetupKey, Trade
from fse.logio.canonical_json import to_jsonable

NS = 1_000_000_000
BASE_NS = 1_791_300_000 * NS


def trade(
    i: int,
    r_multiple: str,
    *,
    net: str | None = None,
    shadow: bool = False,
    session: date = date(2026, 10, 6),
) -> Trade:
    r = Decimal("25.00")
    rm = Decimal(r_multiple)
    entry = Fill(f"e{i}", BASE_NS + i * 600 * NS, 23600, 1, Decimal("0.62"))
    exit_ = Fill(
        f"x{i}", BASE_NS + i * 600 * NS + 300 * NS, 23600 + int(rm * 20), 1, Decimal("0.62")
    )
    return Trade(
        SetupKey("MES", "floor_ceiling_bounce", 5900.0, "long", session, i + 1),
        entry,
        23580,
        1,
        (exit_,),
        Decimal(net) if net else rm * r,
        r,
        rm,
        rm > 0,
        Decimal("0.5"),
        max(rm, Decimal(0)),
        0,
        shadow,
    )


def backtest_run(root: Path, name: str, rs: list[str], *, shadow: int = 0) -> Path:
    run = root / name
    run.mkdir(parents=True)
    trades = [trade(i, r) for i, r in enumerate(rs)]
    trades += [trade(100 + i, "-1", shadow=True) for i in range(shadow)]
    (run / "trades.json").write_text(json.dumps(to_jsonable(trades)))
    (run / "run_manifest.json").write_text(
        json.dumps(
            {
                "run_id": name,
                "kind": "backtest",
                "config_hash": "3f1a9c",
                "status": "complete",
                "config_path": "configs/playbook_baseline.yaml",
                "seed": 7,
                "data_range": {"start": "2026-10-06", "end": "2026-10-07"},
                "sessions_evaluated": ["2026-10-06", "2026-10-07"],
                "outputs": [],
                "warnings": [],
            }
        )
    )
    (run / "gate_funnel.json").write_text(json.dumps({"gates": [], "accepted": {}}))
    (run / "shadow_trades.csv").write_text("setup,gate,r\nfcb,min_rr,-1.00\nfcb,stale_map,0.50\n")
    return run
