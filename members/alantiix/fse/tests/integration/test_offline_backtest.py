"""Integration test: an offline backtest with ``SKYLIT_API_KEY`` unset (task 19.7).

:func:`run_backtest` runs with ``offline=True`` on a synthetic Data_Cache under
``tmp_path`` and the test calendar files of ``tests/strategies/backtest_inputs``.
The suite's ``isolated_home`` fixture leaves every Secret_Variable unset.

The cache holds three sessions. Each configured symbol and metric has one
Snapshot (09:29:30, in the 09:15 Cache_Window), so 27 of the 28 Cache_Windows
of the 09:00-16:00 Pull_Window are absent:

- 2026-03-03: every input.
- 2026-03-04: every input, and the SPX gamma 09:30 window is ``incomplete``.
- 2026-03-05: no QQQ vanna Snapshot (28 absent windows), so it is skipped.

Guards: ``SkylitClient.__init__`` raises, every socket connect raises and is
recorded, and the suite's respx router (``assert_all_mocked=True``) records
any httpx request.

Checked: no network call and no client was attempted, nothing was written to
stderr, the run completed, and the printout lists every session, symbol and
metric with absent or incomplete Cache_Windows before the first Decision_Time
(Req 3.13).

**Validates: Requirements 1.8, 3.12, 3.13**
"""

from __future__ import annotations

import io
import json
import os
import socket
from datetime import date, time
from pathlib import Path

import pytest
import respx

from fse.backtest.decision_log import DECISION_LOG_FILE_NAME
from fse.backtest.manifest import MANIFEST_FILE_NAME, DataRange
from fse.config.schema import StrategyConfig
from fse.config.schema.gates import GATE_IDS
from fse.data.cache import CacheWindowKey, DataCache
from fse.engine.types import Bar, Metric, Snapshot
from fse.skylit import client as skylit_client
from fse.timekit import ny_instant
from tests.fakes.configs import minimal_config_data
from tests.strategies.backtest_inputs import (
    CALENDAR,
    INSTRUMENTS,
    KEYS,
    MINUTE,
    VIEW,
    SessionInputs,
    make_bar,
    make_snapshot,
    run,
    write_cache,
    write_calendars,
)

pytestmark = pytest.mark.integration

TUE, WED, THU = date(2026, 3, 3), date(2026, 3, 4), date(2026, 3, 5)
PAIRS: dict[str, tuple[tuple[float, float], ...]] = {
    "SPX": ((5750.0, 3.0e9), (5800.0, -1.0e9), (5850.0, 2.0e9)),
    "QQQ": ((495.0, 1.0e9), (500.0, 0.0), (505.0, -1.0e9)),
}
PRICE_TICKS = {"MES": 5800 * 4, "MNQ": 20000 * 4}


def snapshots(session: date, keys: tuple[tuple[str, Metric], ...]) -> tuple[Snapshot, ...]:
    at = ny_instant(session, time(9, 29, 30))
    spot = {"SPX": 5800.0, "QQQ": 500.0}
    return tuple(make_snapshot(session, s, m, at, spot[s], PAIRS[s]) for s, m in keys)


def bars(session: date) -> tuple[Bar, ...]:
    """Flat MES and MNQ bars from 09:20 to 10:00, then the Flat_Deadline bar."""
    first = ny_instant(session, time(9, 20))
    opens = [first + k * MINUTE for k in range(40)] + [CALENDAR.flat_deadline(session)]
    return tuple(
        make_bar(i, o, PRICE_TICKS[i], PRICE_TICKS[i], PRICE_TICKS[i], PRICE_TICKS[i])
        for i in INSTRUMENTS
        for o in opens
    )


def session(day: date, keys: tuple[tuple[str, Metric], ...] = KEYS) -> SessionInputs:
    return SessionInputs(day, snapshots(day, keys), bars(day), None, (), ())


def config() -> StrategyConfig:
    data = minimal_config_data()
    data.update(
        {
            "time": {"decision_cadence_s": 1800},
            "data": {"symbols": ["SPX", "QQQ"], "nq_sources": ["QQQ"]},
            "gates": {g: {"enabled": False} for g in GATE_IDS},
        }
    )
    return StrategyConfig.model_validate(data)


def test_offline_backtest_makes_no_network_call_and_prints_the_gaps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, respx_router: respx.MockRouter
) -> None:
    assert os.environ.get("SKYLIT_API_KEY") is None
    attempts: list[object] = []

    def refuse_client(*args: object, **kwargs: object) -> None:
        attempts.append("SkylitClient")
        raise AssertionError("offline mode must not build a SkylitClient")

    def refuse_connect(self: socket.socket, address: object) -> None:
        attempts.append(address)
        raise AssertionError(f"offline mode must not connect to {address!r}")

    def refuse_create(address: object, *args: object, **kwargs: object) -> socket.socket:
        attempts.append(address)
        raise AssertionError(f"offline mode must not connect to {address!r}")

    monkeypatch.setattr(skylit_client.SkylitClient, "__init__", refuse_client)
    monkeypatch.setattr(socket.socket, "connect", refuse_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse_connect)
    monkeypatch.setattr(socket, "create_connection", refuse_create)
    respx_router.reset()

    calendars = write_calendars(tmp_path / "calendars")
    no_qqq_vanna = tuple(k for k in KEYS if k != ("QQQ", "vanna"))
    market = (session(TUE), session(WED), session(THU, no_qqq_vanna))
    cache_root = write_cache(tmp_path / "cache", market)
    with DataCache(cache_root, calendar=CALENDAR) as cache:
        start = ny_instant(WED, time(9, 30))
        cache.mark_incomplete(CacheWindowKey.for_view("SPX", "gamma", VIEW, WED, start))

    out, err = io.StringIO(), io.StringIO()
    result = run(
        cache_root,
        calendars,
        config(),
        DataRange(TUE, THU),
        tmp_path / "run",
        offline=True,
        stdout=out,
        stderr=err,
    )

    assert attempts == []
    assert respx_router.calls.call_count == 0
    assert err.getvalue() == ""
    assert result.manifest.status == "completed"
    manifest = json.loads((result.run_dir / MANIFEST_FILE_NAME).read_text(encoding="utf-8"))
    assert manifest["sessions_evaluated"] == ["2026-03-03", "2026-03-04"]
    assert manifest["sessions_skipped"] == [
        {"date": "2026-03-05", "missing": ["snapshots"], "names": ["QQQ/vanna"]}
    ]
    assert (result.run_dir / DECISION_LOG_FILE_NAME).stat().st_size > 0

    def line(day: date, key: str, absent: int, incomplete: int = 0) -> str:
        return (
            f"offline: {day.isoformat()} {key}: {absent + incomplete} absent or incomplete "
            f"Cache_Window(s) ({absent} absent, {incomplete} incomplete)"
        )

    assert out.getvalue().splitlines() == [
        "offline: 3 of 3 requested sessions have absent or incomplete Cache_Windows",
        line(TUE, "SPX gamma", 27),
        line(TUE, "SPX vanna", 27),
        line(TUE, "QQQ gamma", 27),
        line(TUE, "QQQ vanna", 27),
        line(WED, "SPX gamma", 26, 1),
        line(WED, "SPX vanna", 27),
        line(WED, "QQQ gamma", 27),
        line(WED, "QQQ vanna", 27),
        line(THU, "SPX gamma", 27),
        line(THU, "SPX vanna", 27),
        line(THU, "QQQ gamma", 27),
        line(THU, "QQQ vanna", 28),
    ]
