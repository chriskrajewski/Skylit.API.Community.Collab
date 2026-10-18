"""Fake ProjectX bar history for data plane tests: in memory, or behind respx.

- :class:`MemoryBars` is a ``BarRetriever``: a synthetic 1-minute market per
  instrument (one bar per minute, price derived from the minute), published
  ``publish_delay_ns`` after each bar's close. Every call is recorded.
- :class:`RespxBarsGateway` answers ``loginKey`` and ``retrieveBars`` for a
  real :class:`~fse.projectx.bars.ProjectXBars` through the suite's respx
  router (``assert_all_mocked``), with switches for the ``live`` subscription
  (``live_ok``, ``live_has_bars``) and per-window outages.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Final

import httpx
import respx

from fse.clock import Clock
from fse.engine.types import Bar
from fse.projectx.bars import RETRIEVE_BARS_PATH
from fse.projectx.models import BarFetchFailure, BarHistory, format_instant
from fse.projectx.session import LOGIN_PATH
from fse.timekit import NS_PER_MINUTE, Instant, parse_rfc3339

__all__ = ["BASE", "TOKEN", "MemoryBars", "RespxBarsGateway", "synthetic_bar", "tick_of"]

BASE: Final = "https://api.projectx.invalid"
TOKEN: Final = "fake-bars-token-0000"
CONTRACTS: Final = {"MNQ": "MNQZ6", "MES": "MESZ6"}


def tick_of(open_ns: Instant) -> int:
    """A deterministic tick price for the bar opening at ``open_ns``."""
    return 24_000 + (open_ns // NS_PER_MINUTE) % 97


def synthetic_bar(instrument: str, open_ns: Instant, contract: str | None = None) -> Bar:
    o = tick_of(open_ns)
    return Bar(
        instrument=instrument,
        contract=contract or CONTRACTS.get(instrument, f"{instrument}Z6"),
        interval_s=60,
        open_ns=open_ns,
        close_ns=open_ns + NS_PER_MINUTE,
        o=o / 4,
        h=(o + 2) / 4,
        l=(o - 2) / 4,
        c=(o + 1) / 4,
        v=10.0,
        o_t=o,
        h_t=o + 2,
        l_t=o - 2,
        c_t=o + 1,
        source="projectx",
    )


@dataclass
class MemoryBars:
    """An in-memory ``BarRetriever`` (see the module notes)."""

    clock: Clock
    publish_delay_ns: int = 0
    missing: set[tuple[str, Instant]] = field(default_factory=set)
    """``(instrument, open_ns)`` bars that never exist."""
    calls: list[tuple[Instant, str, Instant, Instant]] = field(default_factory=list)
    fail_windows: int = 0
    """The next ``n`` calls return a failure window instead of bars."""
    raise_on_call: BaseException | None = None

    async def retrieve(
        self,
        *,
        instrument: str,
        contract: str,
        contract_id: str,
        start_ns: Instant,
        end_ns: Instant,
        interval_s: int,
    ) -> BarHistory:
        now = self.clock.now()
        self.calls.append((now, instrument, start_ns, end_ns))
        if self.raise_on_call is not None:
            raise self.raise_on_call
        if self.fail_windows:
            self.fail_windows -= 1
            failure = BarFetchFailure(start_ns, end_ns, status=503)
            return BarHistory(
                instrument, contract, contract_id, interval_s, start_ns, end_ns, (), (failure,), 1
            )
        bars = tuple(
            synthetic_bar(instrument, t, contract)
            for t in range(start_ns, end_ns, NS_PER_MINUTE)
            if t + NS_PER_MINUTE + self.publish_delay_ns <= now
            and (instrument, t) not in self.missing
        )
        return BarHistory(
            instrument, contract, contract_id, interval_s, start_ns, end_ns, bars, (), 1
        )

    def calls_for(self, instrument: str) -> list[Instant]:
        return [t for t, i, _, _ in self.calls if i == instrument]


def _ok(**body: object) -> httpx.Response:
    return httpx.Response(200, json={"success": True, "errorCode": 0, "errorMessage": None, **body})


@dataclass
class RespxBarsGateway:
    """``loginKey`` + ``retrieveBars`` behind respx (see the module notes)."""

    clock: Clock
    live_ok: bool = True
    live_has_bars: bool = True
    sim_has_bars: bool = True
    fail_starts: set[Instant] = field(default_factory=set)
    """Request windows starting here answer HTTP 400 (a failed window, not retried)."""
    login_status: int = 200
    bodies: list[dict[str, object]] = field(default_factory=list)
    logins: int = 0
    has_bar: Callable[[str, Instant], bool] = lambda _cid, _t: True
    base: str = BASE
    """The gateway's base URL (``fse run`` uses ProjectXSession's default)."""

    def install(self, router: respx.MockRouter) -> None:
        router.post(self.base + LOGIN_PATH).mock(side_effect=self._login)
        router.post(self.base + RETRIEVE_BARS_PATH).mock(side_effect=self._bars)

    def _login(self, request: httpx.Request) -> httpx.Response:
        self.logins += 1
        if self.login_status != 200:
            return httpx.Response(self.login_status, json={"success": False, "errorCode": 3})
        return _ok(token=TOKEN)

    def _bars(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        live = bool(body["live"])
        lo = parse_rfc3339(str(body["startTime"]))
        hi = parse_rfc3339(str(body["endTime"]))
        if lo in self.fail_starts or (live and not self.live_ok):
            return httpx.Response(400, json={"success": False, "errorCode": 1})
        if (live and not self.live_has_bars) or (not live and not self.sim_has_bars):
            return _ok(bars=[])
        cid = str(body["contractId"])
        now = self.clock.now()
        times = [
            t
            for t in range(lo, hi, NS_PER_MINUTE)
            if t + NS_PER_MINUTE <= now and self.has_bar(cid, t)
        ]
        return _ok(bars=[_wire(t) for t in sorted(times, reverse=True)])

    def bodies_for(self, *, live: bool | None = None) -> list[dict[str, object]]:
        return [b for b in self.bodies if live is None or b["live"] is live]

    def windows(self) -> Iterable[tuple[str, Instant, Instant]]:
        for b in self.bodies:
            yield (
                str(b["contractId"]),
                parse_rfc3339(str(b["startTime"])),
                parse_rfc3339(str(b["endTime"])),
            )


def _wire(t: Instant) -> dict[str, object]:
    o = tick_of(t)
    return {
        "t": format_instant(t).replace("Z", "+00:00"),
        "o": o / 4,
        "h": (o + 2) / 4,
        "l": (o - 2) / 4,
        "c": (o + 1) / 4,
        "v": 10,
    }
