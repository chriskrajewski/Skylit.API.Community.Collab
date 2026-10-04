"""Property 54: Cadence comparison.

*For any* sessions with mixed stored Snapshot and bar intervals and any
cadence set, the comparison runs on exactly the sessions whose stored
intervals are no longer than the shortest cadence, lists included and
excluded dates, and reports each pairwise difference as the shorter
cadence's value minus the longer one's.

**Inputs.** A light Data_Cache over the 9 March sessions of the test
calendar, written per example. Per session: no data at all, or Snapshots at
resolution ``1s`` or ``1m`` written with no storage interval or one of 5, 15,
60 and 300 s, 1-minute bars plus bars at some of 1, 5 and 15 s, and
sometimes bars for MES only. A holdout fraction, a requested range, 2 to 4
cadences from 1 s to 3600 s (the shortest mostly 60 s or longer, so sessions
qualify often), listed in any order, and a seed.

**Model.** A session's stored Snapshot interval is the longer of its
resolution and its storage interval; its stored bar interval is the longest,
over MES and MNQ, of each one's shortest bar interval (none when an
instrument has no bars). The compared sessions are the requested sessions
before the Holdout_Period (the newest ``ceil(fraction x n)`` sessions with
full data) whose two intervals are at most the shortest cadence.

**Checks.** With no requested session outside the Holdout_Period, or none
eligible, the comparison is refused and nothing is written. Otherwise every
cadence's task runs on exactly the compared sessions; ``cadence.json`` lists
them as included and every other requested session outside the
Holdout_Period as excluded, with its intervals; each cadence's values equal
the fake evaluator's plan for that config and those sessions (expectancy
not available with zero trades); and each pairwise difference is the
shorter cadence's value minus the longer's, or not available when either is.

**Validates: Requirements 18.8, 18.10**
"""

from __future__ import annotations

import io
import json
import math
import tempfile
from datetime import date
from fractions import Fraction
from pathlib import Path
from typing import Any, Final

import pytest
from hypothesis import event, given
from hypothesis import strategies as st

from fse.analytics.metrics import round_fraction
from fse.backtest.manifest import DataRange
from fse.config.hashing import config_hash
from fse.engine.types import NotApplicable
from fse.experiments.cadence import (
    CADENCE_FILE_NAME,
    CadenceError,
    cadence_configs,
    run_cadence_comparison,
)
from fse.experiments.runner import ExperimentInputError, ExperimentResult
from fse.logio import LogWriter, Redactor
from tests.strategies.backtest_inputs import FAKE_SECRET, SESSIONS, write_calendars
from tests.strategies.experiment_inputs import (
    LightSession,
    fake_evaluator,
    light_config,
    planned,
    read_task,
    write_light_cache,
)

CADENCES: Final = (1, 5, 15, 30, 60, 300)
RESOLUTION_S: Final = {"1s": 1, "1m": 60}
METRICS: Final = ("setup_keys", "entry_fills", "trades_per_session", "expectancy_r")


@pytest.fixture(scope="module")
def calendar_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_calendars(tmp_path_factory.mktemp("p54") / "calendars")


@st.composite
def stored(draw: st.DrawFn, session: date) -> LightSession | None:
    if not draw(st.integers(0, 5)):
        return None
    extra = draw(st.sets(st.sampled_from((1, 5, 15)), max_size=2))
    return LightSession(
        session,
        resolution=draw(st.sampled_from(("1s", "1s", "1s", "1m"))),
        storage_s=draw(st.sampled_from((None, None, 5, 15, 60, 300))),
        bar_intervals=(60, *sorted(extra)),
        instruments=("MES",) if not draw(st.integers(0, 6)) else ("MES", "MNQ"),
    )


@st.composite
def caches(draw: st.DrawFn) -> list[LightSession | None]:
    return [draw(stored(d), label=d.isoformat()) for d in SESSIONS]


@st.composite
def cadence_sets(draw: st.DrawFn) -> list[int]:
    """2 to 4 cadences; the shortest is drawn first, mostly 60 s or longer."""
    shortest = draw(st.sampled_from((1, 5, 15, 60, 60, 60, 300)), label="shortest")
    longer = [c for c in (*CADENCES, 600, 3600) if c > shortest]
    rest = draw(st.lists(st.sampled_from(longer), min_size=1, max_size=3, unique=True))
    return draw(st.permutations([shortest, *rest]), label="order")


@st.composite
def ranges(draw: st.DrawFn) -> DataRange:
    first = draw(st.integers(0, len(SESSIONS) - 1), label="first")
    last = draw(st.integers(first, len(SESSIONS) - 1), label="last")
    return DataRange(SESSIONS[first], SESSIONS[last])


def _intervals(s: LightSession | None) -> tuple[int | None, int | None]:
    if s is None:
        return None, None
    snapshot = max(RESOLUTION_S[s.resolution], s.storage_s or 0)
    bar = None if len(s.instruments) < 2 else min(s.bar_intervals)
    return snapshot, bar


def _plan_values(cfg_hash: str, sessions: tuple[date, ...]) -> dict[str, Any]:
    plan = planned(cfg_hash, sessions)
    if plan is None:
        return dict.fromkeys(METRICS)
    expectancy = plan["expectancy_r"]
    return {
        "setup_keys": Fraction(plan["setup_keys"]),
        "entry_fills": Fraction(plan["trade_count"]),
        "trades_per_session": Fraction(plan["trade_count"], len(sessions)),
        "expectancy_r": None if isinstance(expectancy, NotApplicable) else expectancy,
    }


def _shown(metric: str, value: Fraction | None) -> object:
    if value is None:
        return "not available"
    if metric in ("setup_keys", "entry_fills"):
        return int(value)
    return str(round_fraction(value, 6))


@given(
    stored_sessions=caches(),
    fraction=st.sampled_from((0.05, 0.1, 0.2, 0.34, 0.5)),
    requested=ranges(),
    cadences=cadence_sets(),
    seed=st.integers(0, 2**63 - 1),
)
def test_cadence_comparison(
    calendar_dir: Path,
    stored_sessions: list[LightSession | None],
    fraction: float,
    requested: DataRange,
    cadences: list[int],
    seed: int,
) -> None:
    by_day = dict(zip(SESSIONS, stored_sessions, strict=True))
    with_data = [d for d, s in by_day.items() if s is not None and len(s.instruments) == 2]
    held = math.ceil(Fraction(repr(fraction)) * len(with_data))
    holdout = with_data[-held:] if with_data else []
    outside = [d for d in SESSIONS if d in requested and (not holdout or d < holdout[0])]
    shortest = min(cadences)
    supported = {
        d for d in outside if all(x is not None and x <= shortest for x in _intervals(by_day[d]))
    }
    included = tuple(d for d in outside if d in supported)
    excluded = [d for d in outside if d not in supported]
    base = light_config(holdout_fraction=fraction)
    writer = LogWriter(Redactor([FAKE_SECRET]), stdout=io.StringIO(), stderr=io.StringIO())

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache_dir = write_light_cache(root / "cache", [s for s in stored_sessions if s])
        out = root / "cadence"

        def compare() -> ExperimentResult:
            return run_cadence_comparison(
                base, requested, cadences=cadences, cache_dir=cache_dir,
                calendar_dir=calendar_dir, out_dir=out, writer=writer, seed=seed,
                evaluator=fake_evaluator, code_version="test-version",
            )  # fmt: skip

        if not included:
            expected = ExperimentInputError if not outside else CadenceError
            with pytest.raises(expected) as caught:
                compare()
            if outside:
                assert f"shortest compared cadence of {shortest} s" in str(caught.value)
            assert not out.exists()
            event("refused")
            return
        result = compare()
        event(f"{len(excluded)} excluded")
        ordered = sorted(cadences)
        configs = cadence_configs(base, cadences)
        assert [c.cfg.time.decision_cadence_s for c in configs] == ordered
        hashes = [config_hash(c.cfg) for c in configs]
        assert [o.config_hash for o in result.outcomes] == hashes
        assert result.sessions == included
        assert list(result.excluded) == excluded
        for o in result.outcomes:
            task = read_task(result.run_dir / "configs" / f"{o.index:03d}-{o.name}")
            assert task["only_sessions"] == [d.isoformat() for d in included]
            if o.result is not None:
                assert o.result.sessions == included

        saved = json.loads((result.run_dir / CADENCE_FILE_NAME).read_text())
        assert saved["cadences_s"] == ordered
        assert saved["included_sessions"] == [d.isoformat() for d in included]
        assert [e["session"] for e in saved["excluded_sessions"]] == [
            d.isoformat() for d in excluded
        ]
        for e in saved["excluded_sessions"]:
            snapshot, bar = _intervals(by_day[date.fromisoformat(e["session"])])
            assert (e["snapshot_interval_s"], e["bar_interval_s"]) == (snapshot, bar)

        values = [_plan_values(h, included) for h in hashes]
        for row, cadence, v in zip(saved["cadences"], ordered, values, strict=True):
            assert row["cadence_s"] == cadence
            for m in METRICS:
                assert row[m] == _shown(m, v[m])
        diffs = saved["differences"]
        assert len(diffs) == len(ordered) * (len(ordered) - 1) // 2
        pairs = [(i, j) for i in range(len(ordered)) for j in range(i + 1, len(ordered))]
        for d, (i, j) in zip(diffs, pairs, strict=True):
            assert (d["shorter_s"], d["longer_s"]) == (ordered[i], ordered[j])
            for m in METRICS:
                a, b = values[i][m], values[j][m]
                assert d[m] == _shown(m, None if a is None or b is None else a - b)
