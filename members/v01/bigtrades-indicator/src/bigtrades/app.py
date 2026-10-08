# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 v01
"""Live wiring: REST login -> contract resolve -> market hub -> aggregator -> sinks."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from .aggregator import SweepAggregator
from .config import Settings
from .format import format_big_trade
from .gateway import parse_gateway_trade
from .models import BigTrade
from .projectx import HubConnection, ProjectXClient, pick_front_month
from .sinks import ConsoleSink, DiscordWebhookSink, Sink, publish_all
from .tape import TapeRecorder

log = logging.getLogger(__name__)
FLUSH_EVERY_SEC = 0.05


def now_ms() -> float:
    return time.monotonic() * 1000


async def resolve_contracts(px: ProjectXClient, symbols: tuple[str, ...], live: bool) -> dict[str, str]:
    """``("ES", "CON.F.US.ENQ.Z26")`` -> {root: contract_id}. Explicit ids pass through."""
    from .projectx.symbols import display_root

    resolved: dict[str, str] = {}
    for symbol in symbols:
        if symbol.upper().startswith("CON."):
            resolved[display_root(symbol)] = symbol
            continue
        rows = await px.search_contracts(symbol, live=live)
        row = pick_front_month(rows, symbol)
        if row is None:
            log.warning("no contract found for %s", symbol)
            continue
        resolved[symbol.upper()] = str(row["id"])
    return resolved


class BigTradesApp:
    def __init__(self, settings: Settings, sinks: list[Sink], recorder: TapeRecorder | None = None) -> None:
        self.settings = settings
        self.sinks = sinks
        self.recorder = recorder
        self.aggregator = SweepAggregator(
            settings.thresholds,
            join_ms=settings.join_ms,
            quiet_ms=settings.quiet_ms,
            publish_mode=settings.publish_mode,
        )

    async def on_hub_message(self, target: str, arguments: list[Any]) -> None:
        if target != "GatewayTrade":
            return
        arrived = now_ms()
        if self.recorder is not None:
            self.recorder.record(arguments, time.time() * 1000)
        for p in parse_gateway_trade(arguments):
            await self._publish(self.aggregator.on_print(p, arrived))

    async def flush_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await self._publish(self.aggregator.flush(now_ms()))
            try:
                await asyncio.wait_for(stop.wait(), timeout=FLUSH_EVERY_SEC)
            except TimeoutError:
                pass
        await self._publish(self.aggregator.close_all())

    async def _publish(self, trades: list[BigTrade]) -> None:
        for trade in trades:
            await publish_all(self.sinks, trade, format_big_trade(trade, self.settings.tz))


async def run(
    settings: Settings,
    stop: asyncio.Event | None = None,
    extra_sinks: list[Sink] | None = None,
) -> None:
    """Run live. ``extra_sinks`` receive every big trade too (e.g. ``CallbackSink`` for a bot)."""
    settings.require_credentials()
    stop = stop or asyncio.Event()
    async with httpx.AsyncClient(timeout=15.0) as http:
        sinks: list[Sink] = [ConsoleSink()]
        if settings.discord_webhook_url:
            sinks.append(DiscordWebhookSink(settings.discord_webhook_url, http))
        sinks.extend(extra_sinks or [])
        recorder = TapeRecorder(settings.tape_dir) if settings.tape_dir else None
        app = BigTradesApp(settings, sinks, recorder)
        async with ProjectXClient(settings.username, settings.api_key, base_url=settings.api_url, http=http) as px:
            contracts = await resolve_contracts(px, settings.symbols, settings.live_data)
            if not contracts:
                raise SystemExit("No contracts resolved. Check BIGTRADES_SYMBOLS.")
            for root, cid in contracts.items():
                threshold = settings.thresholds.for_root(root)
                log.info("watching %s (%s), threshold %s", root, cid, threshold or "none")

            async def on_connected(hub: HubConnection) -> None:
                for cid in contracts.values():
                    await hub.send("SubscribeContractTrades", cid)

            hub = HubConnection(settings.market_hub_url, px.token, app.on_hub_message, on_connected)
            await asyncio.gather(hub.run_forever(stop), app.flush_loop(stop))
