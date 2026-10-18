"""A declarative rules strategy: the runner YAML ``config`` is a :class:`StrategySpec`.

``fse backtest-plugin`` runs it on the same host path as every other plugin.
Each scale-out is its own bracket, because a host bracket has two target roles
and this language allows more than two partials.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import time
from typing import Literal

from fse.engine.types import Bar, Direction
from fse.live.plugins.api import (
    BarNeed,
    Cancel,
    DataNeeds,
    EnterMarket,
    EnterResting,
    Flatten,
    Intent,
    OrderUpdate,
    PluginContext,
    PluginServices,
    StrategyPlugin,
    TargetLeg,
    decision_card,
)
from fse.logio.canonical_json import JsonValue
from fse.logio.canonical_json import dumps as canonical_dumps
from fse.strategy.book import Outcome, Plan, RBar, RulesBook
from fse.strategy.spec import StrategySpec, validate_document
from fse.strategy.warmup import feed_warmup
from fse.timekit import ny_datetime

__all__ = ["RulesPlugin"]

STATE_VERSION = 1
_FLATTEN_REASONS = frozenset({"scale_out", "session_flat"})


class RulesPlugin(StrategyPlugin):
    """Trade a validated rules spec. Unknown rules are refused at load."""

    label = "Rules"
    description = (
        "Declarative futures rules: indicators, conditions, entries, stops, "
        "scale-outs, and fixed-risk sizing. The same code path as fse run."
    )
    config_model = StrategySpec

    @classmethod
    def validate_config(cls, config: Mapping[str, object]) -> list[str]:
        report = validate_document(dict(config))
        lines = list(report.errors)
        lines.extend(
            f"{item.path}: cannot express {item.rule}: {item.reason}"
            for item in report.unsupported
        )
        return lines

    def __init__(
        self, plugin_id: str, config: Mapping[str, object], services: PluginServices
    ) -> None:
        super().__init__(plugin_id, config, services)
        report = validate_document(dict(config))
        if not report.ok:
            problems = self.validate_config(config)
            raise ValueError(f"plugin {plugin_id!r}: bad config: {'; '.join(problems)}")
        self.spec = StrategySpec.model_validate(dict(config))
        self._books = {symbol: RulesBook(self.spec, symbol) for symbol in self.spec.symbols}
        self._written: dict[str, int] = {}

    def needs(self) -> DataNeeds:
        return DataNeeds(
            bars=tuple(BarNeed(symbol) for symbol in self.spec.symbols),
            hours="eth",
            brokerage="host",
        )

    def on_start(self, _ctx: PluginContext) -> Sequence[Intent]:
        feed_warmup(self._books, self.services.cache_dir)
        return ()

    def on_bar(
        self, bar: Bar, received_ns: int, catch_up: bool, ctx: PluginContext
    ) -> Sequence[Intent]:
        book = self._books.get(bar.instrument)
        if book is None:
            return ()
        if bar.c_t is None or bar.h_t is None or bar.l_t is None or bar.o_t is None:
            return ()
        book.note_excursion(bar.h_t, bar.l_t)
        plans = book.on_bar(_rbar(bar), live=not catch_up)
        for entry in book.take_logs():
            ctx.log(_rules_note(entry))
        self._write_new(book)
        intents = tuple(_intent(plan, received_ns) for plan in plans)
        for plan in plans:
            if plan.action.startswith("enter"):
                ctx.log(
                    {
                        "kind": "rules_entry",
                        "level": "info",
                        "instrument": plan.instrument,
                        "side": plan.direction,
                        "qty": plan.qty,
                        "tag": plan.tag,
                        "reason": plan.reason,
                        "card": _card(plan, bar),
                    }
                )
        return intents

    def on_order_update(self, update: OrderUpdate, ctx: PluginContext) -> Sequence[Intent]:
        book = next((item for item in self._books.values() if item.owns(update.tag)), None)
        if book is not None and update.status == "filled_entry" and update.fill is not None:
            book.on_fill(update.tag, update.fill.price)
        elif book is not None and update.status == "closed":
            book.on_closed(update.tag)
            self._write_new(book)
        elif book is not None and update.status in {"rejected", "cancelled", "expired"}:
            book.on_dead(update.tag)
            self._write_new(book)
        payload: dict[str, JsonValue] = {
            "kind": "order_update",
            "level": "info",
            "tag": update.tag,
            "status": update.status,
        }
        if update.reason is not None:
            payload["reason"] = update.reason
        ctx.log(payload)
        return ()

    def snapshot(self) -> JsonValue:
        return {
            "version": STATE_VERSION,
            "books": {symbol: {"seq": book.seq} for symbol, book in self._books.items()},
        }

    def restore(self, state: JsonValue) -> None:
        if not isinstance(state, dict) or state.get("version") != STATE_VERSION:
            raise ValueError("not a version 1 rules plugin state")
        books = state.get("books")
        if not isinstance(books, dict):
            raise ValueError("bad rules plugin state")
        for symbol, book in self._books.items():
            item = books.get(symbol)
            if not isinstance(item, dict):
                raise ValueError(f"bad rules plugin state for {symbol}")
            seq = item.get("seq")
            if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
                raise ValueError(f"bad rules plugin seq for {symbol}")
            book.seq = seq

    def _write_new(self, book: RulesBook) -> None:
        start = self._written.get(book.instrument, 0)
        fresh = book.outcomes[start:]
        if not fresh:
            return
        path = self.services.out_dir / "outcomes.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            for outcome in fresh:
                handle.write(canonical_dumps(_outcome_json(outcome)) + "\n")
        self._written[book.instrument] = start + len(fresh)


def _rbar(bar: Bar) -> RBar:
    ny = ny_datetime(bar.open_ns)
    assert bar.o_t is not None
    assert bar.h_t is not None
    assert bar.l_t is not None
    assert bar.c_t is not None
    return RBar(
        open_ns=bar.open_ns,
        close_ns=bar.close_ns,
        o=bar.o,
        h=bar.h,
        low=bar.l,
        c=bar.c,
        v=bar.v,
        o_t=bar.o_t,
        h_t=bar.h_t,
        l_t=bar.l_t,
        c_t=bar.c_t,
        day=ny.date(),
        wall=time(ny.hour, ny.minute, ny.second),
    )


def _intent(plan: Plan, received_ns: int) -> Intent:
    if plan.action == "cancel":
        return Cancel(plan.tag)
    if plan.action == "flatten":
        reason = plan.reason if plan.reason in _FLATTEN_REASONS else "scale_out"
        return Flatten(plan.tag, reason, "next_open", received_ns)
    if plan.action == "enter_market":
        assert plan.stop_ticks is not None
        assert plan.reference_t is not None
        targets: tuple[TargetLeg, ...] = ()
        if plan.target_t is not None:
            targets = (TargetLeg("tp1", plan.qty, abs(plan.target_t - plan.reference_t)),)
        return EnterMarket(
            tag=plan.tag,
            instrument=plan.instrument,
            direction=plan.direction,
            qty=plan.qty,
            stop_ticks=plan.stop_ticks,
            targets=targets,
            placed_at=plan.placed_at,
            exact_bar_open=True,
            reference_t=plan.reference_t,
        )
    assert plan.price_t is not None
    assert plan.stop_t is not None
    kind: Literal["limit", "stop"] = "stop" if plan.action == "enter_stop" else "limit"
    resting: tuple[tuple[Literal["tp1", "tp2"], int, int], ...] = ()
    if plan.target_t is not None:
        resting = (("tp1", plan.qty, plan.target_t),)
    return EnterResting(
        tag=plan.tag,
        instrument=plan.instrument,
        direction=plan.direction,
        qty=plan.qty,
        kind=kind,
        price=plan.price_t,
        stop=plan.stop_t,
        targets=resting,
        setup_through=plan.setup_through,
    )


def _card(plan: Plan, bar: Bar) -> dict[str, JsonValue]:
    side: Direction = plan.direction
    return decision_card(
        id=plan.tag,
        title=f"{side} {plan.instrument} {plan.reason}",
        as_of_ns=bar.close_ns,
        action="enter",
        instrument=plan.instrument,
        side=side,
        qty=plan.qty,
        entry=None if plan.price_t is None else plan.price_t / 4,
        stop=None if plan.stop_t is None else plan.stop_t / 4,
        reason=plan.reason,
        tag=plan.tag,
    )


def _outcome_json(outcome: Outcome) -> dict[str, JsonValue]:
    return {
        "symbol": outcome.symbol,
        "day": outcome.day,
        "clock": outcome.clock,
        "side": outcome.side,
        "entry_t": outcome.entry_t,
        "stop_t": outcome.stop_t,
        "mfe_r": outcome.mfe_r,
        "hit_1": outcome.hit_1,
        "hit_2": outcome.hit_2,
        "hit_3": outcome.hit_3,
        "hit_4": outcome.hit_4,
    }


def _log_values(entry: dict[str, object]) -> dict[str, JsonValue]:
    out: dict[str, JsonValue] = {}
    for key, value in entry.items():
        if isinstance(value, str | int | float | bool) or value is None:
            out[key] = value
    return out


def _rules_note(entry: dict[str, object]) -> dict[str, JsonValue]:
    """Host log line. The book's own kind stays in ``event`` so this line stays ``rules``."""
    values = _log_values(entry)
    event = values.pop("kind", "note")
    if not isinstance(event, str):
        event = "note"
    return {"kind": "rules", "level": "info", "event": event, **values}
