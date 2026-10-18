"""An in-memory data plane for :class:`~fse.live.host.host.PluginHost` tests.

:class:`FakeDataPlane` plays a script of ``(instant, event)`` pairs through
the injected clock (``FakeClock`` in tests), records everything the host
hands it (``record``, ``store_bars``, ``set_urgent``, ``rotate``) and serves
the recorded bars back for the startup repair replay. A restart test builds
the second data plane with ``recorded=`` the first one's recordings, as the
real data plane reads today's recording file.

Helpers build bars, a runner config, a host with the project's calendars,
and read JSONL logs.
"""

from __future__ import annotations

import asyncio
import io
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, time, timedelta
from pathlib import Path
from typing import Any, Final

from fse.calendars import Calendars, load_calendars, project_calendar_dir
from fse.clock import Clock
from fse.engine.types import Bar
from fse.live.host.config import RunnerConfig, parse_runner_config
from fse.live.host.dayclock import GlobexDay, globex_day_at
from fse.live.host.host import (
    BarsEvent,
    BlockingRunner,
    HostPaths,
    HostResources,
    InlineBlockingRunner,
    MarketEvent,
    PluginHost,
)
from fse.live.plugins.api import StrategyPlugin
from fse.logio import LogWriter
from fse.logio.redact import Redactor
from fse.secrets.env import EnvView
from fse.timekit import NS_PER_MINUTE, Instant, ny_datetime, ny_instant

__all__ = [
    "CALENDAR_DIR",
    "PROJECT_DIR",
    "FakeDataPlane",
    "MemoryBarStore",
    "host_entry",
    "make_bar",
    "make_host",
    "ny",
    "read_jsonl",
    "runner_config",
]

PROJECT_DIR: Final = Path(__file__).resolve().parents[2]
CALENDAR_DIR: Final = project_calendar_dir(PROJECT_DIR)


def ny(d: date, hh: int, mm: int = 0, ss: int = 0) -> Instant:
    """The Instant of New York wall time ``hh:mm:ss`` on ``d``."""
    return ny_instant(d, time(hh, mm, ss))


def make_bar(
    instrument: str,
    open_ns: Instant,
    o_t: int,
    h_t: int | None = None,
    l_t: int | None = None,
    c_t: int | None = None,
    *,
    contract: str | None = None,
) -> Bar:
    """A closed 1-minute futures bar in ticks (``h``/``l``/``c`` default to the open)."""
    h = o_t if h_t is None else h_t
    low = o_t if l_t is None else l_t
    c = o_t if c_t is None else c_t
    h, low = max(h, o_t, c), min(low, o_t, c)
    return Bar(
        instrument=instrument,
        contract=contract or f"{instrument}Z6",
        interval_s=60,
        open_ns=open_ns,
        close_ns=open_ns + NS_PER_MINUTE,
        o=o_t / 4,
        h=h / 4,
        l=low / 4,
        c=c / 4,
        v=100.0,
        o_t=o_t,
        h_t=h,
        l_t=low,
        c_t=c,
        source="projectx",
    )


class MemoryBarStore:
    """A :class:`~fse.live.plugins.api.BarStoreView` over the bars the host stored."""

    def __init__(self, root: Path = Path("/nonexistent-bar-store")) -> None:
        self._root = root
        self._bars: dict[tuple[str, date], list[Bar]] = {}

    @property
    def root(self) -> Path:
        return self._root

    def add(self, bar: Bar) -> None:
        day = globex_day_at(bar.open_ns)
        if day is not None:
            self._bars.setdefault((bar.instrument, day.day), []).append(bar)

    def bars(self, instrument: str, day: date) -> tuple[Bar, ...]:
        return tuple(self._bars.get((instrument, day), ()))


class FakeDataPlane:
    """Plays a script of events and records what the host hands it."""

    def __init__(
        self,
        clock: Clock,
        script: Iterable[tuple[Instant, MarketEvent]] = (),
        *,
        recorded: Sequence[MarketEvent] = (),
    ) -> None:
        self.clock = clock
        self.script: list[tuple[Instant, MarketEvent]] = list(script)
        self.recorded: list[MarketEvent] = list(recorded)
        self.stored: list[tuple[Bar, Instant]] = []
        self.urgent: list[list[tuple[str, Instant]]] = []
        self.rotations: list[tuple[date, date | None]] = []
        self.closed = False
        self.calls: list[str] = []
        """``record``/``store`` calls in order, for dispatch-order tests."""
        self.record_error: OSError | None = None
        self.bar_store = MemoryBarStore()
        for event in self.recorded:
            if isinstance(event, BarsEvent):
                for bar in event.bars:
                    self.bar_store.add(bar)

    def at(self, t: Instant, event: MarketEvent) -> FakeDataPlane:
        self.script.append((t, event))
        return self

    async def run(self, submit: Callable[[MarketEvent], None]) -> None:
        for t, event in sorted(self.script, key=lambda p: p[0]):
            delay = t - self.clock.now()
            if delay > 0:
                await self.clock.sleep(delay)
            submit(event)
        await asyncio.get_running_loop().create_future()  # idle until cancelled

    def record(self, event: MarketEvent) -> None:
        if self.record_error is not None:
            raise self.record_error
        self.calls.append(f"record:{type(event).__name__}")
        self.recorded.append(event)

    def store_bars(self, bars: Sequence[Bar], received_ns: Instant) -> None:
        self.calls.append("store")
        for bar in bars:
            self.stored.append((bar, received_ns))
            self.bar_store.add(bar)

    def recorded_bars(self, day: GlobexDay) -> list[tuple[Bar, Instant]]:
        out: list[tuple[Bar, Instant]] = []
        for event in self.recorded:
            if isinstance(event, BarsEvent):
                out.extend(
                    (bar, event.received_ns)
                    for bar in event.bars
                    if day.open_ns <= bar.open_ns < day.rotate_ns
                )
        return out

    def set_urgent(self, closes: Sequence[tuple[str, Instant]]) -> None:
        self.urgent.append(list(closes))

    def rotate(self, closing: GlobexDay, opening: GlobexDay | None) -> None:
        self.rotations.append((closing.day, None if opening is None else opening.day))

    def close(self) -> None:
        self.closed = True


def host_entry(plugin_id: str = "p1", **extra: Any) -> dict[str, Any]:
    """A host-brokered ``plugins[]`` entry (MNQ $1.24 RT, slip 0, generous caps)."""
    entry: dict[str, Any] = {
        "id": plugin_id,
        "module": "tests.fakes.dataplane:unused",
        "fills": {"commission_rt": {"MNQ": "1.24", "MES": "1.24"}, "slippage_ticks": 0},
        "risk": {"max_contracts": 50, "max_open_trades": 5},
    }
    entry.update(extra)
    return entry


def runner_config(
    plugins: Sequence[Mapping[str, Any]],
    *,
    data_plane: Mapping[str, Any] | None = None,
    accounts: Sequence[Mapping[str, Any]] = (),
) -> RunnerConfig:
    doc: dict[str, Any] = {"version": 1, "plugins": [dict(p) for p in plugins]}
    if data_plane is not None:
        doc["data_plane"] = dict(data_plane)
    if accounts:
        doc["accounts"] = [dict(a) for a in accounts]
    return parse_runner_config(doc, Path("runner.yaml"))


def make_host(
    tmp_path: Path,
    classes: Mapping[str, type[StrategyPlugin]],
    plugins: Sequence[Mapping[str, Any]],
    clock: Clock,
    dataplane: FakeDataPlane,
    *,
    data_plane: Mapping[str, Any] | None = None,
    calendars: Calendars | None = None,
    calendar_loader: Callable[[date, date], Calendars] | None = None,
    blocking: BlockingRunner | None = None,
    perf_ns: Callable[[], int] | None = None,
    writer: LogWriter | None = None,
    accounts: Sequence[Mapping[str, Any]] = (),
    resources: HostResources | None = None,
) -> PluginHost:
    """A host over ``tmp_path`` (``live-state/``, ``run/``) with the project's calendars."""
    cfg = runner_config(plugins, data_plane=data_plane, accounts=accounts)
    today = ny_datetime(clock.now()).date()
    if calendars is None:
        calendars = load_calendars(CALENDAR_DIR, today - timedelta(days=3), today + timedelta(7))
    kwargs: dict[str, Any] = {}
    if perf_ns is not None:
        kwargs["perf_ns"] = perf_ns
    return PluginHost(
        cfg,
        classes,
        dataplane=dataplane,
        clock=clock,
        writer=writer or LogWriter(Redactor(), stdout=io.StringIO(), stderr=io.StringIO()),
        paths=HostPaths(
            state_root=tmp_path / "live-state",
            run_dir=tmp_path / "run",
            cache_dir=tmp_path / "cache",
            calendar_dir=CALENDAR_DIR,
            project_dir=PROJECT_DIR,
        ),
        calendars=calendars,
        resources=resources or HostResources(env=EnvView({}, {})),
        calendar_loader=calendar_loader,
        blocking=blocking or InlineBlockingRunner(),
        **kwargs,
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Every line of a JSONL file (``[]`` when it does not exist)."""
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
