"""Opt-in mirror of paper-account fills into Skylit Nexus (paper-analytics §5).

The local book is the source of truth; Nexus is a best-effort copy that can
never block, slow or change trading (H-F3):

- **Enqueue** (host loop, synchronous, never raises): :meth:`NexusMirror.enqueue_fills`
  turns one batch of a member's entry/exit fills into outbox items and
  appends each to ``<live-state>/accounts/<id>/nexus_outbox.jsonl`` (one
  ``O_APPEND`` line, no network). An entry fill is a market order; an exit
  fill that leaves the plugin's net in the instrument non-zero is a
  ``reduceOnly`` order, one that flattens it is a ticker close.
- **Send** (one asyncio task, :meth:`NexusMirror.run`): per account strictly
  in ``seq`` order, one process-wide :class:`RateLimiter` (≤ 5 sends per 5 s,
  ≤ 30 per 60 s, ``Retry-After`` of a 429 pauses every account). Each result
  is acked in the outbox (through ``offload``, off the loop thread), logged as
  one ``nexus_result`` host-log line and summarised in ``nexus_status.json``.
- **Restart**: pending = enqueued minus acked, resent with the same
  ``clientOrderId`` (a replay counts as success, H-5).

Every file and log line carries the account as written in the YAML
(``practice`` or ``env:<VAR>``); the resolved value lives only in memory and
in the requests the mirror sends. Transport errors are logged by type only,
never by text (an ``httpx`` error's text holds the URL).

Field names of the request bodies are pinned in :data:`OPEN_FIELDS` and
:data:`CLOSE_FIELDS` from the Nexus Trades docs (addendum H); the published
OpenAPI could not be fetched during implementation.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import partial
from pathlib import Path
from typing import Any, Final, Protocol
from urllib.parse import quote

from fse.clock import Clock
from fse.engine.types import Direction, Ticks
from fse.live.host.accounts import account_dir
from fse.live.host.config import AccountEntry, NexusBlock
from fse.live.host.dayclock import globex_day_at, next_open, previous_globex_day
from fse.logio import LogWriter, canonical_json
from fse.logio.canonical_json import JsonValue
from fse.secrets.env import EnvView
from fse.sim.fills import MONEY_CONTEXT, tick_value
from fse.timekit import NS_PER_SECOND, Instant

__all__ = [
    "BODY_MAX_BYTES",
    "CLOSE_FIELDS",
    "NEXUS_BASE_URL",
    "OPEN_FIELDS",
    "OUTBOX_FILE_NAME",
    "STATUS_FILE_NAME",
    "HttpNexusTransport",
    "MirrorFill",
    "NexusMirror",
    "NexusResponse",
    "NexusTransport",
    "RateLimiter",
    "bounded",
    "client_order_id",
    "request_for",
    "resolve_nexus_accounts",
]

NEXUS_BASE_URL: Final = "https://app.skylit.ai/api/nexus/v1"
OUTBOX_FILE_NAME: Final = "nexus_outbox.jsonl"
STATUS_FILE_NAME: Final = "nexus_status.json"
ARCHIVE_DIR_NAME: Final = "archive"
STATUS_VERSION: Final = 1

OPEN_FIELDS: Final = (
    "assetClass",
    "ticker",
    "side",
    "quantity",
    "clientOrderId",
    "account",
    "reduceOnly",
    "test",
)
"""Every key a ``POST /trades`` body may carry (documented futures order fields)."""
CLOSE_FIELDS: Final = ("ticker",)
"""Every key a ``POST /trading/accounts/{account}/close`` body may carry."""
BODY_MAX_BYTES: Final = 8 * 1024

CLIENT_ORDER_ID_MAX: Final = 64
_CLIENT_ORDER_ID_RE: Final = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
PRACTICE: Final = "practice"
ENV_PREFIX: Final = "env:"

HTTP_TIMEOUT_S: Final = 10.0
GIVE_UP_NS: Final = 23 * 3600 * NS_PER_SECOND
"""An item not delivered within 23 h (inside Nexus's 24 h idempotency window) fails."""
BACKOFF_CAP_NS: Final = 300 * NS_PER_SECOND
IN_PROGRESS_RETRY_NS: Final = NS_PER_SECOND
DRAIN_TIMEOUT_NS: Final = 2 * NS_PER_SECOND
RATE_WINDOWS: Final = ((5, 5 * NS_PER_SECOND), (30, 60 * NS_PER_SECOND))

PERMANENT_CODES: Final = frozenset(
    {"invalid_api_key", "nexus_access_denied", "futures_access_denied", "account_suspended"}
)
RETRY_CODES: Final = frozenset(
    {
        "order_refused",
        "rules_pending",
        "stale_quote",
        "no_live_price",
        "trades_api_disabled",
        "unavailable",
        "internal_error",
        "rate_limited",
    }
)

_TICK_POINTS: Final = Decimal("0.25")
_CENT: Final = Decimal("0.01")
_ZERO: Final = Decimal("0.00")

type Offload = Callable[[Callable[[], object]], Awaitable[object]]
type HostLog = Callable[[Mapping[str, JsonValue]], None]


# ---------------------------------------------------------------- transport


@dataclass(frozen=True, slots=True)
class NexusResponse:
    """One HTTP response: status, the parsed JSON object (or ``None``) and headers."""

    status: int
    json: Mapping[str, Any] | None
    headers: Mapping[str, str] = field(default_factory=dict)


class NexusTransport(Protocol):
    """Sends one POST under ``/api/nexus/v1``; raises on a transport failure."""

    async def post(self, path: str, body: Mapping[str, JsonValue]) -> NexusResponse: ...


class HttpNexusTransport:
    """The real transport: ``Authorization: Bearer <SKYLIT_API_KEY>``, 10 s timeout.

    Only ``fse run`` builds one (DP-12); it refuses to construct under pytest.
    The key stays in this instance and is registered with the writer's redactor.
    """

    __slots__ = ("_http", "_key")

    def __init__(self, http: Any, api_key: str, *, writer: LogWriter | None = None) -> None:
        if "PYTEST_CURRENT_TEST" in os.environ:
            raise RuntimeError("the Nexus HTTP transport is not available under pytest")
        if not api_key.strip():
            raise ValueError("the Nexus API key is blank")
        self._http = http
        self._key = api_key
        if writer is not None:
            writer.add_secret(api_key)

    def __repr__(self) -> str:
        return f"HttpNexusTransport(base={NEXUS_BASE_URL!r})"

    async def post(self, path: str, body: Mapping[str, JsonValue]) -> NexusResponse:
        resp = await self._http.post(
            NEXUS_BASE_URL + path,
            content=json.dumps(body, separators=(",", ":")).encode("utf-8"),
            headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"},
            timeout=HTTP_TIMEOUT_S,
        )
        doc: object
        try:
            doc = json.loads(resp.text, parse_float=Decimal)
        except ValueError:
            doc = None
        return NexusResponse(
            int(resp.status_code),
            doc if isinstance(doc, dict) else None,
            {str(k).lower(): str(v) for k, v in resp.headers.items()},
        )


# ---------------------------------------------------------------- pure helpers


def client_order_id(day: date, plugin: str, tag: str, leg: str) -> str:
    """``fse:YYYYMMDD:<plugin>:<tag>:<leg>``; hashed to ``fse:`` + 40 hex when it does not fit."""
    full = f"fse:{day:%Y%m%d}:{plugin}:{tag}:{leg}"
    if len(full) <= CLIENT_ORDER_ID_MAX and _CLIENT_ORDER_ID_RE.fullmatch(full):
        return full
    return "fse:" + hashlib.sha256(full.encode("utf-8")).hexdigest()[:40]


def request_for(item: Mapping[str, JsonValue], account: str) -> tuple[str, dict[str, JsonValue]]:
    """The request path and body of an outbox item, with the resolved Nexus ``account``.

    ``practice`` is the default account and is sent by omitting ``account``.
    ``ValueError`` for an unknown op or a body over 8 KB.
    """
    op = item.get("op")
    body: dict[str, JsonValue]
    if op == "close":
        body = {k: item[k] for k in CLOSE_FIELDS if k in item}
        path = f"/trading/accounts/{quote(account, safe='')}/close"
    elif op == "order":
        body = {k: item[k] for k in OPEN_FIELDS if k in item and k != "account"}
        if account != PRACTICE:
            body["account"] = account
        path = "/trades"
    else:
        raise ValueError(f"unknown outbox op {op!r}")
    if len(json.dumps(body, separators=(",", ":")).encode("utf-8")) > BODY_MAX_BYTES:
        raise ValueError("request body over 8 KB")
    return path, body


def resolve_nexus_accounts(
    accounts: Iterable[AccountEntry], env: EnvView
) -> tuple[dict[str, str], dict[str, str]]:
    """``(resolved, disabled)`` for every account with ``nexus.enabled``.

    ``resolved`` maps the paper account id to the Nexus account (``env:<VAR>``
    read from ``env``); ``disabled`` maps an id to the reason its mirror is off
    (``env var <VAR> not set``, ``same nexus account as <other id>``). Reasons
    name variables and paper account ids only, never a value.
    """
    resolved: dict[str, str] = {}
    disabled: dict[str, str] = {}
    for acct in accounts:
        if not acct.nexus.enabled:
            continue
        target = acct.nexus.account
        if target.startswith(ENV_PREFIX):
            var = target[len(ENV_PREFIX) :]
            value = env.get(var)
            if value is None or not value.strip():
                disabled[acct.id] = f"env var {var} not set"
                continue
            resolved[acct.id] = value.strip()
        else:
            resolved[acct.id] = target
    by_value: dict[str, list[str]] = {}
    for acct_id, value in resolved.items():
        by_value.setdefault(value, []).append(acct_id)
    for ids in by_value.values():
        if len(ids) < 2:
            continue
        for acct_id in ids:
            other = next(o for o in ids if o != acct_id)
            disabled[acct_id] = f"same nexus account as {other}"
            del resolved[acct_id]
    return resolved, disabled


class RateLimiter:
    """One per process: sliding windows (≤ 5 per 5 s, ≤ 30 per 60 s) and a pause."""

    __slots__ = ("_paused_until", "_sent", "_windows")

    def __init__(self, windows: Sequence[tuple[int, int]] = RATE_WINDOWS) -> None:
        self._windows = tuple(windows)
        self._sent: deque[Instant] = deque()
        self._paused_until: Instant = 0

    def delay(self, now: Instant) -> int:
        """Nanoseconds to wait before the next send (0: send now)."""
        wait = max(0, self._paused_until - now)
        for limit, span in self._windows:
            recent = [t for t in self._sent if t > now - span]
            if len(recent) >= limit:
                wait = max(wait, recent[-limit] + span - now)
        return wait

    def record(self, now: Instant) -> None:
        self._sent.append(now)
        horizon = max(span for _, span in self._windows)
        while self._sent and self._sent[0] <= now - horizon:
            self._sent.popleft()

    def pause_until(self, t: Instant) -> None:
        """No account sends before ``t`` (a 429's ``Retry-After``)."""
        self._paused_until = max(self._paused_until, t)


@dataclass(frozen=True, slots=True)
class MirrorFill:
    """One local fill of a mirrored account's member, as the host saw it."""

    plugin: str
    tag: str
    leg: str
    """``entry`` or the exit leg (``stop``, ``tp1``, ``tp2``, ``exit``)."""
    entry: bool
    instrument: str
    direction: Direction
    qty: int
    price_t: Ticks
    fill_ns: Instant
    day: date
    """The Globex day the fill is dated to (the ``clientOrderId`` day)."""


def _globex_key(now: Instant) -> date:
    """The Globex day holding ``now``; in the break and the weekend, the last closed one.

    Once-per-day logs and the ``account_locked`` block are keyed on it (§5.2:
    "once per Globex day"), not on the NY date: a Globex day spans two NY dates.
    """
    return previous_globex_day(next_open(now)).day


def _signed(f: MirrorFill) -> int:
    buys = (f.entry and f.direction == "long") or (not f.entry and f.direction == "short")
    return f.qty if buys else -f.qty


def _retry_after_ns(headers: Mapping[str, str]) -> int | None:
    for k, v in headers.items():
        if k.lower() == "retry-after":
            try:
                seconds = Decimal(v.strip())
            except InvalidOperation:
                return None
            if seconds < 0:
                return None
            return int(seconds * NS_PER_SECOND)
    return None


def _error_code(resp: NexusResponse) -> str:
    doc = resp.json or {}
    err = doc.get("error")
    if isinstance(err, Mapping):
        code = err.get("code")
        if isinstance(code, str) and code:
            return code
    if resp.status == 429:
        return "rate_limited"
    return f"http_{resp.status}"


def _is_replay(doc: Mapping[str, Any] | None) -> bool:
    meta = (doc or {}).get("meta")
    return isinstance(meta, Mapping) and meta.get("idempotentReplay") is True


_PRICE_KEYS: Final = ("fillPrice", "filledPrice", "avgPrice", "price")
_TIME_KEYS: Final = ("filledAt", "filled_at", "executedAt", "time", "timestamp")


def _nexus_fill(doc: Mapping[str, Any] | None) -> tuple[str | None, str | None]:
    """The server fill price and time of a success body, if it names them."""
    if not doc:
        return None, None
    data = doc.get("data", doc)
    places: list[Mapping[str, Any]] = []
    if isinstance(data, Mapping):
        places.append(data)
        for key in ("fill", "order", "trade"):
            inner = data.get(key)
            if isinstance(inner, Mapping):
                places.append(inner)
    price: str | None = None
    at: str | None = None
    for place in places:
        for key in _PRICE_KEYS:
            v = place.get(key)
            if price is None and isinstance(v, (int, Decimal, str)) and not isinstance(v, bool):
                price = str(v)
        for key in _TIME_KEYS:
            v = place.get(key)
            if at is None and isinstance(v, (int, str)) and not isinstance(v, bool):
                at = str(v)
    return price, at


def _decimal(text: object) -> Decimal | None:
    if not isinstance(text, str):
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


# ---------------------------------------------------------------- the mirror


@dataclass(slots=True)
class _Item:
    seq: int
    t_ns: Instant
    item: dict[str, JsonValue]
    local: dict[str, JsonValue]
    attempts: int = 0
    last_code: str | None = None
    retry_logged: set[str] = field(default_factory=set)

    @property
    def is_entry(self) -> bool:
        return self.local.get("entry") is True

    @property
    def client_order_id(self) -> str:
        cid = self.item.get("clientOrderId")
        return cid if isinstance(cid, str) else ""


@dataclass(slots=True)
class _Lane:
    """One mirrored paper account."""

    id: str
    block: NexusBlock
    dir: Path
    resolved: str | None
    disabled_reason: str | None = None
    disabled_by: str | None = None
    """``config`` (no key, env var unset, same target) or ``nexus`` (a permanent code)."""
    ended: bool = False
    opens_blocked: bool = False
    opens_blocked_day: date | None = None
    """The Globex day of the ``account_locked``; the 18:00 rotation lifts the block."""
    pending: deque[_Item] = field(default_factory=deque)
    seq: int = 0
    hold_until: Instant = 0
    entries: dict[str, str] = field(default_factory=dict)
    """``<plugin>:<tag>`` -> the ``clientOrderId`` of its latest entry."""
    enqueued: set[str] = field(default_factory=set)
    """Every ``clientOrderId`` in the outbox: a fill replayed after a crash is not queued twice."""
    results: dict[str, str] = field(default_factory=dict)
    """Entry ``clientOrderId`` -> its ack result."""
    prices: dict[str, tuple[Ticks, Decimal | None]] = field(default_factory=dict)
    """Entry ``clientOrderId`` -> (local price ticks, Nexus fill price)."""
    local_net: Decimal = _ZERO
    nexus_net: Decimal = _ZERO
    last_success_ns: Instant | None = None
    last_error: dict[str, JsonValue] | None = None
    outbox_degraded: bool = False
    once: set[tuple[str, ...]] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def outbox(self) -> Path:
        return self.dir / OUTBOX_FILE_NAME

    @property
    def status_path(self) -> Path:
        return self.dir / STATUS_FILE_NAME

    @property
    def active(self) -> bool:
        return self.resolved is not None and self.disabled_reason is None and not self.ended


class NexusMirror:
    """The outbox, sender and status of every mirrored paper account of one run."""

    def __init__(
        self,
        accounts: Sequence[AccountEntry],
        *,
        state_root: Path,
        transport: NexusTransport | None,
        resolved: Mapping[str, str],
        disabled: Mapping[str, str],
        writer: LogWriter,
        clock: Clock,
        log: HostLog,
        offload: Offload,
        next_open_ns: Callable[[Instant], Instant],
        limiter: RateLimiter | None = None,
    ) -> None:
        self._transport = transport
        self._writer = writer
        self._clock = clock
        self._log = log
        self._offload = offload
        self._next_open_ns = next_open_ns
        self._limiter = limiter if limiter is not None else RateLimiter()
        self._wake = asyncio.Event()
        self._idle = asyncio.Event()
        self._lanes: dict[str, _Lane] = {}
        for acct in accounts:
            if not acct.nexus.enabled:
                continue
            lane = _Lane(
                id=acct.id,
                block=acct.nexus,
                dir=account_dir(state_root, acct.id),
                resolved=resolved.get(acct.id),
            )
            if transport is None:
                lane.disabled_reason = "no api key"
            elif acct.id in disabled:
                lane.disabled_reason = disabled[acct.id]
            elif lane.resolved is None:
                lane.disabled_reason = "nexus account not resolved"
            if lane.disabled_reason is not None:
                lane.disabled_by = "config"
                lane.resolved = None
            self._lanes[acct.id] = lane

    # ------------------------------------------------------------ queries

    @property
    def account_ids(self) -> tuple[str, ...]:
        return tuple(self._lanes)

    @property
    def active(self) -> bool:
        """Whether any account can send (the host starts :meth:`run` only then)."""
        return self._transport is not None and any(lane.active for lane in self._lanes.values())

    def mirrors(self, account_id: str) -> bool:
        """Whether fills of ``account_id`` are enqueued."""
        lane = self._lanes.get(account_id)
        return lane is not None and lane.active

    def pending(self, account_id: str) -> int:
        return len(self._lanes[account_id].pending)

    def status(self, account_id: str) -> dict[str, JsonValue]:
        return self._status_doc(self._lanes[account_id])

    # ------------------------------------------------------------ startup

    def load(self) -> None:
        """Restore each lane from its status file and outbox; write the status files.

        Synchronous, at host startup (before any fill can be enqueued).
        """
        for lane in self._lanes.values():
            self._load_status(lane)
            self._load_outbox(lane)
            if lane.disabled_by == "config":
                self._log(
                    {
                        "kind": "nexus_disabled",
                        "level": "warning" if lane.disabled_reason == "no api key" else "error",
                        "account": lane.id,
                        "nexus_account": lane.block.account,
                        "reason": lane.disabled_reason,
                    }
                )
            self._write_status_now(lane)

    def _load_status(self, lane: _Lane) -> None:
        try:
            doc = json.loads(lane.status_path.read_text(encoding="utf-8"))
        except OSError, ValueError:
            return
        if not isinstance(doc, dict):
            return
        if doc.get("disabled_by") == "nexus" and doc.get("disabled") is True:
            reason = doc.get("disabled_reason")
            lane.disabled_reason = reason if isinstance(reason, str) else "disabled"
            lane.disabled_by = "nexus"
        if doc.get("ended") is True:
            lane.ended = True
        blocked_day = doc.get("opens_blocked_day")
        today = globex_day_at(self._clock.now())
        if (
            doc.get("opens_blocked") is True
            and isinstance(blocked_day, str)
            and today is not None
            and blocked_day == today.day.isoformat()
        ):
            # A restart on the day of the lock keeps it (the 18:00 rotation lifts it);
            # a later day, or the weekend gap, starts unblocked: the Sunday open runs
            # no rotation that could lift a Friday block.
            lane.opens_blocked = True
            lane.opens_blocked_day = date.fromisoformat(blocked_day)
        last = doc.get("last_success_ns")
        if isinstance(last, int):
            lane.last_success_ns = last
        err = doc.get("last_error")
        if isinstance(err, dict):
            lane.last_error = err

    def _load_outbox(self, lane: _Lane) -> None:
        try:
            text = lane.outbox.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError:
            self._log({"kind": "nexus_outbox_read_failed", "level": "error", "account": lane.id})
            return
        items: dict[int, _Item] = {}
        acks: dict[int, Mapping[str, Any]] = {}
        for raw in text.splitlines():
            try:
                doc = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(doc, dict) or not isinstance(doc.get("seq"), int):
                continue
            seq = doc["seq"]
            lane.seq = max(lane.seq, seq)
            if doc.get("op") == "enqueue":
                item, local, t_ns = doc.get("item"), doc.get("local"), doc.get("t_ns")
                if isinstance(item, dict) and isinstance(local, dict) and isinstance(t_ns, int):
                    items[seq] = _Item(seq, t_ns, item, local)
            elif doc.get("op") == "ack":
                acks[seq] = doc
        for seq in sorted(items):
            rec = items[seq]
            lane.enqueued.add(rec.client_order_id)
            plugin, tag = rec.local.get("plugin"), rec.local.get("tag")
            if rec.is_entry and isinstance(plugin, str) and isinstance(tag, str):
                lane.entries[f"{plugin}:{tag}"] = rec.client_order_id
            ack = acks.get(seq)
            if ack is None:
                lane.pending.append(rec)
                continue
            result = ack.get("result")
            nexus = ack.get("nexus")
            self._apply_result(
                lane,
                rec,
                result if isinstance(result, str) else "failed",
                nexus if isinstance(nexus, Mapping) else None,
            )

    # ------------------------------------------------------------ enqueue (host loop)

    def enqueue_failed(self, account_id: str, exc: Exception) -> None:
        """The host could not build a batch's fills: log once per account per day."""
        lane = self._lanes.get(account_id)
        if lane is not None:
            self._once(lane, "nexus_enqueue_failed", type(exc).__name__)

    def enqueue_fills(
        self, account_id: str, fills: Sequence[MirrorFill], positions: Mapping[str, int]
    ) -> None:
        """Enqueue one batch of a member's fills; ``positions`` is the book after the batch.

        Never raises: a build failure drops the batch (``nexus_enqueue_failed``),
        an outbox append failure keeps the item in memory only
        (``nexus_outbox_write_failed``).
        """
        lane = self._lanes.get(account_id)
        if lane is None or not lane.active or not fills:
            return
        try:
            built, entries = self._build(lane, fills, positions)
        except Exception as exc:
            self._once(lane, "nexus_enqueue_failed", type(exc).__name__)
            return
        lane.entries.update(entries)
        for item, local in built:
            self._enqueue(lane, item, local)

    def _build(
        self, lane: _Lane, fills: Sequence[MirrorFill], positions: Mapping[str, int]
    ) -> tuple[list[tuple[dict[str, JsonValue], dict[str, JsonValue]]], dict[str, str]]:
        pos = dict(positions)
        for f in fills:
            pos[f.instrument] = pos.get(f.instrument, 0) - _signed(f)
        entries = dict(lane.entries)
        out: list[tuple[dict[str, JsonValue], dict[str, JsonValue]]] = []
        test = lane.block.test_only
        seen = set(lane.enqueued)
        for f in fills:
            signed = _signed(f)
            pos[f.instrument] = pos.get(f.instrument, 0) + signed
            cid = client_order_id(f.day, f.plugin, f.tag, f.leg)
            if cid in seen:
                continue  # the same fill again (a replay after a crash): queued once
            seen.add(cid)
            key = f"{f.plugin}:{f.tag}"
            item: dict[str, JsonValue]
            order: dict[str, JsonValue] = {
                "op": "order",
                "assetClass": "futures",
                "ticker": f.instrument,
                "side": "buy" if signed > 0 else "sell",
                "quantity": f.qty,
                "clientOrderId": cid,
                "account": lane.block.account,
            }
            if f.entry:
                item = order
                entry_id: str | None = cid
                entries[key] = cid
            else:
                entry_id = entries.get(key)
                if test:
                    # A test order places nothing, so there is no Nexus position to
                    # reduce or close: the exit is priced as a plain test order.
                    item = order
                elif pos[f.instrument] == 0:
                    item = {
                        "op": "close",
                        "ticker": f.instrument,
                        "account": lane.block.account,
                        "clientOrderId": cid,
                    }
                else:
                    item = {**order, "reduceOnly": True}
            if test and item["op"] == "order":
                item["test"] = True
            request_for(item, PRACTICE)  # body allow-list and size check, symbolic account
            local: dict[str, JsonValue] = {
                "plugin": f.plugin,
                "tag": f.tag,
                "leg": f.leg,
                "entry": f.entry,
                "entry_id": entry_id,
                "instrument": f.instrument,
                "direction": f.direction,
                "qty": f.qty,
                "fill_ns": f.fill_ns,
                "price_t": f.price_t,
            }
            out.append((item, local))
        return out, entries

    def _enqueue(
        self, lane: _Lane, item: dict[str, JsonValue], local: dict[str, JsonValue]
    ) -> None:
        lane.seq += 1
        rec = _Item(lane.seq, self._clock.now(), item, local)
        lane.enqueued.add(rec.client_order_id)
        line: dict[str, JsonValue] = {
            "op": "enqueue",
            "seq": rec.seq,
            "t_ns": rec.t_ns,
            "item": item,
            "local": local,
        }
        try:
            self._writer.append_json(lane.outbox, line)
        except OSError as exc:
            lane.outbox_degraded = True
            self._once(lane, "nexus_outbox_write_failed", type(exc).__name__)
        else:
            lane.outbox_degraded = False
        lane.pending.append(rec)
        self._idle.clear()
        self._wake.set()

    def _once(self, lane: _Lane, kind: str, detail: str) -> None:
        """One error line per account per Globex day for ``kind``."""
        key = (kind, _globex_key(self._clock.now()).isoformat())
        if key in lane.once:
            return
        lane.once.add(key)
        self._log({"kind": kind, "level": "error", "account": lane.id, "detail": detail})

    # ------------------------------------------------------------ rotation (host loop)

    def new_day(self, closed: date) -> None:
        """The 18:00 rotation after ``closed``: lift each ``account_locked`` block.

        Nexus locks an account for its session (a daily-loss style lock), so
        opens resume with the next Globex day instead of staying skipped until
        a restart. Runs on the host loop inside ``_rotate``.
        """
        for lane in self._lanes.values():
            if not lane.opens_blocked:
                continue
            if lane.opens_blocked_day is not None and lane.opens_blocked_day > closed:
                continue
            lane.opens_blocked = False
            lane.opens_blocked_day = None
            self._log(
                {
                    "kind": "nexus_opens_unblocked",
                    "level": "info",
                    "account": lane.id,
                    "day": closed.isoformat(),
                }
            )
            self._write_status_now(lane)

    def archive(self, day: date) -> None:
        """Move each fully acked outbox to ``archive/nexus_outbox.<day>.jsonl``.

        Runs on the host loop inside ``_rotate``, with no ``await``: no enqueue
        can interleave. Skipped (retried at the next rotation) while items are
        pending or an ack write holds the account's lock.
        """
        for lane in self._lanes.values():
            if lane.pending or lane.lock.locked() or not lane.outbox.exists():
                continue
            target_dir = lane.dir / ARCHIVE_DIR_NAME
            try:
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / f"nexus_outbox.{day.isoformat()}.jsonl"
                n = 1
                while target.exists():
                    target = target_dir / f"nexus_outbox.{day.isoformat()}-{n}.jsonl"
                    n += 1
                lane.outbox.rename(target)
            except OSError as exc:
                self._log(
                    {
                        "kind": "nexus_archive_failed",
                        "level": "warning",
                        "account": lane.id,
                        "detail": type(exc).__name__,
                    }
                )

    # ------------------------------------------------------------ the task

    async def run(self) -> None:
        """Send every account's queue until cancelled; a bug stops only the mirror."""
        try:
            while True:
                self._wake.clear()
                wait, idle = await self._pump()
                if idle:
                    self._idle.set()
                await self._sleep_or_wake(wait)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._idle.set()
            self._log(
                {"kind": "nexus_mirror_failed", "level": "error", "detail": type(exc).__name__}
            )

    async def drain(self) -> None:
        """Return once nothing is sendable now (what is held or unsent stays pending)."""
        self._wake.set()
        await self._idle.wait()

    async def _sleep_or_wake(self, wait: int | None) -> None:
        tasks: set[asyncio.Future[Any]] = {asyncio.ensure_future(self._wake.wait())}
        if wait is not None:
            tasks.add(asyncio.ensure_future(self._clock.sleep(max(wait, 1))))
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()

    async def _pump(self) -> tuple[int | None, bool]:
        """Send heads until none is sendable; ``(ns to wait or None, idle)``."""
        while True:
            progressed = False
            wait: int | None = None
            for lane in list(self._lanes.values()):
                if not lane.active or not lane.pending:
                    continue
                now = self._clock.now()
                rec = lane.pending[0]
                if now - rec.t_ns >= GIVE_UP_NS:
                    await self._ack(lane, rec, "failed", rec.last_code or "gave_up", None)
                    progressed = True
                    continue
                if not rec.is_entry:
                    entry_id = rec.local.get("entry_id")
                    entry_result = lane.results.get(entry_id) if isinstance(entry_id, str) else None
                    if entry_result in ("skipped", "failed"):
                        await self._ack(lane, rec, "skipped", "entry_not_mirrored", None)
                        progressed = True
                        continue
                elif lane.opens_blocked:
                    await self._ack(lane, rec, "skipped", "account_locked", None)
                    progressed = True
                    continue
                if lane.hold_until > now:
                    held = lane.hold_until - now
                    wait = held if wait is None else min(wait, held)
                    continue
                limit = self._limiter.delay(now)
                if limit > 0:
                    return limit, False
                self._limiter.record(now)
                await self._send(lane, rec)
                progressed = True
            if not progressed:
                return wait, True

    async def _send(self, lane: _Lane, rec: _Item) -> None:
        assert self._transport is not None
        assert lane.resolved is not None
        try:
            path, body = request_for(rec.item, lane.resolved)
        except ValueError:
            await self._ack(lane, rec, "failed", "invalid_body", None)
            return
        try:
            resp = await self._transport.post(path, body)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._retry(lane, rec, "transport", None, detail=type(exc).__name__)
            return
        if 200 <= resp.status < 300:
            price, at = _nexus_fill(resp.json)
            nexus: dict[str, JsonValue] = {"price": price, "filled_at": at}
            if _is_replay(resp.json):
                nexus["idempotent_replay"] = True
            await self._ack(lane, rec, "filled", None, nexus)
            return
        await self._on_error(lane, rec, resp, _error_code(resp))

    async def _on_error(self, lane: _Lane, rec: _Item, resp: NexusResponse, code: str) -> None:
        now = self._clock.now()
        if code in PERMANENT_CODES or (resp.status in (401, 403) and code not in RETRY_CODES):
            lane.disabled_reason = code
            lane.disabled_by = "nexus"
            await self._fail_all(lane, code)
        elif code == "account_ended":
            lane.ended = True
            await self._fail_all(lane, code)
        elif code == "account_locked":
            if rec.is_entry:
                lane.opens_blocked = True
                lane.opens_blocked_day = _globex_key(now)
                await self._ack(lane, rec, "skipped", code, None)
            else:
                await self._ack(lane, rec, "failed", code, None)
        elif code == "market_closed":
            await self._retry(lane, rec, code, self._next_open_ns(now) - now)
        elif code == "request_in_progress":
            await self._retry(lane, rec, code, IN_PROGRESS_RETRY_NS)
        elif code in RETRY_CODES or resp.status == 429 or resp.status >= 500:
            after = _retry_after_ns(resp.headers)
            if resp.status == 429 and after is not None:
                self._limiter.pause_until(now + after)
            await self._retry(lane, rec, code, after)
        else:  # contract_not_offered, client_order_id_used, any other 4xx
            await self._ack(lane, rec, "failed", code, None)

    async def _retry(
        self,
        lane: _Lane,
        rec: _Item,
        code: str,
        delay_ns: int | None,
        *,
        detail: str | None = None,
    ) -> None:
        now = self._clock.now()
        rec.attempts += 1
        rec.last_code = code
        if delay_ns is None:
            delay_ns = min(NS_PER_SECOND * 2 ** min(rec.attempts - 1, 16), BACKOFF_CAP_NS)
        lane.hold_until = now + max(delay_ns, 0)
        lane.last_error = {"code": code, "t_ns": now}
        if code not in rec.retry_logged:
            rec.retry_logged.add(code)
            self._result_line(lane, rec, "retry", code, None, "info", detail=detail)
        await self._write_status(lane)

    async def _fail_all(self, lane: _Lane, code: str) -> None:
        while lane.pending:
            await self._ack(lane, lane.pending[0], "failed", code, None, level="error")

    async def _ack(
        self,
        lane: _Lane,
        rec: _Item,
        result: str,
        code: str | None,
        nexus: Mapping[str, JsonValue] | None,
        *,
        level: str | None = None,
    ) -> None:
        now = self._clock.now()
        line: dict[str, JsonValue] = {
            "op": "ack",
            "seq": rec.seq,
            "result": result,
            "nexus": dict(nexus) if nexus is not None else None,
            "error_code": code,
            "t_ns": now,
        }
        async with lane.lock:
            try:
                await self._offload(partial(self._writer.append_json, lane.outbox, line))
            except OSError as exc:
                lane.outbox_degraded = True
                self._once(lane, "nexus_outbox_write_failed", type(exc).__name__)
        if lane.pending and lane.pending[0] is rec:
            lane.pending.popleft()
        else:
            with contextlib.suppress(ValueError):
                lane.pending.remove(rec)
        self._apply_result(lane, rec, result, nexus)
        if result == "filled":
            lane.last_success_ns = now
        elif code is not None:
            lane.last_error = {"code": code, "t_ns": now}
            if code == "contract_not_offered":
                lane.last_error["ticker"] = rec.item.get("ticker")
        if level is None:
            level = self._level(lane, rec, result, code)
        self._result_line(lane, rec, result, code, nexus, level)
        await self._write_status(lane)

    def _level(self, lane: _Lane, rec: _Item, result: str, code: str | None) -> str:
        if result == "filled":
            return "info"
        if code == "contract_not_offered":
            day = _globex_key(self._clock.now()).isoformat()
            key = ("contract_not_offered", str(rec.item.get("ticker")), day)
            if key in lane.once:
                return "warning"
            lane.once.add(key)
            return "error"
        if code == "client_order_id_used" or (
            result == "failed" and code is not None and code.startswith("http_4")
        ):
            return "error"
        return "warning"

    def _apply_result(
        self, lane: _Lane, rec: _Item, result: str, nexus: Mapping[str, Any] | None
    ) -> None:
        """Book an ack: the entry's result and prices, or a filled exit's local/Nexus P&L."""
        price_t = rec.local.get("price_t")
        nx = _decimal((nexus or {}).get("price"))
        if rec.is_entry:
            lane.results[rec.client_order_id] = result
            if result == "filled" and isinstance(price_t, int):
                lane.prices[rec.client_order_id] = (price_t, nx)
            return
        entry_id = rec.local.get("entry_id")
        if result != "filled" or not isinstance(entry_id, str) or not isinstance(price_t, int):
            return
        entry = lane.prices.get(entry_id)
        qty, instrument = rec.local.get("qty"), rec.local.get("instrument")
        if entry is None or not isinstance(qty, int) or not isinstance(instrument, str):
            return
        entry_t, entry_nx = entry
        if entry_nx is None or nx is None:
            return
        try:
            tv = tick_value(instrument)
        except ValueError:
            return
        sign = 1 if rec.local.get("direction") == "long" else -1
        local = MONEY_CONTEXT.multiply(Decimal((price_t - entry_t) * sign * qty), tv)
        ticks = MONEY_CONTEXT.divide(MONEY_CONTEXT.subtract(nx, entry_nx), _TICK_POINTS)
        remote = MONEY_CONTEXT.multiply(MONEY_CONTEXT.multiply(ticks, Decimal(sign * qty)), tv)
        lane.local_net = MONEY_CONTEXT.add(lane.local_net, local).quantize(_CENT)
        lane.nexus_net = MONEY_CONTEXT.add(lane.nexus_net, remote).quantize(_CENT)

    def _result_line(
        self,
        lane: _Lane,
        rec: _Item,
        result: str,
        code: str | None,
        nexus: Mapping[str, JsonValue] | None,
        level: str,
        *,
        detail: str | None = None,
    ) -> None:
        line: dict[str, JsonValue] = {
            "kind": "nexus_result",
            "level": level,
            "account": lane.id,
            "nexus_account": lane.block.account,
            "plugin": rec.local.get("plugin"),
            "tag": rec.local.get("tag"),
            "leg": rec.local.get("leg"),
            "client_order_id": rec.client_order_id,
            "op": rec.item.get("op"),
            "result": result,
            "error_code": code,
            "local_price_t": rec.local.get("price_t"),
            "nexus_price": (nexus or {}).get("price"),
        }
        if detail is not None:
            line["detail"] = detail
        self._log(line)

    # ------------------------------------------------------------ status

    def _status_doc(self, lane: _Lane) -> dict[str, JsonValue]:
        return {
            "version": STATUS_VERSION,
            "account": lane.id,
            "enabled": True,
            "disabled": lane.disabled_reason is not None,
            "disabled_reason": lane.disabled_reason,
            "disabled_by": lane.disabled_by,
            "nexus_account": lane.block.account,
            "test_only": lane.block.test_only,
            "queue_depth": len(lane.pending),
            "last_success_ns": lane.last_success_ns,
            "last_error": lane.last_error,
            "opens_blocked": lane.opens_blocked,
            "opens_blocked_day": (
                None if lane.opens_blocked_day is None else lane.opens_blocked_day.isoformat()
            ),
            "ended": lane.ended,
            "outbox_degraded": lane.outbox_degraded,
            "local_net": format(lane.local_net, ".2f"),
            "nexus_net": format(lane.nexus_net, ".2f"),
        }

    def _write_status_now(self, lane: _Lane) -> None:
        try:
            self._writer.write_text(
                lane.status_path, canonical_json.dumps(self._status_doc(lane)) + "\n"
            )
        except OSError as exc:
            self._once(lane, "nexus_status_write_failed", type(exc).__name__)

    async def _write_status(self, lane: _Lane) -> None:
        body = canonical_json.dumps(self._status_doc(lane)) + "\n"
        try:
            await self._offload(partial(self._writer.write_text, lane.status_path, body))
        except OSError as exc:
            self._once(lane, "nexus_status_write_failed", type(exc).__name__)


async def bounded(aw: Awaitable[object], clock: Clock, timeout_ns: int) -> bool:
    """Await ``aw`` for at most ``timeout_ns`` on ``clock``; True when it finished.

    The clock (not the loop's real timer) bounds the wait, so a virtual-time
    test clock sees the timeout as a sleeper.
    """
    work = asyncio.ensure_future(aw)
    timer = asyncio.ensure_future(clock.sleep(timeout_ns))
    try:
        await asyncio.wait({work, timer}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        timer.cancel()
        if not work.done():
            work.cancel()
            with contextlib.suppress(BaseException):
                await work
    return work.done() and not work.cancelled() and work.exception() is None
