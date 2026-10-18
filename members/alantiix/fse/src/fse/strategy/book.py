"""Bar-by-bar decisions for a :class:`~fse.strategy.spec.StrategySpec`.

Pure: the plugin turns :class:`Plan` values into host intents and reports fills
back. Indicators update only with completed resampled bars, so a signal never
sees the bar that has not closed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal
from typing import Literal

from fse.engine.levels import TICKS_PER_POINT
from fse.engine.sizing import POINT_VALUE_USD
from fse.engine.types import Direction
from fse.strategy.indicators import IndicatorEngine
from fse.strategy.spec import PRICE_FIELDS, ConditionSpec, ScaleOutSpec, StrategySpec

Action = Literal["enter_market", "enter_limit", "enter_stop", "flatten", "cancel"]
_MINUTES = {"2m": 2, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "60m": 60}


@dataclass(frozen=True, slots=True)
class RBar:
    """One resampled bar. Tick prices are the host's contract ticks."""

    open_ns: int
    close_ns: int
    o: float
    h: float
    low: float
    c: float
    v: float
    o_t: int
    h_t: int
    l_t: int
    c_t: int
    day: date
    wall: time


@dataclass(frozen=True, slots=True)
class Plan:
    """One host intent the plugin should submit."""

    action: Action
    tag: str
    instrument: str
    direction: Direction
    qty: int
    stop_ticks: int | None = None
    stop_t: int | None = None
    price_t: int | None = None
    target_t: int | None = None
    reference_t: int | None = None
    placed_at: int = 0
    reason: str = ""
    setup_through: bool = False
    """The completed bar already traded to a pullback limit. The host fills it at that price."""


@dataclass(frozen=True, slots=True)
class Outcome:
    """One completed entry, measured on the position rather than each leg."""

    symbol: str
    day: str
    clock: str
    side: Direction
    entry_t: int
    stop_t: int
    mfe_r: float
    hit_1: bool
    hit_2: bool
    hit_3: bool
    hit_4: bool


@dataclass
class _Leg:
    tag: str
    qty: int
    target_t: int | None
    when: str | None
    status: Literal["pending", "open", "closed"] = "pending"


@dataclass
class _Position:
    direction: Direction
    instrument: str
    entry_t: int
    stop_t: int
    legs: list[_Leg]
    original_qty: int
    filled: bool = False
    mfe_r: float = 0.0
    stopped: bool = False
    clock: str = ""
    day: str = ""
    seen: set[str] = field(default_factory=set)
    outcome_done: bool = False
    adds_done: set[int] = field(default_factory=set)


def price_to_ticks(price: float) -> int:
    """Nearest tick. The engine's contracts are 0.25 points."""
    return round(price * TICKS_PER_POINT)


def _sign(direction: Direction) -> int:
    return 1 if direction == "long" else -1


class RulesBook:
    """One symbol's rules. Call :meth:`note_excursion` then :meth:`on_bar` per 1-minute bar."""

    def __init__(self, spec: StrategySpec, instrument: str) -> None:
        if instrument not in spec.symbols:
            raise ValueError(f"{instrument} is not in the spec")
        self.spec = spec
        self.instrument = instrument
        self._ind = IndicatorEngine(spec)
        self._bars: list[RBar] = []
        self._partial: tuple[tuple[object, ...], RBar] | None = None
        self._prev: dict[str, float | None] = {}
        self._values: dict[str, float | None] = {}
        self._counts: dict[str, int] = {}
        self._swing_high: list[tuple[int, int]] = []
        self._swing_low: list[tuple[int, int]] = []
        self._position: _Position | None = None
        self._armed = False
        self._seq = 0
        self._logs: list[dict[str, object]] = []
        self._outcomes: list[Outcome] = []

    @property
    def outcomes(self) -> list[Outcome]:
        return list(self._outcomes)

    def take_logs(self) -> list[dict[str, object]]:
        logs, self._logs = self._logs, []
        return logs

    def on_fill(self, tag: str, price_t: int) -> None:
        """The host filled ``tag``. Excursion is measured from this price."""
        pos = self._position
        if pos is None:
            return
        for leg in pos.legs:
            if leg.tag == tag and leg.status == "pending":
                leg.status = "open"
                pos.filled = True
                pos.entry_t = price_t
                if _sign(pos.direction) * pos.stop_t >= _sign(pos.direction) * price_t:
                    pos.stopped = True
                return

    def on_closed(self, tag: str) -> Outcome | None:
        """The host closed ``tag``. The position outcome fires when the last leg closes."""
        pos = self._position
        if pos is None:
            return None
        for leg in pos.legs:
            if leg.tag == tag:
                leg.status = "closed"
        return self._finish_if_flat()

    def on_dead(self, tag: str) -> Outcome | None:
        """A working entry was rejected, cancelled, or expired without a fill."""
        pos = self._position
        if pos is None:
            return None
        for leg in pos.legs:
            if leg.tag == tag and leg.status == "pending":
                leg.status = "closed"
        return self._finish_if_flat()

    def note_excursion(self, high_t: int, low_t: int) -> None:
        """1-minute extremes while a fill is open. A bar that touches the stop does not count."""
        pos = self._position
        if pos is None or not pos.filled or pos.stopped:
            return
        risk = abs(pos.entry_t - pos.stop_t) or 1
        if pos.direction == "long":
            if low_t <= pos.stop_t:
                pos.stopped = True
                return
            pos.mfe_r = max(pos.mfe_r, (high_t - pos.entry_t) / risk)
        else:
            if high_t >= pos.stop_t:
                pos.stopped = True
                return
            pos.mfe_r = max(pos.mfe_r, (pos.entry_t - low_t) / risk)

    @property
    def seq(self) -> int:
        return self._seq

    @seq.setter
    def seq(self, value: int) -> None:
        if isinstance(value, bool) or value < 0:
            raise ValueError("seq must be a non-negative integer")
        self._seq = value

    def owns(self, tag: str) -> bool:
        """True when ``tag`` is a leg of the working position."""
        pos = self._position
        return pos is not None and any(leg.tag == tag for leg in pos.legs)

    def on_bar(self, bar: RBar, *, live: bool = True) -> list[Plan]:
        """Plans caused by this 1-minute bar (possibly none, until a resampled bar closes).

        ``live=False`` is catch-up repair: indicators and latches still update, and
        no entry, flatten, or cancel is emitted.
        """
        if not self._in_session(bar.wall):
            return []
        plans: list[Plan] = []
        completed = self._push(bar)
        if live and self._flatten_due(bar.wall) and self._partial is not None and completed is None:
            completed = self._partial[1]
            self._partial = None
        if completed is None:
            if live and self._flatten_due(bar.wall):
                plans.extend(self._flatten_all("session_flat"))
            return plans
        self._bars.append(completed)
        self._values = self._ind.update(
            completed.o, completed.h, completed.low, completed.c, completed.v, completed.day
        )
        self._confirm_swings()
        flags = self._flags()
        self._latch(flags)
        if live:
            if self._flatten_due(bar.wall):
                self._armed = False
                plans.extend(self._flatten_all("session_flat"))
            else:
                plans.extend(self._manage(completed, flags))
                if self._position is None:
                    plans.extend(self._enter_or_arm(completed, flags))
        self._prev = dict(self._values)
        return plans

    # --------------------------------------------------------------- session

    def _in_session(self, wall: time) -> bool:
        start, end = self.spec.session.start, self.spec.session.end
        if start <= end:
            return start <= wall < end
        return wall >= start or wall < end

    def _flatten_due(self, wall: time) -> bool:
        flat = self.spec.session.flatten_at
        if flat is None or not self._in_session(wall):
            return False
        return wall >= flat

    def _push(self, bar: RBar) -> RBar | None:
        size = self.spec.timeframe.bar
        key = _bucket(size, bar)
        if size == "1m":
            return bar
        if self._partial is None:
            self._partial = (key, bar)
            return None
        prev_key, prev = self._partial
        if key == prev_key:
            self._partial = (key, _merge(prev, bar))
            return None
        self._partial = (key, bar)
        return prev

    # --------------------------------------------------------------- conditions

    def _flags(self) -> dict[str, bool]:
        raw = {
            cond.id: self._eval(cond)
            for cond in self.spec.conditions
            if cond.op != "after"
        }
        for cond in self.spec.conditions:
            if cond.op == "after" and cond.left is not None and cond.right is not None:
                seen = self._position.seen if self._position is not None else set()
                raw[cond.id] = bool(raw.get(cond.left) and cond.right in seen)
        return raw

    def _latch(self, flags: dict[str, bool]) -> None:
        pos = self._position
        if pos is None or not pos.filled:
            return
        for cond in self.spec.conditions:
            if cond.op != "after" and flags.get(cond.id):
                pos.seen.add(cond.id)

    def _eval(self, cond: ConditionSpec) -> bool:
        if cond.op == "crosses_above":
            return self._cross(cond.left, cond.right, "above")
        if cond.op == "crosses_below":
            return self._cross(cond.left, cond.right, "below")
        if cond.op == "crosses":
            return self._cross(cond.left, cond.right, "above") or self._cross(
                cond.left, cond.right, "below"
            )
        if cond.op == "close_above":
            return self._compare(cond.right, above=True)
        if cond.op == "close_below":
            return self._compare(cond.right, above=False)
        if cond.op == "n_consecutive_closes":
            return self._consecutive(cond)
        if cond.op == "higher_low":
            return self._pivot(self._swing_low, cond.lookback or 1, higher=True)
        if cond.op == "lower_high":
            return self._pivot(self._swing_high, cond.lookback or 1, higher=False)
        if cond.op == "range_breakout":
            return self._breakout(cond.lookback or 1, cond.side or "above")
        if cond.op == "supertrend_up":
            return self._values.get(f"{cond.left}.dir") == 1.0
        if cond.op == "supertrend_down":
            return self._values.get(f"{cond.left}.dir") == -1.0
        return False

    def _series(self, name: str | None) -> float | None:
        if name is None or not self._bars:
            return None
        bar = self._bars[-1]
        if name == "open":
            return bar.o
        if name == "high":
            return bar.h
        if name == "low":
            return bar.low
        if name == "close":
            return bar.c
        if name in PRICE_FIELDS:
            return None
        return self._values.get(name)

    def _cross(self, left: str | None, right: str | None, way: Literal["above", "below"]) -> bool:
        if left is None or right is None:
            return False
        prev_l, prev_r = self._prev.get(left), self._prev.get(right)
        cur_l, cur_r = self._series(left), self._series(right)
        if None in (prev_l, prev_r, cur_l, cur_r):
            return False
        assert prev_l is not None
        assert prev_r is not None
        assert cur_l is not None
        assert cur_r is not None
        if way == "above":
            return prev_l <= prev_r and cur_l > cur_r
        return prev_l >= prev_r and cur_l < cur_r

    def _compare(self, right: str | None, *, above: bool) -> bool:
        cur = self._series("close")
        other = self._series(right)
        if cur is None or other is None:
            return False
        return cur > other if above else cur < other

    def _consecutive(self, cond: ConditionSpec) -> bool:
        other = self._series(cond.right)
        close = self._series("close")
        n = cond.n or 1
        ok = (
            other is not None
            and close is not None
            and (close < other if cond.side == "below" else close > other)
        )
        self._counts[cond.id] = self._counts.get(cond.id, 0) + 1 if ok else 0
        return self._counts[cond.id] >= n

    def _confirm_swings(self) -> None:
        """A pivot is confirmed ``lookback`` bars after it prints. No future bar is read early."""
        lookbacks = {
            cond.lookback
            for cond in self.spec.conditions
            if cond.op in {"higher_low", "lower_high"} and cond.lookback is not None
        }
        lookbacks.add(self.spec.stop.swing_lookback)
        for k in lookbacks:
            self._confirm(k)

    def _confirm(self, k: int) -> None:
        index = len(self._bars) - 1 - k
        if index < k:
            return
        bar = self._bars[index]
        left = self._bars[index - k : index]
        right = self._bars[index + 1 : index + 1 + k]
        if len(left) < k or len(right) < k:
            return
        if all(other.h < bar.h for other in (*left, *right)) and not any(
            item[0] == index for item in self._swing_high
        ):
            self._swing_high.append((index, bar.h_t))
        if all(other.low > bar.low for other in (*left, *right)) and not any(
            item[0] == index for item in self._swing_low
        ):
            self._swing_low.append((index, bar.l_t))

    def _pivot(self, swings: list[tuple[int, int]], k: int, *, higher: bool) -> bool:
        fresh = [item for item in swings if item[0] == len(self._bars) - 1 - k]
        if not fresh:
            return False
        prior = [item for item in swings if item[0] < fresh[-1][0]]
        if not prior:
            return False
        return fresh[-1][1] > prior[-1][1] if higher else fresh[-1][1] < prior[-1][1]

    def _breakout(self, lookback: int, side: str) -> bool:
        if len(self._bars) <= lookback:
            return False
        prior = self._bars[-lookback - 1 : -1]
        close_t = self._bars[-1].c_t
        if side == "below":
            return close_t < min(bar.l_t for bar in prior)
        return close_t > max(bar.h_t for bar in prior)

    # --------------------------------------------------------------- management

    def _manage(self, bar: RBar, flags: dict[str, bool]) -> list[Plan]:
        pos = self._position
        if pos is None:
            return []
        plans: list[Plan] = []
        if not pos.filled and not self._all_true(self.spec.context, flags):
            plans.extend(self._cancel_pending("context_off"))
        for leg in pos.legs:
            if leg.status == "open" and leg.when is not None and flags.get(leg.when):
                plans.append(_flatten(leg, pos, "scale_out"))
                leg.status = "closed"
        if pos.filled:
            plans.extend(self._add_backs(bar, flags))
        self._finish_if_flat()
        return plans

    def _add_backs(self, bar: RBar, flags: dict[str, bool]) -> list[Plan]:
        pos = self._position
        if pos is None:
            return []
        plans: list[Plan] = []
        for index, add in enumerate(self.spec.add_backs):
            if index in pos.adds_done or not flags.get(add.when):
                continue
            qty = round(pos.original_qty * add.pct / 100)
            if qty < 1:
                self._logs.append({"kind": "skip", "reason": "add_back_rounds_to_zero"})
                pos.adds_done.add(index)
                continue
            stop_ticks = abs(bar.c_t - pos.stop_t)
            stop_side = _sign(pos.direction) * pos.stop_t >= _sign(pos.direction) * bar.c_t
            if stop_ticks < 1 or stop_side:
                self._logs.append({"kind": "skip", "reason": "add_back_stop_invalid"})
                continue
            self._seq += 1
            tag = f"a{self._seq:04d}"
            pos.legs.append(_Leg(tag, qty, None, None, "pending"))
            pos.adds_done.add(index)
            plans.append(
                Plan(
                    action="enter_market",
                    tag=tag,
                    instrument=pos.instrument,
                    direction=pos.direction,
                    qty=qty,
                    stop_ticks=stop_ticks,
                    reference_t=bar.c_t,
                    placed_at=bar.close_ns,
                    reason="add_back",
                )
            )
        return plans

    def _enter_or_arm(self, bar: RBar, flags: dict[str, bool]) -> list[Plan]:
        """Place the entry. A pullback that cannot rest yet stays armed.

        ``bull_cross`` (or whatever ``when_all`` names) is true on one completed
        bar. On a 60-minute bar that cross usually prints while the 10 EMA is
        still below every recent swing, so the stop is rejected and the signal
        is gone. The pullback the limit is waiting for happens on a later bar.
        Arm the setup while context holds and try again on each completed bar
        until the limit, the stop, and the size are all valid.
        """
        signaled = self._signal_direction(flags) is not None
        pullback = self.spec.entry.kind == "pullback_limit"
        if signaled and pullback and self.spec.side in {"long", "short"}:
            self._armed = True
        elif not self._all_true(self.spec.context, flags):
            self._armed = False
        if not signaled and not (self._armed and pullback):
            return []
        plans = self._maybe_enter(bar, flags, armed=self._armed and not signaled)
        if self._position is not None:
            self._armed = False
        return plans

    def _maybe_enter(self, bar: RBar, flags: dict[str, bool], *, armed: bool = False) -> list[Plan]:
        direction = self._signal_direction(flags)
        if direction is None and armed:
            direction = self._armed_direction(flags)
        if direction is None:
            return []
        entry_t = self._entry_price(direction, bar)
        if entry_t is None:
            return []
        stop_t = self._stop_price(direction, entry_t)
        if stop_t is None:
            self._logs.append({"kind": "skip", "reason": "stop_not_beyond_entry"})
            return []
        qty = _qty(self.spec, self.instrument, entry_t, stop_t)
        if qty < 1:
            self._logs.append({"kind": "skip", "reason": "size_zero"})
            return []
        parts = _split(qty, [item.pct for item in self.spec.scale_outs])
        if any(part == 0 for part in parts):
            self._logs.append({"kind": "note", "reason": "zero_leg_folded"})
        legs: list[_Leg] = []
        plans: list[Plan] = []
        self._seq += 1
        group = self._seq
        for index, (part, scale) in enumerate(_paired(parts, self.spec.scale_outs)):
            if part < 1:
                continue
            tag = f"e{group:04d}{index}"
            target = _target(direction, entry_t, stop_t, scale)
            legs.append(_Leg(tag, part, target, None if scale is None else scale.when))
            plans.append(
                self._entry_plan(direction, bar, tag, part, entry_t, stop_t, target)
            )
        if not plans:
            return []
        self._position = _Position(
            direction=direction,
            instrument=self.instrument,
            entry_t=entry_t,
            stop_t=stop_t,
            legs=legs,
            original_qty=qty,
            clock=bar.wall.strftime("%H:%M"),
            day=bar.day.isoformat(),
        )
        return plans

    def _armed_direction(self, flags: dict[str, bool]) -> Direction | None:
        """The side a latched pullback may still enter, while context holds."""
        if not self._all_true(self.spec.context, flags):
            return None
        side = self.spec.side
        if side == "long":
            return "long"
        if side == "short":
            return "short"
        return None

    def _signal_direction(self, flags: dict[str, bool]) -> Direction | None:
        long_on = self._all_true(self.spec.entry.when_all, flags) and self._all_true(
            self.spec.context, flags
        )
        short_ids = self.spec.entry.short_when_all or []
        short_on = self._all_true(short_ids, flags) and self._all_true(self.spec.context, flags)
        side = self.spec.side
        if side == "long" and long_on:
            return "long"
        if side == "short" and long_on:
            return "short"
        if side == "both" and long_on and not short_on:
            return "long"
        if side == "both" and short_on and not long_on:
            return "short"
        return None

    def _all_true(self, ids: list[str], flags: dict[str, bool]) -> bool:
        return all(flags.get(item) for item in ids)

    def _entry_price(self, direction: Direction, bar: RBar) -> int | None:
        kind = self.spec.entry.kind
        if kind == "market":
            return bar.c_t
        if kind == "pullback_limit":
            raw = self._values.get(self.spec.entry.limit_at or "")
            if raw is None:
                self._logs.append({"kind": "skip", "reason": "limit_not_ready"})
                return None
            limit_t = price_to_ticks(raw)
            if direction == "long" and limit_t >= bar.c_t:
                self._logs.append({"kind": "skip", "reason": "limit_not_a_pullback"})
                return None
            if direction == "short" and limit_t <= bar.c_t:
                self._logs.append({"kind": "skip", "reason": "limit_not_a_pullback"})
                return None
            return limit_t
        lookback = self.spec.entry.lookback or 1
        if len(self._bars) <= lookback:
            return None
        prior = self._bars[-lookback - 1 : -1]
        if direction == "long":
            level = max(item.h_t for item in prior) + 1
            if level <= bar.c_t:
                self._logs.append({"kind": "skip", "reason": "breakout_already_through"})
                return None
            return level
        level = min(item.l_t for item in prior) - 1
        if level >= bar.c_t:
            self._logs.append({"kind": "skip", "reason": "breakout_already_through"})
            return None
        return level

    def _stop_price(self, direction: Direction, entry_t: int) -> int | None:
        stop = self.spec.stop
        if stop.kind == "ticks":
            assert stop.ticks is not None
            price = entry_t - stop.ticks if direction == "long" else entry_t + stop.ticks
        elif stop.kind == "atr":
            atr = self._values.get(stop.atr or "")
            if atr is None:
                return None
            dist = max(1, round(atr * stop.atr_mult * TICKS_PER_POINT))
            price = entry_t - dist if direction == "long" else entry_t + dist
        else:
            swing = self._swing_price(direction, entry_t)
            if swing is None:
                return None
            price = (
                swing - stop.buffer_ticks
                if direction == "long"
                else swing + stop.buffer_ticks
            )
        if _sign(direction) * price >= _sign(direction) * entry_t:
            return None
        return price

    def _swing_price(self, direction: Direction, entry_t: int) -> int | None:
        """A swing on the loss side of ``entry_t``, the one closer to the entry.

        The latest confirmed swing is often the wrong side of a pullback limit:
        it was made at a higher price, and the new low is not confirmed yet.
        A long stop uses the most recent swing low still below the entry (a
        short stop, the most recent swing high above it). The lookback window
        is the local extreme when that extreme is also beyond the entry. A
        pivot from an earlier price can sit hundreds of points away and size
        to zero, so the nearer of the two is the stop.
        """
        longs = direction == "long"
        swings = self._swing_low if longs else self._swing_high
        beyond = [
            price
            for _index, price in swings
            if (price < entry_t if longs else price > entry_t)
        ]
        recent = beyond[-1] if beyond else None
        window = self._bars[-self.spec.stop.swing_lookback :]
        extreme: int | None = None
        if window:
            extreme = min(bar.l_t for bar in window) if longs else max(bar.h_t for bar in window)
            wrong_side = (longs and extreme >= entry_t) or (not longs and extreme <= entry_t)
            if wrong_side:
                extreme = None
        if recent is None:
            return extreme
        if extreme is None:
            return recent
        # The lookback window is the local structure. A confirmed swing from
        # before this pullback can sit hundreds of points away and size to
        # zero; the nearer of the two is the stop that still protects the entry.
        if longs:
            return extreme if extreme > recent else recent
        return extreme if extreme < recent else recent

    def _entry_plan(
        self,
        direction: Direction,
        bar: RBar,
        tag: str,
        qty: int,
        entry_t: int,
        stop_t: int,
        target_t: int | None,
    ) -> Plan:
        kind = self.spec.entry.kind
        if kind == "market":
            return Plan(
                action="enter_market",
                tag=tag,
                instrument=self.instrument,
                direction=direction,
                qty=qty,
                stop_ticks=abs(entry_t - stop_t),
                target_t=target_t,
                reference_t=entry_t,
                placed_at=bar.close_ns,
                reason="entry",
            )
        through = kind == "pullback_limit" and (
            (direction == "long" and bar.l_t <= entry_t)
            or (direction == "short" and bar.h_t >= entry_t)
        )
        return Plan(
            action="enter_stop" if kind == "breakout_stop" else "enter_limit",
            tag=tag,
            instrument=self.instrument,
            direction=direction,
            qty=qty,
            stop_t=stop_t,
            price_t=entry_t,
            target_t=target_t,
            placed_at=bar.close_ns,
            reason="entry",
            setup_through=through,
        )

    def _cancel_pending(self, reason: str) -> list[Plan]:
        pos = self._position
        if pos is None:
            return []
        plans: list[Plan] = []
        for leg in pos.legs:
            if leg.status == "pending":
                plans.append(
                    Plan(
                        action="cancel",
                        tag=leg.tag,
                        instrument=pos.instrument,
                        direction=pos.direction,
                        qty=leg.qty,
                        reason=reason,
                    )
                )
                leg.status = "closed"
        self._finish_if_flat()
        return plans

    def _flatten_all(self, reason: str) -> list[Plan]:
        pos = self._position
        if pos is None:
            return []
        plans: list[Plan] = []
        for leg in pos.legs:
            if leg.status == "pending":
                plans.append(
                    Plan(
                        action="cancel",
                        tag=leg.tag,
                        instrument=pos.instrument,
                        direction=pos.direction,
                        qty=leg.qty,
                        reason=reason,
                    )
                )
                leg.status = "closed"
            elif leg.status == "open":
                plans.append(_flatten(leg, pos, reason))
                leg.status = "closed"
        self._finish_if_flat()
        return plans

    def _finish_if_flat(self) -> Outcome | None:
        pos = self._position
        if pos is None or pos.outcome_done:
            return None
        if any(leg.status != "closed" for leg in pos.legs):
            return None
        pos.outcome_done = True
        outcome: Outcome | None = None
        if pos.filled:
            mfe = pos.mfe_r
            outcome = Outcome(
                symbol=pos.instrument,
                day=pos.day,
                clock=pos.clock,
                side=pos.direction,
                entry_t=pos.entry_t,
                stop_t=pos.stop_t,
                mfe_r=mfe,
                hit_1=mfe >= 1,
                hit_2=mfe >= 2,
                hit_3=mfe >= 3,
                hit_4=mfe >= 4,
            )
            self._outcomes.append(outcome)
        self._position = None
        return outcome


def _flatten(leg: _Leg, pos: _Position, reason: str) -> Plan:
    return Plan(
        action="flatten",
        tag=leg.tag,
        instrument=pos.instrument,
        direction=pos.direction,
        qty=leg.qty,
        reason=reason,
    )


def _paired(
    parts: list[int], scales: list[ScaleOutSpec]
) -> list[tuple[int, ScaleOutSpec | None]]:
    if not parts:
        return []
    if not scales:
        return [(parts[0], None)]
    return list(zip(parts, scales, strict=True))


def _target(
    direction: Direction, entry_t: int, stop_t: int, scale: ScaleOutSpec | None
) -> int | None:
    if scale is None or scale.at_r is None:
        return None
    risk = abs(entry_t - stop_t)
    dist = max(1, round(scale.at_r * risk))
    return entry_t + _sign(direction) * dist


def _qty(spec: StrategySpec, instrument: str, entry_t: int, stop_t: int) -> int:
    risk_ticks = abs(entry_t - stop_t)
    if risk_ticks < 1:
        return 0
    risk_points = Decimal(risk_ticks) / Decimal(TICKS_PER_POINT)
    point = POINT_VALUE_USD[instrument]
    qty = int(spec.sizing.risk_usd / (risk_points * point))
    return max(0, min(qty, spec.sizing.max_contracts))


def _split(total: int, pcts: list[float]) -> list[int]:
    if total < 1:
        return []
    if not pcts:
        return [total]
    raw = [total * pct / 100 for pct in pcts]
    floors = [math.floor(item) for item in raw]
    remain = total - sum(floors)
    order = sorted(range(len(pcts)), key=lambda i: (raw[i] - floors[i], -i), reverse=True)
    for index in order[:remain]:
        floors[index] += 1
    return floors


def _bucket(size: str, bar: RBar) -> tuple[object, ...]:
    if size == "1d":
        return ("d", bar.day)
    if size == "1m":
        return ("m", bar.day, bar.wall.hour, bar.wall.minute)
    minutes = _MINUTES[size]
    total = bar.wall.hour * 60 + bar.wall.minute
    return ("b", bar.day, (total // minutes) * minutes)


def _merge(left: RBar, right: RBar) -> RBar:
    return RBar(
        open_ns=left.open_ns,
        close_ns=right.close_ns,
        o=left.o,
        h=max(left.h, right.h),
        low=min(left.low, right.low),
        c=right.c,
        v=left.v + right.v,
        o_t=left.o_t,
        h_t=max(left.h_t, right.h_t),
        l_t=min(left.l_t, right.l_t),
        c_t=right.c_t,
        day=left.day,
        wall=right.wall,
    )

