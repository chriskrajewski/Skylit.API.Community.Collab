"""Unit tests for ``run_backtest`` (design §18 "Backtester", "Decision loop").

A synthetic Data_Cache under ``tmp_path`` and the Project's real calendar
files. Configured symbols SPX and QQQ, traded MES and MNQ, every Gate off, so
every priced setup is A_Plus (as in ``test_engine_step``).

- Wednesday 2026-03-04: the SPX gamma Map of ``test_engine_step`` (Nodes
  5700 to 5900, King 5750). MES pairs at 5800, drops to 5752 into 09:30, then
  the 09:30 bar fills the ``gatekeeper_fade`` long at 5760 and the 09:31 bar
  fills its 5790 target: +30 points on 5 MES.
- Thursday 2026-03-05: the same Map, MES flat at 5800, so nothing fills.
- Friday 2026-03-06: no QQQ vanna Snapshot and no MNQ bars, so it is skipped.

The SPX vanna and QQQ Maps are all zeros (no Nodes). The Decision_Cadence is
900 s: 26 Decision_Times per session.

**Validates: Requirements 1.8, 3.12, 3.13, 5.5, 5.6, 15.9, 15.17, 15.18, 18.1,
18.2, 18.3, 18.4, 18.5, 18.6**
"""

from __future__ import annotations

import ast
import csv
import io
import json
from collections.abc import Callable, Iterator
from datetime import date, time
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from fse.backtest import runner
from fse.backtest.decision_log import DECISION_LOG_FILE_NAME
from fse.backtest.manifest import MANIFEST_FILE_NAME, DataRange
from fse.backtest.runner import (
    ATTEMPTS_FILE_NAME,
    BACKTEST_OUTPUT_FILES,
    FUNNEL_FILE_NAME,
    REPORT_INPUTS_FILE_NAME,
    SESSION_OUTCOMES_FILE_NAME,
    SETUPS_FILE_NAME,
    SHADOW_TRADES_FILE_NAME,
    TRADE_COLUMNS,
    TRADES_CSV_FILE_NAME,
    TRADES_JSON_FILE_NAME,
    BacktestInputError,
    BacktestResult,
    backtest_range,
    run_backtest,
)
from fse.calendars import load_exchange_calendar
from fse.config.schema import StrategyConfig
from fse.config.schema.gates import GATE_IDS
from fse.config.schema.orders import CANCEL_TRIGGERS
from fse.data.cache import CacheWindowKey, DataCache, HeatmapView
from fse.engine.types import Bar, Metric, Snapshot
from fse.logio import LogWriter, Redactor
from fse.skylit import client as skylit_client
from fse.timekit import NS_PER_SECOND, ny_instant
from tests.fakes.configs import minimal_config_data

PROJECT_DIR = Path(__file__).resolve().parents[2]
CALENDARS = PROJECT_DIR / "calendars"
CALENDAR = load_exchange_calendar(CALENDARS / "exchange_calendar.yaml").sessions
VIEW = HeatmapView()
WED, THU, FRI = date(2026, 3, 4), date(2026, 3, 5), date(2026, 3, 6)
RANGE = DataRange(WED, FRI)
MINUTE = 60 * NS_PER_SECOND
CADENCE_S = 900
SEED = 7
STARTED, ENDED = 1_772_000_000_000_000_000, 1_772_000_060_000_000_000
SPX_GAMMA: tuple[tuple[float, float], ...] = (
    *((5700.0, -1.0e9), (5750.0, 3.0e9), (5760.0, 0.9e9)),
    *((5790.0, 0.9e9), (5850.0, -2.0e9), (5900.0, 1.0e9)),
)


def at(session: date, hh: int, mm: int, ss: int = 0) -> int:
    return ny_instant(session, time(hh, mm, ss))


def ticks(points: float) -> int:
    return round(points * 4)


def snap(
    session: date, symbol: str, metric: Metric, pairs: tuple[tuple[float, float], ...]
) -> Snapshot:
    as_of = at(session, 9, 29, 30)
    return Snapshot(
        symbol=symbol,
        metric=metric,
        view_id=VIEW.view_id(),
        as_of_ns=as_of,
        as_of_raw=f"raw-{as_of}",
        spot=5800.0 if symbol == "SPX" else 500.0,
        previous_close=None,
        strikes=tuple(k for k, _ in pairs),
        values=tuple(v for _, v in pairs),
        node_types=None,
        expirations=(session.isoformat(),),
        resolution="1s",
        source_endpoint="range",
        extra_json="{}",
    )


def bar(instrument: str, open_ns: int, o: float, h: float, low: float, c: float) -> Bar:
    contract = {"MES": "MESH6", "MNQ": "MNQH6"}[instrument]
    return Bar(
        instrument, contract, 60, open_ns, open_ns + MINUTE, o, h, low, c, 100.0,
        ticks(o), ticks(h), ticks(low), ticks(c), "atlas",
    )  # fmt: skip


def minute_opens(session: date) -> list[int]:
    """Every 1-minute bar open from 09:00 to the 16:10 Flat_Deadline bar."""
    return list(range(at(session, 9, 0), at(session, 16, 10) + 1, MINUTE))


def mes_bars(session: date, *, trade: bool) -> list[Bar]:
    out: list[Bar] = []
    for open_ns in minute_opens(session):
        if not trade or open_ns < at(session, 9, 29):
            out.append(bar("MES", open_ns, 5800.0, 5800.0, 5800.0, 5800.0))
        elif open_ns == at(session, 9, 29):
            out.append(bar("MES", open_ns, 5760.0, 5761.0, 5751.0, 5752.0))
        elif open_ns == at(session, 9, 30):
            out.append(bar("MES", open_ns, 5752.0, 5790.0, 5751.0, 5788.0))  # fills 5760
        elif open_ns == at(session, 9, 31):
            out.append(bar("MES", open_ns, 5788.0, 5795.0, 5787.0, 5794.0))  # fills 5790
        else:
            out.append(bar("MES", open_ns, 5794.0, 5794.0, 5794.0, 5794.0))
    return out


def mnq_bars(session: date) -> list[Bar]:
    return [bar("MNQ", o, 20000.0, 20000.0, 20000.0, 20000.0) for o in minute_opens(session)]


def store(cache: DataCache, session: date, symbol: str, metric: Metric, s: Snapshot) -> None:
    key = CacheWindowKey.for_view(symbol, metric, VIEW, session, at(session, 9, 15))
    cache.write_window(key, [s], "range", view=VIEW)


@pytest.fixture(scope="module")
def cache_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("bt") / "cache"
    zeros_spx = tuple((k, 0.0) for k, _ in SPX_GAMMA)
    zeros_qqq = ((490.0, 0.0), (500.0, 0.0), (510.0, 0.0))
    with DataCache(root, calendar=CALENDAR) as cache:
        for session in (WED, THU, FRI):
            store(cache, session, "SPX", "gamma", snap(session, "SPX", "gamma", SPX_GAMMA))
            store(cache, session, "SPX", "vanna", snap(session, "SPX", "vanna", zeros_spx))
            store(cache, session, "QQQ", "gamma", snap(session, "QQQ", "gamma", zeros_qqq))
            if session != FRI:
                store(cache, session, "QQQ", "vanna", snap(session, "QQQ", "vanna", zeros_qqq))
            mes = mes_bars(session, trade=session == WED)
            cache.bars.write_session("MES", 60, session, mes, contract="MESH6", source="atlas")
            if session != FRI:
                mnq = mnq_bars(session)
                cache.bars.write_session("MNQ", 60, session, mnq, contract="MNQH6", source="atlas")
        cache.mark_incomplete(CacheWindowKey.for_view("SPX", "gamma", VIEW, WED, at(WED, 9, 30)))
    return root


def config(**account: Any) -> StrategyConfig:
    data = minimal_config_data()
    data.update(
        {
            "time": {"decision_cadence_s": CADENCE_S},
            "data": {"symbols": ["SPX", "QQQ"], "nq_sources": ["QQQ"]},
            "gates": {g: {"enabled": False} for g in GATE_IDS},
            "sizing": {"trinity_size_down": {"enabled": False}, "vix_gap": {"enabled": False}},
            "orders": {"cancel_triggers": dict.fromkeys(CANCEL_TRIGGERS, False)},
            "account": {
                "profit_target": {"value": "500.00"},
                "consistency_target": {"enabled": False},
                **account,
            },
        }
    )
    return StrategyConfig.model_validate(data)


def clock() -> Callable[[], int]:
    times: Iterator[int] = iter((STARTED, ENDED))
    return lambda: next(times)


def backtest(
    cache_root: Path,
    out_dir: Path,
    cfg: StrategyConfig | None = None,
    *,
    stdout: io.StringIO | None = None,
    **kwargs: Any,
) -> BacktestResult:
    writer = LogWriter(Redactor(["fake-runner-secret-0000"]), stdout=stdout, stderr=io.StringIO())
    with DataCache(cache_root, calendar=CALENDAR) as cache:
        return run_backtest(
            config() if cfg is None else cfg,
            kwargs.pop("sessions", RANGE),
            cache,
            out_dir,
            SEED,
            writer=writer,
            calendar_dir=CALENDARS,
            clock=clock(),
            code_version="test-version",
            **kwargs,
        )


@pytest.fixture(scope="module")
def baseline(cache_root: Path, tmp_path_factory: pytest.TempPathFactory) -> BacktestResult:
    return backtest(cache_root, tmp_path_factory.mktemp("run") / "baseline")


def lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# ---------------------------------------------------------------- decision log and manifest


def test_one_decision_log_line_per_decision_time_of_each_evaluated_session(
    baseline: BacktestResult,
) -> None:
    entries = lines(baseline.run_dir / DECISION_LOG_FILE_NAME)
    grid = [t for s in (WED, THU) for t in CALENDAR.decision_times(s, CADENCE_S)]
    assert len(grid) == 2 * 26
    assert [e["t"] for e in entries] == grid
    assert baseline.decision_times == len(grid)
    assert {e["session"] for e in entries} == {"2026-03-04", "2026-03-05"}
    first = entries[0]
    assert first["map"]["SPX/gamma"]["king"] == 5750.0
    assert first["futures"] == {"MES": ticks(5752), "MNQ": ticks(20000)}


def test_the_manifest_records_evaluated_and_skipped_sessions(baseline: BacktestResult) -> None:
    manifest = json.loads((baseline.run_dir / MANIFEST_FILE_NAME).read_text(encoding="utf-8"))
    assert manifest["status"] == "completed"
    assert manifest["kind"] == "backtest"
    assert manifest["seed"] == SEED
    assert manifest["data_range"] == {"start": "2026-03-04", "end": "2026-03-06"}
    assert manifest["sessions_evaluated"] == ["2026-03-04", "2026-03-05"]
    assert manifest["sessions_skipped"] == [
        {"date": "2026-03-06", "missing": ["snapshots", "bars"], "names": ["QQQ/vanna", "MNQ"]}
    ]
    assert manifest["outputs"] == [
        DECISION_LOG_FILE_NAME,
        TRADES_CSV_FILE_NAME,
        TRADES_JSON_FILE_NAME,
        ATTEMPTS_FILE_NAME,
        SESSION_OUTCOMES_FILE_NAME,
        SETUPS_FILE_NAME,
        SHADOW_TRADES_FILE_NAME,
        FUNNEL_FILE_NAME,
        REPORT_INPUTS_FILE_NAME,
    ]
    assert sorted(p.name for p in baseline.run_dir.iterdir()) == sorted(BACKTEST_OUTPUT_FILES)


# ---------------------------------------------------------------- fills, trades and accounts


def test_the_trade_list_is_written_as_csv_and_json(baseline: BacktestResult) -> None:
    [trade] = baseline.trades
    assert trade.setup_key.pattern == "gatekeeper_fade"
    assert trade.entry_fill.bar_open_ns == at(WED, 9, 30)  # placed at 09:30, fills on 09:30 bar
    assert [f.bar_open_ns for f in trade.exits] == [at(WED, 9, 31)]
    assert trade.net == Decimal("742.80")  # 120 ticks x $1.25 x 5 - 10 fills x $0.72
    with (baseline.run_dir / TRADES_CSV_FILE_NAME).open(encoding="utf-8", newline="") as fh:
        header, *rows = list(csv.reader(fh))
    assert tuple(header) == TRADE_COLUMNS
    [row] = rows
    record = dict(zip(header, row, strict=True))
    assert (record["net"], record["entry_price_ticks"], record["exit_count"]) == (
        "742.80",
        str(ticks(5760)),
        "1",
    )
    assert (record["mae_r"], record["mfe_r"]) == (str(trade.mae_r), str(trade.mfe_r))
    [listed] = json.loads((baseline.run_dir / TRADES_JSON_FILE_NAME).read_text(encoding="utf-8"))
    assert (listed["net"], listed["mae_r"], listed["mfe_r"]) == (
        "742.80",
        str(trade.mae_r),
        str(trade.mfe_r),
    )


def test_a_pass_ends_the_attempt_and_the_next_session_starts_another(
    baseline: BacktestResult,
) -> None:
    passed, running = baseline.attempts
    assert (passed.number, passed.outcome, passed.first_session, passed.last_session) == (
        1,
        "passed",
        WED,
        WED,
    )
    assert passed.final_balance == Decimal("50742.80")
    assert (running.number, running.outcome, running.first_session, running.last_session) == (
        2,
        "incomplete",
        THU,
        THU,
    )
    listed = json.loads((baseline.run_dir / ATTEMPTS_FILE_NAME).read_text(encoding="utf-8"))
    assert [a["outcome"] for a in listed] == ["passed", "incomplete"]


def test_each_session_records_its_net_and_intraday_low(baseline: BacktestResult) -> None:
    wed, thu = baseline.outcomes
    # The entry bar's low (5751) against the 5760 fill: -36 ticks x $1.25 x 5, minus $3.60 fees.
    assert (wed.session, wed.net, wed.intraday_low) == (WED, Decimal("742.80"), Decimal("-228.60"))
    assert (thu.session, thu.net, thu.intraday_low) == (THU, Decimal("0.00"), Decimal("0.00"))
    assert not wed.truncated_by_run_account
    assert not thu.truncated_by_run_account
    listed = json.loads((baseline.run_dir / SESSION_OUTCOMES_FILE_NAME).read_text(encoding="utf-8"))
    assert listed[0] == {
        "session": "2026-03-04",
        "net": "742.80",
        "intraday_low": "-228.60",
        "truncated_by_run_account": False,
    }


def test_an_mll_breach_liquidates_and_fails_the_attempt_mid_session(
    cache_root: Path, tmp_path: Path
) -> None:
    cfg = config(maximum_loss_limit={"value": "100.00"})
    result = backtest(cache_root, tmp_path / "mll", cfg)
    [trade] = result.trades
    [exit_fill] = trade.exits
    assert exit_fill.client_id == "maximum_loss_limit:fse-000001-entry"
    assert (exit_fill.bar_open_ns, exit_fill.price) == (at(WED, 9, 30), ticks(5751))
    failed, running = result.attempts
    # Valued at the bar's open (5752) the balance is already below the 49,900 floor (Req 15.8).
    assert (failed.outcome, failed.ended_ns, failed.final_balance) == (
        "failed",
        at(WED, 9, 30),
        Decimal("49796.40"),
    )
    assert (running.number, running.first_session, running.outcome) == (2, THU, "incomplete")
    assert [o.truncated_by_run_account for o in result.outcomes] == [True, False]
    assert result.outcomes[0].net == Decimal("-203.60")
    # The planner saw the liquidation fill: no plan is left open in the log.
    entries = lines(result.run_dir / DECISION_LOG_FILE_NAME)
    assert any(
        f["fill"]["client_id"] == "maximum_loss_limit:fse-000001-entry"
        for e in entries
        for f in e["fills"]
    )


# ---------------------------------------------------------------- determinism


def test_equal_inputs_and_seed_give_byte_identical_outputs(
    baseline: BacktestResult, cache_root: Path, tmp_path: Path
) -> None:
    """The second run also loads sessions on two worker threads; the bytes do not change."""
    again = backtest(cache_root, tmp_path / "again", workers=2)
    for name in BACKTEST_OUTPUT_FILES:
        assert (again.run_dir / name).read_bytes() == (baseline.run_dir / name).read_bytes(), name


# ---------------------------------------------------------------- input validation


def test_an_invalid_range_is_refused_before_any_output(cache_root: Path, tmp_path: Path) -> None:
    with pytest.raises(BacktestInputError, match="invalid date range"):
        backtest_range(FRI, WED)
    weekend = DataRange(date(2026, 3, 7), date(2026, 3, 8))
    out = tmp_path / "weekend"
    with pytest.raises(BacktestInputError, match="contains no session"):
        backtest(cache_root, out, sessions=weekend)
    assert not out.exists()


def test_a_used_run_directory_is_refused(baseline: BacktestResult, cache_root: Path) -> None:
    before = (baseline.run_dir / MANIFEST_FILE_NAME).read_bytes()
    with pytest.raises(BacktestInputError, match="new run directory"):
        backtest(cache_root, baseline.run_dir)
    assert (baseline.run_dir / MANIFEST_FILE_NAME).read_bytes() == before


# ---------------------------------------------------------------- offline mode


def test_offline_mode_prints_the_gaps_and_builds_no_skylit_client(
    cache_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("offline mode must not build a SkylitClient")

    monkeypatch.setattr(skylit_client.SkylitClient, "__init__", refuse)
    out = io.StringIO()
    result = backtest(cache_root, tmp_path / "offline", stdout=out, offline=True)
    printed = out.getvalue().splitlines()
    assert printed[0] == (
        "offline: 3 of 3 requested sessions have absent or incomplete Cache_Windows"
    )
    assert (
        "offline: 2026-03-04 SPX gamma: 27 absent or incomplete Cache_Window(s) "
        "(26 absent, 1 incomplete)"
    ) in printed
    assert (
        "offline: 2026-03-06 QQQ vanna: 28 absent or incomplete Cache_Window(s) "
        "(28 absent, 0 incomplete)"
    ) in printed
    assert len(printed) == 1 + 4 + 4 + 4
    assert result.manifest.status == "completed"


def test_the_runner_never_imports_the_skylit_client() -> None:
    tree = ast.parse(Path(runner.__file__).read_text(encoding="utf-8"))
    imported = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    ]
    imported += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert not [m for m in imported if m.startswith(("fse.skylit", "httpx"))]
