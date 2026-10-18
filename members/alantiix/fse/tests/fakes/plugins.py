"""Test plugins: a scriptable recording plugin and loader samples.

:class:`RecordingPlugin` records every hook call in ``calls`` as
``(hook, now, detail)`` and runs ``handlers[hook](*args)`` when a test set
one, returning its intents. Its state is ``self.state`` (JSON), saved by
``snapshot()``; ``restore`` raises ``ValueError`` for ``{"unusable": true}``.
Subclasses change ``NEEDS``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, ClassVar

from fse.live.plugins import (
    Bar,
    BarNeed,
    DataNeeds,
    GlobexDay,
    Instant,
    Intent,
    OrderUpdate,
    PluginContext,
    PluginServices,
    StrategyPlugin,
)
from fse.logio.canonical_json import JsonValue

__all__ = ["MinimalPlugin", "NotAPlugin", "OldApiPlugin", "RecordingPlugin"]

type Handler = Callable[..., Sequence[Intent] | None]


class RecordingPlugin(StrategyPlugin):
    NEEDS: ClassVar[DataNeeds] = DataNeeds(bars=(BarNeed("MNQ"),), hours="eth")

    def __init__(
        self, plugin_id: str, config: Mapping[str, object], services: PluginServices
    ) -> None:
        super().__init__(plugin_id, config, services)
        self.calls: list[tuple[str, Instant, Any]] = []
        self.handlers: dict[str, Handler] = {}
        self.wake: Instant | None = None
        self.state: JsonValue = {"n": 0}
        self.updates: list[OrderUpdate] = []

    def needs(self) -> DataNeeds:
        return self.NEEDS

    def _run(self, hook: str, now: Instant, detail: Any, *args: Any) -> Sequence[Intent]:
        self.calls.append((hook, now, detail))
        handler = self.handlers.get(hook)
        if handler is None:
            return ()
        return handler(*args) or ()

    def hooks(self) -> list[str]:
        return [c[0] for c in self.calls]

    def on_start(self, ctx: PluginContext) -> Sequence[Intent]:
        return self._run("on_start", ctx.now, None, ctx)

    def on_day_open(self, day: GlobexDay, ctx: PluginContext) -> Sequence[Intent]:
        return self._run("on_day_open", ctx.now, day.day, day, ctx)

    def on_day_close(self, day: GlobexDay, ctx: PluginContext) -> Sequence[Intent]:
        return self._run("on_day_close", ctx.now, day.day, day, ctx)

    def on_bar(
        self, bar: Bar, received_ns: Instant, catch_up: bool, ctx: PluginContext
    ) -> Sequence[Intent]:
        return self._run(
            "on_bar",
            ctx.now,
            (bar.instrument, bar.open_ns, catch_up),
            bar,
            received_ns,
            catch_up,
            ctx,
        )

    def on_snapshots(
        self, snaps: Any, received_ns: Instant, ctx: PluginContext
    ) -> Sequence[Intent]:
        return self._run("on_snapshots", ctx.now, tuple(s.symbol for s in snaps), snaps, ctx)

    def on_dark_pool(
        self, ticker: str, prints: Any, received_ns: Instant, ctx: PluginContext
    ) -> Sequence[Intent]:
        return self._run("on_dark_pool", ctx.now, ticker, ticker, prints, ctx)

    def on_quote(self, quote: Any, ctx: PluginContext) -> Sequence[Intent]:
        return self._run("on_quote", ctx.now, quote.instrument, quote, ctx)

    def on_order_update(self, update: OrderUpdate, ctx: PluginContext) -> Sequence[Intent]:
        self.updates.append(update)
        return self._run("on_order_update", ctx.now, (update.tag, update.status), update, ctx)

    def on_feed_status(
        self, feed: Any, up: bool, detail: str, ctx: PluginContext
    ) -> Sequence[Intent]:
        return self._run("on_feed_status", ctx.now, (feed, up), feed, up, ctx)

    def next_wake(self, now: Instant) -> Instant | None:
        handler = self.handlers.get("next_wake")
        if handler is not None:
            return handler(now)  # type: ignore[return-value]
        return self.wake

    def on_wake(self, now: Instant, ctx: PluginContext) -> Sequence[Intent]:
        return self._run("on_wake", now, None, now, ctx)

    def urgent_bar_closes(self, day: GlobexDay) -> Sequence[tuple[str, Instant]]:
        return (("MNQ", day.rotate_ns - 3_600_000_000_000),)

    def snapshot(self) -> JsonValue:
        handler = self.handlers.get("snapshot")
        if handler is not None:
            return handler()  # type: ignore[return-value]
        return self.state

    def restore(self, state: JsonValue) -> None:
        if isinstance(state, dict) and state.get("unusable") is True:
            raise ValueError("unusable state")
        self.state = state
        self.calls.append(("restore", 0, state))

    def on_stop(self, ctx: PluginContext) -> None:
        self.calls.append(("on_stop", ctx.now, None))


class MinimalPlugin(StrategyPlugin):
    """Only ``needs``: every other hook is the base class no-op."""

    def needs(self) -> DataNeeds:
        return DataNeeds(bars=(BarNeed("MNQ"),), hours="eth")


class OldApiPlugin(MinimalPlugin):
    api_version: ClassVar[int] = 0


class NotAPlugin:
    pass
