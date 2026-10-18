"""A runnable template for a new ``fse run`` strategy plugin (copy it, rename it, fill it in).

``TemplatePlugin`` is a deliberately small strategy so the file runs as-is: once per
Globex day, at ``entry_time`` (NY), it enters ``qty`` contracts of ``instrument`` at
market with a stop and one target, and flattens whatever is still open at
``flatten_time``. Every hook of :class:`~fse.live.plugins.StrategyPlugin` is written
out with a comment saying when the host calls it and what a real strategy does there.

Load it from the runner YAML (``configs/runner.yaml`` ships this entry disabled)::

    - id: template
      module: fse.live.plugins.template:TemplatePlugin
      enabled: true
      config: {instrument: MNQ, entry_time: "10:00", direction: long, qty: 1,
               stop_ticks: 40, target_ticks: 40, flatten_time: "15:55"}
      fills: {trade_through_ticks: 1, slippage_ticks: 0, stop_fill: at_stop,
              commission_rt: {MNQ: "1.24"}}
      risk: {max_contracts: 1, max_open_trades: 1}

Rules (see :mod:`fse.live.plugins.api`): import only from ``fse.live.plugins`` (plus
pure helpers such as ``fse.timekit``); hooks are synchronous, return intents and must
finish within 1 s; slow or blocking work goes through ``services.run_blocking``.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from datetime import date, time
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fse.live.plugins import (
    Bar,
    BarNeed,
    DarkPoolPrint,
    DataNeeds,
    EnterMarket,
    FeedName,
    Flatten,
    GlobexDay,
    Instant,
    Intent,
    OrderUpdate,
    PluginContext,
    PluginServices,
    Quote,
    Snapshot,
    StrategyPlugin,
    TargetLeg,
    globex_day_at,
)
from fse.logio.canonical_json import JsonValue
from fse.timekit import ny_instant

__all__ = ["TemplateConfig", "TemplatePlugin"]

STATE_VERSION: Final = 1


class TemplateConfig(BaseModel):
    """The plugin's own ``config`` block: strict, unknown keys refused (exit 2 at load)."""

    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    instrument: str = "MNQ"
    """The futures contract it trades."""
    entry_time: time = time(10, 0)
    """NY wall time; the entry goes in when the bar closing at this instant arrives."""
    direction: Literal["long", "short"] = "long"
    """Buy (long) or sell (short)."""
    qty: int = Field(default=1, ge=1)
    """Contracts per entry."""
    stop_ticks: int = Field(default=40, ge=1)
    """Stop distance from the fill, in ticks (4 ticks = 1 point)."""
    target_ticks: int = Field(default=40, ge=1)
    """Target distance from the fill, in ticks."""
    flatten_time: time = time(15, 55)
    """NY wall time; anything still open is closed at the last bar close before it."""
    warmup_bars: int = Field(default=5, ge=0)
    """Closed bars needed before the first entry (a stand-in for an indicator warm-up)."""


class TemplatePlugin(StrategyPlugin):
    """One market entry per Globex day at ``entry_time``, flattened at ``flatten_time``."""

    label = "Template"
    description = (
        "One market entry per day at entry_time, closed at flatten_time: the starting "
        "point for a new plugin."
    )
    config_model = TemplateConfig

    def __init__(
        self, plugin_id: str, config: Mapping[str, object], services: PluginServices
    ) -> None:
        # 1. Construction: parse and check config only. No I/O, no network: the host
        #    builds every plugin before it starts any feed. Raise ValueError on bad config.
        super().__init__(plugin_id, config, services)
        try:
            self.cfg = TemplateConfig.model_validate(dict(config))
        except ValidationError as exc:
            raise ValueError(f"plugin {plugin_id!r}: bad config: {exc}") from exc
        # Persisted state (snapshot/restore): what must survive a restart.
        self.day: date | None = None
        self.entered = False
        self.exited = False
        # Runtime only: rebuilt from bars at start and by the host's repair replay.
        self.closes: deque[float] = deque(maxlen=max(self.cfg.warmup_bars, 1))
        self.bars_up = True

    # ------------------------------------------------------------ declaration

    def needs(self) -> DataNeeds:
        # 2. Needs: constant for the plugin's lifetime and checked against the data plane
        #    at start (every mismatch exits 2). Add ``skylit=SkylitNeed(...)`` for map
        #    snapshots or dark-pool prints, ``quotes=True`` for quotes. ``brokerage="host"``
        #    gives the plugin its own paper book (``fills``/``risk`` required in the YAML).
        #    ``hold_through_break=False`` lets the host flatten at the 18:00 rotation.
        return DataNeeds(bars=(BarNeed(self.cfg.instrument),), hours="eth", brokerage="host")

    # ------------------------------------------------------------ lifecycle

    def on_start(self, ctx: PluginContext) -> Sequence[Intent]:
        # 3. Warm-up: after restore() (or a fresh start), before the host replays the
        #    bars recorded since the last state write. Read history cheaply here (the
        #    bar store is local); a slow load (DataCache, big files) goes through
        #    services.run_blocking and is picked up in on_wake.
        d = globex_day_at(ctx.now)
        if d is not None:
            for bar in self.services.bar_store.bars(self.cfg.instrument, d.day):
                if bar.close_ns <= ctx.now:
                    self.closes.append(bar.c)
        ctx.log({"kind": "template_start", "level": "info", "warm_bars": len(self.closes)})
        return ()

    def on_day_open(self, day: GlobexDay, ctx: PluginContext) -> Sequence[Intent]:
        # 4. Session open: at day.open_ns (18:00 NY the evening before), and at start
        #    when started inside a day. Must be idempotent: a restart calls it again
        #    for a day already restored.
        if self.day != day.day:
            self.day = day.day
            self.entered = False
            self.exited = False
        return ()

    def on_day_close(self, day: GlobexDay, ctx: PluginContext) -> Sequence[Intent]:
        # 5. Session close: at day.rotate_ns (18:00 NY), before the host's backstop
        #    flattens positions the plugin may not hold through the break.
        if ctx.open_tags() or ctx.working_tags():
            return (Flatten(None, "day_close", "last_close", day.rotate_ns),)
        return ()

    def on_stop(self, ctx: PluginContext) -> None:
        # 6. Graceful stop (SIGINT/SIGTERM/--until). Close files the plugin opened.
        #    Open paper positions are not flattened; they resume on restart.
        return None

    # ------------------------------------------------------------ market data

    def on_bar(
        self, bar: Bar, received_ns: Instant, catch_up: bool, ctx: PluginContext
    ) -> Sequence[Intent]:
        # 7. A closed bar of a needed instrument, after the plugin's paper book has
        #    processed it (so fills on this bar are already reported). ``catch_up`` is
        #    true for backfill/repair-replay bars: update state, but decide whether a
        #    late entry still makes sense (this template does not enter on them).
        self.closes.append(bar.c)
        day = globex_day_at(bar.open_ns)
        if day is None or catch_up or self.entered or not self.bars_up:
            return ()
        if self.day != day.day:
            return ()  # on_day_open has not run for this day yet
        entry_ns = ny_instant(day.day, self.cfg.entry_time)
        if bar.close_ns != entry_ns or len(self.closes) < self.cfg.warmup_bars:
            return ()
        # 8. Risk/flatten policy before an entry: the host also enforces the YAML risk
        #    caps and kill switches (and rejects with a reason), but checking here
        #    keeps the plugin's own log readable.
        blocked = ctx.entries_blocked()
        if blocked or ctx.position(self.cfg.instrument) != 0:
            ctx.log({"kind": "entry_skipped", "level": "info", "blocked": list(blocked)})
            self.entered = True  # one try per day
            return ()
        self.entered = True
        tag = f"tpl{day.day:%Y%m%d}"
        return (
            EnterMarket(
                tag=tag,
                instrument=self.cfg.instrument,
                direction=self.cfg.direction,
                qty=self.cfg.qty,
                stop_ticks=self.cfg.stop_ticks,
                targets=(TargetLeg("tp1", self.cfg.qty, self.cfg.target_ticks),),
                placed_at=bar.close_ns,
                exact_bar_open=True,  # only the next bar (opening at placed_at) may fill
            ),
        )

    def on_snapshots(
        self, snaps: Sequence[Snapshot], received_ns: Instant, ctx: PluginContext
    ) -> Sequence[Intent]:
        # 9. Skylit map snapshots (only with needs().skylit), inside the Skylit window.
        return ()

    def on_dark_pool(
        self,
        ticker: str,
        prints: Sequence[DarkPoolPrint],
        received_ns: Instant,
        ctx: PluginContext,
    ) -> Sequence[Intent]:
        # 10. Skylit dark-pool prints (only with needs().skylit.dark_pool_tickers).
        return ()

    def on_quote(self, quote: Quote, ctx: PluginContext) -> Sequence[Intent]:
        # 11. ProjectX quotes (only with needs().quotes). Diagnostics or tick logic.
        return ()

    def on_feed_status(
        self, feed: FeedName, up: bool, detail: str, ctx: PluginContext
    ) -> Sequence[Intent]:
        # 12. A needed feed went down or up. This template stops entering while bars
        #     are down; the host keeps managing open brackets on the bars it gets.
        if feed == "bars":
            self.bars_up = up
        ctx.log({"kind": "feed_status", "level": "info", "feed": feed, "up": up})
        return ()

    # ------------------------------------------------------------ orders and fills

    def on_order_update(self, update: OrderUpdate, ctx: PluginContext) -> Sequence[Intent]:
        # 13. Fills and order events of the plugin's own tags: accepted, rejected
        #     (reason = the cap or block), filled_entry, filled_exit, closed (with the
        #     trade), expired, cancelled. Write the plugin's own outputs here.
        entry: dict[str, JsonValue] = {
            "kind": "order_update",
            "level": "info",
            "tag": update.tag,
            "status": update.status,
        }
        if update.reason is not None:
            entry["reason"] = update.reason
        if update.trade is not None:
            entry["net"] = str(update.trade.net)
        ctx.log(entry)
        return ()

    # ------------------------------------------------------------ timers

    def next_wake(self, now: Instant) -> Instant | None:
        # 14. The next instant the plugin wants on_wake (None: no timer). Evaluated
        #     after every dispatch round.
        if self.day is None:
            return None
        at = ny_instant(self.day, self.cfg.flatten_time)
        return at if self.entered and not self.exited else None

    def on_wake(self, now: Instant, ctx: PluginContext) -> Sequence[Intent]:
        # 15. At a wanted wake, and once after each finished run_blocking/spawn task
        #     (read task.result() here). Time-based exits go here.
        if self.day is None:
            return ()
        at = ny_instant(self.day, self.cfg.flatten_time)
        if now < at or not self.entered or self.exited:
            return ()
        self.exited = True  # one flatten per day; next_wake stops asking
        if ctx.open_tags() or ctx.working_tags():
            return (Flatten(None, "time_exit", "last_close", at),)
        return ()

    def urgent_bar_closes(self, day: GlobexDay) -> Sequence[tuple[str, Instant]]:
        # 16. Bars the data plane should fetch as early as possible (hot polling).
        return ((self.cfg.instrument, ny_instant(day.day, self.cfg.entry_time)),)

    # ------------------------------------------------------------ state persistence

    def snapshot(self) -> JsonValue:
        # 17. State for a restart, stored with the paper book in plugin.json after each
        #     dispatch round. Plain JSON only; keep it small.
        return {
            "version": STATE_VERSION,
            "day": None if self.day is None else self.day.isoformat(),
            "entered": self.entered,
            "exited": self.exited,
        }

    def restore(self, state: JsonValue) -> None:
        # 18. The inverse of snapshot(). Raise ValueError when unusable: the host renames
        #     the file plugin.json.bad-<stamp>, starts the plugin fresh and blocks its
        #     entries (restore_failed) until `fse clear --state-dir <live-state>/plugins/<id>`.
        if not isinstance(state, dict) or state.get("version") != STATE_VERSION:
            raise ValueError("not a version 1 template plugin state")
        day, entered, exited = state.get("day"), state.get("entered"), state.get("exited")
        if (
            not isinstance(entered, bool)
            or not isinstance(exited, bool)
            or not (day is None or isinstance(day, str))
        ):
            raise ValueError("bad template plugin state")
        self.day = None if day is None else date.fromisoformat(day)
        self.entered = entered
        self.exited = exited
