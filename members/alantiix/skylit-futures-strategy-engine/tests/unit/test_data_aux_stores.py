"""Unit tests for the bar, VIX and dark-pool stores of the Data_Cache.

Every cache directory is under ``tmp_path``, outside any git repository.

**Validates: Requirements 1.11, 3.9, 4.5, 4.7, 4.13**
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import date, time
from pathlib import Path
from typing import Any

import pytest

from fse.data.aux_stores import VixDailyRecord, trade_date_of
from fse.data.cache import DataCache
from fse.data.cache_io import CacheReadError, CacheWriteError
from fse.engine.types import Bar, DarkPoolPrint
from fse.timekit import NS_PER_MINUTE, ny_instant, parse_rfc3339

SESSION = date(2026, 3, 5)
OPEN_0930 = ny_instant(SESSION, time(9, 30))


def bar(open_ns: int, **overrides: Any) -> Bar:
    fields: dict[str, Any] = {
        "instrument": "MES",
        "contract": "MESH6",
        "interval_s": 60,
        "open_ns": open_ns,
        "close_ns": open_ns + NS_PER_MINUTE,
        "o": 5800.25,
        "h": 5801.0,
        "l": 5799.5,
        "c": 5800.75,
        "v": 1234.0,
        "o_t": 23201,
        "h_t": 23204,
        "l_t": 23198,
        "c_t": 23203,
        "source": "atlas",
    }
    fields.update(overrides)
    return Bar(**fields)


def vix_bar(open_ns: int, close: float) -> Bar:
    return bar(
        open_ns,
        instrument="VIX",
        contract="VIX",
        o=close,
        h=close,
        l=close,
        c=close,
        v=0.0,
        o_t=None,
        h_t=None,
        l_t=None,
        c_t=None,
    )


def dp(ts: str, price: float = 580.0, size: int = 2_000, venue: str = "TRF") -> DarkPoolPrint:
    return DarkPoolPrint("SPY", parse_rfc3339(ts), price, size, price * size, venue)


@pytest.fixture
def cache(tmp_path: Path) -> Iterator[DataCache]:
    with DataCache(tmp_path / "cache") as c:
        yield c


# ---------------------------------------------------------------- bars


def test_bar_round_trip_and_coverage(cache: DataCache) -> None:
    bars = [
        bar(OPEN_0930 + i * NS_PER_MINUTE, c=5800.0 + 0.25 * i, c_t=23200 + i) for i in range(3)
    ]
    cache.bars.write_session("MES", 60, SESSION, bars, contract="MESH6", source="atlas")
    assert cache.bars.read_session("MES", 60, SESSION) == bars
    path = cache.bars.path("MES", 60, SESSION)
    assert path == cache.root / "bars" / "MES" / "60s" / "2026-03-05.parquet"
    record = cache.bars.record("MES", 60, SESSION)
    assert record is not None
    assert (record.status, record.rows, record.contract, record.source) == (
        "complete",
        3,
        "MESH6",
        "atlas",
    )
    assert record.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_session_without_bars_is_no_data(cache: DataCache) -> None:
    cache.bars.write_session("MES", 60, SESSION, [], contract="MESH6", source="projectx")
    assert cache.bars.status("MES", 60, SESSION) == "no_data"
    assert cache.bars.read_session("MES", 60, SESSION) == []
    record = cache.bars.record("MES", 60, SESSION)
    assert record is not None
    assert (record.contract, record.source) == ("MESH6", "projectx")


@pytest.mark.parametrize(
    "bars",
    [
        [bar(OPEN_0930, contract="MESM6")],  # another contract (Req 4.5)
        [bar(OPEN_0930, source="projectx")],
        [bar(OPEN_0930, instrument="MNQ")],
        [bar(OPEN_0930 + NS_PER_MINUTE), bar(OPEN_0930)],  # out of order
        [bar(OPEN_0930), bar(OPEN_0930)],  # duplicate open time
    ],
)
def test_bars_that_do_not_fit_write_nothing(cache: DataCache, bars: list[Bar]) -> None:
    with pytest.raises(ValueError, match=r"bars\[\d\]"):
        cache.bars.write_session("MES", 60, SESSION, bars, contract="MESH6", source="atlas")
    assert cache.bars.status("MES", 60, SESSION) == "absent"
    assert not cache.root.exists()


def test_failed_bar_write_leaves_incomplete(
    cache: DataCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(src: object, dst: object) -> None:
        raise OSError(5, "Input/output error")

    monkeypatch.setattr("fse.data.cache_io.os.replace", fail)
    with pytest.raises(CacheWriteError, match=r"MES 60s bars, session 2026-03-05"):
        cache.bars.write_session(
            "MES", 60, SESSION, [bar(OPEN_0930)], contract="MESH6", source="atlas"
        )
    assert cache.bars.status("MES", 60, SESSION) == "incomplete"
    with pytest.raises(CacheReadError, match="incomplete"):
        cache.bars.read_session("MES", 60, SESSION)


# ---------------------------------------------------------------- VIX


def test_vix_bars_are_a_separate_dataset(cache: DataCache) -> None:
    bars = [vix_bar(OPEN_0930, 17.25), vix_bar(OPEN_0930 + NS_PER_MINUTE, 17.5)]
    cache.vix.bars.write_session("VIX", 60, SESSION, bars, contract="VIX", source="atlas")
    assert cache.vix.bars.read_session("VIX", 60, SESSION) == bars
    assert cache.vix.bars.path("VIX", 60, SESSION).is_relative_to(cache.root / "vix" / "bars")
    assert cache.bars.status("VIX", 60, SESSION) == "absent"
    [record] = cache.catalog.bars_coverage(dataset="vix")
    assert (record.instrument, record.rows) == ("VIX", 2)


def test_vix_daily_upserts_by_session(cache: DataCache) -> None:
    def rec(d: date, open_: float | None, close: float | None) -> VixDailyRecord:
        return VixDailyRecord(
            session=d,
            open=open_,
            open_at_ns=None if open_ is None else ny_instant(d, time(9, 31)),
            close=close,
            close_at_ns=None if close is None else ny_instant(d, time(16, 0)),
            source="import",
        )

    assert cache.vix.read_daily() == {}
    first = [rec(date(2026, 3, 3), 18.0, 18.5), rec(date(2026, 3, 4), None, 19.25)]
    cache.vix.write_daily(first)
    cache.vix.write_daily([rec(date(2026, 3, 4), 19.0, 19.25), rec(date(2026, 3, 2), 17.0, None)])
    daily = cache.vix.read_daily()
    assert list(daily) == [date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4)]
    assert daily[date(2026, 3, 3)] == first[0]
    assert daily[date(2026, 3, 4)].open == 19.0
    assert daily[date(2026, 3, 2)].close is None
    with pytest.raises(ValueError, match="more than once"):
        cache.vix.write_daily([first[0], first[0]])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"open": 18.0, "open_at_ns": None},
        {"close": None, "close_at_ns": 1},
        {"source": "csv"},
    ],
)
def test_vix_daily_record_validation(kwargs: dict[str, Any]) -> None:
    fields: dict[str, Any] = {
        "session": SESSION,
        "open": 18.0,
        "open_at_ns": 1,
        "close": 18.5,
        "close_at_ns": 2,
        "source": "atlas",
    }
    fields.update(kwargs)
    with pytest.raises(ValueError, match="VixDailyRecord"):
        VixDailyRecord(**fields)


# ---------------------------------------------------------------- dark pool


def test_trade_date_is_the_new_york_date() -> None:
    assert trade_date_of(parse_rfc3339("2026-03-03T03:00:00Z")) == date(2026, 3, 2)
    assert trade_date_of(parse_rfc3339("2026-03-03T05:00:00Z")) == date(2026, 3, 3)


def test_dark_pool_fetch_results_by_trade_date(cache: DataCache) -> None:
    mar2 = [
        dp("2026-03-02T15:00:00Z", 581.5),
        dp("2026-03-02T14:30:00Z"),
        dp("2026-03-03T03:00:00Z"),
    ]
    cache.darkpool.write_trade_dates("SPY", [date(2026, 3, 2), date(2026, 3, 3)], mar2)
    assert cache.darkpool.fetched_dates("SPY") == {date(2026, 3, 2), date(2026, 3, 3)}
    got = cache.darkpool.read("SPY", date(2026, 3, 1), date(2026, 3, 31))
    assert got == sorted(mar2, key=lambda p: p.ts_ns)

    # A refetch of 2026-03-02 replaces that date only; 2026-03-03 stays fetched with no prints.
    again = [dp("2026-03-02T16:00:00Z", 579.0, venue="ADF")]
    cache.darkpool.write_trade_dates("SPY", [date(2026, 3, 2)], again)
    assert cache.darkpool.read("SPY", date(2026, 3, 2), date(2026, 3, 3)) == again
    assert cache.darkpool.fetched_dates("SPY") == {date(2026, 3, 2), date(2026, 3, 3)}


def test_dark_pool_spans_split_into_month_files(cache: DataCache) -> None:
    prints = [dp("2026-03-31T15:00:00Z"), dp("2026-04-01T15:00:00Z")]
    cache.darkpool.write_trade_dates("SPY", [date(2026, 3, 31), date(2026, 4, 1)], prints)
    names = sorted(p.name for p in (cache.root / "darkpool" / "SPY").iterdir())
    assert names == ["2026-03.parquet", "2026-04.parquet"]
    assert cache.darkpool.read("SPY", date(2026, 3, 31), date(2026, 4, 1)) == prints
    assert cache.darkpool.read("SPY", date(2026, 4, 1), date(2026, 4, 30)) == prints[1:]
    assert cache.darkpool.fetched_dates("QQQ") == frozenset()


@pytest.mark.parametrize(
    "prints",
    [
        [dp("2026-03-04T15:00:00Z")],  # not on a listed trade date
        [DarkPoolPrint("QQQ", parse_rfc3339("2026-03-02T15:00:00Z"), 1.0, 1, 1.0, "TRF")],
    ],
)
def test_dark_pool_prints_that_do_not_fit_write_nothing(
    cache: DataCache, prints: list[DarkPoolPrint]
) -> None:
    with pytest.raises(ValueError, match=r"prints\[0\] is (on|for)"):
        cache.darkpool.write_trade_dates("SPY", [date(2026, 3, 2)], prints)
    assert not cache.root.exists()
