"""``PollingMarketData``: the poll grid phase, hot polls, receipt times and lag (design 4.2)."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import pytest

from fse.engine.types import Bar
from fse.live.bar_feed import FeedInstrument
from fse.live.host.dayclock import globex_day
from fse.live.host.sources import HOT_POLL_WINDOW_NS, PollingMarketData
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, Instant
from tests.fakes.bar_api import MemoryBars
from tests.fakes.clock import FakeClock
from tests.fakes.dataplane import ny

S = NS_PER_SECOND
M = NS_PER_MINUTE
MON = date(2026, 10, 12)
DAY = globex_day(MON)
FEEDS = (
    FeedInstrument("MNQ", "MNQZ6", "CON.F.US.MNQ.Z26"),
    FeedInstrument("MES", "MESZ6", "CON.F.US.MES.Z26"),
)


class Sink:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.bars: list[tuple[Bar, Instant]] = []
        self.status: list[tuple[str, bool, str]] = []

    def on_bars(self, bars: Any, received: Instant) -> None:
        assert received == self.clock.now()  # received_ns is taken after the fetch
        self.bars.extend((b, received) for b in bars)

    def on_status(self, feed: str, up: bool, detail: str) -> None:
        self.status.append((feed, up, detail))


def source(clock: FakeClock, api: MemoryBars, sink: Sink, **kw: Any) -> PollingMarketData:
    return PollingMarketData(api, clock, on_bars=sink.on_bars, on_status=sink.on_status, **kw)


async def run_until(clock: FakeClock, src: PollingMarketData, until: Instant, **kw: Any) -> None:
    await clock.run(src.run(DAY, FEEDS, DAY.open_ns, until, **kw))


@pytest.mark.asyncio
async def test_normal_polls_follow_the_start_phase() -> None:
    start = ny(MON, 10, 0, 2)
    clock = FakeClock(start)
    api = MemoryBars(clock, publish_delay_ns=S)
    sink = Sink(clock)
    await run_until(clock, source(clock, api, sink), ny(MON, 10, 3))
    times = api.calls_for("MNQ")
    assert times[0] == start
    assert all((t - start) % (5 * S) == 0 for t in times)
    assert api.calls_for("MES") == times  # both instruments every tick
    # every bar from the day open, once, oldest first per instrument
    mnq = [b.open_ns for b, _ in sink.bars if b.instrument == "MNQ"]
    assert mnq == list(range(DAY.open_ns, ny(MON, 10, 2), M))
    assert len(set(mnq)) == len(mnq)


@pytest.mark.asyncio
async def test_hot_polls_get_the_urgent_bar_early_without_moving_the_grid() -> None:
    start = ny(MON, 10, 0, 2)
    clock = FakeClock(start)
    api = MemoryBars(clock, publish_delay_ns=3 * S)
    sink = Sink(clock)
    src = source(clock, api, sink)
    close = ny(MON, 10, 2)
    src.set_urgent([("MNQ", close)])
    await run_until(clock, src, ny(MON, 10, 3))
    got = {b.open_ns: r for b, r in sink.bars if b.instrument == "MNQ"}
    # published at close + 3 s, received at close + 3 s by a hot poll (grid: :57, :02, :07)
    assert got[close - M] == close + 3 * S
    mes = {b.open_ns: r for b, r in sink.bars if b.instrument == "MES"}
    assert mes[close - M] == close + 7 * S  # MES waits for the normal grid
    hot = [t for t in api.calls_for("MNQ") if (t - start) % (5 * S)]
    assert hot == [close, close + S, close + 3 * S]
    # the grid never shifts: every MES request is a grid tick (a tick with no newly
    # closed minute sends nothing)
    assert all((t - start) % (5 * S) == 0 for t in api.calls_for("MES"))
    assert ny(MON, 10, 2, 2) in api.calls_for("MES")


@pytest.mark.asyncio
async def test_hot_polls_stop_20_s_after_the_close() -> None:
    start = ny(MON, 10, 0, 2)
    clock = FakeClock(start)
    close = ny(MON, 10, 1)
    api = MemoryBars(clock, missing={("MNQ", close - M)})
    sink = Sink(clock)
    src = source(clock, api, sink)
    src.set_urgent([("MNQ", close)])
    await run_until(clock, src, ny(MON, 10, 2))
    hot = [t for t in api.calls_for("MNQ") if (t - start) % (5 * S)]
    assert hot
    assert max(hot) <= close + HOT_POLL_WINDOW_NS
    assert len(hot) <= 21


@pytest.mark.asyncio
async def test_request_budget_stays_under_the_shared_limiter() -> None:
    start = ny(MON, 10, 0, 2)
    clock = FakeClock(start)
    api = MemoryBars(clock, publish_delay_ns=25 * S)  # every hot window runs to its end
    sink = Sink(clock)
    src = source(clock, api, sink)
    src.set_urgent([("MNQ", ny(MON, 10, m)) for m in range(1, 10)])
    await run_until(clock, src, ny(MON, 10, 10))
    times = sorted(t for t, *_ in api.calls)
    worst = max(sum(1 for u in times if t <= u < t + 30 * S) for t in times)
    assert worst <= 45


@pytest.mark.asyncio
async def test_seed_skips_recorded_bars_and_lag_is_tracked() -> None:
    start = ny(MON, 10, 0, 30)
    clock = FakeClock(start)
    api = MemoryBars(clock, publish_delay_ns=2 * S)
    sink = Sink(clock)
    src = source(clock, api, sink)
    await run_until(clock, src, ny(MON, 10, 2), seed={"MNQ": ny(MON, 9, 59)})
    mnq = [b.open_ns for b, _ in sink.bars if b.instrument == "MNQ"]
    assert mnq[0] == ny(MON, 9, 59)
    lag = src.take_lag()
    assert set(lag) == {"MES", "MNQ"}
    entry = lag["MNQ"]
    assert isinstance(entry, dict)
    assert entry["max_ms"] >= 2000
    assert src.take_lag() == {}  # reset after each heartbeat
    assert src.status.transport == "polling"
    assert not src.status.connected


@pytest.mark.asyncio
async def test_set_urgent_while_running_wakes_the_hot_poller() -> None:
    start = ny(MON, 10, 0, 2)
    clock = FakeClock(start)
    api = MemoryBars(clock, publish_delay_ns=2 * S)
    sink = Sink(clock)
    src = source(clock, api, sink)

    async def main() -> None:
        task = asyncio.ensure_future(src.run(DAY, FEEDS, DAY.open_ns, ny(MON, 10, 3)))
        await clock.sleep(10 * S)
        src.set_urgent([("MNQ", ny(MON, 10, 1))])
        await task

    await clock.run(main())
    got = {b.open_ns: r for b, r in sink.bars if b.instrument == "MNQ"}
    assert got[ny(MON, 10, 0)] == ny(MON, 10, 1, 2)


def test_hot_interval_must_be_below_the_poll_interval() -> None:
    clock = FakeClock(0)
    sink = Sink(clock)
    with pytest.raises(ValueError, match="hot_poll_interval_s"):
        source(clock, MemoryBars(clock), sink, poll_interval_s=5, hot_poll_interval_s=5)
