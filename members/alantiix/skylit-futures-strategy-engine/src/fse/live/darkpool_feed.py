"""The live dark-pool feed (design §23 "Live data flow", Req 4.12).

Runs only when the ``dark_pool_confluence`` Gate is enabled
(:func:`feed_tickers` is empty otherwise). Every ``interval_s`` (default 60 s)
:class:`DarkPoolFeed` reads the session's new prints for the Gate's tickers
with ``GET /v1/dark-pool/trades`` (one request for all tickers, ``order=asc``,
``limit=5000``, the session's New York trade date). Pages are read from the
offset already received until a page has ``hasMore: false``, so each poll asks
only for prints added since the last one.

Each successful poll calls ``on_prints`` once per ticker, even with no new
print, with the receipt time of the last page; from the first such call the
ticker counts as fetched in :class:`~fse.pit.market_view.LiveInputs`. A print
for another ticker is dropped. A failed or refused request is logged and the
poll ends; the next poll resumes at the same offset. ``404 no_data`` counts as
an empty page, not a failure. When the next offset
would pass the documented 50,000 cap, the feed logs it once and stops polling
for the session, so the Gate sees only the prints received so far.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import date
from typing import Final

from fse.clock import Clock
from fse.config.schema.gates import DarkPoolConfluenceConfig
from fse.data.darkpool import PAGE_LIMIT, DarkPoolTradesClient
from fse.engine.types import DarkPoolPrint
from fse.live._wait import FeedLog, next_tick, no_log
from fse.skylit.client import Failed
from fse.skylit.endpoints import DARK_POOL_MAX_OFFSET
from fse.timekit import NS_PER_SECOND, Instant

__all__ = ["DEFAULT_INTERVAL_S", "DarkPoolFeed", "OnPrints", "feed_tickers"]

DEFAULT_INTERVAL_S: Final = 60

type OnPrints = Callable[[str, Sequence[DarkPoolPrint], Instant], None]
"""Receives one ticker's new prints (possibly none) and their receipt time."""


def feed_tickers(gate: DarkPoolConfluenceConfig) -> tuple[str, ...]:
    """The tickers to feed: the Gate's ES and NQ tickers when it is enabled, else none."""
    if not gate.enabled:
        return ()
    return tuple(dict.fromkeys((gate.es_ticker, gate.nq_ticker)))


class DarkPoolFeed:
    """Polls the session's new dark-pool prints (see the module notes)."""

    __slots__ = (
        "_capped",
        "_client",
        "_clock",
        "_interval_ns",
        "_log",
        "_min_notional",
        "_offset",
        "_on_prints",
        "_session",
        "_tickers",
    )

    def __init__(
        self,
        client: DarkPoolTradesClient,
        clock: Clock,
        *,
        tickers: Iterable[str],
        session: date,
        on_prints: OnPrints,
        log: FeedLog = no_log,
        interval_s: int = DEFAULT_INTERVAL_S,
        min_notional: float | None = None,
    ) -> None:
        self._tickers = tuple(dict.fromkeys(tickers))
        if not self._tickers:
            raise ValueError("the dark-pool feed needs at least one ticker")
        if isinstance(interval_s, bool) or interval_s < 1:
            raise ValueError(f"interval_s must be at least 1, got {interval_s!r}")
        self._client = client
        self._clock = clock
        self._session = session
        self._on_prints = on_prints
        self._log = log
        self._interval_ns = interval_s * NS_PER_SECOND
        self._min_notional = min_notional
        self._offset = 0
        self._capped = False

    @property
    def offset(self) -> int:
        """Prints received so far for the session, all tickers together."""
        return self._offset

    @property
    def stopped(self) -> bool:
        """True once the 50,000 offset cap ended polling for the session."""
        return self._capped

    async def poll_once(self) -> None:
        """Read the pages added since the last poll and deliver them per ticker."""
        if self._capped:
            return
        new: dict[str, list[DarkPoolPrint]] = {t: [] for t in self._tickers}
        received: Instant | None = None
        while True:
            response = await self._client.dark_pool_trades(
                self._tickers,
                date_start=self._session,
                date_end=self._session,
                limit=PAGE_LIMIT,
                offset=self._offset,
                min_notional=self._min_notional,
            )
            if isinstance(response, Failed) and response.no_data:
                received = self._clock.now()  # 404 no_data: no print yet, not a failure
                break
            if isinstance(response, Failed):
                self._log(
                    {
                        "event": "dark_pool_fetch_failed",
                        "offset": self._offset,
                        "cause": response.cause,
                    }
                )
                break
            received = self._clock.now()
            for wire in response.prints:
                bucket = new.get(wire.ticker)
                if bucket is not None:
                    bucket.append(wire.to_print())
            self._offset += len(response.prints)
            if not response.page.has_more or not response.prints:
                break
            if self._offset > DARK_POOL_MAX_OFFSET:
                self._capped = True
                self._log({"event": "dark_pool_offset_cap", "offset": self._offset})
                break
        if received is not None:
            for ticker, prints in new.items():
                self._on_prints(ticker, tuple(prints), received)

    async def run(self, end_ns: Instant) -> None:
        """Poll every ``interval_s`` until ``end_ns`` or the offset cap."""
        start = self._clock.now()
        tick = start
        while tick < end_ns and not self._capped:
            now = self._clock.now()
            if now < tick:
                await self._clock.sleep(tick - now)
            await self.poll_once()
            tick = next_tick(start, self._interval_ns, tick, self._clock.now())
