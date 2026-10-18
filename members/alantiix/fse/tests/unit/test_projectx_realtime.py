"""Unit tests for ``fse.projectx.realtime``: the market hub client over a fake connect.

No socket is opened: :class:`tests.fakes.market_hub.FakeMarketHub` is the
injected connect function and :class:`~tests.fakes.clock.FakeClock` drives
every timeout. The token must never appear in any log line.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from collections.abc import Callable, Coroutine
from typing import Any

import pytest

from fse.logio import LogWriter, Redactor
from fse.logio.log_writer import RedactingFilter
from fse.projectx.realtime import (
    BACKOFF_S,
    HANDSHAKE,
    PING_INTERVAL_S,
    SILENCE_S,
    TOKEN_POLL_S,
    HubQuote,
    HubTrade,
    MarketHubClient,
    hub_logger,
    hub_url,
    parse_quote,
    parse_trade,
)
from fse.timekit import NS_PER_SECOND, parse_rfc3339
from tests.fakes.clock import FakeClock
from tests.fakes.market_hub import FakeMarketHub, FakeTokens, quote_payload, trade_payload

S = NS_PER_SECOND
T0 = parse_rfc3339("2026-10-12T14:00:00Z")
CID = "CON.F.US.MNQ.Z26"
CID2 = "CON.F.US.MES.Z26"
TS = "2026-10-12T14:00:01.250+00:00"


class Harness:
    def __init__(
        self, plan: list[int | str] | None = None, *, tokens: FakeTokens | None = None
    ) -> None:
        self.clock = FakeClock(T0)
        self.hub = FakeMarketHub(plan=list(plan or []), clock=self.clock)
        self.tokens = tokens or FakeTokens()
        self.quotes: list[HubQuote] = []
        self.trades: list[HubTrade] = []
        self.states: list[tuple[bool, str]] = []
        self.logs: list[dict[str, Any]] = []
        self.client = MarketHubClient(
            self.tokens,
            self.clock,
            rtc_url="https://rtc.example.invalid/hubs/market",
            contract_ids=(CID, CID2),
            connect=self.hub.connect,
            on_quote=lambda q, r: self.quotes.append(q),
            on_trade=lambda t, r: self.trades.append(t),
            on_state=lambda up, d: self.states.append((up, d)),
            log=lambda e: self.logs.append(dict(e)),
        )

    async def drive(
        self, script: Callable[[Harness], Coroutine[Any, Any, None]], *, max_s: int = 600
    ) -> None:
        """Run the client and ``script`` together; the client is cancelled when it ends."""

        async def main() -> None:
            task = asyncio.ensure_future(self.client.run())
            try:
                await script(self)
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

        await self.clock.run(main())

    async def run_to_end(self) -> None:
        await self.clock.run(self.client.run())

    def log_text(self) -> str:
        return "\n".join(json.dumps(e) for e in self.logs)


async def settle(h: Harness, seconds: float = 0.0) -> None:
    """Sleep ``seconds`` of virtual time, then 1 ms so every pushed frame is processed."""
    await h.clock.sleep(int(seconds * S) + 1_000_000)


# ---------------------------------------------------------------- parsing


def test_hub_url_carries_the_token_and_forces_wss() -> None:
    url = hub_url("https://rtc.example.invalid/hubs/market", "a b/c")
    assert url == "wss://rtc.example.invalid/hubs/market?access_token=a%20b%2Fc"
    with pytest.raises(ValueError, match="https"):
        hub_url("http://rtc.example.invalid/hubs/market", "t")
    with pytest.raises(ValueError, match="query"):
        hub_url("wss://rtc.example.invalid/hubs/market?x=1", "t")


def test_parse_quote_validates_ticks_and_timestamp() -> None:
    q = parse_quote(CID, quote_payload(TS, bid=6000.0, ask=6000.25, last=6000.5))
    assert q == HubQuote(CID, parse_rfc3339(TS), 24000, 24001, 24002)
    assert parse_quote(CID, quote_payload(TS, bid=6000.1)) is None  # off the tick grid
    assert parse_quote(CID, quote_payload("2026-10-12 14:00:01")) is None  # no offset
    assert parse_quote(CID, {**quote_payload(TS), "lastPrice": float("nan")}) is None
    assert parse_quote(CID, {"timestamp": TS}) is None  # no price at all
    partial = parse_quote(CID, {"timestamp": TS, "lastPrice": 6000.0})
    assert partial is not None
    assert partial.bid_t is None
    assert partial.last_t == 24000
    assert parse_quote(CID, ["not", "a", "dict"]) is None


def test_parse_trade_validates() -> None:
    assert parse_trade(CID, trade_payload(TS, 6000.75)) == HubTrade(CID, parse_rfc3339(TS), 24003)
    assert parse_trade(CID, trade_payload(TS, 6000.3)) is None
    assert parse_trade(CID, {"price": 6000.0}) is None
    assert parse_trade(CID, {"price": True, "timestamp": TS}) is None


# ---------------------------------------------------------------- the connection


@pytest.mark.asyncio
async def test_handshake_subscribe_and_frames() -> None:
    h = Harness()

    async def script(h: Harness) -> None:
        await settle(h)
        conn = h.hub.last
        assert conn.sent[0] == HANDSHAKE
        assert conn.subscriptions == [
            ("SubscribeContractQuotes", CID),
            ("SubscribeContractTrades", CID),
            ("SubscribeContractQuotes", CID2),
            ("SubscribeContractTrades", CID2),
        ]
        conn.push_quote(CID, quote_payload(TS))
        # one frame holding two records: a ping and a list-of-trades message
        conn.push_record(
            {"type": 6},
            {
                "type": 1,
                "target": "GatewayTrade",
                "arguments": [CID2, [trade_payload(TS), trade_payload(TS, 6001.0)]],
            },
        )
        conn.push_record({"type": 1, "target": "GatewayDepth", "arguments": [CID, {}]})
        await settle(h)

    await h.drive(script)
    assert [q.contract_id for q in h.quotes] == [CID]
    assert [t.price_t for t in h.trades] == [24000, 24004]
    assert h.client.dropped == 0
    assert h.states[0] == (True, "connected")
    assert h.client.connects == 1


@pytest.mark.asyncio
async def test_invalid_payloads_are_dropped_and_counted() -> None:
    h = Harness()

    async def script(h: Harness) -> None:
        await settle(h)
        conn = h.hub.last
        conn.push_quote(CID, quote_payload(TS, bid=6000.1))  # off-tick
        conn.push_quote("CON.F.US.ES.Z26", quote_payload(TS))  # not subscribed
        conn.push_trade(CID, {"price": 6000.0, "timestamp": "yesterday"})
        conn.push("not json" + "\x1e")
        conn.push_quote(CID, quote_payload(TS))
        await settle(h)

    await h.drive(script)
    assert len(h.quotes) == 1
    assert h.client.dropped == 4
    assert "6000.1" not in h.log_text()  # payloads are never logged


@pytest.mark.asyncio
async def test_pings_every_15_s_and_silence_reconnects() -> None:
    h = Harness()

    async def script(h: Harness) -> None:
        await settle(h)
        first = h.hub.last
        await settle(h, PING_INTERVAL_S * 3.5)  # answered pings keep it alive
        assert first.pings >= 3
        assert not first.closed
        first.silent = True
        await settle(h, SILENCE_S)  # nothing received for 30 s
        await settle(h, BACKOFF_S[0] + 0.5)
        assert len(h.hub.connections) == 2
        assert first.closed
        assert h.hub.last.subscriptions  # resubscribed

    await h.drive(script)
    assert (False, f"no frame for {SILENCE_S} s") in h.states
    assert h.states.count((True, "connected")) == 2


@pytest.mark.asyncio
async def test_close_message_reconnects() -> None:
    h = Harness()

    async def script(h: Harness) -> None:
        await settle(h)
        h.hub.last.push_record({"type": 7})
        await settle(h, BACKOFF_S[0] + 0.5)
        assert len(h.hub.connections) == 2

    await h.drive(script)
    assert (False, "close message") in h.states


@pytest.mark.asyncio
async def test_backoff_sequence_on_connect_failures() -> None:
    h = Harness(["hang", "error_handshake", "no_handshake", "hang", "hang", "hang", "ok"])

    async def script(h: Harness) -> None:
        start = h.clock.now()
        while h.client.connects == 0:
            await settle(h, 1)
        assert h.clock.now() - start < 300 * S

    await h.drive(script)
    # hang (10 s timeout) +1, refused at once +2, no reply (10 s) +5, hang +10, hang +30, hang +30
    offsets = [(t - T0) // S for t in h.hub.attempt_times]
    assert offsets == [0, 11, 13, 28, 48, 88, 128]
    reasons = [e["reason"] for e in h.logs if e["event"] == "market_hub_connect_failed"]
    assert reasons[:3] == [
        "connect timed out",
        "handshake refused",
        "no handshake reply within 10 s",
    ]


@pytest.mark.asyncio
async def test_401_refreshes_once_then_two_refusals_mean_down_for_the_day() -> None:
    h = Harness([401, 403])
    await h.run_to_end()  # returns: quotes are down for the day
    assert h.tokens.refreshes == ["fake-hub-token-0000"]
    assert h.hub.attempts == 2
    assert h.states[-1][0] is False
    assert "down for the day" in h.states[-1][1]
    down = [e for e in h.logs if e["event"] == "market_hub_down_for_day"]
    assert down
    assert down[0]["level"] == "error"


@pytest.mark.asyncio
async def test_401_then_success_keeps_running() -> None:
    h = Harness([401])

    async def script(h: Harness) -> None:
        while h.client.connects == 0:
            await settle(h, 1)

    await h.drive(script)
    assert h.tokens.refreshes == ["fake-hub-token-0000"]
    assert h.client.connected


@pytest.mark.asyncio
async def test_login_refusal_ends_the_day() -> None:
    h = Harness(tokens=FakeTokens(refuse_login=True))
    await h.run_to_end()
    assert h.hub.attempts == 0
    assert "down for the day" in h.states[-1][1]


@pytest.mark.asyncio
async def test_token_rotation_is_make_before_break() -> None:
    h = Harness()

    async def script(h: Harness) -> None:
        await settle(h)
        old = h.hub.last
        await settle(h, TOKEN_POLL_S + 1)  # same token: no new connection
        assert len(h.hub.connections) == 1
        h.tokens.value = "fake-hub-token-0001"
        await settle(h, TOKEN_POLL_S)
        assert len(h.hub.connections) == 2
        new = h.hub.last
        assert new.token == "fake-hub-token-0001"
        assert new.subscriptions == old.subscriptions  # no gap in subscriptions
        assert old.closed
        assert not new.closed
        new.push_quote(CID, quote_payload(TS))
        await settle(h)

    await h.drive(script)
    assert len(h.quotes) == 1
    assert any(e["event"] == "market_hub_token_rotated" for e in h.logs)
    # the state never went down during the rotation
    assert all(up for up, _ in h.states)


@pytest.mark.asyncio
async def test_failed_rotation_keeps_the_old_connection() -> None:
    h = Harness(["ok", 401])

    async def script(h: Harness) -> None:
        await settle(h)
        old = h.hub.last
        h.tokens.value = "fake-hub-token-0001"
        await settle(h, TOKEN_POLL_S + 1)
        assert not old.closed
        assert h.client.connected

    await h.drive(script)
    assert any(e["event"] == "market_hub_rotation_failed" for e in h.logs)


@pytest.mark.asyncio
async def test_the_token_is_never_in_any_log_line() -> None:
    tokens = FakeTokens(value="fake-secret-hub-token-XYZ")
    h = Harness([401, "hang", "ok"], tokens=tokens)

    async def script(h: Harness) -> None:
        while h.client.connects == 0:
            await settle(h, 1)
        h.hub.last.drop()
        await settle(h, 5)
        h.tokens.value = "fake-secret-hub-token-ROTATED"
        await settle(h, TOKEN_POLL_S + 1)

    await h.drive(script)
    text = h.log_text() + "".join(d for _, d in h.states)
    assert h.logs
    assert "fake-secret-hub-token" not in text
    assert "rtc.example.invalid" not in text  # the URL is never logged
    assert "access_token" not in text


def test_hub_logger_is_capped_and_redacted() -> None:
    redactor = Redactor()
    redactor.add("fake-secret-hub-token-XYZ")
    writer = LogWriter(redactor, stdout=io.StringIO(), stderr=io.StringIO())
    logger = hub_logger(writer)
    hub_logger(writer)  # idempotent
    try:
        assert logger.level == logging.WARNING
        filters = [f for f in logger.filters if isinstance(f, RedactingFilter)]
        assert len(filters) == 1
        record = logger.makeRecord(
            "websockets",
            logging.WARNING,
            __file__,
            1,
            "failed wss://h/hubs/market?access_token=%s",
            ("fake-secret-hub-token-XYZ",),
            None,
        )
        assert filters[0].filter(record)
        assert "fake-secret-hub-token-XYZ" not in record.getMessage()
    finally:
        for f in list(logger.filters):
            logger.removeFilter(f)
        logger.setLevel(logging.NOTSET)
