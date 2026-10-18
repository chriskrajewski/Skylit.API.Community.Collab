"""``StreamingMarketData``: hub-triggered bar fetches and transport switching (design 4.2).

The real :class:`~fse.projectx.realtime.MarketHubClient` runs over the fake
connect of :mod:`tests.fakes.market_hub`; bars come from
:class:`~tests.fakes.bar_api.MemoryBars`. No socket is opened.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from datetime import date
from typing import Any

import pytest

from fse.engine.types import Bar
from fse.live.bar_feed import FeedInstrument
from fse.live.host.dayclock import globex_day
from fse.live.host.sources import StreamingMarketData
from fse.live.plugins.api import Quote
from fse.projectx.models import format_instant
from fse.projectx.realtime import MarketHubClient
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, Instant
from tests.fakes.bar_api import MemoryBars
from tests.fakes.clock import FakeClock
from tests.fakes.dataplane import ny
from tests.fakes.market_hub import FakeMarketHub, FakeTokens, quote_payload, trade_payload

S = NS_PER_SECOND
MS = 1_000_000
M = NS_PER_MINUTE
MON = date(2026, 10, 12)
DAY = globex_day(MON)
MNQ_ID = "CON.F.US.MNQ.Z26"
MES_ID = "CON.F.US.MES.Z26"
FEEDS = (
    FeedInstrument("MNQ", "MNQZ6", MNQ_ID),
    FeedInstrument("MES", "MESZ6", MES_ID),
)
START = ny(MON, 10, 0, 2)


def iso(t: Instant) -> str:
    return format_instant(t).replace("Z", "+00:00")


class Rig:
    def __init__(self, *, publish_delay_ns: int = 200 * MS, plan: list[int | str] | None = None):
        self.clock = FakeClock(START)
        self.api = MemoryBars(self.clock, publish_delay_ns=publish_delay_ns)
        self.hub = FakeMarketHub(plan=list(plan or []), clock=self.clock)
        self.tokens = FakeTokens()
        self.bars: list[tuple[Bar, Instant]] = []
        self.quotes: list[Quote] = []
        self.status: list[tuple[str, bool, str]] = []
        self.trades: list[tuple[str, int]] = []
        self.src = StreamingMarketData(
            self.api,
            self.clock,
            hub=self._hub,
            on_bars=lambda bars, r: self.bars.extend((b, r) for b in bars),
            on_quote=self.quotes.append,
            on_status=lambda f, up, d: self.status.append((f, up, d)),
            on_trade=lambda i, t, r: self.trades.append((i, t.price_t)),
        )

    def _hub(
        self,
        cids: tuple[str, ...],
        on_quote: Callable[..., None],
        on_trade: Callable[..., None],
        on_state: Callable[[bool, str], None],
    ) -> MarketHubClient:
        return MarketHubClient(
            self.tokens,
            self.clock,
            rtc_url="wss://rtc.example.invalid/hubs/market",
            contract_ids=cids,
            connect=self.hub.connect,
            on_quote=on_quote,
            on_trade=on_trade,
            on_state=on_state,
        )

    async def drive(
        self, until: Instant, script: Callable[[Rig], Coroutine[Any, Any, None]] | None = None
    ) -> None:
        async def main() -> None:
            task = asyncio.ensure_future(self.src.run(DAY, FEEDS, DAY.open_ns, until))
            if script is not None:
                await script(self)
            await task

        await self.clock.run(main())

    async def at(self, t: Instant) -> None:
        """Sleep until ``t`` (virtual time; other tasks run while this one sleeps)."""
        now = self.clock.now()
        if t > now:
            await self.clock.sleep(t - now)

    def received(self, instrument: str) -> dict[Instant, Instant]:
        return {b.open_ns: r for b, r in self.bars if b.instrument == instrument}


@pytest.mark.asyncio
async def test_first_trade_after_the_minute_triggers_the_fetch() -> None:
    rig = Rig()
    m = ny(MON, 10, 1)

    async def script(rig: Rig) -> None:
        await rig.at(m + 400 * MS)
        rig.hub.last.push_trade(MNQ_ID, trade_payload(iso(m + 300 * MS)))

    await rig.drive(ny(MON, 10, 1, 30), script)
    assert rig.received("MNQ")[m - M] == m + 400 * MS  # the trade's arrival
    assert rig.received("MES")[m - M] == m + 1500 * MS  # no MES trade: m + 1.5 s
    assert rig.trades == [("MNQ", 24000)]
    assert rig.src.status.transport == "polling"  # the day ended: the hub is gone


@pytest.mark.asyncio
async def test_trigger_retries_every_second_until_the_bar_arrives() -> None:
    rig = Rig(publish_delay_ns=3 * S)
    m = ny(MON, 10, 1)
    await rig.drive(ny(MON, 10, 1, 30))
    # triggered at m + 1.5 s, retried at +2.5 s and +3.5 s (published at +3 s);
    # the 5 s safety grid (:02) would only have seen it at +7 s
    assert rig.received("MNQ")[m - M] == m + 3500 * MS
    calls = [t for t in rig.api.calls_for("MNQ") if m <= t < m + 10 * S]
    assert calls == [m + 1500 * MS, m + 2 * S, m + 2500 * MS, m + 3500 * MS]


@pytest.mark.asyncio
async def test_quotes_reach_the_callback_with_the_instrument() -> None:
    rig = Rig()

    async def script(rig: Rig) -> None:
        await rig.at(START + S)
        rig.hub.last.push_quote(MES_ID, quote_payload(iso(START + S)))

    await rig.drive(START + 10 * S, script)
    assert [(q.instrument, q.contract_id, q.bid_t, q.ask_t) for q in rig.quotes] == [
        ("MES", MES_ID, 24000, 24001)
    ]


@pytest.mark.asyncio
async def test_transport_switches_both_ways_and_no_bar_is_lost() -> None:
    rig = Rig()

    async def script(rig: Rig) -> None:
        await rig.at(START + S)
        assert rig.src.status.transport == "stream"
        rig.hub.last.drop()
        await rig.at(START + S + 100 * MS)
        assert rig.src.status.transport == "polling"
        await rig.at(START + 3 * S)  # reconnect after the 1 s backoff
        assert rig.src.status.transport == "stream"

    await rig.drive(ny(MON, 10, 3), script)
    quotes = [(up, d.split(":")[0]) for f, up, d in rig.status if f == "quotes"]
    assert quotes == [
        (True, "transport stream"),
        (False, "transport polling"),
        (True, "transport stream"),
        (False, "transport polling"),  # the day ended
    ]
    mnq = sorted(rig.received("MNQ"))
    assert mnq == list(range(DAY.open_ns, ny(MON, 10, 2), M))


@pytest.mark.asyncio
async def test_auto_falls_back_to_polling_when_the_hub_never_connects() -> None:
    rig = Rig(plan=["hang"] * 10)
    await rig.drive(ny(MON, 10, 2))
    assert not any(up for f, up, _ in rig.status if f == "quotes")
    # the safety grid's last tick before 10:02 is 10:01:57: bars up to the 10:00 bar
    assert sorted(rig.received("MNQ")) == list(range(DAY.open_ns, ny(MON, 10, 1), M))
    assert rig.src.status.transport == "polling"


@pytest.mark.asyncio
async def test_rt_dropped_counts_invalid_hub_messages() -> None:
    rig = Rig()

    async def script(rig: Rig) -> None:
        await rig.at(START + S)
        rig.hub.last.push_quote(MNQ_ID, quote_payload(iso(START), bid=6000.1))
        await rig.at(START + 2 * S)
        assert rig.src.status.rt_dropped == 1

    await rig.drive(START + 5 * S, script)
    assert rig.src.status.rt_dropped == 1
