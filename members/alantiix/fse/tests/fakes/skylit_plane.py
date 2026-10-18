"""Fakes and builders for :class:`~fse.live.host.dataplane.DataPlane` tests.

:class:`FakeSkylit` is a ``SkylitPlaneClient``: ``heatmap`` serves one small
board per symbol stamped with the request instant, ``dark_pool_trades``
answers ``404 no_data`` (no print yet), ``gex_levels`` fails, and ``stream``
is never used (the tests run the map in polling mode). :func:`make_dataplane`
builds a data plane over ``tmp_path`` with the project's calendars.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from fse.calendars import Calendars, load_calendars
from fse.clock import Clock
from fse.live.bar_feed import BarRetriever
from fse.live.host.config import DataPlaneConfig
from fse.live.host.dataplane import DataPlane, DataPlanePaths
from fse.live.host.sources import HubFactory
from fse.logio import LogWriter, Redactor
from fse.logio.canonical_json import JsonValue, ny_iso
from fse.skylit.client import Failed
from fse.skylit.endpoints import Host
from fse.skylit.fetch_log import FetchLog
from fse.skylit.models import HeatmapResponse
from fse.timekit import Instant, ny_datetime
from tests.fakes.dataplane import CALENDAR_DIR

__all__ = ["FakeSkylit", "Plane", "make_dataplane", "plane_config"]


@dataclass
class FakeSkylit:
    clock: Clock
    fetch_log: FetchLog | None = None
    heatmaps: list[tuple[Instant, str]] = field(default_factory=list)
    dark_pool_calls: list[Instant] = field(default_factory=list)

    async def heatmap(
        self, symbols: Iterable[str], *, metric: str, view: Any, layout: Any = "net"
    ) -> HeatmapResponse:
        now = self.clock.now()
        self.heatmaps.append((now, metric))
        boards = [
            {
                "symbol": s,
                "asOf": ny_iso(now),
                "spot": 500.0,
                "previousClose": 499.0,
                "expirations": ["2026-10-12"],
                "strikes": [{"strike": 500.0, "value": 1.5, "nodeType": None}],
            }
            for s in symbols
        ]
        return HeatmapResponse.parse(
            {
                "data": {"symbols": boards},
                "meta": {"metric": metric, "resolution": "1s", "mode": "live", "cached": False},
            }
        )

    def stream(self, params: Any, *, last_event_id: str | None = None) -> Any:
        raise AssertionError("the tests run the map in polling mode")

    async def gex_levels(self, symbols: Iterable[str], *, metric: str, view: Any) -> Failed:
        return Failed(Host.API, "/v1/gex/levels", {}, "failed", 1, status=503)

    async def dark_pool_trades(self, tickers: Iterable[str], **kw: Any) -> Failed:
        self.dark_pool_calls.append(self.clock.now())
        return Failed(
            Host.API, "/v1/dark-pool/trades", {}, "rejected", 1, status=404, error_code="no_data"
        )


def plane_config(**overrides: Any) -> DataPlaneConfig:
    """A data plane config: polling transport, map polling every 15 s, 09:00-09:05."""
    doc: dict[str, Any] = {
        "instruments": ["MNQ", "MES"],
        "projectx": {"transport": "polling", "subscription": "sim", "backfill_sessions": 0},
        "skylit": {
            "mode": "polling",
            "refresh_interval_s": 15,
            "window": {"start": "09:00", "end": "09:05"},
            "dark_pool": {"enabled": True, "tickers": ["QQQ"]},
        },
    }
    for key, value in overrides.items():
        if isinstance(value, Mapping) and isinstance(doc.get(key), dict):
            doc[key] = {**doc[key], **value}
        else:
            doc[key] = value
    return DataPlaneConfig.model_validate(doc)


@dataclass
class Plane:
    plane: DataPlane
    logs: list[dict[str, Any]]
    fatal: list[tuple[int, str]]
    skylit: list[FakeSkylit]
    writer: LogWriter
    paths: DataPlanePaths

    def kinds(self) -> list[str]:
        return [str(e["kind"]) for e in self.logs]


def make_dataplane(
    tmp_path: Path,
    clock: Clock,
    bars: Callable[[bool], BarRetriever],
    cfg: DataPlaneConfig | None = None,
    *,
    hub: HubFactory | None = None,
    calendars: Calendars | None = None,
    writer: LogWriter | None = None,
) -> Plane:
    today = ny_datetime(clock.now()).date()
    cals = calendars or load_calendars(
        CALENDAR_DIR, today - timedelta(days=40), today + timedelta(days=8)
    )
    logs: list[dict[str, Any]] = []
    fatal: list[tuple[int, str]] = []
    skylits: list[FakeSkylit] = []
    w = writer or LogWriter(Redactor(), stdout=io.StringIO(), stderr=io.StringIO())

    def skylit(fetch_log: FetchLog) -> FakeSkylit:
        client = FakeSkylit(clock, fetch_log)
        skylits.append(client)
        return client

    def log(entry: Mapping[str, JsonValue]) -> None:
        logs.append({"t_ns": clock.now(), **entry})

    paths = DataPlanePaths(tmp_path / "rec", tmp_path / "run", tmp_path / "bars")
    plane = DataPlane(
        cfg or plane_config(),
        clock=clock,
        writer=w,
        paths=paths,
        calendars=cals,
        bars=bars,
        log=log,
        on_fatal=lambda code, detail: fatal.append((code, detail)),
        hub=hub,
        skylit=skylit,
        calendar_loader=lambda first, last: load_calendars(CALENDAR_DIR, first, last),
    )
    return Plane(plane, logs, fatal, skylits, w, paths)
