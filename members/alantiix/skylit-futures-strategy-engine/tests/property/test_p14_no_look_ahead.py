"""Property 14: No look-ahead.

*For any* input set (Snapshots, bars, VIX values, dark-pool prints),
Strategy_Config and instant t, perturbing, adding or removing inputs whose
Observation_Time is after t leaves every decision-log entry at or before t
byte-identical, including chart features, Tap counts, trailing medians,
Futures_Price and conversion factors.

Each example draws synthetic sessions and a Strategy_Config
(``tests/strategies/backtest_inputs``), an instant ``t`` (a Decision_Time of
one of the sessions, or 1 s to one cadence after it), and a second input set
that equals the first up to ``t``. After ``t``, independently per record:

- **Snapshots** (Observation_Time ``asOf``) are removed, replaced by a
  Snapshot with new strikes, values and spot at the same ``asOf``, or kept;
  up to two new Snapshots per symbol and metric are added.
- **Futures bars** (Observation_Time: the close) get new prices or are
  removed; up to three bars are added in free minutes.
- **VIX**: a daily open or close observed after ``t`` gets a new value, and
  1-minute VIX bars closing after ``t`` get new prices or are removed.
- **Dark-pool prints** (Observation_Time ``ts``) are removed or get a new
  price and size, and up to two prints per ticker are added.

Two things are kept so that both runs evaluate the same sessions (Req 18.3
skips a session on whole-session data, which is not a Decision_Time value): a
symbol and metric with Snapshots keeps at least one, and every instrument
with bars keeps the bar that opens at the Flat_Deadline. Each input set is
written to its own Data_Cache and run with :func:`run_backtest`; the
trailing Regime medians are built from each cache.

Checked: the two decision logs hold the same lines, byte for byte, for every
Decision_Time at or before ``t``, and that is every evaluated Decision_Time at
or before ``t``.

**Validates: Requirements 5.3, 5.4, 5.11, 9.1**
"""

from __future__ import annotations

import json
import random
import tempfile
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from hypothesis import event, given
from hypothesis import strategies as st

from fse.backtest.decision_log import DECISION_LOG_FILE_NAME
from fse.backtest.runner import BacktestResult
from fse.config.schema import StrategyConfig
from fse.data.aux_stores import VixDailyRecord
from fse.engine.types import Bar, DarkPoolPrint, Snapshot
from fse.timekit import NS_PER_SECOND, Instant
from tests.strategies.backtest_inputs import (
    CALENDAR,
    DARK_POOL_TICKERS,
    INSTRUMENTS,
    KEYS,
    MINUTE,
    MarketInputs,
    SessionInputs,
    make_snapshot,
    market_inputs,
    random_bar,
    random_pairs,
    random_spot,
    run,
    strategy_configs,
    vix_bar,
    write_cache,
    write_calendars,
)


@dataclass(frozen=True, slots=True)
class Case:
    market: MarketInputs
    cfg: StrategyConfig
    cut: Instant
    seed: int


@st.composite
def cases(draw: st.DrawFn) -> Case:
    market = draw(market_inputs(gaps=True))
    cfg = draw(strategy_configs())
    cadence = cfg.time.decision_cadence_s
    grid = [t for s in market.sessions for t in CALENDAR.decision_times(s.session, cadence)]
    at = draw(st.sampled_from(grid), label="cut Decision_Time")
    offset = draw(st.sampled_from((0, 1, 30, 60, cadence - 1)), label="cut offset s")
    seed = draw(st.integers(0, 2**32 - 1), label="perturbation seed")
    return Case(market, cfg, at + offset * NS_PER_SECOND, seed)


# ---------------------------------------------------------------- inputs after the cut


def new_snapshot(session: date, s: Snapshot, rng: random.Random) -> Snapshot:
    """Another Snapshot of the same symbol and metric at the same ``asOf``."""
    pairs = random_pairs(rng, s.symbol)
    return make_snapshot(session, s.symbol, s.metric, s.as_of_ns, random_spot(rng, s.symbol), pairs)


def later_snapshots(s: SessionInputs, cut: Instant, rng: random.Random) -> list[Snapshot]:
    pull_start, pull_end = CALENDAR.pull_window(s.session)
    start = max(cut + 1, pull_start)
    out: list[Snapshot] = []
    for symbol, metric in KEYS:
        mine = s.snapshots_of(symbol, metric)
        if not mine:
            continue  # a missing symbol and metric stays missing
        kept: list[Snapshot] = []
        for x in mine:
            r = rng.random()
            if x.as_of_ns <= cut or r >= 0.8:
                kept.append(x)
            elif r >= 0.3:
                kept.append(new_snapshot(s.session, x, rng))
        taken = {x.as_of_ns for x in mine}
        for _ in range(rng.randint(0, 2)):
            if start < pull_end:
                at = rng.randrange(start, pull_end)
                if at not in taken:
                    taken.add(at)
                    spot = random_spot(rng, symbol)
                    pairs = random_pairs(rng, symbol)
                    kept.append(make_snapshot(s.session, symbol, metric, at, spot, pairs))
        if not kept:
            kept.append(new_snapshot(s.session, mine[-1], rng))
        out.extend(kept)
    return sorted(out, key=lambda x: (x.as_of_ns, x.symbol, x.metric))


def later_bars(s: SessionInputs, cut: Instant, rng: random.Random) -> list[Bar]:
    deadline = CALENDAR.flat_deadline(s.session)
    pull_start, _ = CALENDAR.pull_window(s.session)
    out: list[Bar] = []
    for instrument in INSTRUMENTS:
        mine = s.bars_of(instrument)
        if not mine:
            continue  # a missing instrument stays missing
        bars: list[Bar] = []
        for b in mine:
            assert b.o_t is not None
            if b.close_ns <= cut:
                bars.append(b)
            elif b.open_ns != deadline and rng.random() < 0.2:
                continue
            elif rng.random() < 0.7:
                bars.append(random_bar(rng, instrument, b.open_ns, b.o_t))
            else:
                bars.append(b)
        # A bar closing after the cut opens after cut - 60 s, on a minute of the session.
        first = pull_start + max(0, -(-(cut - MINUTE + 1 - pull_start) // MINUTE)) * MINUTE
        taken = {b.open_ns for b in mine}
        free = [o for o in range(first, deadline, MINUTE) if o not in taken]
        last_close = mine[-1].c_t
        assert last_close is not None
        for open_ns in rng.sample(free, min(len(free), rng.randint(0, 3))):
            bars.append(random_bar(rng, instrument, open_ns, last_close))
        out.extend(sorted(bars, key=lambda b: b.open_ns))
    return out


def later_vix(s: SessionInputs, cut: Instant, rng: random.Random) -> SessionInputs:
    daily: VixDailyRecord | None = s.vix
    if daily is not None:
        if daily.open_at_ns is not None and daily.open_at_ns > cut:
            daily = replace(daily, open=round(rng.uniform(10.0, 40.0), 2))
        if daily.close_at_ns is not None and daily.close_at_ns > cut:
            daily = replace(daily, close=round(rng.uniform(10.0, 40.0), 2))
    bars: list[Bar] = []
    for b in s.vix_bars:
        if b.close_ns <= cut:
            bars.append(b)
        elif rng.random() < 0.7:
            c = round(b.c + rng.uniform(-2.0, 2.0), 2)
            bars.append(vix_bar(b.open_ns, b.o, max(b.o, c), min(b.o, c), c))
    return replace(s, vix=daily, vix_bars=tuple(bars))


def later_prints(s: SessionInputs, cut: Instant, rng: random.Random) -> list[DarkPoolPrint]:
    _, pull_end = CALENDAR.pull_window(s.session)
    rth_open = CALENDAR.rth_open(s.session)
    out: list[DarkPoolPrint] = []
    for p in s.prints:
        r = rng.random()
        if p.ts_ns <= cut or r >= 0.7:
            out.append(p)
        elif r >= 0.3:
            size = rng.randint(1_000, 50_000)
            price = round(p.price + rng.uniform(-3.0, 3.0), 2)
            out.append(replace(p, price=price, size=size, notional=round(price * size, 2)))
    start = max(cut + 1, rth_open)
    for ticker in DARK_POOL_TICKERS:
        for _ in range(rng.randint(0, 2)):
            if start < pull_end:
                price = round(rng.uniform(495.0, 585.0), 2)
                size = rng.randint(1_000, 50_000)
                ts = rng.randrange(start, pull_end)
                out.append(DarkPoolPrint(ticker, ts, price, size, round(price * size, 2), "D"))
    return sorted(out, key=lambda p: (p.ts_ns, p.ticker))


def after_cut(market: MarketInputs, cut: Instant, seed: int) -> MarketInputs:
    """``market`` with its inputs after ``cut`` perturbed, removed or added (module notes)."""
    rng = random.Random(seed)
    sessions: list[SessionInputs] = []
    for s in market.sessions:
        changed = replace(
            later_vix(s, cut, rng),
            snapshots=tuple(later_snapshots(s, cut, rng)),
            bars=tuple(later_bars(s, cut, rng)),
            prints=tuple(later_prints(s, cut, rng)),
        )
        assert changed.skipped == s.skipped
        sessions.append(changed)
    return MarketInputs(tuple(sessions))


# ---------------------------------------------------------------- the property


def lines_until(result: BacktestResult, cut: Instant) -> list[str]:
    text = (result.run_dir / DECISION_LOG_FILE_NAME).read_text(encoding="utf-8")
    return [line for line in text.splitlines() if json.loads(line)["t"] <= cut]


# Feature: skylit-futures-strategy-engine, Property 14: No look-ahead
@given(case=cases())
def test_inputs_after_t_do_not_change_entries_at_or_before_t(case: Case) -> None:
    changed = after_cut(case.market, case.cut, case.seed)
    if changed != case.market:
        event("inputs after t changed")
    with tempfile.TemporaryDirectory(prefix="fse-p14-") as tmp:
        root = Path(tmp)
        calendars = write_calendars(root / "calendars")
        span = case.market.data_range
        first = run(write_cache(root / "a", case.market), calendars, case.cfg, span, root / "ra")
        second = run(write_cache(root / "b", changed), calendars, case.cfg, span, root / "rb")
        before = lines_until(first, case.cut)
        assert lines_until(second, case.cut) == before
        cadence = case.cfg.time.decision_cadence_s
        expected = [
            t
            for s in case.market.evaluated
            for t in CALENDAR.decision_times(s.session, cadence)
            if t <= case.cut
        ]
        assert [json.loads(line)["t"] for line in before] == expected
        event(f"entries at or before t: {'some' if before else 'none'}")
        later = (first.run_dir / DECISION_LOG_FILE_NAME).read_bytes()
        if later != (second.run_dir / DECISION_LOG_FILE_NAME).read_bytes():
            event("entries after t differ")
