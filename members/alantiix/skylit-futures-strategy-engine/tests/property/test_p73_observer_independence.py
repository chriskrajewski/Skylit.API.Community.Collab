"""Property 73: Observer independence.

*For any* live session, the sequence of order submissions, modifications and
cancellations (instrument, side, type, price, contracts, Decision_Time) is
identical whether the Narrator is disabled, enabled, failing or timing out,
whether Notifier deliveries succeed or fail, and whatever `/v1/gex/levels`
returns.

Each example drives :class:`~fse.live.runner.LiveSession` over generated
sessions (:class:`tests.strategies.live_sessions.Driver`) twice: once with the
Narrator off, a file sink only and no levels comparison, and once with a drawn
observer setup. The observer run's Strategy_Config differs only in its
``notify`` section (Narrator on or off, webhook sink or not). After every
Decision_Time, as the Notifier's own task would between Decision_Times, each
queued Finding_Card and alert goes through :meth:`Notifier.deliver` (Narrator,
then sinks), and a :class:`~fse.live.levels_compare.LevelsCompare` fetch runs
against the session's live inputs and logs to the session's feed log. The
Narrator and webhook endpoints are respx routes (``assert_all_mocked``) that
answer with the drawn outcome: prose, an HTTP error, empty or over-length
prose, or no reply within the timeout (Narrator); success, an HTTP error, a
network error or no reply within the delivery timeout (webhook). The levels
client returns drawn levels, a failure, or nothing within its timeout. Time
runs on a fake clock that follows the Decision_Times.

Checked: every intent the Paper_Broker receives, tagged with the number of
Decision_Times done when it arrived, and every Decision_Time, are the same in
both runs. Also checked: ``notifier.jsonl`` holds one ``delivery_failed`` entry
per delivered message when the webhook fails or times out, and none otherwise.

**Validates: Requirements 23.10, 25.14, 25.15**
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Final

import httpx
import pytest
import respx
from hypothesis import event, given
from hypothesis import strategies as st

from fse.config.schema import StrategyConfig
from fse.engine.planner import OrderIntent
from fse.live.levels_compare import LEVELS_TIMEOUT_S, LevelsCompare
from fse.live.runner import LiveDecision, LiveSession
from fse.notify.notifier import (
    DELIVERY_TIMEOUT_S,
    NOTIFIER_LOG_FILE_NAME,
    WEBHOOK_ENV,
    Notifier,
)
from fse.pit.market_view import heatmap_view
from fse.secrets.env import EnvView
from fse.sim.paper_broker import PaperBroker
from fse.skylit.client import Failed
from fse.skylit.endpoints import Host, ViewParams
from fse.skylit.models import HeatmapMeta, Level, LevelsResponse, SymbolLevels
from fse.timekit import NS_PER_SECOND, Instant
from tests.fakes.clock import FakeClock
from tests.strategies.backtest_inputs import MarketInputs, market_inputs, strategy_configs
from tests.strategies.live_sessions import Driver, LivePlan, live_plans

NARRATOR_BASE: Final = "https://narrator.invalid/v1"
WEBHOOK_URL: Final = "https://hooks.invalid/fse-p73"
NARRATOR_OUTCOMES: Final = ("off", "prose", "http_error", "empty", "too_long", "timeout")
WEBHOOK_OUTCOMES: Final = ("none", "ok", "http_error", "network_error", "timeout")
LEVELS_OUTCOMES: Final = ("none", "response", "failed", "timeout")
_NODE_TYPES: Final = ("king", "floor", "ceiling", "gatekeeper", "node", "wall", None)


@dataclass(frozen=True, slots=True)
class Observers:
    narrator: str
    webhook: str
    levels: str
    strikes: tuple[tuple[float, str | None], ...]


@st.composite
def observers(draw: st.DrawFn) -> Observers:
    strikes = draw(
        st.lists(
            st.tuples(
                st.integers(100, 30_000).map(float),
                st.sampled_from(_NODE_TYPES),
            ),
            max_size=6,
        ),
        label="levels",
    )
    return Observers(
        draw(st.sampled_from(NARRATOR_OUTCOMES), label="narrator"),
        draw(st.sampled_from(WEBHOOK_OUTCOMES), label="webhook"),
        draw(st.sampled_from(LEVELS_OUTCOMES), label="levels outcome"),
        tuple(strikes),
    )


def _with_notify(cfg: StrategyConfig, obs: Observers | None) -> StrategyConfig:
    data = cfg.model_dump(mode="python", by_alias=True)
    sinks = ["file"]
    narrator: dict[str, Any] = {"enabled": False}
    if obs is not None:
        if obs.webhook != "none":
            sinks.append("webhook")
        if obs.narrator != "off":
            narrator = {"enabled": True, "base_url": NARRATOR_BASE, "model": "test-model"}
    data["notify"]["sinks"] = sinks
    data["notify"]["narrator"] = {**data["notify"]["narrator"], **narrator}
    return StrategyConfig.model_validate(data)


class FakeLevels:
    """The levels client: the drawn levels, a failure, or no answer within the timeout."""

    def __init__(self, clock: FakeClock, obs: Observers) -> None:
        self._clock = clock
        self._obs = obs

    async def gex_levels(
        self, symbols: Iterable[str], *, metric: str, view: ViewParams
    ) -> LevelsResponse | Failed:
        if self._obs.levels == "failed":
            return Failed(Host.API, "/v1/gex/levels", {}, "failed", 5, status=503)
        if self._obs.levels == "timeout":
            await self._clock.sleep((LEVELS_TIMEOUT_S + 1) * NS_PER_SECOND)
        now = self._clock.now()
        levels = tuple(Level(s, 1.0, kind, 0.0) for s, kind in self._obs.strikes)
        items = tuple(
            SymbolLevels(sym, "2026-03-02T10:00:00-05:00", now, 5000.0, None, None, levels, None)
            for sym in symbols
        )
        return LevelsResponse(items, HeatmapMeta("gamma", "1m", None, None))


def _routes(router: respx.MockRouter, clock: FakeClock, obs: Observers) -> None:
    async def narrator(request: httpx.Request) -> httpx.Response:
        match obs.narrator:
            case "prose":
                content = "The map shows the King above spot."
            case "http_error":
                return httpx.Response(503)
            case "empty":
                content = "  "
            case "too_long":
                content = "x" * 1_501
            case _:
                await clock.sleep(11 * NS_PER_SECOND)
                content = "late"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    async def webhook(request: httpx.Request) -> httpx.Response:
        match obs.webhook:
            case "http_error":
                return httpx.Response(500)
            case "network_error":
                raise httpx.ConnectError("refused", request=request)
            case "timeout":
                await clock.sleep((DELIVERY_TIMEOUT_S + 1) * NS_PER_SECOND)
        return httpx.Response(204)

    router.post(f"{NARRATOR_BASE}/chat/completions").mock(side_effect=narrator)
    router.post(WEBHOOK_URL).mock(side_effect=webhook)


type Routed = list[tuple[int, list[OrderIntent]]]


@dataclass(frozen=True, slots=True)
class Delivery:
    """The kinds of the messages delivered, and each ``delivery_failed`` (sink, kind) logged."""

    kinds: list[str]
    failed: list[tuple[str, str]]


def _delivery(notifier: Notifier, out_dir: Path) -> Delivery:
    log = out_dir / NOTIFIER_LOG_FILE_NAME
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    entries = [json.loads(line) for line in lines]
    failed = [(e["sink"], e["message"]) for e in entries if e["event"] == "delivery_failed"]
    return Delivery([m.kind for m in notifier.sent], failed)


def _run(
    root: Path, market: MarketInputs, cfg: StrategyConfig, plan: LivePlan, obs: Observers | None
) -> tuple[Routed, list[Instant | None], Delivery]:
    """The Paper_Broker's intents (tagged), the Decision_Times and the deliveries of one run."""
    driver = Driver(root, market, cfg)
    clock = FakeClock(0)
    env = EnvView({WEBHOOK_ENV: WEBHOOK_URL}, {})
    notifier = Notifier(cfg.notify, env, driver.writer, clock, root / "notify")
    view = heatmap_view(cfg.data.heatmap_view)
    loop = asyncio.new_event_loop()
    routed: Routed = []
    times: list[Instant | None] = []
    done = 0
    original = PaperBroker.route

    def route(self: PaperBroker, intents: Iterable[OrderIntent]) -> Any:
        given_ = list(intents)
        routed.append((len(driver.decisions), given_))
        return original(self, given_)

    async def observe(live: LiveSession) -> None:
        nonlocal done
        queued = driver.outbox.messages
        while done < len(queued):
            await notifier.deliver(queued[done])
            done += 1
        if obs is not None and obs.levels != "none":
            compare = LevelsCompare(
                FakeLevels(clock, obs),
                clock,
                symbols=cfg.data.symbols,
                inputs=live.inputs,
                node_params=live.engine.params.node_params,
                sink=live.feed_log(),
                interval_s=cfg.live.levels_compare_interval_s,
            )
            await compare.compare_once(view)

    def hook(live: LiveSession, _decision: LiveDecision) -> None:
        t = live.last_t
        times.append(t)
        if t is not None and t > clock.now():
            clock.advance_to(t)
        loop.run_until_complete(clock.run(observe(live)))

    try:
        with (
            pytest.MonkeyPatch.context() as mp,
            respx.mock(assert_all_mocked=True, assert_all_called=False) as router,
        ):
            mp.setattr(PaperBroker, "route", route)
            if obs is not None:
                _routes(router, clock, obs)
            driver.run_market([plan], hook)
            loop.run_until_complete(notifier.aclose())
    finally:
        loop.close()
    return routed, times, _delivery(notifier, root / "notify")


# Feature: skylit-futures-strategy-engine, Property 73: Observer independence
@given(
    market=market_inputs(),
    cfg=strategy_configs(),
    plan=live_plans(halts=False),
    obs=observers(),
)
def test_orders_are_the_same_whatever_the_narrator_notifier_and_levels_do(
    market: MarketInputs, cfg: StrategyConfig, plan: LivePlan, obs: Observers
) -> None:
    plan = LivePlan(plan.seed, 300, plan.max_delay_s, plan.outage, 0.02, ())
    with TemporaryDirectory(prefix="fse-p73-") as tmp:
        root = Path(tmp)
        routed, times, base_delivery = _run(
            root / "base", market, _with_notify(cfg, None), plan, None
        )
        seen_routed, seen_times, delivery = _run(
            root / "observed", market, _with_notify(cfg, obs), plan, obs
        )
    assert (seen_routed, seen_times) == (routed, times)
    # Each failed or late webhook delivery is logged as delivery_failed (Req 25.14).
    assert base_delivery.failed == []
    webhook_fails = obs.webhook in ("http_error", "network_error", "timeout")
    assert delivery.failed == ([("webhook", k) for k in delivery.kinds] if webhook_fails else [])
    event(f"narrator: {obs.narrator}; webhook: {obs.webhook}; levels: {obs.levels}")
    event(f"intents routed: {'some' if any(i for _, i in routed) else 'none'}")
