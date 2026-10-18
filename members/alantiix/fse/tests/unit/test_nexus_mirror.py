"""The opt-in Nexus mirror (paper-analytics §5, H-1 to H-9): mocked transport, no network.

Mirror-level tests drive :class:`NexusMirror` on a :class:`FakeClock`; host-level
tests run a :class:`PluginHost` with a fake transport and compare it with a run
without the mirror.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from fse.commands.run import _nexus
from fse.live.host.config import AccountEntry
from fse.live.host.host import HostResources
from fse.live.host.nexus import (
    BODY_MAX_BYTES,
    CLOSE_FIELDS,
    OPEN_FIELDS,
    HttpNexusTransport,
    MirrorFill,
    NexusMirror,
    NexusResponse,
    RateLimiter,
    client_order_id,
    request_for,
    resolve_nexus_accounts,
)
from fse.logio import LogWriter
from fse.logio.redact import Redactor
from fse.secrets.env import EnvView
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, Instant
from tests.fakes.clock import FakeClock
from tests.fakes.dataplane import (
    FakeDataPlane,
    host_entry,
    make_host,
    ny,
    read_jsonl,
    runner_config,
)
from tests.fakes.plugins import RecordingPlugin
from tests.unit.test_plugin_host_accounts import (
    THU,
    WED,
    Run,
    account,
    bar,
    enter,
    entries_on_bar,
    flat_bars,
    host_log,
    run_host,
)

T0 = ny(WED, 10, 0)
HOUR = 60 * NS_PER_MINUTE
ENV_ACCT = "env:SKYLIT_NEXUS_ACCT"
OK = NexusResponse(200, {"data": {"fillPrice": "20000.25", "filledAt": "2026-10-14T14:01:00Z"}})
SRC = Path(__file__).resolve().parents[2] / "src"


def err(status: int, code: str, retry_after: str | None = None) -> NexusResponse:
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    return NexusResponse(status, {"error": {"code": code, "message": "x"}}, headers)


type Reply = NexusResponse | Exception | None
"""A response, an exception to raise, or ``None``: hang until cancelled."""


class FakeNexus:
    """Scripted replies (then ``default``); records ``(t, path, body)`` of every call."""

    def __init__(self, clock: FakeClock, *replies: Reply, default: Reply = OK) -> None:
        self.clock = clock
        self.replies: deque[Reply] = deque(replies)
        self.default = default
        self.calls: list[tuple[Instant, str, dict[str, Any]]] = []

    async def post(self, path: str, body: Mapping[str, Any]) -> NexusResponse:
        self.calls.append((self.clock.now(), path, dict(body)))
        reply = self.replies.popleft() if self.replies else self.default
        if reply is None:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")
        if isinstance(reply, Exception):
            raise reply
        return reply

    @property
    def ids(self) -> list[Any]:
        return [b.get("clientOrderId", f"close:{p}") for _, p, b in self.calls]


def entries(*specs: tuple[str, str, bool]) -> tuple[AccountEntry, ...]:
    """``(id, nexus account, test_only)`` -> mirrored account entries (one plugin each)."""
    plugins = [host_entry(f"p{i}", account=a) for i, (a, _, _) in enumerate(specs)]
    accts = [
        {"id": a, "preset": "none", "nexus": {"enabled": True, "account": n, "test_only": t}}
        for a, n, t in specs
    ]
    return runner_config(plugins, accounts=accts).accounts


class Rig:
    def __init__(
        self,
        tmp_path: Path,
        *replies: Reply,
        specs: Sequence[tuple[str, str, bool]] = (("a", "practice", False),),
        resolved: Mapping[str, str] | None = None,
        disabled: Mapping[str, str] | None = None,
        default: Reply = OK,
        no_transport: bool = False,
        next_open: Callable[[Instant], Instant] | None = None,
        start: Instant = T0,
    ) -> None:
        self.clock = FakeClock(start)
        self.transport = FakeNexus(self.clock, *replies, default=default)
        self.log: list[dict[str, Any]] = []
        self.root = tmp_path / "live-state"
        accts = entries(*specs)
        if resolved is None:
            resolved = {a.id: a.nexus.account for a in accts}

        async def offload(fn: Callable[[], object]) -> object:
            return fn()

        self.mirror = NexusMirror(
            accts,
            state_root=self.root,
            transport=None if no_transport else self.transport,
            resolved=resolved,
            disabled=disabled or {},
            writer=LogWriter(Redactor(), stdout=io.StringIO(), stderr=io.StringIO()),
            clock=self.clock,
            log=lambda line: self.log.append(dict(line)),
            offload=offload,
            next_open_ns=next_open or (lambda now: now + HOUR),
        )

    def drive(self, until: int, steps: Sequence[tuple[int, Callable[[], None]]] = ()) -> None:
        """``load()``, run the mirror task, call each step at its offset, stop at ``until``."""

        async def main() -> None:
            self.mirror.load()
            task = asyncio.get_running_loop().create_task(self.mirror.run())
            t = 0
            for at, fn in sorted(steps, key=lambda s: s[0]):
                if at > t:
                    await self.clock.sleep(at - t)
                    t = at
                fn()
                await asyncio.sleep(0)
            await self.clock.sleep(until - t)
            task.cancel()
            with contextlib.suppress(BaseException):
                await task

        asyncio.run(self.clock.run(main(), max_steps=1_000_000))

    def outbox(self, acct: str = "a") -> list[dict[str, Any]]:
        return read_jsonl(self.root / "accounts" / acct / "nexus_outbox.jsonl")

    def acks(self, acct: str = "a") -> dict[int, dict[str, Any]]:
        return {ln["seq"]: ln for ln in self.outbox(acct) if ln["op"] == "ack"}

    def status(self, acct: str = "a") -> dict[str, Any]:
        doc: dict[str, Any] = json.loads(
            (self.root / "accounts" / acct / "nexus_status.json").read_text(encoding="utf-8")
        )
        return doc

    def results(self) -> list[tuple[Any, ...]]:
        return [
            (ln["tag"], ln["leg"], ln["result"], ln["error_code"])
            for ln in self.log
            if ln["kind"] == "nexus_result"
        ]


def mf(
    tag: str,
    leg: str = "entry",
    qty: int = 1,
    *,
    direction: str = "long",
    plugin: str = "p0",
    instrument: str = "MNQ",
    day: date = WED,
    price_t: int = 80_000,
) -> MirrorFill:
    return MirrorFill(
        plugin=plugin,
        tag=tag,
        leg=leg,
        entry=leg == "entry",
        instrument=instrument,
        direction=direction,  # type: ignore[arg-type]
        qty=qty,
        price_t=price_t,
        fill_ns=T0,
        day=day,
    )


def enq(rig: Rig, fill: MirrorFill, position: int, acct: str = "a") -> Callable[[], None]:
    """Enqueue ``fill`` with the plugin's MNQ position after it."""
    return lambda: rig.mirror.enqueue_fills(acct, [fill], {fill.instrument: position})


def together(*steps: Callable[[], None]) -> Callable[[], None]:
    """Several enqueues with no yield between them (the mirror sees them queued at once)."""

    def run() -> None:
        for step in steps:
            step()

    return run


# ---------------------------------------------------------------- H-1 bodies


def test_open_reduce_and_close_bodies_use_only_the_allowed_keys(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.drive(
        10 * NS_PER_SECOND,
        [
            (0, enq(rig, mf("t1", qty=2), 2)),
            (1, enq(rig, mf("t1", "tp1"), 1)),
            (2, enq(rig, mf("t1", "stop"), 0)),
        ],
    )
    assert [(p, b) for _, p, b in rig.transport.calls] == [
        (
            "/trades",
            {
                "assetClass": "futures",
                "ticker": "MNQ",
                "side": "buy",
                "quantity": 2,
                "clientOrderId": "fse:20261014:p0:t1:entry",
            },
        ),
        (
            "/trades",
            {
                "assetClass": "futures",
                "ticker": "MNQ",
                "side": "sell",
                "quantity": 1,
                "clientOrderId": "fse:20261014:p0:t1:tp1",
                "reduceOnly": True,
            },
        ),
        ("/trading/accounts/practice/close", {"ticker": "MNQ"}),
    ]
    for _, path, body in rig.transport.calls:
        allowed = CLOSE_FIELDS if path.endswith("/close") else OPEN_FIELDS
        assert set(body) <= set(allowed)
        assert len(json.dumps(body).encode()) < BODY_MAX_BYTES
    assert rig.results() == [
        ("t1", "entry", "filled", None),
        ("t1", "tp1", "filled", None),
        ("t1", "stop", "filled", None),
    ]
    assert rig.status()["queue_depth"] == 0


def test_a_short_entry_sells_and_its_exit_buys(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.drive(
        5 * NS_PER_SECOND,
        [
            (0, enq(rig, mf("s1", qty=3, direction="short"), -3)),
            (1, enq(rig, mf("s1", "tp1", 1, direction="short"), -2)),
        ],
    )
    assert [(b["side"], b.get("reduceOnly")) for _, _, b in rig.transport.calls] == [
        ("sell", None),
        ("buy", True),
    ]


def test_test_only_adds_test_and_a_named_account(tmp_path: Path) -> None:
    rig = Rig(tmp_path, specs=(("a", "agent", True),))
    rig.drive(5 * NS_PER_SECOND, [(0, enq(rig, mf("t1"), 1)), (1, enq(rig, mf("t1", "stop"), 0))])
    bodies = [b for _, _, b in rig.transport.calls]
    assert [p for _, p, _ in rig.transport.calls] == ["/trades", "/trades"]
    assert all(b["test"] is True and b["account"] == "agent" for b in bodies)
    assert [b["side"] for b in bodies] == ["buy", "sell"]


def test_request_for_refuses_an_unknown_op_and_an_oversized_body() -> None:
    with pytest.raises(ValueError, match="unknown outbox op"):
        request_for({"op": "cancel"}, "practice")
    with pytest.raises(ValueError, match="8 KB"):
        request_for({"op": "close", "ticker": "M" * BODY_MAX_BYTES}, "practice")
    path, body = request_for({"op": "close", "ticker": "MNQ", "clientOrderId": "x"}, "a b/c")
    assert (path, body) == ("/trading/accounts/a%20b%2Fc/close", {"ticker": "MNQ"})


# ---------------------------------------------------------------- H-2 clientOrderId


def test_client_order_id_is_deterministic_bounded_and_in_charset() -> None:
    cid = client_order_id(WED, "alpha", "s1", "tp1")
    assert cid == "fse:20261014:alpha:s1:tp1"
    assert client_order_id(WED, "alpha", "s1", "tp1") == cid
    assert client_order_id(date(2026, 10, 15), "alpha", "s1", "tp1") != cid
    long_tag = "t" * 80
    hashed = client_order_id(WED, "alpha", long_tag, "entry")
    assert hashed == client_order_id(WED, "alpha", long_tag, "entry")
    assert hashed.startswith("fse:")
    assert len(hashed) == 44
    odd = client_order_id(WED, "alpha", "a b", "entry")  # a space is outside the charset
    assert odd.startswith("fse:")
    assert len(odd) == 44
    for c in (cid, hashed, odd):
        assert len(c) <= 64
        assert all(ch.isalnum() or ch in "_.:-" for ch in c)


# ---------------------------------------------------------------- H-3 retries and the rate limit


def test_429_retries_after_retry_after_with_the_same_id_and_pauses_every_account(
    tmp_path: Path,
) -> None:
    rig = Rig(
        tmp_path,
        err(429, "rate_limited", "7"),
        specs=(("a", "practice", False), ("b", "evaluation", False)),
    )
    rig.drive(
        20 * NS_PER_SECOND,
        [
            (0, enq(rig, mf("t1"), 1, "a")),
            (0, lambda: rig.mirror.enqueue_fills("b", [mf("u1", plugin="p1")], {"MNQ": 1})),
        ],
    )
    calls = rig.transport.calls
    assert [(t - T0, b["clientOrderId"]) for t, _, b in calls] == [
        (0, "fse:20261014:p0:t1:entry"),
        (7 * NS_PER_SECOND, "fse:20261014:p0:t1:entry"),
        (7 * NS_PER_SECOND, "fse:20261014:p1:u1:entry"),
    ]
    assert rig.acks("a")[1]["result"] == "filled"


def test_503_backs_off_1_then_2_seconds_with_the_same_id(tmp_path: Path) -> None:
    rig = Rig(tmp_path, err(503, "unavailable"), err(503, "stale_quote"))
    rig.drive(10 * NS_PER_SECOND, [(0, enq(rig, mf("t1"), 1))])
    assert [t - T0 for t, _, _ in rig.transport.calls] == [0, NS_PER_SECOND, 3 * NS_PER_SECOND]
    assert len(set(rig.transport.ids)) == 1
    assert rig.results() == [
        ("t1", "entry", "retry", "unavailable"),
        ("t1", "entry", "retry", "stale_quote"),
        ("t1", "entry", "filled", None),
    ]


def test_burst_and_minute_limits_hold_across_two_accounts(tmp_path: Path) -> None:
    rig = Rig(tmp_path, specs=(("a", "practice", False), ("b", "evaluation", False)))

    def load() -> None:
        for i in range(20):
            rig.mirror.enqueue_fills("a", [mf(f"t{i}")], {"MNQ": i + 1})
            rig.mirror.enqueue_fills("b", [mf(f"u{i}", plugin="p1")], {"MNQ": i + 1})

    rig.drive(3 * 60 * NS_PER_SECOND, [(0, load)])
    times = [t for t, _, _ in rig.transport.calls]
    assert len(times) == 40
    for i, t in enumerate(times):
        assert sum(1 for u in times[i:] if u < t + 5 * NS_PER_SECOND) <= 5
        assert sum(1 for u in times[i:] if u < t + 60 * NS_PER_SECOND) <= 30


def test_rate_limiter_pause_and_windows() -> None:
    lim = RateLimiter()
    for i in range(5):
        assert lim.delay(i) == 0
        lim.record(i)
    assert lim.delay(5) == 5 * NS_PER_SECOND - 5  # the 6th waits for the first (t=0) to age out
    lim.pause_until(100 * NS_PER_SECOND)
    assert lim.delay(10 * NS_PER_SECOND) == 90 * NS_PER_SECOND


# ---------------------------------------------------------------- H-4 error policy


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "invalid_api_key"),
        (403, "nexus_access_denied"),
        (403, "futures_access_denied"),
        (403, "account_suspended"),
    ],
)
def test_permanent_codes_disable_the_mirror_and_persist(
    tmp_path: Path, status: int, code: str
) -> None:
    rig = Rig(tmp_path, err(status, code))
    rig.drive(
        5 * NS_PER_SECOND,
        [
            (0, together(enq(rig, mf("t1"), 1), enq(rig, mf("t1", "stop"), 0))),
            (2, enq(rig, mf("t2"), 1)),  # after the disable: not queued
        ],
    )
    assert len(rig.transport.calls) == 1
    assert [a["result"] for a in rig.acks().values()] == ["failed", "failed"]
    st = rig.status()
    assert (st["disabled"], st["disabled_reason"], st["disabled_by"]) == (True, code, "nexus")
    assert not rig.mirror.mirrors("a")
    assert {ln["level"] for ln in rig.log if ln["kind"] == "nexus_result"} == {"error"}
    again = Rig(tmp_path)
    again.mirror.load()
    assert not again.mirror.mirrors("a")
    assert not again.mirror.active
    assert again.status()["disabled_reason"] == code


def test_market_closed_waits_for_the_next_open(tmp_path: Path) -> None:
    reopen = T0 + 3 * HOUR
    rig = Rig(tmp_path, err(422, "market_closed"), next_open=lambda now: reopen)
    rig.drive(4 * HOUR, [(0, enq(rig, mf("t1"), 1))])
    assert [t for t, _, _ in rig.transport.calls] == [T0, reopen]
    assert rig.acks()[1]["result"] == "filled"


def test_account_locked_skips_opens_and_keeps_sending_reduces(tmp_path: Path) -> None:
    rig = Rig(tmp_path, OK, err(409, "account_locked"))
    rig.drive(
        30 * NS_PER_SECOND,
        [
            (0, enq(rig, mf("a1"), 1)),
            (0, enq(rig, mf("b1"), 2)),
            (0, enq(rig, mf("c1"), 3)),
            (0, enq(rig, mf("a1", "stop"), 2)),
        ],
    )
    assert rig.transport.ids == [
        "fse:20261014:p0:a1:entry",
        "fse:20261014:p0:b1:entry",
        "fse:20261014:p0:a1:stop",
    ]
    assert rig.transport.calls[-1][2]["reduceOnly"] is True
    assert [a["result"] for _, a in sorted(rig.acks().items())] == [
        "filled",
        "skipped",
        "skipped",
        "filled",
    ]
    assert rig.status()["opens_blocked"] is True
    assert rig.status()["opens_blocked_day"] == WED.isoformat()


def test_the_rotation_lifts_account_locked_and_the_next_entry_is_sent(tmp_path: Path) -> None:
    rig = Rig(tmp_path, err(409, "account_locked"))
    rotate = lambda: rig.mirror.new_day(WED)  # noqa: E731
    rig.drive(
        30 * NS_PER_SECOND,
        [
            (0, enq(rig, mf("a1"), 1)),
            (5, rotate),
            (10, enq(rig, mf("b1", day=THU), 1)),
        ],
    )
    assert rig.transport.ids == ["fse:20261014:p0:a1:entry", "fse:20261015:p0:b1:entry"]
    assert [a["result"] for _, a in sorted(rig.acks().items())] == ["skipped", "filled"]
    [line] = [ln for ln in rig.log if ln["kind"] == "nexus_opens_unblocked"]
    assert (line["account"], line["day"], line["level"]) == ("a", WED.isoformat(), "info")
    assert rig.status()["opens_blocked"] is False
    assert rig.status()["opens_blocked_day"] is None


def test_account_locked_survives_a_restart_on_its_day_only(tmp_path: Path) -> None:
    rig = Rig(tmp_path, err(409, "account_locked"))
    rig.drive(5 * NS_PER_SECOND, [(0, enq(rig, mf("a1"), 1))])
    same_day = Rig(tmp_path, start=ny(WED, 16, 0))
    same_day.mirror.load()
    assert same_day.mirror.status("a")["opens_blocked"] is True
    next_day = Rig(tmp_path, start=ny(WED, 18, 30))  # Globex day Thu
    next_day.mirror.load()
    assert next_day.mirror.status("a")["opens_blocked"] is False


@pytest.mark.parametrize("restart", [(date(2026, 10, 17), 12), (date(2026, 10, 18), 17)])
def test_a_friday_lock_is_not_restored_by_a_weekend_restart(
    tmp_path: Path, restart: tuple[date, int]
) -> None:
    """The Sunday 18:00 open runs no rotation, so a weekend restart must not keep the block."""
    fri, mon = date(2026, 10, 16), date(2026, 10, 19)
    rig = Rig(tmp_path, err(409, "account_locked"), start=ny(fri, 10, 0))
    rig.drive(5 * NS_PER_SECOND, [(0, enq(rig, mf("a1", day=fri), 1))])
    assert rig.status()["opens_blocked_day"] == fri.isoformat()
    on_day, hour = restart
    weekend = Rig(tmp_path, start=ny(on_day, hour, 0))
    weekend.drive(5 * NS_PER_SECOND, [(0, enq(weekend, mf("m1", day=mon), 1))])
    assert weekend.transport.ids == ["fse:20261019:p0:m1:entry"]
    assert weekend.acks()[2]["result"] == "filled"
    assert weekend.status()["opens_blocked"] is False


def test_once_per_day_logs_are_keyed_on_the_globex_day(tmp_path: Path) -> None:
    """23:00 and 01:00 NY are one Globex day (Thu): one error line, then warnings."""
    rig = Rig(
        tmp_path,
        err(422, "contract_not_offered"),
        err(422, "contract_not_offered"),
        start=ny(WED, 23, 0),
    )
    rig.drive(
        3 * HOUR,
        [(0, enq(rig, mf("t1", day=THU), 1)), (2 * HOUR, enq(rig, mf("t2", day=THU), 1))],
    )
    levels = [ln["level"] for ln in rig.log if ln["kind"] == "nexus_result"]
    assert levels == ["error", "warning"]


def test_account_ended_stops_the_mirror(tmp_path: Path) -> None:
    rig = Rig(tmp_path, err(409, "account_ended"))
    rig.drive(
        5 * NS_PER_SECOND,
        [
            (0, together(enq(rig, mf("t1"), 1), enq(rig, mf("t2"), 2))),
            (2, enq(rig, mf("t3"), 3)),
        ],
    )
    assert len(rig.transport.calls) == 1
    assert [a["result"] for _, a in sorted(rig.acks().items())] == ["failed", "failed"]
    assert rig.status()["ended"] is True
    assert [ln["seq"] for ln in rig.outbox() if ln["op"] == "enqueue"] == [1, 2]  # t3 not queued
    again = Rig(tmp_path)  # a fresh mirror reloads the flag
    again.mirror.load()
    assert not again.mirror.mirrors("a")


def test_request_in_progress_retries_after_one_second(tmp_path: Path) -> None:
    rig = Rig(tmp_path, err(409, "request_in_progress"))
    rig.drive(5 * NS_PER_SECOND, [(0, enq(rig, mf("t1"), 1))])
    assert [t - T0 for t, _, _ in rig.transport.calls] == [0, NS_PER_SECOND]


def test_client_order_id_used_fails_without_a_retry(tmp_path: Path) -> None:
    rig = Rig(tmp_path, err(409, "client_order_id_used"))
    rig.drive(30 * NS_PER_SECOND, [(0, enq(rig, mf("t1"), 1))])
    assert len(rig.transport.calls) == 1
    [line] = [ln for ln in rig.log if ln["kind"] == "nexus_result"]
    assert (line["result"], line["error_code"], line["level"]) == (
        "failed",
        "client_order_id_used",
        "error",
    )


def test_contract_not_offered_fails_the_item_and_skips_its_exits(tmp_path: Path) -> None:
    rig = Rig(tmp_path, err(422, "contract_not_offered"))
    rig.drive(30 * NS_PER_SECOND, [(0, enq(rig, mf("t1"), 1)), (0, enq(rig, mf("t1", "stop"), 0))])
    assert len(rig.transport.calls) == 1
    assert rig.results() == [
        ("t1", "entry", "failed", "contract_not_offered"),
        ("t1", "stop", "skipped", "entry_not_mirrored"),
    ]
    assert rig.status()["last_error"]["code"] == "entry_not_mirrored"
    failed = next(ln for ln in rig.log if ln.get("error_code") == "contract_not_offered")
    assert failed["level"] == "error"
    assert rig.mirror.mirrors("a")  # only the item failed


def test_contract_not_offered_names_the_ticker_in_the_status(tmp_path: Path) -> None:
    rig = Rig(tmp_path, err(422, "contract_not_offered"))
    rig.drive(5 * NS_PER_SECOND, [(0, enq(rig, mf("t1"), 1))])
    assert rig.status()["last_error"]["ticker"] == "MNQ"


def test_an_item_older_than_23_hours_fails(tmp_path: Path) -> None:
    rig = Rig(tmp_path, default=err(503, "no_live_price"))
    rig.drive(24 * HOUR, [(0, enq(rig, mf("t1"), 1))])
    ack = rig.acks()[1]
    assert (ack["result"], ack["error_code"]) == ("failed", "no_live_price")
    assert ack["t_ns"] - T0 >= 23 * HOUR
    assert rig.transport.calls[-1][0] - T0 < 23 * HOUR


# ---------------------------------------------------------------- H-5 restart, symbolic account


def test_restart_resends_only_unacked_items_with_the_same_ids(tmp_path: Path) -> None:
    resolved = {"a": "acct-777"}
    first = Rig(tmp_path, OK, None, specs=(("a", ENV_ACCT, False),), resolved=resolved)
    first.drive(
        10 * NS_PER_SECOND,
        [
            (0, enq(first, mf("t1"), 1)),
            (0, enq(first, mf("t2"), 2)),
            (0, enq(first, mf("t1", "stop"), 1)),
            (0, enq(first, mf("t2", "stop"), 0)),
        ],
    )
    assert len(first.transport.calls) == 2  # t1 sent, t2 hangs and is cancelled
    assert set(first.acks()) == {1}
    replay = NexusResponse(200, {"data": {}, "meta": {"idempotentReplay": True}})
    second = Rig(tmp_path, replay, specs=(("a", ENV_ACCT, False),), resolved=resolved)
    second.drive(10 * NS_PER_SECOND)
    assert second.transport.ids == [
        "fse:20261014:p0:t2:entry",
        "fse:20261014:p0:t1:stop",
        "close:/trading/accounts/acct-777/close",
    ]
    assert first.transport.ids[1] == second.transport.ids[0]
    assert all(b["account"] == "acct-777" for _, p, b in second.transport.calls if p == "/trades")
    acks = second.acks()
    assert sorted(acks) == [1, 2, 3, 4]
    assert acks[2]["result"] == "filled"
    assert acks[2]["nexus"]["idempotent_replay"] is True
    text = (tmp_path / "live-state" / "accounts" / "a" / "nexus_outbox.jsonl").read_text()
    status = (tmp_path / "live-state" / "accounts" / "a" / "nexus_status.json").read_text()
    for blob in (text, status, json.dumps(first.log), json.dumps(second.log)):
        assert "acct-777" not in blob
        assert "[REDACTED]" not in blob
    assert ENV_ACCT in text
    assert json.loads(status)["nexus_account"] == ENV_ACCT


def test_a_replayed_fill_is_queued_once(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.drive(5 * NS_PER_SECOND, [(0, enq(rig, mf("t1"), 1)), (1, enq(rig, mf("t1"), 1))])
    assert len(rig.transport.calls) == 1
    again = Rig(tmp_path)
    again.drive(5 * NS_PER_SECOND, [(0, enq(again, mf("t1"), 1))])
    assert again.transport.calls == []


# ---------------------------------------------------------------- H-6 ordering


def test_an_exit_waits_for_its_entry(tmp_path: Path) -> None:
    rig = Rig(tmp_path, err(503, "unavailable"), err(503, "unavailable"))
    rig.drive(10 * NS_PER_SECOND, [(0, enq(rig, mf("t1"), 1)), (0, enq(rig, mf("t1", "stop"), 0))])
    assert rig.transport.ids == [
        "fse:20261014:p0:t1:entry",
        "fse:20261014:p0:t1:entry",
        "fse:20261014:p0:t1:entry",
        "close:/trading/accounts/practice/close",
    ]


def test_the_exit_of_a_failed_entry_is_skipped_and_logged(tmp_path: Path) -> None:
    rig = Rig(tmp_path, err(400, "invalid_request"))
    rig.drive(5 * NS_PER_SECOND, [(0, enq(rig, mf("t1"), 1)), (0, enq(rig, mf("t1", "tp1"), 0))])
    assert len(rig.transport.calls) == 1
    assert rig.results() == [
        ("t1", "entry", "failed", "invalid_request"),
        ("t1", "tp1", "skipped", "entry_not_mirrored"),
    ]


# ---------------------------------------------------------------- resolution and config


def test_an_unset_env_var_disables_the_mirror_naming_only_the_variable(tmp_path: Path) -> None:
    accts = entries(("a", ENV_ACCT, False))
    resolved, disabled = resolve_nexus_accounts(accts, EnvView({}, {}))
    assert (resolved, disabled) == ({}, {"a": "env var SKYLIT_NEXUS_ACCT not set"})
    rig = Rig(tmp_path, specs=(("a", ENV_ACCT, False),), resolved=resolved, disabled=disabled)
    rig.drive(NS_PER_SECOND, [(0, enq(rig, mf("t1"), 1))])
    assert rig.transport.calls == []
    assert rig.status()["disabled_reason"] == "env var SKYLIT_NEXUS_ACCT not set"
    [line] = [ln for ln in rig.log if ln["kind"] == "nexus_disabled"]
    assert line["reason"] == "env var SKYLIT_NEXUS_ACCT not set"
    assert not (rig.root / "accounts" / "a" / "nexus_outbox.jsonl").exists()


def test_two_env_refs_resolving_to_one_account_disable_both() -> None:
    accts = entries(("a", "env:NX_A", False), ("b", "env:NX_B", False), ("c", "agent", False))
    env = EnvView({"NX_A": "4242", "NX_B": "4242"}, {})
    resolved, disabled = resolve_nexus_accounts(accts, env)
    assert resolved == {"c": "agent"}
    assert disabled == {"a": "same nexus account as b", "b": "same nexus account as a"}
    assert "4242" not in json.dumps(disabled)


def test_no_api_key_reports_disabled_and_sends_nothing(tmp_path: Path) -> None:
    rig = Rig(tmp_path, no_transport=True)
    rig.drive(NS_PER_SECOND, [(0, enq(rig, mf("t1"), 1))])
    assert not rig.mirror.active
    st = rig.status()
    assert (st["disabled"], st["disabled_reason"]) == (True, "no api key")
    [line] = [ln for ln in rig.log if ln["kind"] == "nexus_disabled"]
    assert line["level"] == "warning"


def test_fse_run_builds_a_transport_only_with_a_key_and_an_enabled_mirror() -> None:
    plugins = [host_entry("p0", account="a")]
    on = runner_config(
        plugins, accounts=[{"id": "a", "preset": "none", "nexus": {"enabled": True}}]
    )
    off = runner_config(plugins, accounts=[{"id": "a", "preset": "none"}])
    ctx = SimpleNamespace(env=EnvView({}, {}), writer=None)
    assert _nexus(off, ctx, None) == (None, {}, {})  # type: ignore[arg-type]
    assert _nexus(on, ctx, None) == (None, {"a": "practice"}, {})  # type: ignore[arg-type]
    keyed = SimpleNamespace(env=EnvView({"SKYLIT_API_KEY": "k" * 40}, {}), writer=None)
    with pytest.raises(RuntimeError, match="pytest"):  # the H-9 guard, even with a key
        _nexus(on, keyed, None)  # type: ignore[arg-type]


# ---------------------------------------------------------------- H-9 scope guard


def test_the_http_transport_refuses_under_pytest() -> None:
    with pytest.raises(RuntimeError, match="pytest"):
        HttpNexusTransport(object(), "secret-key")


def test_only_fse_run_builds_the_http_transport() -> None:
    users = sorted(
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if "HttpNexusTransport(" in p.read_text(encoding="utf-8")
    )
    assert users == ["fse/commands/run.py", "fse/live/host/nexus.py"]
    assert HostResources(env=EnvView({}, {})).nexus is None


def test_repr_never_shows_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    writer = LogWriter(Redactor(), stdout=io.StringIO(), stderr=io.StringIO())
    t = HttpNexusTransport(object(), "secret-key-0123456789", writer=writer)
    assert "secret-key" not in repr(t)
    assert writer.redact("x secret-key-0123456789 y") == "x [REDACTED] y"


# ---------------------------------------------------------------- rotation archive


def test_archive_waits_for_acks_and_the_ack_lock(tmp_path: Path) -> None:
    rig = Rig(tmp_path, None)  # the only send hangs
    lane_dir = rig.root / "accounts" / "a"
    archived = lane_dir / "archive" / "nexus_outbox.2026-10-14.jsonl"
    seen: list[bool] = []

    def try_archive() -> None:
        rig.mirror.archive(WED)
        seen.append((lane_dir / "nexus_outbox.jsonl").exists())

    rig.drive(3 * NS_PER_SECOND, [(0, enq(rig, mf("t1"), 1)), (1, try_archive)])
    assert seen == [True]  # pending: not archived

    done = Rig(tmp_path / "acked")
    done_dir = done.root / "accounts" / "a"

    async def locked_then_free() -> None:
        lane = done.mirror._lanes["a"]
        async with lane.lock:
            done.mirror.archive(WED)
            seen.append((done_dir / "nexus_outbox.jsonl").exists())
        done.mirror.archive(WED)
        seen.append((done_dir / "nexus_outbox.jsonl").exists())

    done.drive(2 * NS_PER_SECOND, [(0, enq(done, mf("t1"), 1))])
    asyncio.run(locked_then_free())
    assert seen == [True, True, False]
    assert (done_dir / "archive" / "nexus_outbox.2026-10-14.jsonl").exists()
    assert not archived.exists()


# ---------------------------------------------------------------- host level: H-8, H-F4


RISING = [
    bar(WED, 10, i, 80_000 + 4 * i, 80_005 + 4 * i, 79_999 + 4 * i, 80_004 + 4 * i)
    for i in range(6)
]


def _two_trades(run: Run) -> None:
    entries_on_bar(
        run.plugins["p1"],
        {
            T0: lambda b: [enter("t1", b.close_ns, 1, target=8)],
            T0 + 2 * NS_PER_MINUTE: lambda b: [enter("t2", b.close_ns, 1, target=8)],
        },
    )


def _host_run(
    tmp_path: Path,
    transport: object | None,
    *,
    nexus_account: str = "practice",
    resolved: str = "practice",
    mirror: bool = True,
) -> Run:
    acct = account()
    if mirror:
        acct["nexus"] = {"enabled": True, "account": nexus_account}
    res = HostResources(
        env=EnvView({}, {}),
        nexus=transport,  # type: ignore[arg-type]
        nexus_resolved={"acct": resolved} if mirror else {},
    )
    return run_host(
        tmp_path,
        T0,
        ny(WED, 10, 7),
        bars=RISING,
        setup=_two_trades,
        accounts=[acct],
        resources=res,
    )


def _same_trading(a: Run, b: Run) -> None:
    assert a.plugins["p1"].updates == b.plugins["p1"].updates
    ba, bb = a.host.broker("p1"), b.host.broker("p1")
    assert ba is not None
    assert bb is not None
    assert ba.state == bb.state
    assert a.host.account("acct").state == b.host.account("acct").state


class Raising:
    def __init__(self) -> None:
        self.calls = 0

    async def post(self, path: str, body: Mapping[str, Any]) -> NexusResponse:
        self.calls += 1
        raise RuntimeError(f"connect failed for https://app.skylit.ai/api/nexus/v1{path}")


class Hanging:
    def __init__(self) -> None:
        self.calls = 0

    async def post(self, path: str, body: Mapping[str, Any]) -> NexusResponse:
        self.calls += 1
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


def test_a_raising_or_hanging_transport_never_changes_trading(tmp_path: Path) -> None:
    control = _host_run(tmp_path / "control", None, mirror=False)
    raising, hanging = Raising(), Hanging()
    r = _host_run(tmp_path / "raising", raising)
    h = _host_run(tmp_path / "hanging", hanging)
    _same_trading(control, r)
    _same_trading(control, h)
    assert raising.calls >= 1
    assert hanging.calls == 1
    assert rejected_none(tmp_path / "raising")
    # the transport error is logged by type only, never its text (the URL)
    lines = host_log(tmp_path / "raising", "nexus_result")
    assert lines
    assert {ln["error_code"] for ln in lines} == {"transport"}
    assert {ln.get("detail") for ln in lines} == {"RuntimeError"}
    assert "skylit.ai" not in (tmp_path / "raising" / "run" / "host_log.jsonl").read_text()
    # the hung item and everything after it stay pending in the outbox
    outbox = read_jsonl(
        tmp_path / "hanging" / "live-state" / "accounts" / "acct" / "nexus_outbox.jsonl"
    )
    assert [ln["op"] for ln in outbox] == ["enqueue"] * 4
    stopped = host_log(tmp_path / "hanging", "host_stopped")
    assert stopped


def rejected_none(root: Path) -> bool:
    return not any(
        ln["kind"] == "intent_rejected"
        for ln in read_jsonl(root / "run" / "plugins" / "p1" / "plugin_log.jsonl")
    )


def test_a_numeric_nexus_account_leaves_every_state_file_unchanged(tmp_path: Path) -> None:
    control = _host_run(tmp_path / "control", None, mirror=False)
    transport = FakeNexus(FakeClock(T0))
    mirrored = _host_run(tmp_path / "mirror", transport, nexus_account=ENV_ACCT, resolved="1234")
    _same_trading(control, mirrored)
    assert [p for _, p, _ in transport.calls].count("/trading/accounts/1234/close") == 2
    for rel in (
        "live-state/plugins/p1/plugin.json",
        "live-state/accounts/acct/account.json",
        "run/plugins/p1/trades.jsonl",
    ):
        assert (tmp_path / "mirror" / rel).read_bytes() == (tmp_path / "control" / rel).read_bytes()

    def non_nexus(root: Path) -> list[dict[str, Any]]:
        return [ln for ln in host_log(root) if not str(ln["kind"]).startswith("nexus_")]

    assert non_nexus(tmp_path / "mirror") == non_nexus(tmp_path / "control")
    for path in (tmp_path / "mirror" / "live-state" / "accounts" / "acct").glob("nexus_*"):
        text = path.read_text()
        assert '"1234"' not in text  # the resolved id is never written
        assert ENV_ACCT in text
        assert "[REDACTED]" not in text


def test_an_enabled_mirror_without_a_key_runs_normally(tmp_path: Path) -> None:
    control = _host_run(tmp_path / "control", None, mirror=False)
    run = _host_run(tmp_path / "nokey", None)
    _same_trading(control, run)
    [line] = host_log(tmp_path / "nokey", "nexus_disabled")
    assert (line["level"], line["reason"]) == ("warning", "no api key")
    acct_dir = tmp_path / "nokey" / "live-state" / "accounts" / "acct"
    assert (
        json.loads((acct_dir / "nexus_status.json").read_text())["disabled_reason"] == "no api key"
    )
    assert not (acct_dir / "nexus_outbox.jsonl").exists()


def test_a_multi_day_run_resumes_opens_after_the_rotation(tmp_path: Path) -> None:
    """``account_locked`` on Wed; the 18:00 rotation lifts it and Thursday's entry is sent."""
    wed = flat_bars(WED, 10, 0, 3)
    thu = flat_bars(WED, 18, 30, 3)  # Globex day Thu
    wed_bar, thu_bar = wed[0], thu[0]

    def setup(run: Run) -> None:
        entries_on_bar(
            run.plugins["p1"],
            {
                wed_bar.open_ns: lambda b: [enter("t1", b.close_ns, 1)],
                thu_bar.open_ns: lambda b: [enter("t2", b.close_ns, 1)],
            },
        )

    transport = FakeNexus(FakeClock(T0), err(409, "account_locked"))
    run_host(
        tmp_path,
        T0,
        ny(WED, 18, 40),
        bars=[*wed, *thu],
        setup=setup,
        accounts=[{**account(), "nexus": {"enabled": True}}],
        resources=HostResources(
            env=EnvView({}, {}), nexus=transport, nexus_resolved={"acct": "practice"}
        ),
    )
    sent = [b.get("clientOrderId") for _, _, b in transport.calls]
    assert sent[0] == "fse:20261014:p1:t1:entry"  # refused: account_locked
    assert "fse:20261015:p1:t2:entry" in sent
    [unblocked] = host_log(tmp_path, "nexus_opens_unblocked")
    assert unblocked["day"] == WED.isoformat()
    t2 = [
        ln for ln in host_log(tmp_path, "nexus_result") if (ln["tag"], ln["leg"]) == ("t2", "entry")
    ]
    assert [ln["result"] for ln in t2] == ["filled"]


def test_the_exception_path_drains_for_at_most_two_seconds(tmp_path: Path) -> None:
    clock = FakeClock(T0)
    plane = FakeDataPlane(clock, [])
    transport = Hanging()
    host = make_host(
        tmp_path,
        {"p1": RecordingPlugin},
        [host_entry("p1", account="acct")],
        clock,
        plane,
        accounts=[{**account(), "nexus": {"enabled": True}}],
        resources=HostResources(
            env=EnvView({}, {}), nexus=transport, nexus_resolved={"acct": "practice"}
        ),
    )
    raised_at: list[Instant] = []

    async def failing_loop(until_ns: Instant | None) -> None:
        mirror = host._mirror
        assert mirror is not None
        mirror.enqueue_fills("acct", [mf("t1", plugin="p1")], {"MNQ": 1})
        await clock.sleep(NS_PER_SECOND)
        raised_at.append(clock.now())
        raise RuntimeError("boom")

    host._loop = failing_loop  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(clock.run(host.run(), max_steps=100_000))
    assert transport.calls == 1
    assert clock.now() - raised_at[0] <= 2 * NS_PER_SECOND
    outbox = read_jsonl(tmp_path / "live-state" / "accounts" / "acct" / "nexus_outbox.jsonl")
    assert [ln["op"] for ln in outbox] == ["enqueue"]  # unsent: still pending


def test_the_normal_path_drains_what_is_queued(tmp_path: Path) -> None:
    transport = FakeNexus(FakeClock(T0))
    _host_run(tmp_path, transport)
    outbox = read_jsonl(tmp_path / "live-state" / "accounts" / "acct" / "nexus_outbox.jsonl")
    assert [ln["op"] for ln in outbox].count("ack") == 4
    status = json.loads(
        (tmp_path / "live-state" / "accounts" / "acct" / "nexus_status.json").read_text()
    )
    assert status["queue_depth"] == 0
