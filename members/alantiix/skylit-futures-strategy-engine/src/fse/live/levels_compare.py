"""The ``GET /v1/gex/levels`` comparison log (design §23 "Levels comparison", Req 23.10).

In Paper and Practice Order_Mode (:func:`compare_active`), while the run window
is open, :class:`LevelsCompare` fetches ``GET /v1/gex/levels`` for every
configured symbol every ``live.levels_compare_interval_s`` (default 60 s), one
request for the ``gamma`` metric (:data:`LEVELS_METRIC`), the metric the King
and Gatekeeper labels are drawn from.

For each returned level (the ``kingNode`` and every entry of ``levels``) it
logs the Skylit ``nodeType`` and the labels the Node_Classifier
(:func:`fse.engine.nodes.classify`) gives the same strike in the live
Map_State Snapshot of that symbol at the receipt time: ``king``, ``floor``,
``ceiling``, ``gatekeeper`` and ``node``. ``agree`` is whether the Skylit
label (compared case-insensitively) is one of them; it is ``null`` when the
Skylit label is not one of those five names or is missing, or when the
Map_State has no Snapshot for the symbol. Strikes are compared exactly.

The fetched levels go only to the log (``sink``); nothing here is passed to
the Strategy_Engine. A failed request or no response within 5 s is logged and
the next fetch goes at the next tick.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final, Protocol

from fse.clock import Clock
from fse.engine.nodes import NodeLabels, NodeParams, classify
from fse.engine.types import Metric, Snapshot
from fse.live._wait import FeedLog, next_tick, within
from fse.logio.canonical_json import JsonValue
from fse.pit.market_view import LiveInputs
from fse.skylit.client import Failed
from fse.skylit.endpoints import ViewParams
from fse.skylit.models import Level, LevelsResponse
from fse.timekit import NS_PER_SECOND, Instant

__all__ = [
    "COMPARED_LABELS",
    "COMPARE_MODES",
    "LEVELS_METRIC",
    "LEVELS_TIMEOUT_S",
    "LevelsClient",
    "LevelsCompare",
    "compare_active",
    "engine_labels_at",
]

LEVELS_METRIC: Final[Metric] = "gamma"
LEVELS_TIMEOUT_S: Final = 5
COMPARE_MODES: Final = frozenset({"paper", "practice"})
"""The Order_Modes in which the comparison runs (Req 23.10)."""
COMPARED_LABELS: Final = ("king", "floor", "ceiling", "gatekeeper", "node")


def compare_active(order_mode: str) -> bool:
    """Whether the comparison runs in ``order_mode`` (Paper or Practice only)."""
    return order_mode in COMPARE_MODES


def engine_labels_at(labels: NodeLabels, strike: float) -> tuple[str, ...]:
    """The Node_Classifier labels of ``strike``, in :data:`COMPARED_LABELS` order."""
    found = {
        "king": labels.king == strike,
        "floor": labels.floor == strike,
        "ceiling": labels.ceiling == strike,
        "gatekeeper": strike in labels.gatekeepers,
        "node": strike in labels.nodes,
    }
    return tuple(name for name in COMPARED_LABELS if found[name])


class LevelsClient(Protocol):
    """The part of :class:`~fse.skylit.client.SkylitClient` the comparison uses."""

    async def gex_levels(
        self, symbols: Iterable[str], *, metric: str, view: ViewParams
    ) -> LevelsResponse | Failed: ...


class LevelsCompare:
    """Fetches the Skylit levels and logs their agreement with the engine's labels."""

    __slots__ = ("_client", "_clock", "_inputs", "_interval_ns", "_params", "_sink", "_symbols")

    def __init__(
        self,
        client: LevelsClient,
        clock: Clock,
        *,
        symbols: Iterable[str],
        inputs: LiveInputs,
        node_params: NodeParams,
        sink: FeedLog,
        interval_s: int,
    ) -> None:
        if isinstance(interval_s, bool) or interval_s < 1:
            raise ValueError(f"interval_s must be at least 1, got {interval_s!r}")
        self._client = client
        self._clock = clock
        self._symbols = tuple(symbols)
        if not self._symbols:
            raise ValueError("the levels comparison needs at least one symbol")
        self._inputs = inputs
        self._params = node_params
        self._sink = sink
        self._interval_ns = interval_s * NS_PER_SECOND

    async def compare_once(self, view: ViewParams) -> None:
        """One fetch and its comparison log entry (or a failure entry)."""
        sent = self._clock.now()
        result = await within(
            self._clock,
            self._client.gex_levels(self._symbols, metric=LEVELS_METRIC, view=view),
            LEVELS_TIMEOUT_S * NS_PER_SECOND,
        )
        received = self._clock.now()
        if not isinstance(result, LevelsResponse):
            cause = (
                result.cause
                if isinstance(result, Failed)
                else f"no response within {LEVELS_TIMEOUT_S} s"
            )
            self._sink({"event": "levels_compare_failed", "sent_ns": sent, "cause": cause})
            return
        state = self._inputs.view(received).map_state()
        symbols: list[JsonValue] = []
        for item in result.symbols:
            entry = state.get(item.symbol, LEVELS_METRIC)
            snapshot = entry if isinstance(entry, Snapshot) else None
            labels = None if snapshot is None else classify(snapshot, self._params)
            levels = (*(() if item.king_node is None else (item.king_node,)), *item.levels)
            symbols.append(
                {
                    "symbol": item.symbol,
                    "levels_as_of": item.as_of_raw,
                    "map_as_of": None if snapshot is None else snapshot.as_of_raw,
                    "levels": [_compare(level, labels) for level in levels],
                }
            )
        self._sink(
            {
                "event": "levels_compare",
                "received_ns": received,
                "metric": LEVELS_METRIC,
                "symbols": symbols,
            }
        )

    async def run(self, view: ViewParams, start_ns: Instant, end_ns: Instant) -> None:
        """Compare at ``start_ns + k * interval`` while before ``end_ns`` (the run window)."""
        now = self._clock.now()
        if now < start_ns:
            await self._clock.sleep(start_ns - now)
        tick = next_tick(start_ns, self._interval_ns, start_ns - 1, self._clock.now())
        while tick < end_ns:
            now = self._clock.now()
            if now < tick:
                await self._clock.sleep(tick - now)
            await self.compare_once(view)
            tick = next_tick(start_ns, self._interval_ns, tick, self._clock.now())


def _compare(level: Level, labels: NodeLabels | None) -> JsonValue:
    theirs = None if level.node_type is None else level.node_type.strip().lower()
    ours = None if labels is None else engine_labels_at(labels, level.strike)
    agree: bool | None = None
    if ours is not None and theirs in COMPARED_LABELS:
        agree = theirs in ours
    return {
        "strike": level.strike,
        "skylit_label": level.node_type,
        "engine_labels": None if ours is None else list(ours),
        "agree": agree,
    }
