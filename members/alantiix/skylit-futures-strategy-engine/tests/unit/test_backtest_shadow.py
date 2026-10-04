"""Unit test for the cause of a cancelled Setup_Key in the ShadowBook (design §19).

Hand-built decision-log payloads feed one :class:`ShadowBook` session. A key
placed at T1 whose resting entry a Cancel_Trigger cancels at T2 keeps that
cause when a later emission at T3 is sized to zero: the key can never be
placed again, so the re-emission blocked nothing. A key that was never placed
still gets the sizing rejection as its cause.

**Validates: Requirements 19.5**
"""

from __future__ import annotations

from datetime import date, time

from fse.backtest.shadow import ShadowBook
from fse.engine.gates.registry import GateEvaluation, GateResult
from fse.engine.planner import EntryCancelled, PlaceBracket, PlannerContext
from fse.engine.sizing import SIZING_STEPS, SizingRejection, StepOutcome
from fse.engine.step import (
    CardTriggers,
    DecisionPayload,
    EngineParams,
    ManagementEvent,
    SetupDecision,
    Sized,
)
from fse.engine.taps import Tap, TapState
from fse.engine.types import (
    CandidateSetup,
    MissingInput,
    Order,
    OrderKind,
    OrderRole,
    SetupInputs,
    SetupKey,
    Side,
    SnapshotAsOf,
    SourceNodeRef,
    Unavailable,
)
from fse.timekit import NS_PER_SECOND, Instant, ny_instant
from tests.strategies.backtest_inputs import fixed_config

SESSION = date(2026, 3, 5)
T1 = ny_instant(SESSION, time(10, 0))
T2 = T1 + 300 * NS_PER_SECOND
T3 = T2 + 300 * NS_PER_SECOND
RTH = (ny_instant(SESSION, time(9, 30)), ny_instant(SESSION, time(16, 0)))
FLATTEN = ny_instant(SESSION, time(15, 50))
PLACED_STRIKE = 5760.0
NEVER_PLACED_STRIKE = 5740.0


def key(strike: float) -> SetupKey:
    return SetupKey("MES", "floor_ceiling_bounce", strike, "long", SESSION, 1)


def candidate(strike: float, t: Instant) -> CandidateSetup:
    level = int(strike * 4)
    inputs = SetupInputs(
        map_as_of=(SnapshotAsOf("SPX", "gamma", t - 3 * NS_PER_SECOND),),
        source_spot=5800.0,
        futures_price=23008,
        conversion_method="offset",
        conversion_factor=0.0,
        band_half_width_pts=5.0,
        regime="Positive_Gamma",
        map_grade="Neutral_Map",
        stop_rule="fixed_ticks",
    )
    source = SourceNodeRef("SPX", "gamma", strike, 3.0e9, level, (strike - 5.0, strike + 5.0))
    return CandidateSetup(
        key(strike), "floor_ceiling_bounce", t, level, level - 20, (level + 60,), "fixed_r",
        source, inputs,
    )  # fmt: skip


def a_plus(strike: float, t: Instant) -> GateEvaluation:
    result = GateResult(key(strike), t, "stale_map", "pass", 3.0, 90)
    return GateEvaluation(key(strike), t, (result,), (), "A_Plus")


def order(client_id: str, role: OrderRole, side: Side, kind: OrderKind, price: int) -> Order:
    return Order(client_id, key(PLACED_STRIKE), "MES", side, kind, 1, price, T1, role)


def placed(strike: float, t: Instant) -> SetupDecision:
    sized = Sized(1, tuple(StepOutcome(s, 1, s == "base_contracts") for s in SIZING_STEPS))
    bracket = PlaceBracket(
        order("c1", "entry", "buy", "limit", 23040),
        order("c2", "stop", "sell", "stop", 23020),
        (order("c3", "tp1", "sell", "limit", 23100),),
    )
    return SetupDecision(candidate(strike, t), a_plus(strike, t), sized, bracket)


def sized_to_zero(strike: float, t: Instant) -> SetupDecision:
    steps = (
        StepOutcome("base_contracts", 1, True),
        StepOutcome("big_win_reduce", 1, False),
        StepOutcome("trinity_size_down", 0, True),
    )
    rejection = SizingRejection(key(strike), "size_zero", "trinity_size_down", None, steps)
    return SetupDecision(candidate(strike, t), a_plus(strike, t), rejection, None)


def payload(
    t: Instant,
    setups: tuple[SetupDecision, ...] = (),
    management: tuple[ManagementEvent, ...] = (),
) -> DecisionPayload:
    missing = MissingInput(("SPX/gamma",))
    return DecisionPayload(
        session=SESSION,
        t=t,
        map_entries=(),
        snapshot_age_ns=Unavailable("no Snapshot"),
        futures=(),
        regime=missing,
        map_grade=missing,
        setups=setups,
        skips=(),
        intents=(),
        management=management,
        fills=(),
        bar_events=(),
        external_blocks=(),
        lockouts=(),
        cards=CardTriggers((), (), order_event=False, first_alerts=()),
    )


def tapped(*strikes: float) -> TapState:
    """A first Tap of each Node after T3, so every key's governing emission is its last."""
    first = T3 + 60 * NS_PER_SECOND
    taps = tuple(
        Tap("SPX", "gamma", s, "MES", SESSION, 1, first, first + 60 * NS_PER_SECOND,
            first + 60 * NS_PER_SECOND, ended=True)
        for s in strikes
    )  # fmt: skip
    return TapState(session=SESSION, ended_taps=taps)


def test_a_cancelled_entry_keeps_its_cause_after_a_later_size_zero_emission() -> None:
    cfg = fixed_config(300)
    book = ShadowBook(cfg.fills, EngineParams.from_sections(cfg).planner, cfg.sizing)
    book.start_session(SESSION, RTH)
    steps = (
        payload(T1, (placed(PLACED_STRIKE, T1),)),
        payload(T2, management=(EntryCancelled(key(PLACED_STRIKE), T2, ("king_flip",)),)),
        payload(T3, (sized_to_zero(PLACED_STRIKE, T3), sized_to_zero(NEVER_PLACED_STRIKE, T3))),
    )
    for p in steps:
        book.on_decision(p.t, p, PlannerContext(p.t, SESSION, FLATTEN), TapState.initial())

    records = {r.key: r for r in book.finish_session(tapped(PLACED_STRIKE, NEVER_PLACED_STRIKE))}

    cancelled = records[key(PLACED_STRIKE)]
    assert (cancelled.status, cancelled.cause) == ("cancelled", ("king_flip",))
    never_placed = records[key(NEVER_PLACED_STRIKE)]
    assert (never_placed.status, never_placed.cause) == (
        "cancelled",
        ("size_zero", "trinity_size_down"),
    )
