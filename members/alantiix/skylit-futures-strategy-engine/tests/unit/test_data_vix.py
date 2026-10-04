"""Unit tests for the VIX data (``fse.data.vix``).

Atlas is a respx fake that answers ``/v1/history`` with one bar per minute of
the requested window; the key is fake and time is virtual.

**Validates: Requirements 4.7, 4.9, 4.10, 4.11**
"""

from __future__ import annotations

import random
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import date, time
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
import respx

from fse.data.aux_stores import VixDailyRecord
from fse.data.bar_source import AtlasBars
from fse.data.cache import DataCache
from fse.data.coverage import TimeRange, VixGap
from fse.data.vix import (
    VIX_INSTRUMENT,
    daily_record,
    prior_session,
    pull_vix,
    replaces,
    vix_gaps,
)
from fse.engine.types import Bar
from fse.logio import LogWriter, Redactor
from fse.secrets.env import EnvView
from fse.skylit.client import ClientConfig, SkylitClient
from fse.skylit.endpoints import Host
from fse.skylit.fetch_log import FETCH_LOG_FILE_NAME, FetchLog
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, Instant, SessionCalendar, ny_instant
from tests.fakes.clock import FakeClock

KEY = "fake-skylit-key-0000"
S = NS_PER_SECOND
M = NS_PER_MINUTE
THU, FRI, MON = date(2026, 3, 5), date(2026, 3, 6), date(2026, 3, 9)
EARLY = date(2026, 3, 11)
CALENDAR = SessionCalendar(date(2026, 3, 2), date(2026, 3, 31), early_closes={EARLY: time(13, 15)})
T0 = ny_instant(date(2026, 3, 20), time(8, 0))


def ny(d: date, hh: int, mm: int) -> Instant:
    return ny_instant(d, time(hh, mm))


def vix_bar(open_ns: Instant, o: float, c: float) -> Bar:
    no_ticks = (None, None, None, None)
    return Bar("VIX", "VIX", 60, open_ns, open_ns + M, o, o, c, c, 0.0, *no_ticks, "atlas")


@dataclass
class FakeAtlas:
    missing: set[int] = field(default_factory=set)
    calls: list[dict[str, str]] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.calls.append(params)
        lo, hi = int(params["from"]), int(params["to"])
        t = [s for s in range(lo, hi, 60) if s not in self.missing]
        p = [20.0 + (s // 60) % 50 / 100 for s in t]
        cols = {"o": p, "h": [x + 0.1 for x in p], "l": [x - 0.1 for x in p], "c": p}
        return httpx.Response(200, json={"s": "ok", "t": t, **cols, "v": [0] * len(t)})


@pytest.fixture
def fetch_log(tmp_path: Path) -> Iterator[FetchLog]:
    with FetchLog(LogWriter(Redactor([KEY])), tmp_path / FETCH_LOG_FILE_NAME) as log:
        yield log


@pytest_asyncio.fixture
async def atlas(
    respx_router: respx.MockRouter, fetch_log: FetchLog
) -> AsyncIterator[tuple[AtlasBars, FakeAtlas, FakeClock]]:
    fake = FakeAtlas()
    clock = FakeClock(T0)
    respx_router.route(host=Host.API.value, path="/v1/account").mock(
        return_value=httpx.Response(200, json={"data": {"limits": {}}})
    )
    respx_router.route(host=Host.ATLAS.value, path="/v1/config").mock(
        return_value=httpx.Response(200, json={})
    )
    respx_router.route(host=Host.ATLAS.value, path="/v1/history").mock(side_effect=fake)
    async with httpx.AsyncClient() as http:
        client = SkylitClient(
            EnvView({"SKYLIT_API_KEY": KEY}, {}),
            ClientConfig(),
            fetch_log,
            clock,
            random.Random(7),
            http=http,
        )
        yield AtlasBars(client), fake, clock


def test_daily_record_uses_the_0930_open_and_the_rth_close_bar() -> None:
    bars = [
        vix_bar(ny(FRI, 9, 30), 21.5, 21.7),
        vix_bar(ny(FRI, 9, 31), 21.7, 21.9),
        vix_bar(ny(FRI, 15, 59), 19.8, 19.95),
    ]
    record = daily_record(CALENDAR, FRI, bars)
    assert record == VixDailyRecord(FRI, 21.5, ny(FRI, 9, 31), 19.95, ny(FRI, 16, 0), "atlas")
    early = daily_record(CALENDAR, EARLY, [vix_bar(ny(EARLY, 13, 14), 18.0, 18.25)])
    assert (early.open, early.open_at_ns, early.close, early.close_at_ns) == (
        None,
        None,
        18.25,
        ny(EARLY, 13, 15),
    )
    # No bar closes at 16:00: the close stays missing rather than taken from 15:58.
    assert daily_record(CALENDAR, FRI, [vix_bar(ny(FRI, 15, 58), 1.0, 2.0)]).close is None


def test_gaps_name_each_missing_item() -> None:
    daily = {
        FRI: VixDailyRecord(FRI, 21.0, ny(FRI, 9, 31), None, None, "atlas"),
        MON: VixDailyRecord(MON, None, None, 20.0, ny(MON, 16, 0), "import"),
    }
    full = [vix_bar(t, 1.0, 1.0) for t in range(ny(MON, 9, 30), ny(MON, 16, 0), M)]
    gaps = vix_gaps(CALENDAR, [FRI, MON], daily, intraday={MON: full[:-2]})
    assert prior_session(CALENDAR, MON) == FRI
    assert gaps == (
        VixGap(FRI, ("prior_close", "intraday_bars"), (TimeRange(ny(FRI, 9, 30), ny(FRI, 16, 0)),)),
        VixGap(
            MON,
            ("daily_open", "prior_close", "intraday_bars"),
            (TimeRange(ny(MON, 15, 58), ny(MON, 16, 0)),),
        ),
    )


def test_stored_values_are_only_improved() -> None:
    atlas_full = VixDailyRecord(FRI, 21.0, 1, 20.0, 2, "atlas")
    open_only = VixDailyRecord(FRI, 21.0, 1, None, None, "atlas")
    imported = VixDailyRecord(FRI, 22.0, 1, None, None, "import")
    empty = VixDailyRecord(FRI, None, None, None, None, "atlas")
    assert replaces(open_only, None)
    assert not replaces(empty, None)
    assert replaces(atlas_full, open_only)
    assert not replaces(open_only, atlas_full)  # would lose the close
    assert not replaces(open_only, imported)  # adds nothing the Operator lacks
    assert replaces(atlas_full, imported)  # adds the close
    assert not replaces(atlas_full, atlas_full)


@pytest.mark.asyncio
async def test_pull_vix_stores_daily_values_intraday_bars_and_the_prior_close(
    atlas: tuple[AtlasBars, FakeAtlas, FakeClock], tmp_path: Path
) -> None:
    bars, fake, clock = atlas
    fake.missing = {ny(MON, 9, 30) // S}  # Monday's opening bar is missing
    with DataCache(tmp_path / "cache") as cache:
        result = await clock.run(pull_vix(bars, cache.vix, CALENDAR, [MON], intraday=True))
        daily = cache.vix.read_daily()
        assert cache.vix.bars.status(VIX_INSTRUMENT, 60, MON) == "complete"
        assert cache.vix.bars.status(VIX_INSTRUMENT, 60, FRI) == "absent"
        stored = cache.vix.bars.read_session(VIX_INSTRUMENT, 60, MON)

    # Friday was fetched too, for Monday's prior close; one request covers both (RTH only).
    assert fake.calls == [
        {
            "symbol": "VIX",
            "resolution": "1",
            "from": str(ny(FRI, 9, 30) // S),
            "to": str(ny(MON, 16, 0) // S),
            "extended": "false",
        }
    ]
    assert [s.session for s in result.fetched] == [FRI, MON]
    assert result.written == (FRI, MON)
    assert daily[FRI].close_at_ns == ny(FRI, 16, 0)
    assert daily[FRI].open_at_ns == ny(FRI, 9, 31)
    assert (daily[MON].open, daily[MON].source) == (None, "atlas")
    assert len(stored) == 389
    assert all(b.o_t is None and b.source == "atlas" for b in stored)
    assert result.gaps == (
        VixGap(MON, ("daily_open", "intraday_bars"), (TimeRange(ny(MON, 9, 30), ny(MON, 9, 31)),)),
    )
