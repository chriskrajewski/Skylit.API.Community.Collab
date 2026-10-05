"""Property 71: Paper isolation.

*For any* live session in Paper Order_Mode, the Broker_Adapter receives zero
order submit, modify or cancel calls, and every order intent reaches the
Paper_Broker.

Each example drives :class:`~fse.live.runner.LiveSession` over generated
sessions (:class:`tests.strategies.live_sessions.Driver`) with a spy
Broker_Adapter that records every call, and a Strategy_Config whose
``order_mode`` is drawn from paper, practice and combine (``fse paper`` runs
Paper Order_Mode whatever the config says). The engine is wrapped so every
intent it returns (``Engine.step`` and the bar-phase hooks) is recorded, and
``PaperBroker.route`` records what it receives.

Checked: the routing is Paper; the spy adapter got no call at all; the
adapter as the router exposes it has none of the order methods; and the
Paper_Broker received exactly the engine's intents, in order.

**Validates: Requirements 23.1, 23.15**
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest
from hypothesis import event, given
from hypothesis import strategies as st

from fse.config.schema import StrategyConfig
from fse.engine.planner import OrderIntent
from fse.engine.step import BarPhaseResult, Engine, StepResult
from fse.live.order_router import ORDER_METHODS, READ_METHODS
from fse.sim.paper_broker import PaperBroker
from tests.strategies.backtest_inputs import MarketInputs, market_inputs, strategy_configs
from tests.strategies.live_sessions import Driver, LivePlan, live_plans


class SpyAdapter:
    """A Broker_Adapter stand-in that records every call."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def _record(self, name: str) -> Any:
        def call(*args: object, **kwargs: object) -> None:
            self.calls.append(name)

        return call

    def __getattr__(self, name: str) -> Any:
        if name in ORDER_METHODS | READ_METHODS:
            return self._record(name)
        raise AttributeError(name)


class RecordingEngine:
    """An :class:`Engine` that records every intent it returns."""

    def __init__(self, engine: Engine, out: list[OrderIntent]) -> None:
        self._engine = engine
        self._out = out

    def __getattr__(self, name: str) -> Any:
        return getattr(self._engine, name)

    def step(self, *args: Any, **kwargs: Any) -> StepResult:
        result = self._engine.step(*args, **kwargs)
        self._out.extend(result.intents)
        return result

    def update_stops(self, *args: Any, **kwargs: Any) -> BarPhaseResult:
        result = self._engine.update_stops(*args, **kwargs)
        self._out.extend(result.intents)
        return result

    def check_loss_stop(self, *args: Any, **kwargs: Any) -> BarPhaseResult:
        result = self._engine.check_loss_stop(*args, **kwargs)
        self._out.extend(result.intents)
        return result


@st.composite
def configs(draw: st.DrawFn) -> StrategyConfig:
    data = draw(strategy_configs()).model_dump(mode="python", by_alias=True)
    data["order_mode"] = draw(st.sampled_from(("paper", "practice", "combine")), label="mode")
    data["live"]["live_max_snapshot_age_s"] = draw(st.sampled_from((30, 900)), label="max age")
    return StrategyConfig.model_validate(data)


# Feature: skylit-futures-strategy-engine, Property 71: Paper isolation
@given(market=market_inputs(), cfg=configs(), plan=live_plans(halts=False))
def test_paper_mode_sends_no_broker_order_and_routes_every_intent_to_the_paper_broker(
    market: MarketInputs, cfg: StrategyConfig, plan: LivePlan
) -> None:
    plan = LivePlan(plan.seed, 300, plan.max_delay_s, plan.outage, 0.02, ())
    produced: list[OrderIntent] = []
    received: list[OrderIntent] = []
    original = PaperBroker.route

    def route(self: PaperBroker, intents: Iterable[OrderIntent]) -> Any:
        given_ = list(intents)
        received.extend(given_)
        return original(self, given_)

    with TemporaryDirectory(prefix="fse-p71-") as tmp, pytest.MonkeyPatch.context() as mp:
        mp.setattr(PaperBroker, "route", route)
        driver = Driver(Path(tmp), market, cfg)
        spy = SpyAdapter()
        engine = RecordingEngine(driver.engine, produced)
        driver.run_market([plan], engine=engine, adapter=spy)
        for live in driver.sessions:
            assert live.routing.mode == "paper"
            assert not live.routing.sends_broker_orders
            broker = live.routing.broker
            assert broker is not None
            for name in ORDER_METHODS:
                assert not hasattr(broker, name), name
        assert spy.calls == []
        assert received == produced
        event(f"config order_mode: {cfg.order_mode}")
        event(f"intents routed: {'some' if produced else 'none'}")
