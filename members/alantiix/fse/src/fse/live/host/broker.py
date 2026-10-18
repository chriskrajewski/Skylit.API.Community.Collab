"""``PluginPaperBroker``: one host-brokered plugin's paper book (design 7.2-7.4).

Each host-brokered plugin owns one broker over an immutable
:class:`~fse.sim.fills.SimBook`; nothing is shared between plugins. Every
order gets ``client_id = f"{plugin_id}:{tag}:{leg}"`` (``leg`` in ``entry``,
``stop``, ``tp1``, ``tp2``, ``exit``) and ``SetupKey(instrument,
pattern=f"{plugin_id}:{tag}", source_strike=0.0, direction, session=<Globex
day>, tap_seq=1)``, so a fill maps back to its tag through the Setup_Key.

- **Routing** (:meth:`PluginPaperBroker.route`): entries are checked in this
  order: ``bad_tag`` (``TAG_RE``), ``duplicate_tag`` (working, open, or used
  this Globex day), ``instrument_not_declared``, ``no_globex_day``, for
  ``EnterMarket`` ``stale_placement`` (``placed_at`` outside ``[now - 60 s,
  now]`` or not before every bar the book processed) and ``bad_freeze``
  (``freeze_from_ns < placed_at + 60 s``), then the blocks the caller passes
  (halt file, persistent blocks, host blocks), then the caps
  (``max_daily_loss`` — day P&L halt and per-trade stop dollar risk —
  ``max_trades_per_day``, ``max_open_trades``, ``max_contracts``), then the
  paper account's ``account_check`` when the plugin is on an account, then
  the book's own validation (``invalid_order``).
- **Bars** (:meth:`PluginPaperBroker.on_bar`): ``fills.on_bar`` with the
  plugin's ``stop_fill``; a ring of the last 120 bars per instrument; setups
  whose entry carried ``freeze_from_ns`` see no bar opening at or after it
  (``post_close_bar`` warning once per tag). A market entry that leaves the
  book without a fill is ``cancelled`` with the reason when it was asked to
  cancel, else ``expired`` / ``fill_bar_missing`` with ``exact_bar_open``,
  else ``expired`` / ``reference_gap`` with ``reference_t`` (its fill bar
  gapped to a leg), else ``cancelled``.
- **Flatten**: ``last_close`` closes at the newest ring bar closing at or
  before ``stamp_ns`` (``close_setup``, no slippage; ``rejected: no_price``
  without one); ``next_open`` submits a market exit. Working entries of the
  flattened tags are cancelled.
- **ModifyStop**: a new version of ``<id>:<tag>:stop`` (``SimBook.modify``)
  effective from ``max(last bar open + 60 s, the stop's latest version)``,
  i.e. the next bar the book has not processed (``ctx.now`` would skip it:
  live bars arrive after their close); a second modify before that bar
  replaces the first. ``rejected`` (role ``stop``): ``not_open`` (no open
  trade with the tag), ``invalid_order`` (not an ``int``), ``stop_loosen``.
  The current price is an accepted no-op (``unchanged: True``). Not gated by
  blocks or caps. A stop at or through the market fills on that bar by the
  ``stop_fill`` rule (``at_stop`` fills at the stop: paper optimism). Targets
  and ``initial_stop`` (R) are unchanged.
- **Accounting**: realized net, accepted entries (``day_trades``) and closed
  trades (``day_closed``) per Globex day; the day counters and ``used_tags``
  reset at :meth:`start_day`.
- **Persistence**: :class:`PluginBrokerState`, encoded with
  :data:`BROKER_CODEC`, is the ``book`` half of ``plugin.json`` (the host
  writes it, design 12.2).
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from typing import Final, Literal

from fse.config.schema.fills import FillsConfig
from fse.engine.state import ENGINE_CODEC, StateCodec, dataclass_registry
from fse.engine.types import Bar, Direction, Money, Order, OrderRole, SetupKey, Side, Ticks
from fse.live.host.config import FillsBlock, RiskBlock
from fse.live.plugins.api import (
    BAR_INTERVAL_S,
    TAG_RE,
    Cancel,
    EnterMarket,
    EnterResting,
    Flatten,
    Intent,
    ModifyStop,
    OrderUpdate,
    Quote,
)
from fse.logio.canonical_json import JsonValue
from fse.sim import fills as fills_module
from fse.sim.fills import (
    MONEY_CONTEXT,
    FillEvent,
    SimBook,
    StopFill,
    close_setup,
    on_bar,
    tick_value,
)
from fse.timekit import NS_PER_SECOND, Instant

__all__ = [
    "BROKER_CODEC",
    "PLACEMENT_WINDOW_NS",
    "QUOTE_MAX_AGE_NS",
    "REASON_RE",
    "RING_SIZE",
    "AccountCheck",
    "PluginBrokerState",
    "PluginPaperBroker",
    "RiskCaps",
    "client_id",
    "tag_of",
]

RING_SIZE: Final = 120
PLACEMENT_WINDOW_NS: Final = 60 * NS_PER_SECOND
QUOTE_MAX_AGE_NS: Final = 5 * NS_PER_SECOND
REASON_RE: Final = re.compile(r"^[a-z_]{1,32}$")
_ZERO: Final = Decimal(0)

type Leg = Literal["entry", "stop", "tp1", "tp2", "exit"]
type AccountCheck = Callable[[str, int], str | None]
"""A paper account's entry check ``(instrument, qty) -> refusal reason or None``."""


def client_id(plugin_id: str, tag: str, leg: Leg) -> str:
    """``f"{plugin_id}:{tag}:{leg}"``."""
    return f"{plugin_id}:{tag}:{leg}"


def tag_of(key: SetupKey) -> str:
    """The tag of a Setup_Key whose pattern is ``f"{plugin_id}:{tag}"``."""
    return key.pattern.partition(":")[2]


def _side(direction: Direction) -> Side:
    return "buy" if direction == "long" else "sell"


def _exit_side(direction: Direction) -> Side:
    return "sell" if direction == "long" else "buy"


@dataclass(frozen=True, slots=True)
class RiskCaps:
    """A plugin's risk caps (design 7.4)."""

    max_contracts: int
    max_open_trades: int = 1
    max_trades_per_day: int | None = None
    max_daily_loss: Money | None = None

    @classmethod
    def from_block(cls, block: RiskBlock) -> RiskCaps:
        return cls(
            block.max_contracts,
            block.max_open_trades,
            block.max_trades_per_day,
            block.max_daily_loss,
        )


@dataclass(frozen=True, slots=True)
class PluginBrokerState:
    """The persisted broker: book, bar ring, day accounting and tag bookkeeping."""

    book: SimBook = field(default_factory=SimBook)
    ring: Mapping[str, tuple[Bar, ...]] = field(default_factory=dict)
    day: date | None = None
    day_net: Money = _ZERO
    day_trades: int = 0
    """Entries accepted this Globex day."""
    day_closed: int = 0
    """Trades closed this Globex day (an older ``plugin.json`` without it decodes as 0)."""
    used_tags: frozenset[str] = frozenset()
    freeze: Mapping[str, Instant] = field(default_factory=dict)
    """Tag -> ``freeze_from_ns`` of its entry."""
    cancels: Mapping[str, str | None] = field(default_factory=dict)
    """Tag -> reason (``None``: the plugin's own ``Cancel``) of a cancellation not yet reported."""


BROKER_CODEC: Final[StateCodec] = ENGINE_CODEC.extended(
    dataclass_registry(fills_module, sys.modules[__name__])
)
"""The engine state types plus the simulator's and this module's dataclasses."""


class PluginPaperBroker:
    """One plugin's paper book, caps and accounting; mutated only by the dispatcher."""

    __slots__ = (
        "_caps",
        "_cfg",
        "_declared",
        "_id",
        "_log",
        "_logged_post_close",
        "_rth_for",
        "_state",
        "_stop_fill",
        "_submit_quotes",
    )

    def __init__(
        self,
        plugin_id: str,
        *,
        fills: FillsConfig,
        stop_fill: StopFill,
        caps: RiskCaps,
        declared: frozenset[str],
        rth_for: Callable[[Bar], tuple[Instant, Instant]],
        log: Callable[[Mapping[str, JsonValue]], None],
        state: PluginBrokerState | None = None,
    ) -> None:
        self._id = plugin_id
        self._cfg = fills
        self._stop_fill: StopFill = stop_fill
        self._caps = caps
        self._declared = declared
        self._rth_for = rth_for
        self._log = log
        self._state = state if state is not None else PluginBrokerState()
        self._logged_post_close: set[str] = set()
        self._submit_quotes: dict[str, Ticks] = {}

    @classmethod
    def from_blocks(
        cls,
        plugin_id: str,
        fills: FillsBlock,
        risk: RiskBlock,
        *,
        declared: frozenset[str],
        rth_for: Callable[[Bar], tuple[Instant, Instant]],
        log: Callable[[Mapping[str, JsonValue]], None],
        state: PluginBrokerState | None = None,
    ) -> PluginPaperBroker:
        """A broker from the plugin's runner ``fills`` and ``risk`` blocks."""
        return cls(
            plugin_id,
            fills=fills.fills_config(),
            stop_fill=fills.stop_fill,
            caps=RiskCaps.from_block(risk),
            declared=declared,
            rth_for=rth_for,
            log=log,
            state=state,
        )

    # ------------------------------------------------------------ state and queries

    @property
    def state(self) -> PluginBrokerState:
        return self._state

    @property
    def book(self) -> SimBook:
        return self._state.book

    @property
    def caps(self) -> RiskCaps:
        return self._caps

    def restore(self, state: PluginBrokerState) -> None:
        self._state = state
        self._logged_post_close.clear()
        self._submit_quotes.clear()

    def key_of(self, tag: str) -> SetupKey | None:
        """The Setup_Key of a tag still in the book (working or open); ``None`` otherwise."""
        return self._key(tag)

    def _key(self, tag: str) -> SetupKey | None:
        for key in (*self.book.brackets, *self.book.market_brackets, *self.book.trades):
            if tag_of(key) == tag:
                return key
        for working in self.book.orders.values():
            placed = working.placed.setup_key
            if placed is not None and tag_of(placed) == tag:
                return placed
        return None

    def _keys(self) -> list[SetupKey]:
        seen: dict[SetupKey, None] = {}
        for key in (*self.book.trades, *self.book.brackets, *self.book.market_brackets):
            seen[key] = None
        for working in self.book.orders.values():
            placed = working.placed.setup_key
            if placed is not None:
                seen[placed] = None
        return list(seen)

    def position(self, instrument: str) -> int:
        return self.book.positions().get(instrument, 0)

    def open_tags(self) -> tuple[str, ...]:
        return tuple(tag_of(k) for k in self.book.trades)

    def working_tags(self) -> tuple[str, ...]:
        """Tags with a working entry not cancelled."""
        out: list[str] = []
        for working in self.book.orders.values():
            order = working.latest
            if order is not None and order.role == "entry" and order.setup_key is not None:
                tag = tag_of(order.setup_key)
                if tag not in out:
                    out.append(tag)
        return tuple(out)

    def has_working_entries(self) -> bool:
        return bool(self.working_tags())

    def has_exposure(self) -> bool:
        """An open trade or any working order."""
        return bool(self.book.trades) or bool(self.book.orders)

    def oldest_trade_session(self) -> date | None:
        """The earliest Globex day of an open trade (its Setup_Key session)."""
        days = [k.session for k in self.book.trades]
        return min(days) if days else None

    def exit_fee_per_contract(self, instrument: str) -> Money | None:
        """What one contract pays per fill (commission + exchange fee); ``None`` unset."""
        costs = self._cfg.costs_for(instrument)
        return None if costs is None else costs.per_contract

    def cap_blocks(self) -> tuple[str, ...]:
        """Caps that refuse every entry now (daily loss, trades per day)."""
        out: list[str] = []
        s, caps = self._state, self._caps
        if caps.max_daily_loss is not None and s.day_net <= -caps.max_daily_loss:
            out.append("max_daily_loss")
        if caps.max_trades_per_day is not None and s.day_trades >= caps.max_trades_per_day:
            out.append("max_trades_per_day")
        return tuple(out)

    # ------------------------------------------------------------ day

    def start_day(self, day: date | None) -> None:
        """Reset ``used_tags`` and the day counters for Globex day ``day``."""
        self._state = replace(
            self._state,
            day=day,
            day_net=_ZERO,
            day_trades=0,
            day_closed=0,
            used_tags=frozenset(),
        )

    def reset_day_net(self) -> bool:
        """Zero realized day net only (operator reset of today's max-loss budget).

        Leaves ``day``, ``day_trades``, ``day_closed``, ``used_tags``, the book and
        ``max_daily_loss`` unchanged. Returns whether ``day_net`` changed.
        """
        if self._state.day_net == _ZERO:
            return False
        self._state = replace(self._state, day_net=_ZERO)
        return True

    # ------------------------------------------------------------ bars

    def on_bar(self, bar: Bar) -> list[OrderUpdate]:
        """Fill the book on ``bar`` and report what happened, in fill order."""
        s = self._state
        ring = dict(s.ring)
        kept = (*ring.get(bar.instrument, ()), bar)
        ring[bar.instrument] = kept[-RING_SIZE:]
        before = s.book
        frozen = self._frozen_keys(bar)
        if frozen:
            view, held = _split(before, frozen)
            after_view, events = on_bar(
                view, bar, self._cfg, rth=self._rth_for(bar), stop_fill=self._stop_fill
            )
            after = _merge(after_view, held)
            for key in frozen:
                tag = tag_of(key)
                if tag not in self._logged_post_close:
                    self._logged_post_close.add(tag)
                    self._log(
                        {
                            "kind": "post_close_bar",
                            "level": "warning",
                            "tag": tag,
                            "bar_open_ns": bar.open_ns,
                            "freeze_from_ns": s.freeze.get(tag),
                        }
                    )
        else:
            after, events = on_bar(
                before, bar, self._cfg, rth=self._rth_for(bar), stop_fill=self._stop_fill
            )
        self._state = replace(s, ring=ring)
        return self._apply(before, after, events, bar, bar.close_ns)

    def _frozen_keys(self, bar: Bar) -> set[SetupKey]:
        out: set[SetupKey] = set()
        freeze = self._state.freeze
        if not freeze:
            return out
        for key in self._keys():
            f = freeze.get(tag_of(key))
            if f is not None and key.instrument == bar.instrument and bar.open_ns >= f:
                out.add(key)
        return out

    def _apply(
        self,
        before: SimBook,
        after: SimBook,
        events: Sequence[FillEvent],
        bar: Bar | None,
        at: Instant,
    ) -> list[OrderUpdate]:
        """Store ``after`` and turn the change from ``before`` into updates."""
        s = self._state
        out: list[OrderUpdate] = []
        day_net = s.day_net
        day_closed = s.day_closed
        filled_entries: set[str] = set()
        for ev in events:
            key = ev.order.setup_key
            if key is None:
                continue
            tag = tag_of(key)
            if ev.order.role == "entry":
                filled_entries.add(tag)
                detail: dict[str, JsonValue] | None = None
                quote = self._submit_quotes.pop(tag, None)
                if quote is not None:
                    detail = {"submit_quote_t": quote, "fill_minus_quote_t": ev.fill.price - quote}
                out.append(
                    OrderUpdate(tag, "filled_entry", None, ev.fill, "entry", None, bar, at, detail)
                )
                continue
            out.append(OrderUpdate(tag, "filled_exit", None, ev.fill, ev.order.role, None, bar, at))
            if ev.closed is not None:
                day_net = MONEY_CONTEXT.add(day_net, ev.closed.net)
                day_closed += 1
                out.append(
                    OrderUpdate(tag, "closed", None, ev.fill, ev.order.role, ev.closed, bar, at)
                )
        cancels = dict(s.cancels)
        for tag, key, exact, reference in _working_entries(before):
            if tag in filled_entries or _holds_entry(after, key) or key in after.trades:
                continue
            if tag in cancels:
                reason = cancels.pop(tag)
                out.append(OrderUpdate(tag, "cancelled", reason, None, "entry", None, bar, at))
            elif exact:
                out.append(
                    OrderUpdate(tag, "expired", "fill_bar_missing", None, "entry", None, bar, at)
                )
            elif reference:
                out.append(
                    OrderUpdate(tag, "expired", "reference_gap", None, "entry", None, bar, at)
                )
            else:
                out.append(OrderUpdate(tag, "cancelled", None, None, "entry", None, bar, at))
            self._submit_quotes.pop(tag, None)
        live_tags = {tag_of(k) for k in _all_keys(after)}
        cancels = {t: r for t, r in cancels.items() if t in live_tags and _entry_live(after, t)}
        freeze = {t: f for t, f in s.freeze.items() if t in live_tags}
        self._state = replace(
            s,
            book=after,
            day_net=day_net,
            day_closed=day_closed,
            cancels=cancels,
            freeze=freeze,
        )
        return out

    # ------------------------------------------------------------ routing

    def route(
        self,
        intent: Intent,
        now: Instant,
        *,
        blocks: Sequence[str] = (),
        quote: Quote | None = None,
        account_check: AccountCheck | None = None,
    ) -> list[OrderUpdate]:
        """Apply one intent at ``now``; ``blocks`` are the gate's and host's reasons in force.

        ``account_check(instrument, qty)`` is the paper account's entry check,
        run after the plugin's own caps pass; ``None`` (no account) skips it.
        """
        if isinstance(intent, EnterMarket):
            return self._enter_market(intent, now, blocks, quote, account_check)
        if isinstance(intent, EnterResting):
            return self._enter_resting(intent, now, blocks, account_check)
        if isinstance(intent, Flatten):
            return self.flatten(intent, now)
        if isinstance(intent, Cancel):
            return self._cancel_tag(intent.tag, now, None)
        if isinstance(intent, ModifyStop):
            return self._modify_stop(intent, now)
        raise TypeError(f"not an intent: {type(intent).__name__}")

    def _reject(
        self, tag: str, reason: str, now: Instant, role: OrderRole = "entry"
    ) -> list[OrderUpdate]:
        return [OrderUpdate(tag, "rejected", reason, None, role, None, None, now)]

    def _modify_stop(self, intent: ModifyStop, now: Instant) -> list[OrderUpdate]:
        """Tighten the open trade's stop from the next unprocessed bar's open on."""
        tag = intent.tag
        key = next((k for k in self.book.trades if tag_of(k) == tag), None)
        if key is None:
            return self._reject(tag, "not_open", now, "stop")
        price = intent.stop_price
        if isinstance(price, bool) or not isinstance(price, int):
            return [
                OrderUpdate(
                    tag,
                    "rejected",
                    "invalid_order",
                    None,
                    "stop",
                    None,
                    None,
                    now,
                    {"error": f"stop_price must be an int, got {type(price).__name__}"},
                )
            ]
        trade = self.book.trades[key]
        cid = client_id(self._id, tag, "stop")
        working = self.book.orders.get(cid)
        current = working.latest if working is not None else None
        if working is None or current is None or current.price is None:
            return self._reject(tag, "not_open", now, "stop")
        previous = current.price
        if price == previous:
            detail: dict[str, JsonValue] = {
                "stop_price": price,
                "previous_stop": previous,
                "effective_ns": None,
                "unchanged": True,
            }
            return [OrderUpdate(tag, "accepted", None, None, "stop", None, None, now, detail)]
        if trade.sign * (price - previous) < 0:
            return self._reject(tag, "stop_loosen", now, "stop")
        interval = BAR_INTERVAL_S * NS_PER_SECOND
        last = self.book.last_bar_open.get(trade.instrument, now - interval)
        at = max(last + interval, working.versions[-1][0])
        try:
            book = self.book.modify(cid, price, at)
        except (ValueError, TypeError) as exc:
            return [
                OrderUpdate(
                    tag,
                    "rejected",
                    "invalid_order",
                    None,
                    "stop",
                    None,
                    None,
                    now,
                    {"error": str(exc)},
                )
            ]
        self._state = replace(self._state, book=book)
        detail = {
            "stop_price": price,
            "previous_stop": previous,
            "effective_ns": at,
            "unchanged": False,
        }
        return [OrderUpdate(tag, "accepted", None, None, "stop", None, None, now, detail)]

    def _entry_checks(self, tag: str, instrument: str) -> str | None:
        if not isinstance(tag, str) or TAG_RE.fullmatch(tag) is None:
            return "bad_tag"
        s = self._state
        if tag in s.used_tags or self._key(tag) is not None:
            return "duplicate_tag"
        if instrument not in self._declared:
            return "instrument_not_declared"
        if s.day is None:
            return "no_globex_day"
        return None

    def _cap_check(
        self,
        qty: int,
        blocks: Sequence[str],
        instrument: str,
        account_check: AccountCheck | None,
        *,
        stop_ticks: int | None = None,
    ) -> str | None:
        reason = self._plugin_caps(qty, blocks, instrument=instrument, stop_ticks=stop_ticks)
        if reason is None and account_check is not None:
            return account_check(instrument, qty)
        return reason

    def _stop_risk_dollars(self, instrument: str, stop_ticks: int, qty: int) -> Money | None:
        """``stop_ticks * tick_value * qty``; ``None`` when the instrument has no tick value."""
        try:
            tv = tick_value(instrument)
        except ValueError:
            return None
        return MONEY_CONTEXT.multiply(MONEY_CONTEXT.multiply(tv, stop_ticks), qty)

    def _plugin_caps(
        self,
        qty: int,
        blocks: Sequence[str],
        *,
        instrument: str | None = None,
        stop_ticks: int | None = None,
    ) -> str | None:
        if blocks:
            return blocks[0]
        caps_now = self.cap_blocks()
        if caps_now:
            return caps_now[0]
        book = self.book
        pending = len(book.market_brackets) + sum(1 for k in book.brackets if k not in book.trades)
        if len(book.trades) + pending + 1 > self._caps.max_open_trades:
            return "max_open_trades"
        exposure = sum(abs(q) for q in book.positions().values())
        exposure += sum(book.working_entry_contracts().values())
        if exposure + qty > self._caps.max_contracts:
            return "max_contracts"
        loss_cap = self._caps.max_daily_loss
        if (
            loss_cap is not None
            and instrument is not None
            and stop_ticks is not None
            and stop_ticks > 0
            and qty > 0
        ):
            remaining = MONEY_CONTEXT.add(loss_cap, self._state.day_net)
            risk = self._stop_risk_dollars(instrument, stop_ticks, qty)
            if risk is not None and risk > remaining:
                return "max_daily_loss"
        return None

    def _setup_key(self, tag: str, instrument: str, direction: Direction) -> SetupKey:
        day = self._state.day
        assert day is not None  # checked by _entry_checks
        return SetupKey(instrument, f"{self._id}:{tag}", 0.0, direction, day, 1)

    def _accept(
        self,
        tag: str,
        book: SimBook,
        now: Instant,
        side: Side,
        instrument: str,
        quote: Quote | None,
        freeze: Instant | None = None,
    ) -> list[OrderUpdate]:
        s = self._state
        freezes = dict(s.freeze)
        if freeze is not None:
            freezes[tag] = freeze
        self._state = replace(
            s,
            book=book,
            used_tags=s.used_tags | {tag},
            day_trades=s.day_trades + 1,
            freeze=freezes,
        )
        detail: dict[str, JsonValue] = {"submit_quote_t": None}
        if quote is not None and quote.instrument == instrument:
            price = quote.ask_t if side == "buy" else quote.bid_t
            age = now - quote.received_ns
            if price is not None and 0 <= age <= QUOTE_MAX_AGE_NS:
                detail = {"submit_quote_t": price, "quote_age_ms": age // 1_000_000}
                self._submit_quotes[tag] = price
        return [OrderUpdate(tag, "accepted", None, None, "entry", None, None, now, detail)]

    def _enter_market(
        self,
        intent: EnterMarket,
        now: Instant,
        blocks: Sequence[str],
        quote: Quote | None,
        account_check: AccountCheck | None = None,
    ) -> list[OrderUpdate]:
        tag = intent.tag
        reason = self._entry_checks(tag, intent.instrument)
        if reason is None:
            last = self.book.last_bar_open.get(intent.instrument)
            if not now - PLACEMENT_WINDOW_NS <= intent.placed_at <= now or (
                last is not None and last >= intent.placed_at
            ):
                reason = "stale_placement"
            elif (
                intent.freeze_from_ns is not None
                and intent.freeze_from_ns < intent.placed_at + PLACEMENT_WINDOW_NS
            ):
                reason = "bad_freeze"
        if reason is None:
            reason = self._cap_check(
                intent.qty,
                blocks,
                intent.instrument,
                account_check,
                stop_ticks=intent.stop_ticks,
            )
        if reason is not None:
            return self._reject(tag, reason, now)
        side = _side(intent.direction)
        try:
            key = self._setup_key(tag, intent.instrument, intent.direction)
            entry = Order(
                client_id(self._id, tag, "entry"),
                key,
                intent.instrument,
                side,
                "market",
                intent.qty,
                None,
                intent.placed_at,
                "entry",
            )
            book = self.book.submit_market_bracket(
                entry,
                stop_id=client_id(self._id, tag, "stop"),
                stop_ticks=intent.stop_ticks,
                targets=[
                    (leg.role, client_id(self._id, tag, leg.role), leg.qty, leg.ticks)
                    for leg in intent.targets
                ],
                exact_bar_open=intent.exact_bar_open,
                reference_t=intent.reference_t,
            )
        except (ValueError, TypeError) as exc:
            return self._invalid(tag, exc, now)
        return self._accept(tag, book, now, side, intent.instrument, quote, intent.freeze_from_ns)

    def _enter_resting(
        self,
        intent: EnterResting,
        now: Instant,
        blocks: Sequence[str],
        account_check: AccountCheck | None = None,
    ) -> list[OrderUpdate]:
        tag = intent.tag
        reason = self._entry_checks(tag, intent.instrument)
        if reason is None:
            reason = self._cap_check(intent.qty, blocks, intent.instrument, account_check)
        if reason is not None:
            return self._reject(tag, reason, now)
        side = _side(intent.direction)
        exit_side = _exit_side(intent.direction)
        touch = intent.setup_through
        last_open = self.book.last_bar_open.get(intent.instrument)
        if touch and (last_open is None or last_open >= now):
            touch = False
        placed_at = last_open if touch and last_open is not None else now
        try:
            key = self._setup_key(tag, intent.instrument, intent.direction)
            entry = Order(
                client_id(self._id, tag, "entry"),
                key,
                intent.instrument,
                side,
                intent.kind,
                intent.qty,
                intent.price,
                placed_at,
                "entry",
            )
            stop = Order(
                client_id(self._id, tag, "stop"),
                key,
                intent.instrument,
                exit_side,
                "stop",
                intent.qty,
                intent.stop,
                placed_at,
                "stop",
            )
            targets = [
                Order(
                    client_id(self._id, tag, role),
                    key,
                    intent.instrument,
                    exit_side,
                    "limit",
                    qty,
                    price,
                    placed_at,
                    role,
                )
                for role, qty, price in intent.targets
            ]
            book = self.book.submit_bracket(entry, stop, targets)
        except (ValueError, TypeError) as exc:
            return self._invalid(tag, exc, now)
        accepted = self._accept(tag, book, now, side, intent.instrument, None)
        if not touch or last_open is None:
            return accepted
        return accepted + self._fill_setup_touch(intent, last_open)

    def _fill_setup_touch(self, intent: EnterResting, last_open: Instant) -> list[OrderUpdate]:
        """Fill a pullback the setup bar already traded to.

        The order is effective from the bar the host just processed. A bar
        opening 1 ns later trades through the limit and is still before the
        next real minute, so that minute is not skipped.
        """
        price = intent.price
        through = self._cfg.trade_through_ticks
        if intent.direction == "long":
            low_t, high_t = price - through, price
        else:
            low_t, high_t = price, price + through
        open_ns = last_open + 1
        ring = self._state.ring.get(intent.instrument, ())
        contract = ring[-1].contract if ring else intent.instrument
        bar = Bar(
            instrument=intent.instrument,
            contract=contract,
            interval_s=BAR_INTERVAL_S,
            open_ns=open_ns,
            close_ns=open_ns + BAR_INTERVAL_S * NS_PER_SECOND,
            o=price / 4,
            h=high_t / 4,
            l=low_t / 4,
            c=price / 4,
            v=1.0,
            o_t=price,
            h_t=high_t,
            l_t=low_t,
            c_t=price,
            source="projectx",
        )
        return self.on_bar(bar)

    def _invalid(self, tag: str, exc: Exception, now: Instant) -> list[OrderUpdate]:
        return [
            OrderUpdate(
                tag,
                "rejected",
                "invalid_order",
                None,
                "entry",
                None,
                None,
                now,
                {"error": str(exc)},
            )
        ]

    # ------------------------------------------------------------ cancel and flatten

    def _cancel_tag(self, tag: str, now: Instant, reason: str | None) -> list[OrderUpdate]:
        """Cancel ``tag``'s working entry (from bars opening at ``now`` on)."""
        cid = client_id(self._id, tag, "entry")
        working = self.book.orders.get(cid)
        if working is None or working.latest is None:
            return []
        before = self.book
        at = max(now, working.versions[-1][0])
        after = before.cancel(cid, at)
        cancels = dict(self._state.cancels)
        cancels.setdefault(tag, reason)
        self._state = replace(self._state, cancels=cancels)
        return self._apply(before, after, [], None, now)

    def cancel_entries(self, now: Instant, reason: str) -> list[OrderUpdate]:
        """Cancel every working entry (a block is in force); stops and targets stay."""
        out: list[OrderUpdate] = []
        for tag in self.working_tags():
            out.extend(self._cancel_tag(tag, now, reason))
        return out

    def flatten(self, intent: Flatten, now: Instant) -> list[OrderUpdate]:
        """Close ``intent.tag`` (or every tag) at the last close or the next open."""
        if REASON_RE.fullmatch(intent.reason) is None:
            return self._reject(intent.tag or "*", "bad_reason", now)
        keys = [k for k in self._keys() if intent.tag is None or tag_of(k) == intent.tag]
        out: list[OrderUpdate] = []
        for key in keys:
            if intent.price == "last_close":
                out.extend(self._flatten_last_close(key, intent, now))
            else:
                out.extend(self._flatten_next_open(key, intent, now))
        return out

    def _flatten_last_close(
        self, key: SetupKey, intent: Flatten, now: Instant
    ) -> list[OrderUpdate]:
        tag = tag_of(key)
        before = self.book
        price: Ticks = 0
        open_ns: Instant = now
        if key in before.trades:
            bar = self._ring_bar(key.instrument, intent.stamp_ns)
            if bar is None or bar.c_t is None:
                self._log(
                    {
                        "kind": "flatten_no_price",
                        "level": "error",
                        "tag": tag,
                        "reason": intent.reason,
                    }
                )
                return [OrderUpdate(tag, "rejected", "no_price", None, "exit", None, None, now)]
            price, open_ns = bar.c_t, bar.open_ns
        cancels = dict(self._state.cancels)
        if _holds_entry(before, key):
            cancels.setdefault(tag, intent.reason)
            self._state = replace(self._state, cancels=cancels)
        after, events = close_setup(before, key, open_ns, price, self._cfg, reason=intent.reason)
        return self._apply(before, after, events, None, now)

    def _flatten_next_open(self, key: SetupKey, intent: Flatten, now: Instant) -> list[OrderUpdate]:
        tag = tag_of(key)
        out = self._cancel_tag(tag, now, intent.reason)
        trade = self.book.trades.get(key)
        cid = client_id(self._id, tag, "exit")
        if trade is None or cid in self.book.orders:
            return out
        order = Order(
            cid,
            key,
            trade.instrument,
            _exit_side(trade.direction),
            "market",
            trade.open_qty,
            None,
            now,
            "exit",
        )
        self._state = replace(self._state, book=self.book.submit_exit(order))
        return out

    def _ring_bar(self, instrument: str, stamp_ns: Instant | None) -> Bar | None:
        for bar in reversed(self._state.ring.get(instrument, ())):
            if stamp_ns is None or bar.close_ns <= stamp_ns:
                return bar
        return None


# ---------------------------------------------------------------- book helpers


def _all_keys(book: SimBook) -> set[SetupKey]:
    out = set(book.trades) | set(book.brackets) | set(book.market_brackets)
    for working in book.orders.values():
        if working.placed.setup_key is not None:
            out.add(working.placed.setup_key)
    return out


def _holds_entry(book: SimBook, key: SetupKey) -> bool:
    if key in book.market_brackets:
        return True
    return any(w.placed.setup_key == key and w.placed.role == "entry" for w in book.orders.values())


def _entry_live(book: SimBook, tag: str) -> bool:
    return any(
        w.placed.role == "entry"
        and w.placed.setup_key is not None
        and tag_of(w.placed.setup_key) == tag
        for w in book.orders.values()
    )


def _working_entries(book: SimBook) -> list[tuple[str, SetupKey, bool, bool]]:
    """``(tag, key, exact_bar_open, reference)`` of every entry order in the book.

    ``reference``: the entry's market bracket carries ``reference_t``.
    """
    out: list[tuple[str, SetupKey, bool, bool]] = []
    for working in book.orders.values():
        placed = working.placed
        key = placed.setup_key
        if placed.role != "entry" or key is None:
            continue
        mb = book.market_brackets.get(key)
        exact = mb is not None and mb.exact_bar_open
        reference = mb is not None and mb.reference_t is not None
        out.append((tag_of(key), key, exact, reference))
    return out


def _split(book: SimBook, keys: set[SetupKey]) -> tuple[SimBook, SimBook]:
    """``(view without keys, the keys' part)``."""

    def part(inside: bool) -> SimBook:
        return SimBook(
            orders={c: w for c, w in book.orders.items() if (w.placed.setup_key in keys) == inside},
            brackets={k: b for k, b in book.brackets.items() if (k in keys) == inside},
            trades={k: t for k, t in book.trades.items() if (k in keys) == inside},
            last_bar_open=dict(book.last_bar_open),
            market_brackets={
                k: m for k, m in book.market_brackets.items() if (k in keys) == inside
            },
        )

    return part(False), part(True)


def _merge(view: SimBook, held: SimBook) -> SimBook:
    """``view`` with ``held``'s setups put back unchanged."""
    return SimBook(
        orders={**view.orders, **held.orders},
        brackets={**view.brackets, **held.brackets},
        trades={**view.trades, **held.trades},
        last_bar_open=dict(view.last_bar_open),
        market_brackets={**view.market_brackets, **held.market_brackets},
    )
