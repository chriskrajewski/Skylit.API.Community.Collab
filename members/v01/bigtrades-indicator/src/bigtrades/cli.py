# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 v01
"""Command line: ``bigtrades run`` (live) and ``bigtrades replay FILE`` (offline)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from collections.abc import Sequence

from .aggregator import SweepAggregator
from .config import Settings
from .format import format_big_trade
from .tape import replay


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # optional
        return
    load_dotenv()


async def _run_live(settings: Settings) -> None:
    from .app import run

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass
    await run(settings, stop)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bigtrades", description="Big trades from the ProjectX tape.")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="connect to ProjectX and print big trades live")
    rp = sub.add_parser("replay", help="replay a recorded JSONL tape offline (no network)")
    rp.add_argument("path")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _load_dotenv()
    settings = Settings.from_env()

    if args.command == "replay":
        aggregator = SweepAggregator(
            settings.thresholds,
            join_ms=settings.join_ms,
            quiet_ms=settings.quiet_ms,
            publish_mode=settings.publish_mode,
        )
        with open(args.path, encoding="utf-8") as handle:
            for trade in replay(handle, aggregator):
                print(format_big_trade(trade, settings.tz))
        return 0

    try:
        asyncio.run(_run_live(settings))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
