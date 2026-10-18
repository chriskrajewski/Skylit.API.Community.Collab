"""Backtest performance budget (Req 18.12), marker ``perf``, run manually.

``python -m pytest -m perf tests/perf/test_backtest_budget.py`` on the
Operator's machine. The test builds a synthetic Data_Cache of 250 sessions
(not timed), then runs the shipped Playbook_Baseline at its 60 s
Decision_Cadence over all of them and checks that the Run_Manifest is written
within 10 minutes of wall-clock time from the start of the run.

**Config.** ``configs/playbook_baseline.yaml`` with the test-only overlay of
fake Operator values from ``tests/unit/test_playbook_baseline`` (fees and
``regime.min_abs_value``); nothing is written to the file.

**Cache** (seeded, so every build is the same): a weekday calendar from
2025-01-02 with no holiday. Per session, for each of the baseline's five
symbols and both metrics, one Snapshot every 60 s from 09:29:30 to 15:59:30
(a random subset of a strike grid around a random-walk spot, values from a
fixed set with both signs); MES and MNQ 1-minute bars as tick random walks
from 09:20 through the Flat_Deadline bar; a VIX daily record. The first
sessions also give the later ones their trailing Regime medians.

No network, no key: the run reads only the local Data_Cache.

**Validates: Requirements 18.12**
"""

from __future__ import annotations

import io
import random
from collections.abc import Sequence
from datetime import date, time, timedelta
from pathlib import Path
from time import monotonic
from typing import Final

import pytest

from fse.backtest.manifest import MANIFEST_FILE_NAME, DataRange
from fse.backtest.runner import run_backtest
from fse.calendars import ECONOMIC_EVENTS_FILE, EXCHANGE_CALENDAR_FILE, ROLL_CALENDAR_FILE
from fse.config.loader import LoadOk, validate_data
from fse.config.schema import StrategyConfig
from fse.data.aux_stores import VixDailyRecord
from fse.data.cache import CACHE_WINDOW_NS, CacheWindowKey, DataCache, HeatmapView
from fse.engine.types import Bar, Metric, Snapshot
from fse.logio import LogWriter, Redactor
from fse.pit.market_view import MAP_METRICS
from fse.timekit import NS_PER_SECOND, SessionCalendar, SessionTimes, ny_instant
from tests.strategies.backtest_inputs import CONTRACTS, FAKE_SECRET, make_bar, make_snapshot
from tests.unit.test_playbook_baseline import (
    BASELINE,
    FAKE_OPERATOR_VALUES,
    overlay,
    parsed_baseline,
)

pytestmark = pytest.mark.perf

SESSION_COUNT: Final = 250
BUDGET_S: Final = 600.0
CADENCE_S: Final = 60
SEED: Final = 18_12
MINUTE: Final = 60 * NS_PER_SECOND
FIRST: Final = date(2025, 1, 2)

SPOTS: Final = {"SPX": 5800.0, "SPY": 580.0, "QQQ": 500.0, "NDX": 20000.0, "NDXP": 20000.0}
STEPS: Final = {"SPX": 10.0, "SPY": 1.0, "QQQ": 1.0, "NDX": 50.0, "NDXP": 50.0}
VALUES: Final = (-3.0e9, -2.0e9, -1.0e9, -0.5e9, 0.0, 0.5e9, 0.9e9, 1.0e9, 2.0e9, 3.0e9)
TICKS: Final = {"MES": (5800 * 4, 12), "MNQ": (20000 * 4, 40)}  # start price, largest step
VIEW: Final = HeatmapView()


def _sessions() -> tuple[date, ...]:
    days: list[date] = []
    d = FIRST
    while len(days) < SESSION_COUNT:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return tuple(days)


SESSIONS: Final = _sessions()
LAST: Final = SESSIONS[-1]


def _calendar_files(directory: Path) -> Path:
    directory.mkdir(parents=True)
    covers = f"covers: {{first: {FIRST}, last: {LAST}}}\n"
    files = {
        EXCHANGE_CALENDAR_FILE: covers + "holidays: []\nearly_closes: []\n",
        ECONOMIC_EVENTS_FILE: covers + "events: []\n",
        ROLL_CALENDAR_FILE: covers
        + "instruments:\n"
        + f"  ES:\n    - {{first: {FIRST}, last: {LAST}, contract: ESZ5}}\n"
        + f"  NQ:\n    - {{first: {FIRST}, last: {LAST}, contract: NQZ5}}\n",
    }
    for name, text in files.items():
        (directory / name).write_text(text, encoding="utf-8")
    return directory


def _baseline() -> StrategyConfig:
    loaded = validate_data(overlay(parsed_baseline(), FAKE_OPERATOR_VALUES), source=str(BASELINE))
    assert isinstance(loaded, LoadOk), loaded
    cfg = loaded.config
    assert cfg.time.decision_cadence_s == CADENCE_S
    return cfg


def _pairs(rng: random.Random, symbol: str, spot: float) -> list[tuple[float, float]]:
    step = STEPS[symbol]
    center = round(spot / step) * step
    grid = [center + step * k for k in range(-10, 11)]
    return [(k, rng.choice(VALUES)) for k in sorted(rng.sample(grid, rng.randint(6, 14)))]


def _snapshots(rng: random.Random, session: date, symbol: str) -> dict[Metric, list[Snapshot]]:
    first = ny_instant(session, time(9, 29, 30))
    times = [first + k * MINUTE for k in range(391)]  # 09:29:30 to 15:59:30
    out: dict[Metric, list[Snapshot]] = {}
    for metric in MAP_METRICS:
        spot = SPOTS[symbol]
        pairs = _pairs(rng, symbol, spot)
        snaps: list[Snapshot] = []
        for t in times:
            spot += rng.uniform(-0.2, 0.2) * STEPS[symbol]
            if rng.random() < 0.1:
                pairs = _pairs(rng, symbol, spot)
            snaps.append(make_snapshot(session, symbol, metric, t, round(spot, 2), pairs))
        out[metric] = snaps
    return out


def _bars(rng: random.Random, cal: SessionCalendar, session: date, instrument: str) -> list[Bar]:
    price, step = TICKS[instrument]
    price += rng.randint(-4 * step, 4 * step)
    start = ny_instant(session, time(9, 20))
    count = (cal.flat_deadline(session) - start) // MINUTE + 1
    bars: list[Bar] = []
    for k in range(count):
        o = price
        c = o + rng.randint(-step, step)
        h = max(o, c) + rng.randint(0, step // 3)
        low = min(o, c) - rng.randint(0, step // 3)
        bars.append(make_bar(instrument, start + k * MINUTE, o, h, low, c))
        price = c
    return bars


def _write_cache(root: Path, cal: SessionCalendar, symbols: Sequence[str]) -> Path:
    with DataCache(root, calendar=cal) as cache:
        vix: list[VixDailyRecord] = []
        for i, session in enumerate(SESSIONS):
            rng = random.Random(SEED * 1000 + i)
            pull_start, _ = cal.pull_window(session)
            for symbol in symbols:
                for metric, snaps in _snapshots(rng, session, symbol).items():
                    windows: dict[int, list[Snapshot]] = {}
                    for s in snaps:
                        start = pull_start + (s.as_of_ns - pull_start) // CACHE_WINDOW_NS * (
                            CACHE_WINDOW_NS
                        )
                        windows.setdefault(start, []).append(s)
                    for start, group in windows.items():
                        key = CacheWindowKey.for_view(symbol, metric, VIEW, session, start)
                        cache.write_window(key, group, "range", view=VIEW)
            for instrument in TICKS:
                cache.bars.write_session(
                    instrument, 60, session, _bars(rng, cal, session, instrument),
                    contract=CONTRACTS[instrument], source="atlas",
                )  # fmt: skip
            rth_open = cal.rth_open(session)
            vix.append(
                VixDailyRecord(
                    session=session,
                    open=round(rng.uniform(12.0, 30.0), 2),
                    open_at_ns=rth_open + MINUTE,
                    close=round(rng.uniform(12.0, 30.0), 2),
                    close_at_ns=cal.rth_close(session),
                    source="import",
                )
            )
        cache.vix.write_daily(vix)
    return root


def test_a_250_session_baseline_backtest_writes_its_manifest_within_10_minutes(
    tmp_path: Path,
) -> None:
    cfg = _baseline()
    times = cfg.account.session_times(SessionTimes())
    cal = SessionCalendar(FIRST, LAST, times=times)
    assert len(cal.sessions(FIRST, LAST)) == SESSION_COUNT
    calendars = _calendar_files(tmp_path / "calendars")
    cache_root = _write_cache(tmp_path / "cache", cal, tuple(cfg.data.symbols))
    writer = LogWriter(Redactor([FAKE_SECRET]), stdout=io.StringIO(), stderr=io.StringIO())
    out = tmp_path / "run"

    started = monotonic()
    with DataCache(cache_root, calendar=cal) as cache:
        result = run_backtest(
            cfg, DataRange(FIRST, LAST), cache, out, SEED, writer=writer, calendar_dir=calendars
        )
    elapsed = monotonic() - started

    assert (out / MANIFEST_FILE_NAME).is_file()
    assert result.manifest.status == "completed"
    assert len(result.manifest.sessions_evaluated) == SESSION_COUNT
    assert elapsed <= BUDGET_S, f"the run took {elapsed:.0f} s; the budget is {BUDGET_S:.0f} s"
