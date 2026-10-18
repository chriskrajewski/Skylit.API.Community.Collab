"""The Paper_Broker: an in-process broker on the Fill_Simulator rules (design §23, Req 23.15).

The Backtester and the Live_Runner's Paper Order_Mode route orders to the
same :class:`PaperBroker`, so a recorded paper session replays to the same
fills (Req 23.9). It holds:

- a :class:`~fse.sim.fills.SimBook`: working orders, brackets and open trades,
  filled by :func:`fse.sim.fills.on_bar` (Req 13);
- the :class:`~fse.sim.account.AccountSim`: every fill is booked, every
  entry is checked first (position cap, DLL and Flat_Deadline blocks, the end
  of the attempt), and each bar is checked for the loss limits (Req 15);
- the last bar seen per instrument, to value an open trade on an interval
  without a bar of its instrument.

**Routing** (:meth:`route`). ``PlaceBracket`` joins the book once the account
accepts its entry; a refusal becomes an
:class:`~fse.engine.step.EntryRejected` for the next ``Engine.step``.
``ModifyOrder``, ``CancelOrder`` and ``SubmitExit`` change the book; one that
refers to an order or trade the book no longer holds (it filled, or an
account rule closed it) changes nothing, so an exit sent after the position
closed can never open a reverse position. Every order fills only on bars
that open at or after its placement or change (Req 5.5-5.6).

**Closes** (:meth:`close`). An account rule that fires (a loss-limit
liquidation, the Flat_Deadline) closes every open trade at the given price
per (instrument, direction) and cancels every working order.

The Paper_Broker never calls a network service. :meth:`snapshot` and
:meth:`restore` let a restarted Live_Runner continue with the same book and
account.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from fse.config.schema.fills import FillsConfig
from fse.engine.planner import CancelOrder, ModifyOrder, OrderIntent, PlaceBracket
from fse.engine.step import EntryRejected
from fse.engine.types import Bar, Direction, Money, Order, SetupKey, Ticks
from fse.sim.account import AccountEvent, AccountRejection, AccountSim, AccountSnapshot
from fse.sim.fills import FillEvent, OpenTrade, SimBook, close_all
from fse.sim.fills import on_bar as fill_bar
from fse.timekit import Instant

__all__ = ["PaperBroker", "PaperBrokerSnapshot", "PricedClose"]

type PricedClose = Mapping[tuple[str, Direction], tuple[Bar, Ticks]]
"""Per (instrument, direction) with open trades: the bar the closing fills are stamped with
and the closing price."""

_ZERO: Final = Decimal("0.00")


@dataclass(frozen=True, slots=True)
class PaperBrokerSnapshot:
    """A :class:`PaperBroker`'s book, account and last bars, frozen."""

    book: SimBook
    account: AccountSnapshot
    last_bars: tuple[tuple[str, Bar], ...]


class PaperBroker:
    """Fills the Order_Planner's orders with the Fill_Simulator (see the module notes)."""

    __slots__ = ("_account", "_book", "_fills", "_last_bar")

    def __init__(
        self,
        fills: FillsConfig,
        account: AccountSim,
        *,
        book: SimBook | None = None,
        last_bars: Mapping[str, Bar] | None = None,
    ) -> None:
        self._fills = fills
        self._account = account
        self._book = SimBook() if book is None else book
        self._last_bar: dict[str, Bar] = {} if last_bars is None else dict(last_bars)

    @property
    def account(self) -> AccountSim:
        return self._account

    @property
    def book(self) -> SimBook:
        return self._book

    @property
    def trades(self) -> Mapping[SetupKey, OpenTrade]:
        """The open trades, by Setup_Key."""
        return self._book.trades

    def working_orders(self) -> tuple[Order, ...]:
        return self._book.working_orders()

    def positions(self) -> dict[str, int]:
        """Net open contracts per instrument (positive long)."""
        return self._book.positions()

    def last_bar(self, instrument: str) -> Bar | None:
        return self._last_bar.get(instrument)

    # ------------------------------------------------------------ orders

    def route(self, intents: Iterable[OrderIntent]) -> list[EntryRejected]:
        """Apply the Order_Planner's intents in order; the entries the account refused."""
        rejected: list[EntryRejected] = []
        for intent in intents:
            book = self._book
            if isinstance(intent, PlaceBracket):
                entry = intent.entry
                working = [o for o in book.working_orders() if o.role == "entry"]
                checked = self._account.check_order(entry, working)
                if isinstance(checked, AccountRejection):
                    assert entry.setup_key is not None  # planner entries carry their key
                    rejected.append(
                        EntryRejected(entry.setup_key, entry.placed_at, checked.message)
                    )
                    continue
                self._book = book.submit_bracket(entry, intent.stop, intent.targets)
            elif isinstance(intent, ModifyOrder):
                if book.order(intent.client_id) is not None:
                    self._book = book.modify(intent.client_id, intent.price, intent.at)
            elif isinstance(intent, CancelOrder):
                if intent.client_id in book.orders:
                    self._book = book.cancel(intent.client_id, intent.at)
            else:
                order = intent.order
                if order.setup_key in book.trades and order.client_id not in book.orders:
                    self._book = book.submit_exit(order)
        return rejected

    # ------------------------------------------------------------ bars

    def fill(
        self, bars: Sequence[Bar], rth: tuple[Instant, Instant]
    ) -> list[tuple[Bar, FillEvent]]:
        """Fill working orders on one interval's bars; the account books each fill."""
        out: list[tuple[Bar, FillEvent]] = []
        for bar in bars:
            self._book, events = fill_bar(self._book, bar, self._fills, rth=rth)
            for event in events:
                self._account.on_fill(event.fill, event.order)
                out.append((bar, event))
        return out

    def check_account(self, bars: Mapping[str, Bar]) -> list[AccountEvent]:
        """The Maximum and Daily Loss Limit checks on one interval's bars (Req 15.8-15.12)."""
        return self._account.on_bar(bars)

    def note_bars(self, bars: Iterable[Bar]) -> None:
        """Remember each instrument's latest bar, to value trades on intervals without one."""
        for bar in bars:
            self._last_bar[bar.instrument] = bar

    def unrealized(self, bars: Mapping[str, Bar]) -> Money:
        """The open trades at their worst price on ``bars`` (the last close without a bar)."""
        total = _ZERO
        for trade in self._book.trades.values():
            bar = bars.get(trade.instrument)
            if bar is not None:
                price = trade.worst_price(bar)
            else:
                last = self._last_bar.get(trade.instrument)
                if last is None or last.c_t is None:
                    raise ValueError(f"no {trade.instrument} bar to value an open trade")
                price = last.c_t
            total += trade.unrealized(price)
        return total

    def liquidation_prices(
        self, bars: Mapping[str, Bar]
    ) -> dict[tuple[str, Direction], tuple[Bar, Ticks]]:
        """Each open trade at its worst price on the breach bar (its last bar without one)."""
        priced: dict[tuple[str, Direction], tuple[Bar, Ticks]] = {}
        for trade in self._book.trades.values():
            bar = bars.get(trade.instrument) or self._last_bar[trade.instrument]
            priced[(trade.instrument, trade.direction)] = (bar, trade.worst_price(bar))
        return priced

    def close(self, priced: PricedClose, reason: str) -> list[tuple[Bar, FillEvent]]:
        """Close every open trade at its ``priced`` price, then cancel every working order."""
        out: list[tuple[Bar, FillEvent]] = []
        book = self._book
        for (instrument, direction), (bar, price) in sorted(priced.items()):
            keys = [
                k
                for k, t in book.trades.items()
                if (t.instrument, t.direction) == (instrument, direction)
            ]
            part = SimBook(
                brackets={k: book.brackets[k] for k in keys},
                trades={k: book.trades[k] for k in keys},
                last_bar_open=dict(book.last_bar_open),
            )
            _, events = close_all(
                part, bar.open_ns, {instrument: price}, self._fills, reason=reason
            )
            out.extend((bar, e) for e in events)
        self._book = SimBook(last_bar_open=dict(book.last_bar_open))
        return out

    # ------------------------------------------------------------ snapshots

    def snapshot(self) -> PaperBrokerSnapshot:
        return PaperBrokerSnapshot(
            self._book, self._account.snapshot(), tuple(self._last_bar.items())
        )

    @classmethod
    def restore(
        cls, fills: FillsConfig, account: AccountSim, snap: PaperBrokerSnapshot
    ) -> PaperBroker:
        """A broker holding ``snap``'s book and last bars; ``account`` is already restored."""
        return cls(fills, account, book=snap.book, last_bars=dict(snap.last_bars))

    def __repr__(self) -> str:
        return (
            f"PaperBroker({len(self._book.orders)} working orders, "
            f"{len(self._book.trades)} open trades)"
        )
