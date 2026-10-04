"""Fill_Simulator: fills of working orders against futures bars (design §13, Req 13).

The book is immutable: every operation returns a new :class:`SimBook`.

**Timing** (Req 5.5-5.6). An order fills only on bars that open at or after
its ``placed_at``. A price change or cancellation at Decision_Time ``t`` is a
new version of the order, effective for bars that open at or after ``t``. A
bar that opened earlier still sees the old version, so a fill on it stands.
Bars of one instrument must arrive in open-time order.

**Per bar** of an instrument, in this order (design §13):

1. Market orders fill at ``open ± slippage``, against the order (Req 13.4).
2. Stop orders (stop entries and protective stops) are touched when
   ``high ≥`` a buy stop or ``low ≤`` a sell stop. They fill at the stop
   price, or at the open when the bar opens past it, then ``± slippage``
   against the order (Req 13.3). A protective stop fills every open contract
   of its trade (Req 13.6).
3. Limit entries fill in full at the limit price when ``low ≤ limit -
   through`` for a buy or ``high ≥ limit + through`` for a sell, gap opens
   included (Req 13.1). A new trade's stop is checked on its entry fill bar
   (Req 13.5).
4. Targets (``tp1``, ``tp2``) fill like limit entries, from the bar after the
   entry fill (Req 13.5). A bar that fills the stop leaves no contract for a
   target, so a bar meeting both fills only the stop (Req 13.6).

Each fill costs ``contracts x (commission + exchange fee)`` (Req 13.7).

**Trades.** :meth:`SimBook.submit_bracket` places one entry (limit or stop)
with its protective stop and targets under one Setup_Key. The stop and targets
work only while the trade is open, and whatever is left is cancelled when the
trade closes (OCO). :meth:`SimBook.submit_exit` adds a market exit. A target or
market exit fills at most the open contracts.

**Accounting** (Req 13.9-13.12) is exact. Prices are ticks and money is
``Decimal`` in a fixed context, so no float touches P&L. For a closed trade:

- ``net`` = the sum over exits of ``(exit - entry)`` ticks x direction sign
  x tick value x contracts, minus the fees of every fill;
- ``r`` = ``|entry fill - initial stop|`` ticks x tick value x contracts at
  entry, where the initial stop is the stop price in effect on the entry fill
  bar;
- ``r_multiple`` = ``net / r``: exactly ``-1`` for a full exit at the initial
  stop with zero fees;
- ``mae_r`` and ``mfe_r``: the largest adverse and favorable excursion from
  the entry fill, in R, over the highs and lows of every bar from the entry
  fill bar through the final exit bar (Req 20.6);
- ``missing_bars``: bar slots inside the session's RTH that had no bar while
  the trade was open. The next available bar gets the usual fill rules.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
)
from itertools import pairwise
from types import MappingProxyType
from typing import Final

from fse.config.schema.fills import FillsConfig
from fse.engine.levels import TICKS_PER_POINT
from fse.engine.types import (
    Bar,
    Direction,
    Fill,
    Money,
    Order,
    OrderRole,
    SetupKey,
    Side,
    Ticks,
    Trade,
    direction_sign,
)
from fse.timekit import NS_PER_SECOND, Instant

__all__ = [
    "POINT_VALUE_USD",
    "TICK_VALUE_USD",
    "Bracket",
    "FillEvent",
    "OpenTrade",
    "SimBook",
    "WorkingOrder",
    "close_all",
    "on_bar",
    "tick_value",
]

_CTX: Final = Context(
    prec=34, rounding=ROUND_HALF_EVEN, traps=[InvalidOperation, DivisionByZero, Overflow]
)
"""Every money operation runs here, so results do not depend on the caller's context."""

TICK_VALUE_USD: Final[Mapping[str, Money]] = MappingProxyType(
    {"MES": Decimal("1.25"), "MNQ": Decimal("0.50"), "ES": Decimal("12.50"), "NQ": Decimal("5.00")}
)
"""Dollars per 0.25-point tick per contract (Req 13.9)."""

POINT_VALUE_USD: Final[Mapping[str, Money]] = MappingProxyType(
    {name: _CTX.multiply(value, TICKS_PER_POINT) for name, value in TICK_VALUE_USD.items()}
)
"""Dollars per point per contract: MES $5, MNQ $2, ES $50, NQ $20 (Req 13.9)."""

_ZERO: Final = Decimal(0)
_ENTRY_KINDS: Final = frozenset({"limit", "stop"})
_TARGET_ROLES: Final[frozenset[OrderRole]] = frozenset({"tp1", "tp2"})


def tick_value(instrument: str) -> Money:
    """The tick value of ``instrument``; ``ValueError`` for an instrument without one."""
    value = TICK_VALUE_USD.get(instrument)
    if value is None:
        raise ValueError(
            f"instrument {instrument!r} has no contract value; known: {sorted(TICK_VALUE_USD)}"
        )
    return value


def _direction_of(entry_side: Side) -> Direction:
    return "long" if entry_side == "buy" else "short"


def _exit_side(direction: Direction) -> Side:
    return "sell" if direction == "long" else "buy"


def _price(order: Order) -> Ticks:
    if order.price is None:
        raise ValueError(f"order {order.client_id!r} has no price")
    return order.price


def _require_int(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer, got {value!r}")


# ---------------------------------------------------------------- book records


@dataclass(frozen=True, slots=True)
class WorkingOrder:
    """One order in the book with its timeline of versions (Req 5.5-5.6).

    ``versions`` holds ``(effective_from, order)`` pairs in increasing time
    order. An ``order`` of ``None`` is a cancellation. A bar that opens at
    ``o`` sees the last version with ``effective_from <= o``, and no version
    before the first one (the placement).
    """

    versions: tuple[tuple[Instant, Order | None], ...]

    def __post_init__(self) -> None:
        if not self.versions or self.versions[0][1] is None:
            raise ValueError("a WorkingOrder starts with a placed order")
        times = [at for at, _ in self.versions]
        if any(a >= b for a, b in pairwise(times)):
            raise ValueError("WorkingOrder versions must be in increasing time order")

    @property
    def placed(self) -> Order:
        """The earliest version still kept; its identity fields never change."""
        first = self.versions[0][1]
        assert first is not None  # checked in __post_init__
        return first

    @property
    def latest(self) -> Order | None:
        """The newest version, ``None`` once a cancellation is pending or done."""
        return self.versions[-1][1]

    def effective(self, open_ns: Instant) -> tuple[int, Order | None] | None:
        """The index and version a bar opening at ``open_ns`` sees; ``None`` before placement."""
        for i in range(len(self.versions) - 1, -1, -1):
            at, version = self.versions[i]
            if at <= open_ns:
                return i, version
        return None

    def with_version(self, at: Instant, version: Order | None) -> WorkingOrder | None:
        """A new version from ``at`` on; ``None`` when a cancellation leaves no placed version."""
        _require_int("at", at)
        last_at = self.versions[-1][0]
        if at < last_at:
            raise ValueError(
                f"order {self.placed.client_id!r}: a change at {at} comes before "
                f"its latest version at {last_at}"
            )
        kept = self.versions[:-1] if at == last_at else self.versions
        out = (*kept, (at, version))
        if out[0][1] is None:
            return None
        return WorkingOrder(out)


@dataclass(frozen=True, slots=True)
class Bracket:
    """A placed Setup_Key: its instrument, direction and Shadow_Trade flag."""

    instrument: str
    direction: Direction
    shadow: bool


@dataclass(frozen=True, slots=True)
class OpenTrade:
    """An open position of one Setup_Key, with its running accounting.

    ``gross`` is the price P&L of the exits so far and ``fees`` the fees of
    every fill so far, the entry included. ``adverse_ticks`` and
    ``favorable_ticks`` are the excursions from the entry fill price so far.
    ``last_close_ns`` is the close of the last bar processed while open.
    """

    setup_key: SetupKey
    instrument: str
    direction: Direction
    entry_fill: Fill
    initial_stop: Ticks
    qty_at_entry: int
    open_qty: int
    last_close_ns: Instant
    exits: tuple[Fill, ...] = ()
    gross: Money = _ZERO
    fees: Money = _ZERO
    reached_tp1: bool = False
    adverse_ticks: int = 0
    favorable_ticks: int = 0
    missing_bars: int = 0
    shadow: bool = False

    @property
    def sign(self) -> int:
        """``+1`` for a long, ``-1`` for a short."""
        return direction_sign(self.direction)

    @property
    def risk_ticks(self) -> int:
        """``|entry fill - initial stop|`` in ticks; above 0."""
        return abs(self.entry_fill.price - self.initial_stop)

    @property
    def r(self) -> Money:
        """R: risk ticks x tick value x contracts at entry (Req 13.11)."""
        return _CTX.multiply(
            _CTX.multiply(tick_value(self.instrument), self.risk_ticks), self.qty_at_entry
        )

    @property
    def realized(self) -> Money:
        """Realized P&L so far: the exits' price P&L minus every fee charged."""
        return _CTX.subtract(self.gross, self.fees)

    def unrealized(self, price: Ticks) -> Money:
        """The open contracts valued at ``price``, before fees."""
        return _gross(self, price, self.open_qty)

    def worst_price(self, bar: Bar) -> Ticks:
        """The bar's worst price for this position: the low for a long, the high for a short."""
        h, low = bar.h_t, bar.l_t
        if h is None or low is None:
            raise ValueError(f"bar of {bar.instrument!r} has no tick prices")
        return low if self.direction == "long" else h


@dataclass(frozen=True, slots=True)
class FillEvent:
    """One fill with what the Account_Simulator and Risk_Manager need about it.

    ``order`` is the order version that filled (role, side, Setup_Key); a
    protective stop fills every open contract, so ``fill.qty`` can differ from
    ``order.qty``. ``gross`` is the fill's price P&L before fees, 0 for an
    entry. ``closed`` is the trade this fill closed, if any.
    """

    fill: Fill
    order: Order
    gross: Money
    closed: Trade | None

    @property
    def net(self) -> Money:
        """The fill's realized P&L after its fees."""
        return _CTX.subtract(self.gross, self.fill.fees)


def _gross(trade: OpenTrade, price: Ticks, qty: int) -> Money:
    ticks = (price - trade.entry_fill.price) * trade.sign
    return _CTX.multiply(_CTX.multiply(tick_value(trade.instrument), ticks), qty)


def _fees(cfg: FillsConfig, instrument: str, qty: int) -> Money:
    costs = cfg.costs_for(instrument)
    if costs is None:
        raise ValueError(
            f"fills.costs.{instrument} is not set: commission and exchange_fee are required "
            f"for every traded instrument"
        )
    return _CTX.multiply(_CTX.add(costs.commission, costs.exchange_fee), qty)


def _excursions(direction: Direction, entry: Ticks, high: Ticks, low: Ticks) -> tuple[int, int]:
    """``(adverse, favorable)`` ticks of a bar from ``entry``, each at least 0."""
    if direction == "long":
        return max(0, entry - low), max(0, high - entry)
    return max(0, high - entry), max(0, entry - low)


def _extend(trade: OpenTrade, high: Ticks, low: Ticks) -> OpenTrade:
    adverse, favorable = _excursions(trade.direction, trade.entry_fill.price, high, low)
    if adverse <= trade.adverse_ticks and favorable <= trade.favorable_ticks:
        return trade
    return replace(
        trade,
        adverse_ticks=max(trade.adverse_ticks, adverse),
        favorable_ticks=max(trade.favorable_ticks, favorable),
    )


def _apply_exit(
    trade: OpenTrade,
    client_id: str,
    bar_open_ns: Instant,
    price: Ticks,
    qty: int,
    role: OrderRole,
    cfg: FillsConfig,
) -> tuple[OpenTrade, Fill, Money]:
    fees = _fees(cfg, trade.instrument, qty)
    gross = _gross(trade, price, qty)
    fill = Fill(client_id, bar_open_ns, price, qty, fees)
    updated = replace(
        trade,
        open_qty=trade.open_qty - qty,
        exits=(*trade.exits, fill),
        gross=_CTX.add(trade.gross, gross),
        fees=_CTX.add(trade.fees, fees),
        reached_tp1=trade.reached_tp1 or role == "tp1",
    )
    return updated, fill, gross


def _closed_trade(trade: OpenTrade) -> Trade:
    """The closed :class:`Trade` of a fully exited position (Req 13.10-13.12)."""
    risk = trade.risk_ticks
    r = trade.r
    net = trade.realized
    return Trade(
        setup_key=trade.setup_key,
        entry_fill=trade.entry_fill,
        initial_stop=trade.initial_stop,
        qty_at_entry=trade.qty_at_entry,
        exits=trade.exits,
        net=net,
        r=r,
        r_multiple=_CTX.divide(net, r),
        reached_tp1=trade.reached_tp1,
        mae_r=_CTX.divide(Decimal(trade.adverse_ticks), Decimal(risk)),
        mfe_r=_CTX.divide(Decimal(trade.favorable_ticks), Decimal(risk)),
        missing_bars=trade.missing_bars,
        shadow=trade.shadow,
    )


def _check_prices(
    direction: Direction, entry: Ticks, stop: Ticks, targets: Iterable[Ticks]
) -> None:
    sign = direction_sign(direction)
    if not sign * stop < sign * entry:
        raise ValueError(
            f"a {direction} bracket needs its stop beyond the entry on the loss side: "
            f"entry {entry}, stop {stop}"
        )
    for target in targets:
        if not sign * entry < sign * target:
            raise ValueError(
                f"a {direction} bracket needs each target beyond the entry on the profit side: "
                f"entry {entry}, target {target}"
            )


def _missing_slots(
    prev_close: Instant, next_open: Instant, interval_ns: int, rth: tuple[Instant, Instant]
) -> int:
    """Bar slots starting in ``[prev_close, next_open)`` and inside ``[rth open, rth close)``."""
    lo = max(prev_close, rth[0])
    hi = min(next_open, rth[1])
    if hi <= lo:
        return 0
    first = -(-(lo - prev_close) // interval_ns)
    end = -(-(hi - prev_close) // interval_ns)
    return max(0, end - first)


# ---------------------------------------------------------------- the book


@dataclass(frozen=True, slots=True)
class SimBook:
    """Working orders, placed Setup_Keys and open trades. Never mutated in place.

    - ``orders``: every working order by client id, in submission order;
    - ``brackets``: every Setup_Key with a working entry or an open trade;
    - ``trades``: the open trades;
    - ``last_bar_open``: the open of the last bar processed per instrument.
    """

    orders: Mapping[str, WorkingOrder] = field(default_factory=dict)
    brackets: Mapping[SetupKey, Bracket] = field(default_factory=dict)
    trades: Mapping[SetupKey, OpenTrade] = field(default_factory=dict)
    last_bar_open: Mapping[str, Instant] = field(default_factory=dict)

    # ------------------------------------------------------------ queries

    def order(self, client_id: str) -> Order | None:
        """The latest version of a working order; ``None`` if unknown or cancelled."""
        working = self.orders.get(client_id)
        return None if working is None else working.latest

    def working_orders(self) -> tuple[Order, ...]:
        """The latest version of every working order not cancelled, in submission order."""
        return tuple(w.latest for w in self.orders.values() if w.latest is not None)

    def positions(self) -> dict[str, int]:
        """Open contracts per instrument, positive for long and negative for short."""
        out: dict[str, int] = {}
        for trade in self.trades.values():
            out[trade.instrument] = out.get(trade.instrument, 0) + trade.sign * trade.open_qty
        return out

    def working_entry_contracts(self) -> dict[str, int]:
        """Contracts of working entry orders not cancelled, per instrument."""
        out: dict[str, int] = {}
        for order in self.working_orders():
            if order.role == "entry":
                out[order.instrument] = out.get(order.instrument, 0) + order.qty
        return out

    # ------------------------------------------------------------ order management

    def submit_bracket(
        self, entry: Order, stop: Order, targets: Iterable[Order] = (), *, shadow: bool = False
    ) -> SimBook:
        """Place an entry with its protective stop and targets under one Setup_Key.

        The entry is a limit or stop order with role ``entry``; the stop a stop
        order with role ``stop`` for the entry quantity; the targets limit
        orders with distinct roles ``tp1`` and ``tp2`` whose quantities sum to
        at most the entry quantity. All share the Setup_Key, instrument and
        ``placed_at``; the stop and targets are on the opposite side. Prices
        must run stop, entry, targets in the trade direction.
        """
        legs = (entry, stop, *targets)
        key = entry.setup_key
        if key is None:
            raise ValueError("a bracket entry needs a setup_key")
        if entry.role != "entry" or entry.kind not in _ENTRY_KINDS:
            raise ValueError("a bracket entry is a limit or stop order with role 'entry'")
        tick_value(entry.instrument)
        if stop.role != "stop" or stop.kind != "stop":
            raise ValueError("a bracket stop is a stop order with role 'stop'")
        roles = [t.role for t in legs[2:]]
        if any(r not in _TARGET_ROLES for r in roles) or len(set(roles)) != len(roles):
            raise ValueError("bracket targets have distinct roles 'tp1' and 'tp2'")
        if any(t.kind != "limit" for t in legs[2:]):
            raise ValueError("bracket targets are limit orders")
        for leg in legs:
            if (leg.setup_key, leg.instrument, leg.placed_at) != (
                key,
                entry.instrument,
                entry.placed_at,
            ):
                raise ValueError(
                    f"order {leg.client_id!r} must share the entry's setup_key, "
                    f"instrument and placed_at"
                )
        direction = _direction_of(entry.side)
        if any(leg.side != _exit_side(direction) for leg in legs[1:]):
            raise ValueError("a bracket's stop and targets are on the side opposite the entry")
        if stop.qty != entry.qty:
            raise ValueError(f"the stop covers {stop.qty} contracts, the entry {entry.qty}")
        if sum(t.qty for t in legs[2:]) > entry.qty:
            raise ValueError("bracket targets cover more contracts than the entry")
        _check_prices(direction, _price(entry), _price(stop), [_price(t) for t in legs[2:]])
        if key in self.brackets:
            raise ValueError(f"Setup_Key {key} is already placed")
        ids = [leg.client_id for leg in legs]
        if len(set(ids)) != len(ids) or any(cid in self.orders for cid in ids):
            raise ValueError(f"client ids {ids} must be new and distinct")
        orders = dict(self.orders)
        for leg in legs:
            orders[leg.client_id] = WorkingOrder(((leg.placed_at, leg),))
        brackets = {**self.brackets, key: Bracket(entry.instrument, direction, shadow)}
        return replace(self, orders=orders, brackets=brackets)

    def submit_exit(self, order: Order) -> SimBook:
        """Add a market exit for a placed Setup_Key; it fills only while the trade is open."""
        if order.role != "exit" or order.kind != "market":
            raise ValueError("an exit is a market order with role 'exit'")
        bracket = None if order.setup_key is None else self.brackets.get(order.setup_key)
        if bracket is None:
            raise ValueError(f"exit {order.client_id!r} has no placed Setup_Key")
        if order.instrument != bracket.instrument or order.side != _exit_side(bracket.direction):
            raise ValueError(f"exit {order.client_id!r} must close its trade's instrument and side")
        if order.client_id in self.orders:
            raise ValueError(f"client id {order.client_id!r} is already working")
        orders = {**self.orders, order.client_id: WorkingOrder(((order.placed_at, order),))}
        return replace(self, orders=orders)

    def modify(self, client_id: str, price: Ticks, at: Instant) -> SimBook:
        """Change a limit or stop price for bars opening at or after ``at`` (Req 5.6)."""
        working = self.orders[client_id]
        latest = working.latest
        if latest is None:
            raise ValueError(f"order {client_id!r} is cancelled")
        if latest.kind == "market":
            raise ValueError(f"market order {client_id!r} has no price to change")
        _require_int("price", price)
        changed = replace(latest, price=price)
        key = latest.setup_key
        if key is not None and key not in self.trades:
            self._check_pending(key, changed)
        updated = working.with_version(at, changed)
        assert updated is not None  # a price change is never a cancellation
        return replace(self, orders={**self.orders, client_id: updated})

    def cancel(self, client_id: str, at: Instant) -> SimBook:
        """Cancel an order for bars opening at or after ``at`` (Req 5.6).

        An entry that a bar opening before ``at`` fills still fills, with its
        stop and targets. Once a cancelled entry can no longer fill, its stop
        and targets are removed with it. Cancelling an order whose cancellation
        is already pending changes nothing.
        """
        working = self.orders[client_id]
        if working.latest is None:
            return self
        updated = working.with_version(at, None)
        if updated is not None:
            return replace(self, orders={**self.orders, client_id: updated})
        # Cancelled at its placement time: no bar can ever see the order.
        orders = {cid: w for cid, w in self.orders.items() if cid != client_id}
        brackets = dict(self.brackets)
        placed = working.placed
        key = placed.setup_key
        if placed.role == "entry" and key is not None and key not in self.trades:
            orders = {cid: w for cid, w in orders.items() if w.placed.setup_key != key}
            brackets.pop(key, None)
        return replace(self, orders=orders, brackets=brackets)

    def _check_pending(self, key: SetupKey, changed: Order) -> None:
        """Keep a pending bracket's prices in order after a change to one leg."""
        latest: dict[OrderRole, Order] = {}
        for working in self.orders.values():
            order = working.latest
            if order is not None and order.setup_key == key:
                latest[order.role] = order
        latest[changed.role] = changed
        entry, stop = latest.get("entry"), latest.get("stop")
        if entry is None or stop is None:
            return
        targets = [_price(o) for role, o in latest.items() if role in _TARGET_ROLES]
        _check_prices(_direction_of(entry.side), _price(entry), _price(stop), targets)


# ---------------------------------------------------------------- bar processing


class _BarRun:
    """The mutable working copies of one :func:`on_bar` call."""

    __slots__ = (
        "bar",
        "brackets",
        "cfg",
        "events",
        "high",
        "live",
        "low",
        "open",
        "orders",
        "rth",
        "trades",
    )

    def __init__(
        self,
        book: SimBook,
        bar: Bar,
        ticks: tuple[Ticks, Ticks, Ticks],
        cfg: FillsConfig,
        rth: tuple[Instant, Instant],
    ) -> None:
        self.bar = bar
        self.open, self.high, self.low = ticks
        self.cfg = cfg
        self.rth = rth
        self.orders = dict(book.orders)
        self.brackets = dict(book.brackets)
        self.trades = dict(book.trades)
        self.live: dict[str, Order] = {}
        self.events: list[FillEvent] = []

    def run(self) -> None:
        self._resolve()
        self._carry_open_trades()
        # Market, then stops, then limit entries, then targets (design §13).
        for phase in range(4):
            for cid, order in list(self.live.items()):
                if cid not in self.live:
                    continue  # filled or cancelled earlier in this bar
                if phase == 0 and order.kind == "market":
                    self._market(cid, order)
                elif phase == 1 and order.kind == "stop":
                    self._stop(cid, order)
                elif phase == 2 and order.kind == "limit" and order.role == "entry":
                    self._limit_entry(cid, order)
                elif phase == 3 and order.kind == "limit" and order.role != "entry":
                    self._target(cid, order)
        for key, trade in list(self.trades.items()):
            if trade.instrument == self.bar.instrument:
                self.trades[key] = replace(trade, last_close_ns=self.bar.close_ns)

    def _resolve(self) -> None:
        """Pick each order's version for this bar; drop cancellations now in effect."""
        cancelled_entries: list[SetupKey | None] = []
        for cid, working in list(self.orders.items()):
            if working.placed.instrument != self.bar.instrument:
                continue
            found = working.effective(self.bar.open_ns)
            if found is None:
                continue
            index, version = found
            if version is None:
                del self.orders[cid]
                if working.placed.role == "entry":
                    cancelled_entries.append(working.placed.setup_key)
                continue
            if index:
                self.orders[cid] = WorkingOrder(working.versions[index:])
            self.live[cid] = version
        for key in cancelled_entries:
            if key is not None and key not in self.trades:
                self._drop_setup(key)

    def _carry_open_trades(self) -> None:
        """Count missing RTH bars since the last bar and widen the excursions (Req 13.12)."""
        interval_ns = self.bar.interval_s * NS_PER_SECOND
        for key, trade in list(self.trades.items()):
            if trade.instrument != self.bar.instrument:
                continue
            missing = _missing_slots(trade.last_close_ns, self.bar.open_ns, interval_ns, self.rth)
            if missing:
                trade = replace(trade, missing_bars=trade.missing_bars + missing)
            self.trades[key] = _extend(trade, self.high, self.low)

    def _drop_setup(self, key: SetupKey) -> None:
        """Remove a Setup_Key with its trade and every order left (OCO)."""
        self.trades.pop(key, None)
        self.brackets.pop(key, None)
        for cid in [c for c, w in self.orders.items() if w.placed.setup_key == key]:
            del self.orders[cid]
            self.live.pop(cid, None)

    def _live_leg(self, key: SetupKey | None, role: OrderRole) -> tuple[str, Order] | None:
        for cid, order in self.live.items():
            if order.setup_key == key and order.role == role:
                return cid, order
        return None

    def _touched(self, order: Order) -> bool:
        price = _price(order)
        return self.high >= price if order.side == "buy" else self.low <= price

    def _traded_through(self, order: Order) -> bool:
        price = _price(order)
        through = self.cfg.trade_through_ticks
        if order.side == "buy":
            return self.low <= price - through
        return self.high >= price + through

    def _stop_price(self, order: Order) -> Ticks:
        price = _price(order)
        slip = self.cfg.slippage_ticks
        if order.side == "buy":
            return max(price, self.open) + slip
        return min(price, self.open) - slip

    # ------------------------------------------------------------ phases

    def _market(self, cid: str, order: Order) -> None:
        trade = self.trades.get(order.setup_key) if order.setup_key is not None else None
        if trade is None:
            return
        slip = self.cfg.slippage_ticks
        price = self.open + slip if order.side == "buy" else self.open - slip
        self._exit(cid, order, price, min(order.qty, trade.open_qty))

    def _stop(self, cid: str, order: Order) -> None:
        if order.role == "entry":
            if self._touched(order):
                self._enter(cid, order, self._stop_price(order))
            return
        trade = self.trades.get(order.setup_key) if order.setup_key is not None else None
        if trade is not None and self._touched(order):
            self._exit(cid, order, self._stop_price(order), trade.open_qty)

    def _limit_entry(self, cid: str, order: Order) -> None:
        if self._traded_through(order):
            self._enter(cid, order, _price(order))

    def _target(self, cid: str, order: Order) -> None:
        trade = self.trades.get(order.setup_key) if order.setup_key is not None else None
        if trade is None or trade.entry_fill.bar_open_ns >= self.bar.open_ns:
            return
        if self._traded_through(order):
            self._exit(cid, order, _price(order), min(order.qty, trade.open_qty))

    # ------------------------------------------------------------ fills

    def _enter(self, cid: str, order: Order, price: Ticks) -> None:
        key = order.setup_key
        assert key is not None  # submit_bracket requires it
        bracket = self.brackets[key]
        stop = self._live_leg(key, "stop")
        if stop is None:
            raise ValueError(f"entry {cid!r} filled with no working stop for Setup_Key {key}")
        initial_stop = _price(stop[1])
        if price == initial_stop:
            raise ValueError(f"entry {cid!r} filled at its stop price {price}: R would be 0")
        fees = _fees(self.cfg, order.instrument, order.qty)
        fill = Fill(cid, self.bar.open_ns, price, order.qty, fees)
        del self.orders[cid]
        del self.live[cid]
        trade = OpenTrade(
            setup_key=key,
            instrument=order.instrument,
            direction=bracket.direction,
            entry_fill=fill,
            initial_stop=initial_stop,
            qty_at_entry=order.qty,
            open_qty=order.qty,
            last_close_ns=self.bar.close_ns,
            fees=fees,
            shadow=bracket.shadow,
        )
        self.trades[key] = _extend(trade, self.high, self.low)
        self.events.append(FillEvent(fill, order, _ZERO, None))
        self._stop(*stop)  # the stop is checked on the entry fill bar (Req 13.5)

    def _exit(self, cid: str, order: Order, price: Ticks, qty: int) -> None:
        key = order.setup_key
        assert key is not None  # only orders of an open trade exit
        trade, fill, gross = _apply_exit(
            self.trades[key], cid, self.bar.open_ns, price, qty, order.role, self.cfg
        )
        self.orders.pop(cid, None)
        self.live.pop(cid, None)
        closed = None
        if trade.open_qty == 0:
            closed = _closed_trade(trade)
            self._drop_setup(key)
        else:
            self.trades[key] = trade
        self.events.append(FillEvent(fill, order, gross, closed))


def _bar_ticks(bar: Bar) -> tuple[Ticks, Ticks, Ticks]:
    o, h, low, c = bar.o_t, bar.h_t, bar.l_t, bar.c_t
    if o is None or h is None or low is None or c is None:
        raise ValueError(f"bar of {bar.instrument!r} at {bar.open_ns} has no tick prices")
    if not low <= min(o, c) <= max(o, c) <= h:
        raise ValueError(f"bar of {bar.instrument!r} at {bar.open_ns} has low/high outside o/c")
    return o, h, low


def on_bar(
    book: SimBook, bar: Bar, cfg: FillsConfig, *, rth: tuple[Instant, Instant]
) -> tuple[SimBook, list[FillEvent]]:
    """Fill the book's orders on one futures bar (design §13).

    ``rth`` is the RTH ``(open, close)`` of the bar's session: bar slots
    missing inside it while a trade is open are counted on the trade
    (Req 13.12). Returns the new book and the fills in the order they happened.
    """
    ticks = _bar_ticks(bar)
    _require_int("rth open", rth[0])
    _require_int("rth close", rth[1])
    if rth[0] >= rth[1]:
        raise ValueError(f"rth open {rth[0]} must be before rth close {rth[1]}")
    last = book.last_bar_open.get(bar.instrument)
    if last is not None and bar.open_ns <= last:
        raise ValueError(
            f"bar of {bar.instrument!r} at {bar.open_ns} is not after the last one at {last}"
        )
    last_bar_open = {**book.last_bar_open, bar.instrument: bar.open_ns}
    busy = any(w.placed.instrument == bar.instrument for w in book.orders.values()) or any(
        t.instrument == bar.instrument for t in book.trades.values()
    )
    if not busy:
        return replace(book, last_bar_open=last_bar_open), []
    run = _BarRun(book, bar, ticks, cfg, rth)
    run.run()
    return SimBook(run.orders, run.brackets, run.trades, last_bar_open), run.events


def close_all(
    book: SimBook,
    bar_open_ns: Instant,
    prices: Mapping[str, Ticks],
    cfg: FillsConfig,
    *,
    reason: str,
) -> tuple[SimBook, list[FillEvent]]:
    """Close every open trade at ``prices[instrument]`` and cancel every working order.

    For account liquidations and the Flat_Deadline close (Req 15.8-15.16): the
    caller picks the prices, and no slippage is added. Each closing fill is a
    market exit with client id ``"{reason}:{entry client id}"``, stamped with
    ``bar_open_ns``, and pays the usual fees (Req 13.7).
    """
    if not reason.strip():
        raise ValueError("close_all needs a non-blank reason")
    _require_int("bar_open_ns", bar_open_ns)
    events: list[FillEvent] = []
    for key, trade in book.trades.items():
        price = prices.get(trade.instrument)
        if price is None:
            raise ValueError(f"close_all has no price for open {trade.instrument!r} contracts")
        _require_int(f"price of {trade.instrument}", price)
        cid = f"{reason}:{trade.entry_fill.client_id}"
        order = Order(
            client_id=cid,
            setup_key=key,
            instrument=trade.instrument,
            side=_exit_side(trade.direction),
            kind="market",
            qty=trade.open_qty,
            price=None,
            placed_at=bar_open_ns,
            role="exit",
        )
        widened = _extend(trade, price, price)
        closed, fill, gross = _apply_exit(
            widened, cid, bar_open_ns, price, trade.open_qty, "exit", cfg
        )
        events.append(FillEvent(fill, order, gross, _closed_trade(closed)))
    return SimBook(last_bar_open=dict(book.last_bar_open)), events
