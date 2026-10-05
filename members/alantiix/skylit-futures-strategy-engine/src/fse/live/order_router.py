"""The order router: where the Live_Runner's order intents go (design §23-24, Req 23.15).

The Order_Mode is resolved once, at start, and :class:`Routing` keeps it for
the whole run (Req 24.2): a Strategy_Config file changed during the run is
never read again.

**Paper Order_Mode** (:func:`paper_routing`). Every intent goes to the
:class:`~fse.sim.paper_broker.PaperBroker`, the in-process broker the
Backtester also uses. A Broker_Adapter given to the router is wrapped in
:class:`ReadOnlyBroker`, which exposes only the read calls
(:data:`READ_METHODS`), so no order submit, modify or cancel request can
reach it (Req 23.15).

Practice and Combine routing, with the Risk_Manager pre-check before the
Broker_Adapter, is added with the Broker_Adapter (task 35).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from fse.sim.paper_broker import PaperBroker

__all__ = ["ORDER_METHODS", "PAPER", "READ_METHODS", "ReadOnlyBroker", "Routing", "paper_routing"]

PAPER: Final = "paper"

READ_METHODS: Final[frozenset[str]] = frozenset(
    {"resolve_accounts", "resolve_contract", "broker_state", "find_by_client_id"}
)
"""The Broker_Adapter calls a Paper Order_Mode run may make: none of them changes an order."""

ORDER_METHODS: Final[frozenset[str]] = frozenset({"place_bracketed", "modify", "cancel"})
"""The Broker_Adapter calls that send an order request (never reachable in Paper mode)."""


class ReadOnlyBroker:
    """A Broker_Adapter without its order methods (Req 23.15).

    Only the names in :data:`READ_METHODS` are passed through; any other
    attribute, an order method included, raises ``AttributeError``.
    """

    __slots__ = ("_adapter",)

    def __init__(self, adapter: object) -> None:
        self._adapter = adapter

    def __getattr__(self, name: str) -> object:
        if name in READ_METHODS:
            return getattr(self._adapter, name)
        raise AttributeError(f"the Broker_Adapter has no {name!r} in Paper Order_Mode")

    def __repr__(self) -> str:
        return "ReadOnlyBroker()"


@dataclass(frozen=True, slots=True)
class Routing:
    """The run's Order_Mode, fixed at start, and where its orders go."""

    mode: Literal["paper"]
    paper: PaperBroker
    broker: ReadOnlyBroker | None

    @property
    def sends_broker_orders(self) -> bool:
        """Whether any order request can reach the Broker_Adapter (never in Paper mode)."""
        return False


def paper_routing(paper: PaperBroker, adapter: object | None = None) -> Routing:
    """Paper Order_Mode: every intent to ``paper``; ``adapter`` read-only."""
    return Routing(PAPER, paper, None if adapter is None else ReadOnlyBroker(adapter))
