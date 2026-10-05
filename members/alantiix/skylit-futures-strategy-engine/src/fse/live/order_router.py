"""The order router: where the Live_Runner's order intents go (design §23-24, Req 23.15, 24).

The Order_Mode is resolved once, at start (:func:`resolve_order_mode`), and
:class:`Routing` keeps it for the whole run (Req 24.2): a Strategy_Config
file changed during the run is never read again.

**Resolution** (Req 24.1, 24.3, 24.6):

- ``combine`` only when all three Combine_Opt_In conditions hold: the
  config selects it, ``COMBINE_ACCOUNT_ID`` is set and exactly equals the id
  of an account the Broker_Adapter resolved, and the start command has the
  live-orders flag;
- ``practice`` only when ``PRACTICE_ACCOUNT_ID`` is set, equals a resolved
  account id and differs from ``COMBINE_ACCOUNT_ID``;
- otherwise Paper, and :attr:`ModeDecision.failures` names every failed
  condition for the first Finding_Card.

Account ids come only from the EnvView (Req 24.7). No account id is put in a
failure text or a card.

**Paper Order_Mode** (:func:`paper_routing`). Every intent goes to the
:class:`~fse.sim.paper_broker.PaperBroker`. A Broker_Adapter given to the
router is wrapped in :class:`ReadOnlyBroker`, which exposes only the read
calls (:data:`READ_METHODS`), so no order request can reach it (Req 23.15).

**Practice and Combine** (:func:`broker_routing`). The intents still go
through a :class:`MirrorBroker`, a Paper_Broker whose book is the
Strategy_Engine's expected positions and working orders (Req 24.16). Before
an intent joins that book, :meth:`MirrorBroker.route` translates it into a
:data:`BrokerAction` for the Broker_Adapter bound to the mode's account:

- an instrument on the ignored list is never submitted, modified or
  cancelled (Req 24.20);
- an entry is withheld while a broker block covers its instrument
  (:class:`BrokerGate`) or when ``RiskManager.precheck`` fails (position cap,
  internal daily loss stop; Req 24.23-24.24). A withheld entry becomes an
  :class:`~fse.engine.step.EntryRejected` for the next ``Engine.step`` and a
  note naming the check, its measured value and its limit;
- an order that only reduces or closes a position passes the pre-check
  (Req 24.25). A market exit is cut to the broker's open quantity on the
  closing side, and dropped when the broker holds nothing to close, so an
  exit can never open a reverse position.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final, Literal, Protocol

from fse.engine.planner import CancelOrder, ModifyOrder, OrderIntent, PlaceBracket, SubmitExit
from fse.engine.risk import RiskState, Withheld, precheck
from fse.engine.step import EntryRejected
from fse.engine.types import Order, OrderKind, SetupKey, Side, Ticks
from fse.projectx.broker import ACCOUNT_VARIABLES, BrokerFailure
from fse.projectx.models import AccountRef
from fse.sim.paper_broker import PaperBroker

__all__ = [
    "BROKER_MODES",
    "COMBINE",
    "LIVE_ORDERS_FLAG",
    "ORDER_METHODS",
    "PAPER",
    "PRACTICE",
    "READ_METHODS",
    "BrokerAction",
    "BrokerGate",
    "CancelAction",
    "EnvReader",
    "ExitAction",
    "MirrorBroker",
    "ModeDecision",
    "ModifyAction",
    "OrderMode",
    "PlaceAction",
    "ReadOnlyBroker",
    "Routing",
    "broker_routing",
    "paper_routing",
    "resolve_order_mode",
]

type OrderMode = Literal["paper", "practice", "combine"]

PAPER: Final = "paper"
PRACTICE: Final = "practice"
COMBINE: Final = "combine"
BROKER_MODES: Final[frozenset[str]] = frozenset({PRACTICE, COMBINE})
LIVE_ORDERS_FLAG: Final = "--live-orders"

READ_METHODS: Final[frozenset[str]] = frozenset(
    {"resolve_accounts", "resolve_contract", "broker_state", "find_by_client_id"}
)
"""The Broker_Adapter calls a Paper Order_Mode run may make: none of them changes an order."""

ORDER_METHODS: Final[frozenset[str]] = frozenset({"place_bracketed", "modify", "cancel"})
"""The Broker_Adapter calls that send an order request (never reachable in Paper mode)."""


# ---------------------------------------------------------------- resolution


class EnvReader(Protocol):
    def get(self, name: str) -> str | None: ...


@dataclass(frozen=True, slots=True)
class ModeDecision:
    """The Order_Mode chosen at start and every condition that failed (Req 24.1-24.6)."""

    requested: str
    mode: OrderMode
    failures: tuple[str, ...] = ()

    @property
    def note(self) -> str | None:
        """The first Finding_Card's line when the requested mode was not used."""
        if not self.failures:
            return None
        return f"Order_Mode {self.requested} was not used, running Paper: " + "; ".join(
            self.failures
        )


def resolve_order_mode(
    requested: str,
    env: EnvReader,
    accounts: Sequence[AccountRef] | BrokerFailure | None,
    *,
    live_orders: bool,
) -> ModeDecision:
    """The run's Order_Mode (see the module notes).

    ``accounts`` is what the Broker_Adapter resolved: a failure, or ``None``
    when no resolution was attempted, resolves no account.
    """
    if requested not in BROKER_MODES:
        return ModeDecision(requested, PAPER)
    resolved: set[str] = set()
    unresolved: str | None = None
    if isinstance(accounts, BrokerFailure):
        unresolved = f"the Broker_Adapter could not resolve the accounts ({accounts})"
    elif accounts is None:
        unresolved = "the Broker_Adapter did not resolve the accounts"
    else:
        resolved = {str(a.id) for a in accounts}
    combine = env.get(ACCOUNT_VARIABLES[COMBINE])
    failures: list[str] = []
    if requested == COMBINE:
        name = ACCOUNT_VARIABLES[COMBINE]
        if combine is None:
            failures.append(f"{name} is unset")
        elif unresolved is not None:
            failures.append(unresolved)
        elif combine not in resolved:
            failures.append(f"{name} matches no account the Broker_Adapter resolved")
        if not live_orders:
            failures.append(f"the start command has no {LIVE_ORDERS_FLAG} flag")
        if failures:
            return ModeDecision(requested, PAPER, tuple(failures))
        return ModeDecision(requested, COMBINE)
    name = ACCOUNT_VARIABLES[PRACTICE]
    practice = env.get(name)
    if practice is None:
        failures.append(f"{name} is unset")
    else:
        if unresolved is not None:
            failures.append(unresolved)
        elif practice not in resolved:
            failures.append(f"{name} matches no account the Broker_Adapter resolved")
        if combine is not None and practice == combine:
            failures.append(f"{name} equals {ACCOUNT_VARIABLES[COMBINE]}")
    if failures:
        return ModeDecision(requested, PAPER, tuple(failures))
    return ModeDecision(requested, PRACTICE)


# ---------------------------------------------------------------- broker actions


@dataclass(frozen=True, slots=True)
class PlaceAction:
    """Submit a bracketed entry (Req 24.8); the legs' engine ids are kept for the fill."""

    entry: Order
    stop: Order
    targets: tuple[Order, ...]

    @property
    def instrument(self) -> str:
        return self.entry.instrument


@dataclass(frozen=True, slots=True)
class ModifyAction:
    """Change the price of the broker order behind engine order ``client_id``."""

    client_id: str
    instrument: str
    kind: OrderKind
    price: Ticks


@dataclass(frozen=True, slots=True)
class CancelAction:
    """Cancel the broker order behind engine order ``client_id``."""

    client_id: str
    instrument: str
    role: str
    setup_key: SetupKey | None


@dataclass(frozen=True, slots=True)
class ExitAction:
    """A market order that closes ``qty`` of the open position (Req 24.25)."""

    order: Order
    side: Side
    qty: int


type BrokerAction = PlaceAction | ModifyAction | CancelAction | ExitAction


class BrokerGate(Protocol):
    """What the router reads from the broker side at route time (the BrokerSafety)."""

    def entry_block(self, instrument: str) -> str | None:
        """Why entries in ``instrument`` are blocked now, or ``None``."""
        ...

    def broker_positions(self) -> Mapping[str, int] | None:
        """The latest signed broker positions per instrument; ``None`` before the first sync."""
        ...

    def broker_working_entries(self) -> Mapping[str, int]:
        """Unfilled contracts of the Project's working entry orders at the broker."""
        ...


class MirrorBroker(PaperBroker):
    """A Paper_Broker that also turns each intent into a broker action (module notes)."""

    __slots__ = (
        "_cap",
        "_gate",
        "_ignored",
        "_known",
        "_note",
        "_outbound",
        "_risk",
        "_session",
    )

    def configure(
        self,
        *,
        gate: BrokerGate,
        ignored: Iterable[str],
        position_cap: int | None,
        risk: Callable[[], RiskState],
        session: Callable[[], date | None],
        note: Callable[[str], None],
    ) -> None:
        self._gate = gate
        self._ignored = frozenset(ignored)
        self._cap = position_cap
        self._risk = risk
        self._session = session
        self._note = note
        self._outbound: list[BrokerAction] = []
        self._known: dict[str, Order] = {}

    def known_order(self, client_id: str) -> Order | None:
        """The latest version of an engine order this run has routed, cancelled ones included."""
        current = self.book.order(client_id)
        return current if current is not None else self._known.get(client_id)

    def take_outbound(self) -> list[BrokerAction]:
        """The broker actions routed since the last call, in order."""
        out, self._outbound = self._outbound, []
        return out

    def route(self, intents: Iterable[OrderIntent]) -> list[EntryRejected]:
        rejected: list[EntryRejected] = []
        for intent in intents:
            if isinstance(intent, PlaceBracket):
                refusal = self._refuse_entry(intent)
                if refusal is not None:
                    key = intent.entry.setup_key
                    assert key is not None  # planner entries carry their key
                    rejected.append(EntryRejected(key, intent.entry.placed_at, refusal))
                    self._note(f"entry {intent.entry.instrument} withheld: {refusal}")
                    continue
                refused = super().route([intent])
                if refused:
                    rejected.extend(refused)
                    continue
                for leg in (intent.entry, intent.stop, *intent.targets):
                    self._known[leg.client_id] = leg
                self._outbound.append(PlaceAction(intent.entry, intent.stop, intent.targets))
                continue
            action = self._action(intent)
            rejected.extend(super().route([intent]))
            if action is not None:
                self._outbound.append(action)
        return rejected

    def _refuse_entry(self, intent: PlaceBracket) -> str | None:
        entry = intent.entry
        if entry.instrument in self._ignored:
            return f"{entry.instrument} is on the ignored-instrument list"
        blocked = self._gate.entry_block(entry.instrument)
        if blocked is not None:
            return blocked
        positions = self._gate.broker_positions()
        session = self._session()
        if positions is None or session is None:
            return "the broker state has not been read yet"
        configured = {i: q for i, q in positions.items() if i not in self._ignored}
        working = {
            i: q for i, q in self._gate.broker_working_entries().items() if i not in self._ignored
        }
        try:
            checked = precheck(self._risk(), entry, session, configured, working, self._cap)
        except ValueError as exc:
            return f"the Risk_Manager pre-check could not run ({exc})"
        if isinstance(checked, Withheld):
            return "; ".join(
                f"{f.check} measured {f.measured}, limit {f.limit}" for f in checked.failures
            )
        return None

    def _action(self, intent: OrderIntent) -> BrokerAction | None:
        if isinstance(intent, ModifyOrder):
            order = self.known_order(intent.client_id)
            if order is None or order.instrument in self._ignored or order.kind == "market":
                return None
            return ModifyAction(intent.client_id, order.instrument, order.kind, intent.price)
        if isinstance(intent, CancelOrder):
            order = self.known_order(intent.client_id)
            if order is None or order.instrument in self._ignored:
                return None
            self._known[order.client_id] = order
            return CancelAction(intent.client_id, order.instrument, order.role, order.setup_key)
        if isinstance(intent, SubmitExit):
            order = intent.order
            if order.instrument in self._ignored:
                return None
            self._known[order.client_id] = order
            held = (self._gate.broker_positions() or {}).get(order.instrument, 0)
            closing: Side = "sell" if held > 0 else "buy"
            if held == 0 or order.side != closing:
                self._note(
                    f"exit {order.instrument} not sent: the broker holds no position it would close"
                )
                return None
            return ExitAction(order, order.side, min(order.qty, abs(held)))
        return None


# ---------------------------------------------------------------- routing


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

    mode: OrderMode
    paper: PaperBroker
    broker: object | None

    @property
    def sends_broker_orders(self) -> bool:
        """Whether any order request can reach the Broker_Adapter (never in Paper mode)."""
        return self.mode in BROKER_MODES


def paper_routing(paper: PaperBroker, adapter: object | None = None) -> Routing:
    """Paper Order_Mode: every intent to ``paper``; ``adapter`` read-only."""
    return Routing(PAPER, paper, None if adapter is None else ReadOnlyBroker(adapter))


def broker_routing(mode: OrderMode, mirror: MirrorBroker, adapter: object) -> Routing:
    """Practice or Combine: ``adapter`` must be bound to ``mode``'s account."""
    if mode not in BROKER_MODES:
        raise ValueError(f"Order_Mode {mode!r} sends no broker orders")
    return Routing(mode, mirror, adapter)
