"""Property 53: Decision-log completeness.

*For any* synthetic session set, a backtest writes exactly one decision-log
entry per evaluated Decision_Time, in Decision_Time order, each validating
against the decision-log schema (missing Snapshots marked, every enabled Gate
result, Rejection_Reasons, Grade, orders, fills since the previous
Decision_Time); sessions missing Snapshots or bars are skipped and listed in
the Run_Manifest.

Each example writes a synthetic Data_Cache (``tests/strategies/backtest_inputs``
with ``gaps=True``: a session can lose every Snapshot of one symbol and metric
or every bar of one instrument) and runs :func:`run_backtest` with a generated
Strategy_Config. The expected values come from the generated inputs:

- **Run_Manifest**: ``sessions_evaluated`` lists the sessions with every input,
  ``sessions_skipped`` each other session with its missing data types and
  names (Req 18.3).
- **One entry per Decision_Time** of each evaluated session at the configured
  cadence, in order, and none for a skipped session (Req 18.6).
- **Entry schema** (Req 18.7): exactly the documented fields; ``map`` has every
  configured symbol and metric, with ``{"missing": true}`` exactly when no
  Snapshot has ``asOf`` at or before ``t``, else that latest ``asOf`` with the
  King, Floor and Ceiling fields; ``futures`` has each instrument's tick close
  of its latest bar closed at or before ``t``; each setup lists every Gate in
  order (``disabled`` exactly for the disabled ones), the Rejection_Reasons
  (``no_conversion_price`` first for an unpriced setup, then each failed Gate
  in order) and a Grade that follows from them; each order is a planner
  intent type.
- **Fills since the previous Decision_Time**: every logged fill is on a bar
  that opened after ``t_prev - 60 s`` and closed at or before ``t`` (Req 5.3).
  Each fill of the trade list whose bar closed at or before the run's last
  Decision_Time is logged exactly once, and no other fill is.

Pinned example: the early-close session of
:func:`~tests.strategies.backtest_inputs.early_close_hold` at the default 60 s
cadence (390 entries). Its trade is closed at the 13:00 Flat_Deadline, so the
closing fill must be logged at 13:01.

**Validates: Requirements 18.3, 18.6, 18.7**
"""

from __future__ import annotations

import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Final

from hypothesis import event, example, given

from fse.backtest.decision_log import DECISION_LOG_FILE_NAME
from fse.backtest.manifest import MANIFEST_FILE_NAME
from fse.backtest.runner import BacktestResult
from fse.config.schema import StrategyConfig
from fse.config.schema.gates import GATE_IDS
from fse.engine.gates.registry import NO_CONVERSION_PRICE
from fse.logio.canonical_json import dumps, to_jsonable
from tests.strategies.backtest_inputs import (
    CALENDAR,
    INSTRUMENTS,
    KEYS,
    MINUTE,
    MarketInputs,
    SessionInputs,
    early_close_hold,
    fixed_config,
    market_inputs,
    run,
    strategy_configs,
    write_cache,
    write_calendars,
)

ENTRY_FIELDS: Final = frozenset(
    {
        "session", "t", "t_ny", "map", "snapshot_age_ns", "futures", "regime",
        "regime_measures", "map_grade", "setups", "skips", "orders", "management",
        "fills", "bar_events", "external_blocks", "lockouts", "cards",
    }
)  # fmt: skip
SETUP_FIELDS: Final = frozenset(
    {
        "key", "detector_id", "entry", "stop", "targets", "exit_mode", "source", "inputs",
        "gates", "rejections", "grade", "sizing", "placement",
    }
)  # fmt: skip
MAP_FIELDS: Final = frozenset({"asOf", "asOf_ny", "king", "floor", "ceiling"})
ORDER_TYPES: Final = frozenset({"PlaceBracket", "ModifyOrder", "CancelOrder", "SubmitExit"})
GRADES: Final = frozenset({"A_Plus", "Alert_2R", "Pass"})


def check_map(entry: dict[str, Any], s: SessionInputs, t: int) -> None:
    assert set(entry["map"]) == {f"{sym}/{m}" for sym, m in KEYS}
    for symbol, metric in KEYS:
        logged = entry["map"][f"{symbol}/{metric}"]
        seen = [x for x in s.snapshots_of(symbol, metric) if x.as_of_ns <= t]
        if not seen:
            assert logged == {"missing": True}, (symbol, metric, t)
            event("map: missing marker")
        else:
            assert set(logged) == MAP_FIELDS, logged
            assert logged["asOf"] == seen[-1].as_of_ns


def check_futures(entry: dict[str, Any], s: SessionInputs, t: int) -> None:
    assert set(entry["futures"]) == set(INSTRUMENTS)
    for instrument in INSTRUMENTS:
        closed = [b for b in s.bars_of(instrument) if b.close_ns <= t]
        assert entry["futures"][instrument] == closed[-1].c_t


def check_setup(setup: dict[str, Any], cfg: StrategyConfig) -> None:
    assert set(setup) == SETUP_FIELDS
    gates = setup["gates"]
    assert [g["id"] for g in gates] == list(GATE_IDS)
    for g in gates:
        if cfg.gates.enabled(g["id"]):
            assert g["result"] in {"pass", "fail"}, g
        else:
            assert g["result"] == "disabled", g
    failing = [g["id"] for g in gates if g["result"] == "fail"]
    reasons = [r["reason"] for r in setup["rejections"]]
    unpriced = bool(reasons) and reasons[0] == NO_CONVERSION_PRICE
    assert (reasons[1:] if unpriced else reasons) == failing, (reasons, failing)
    grade = setup["grade"]
    assert grade in GRADES
    if unpriced or len(failing) > 1 or (failing and failing != ["min_reward_risk"]):
        assert grade == "Pass", (reasons, grade)
    elif not failing:
        assert grade == "A_Plus", (reasons, grade)
    else:
        assert grade in {"Alert_2R", "Pass"}
    event(f"grade: {grade}")


def fill_key(fill: object) -> str:
    return dumps(to_jsonable(fill))


def skipped_record(s: SessionInputs) -> dict[str, Any]:
    """The Run_Manifest's ``sessions_skipped`` item for ``s`` (Req 18.3)."""
    kinds = [
        k for k, names in (("snapshots", s.missing_snapshots), ("bars", s.missing_bars)) if names
    ]
    return {
        "date": s.session.isoformat(),
        "missing": kinds,
        "names": [*s.missing_snapshots, *s.missing_bars],
    }


def check_run(market: MarketInputs, cfg: StrategyConfig, result: BacktestResult) -> None:
    manifest = json.loads((result.run_dir / MANIFEST_FILE_NAME).read_text(encoding="utf-8"))
    assert manifest["status"] == "completed"
    assert manifest["sessions_evaluated"] == [s.session.isoformat() for s in market.evaluated]
    assert manifest["sessions_skipped"] == [skipped_record(s) for s in market.sessions if s.skipped]

    text = (result.run_dir / DECISION_LOG_FILE_NAME).read_text(encoding="utf-8")
    entries = [json.loads(line) for line in text.splitlines()]
    cadence = cfg.time.decision_cadence_s
    grid = [(s, t) for s in market.evaluated for t in CALENDAR.decision_times(s.session, cadence)]
    assert [(e["session"], e["t"]) for e in entries] == [
        (s.session.isoformat(), t) for s, t in grid
    ]
    assert result.decision_times == len(entries)

    logged: Counter[str] = Counter()
    t_prev: int | None = None
    for entry, (s, t) in zip(entries, grid, strict=True):
        assert set(entry) == ENTRY_FIELDS, set(entry) ^ ENTRY_FIELDS
        check_map(entry, s, t)
        check_futures(entry, s, t)
        for setup in entry["setups"]:
            check_setup(setup, cfg)
        for order in entry["orders"]:
            assert order["type"] in ORDER_TYPES, order
            event(f"order: {order['type']}")
        for f in entry["fills"]:
            opened = f["fill"]["bar_open_ns"]
            assert opened + MINUTE <= t, (opened, t)
            assert t_prev is None or opened > t_prev - MINUTE, (opened, t_prev, t)
            logged[dumps(f["fill"])] += 1
        if entry["lockouts"]:
            event("lockout logged")
        t_prev = t

    last_t = grid[-1][1] if grid else None
    expected: Counter[str] = Counter()
    for trade in result.trades:
        for fill in (trade.entry_fill, *trade.exits):
            if last_t is not None and fill.bar_open_ns + MINUTE <= last_t:
                expected[fill_key(fill)] += 1
    assert logged == expected
    event(f"fills logged: {min(sum(logged.values()), 3)}")
    event(f"sessions skipped: {len(market.sessions) - len(market.evaluated)}")


# Feature: skylit-futures-strategy-engine, Property 53: Decision-log completeness
@given(market=market_inputs(gaps=True), cfg=strategy_configs())
@example(market=early_close_hold(), cfg=fixed_config(60))
def test_one_valid_entry_per_evaluated_decision_time(
    market: MarketInputs, cfg: StrategyConfig
) -> None:
    with tempfile.TemporaryDirectory(prefix="fse-p53-") as tmp:
        root = Path(tmp)
        calendars = write_calendars(root / "calendars")
        cache = write_cache(root / "cache", market)
        result = run(cache, calendars, cfg, market.data_range, root / "run")
        check_run(market, cfg, result)
