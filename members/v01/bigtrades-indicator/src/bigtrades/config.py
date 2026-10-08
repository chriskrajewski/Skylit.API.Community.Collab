# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 v01
"""Settings from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from .aggregator import PUBLISH_MODES, Thresholds
from .projectx.client import DEFAULT_API_URL

DEFAULT_MARKET_HUB = "https://rtc.topstepx.com/hubs/market"
# EXAMPLE thresholds only. Tune them for your own market and session.
EXAMPLE_THRESHOLDS = "ES:200,NQ:100"


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    username: str
    api_key: str
    api_url: str = DEFAULT_API_URL
    market_hub_url: str = DEFAULT_MARKET_HUB
    symbols: tuple[str, ...] = ("ES", "NQ")
    thresholds: Thresholds = field(default_factory=lambda: Thresholds.parse(EXAMPLE_THRESHOLDS))
    join_ms: int = 0
    quiet_ms: int = 300
    publish_mode: str = "final"
    live_data: bool = False
    discord_webhook_url: str = ""
    tape_dir: str = ""  # empty = recorder off
    tz: str = "UTC"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        symbols = tuple(s.strip() for s in env.get("BIGTRADES_SYMBOLS", "ES,NQ").split(",") if s.strip())
        mode = env.get("BIGTRADES_PUBLISH_MODE", "final").strip().lower() or "final"
        if mode not in PUBLISH_MODES:
            raise ValueError(f"BIGTRADES_PUBLISH_MODE must be one of {PUBLISH_MODES}")
        return cls(
            username=env.get("PROJECTX_USERNAME", ""),
            api_key=env.get("PROJECTX_API_KEY", ""),
            api_url=env.get("PROJECTX_API_URL", DEFAULT_API_URL) or DEFAULT_API_URL,
            market_hub_url=env.get("PROJECTX_MARKET_HUB_URL", DEFAULT_MARKET_HUB) or DEFAULT_MARKET_HUB,
            symbols=symbols,
            thresholds=Thresholds.parse(env.get("BIGTRADES_THRESHOLDS", EXAMPLE_THRESHOLDS)),
            join_ms=int(env.get("BIGTRADES_JOIN_MS", "0") or 0),
            quiet_ms=int(env.get("BIGTRADES_QUIET_MS", "300") or 300),
            publish_mode=mode,
            live_data=_bool(env.get("PROJECTX_LIVE_DATA")),
            discord_webhook_url=env.get("DISCORD_WEBHOOK_URL", "").strip(),
            tape_dir=env.get("BIGTRADES_TAPE_DIR", "").strip(),
            tz=env.get("BIGTRADES_TZ", "UTC").strip() or "UTC",
        )

    def require_credentials(self) -> None:
        if not self.username or not self.api_key:
            raise SystemExit("Set PROJECTX_USERNAME and PROJECTX_API_KEY (see .env.example).")
