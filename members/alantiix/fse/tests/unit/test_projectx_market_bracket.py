"""Market-bracket entries (design D5, T4-T5): the ``type 2`` ``Order/place`` body.

A market entry carries no price; its stop-loss and take-profit brackets are
tick distances from the broker fill. T5 sends one through a respx-mocked
gateway and checks the exact body.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from fse.logio import Redactor
from fse.projectx.broker import MAX_BRACKET_TICKS, BrokerAdapter, EntryIntent
from fse.projectx.session import API_KEY_VARIABLE, USERNAME_VARIABLE, ProjectXSession
from fse.secrets.env import EnvView
from fse.timekit import parse_rfc3339
from tests.fakes.clock import FakeClock
from tests.fakes.projectx_gateway import BASE, FakeGateway

T0 = parse_rfc3339("2026-03-02T14:35:00Z")
CREDS = {USERNAME_VARIABLE: "fake-projectx-user-0000", API_KEY_VARIABLE: "fake-projectx-key-0000"}
MNQ = "CON.F.US.MNQ.Z26"


def market(**kw: Any) -> EntryIntent:
    args: dict[str, Any] = {"stop_ticks": 40, "target_ticks": 80} | kw
    return EntryIntent.market("fse-0a1b2c3d-000001", MNQ, "buy", 2, **args)


# ---------------------------------------------------------------- T4 payload


@pytest.mark.parametrize(
    ("side", "wire", "sl", "tp"),
    [
        ("buy", 0, -12, 30),  # long: stop below fill, target above
        ("sell", 1, 12, -30),  # short: stop above fill, target below
    ],
)
def test_a_market_entry_payload_is_type_2_with_tick_brackets(
    side: str, wire: int, sl: int, tp: int
) -> None:
    intent = EntryIntent.market(
        "fse-0a1b2c3d-000001", MNQ, side, 3, stop_ticks=12, target_ticks=30  # type: ignore[arg-type]
    )
    assert intent.kind == "market"
    assert (intent.entry, intent.stop, intent.target) == (None, None, None)
    assert intent.bracket_ticks == (12, 30)
    assert (intent.stop_ticks, intent.target_ticks) == (sl, tp)
    assert intent.payload(101) == {
        "accountId": 101,
        "contractId": MNQ,
        "type": 2,
        "side": wire,
        "size": 3,
        "limitPrice": None,
        "stopPrice": None,
        "customTag": "fse-0a1b2c3d-000001",
        "stopLossBracket": {"ticks": sl, "type": 4},
        "takeProfitBracket": {"ticks": tp, "type": 1},
    }


def test_a_short_market_bracket_sends_negative_take_profit_ticks() -> None:
    """ProjectX rejects shorts with TP ticks > 0 (live errorCode 2)."""
    body = EntryIntent.market(
        "fse-0a1b2c3d-000001", MNQ, "sell", 1, stop_ticks=110, target_ticks=110
    ).payload(101)
    assert body["side"] == 1
    assert body["stopLossBracket"] == {"ticks": 110, "type": 4}
    assert body["takeProfitBracket"] == {"ticks": -110, "type": 1}
    assert body["takeProfitBracket"]["ticks"] < 0  # type: ignore[index]


@pytest.mark.parametrize(
    "ticks",
    [
        {"stop_ticks": 0},
        {"target_ticks": 0},
        {"stop_ticks": -3},
        {"target_ticks": MAX_BRACKET_TICKS + 1},
        {"stop_ticks": True},
        {"target_ticks": 2.5},
        {"stop_ticks": None},
    ],
)
def test_out_of_range_or_non_int_ticks_are_refused(ticks: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="bracket"):
        market(**ticks)


def test_the_tick_bounds_are_inclusive() -> None:
    assert market(stop_ticks=1, target_ticks=MAX_BRACKET_TICKS).bracket_ticks == (
        1,
        MAX_BRACKET_TICKS,
    )


def test_a_market_entry_with_a_price_is_refused() -> None:
    with pytest.raises(ValueError, match="no entry, stop or target price"):
        EntryIntent("fse-0a1b2c3d-000001", MNQ, "buy", 1, "market", 100, None, None, (4, 8))
    with pytest.raises(ValueError, match="no entry, stop or target price"):
        EntryIntent("fse-0a1b2c3d-000001", MNQ, "buy", 1, "market", None, 90, None, (4, 8))


def test_a_market_entry_without_bracket_ticks_is_refused() -> None:
    with pytest.raises(ValueError, match="bracket"):
        EntryIntent("fse-0a1b2c3d-000001", MNQ, "buy", 1, "market", None, None, None)


def test_a_limit_entry_with_bracket_ticks_or_a_missing_price_is_refused() -> None:
    with pytest.raises(ValueError, match="only a market entry"):
        EntryIntent("fse-0a1b2c3d-000001", MNQ, "buy", 1, "limit", 100, 90, 120, (10, 20))
    with pytest.raises(ValueError, match="entry, stop and target price"):
        EntryIntent("fse-0a1b2c3d-000001", MNQ, "buy", 1, "limit", 100, None, 120)


def test_an_unknown_kind_is_refused() -> None:
    with pytest.raises(ValueError, match="limit, stop or market"):
        EntryIntent("fse-0a1b2c3d-000001", MNQ, "buy", 1, "trail", 100, 90, 120)  # type: ignore[arg-type]


def test_limit_and_stop_payloads_use_signed_bracket_ticks() -> None:
    # Short limit: stop above entry (+10), take-profit below (-20).
    limit = EntryIntent("fse-0a1b2c3d-000001", MNQ, "sell", 1, "limit", 100, 110, 80)
    body = limit.payload(7)
    assert body["type"] == 1
    assert body["limitPrice"] == 25.0
    assert body["stopPrice"] is None
    assert body["stopLossBracket"] == {"ticks": 10, "type": 4}
    assert body["takeProfitBracket"] == {"ticks": -20, "type": 1}
    # Long stop: stop below entry (-10), take-profit above (+20).
    stop = EntryIntent("fse-0a1b2c3d-000001", MNQ, "buy", 1, "stop", 100, 90, 120)
    assert stop.payload(7)["type"] == 4
    assert stop.payload(7)["stopPrice"] == 25.0
    assert stop.payload(7)["stopLossBracket"] == {"ticks": -10, "type": 4}
    assert stop.payload(7)["takeProfitBracket"] == {"ticks": 20, "type": 1}


# ---------------------------------------------------------------- T5 the wire body


@pytest.mark.asyncio
async def test_the_gateway_receives_the_exact_market_bracket_body(
    respx_router: respx.MockRouter,
) -> None:
    gateway = FakeGateway()
    gateway.install(respx_router)
    clock = FakeClock(T0)
    env = EnvView({**CREDS, "PRACTICE_ACCOUNT_ID": "101"}, {})
    async with httpx.AsyncClient() as http:
        session = ProjectXSession(env, Redactor(), http, clock, base_url=BASE)
        root = BrokerAdapter(session, env, clock)
        await clock.run(root.resolve_accounts())
        adapter = root.for_mode("practice")
        result = await clock.run(adapter.place_bracketed(market()))
    assert result.outcome == "accepted"
    assert gateway.place_bodies() == [
        {
            "accountId": 101,
            "contractId": MNQ,
            "type": 2,
            "side": 0,
            "size": 2,
            "limitPrice": None,
            "stopPrice": None,
            "customTag": "fse-0a1b2c3d-000001",
            "stopLossBracket": {"ticks": -40, "type": 4},
            "takeProfitBracket": {"ticks": 80, "type": 1},
        }
    ]
