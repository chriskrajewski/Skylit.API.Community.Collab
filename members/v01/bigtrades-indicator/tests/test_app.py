# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 v01
from __future__ import annotations

import asyncio

from bigtrades import app as app_module
from bigtrades.app import BigTradesApp
from bigtrades.config import Settings
from bigtrades.sinks import CallbackSink


def test_hub_messages_to_sink_and_recorder(monkeypatch, tmp_path, fixture_lines):
    import json

    clock = [0.0]
    monkeypatch.setattr(app_module, "now_ms", lambda: clock[0])
    got: list[str] = []

    async def collect(trade, text):
        got.append(text)

    settings = Settings.from_env({"BIGTRADES_THRESHOLDS": "ES:200", "BIGTRADES_TAPE_DIR": str(tmp_path)})
    from bigtrades.tape import TapeRecorder

    app = BigTradesApp(settings, [CallbackSink(collect)], TapeRecorder(tmp_path))

    async def go() -> None:
        await app.on_hub_message("GatewayQuote", ["CON.F.US.EP.Z26", {}])  # ignored
        for line in fixture_lines("tape_es_sweep.jsonl")[:6]:
            clock[0] += 1
            await app.on_hub_message("GatewayTrade", json.loads(line)["arguments"])
        clock[0] += 1000
        await app._publish(app.aggregator.flush(clock[0]))

    asyncio.run(go())
    assert len(got) == 1 and got[0].startswith("ES big SELL 501 @ 7830.00→7829.50")
    assert len(next(tmp_path.iterdir()).read_text().splitlines()) == 6
