"""Fakes and builders for the Playbook plugin tests (``fse.live.plugins.playbook``).

- :data:`MARKET`: the pinned funnel sessions (2026-03-02, 03-03) and an
  early-close session (2026-03-06, close 13:15) of
  :mod:`tests.strategies.backtest_inputs`.
- :func:`make_world` writes the test calendars (with MES/MNQ roll entries, so
  ``session_inputs`` finds the bar feed's contracts) and the Data_Cache.
- :class:`FakeSkylit` serves the heatmap, GEX levels and dark-pool prints of
  one fixture session as of the request instant; :class:`FakeBars` its closed
  bars (optionally published late).
- :func:`pinned_plugin` is ``PlaybookPlugin`` with a fake ``perf_ns`` and a
  fixed code version, so two runs write the same live log.
"""

from __future__ import annotations

import io
import itertools
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final

from fse.calendars import ROLL_CALENDAR_FILE, Calendars, load_calendars
from fse.config.schema import StrategyConfig
from fse.engine.types import Bar, Snapshot
from fse.live.plugins.playbook import PlaybookPlugin
from fse.logio import LogWriter, Redactor
from fse.logio.canonical_json import ny_iso
from fse.projectx.models import BarHistory
from fse.skylit.client import Failed
from fse.skylit.models import (
    DarkPoolPage,
    DarkPoolTradesResponse,
    HeatmapResponse,
    LevelsResponse,
    WireDarkPoolPrint,
)
from fse.timekit import Instant, SessionTimes
from tests.fakes.clock import FakeClock
from tests.strategies.backtest_inputs import (
    CODE_VERSION,
    EARLY_CLOSE_DAY,
    FAKE_SECRET,
    MarketInputs,
    SessionInputs,
    bar_opens,
    build_session,
    funnel_case,
    write_cache,
    write_calendars,
)

__all__ = [
    "BASE_CFG",
    "EARLY",
    "FUNNEL",
    "MARKET",
    "FakeBars",
    "FakeSkylit",
    "World",
    "make_world",
    "own_calendars",
    "perf_counter",
    "pinned_plugin",
    "writer",
]

FUNNEL, BASE_CFG = funnel_case()
EARLY: Final = build_session(EARLY_CLOSE_DAY, 1306, bar_opens(EARLY_CLOSE_DAY, 60, (100, 40)))
MARKET: Final = MarketInputs((*FUNNEL.sessions, EARLY))
MICRO_ROLL: Final = """\
  MES:
    - {first: 2026-02-02, last: 2026-03-13, contract: MESH6}
  MNQ:
    - {first: 2026-02-02, last: 2026-03-13, contract: MNQH6}
"""


class FakeSkylit:
    """Heatmap, levels and dark-pool prints of one fixture session, as of the request instant."""

    def __init__(self, clock: FakeClock, market: SessionInputs) -> None:
        self.clock = clock
        self.market = market
        self.calls: list[tuple[Instant, str]] = []

    def _latest(self, metric: str) -> dict[str, Snapshot]:
        now = self.clock.now()
        latest: dict[str, Snapshot] = {}
        for snap in self.market.snapshots:
            if snap.metric == metric and snap.as_of_ns <= now:
                latest[snap.symbol] = snap
        return latest

    async def heatmap(
        self, symbols: Any, *, metric: str, view: Any, layout: Any = "net"
    ) -> HeatmapResponse:
        now = self.clock.now()
        self.calls.append((now, f"heatmap:{metric}"))
        latest = self._latest(metric)
        boards = [_board(latest[s], now) for s in symbols if s in latest]
        return HeatmapResponse.parse(
            {
                "data": {"symbols": boards},
                "meta": {"metric": metric, "resolution": "1s", "mode": "live", "cached": False},
            }
        )

    def stream(self, params: Any, *, last_event_id: str | None = None) -> Any:
        raise AssertionError("the map runs in polling mode")

    async def gex_levels(self, symbols: Iterable[str], *, metric: str, view: Any) -> Any:
        now = self.clock.now()
        self.calls.append((now, "levels"))
        latest = self._latest(metric)
        items: list[dict[str, Any]] = []
        for symbol in symbols:
            snap = latest.get(symbol)
            if snap is None or not snap.strikes:
                continue
            levels = [
                {
                    "strike": k,
                    "value": v,
                    "nodeType": None,
                    "distancePct": round((k - snap.spot) / snap.spot * 100, 4),
                }
                for k, v in zip(snap.strikes, snap.values, strict=True)
            ]
            king = max(levels, key=lambda x: abs(float(str(x["value"]))))
            items.append(
                {
                    "symbol": symbol,
                    "asOf": ny_iso(now),
                    "spot": snap.spot,
                    "previousClose": None,
                    "kingNode": {**king, "nodeType": "king"},
                    "levels": [x for x in levels if x is not king],
                    "summary": None,
                }
            )
        return LevelsResponse.parse(
            {
                "data": {"symbols": items},
                "meta": {"metric": metric, "resolution": "1s", "mode": "live", "cached": False},
            }
        )

    async def dark_pool_trades(
        self,
        tickers: Iterable[str],
        *,
        date_start: date,
        date_end: date,
        limit: int = 500,
        offset: int = 0,
        min_notional: float | None = None,
    ) -> DarkPoolTradesResponse | Failed:
        now = self.clock.now()
        self.calls.append((now, "dark_pool"))
        wanted = set(tickers)
        seen = [p for p in self.market.prints if p.ts_ns <= now and p.ticker in wanted]
        page = seen[offset : offset + limit]
        wire = tuple(
            WireDarkPoolPrint(
                timestamp_raw=ny_iso(p.ts_ns),
                ts_ns=p.ts_ns,
                ticker=p.ticker,
                price=p.price,
                size=p.size,
                notional=p.notional,
                venue=p.venue,
                sector=None,
                industry=None,
                pct_avg_vol=None,
            )
            for p in page
        )
        return DarkPoolTradesResponse(wire, DarkPoolPage(limit, offset, len(wire), False))


def _board(snap: Snapshot, as_of: Instant) -> dict[str, Any]:
    return {
        "symbol": snap.symbol,
        "asOf": ny_iso(as_of),
        "spot": snap.spot,
        "previousClose": snap.previous_close,
        "expirations": list(snap.expirations),
        "strikes": [
            {"strike": k, "value": v, "nodeType": None}
            for k, v in zip(snap.strikes, snap.values, strict=True)
        ],
    }


class FakeBars:
    """ProjectX ``retrieveBars``: the session's bars closed inside the window and published."""

    def __init__(self, clock: FakeClock, market: SessionInputs, publish_ns: int = 0) -> None:
        self.clock = clock
        self.bars: tuple[Bar, ...] = market.bars
        self.publish_ns = publish_ns

    async def retrieve(
        self,
        *,
        instrument: str,
        contract: str,
        contract_id: str,
        start_ns: Instant,
        end_ns: Instant,
        interval_s: int,
    ) -> BarHistory:
        published = self.clock.now() - self.publish_ns
        got = tuple(
            b
            for b in self.bars
            if b.instrument == instrument
            and b.open_ns >= start_ns
            and b.close_ns <= min(end_ns, published)
        )
        return BarHistory(
            instrument, contract, contract_id, interval_s, start_ns, end_ns, got, (), 1
        )


@dataclass(frozen=True, slots=True)
class World:
    root: Path
    calendar_dir: Path
    cache: Path


def make_world(root: Path) -> World:
    """The test calendars (with MES/MNQ roll periods) and the Data_Cache of :data:`MARKET`."""
    calendar_dir = write_calendars(root / "calendars")
    roll = calendar_dir / ROLL_CALENDAR_FILE
    roll.write_text(roll.read_text(encoding="utf-8") + MICRO_ROLL, encoding="utf-8")
    return World(root, calendar_dir, write_cache(root / "cache", MARKET))


def own_calendars(world: World, cfg: StrategyConfig, day: date) -> Calendars:
    """The calendars ``fse paper`` loads for ``day`` (the account's session times)."""
    times = cfg.account.session_times(SessionTimes())
    return load_calendars(world.calendar_dir, day, day, times=times)


def perf_counter() -> Callable[[], int]:
    """A fake ``perf_ns``: every Decision_Time takes 1 ms."""
    ticks = itertools.count(0, 1_000_000)
    return lambda: next(ticks)


def writer() -> LogWriter:
    return LogWriter(Redactor([FAKE_SECRET]), stdout=io.StringIO(), stderr=io.StringIO())


def pinned_plugin(base: type[PlaybookPlugin] = PlaybookPlugin) -> type[PlaybookPlugin]:
    """``base`` with a fake ``perf_ns`` and the fixed :data:`CODE_VERSION`."""
    perf = perf_counter()

    class Pinned(base):  # type: ignore[valid-type,misc]
        perf_ns = staticmethod(perf)

        def code_version(self) -> str:
            return CODE_VERSION

    return Pinned
