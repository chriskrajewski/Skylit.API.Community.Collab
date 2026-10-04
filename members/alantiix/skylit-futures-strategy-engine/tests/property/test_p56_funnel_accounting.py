"""Property 56: Funnel accounting.

*For any* run, each Setup_Key has exactly one final status and the four status
counts sum to the distinct Setup_Key count; rejected keys list exactly the
Gates that failed in their governing evaluation and have exactly one
Shadow_Trade; first-failing counts sum to the rejected count; the
co-rejection matrix is symmetric with a diagonal equal to each Gate's failing
count; untapped keys are excluded from Gate counts; and the per-session top-3
lists follow count then Gate order.

Each example runs :func:`run_backtest` on a synthetic Data_Cache
(``tests/strategies/backtest_inputs``, skipped sessions possible) at a 60,
300 or 900 s Decision_Cadence, so keys are emitted at many Decision_Times
before and during their Taps, and checks
the run's ``setups.json`` and ``gate_funnel.json`` against the decision log,
the trade list and a reference Tap count over the stored bars
(``tests/reference/taps``):

- **Keys**: the records hold each distinct Setup_Key of the decision log once,
  with its first emission time, and the status counts sum to their number.
- **Governing evaluation**: an untapped key has no reference Tap with its
  ``tap_seq``, no entry fill and no Shadow_Trade. Any other key's first Tap is
  its reference Tap start or an entry fill (its accepted entry, or its
  governing Shadow_Trade's entry), and no later than either; the governing
  Decision_Time is the latest emission at or before it, else the first
  emission.
- **Status**: rejected exactly when the governing emission's logged Gate
  results have a failure, listing those Gates in order, with one
  Shadow_Trade placed at the governing Decision_Time; filled exactly when the
  key's accepted entry filled; cancelled with a cause otherwise.
- **Funnel**: per-Gate failing, only-failing and first-failing counts,
  the co-rejection matrix and the per-session top 3 recomputed from the
  rejected records alone.

Pinned example: :func:`~tests.strategies.backtest_inputs.funnel_case`, a run
with every final status and nine Shadow_Trades.

**Validates: Requirements 19.1, 19.3, 19.4, 19.5, 19.6, 19.7, 19.8, 19.17**
"""

from __future__ import annotations

import csv
import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from hypothesis import event, example, given
from hypothesis import strategies as st

from fse.backtest.decision_log import DECISION_LOG_FILE_NAME
from fse.backtest.runner import (
    FUNNEL_FILE_NAME,
    REPORT_INPUTS_FILE_NAME,
    SETUPS_FILE_NAME,
    SHADOW_TRADES_FILE_NAME,
)
from fse.backtest.shadow import FINAL_STATUSES
from fse.config.schema import StrategyConfig
from fse.logio.canonical_json import dumps, to_jsonable
from tests.reference.taps import reference_taps
from tests.strategies.backtest_inputs import (
    CALENDAR,
    MarketInputs,
    funnel_case,
    market_inputs,
    run,
    strategy_configs,
    with_cadence,
    write_cache,
    write_calendars,
)

type Emission = tuple[int, tuple[str, ...], tuple[str, str, float]]


def emissions(log: str) -> dict[str, list[Emission]]:
    """Each Setup_Key's emissions: Decision_Time, failing Gates and source Node."""
    out: dict[str, list[Emission]] = {}
    for line in log.splitlines():
        entry = json.loads(line)
        seen: set[str] = set()
        for s in entry["setups"]:
            key = dumps(s["key"])
            if key in seen:
                continue
            seen.add(key)
            failing = tuple(g["id"] for g in s["gates"] if g["result"] == "fail")
            src = s["source"]
            out.setdefault(key, []).append(
                (entry["t"], failing, (src["symbol"], src["metric"], src["strike"]))
            )
    return out


PINNED = funnel_case()


# Feature: skylit-futures-strategy-engine, Property 56: Funnel accounting
@example(market=PINNED[0], cfg=PINNED[1], cadence=300)
@given(
    market=market_inputs(gaps=True, max_sessions=2),
    cfg=strategy_configs(),
    cadence=st.sampled_from((60, 300, 900)),
)
def test_every_setup_key_has_one_status_and_the_funnel_adds_up(
    market: MarketInputs, cfg: StrategyConfig, cadence: int
) -> None:
    cfg = with_cadence(cfg, cadence)
    with tempfile.TemporaryDirectory(prefix="fse-p56-") as tmp:
        root = Path(tmp)
        calendars = write_calendars(root / "calendars")
        cache = write_cache(root / "cache", market)
        result = run(cache, calendars, cfg, market.data_range, root / "run")
        d = result.run_dir
        log = (d / DECISION_LOG_FILE_NAME).read_text(encoding="utf-8")
        records: list[dict[str, Any]] = json.loads((d / SETUPS_FILE_NAME).read_text("utf-8"))
        funnel: dict[str, Any] = json.loads((d / FUNNEL_FILE_NAME).read_text("utf-8"))
        inputs = json.loads((d / REPORT_INPUTS_FILE_NAME).read_text("utf-8"))
        with (d / SHADOW_TRADES_FILE_NAME).open(encoding="utf-8", newline="") as fh:
            shadow_rows = list(csv.reader(fh))[1:]

    emitted = emissions(log)
    keys = [dumps(r["key"]) for r in records]
    assert sorted(keys) == sorted(emitted)  # every distinct key, exactly once
    statuses = Counter(r["status"] for r in records)
    assert set(statuses) <= set(FINAL_STATUSES)
    assert funnel["setup_keys"] == len(records)
    assert funnel["status_counts"] == {s: statuses.get(s, 0) for s in FINAL_STATUSES}
    assert sum(funnel["status_counts"].values()) == len(emitted)

    fills = {dumps(to_jsonable(t.setup_key)): t.entry_fill.bar_open_ns for t in result.trades}
    taps: dict[tuple[object, int, str], int] = {
        (tap.node, tap.seq, s.session.isoformat()): tap.start
        for s in market.evaluated
        for tap in reference_taps(s, cfg, CALENDAR)
    }
    for r, key in zip(records, keys, strict=True):
        em = emitted[key]
        times = [t for t, _, _ in em]
        failing_at = {t: failing for t, failing, _ in em}
        assert r["first_emitted_at"] == times[0]
        k = r["key"]
        ref = taps.get((em[0][2], k["tap_seq"], k["session"]))
        main = fills.get(key)
        shadow = r["shadow"]
        if r["status"] == "untapped":
            assert (r["governing_at"], r["first_tap_at"], shadow) == (None, None, None)
            assert (r["failing"], r["cause"]) == ([], [])
            assert ref is None
            assert main is None
            continue
        first_tap = r["first_tap_at"]
        governing = max((t for t in times if t <= first_tap), default=times[0])
        assert r["governing_at"] == governing
        shadow_fill = None
        if shadow is not None and shadow["trade"] is not None:
            shadow_fill = shadow["trade"]["entry_fill"]["bar_open_ns"]
        natural = [x for x in (ref, main) if x is not None]
        if not natural or first_tap != min(natural):
            assert first_tap == shadow_fill  # a gap-through Shadow_Trade entry
        assert not natural or first_tap <= min(natural)
        gov_failing = failing_at[governing]
        if gov_failing:
            assert r["status"] == "rejected"
            assert r["failing"] == list(gov_failing)
            assert shadow is not None
            assert shadow["placed_at"] == governing
            if shadow["trade"] is not None:
                assert shadow["trade"]["shadow"] is True
                assert shadow["trade"]["setup_key"] == k
        else:
            assert shadow is None
            assert r["failing"] == []
            assert r["status"] == ("filled" if main is not None else "cancelled")
            assert bool(r["cause"]) == (r["status"] == "cancelled")

    rejected = [r for r in records if r["status"] == "rejected"]
    assert len(shadow_rows) == sum(1 for r in rejected if r["shadow"]["trade"] is not None)
    gates: list[str] = inputs["enabled_gates"]
    order = {g: i for i, g in enumerate(gates)}
    rows = funnel["gates"]
    assert [row["gate_id"] for row in rows] == gates
    for row in rows:
        g = row["gate_id"]
        assert row["failing"] == sum(1 for r in rejected if g in r["failing"])
        assert row["only_failing"] == sum(1 for r in rejected if r["failing"] == [g])
        assert row["first_failing"] == sum(
            1 for r in rejected if min(r["failing"], key=order.__getitem__) == g
        )
    assert sum(row["first_failing"] for row in rows) == len(rejected)
    matrix = funnel["co_rejection"]["rows"]
    for i, a in enumerate(gates):
        assert matrix[i][i] == rows[i]["failing"]
        for j, b in enumerate(gates):
            assert matrix[i][j] == matrix[j][i]
            assert matrix[i][j] == sum(
                1 for r in rejected if a in r["failing"] and b in r["failing"]
            )

    sessions = {s["session"]: s for s in funnel["sessions"]}
    for s in result.manifest.sessions_evaluated:
        day = s.isoformat()
        mine = [r for r in records if r["key"]["session"] == day]
        assert sessions[day]["setup_keys"] == len(mine)
        counts = Counter(g for r in mine if r["status"] == "rejected" for g in r["failing"])
        top = sorted(counts.items(), key=lambda item: (-item[1], order[item[0]]))[:3]
        assert [(t["gate_id"], t["count"]) for t in sessions[day]["top_gates"]] == top

    event(f"statuses: {sorted(statuses)}")
    event(f"shadow trades: {'some' if shadow_rows else 'none'}")
