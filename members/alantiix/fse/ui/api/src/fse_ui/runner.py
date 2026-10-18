"""The 24 h runner: start, stop, mode and config; halt and clear; live monitoring.

``ProcessRunnerController`` runs ``fse run`` or ``fse paper`` as a child of the
API (local development, docker-compose). On AWS the runner is the
``fse-runner`` systemd unit, controlled by ``fse_ui.aws.runner_ssm.SsmRunnerController``.

``fse live`` (Practice / Combine) is refused here, whatever the client asks,
unless ``FSE_UI_ENABLE_LIVE`` is set; a ``paper`` start is also refused when
the Strategy_Config's ``order_mode`` is not ``paper``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import os
import re
import signal
import time
from collections import deque
from collections.abc import Callable, Mapping
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol
from zoneinfo import ZoneInfo

import yaml

from fse_ui.configs import ConfigStore, ConfigStoreError, runner_order_modes
from fse_ui.db import Db
from fse_ui.events import describe
from fse_ui.jobs import exit_meaning

if TYPE_CHECKING:
    from fse.live.host.config import RunnerConfig

type RunnerMode = Literal["run", "paper"]
RUNNER_MODES: tuple[RunnerMode, ...] = ("run", "paper")
PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
STOP_GRACE_S = 60.0
TAIL_BYTES = 512 * 1024
MAX_STATUS_BYTES = 256 * 1024
MAX_RAW_BYTES = 4 * 1024 * 1024
MAX_MARKS_BYTES = 64 * 1024
QUOTE_STALE_S = 10.0
BAR_STALE_S = 120.0
CENTS = Decimal("0.01")
NY = ZoneInfo("America/New_York")


class RunnerError(ValueError):
    pass


class RunnerController(Protocol):
    async def status(self) -> dict[str, Any]: ...

    async def start(self) -> dict[str, Any]: ...

    async def stop(self) -> dict[str, Any]: ...

    async def restart(self) -> dict[str, Any]: ...

    async def close(self) -> None:
        """The console is shutting down."""
        ...


def runner_settings(db: Db) -> dict[str, Any]:
    found: dict[str, Any] = db.get_kv(
        "runner", {"mode": "run", "runner_config": "runner", "strategy_config": "playbook_baseline"}
    )
    return found


def set_runner_settings(
    db: Db, *, mode: str, runner_config: str, strategy_config: str, store: ConfigStore
) -> dict[str, Any]:
    if mode not in RUNNER_MODES:
        raise RunnerError(
            f"mode {mode!r} is not allowed: only {', '.join(RUNNER_MODES)} (fse live is disabled)"
        )
    path = store.current_path("runner", runner_config)
    store.current_path("strategy", strategy_config)
    if runner_config != runner_settings(db)["runner_config"]:
        # A config enters practice only through Live → Order mode (its checks and confirm).
        modes = runner_order_modes(path.read_text(encoding="utf-8"))
        if any(mode != "paper" for _, mode in modes.values()):
            raise RunnerError(
                f"{runner_config} has a non-paper order_mode; select it while it is paper, "
                "then use Live → Order mode"
            )
    value = {"mode": mode, "runner_config": runner_config, "strategy_config": strategy_config}
    db.set_kv("runner", value)
    return value


def runner_target(db: Db, store: ConfigStore, *, enable_live: bool) -> tuple[RunnerMode, Path]:
    """The mode and config file the runner starts with (raises :class:`RunnerError`).

    Only ``run`` and ``paper``; ``paper`` also needs ``order_mode: paper`` unless
    live is enabled.
    """
    s = runner_settings(db)
    if s["mode"] == "run":
        return "run", store.current_path("runner", s["runner_config"])
    if s["mode"] == "paper":
        path = store.current_path("strategy", s["strategy_config"])
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        order_mode = data.get("order_mode", "paper") if isinstance(data, dict) else "paper"
        if order_mode != "paper" and not enable_live:
            raise RunnerError(
                f"the Strategy_Config order_mode is {order_mode!r}; only paper runs are allowed "
                "until the TopstepX terms are verified"
            )
        return "paper", path
    raise RunnerError(f"mode {s['mode']!r} is not allowed")


def runner_argv(db: Db, store: ConfigStore, *, fse_bin: str, enable_live: bool) -> list[str]:
    mode, path = runner_target(db, store, enable_live=enable_live)
    return [fse_bin, mode, "--config", str(path)]


class ProcessRunnerController:
    def __init__(
        self,
        db: Db,
        store: ConfigStore,
        *,
        fse_bin: str,
        enable_live: bool,
        env: Callable[[], dict[str, str]],
        redact: Callable[[], Callable[[str], str]],
        cwd: Path,
        log_path: Path,
    ) -> None:
        self._db, self._store = db, store
        self._fse_bin, self._enable_live = fse_bin, enable_live
        self._env, self._redact, self._cwd = env, redact, cwd
        self._log_path = log_path
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._started_at: float | None = None
        self._last_exit: int | None = None
        self._lines: deque[str] = deque(maxlen=400)
        self._lock = asyncio.Lock()

    def _running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    def _runner_command(self) -> str:
        mode = str(runner_settings(self._db).get("mode") or "run")
        return mode if mode in {"run", "paper"} else "run"

    async def status(self) -> dict[str, Any]:
        settings = runner_settings(self._db)
        return {
            "controller": "process",
            "state": "running" if self._running() else "stopped",
            "pid": self._proc.pid if self._running() and self._proc else None,
            "started_at": self._started_at if self._running() else None,
            "last_exit_code": self._last_exit,
            "last_exit_meaning": exit_meaning(self._last_exit, (self._runner_command(),)),
            "settings": settings,
            "log": list(self._lines)[-200:],
        }

    async def start(self) -> dict[str, Any]:
        async with self._lock:
            if self._running():
                raise RunnerError("the runner is running already")
            argv = runner_argv(
                self._db, self._store, fse_bin=self._fse_bin, enable_live=self._enable_live
            )
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            self._proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=self._env(),
                cwd=self._cwd,
                start_new_session=True,
                limit=1 << 20,
            )
            self._started_at = time.time()
            self._last_exit = None
            self._lines.append("$ " + " ".join(argv))
            self._reader = asyncio.get_running_loop().create_task(self._read(self._proc))
        return await self.status()

    async def _read(self, proc: asyncio.subprocess.Process) -> None:
        redact = self._redact()
        assert proc.stdout is not None
        with self._log_path.open("a", encoding="utf-8") as fh:
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                line = redact(raw.decode("utf-8", "replace").rstrip("\n"))
                self._lines.append(line)
                fh.write(line + "\n")
                fh.flush()
            code = await proc.wait()
            self._last_exit = code
            note = f"runner exited {code} ({exit_meaning(code, (self._runner_command(),))})"
            self._lines.append(note)
            fh.write(note + "\n")

    async def stop(self) -> dict[str, Any]:
        async with self._lock:
            proc = self._proc
            if proc is None or proc.returncode is not None:
                return await self.status()
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, sig)
                try:
                    await asyncio.wait_for(asyncio.shield(proc.wait()), STOP_GRACE_S)
                    break
                except TimeoutError:
                    continue
            if self._reader is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(self._reader, 5)
        return await self.status()

    async def restart(self) -> dict[str, Any]:
        await self.stop()
        return await self.start()

    async def close(self) -> None:
        await self.stop()


# ---------------------------------------------------------------- halt / clear


def plugin_state_dir(live_state: Path, plugin_id: str | None) -> Path:
    if plugin_id is None:
        return live_state
    if not PLUGIN_ID_RE.fullmatch(plugin_id):
        raise RunnerError(f"invalid plugin id {plugin_id!r}")
    return live_state / "plugins" / plugin_id


def halt_argv(fse_bin: str, live_state: Path, plugin_id: str | None, reason: str) -> list[str]:
    reason = reason.strip() or "halted from the fse console"
    if len(reason) > 200 or re.search(r"[\r\n\x00]", reason):
        raise RunnerError("the reason must be one line of at most 200 characters")
    return [
        fse_bin,
        "halt",
        "--state-dir",
        str(plugin_state_dir(live_state, plugin_id)),
        f"--reason={reason}",
    ]


def clear_argv(fse_bin: str, live_state: Path, plugin_id: str | None) -> list[str]:
    return [fse_bin, "clear", "--state-dir", str(plugin_state_dir(live_state, plugin_id))]


def day_loss_reset_argv(fse_bin: str, live_state: Path, plugin_id: str, reason: str) -> list[str]:
    """``fse day-loss-reset --plugin <id> --state-dir <live-state> --reason=…``."""
    if not PLUGIN_ID_RE.fullmatch(plugin_id):
        raise RunnerError(f"invalid plugin id {plugin_id!r}")
    reason = reason.strip() or "reset from the fse console"
    if len(reason) > 200 or re.search(r"[\r\n\x00]", reason):
        raise RunnerError("the reason must be one line of at most 200 characters")
    return [
        fse_bin,
        "day-loss-reset",
        f"--plugin={plugin_id}",
        "--state-dir",
        str(live_state),
        f"--reason={reason}",
    ]


def account_reset_argv(fse_bin: str, live_state: Path, account_id: str, reason: str) -> list[str]:
    """``fse account reset <id> --state-dir <live-state> --reason=<text>`` (§3.7)."""
    if not PLUGIN_ID_RE.fullmatch(account_id):
        raise RunnerError(f"invalid account id {account_id!r}")
    return [
        fse_bin,
        "account",
        "reset",
        account_id,
        "--state-dir",
        str(live_state),
        f"--reason={reason}",
    ]


def _blocks(directory: Path) -> dict[str, Any]:
    from fse.live.state_store import BlockStore, BlocksUnreadable, halt_file
    from fse.logio.log_writer import LogWriter
    from fse.secrets.env import EnvView

    writer = LogWriter.from_env(EnvView({}, {}), stdout=io.StringIO(), stderr=io.StringIO())
    found = BlockStore(writer, directory).read()
    if isinstance(found, BlocksUnreadable):
        blocks: list[dict[str, Any]] = [
            {
                "kind": "blocks_unreadable",
                "detail": found.reason,
                "instrument": None,
                "set_at_ns": None,
            }
        ]
    else:
        blocks = [
            {
                "kind": b.kind,
                "instrument": b.instrument,
                "detail": b.detail,
                "set_at_ns": b.set_at_ns,
            }
            for b in found
        ]
    return {"halt_file": halt_file(directory).exists(), "blocks": blocks}


def block_status(live_state: Path) -> dict[str, Any]:
    root = _blocks(live_state)
    plugins: dict[str, Any] = {}
    base = live_state / "plugins"
    if base.is_dir():
        for d in sorted(base.iterdir()):
            if d.is_dir() and PLUGIN_ID_RE.fullmatch(d.name):
                plugins[d.name] = _blocks(d)
    halted = bool(root["halt_file"] or root["blocks"])
    return {"root": root, "plugins": plugins, "halted": halted}


# ---------------------------------------------------------------- monitoring


def tail_jsonl(path: Path, *, limit: int = 300) -> list[dict[str, Any]]:
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - TAIL_BYTES))
            data = fh.read()
    except OSError:
        return []
    lines = data.decode("utf-8", "replace").splitlines()
    if size > TAIL_BYTES:
        lines = lines[1:]  # the first line may be cut
    out = []
    for line in lines[-limit:]:
        with contextlib.suppress(ValueError):
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
    return out


def _latest(root: Path, pattern: str, needed: str) -> Path | None:
    try:
        dirs = [d for d in root.glob(pattern) if (d / needed).is_file()]
    except OSError:
        return None
    # Name breaks mtime ties (coarse FS clocks) so run-…T1352… beats run-…T1200….
    return max(dirs, key=lambda d: ((d / needed).stat().st_mtime, d.name), default=None)


def _latest_file(root: Path, name: str, depth: int = 4) -> Path | None:
    best: tuple[float, Path] | None = None
    patterns = ["/".join(["*"] * i + [name]) for i in range(1, depth + 1)]
    for pattern in patterns:
        for p in root.glob(pattern):
            with contextlib.suppress(OSError):
                m = p.stat().st_mtime
                if best is None or m > best[0]:
                    best = (m, p)
    return None if best is None else best[1]


def _read_json(path: Path, limit: int) -> dict[str, Any] | None:
    with contextlib.suppress(OSError, ValueError):
        if path.stat().st_size <= limit:
            obj = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                return obj
    return None


def last_order_mode(path: Path) -> dict[str, Any] | None:
    """The last ``kind == "order_mode"`` line of a ``plugin_log.jsonl`` (the host writes
    one at start for the non-paper plugin) as ``{requested, mode, failures, t_ns}``.

    Its own scan of the last :data:`MAX_RAW_BYTES`: the 50-line tail can miss it.
    """
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - MAX_RAW_BYTES))
            data = fh.read()
    except OSError:
        return None
    if b"order_mode" not in data:
        return None
    lines = data.splitlines()
    if size > MAX_RAW_BYTES:
        lines = lines[1:]  # the first line may be cut
    for raw in reversed(lines):
        if b"order_mode" not in raw:
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("kind") == "order_mode":
            failures = obj.get("failures")
            return {
                "requested": obj.get("requested"),
                "mode": obj.get("mode"),
                "failures": [str(f) for f in failures] if isinstance(failures, list) else [],
                "t_ns": _int(obj.get("t_ns")),
            }
    return None


def latest_run_dir(runs_dir: Path) -> Path | None:
    """The newest ``run-*`` folder with a host log (the run the Live page shows)."""
    return _latest(runs_dir, "run-*", "host_log.jsonl")


def _said(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each log line with its plain-English ``say`` (text, severity, category)."""
    return [{**e, "say": describe(e)} for e in entries]


def _skylit(host: list[dict[str, Any]], now_ns: int) -> dict[str, Any] | None:
    """Today's Skylit plan from the last ``credit_projection`` and whether it is fetching now."""
    plan = next((e for e in reversed(host) if e.get("kind") == "credit_projection"), None)
    if plan is None:
        return None
    start, end = plan.get("window_start_ny"), plan.get("window_end_ny")
    now = datetime.fromtimestamp(now_ns / 1e9, NY)
    open_now = False
    if isinstance(start, str) and isinstance(end, str):
        hhmm = now.strftime("%H:%M")
        open_now = (
            bool(plan.get("exchange_session"))
            and plan.get("day") == now.date().isoformat()
            and start <= hhmm < end
        )
    return {
        "day": plan.get("day"),
        "symbols": plan.get("symbols"),
        "credits_per_day": plan.get("total_credits"),
        "refresh_interval_s": plan.get("refresh_interval_s"),
        "window_start": start,
        "window_end": end,
        "exchange_session": plan.get("exchange_session"),
        "open": open_now,
    }


def _dec(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        found = Decimal(str(value))
    except InvalidOperation:
        return None
    return found if found.is_finite() else None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _money(value: Decimal | None) -> str | None:
    return None if value is None else str(value.quantize(CENTS, rounding=ROUND_HALF_EVEN))


def _mark_for(marks: dict[str, Any] | None, instrument: Any, now_ns: int) -> dict[str, Any]:
    """The mark of ``instrument``: the quote unless the newest 1-minute close is newer."""
    none: dict[str, Any] = {"price_t": None, "source": None, "at_ns": None, "age_s": None}
    table = marks.get("marks") if isinstance(marks, dict) else None
    entry = None
    if isinstance(table, dict) and isinstance(instrument, str):
        entry = table.get(instrument)
    if not isinstance(entry, dict):
        return {**none, "stale": True}
    quote, bar = entry.get("quote"), entry.get("bar")
    q_at = _int(quote.get("received_ns")) if isinstance(quote, dict) else None
    q_price = _int(quote.get("price_t")) if isinstance(quote, dict) else None
    b_at = _int(bar.get("close_ns")) if isinstance(bar, dict) else None
    b_price = _int(bar.get("price_t")) if isinstance(bar, dict) else None
    bar_newer = b_price is not None and b_at is not None and (q_at is None or b_at > q_at)
    if q_price is not None and q_at is not None and not bar_newer:
        price, source, at, limit = q_price, "quote", q_at, QUOTE_STALE_S
    elif b_price is not None and b_at is not None:
        price, source, at, limit = b_price, "1m_close", b_at, BAR_STALE_S
    else:
        return {**none, "stale": True}
    age = max(0.0, (now_ns - at) / 1e9)
    return {
        "price_t": price,
        "source": source,
        "at_ns": at,
        "age_s": round(age, 1),
        "stale": age > limit,
    }


def live_pnl(
    status: dict[str, Any] | None, marks: dict[str, Any] | None, now_ns: int
) -> dict[str, Any] | None:
    """Per-position open P&L at the latest mark, after fees, and the day total.

    ``open_net = realized + ticks x tick_value x open_qty - exit_fee x open_qty``:
    ``realized`` already holds every fee so far (the entry fee included) and any
    partial exit, so ``day.total = day.closed (today.net) + day.open``. A
    position without a mark (or without a tick value) has ``null`` P&L and makes
    ``day.open`` and ``day.total`` ``null``. ``None`` for a plugin the host does
    not book (no ``today``). Money is a string with 2 decimals.
    """
    if not isinstance(status, dict) or not isinstance(status.get("today"), dict):
        return None
    today: dict[str, Any] = status["today"]
    positions: dict[str, Any] = {}
    open_sum: Decimal | None = Decimal(0)
    raw_positions = status.get("positions")
    for pos in raw_positions if isinstance(raw_positions, list) else []:
        if not isinstance(pos, dict) or not isinstance(pos.get("tag"), str):
            continue
        mark = _mark_for(marks, pos.get("instrument"), now_ns)
        entry_t, qty = _int(pos.get("entry_t")), _int(pos.get("open_qty"))
        tick_value, realized = _dec(pos.get("tick_value")), _dec(pos.get("realized"))
        exit_fee = _dec(pos.get("exit_fee")) or Decimal(0)
        sign = 1 if pos.get("side") == "long" else -1
        ticks: int | None = None
        gross: Decimal | None = None
        net: Decimal | None = None
        if mark["price_t"] is not None and entry_t is not None:
            ticks = (mark["price_t"] - entry_t) * sign
        if ticks is not None and qty is not None and tick_value is not None:
            gross = Decimal(ticks) * tick_value * qty
            if realized is not None:
                net = realized + gross - exit_fee * qty
        if net is None:
            open_sum = None
        elif open_sum is not None:
            open_sum += net
        positions[pos["tag"]] = {
            "mark": None if mark["price_t"] is None else mark["price_t"] / 4,
            "mark_t": mark["price_t"],
            "mark_source": mark["source"],
            "mark_received_ns": mark["at_ns"],
            "age_s": mark["age_s"],
            "stale": mark["stale"],
            "ticks": ticks,
            "open_gross": _money(gross),
            "open_net": _money(net),
        }
    closed = _dec(today.get("net"))
    total = None if closed is None or open_sum is None else closed + open_sum
    entries = _int(today.get("entries"))
    return {
        "positions": positions,
        "day": {
            "closed": _money(closed),
            "open": _money(open_sum),
            "total": _money(total),
            "entries": entries if entries is not None else _int(today.get("trades")),
            "closed_trades": _int(today.get("closed")),
        },
        "as_of_ns": now_ns,
    }


_CLOCK_KEYS = frozenset({"age_s", "stale"})


def live_digest(snap: dict[str, Any]) -> str:
    """A digest of what changed in a live snapshot, without what moves with the clock
    alone (``now_ns``, P&L ``as_of_ns``, mark ``age_s`` and ``stale``): the browser
    ages marks on its own 1 s clock, so a snapshot is only re-sent on a change."""
    body = {k: v for k, v in snap.items() if k != "now_ns"}
    plugins = []
    for item in body.get("plugins") or []:
        pnl = item.get("pnl") if isinstance(item, dict) else None
        if isinstance(pnl, dict):
            steady = {
                tag: {k: v for k, v in p.items() if k not in _CLOCK_KEYS}
                for tag, p in (pnl.get("positions") or {}).items()
            }
            pnl = {**{k: v for k, v in pnl.items() if k != "as_of_ns"}, "positions": steady}
            item = {**item, "pnl": pnl}
        plugins.append(item)
    body["plugins"] = plugins
    return hashlib.sha256(json.dumps(body, default=str, sort_keys=True).encode()).hexdigest()


def _transport_from_detail(detail: object) -> str | None:
    """``transport stream: connected`` → ``stream``; unknown shapes leave transport unset."""
    if not isinstance(detail, str):
        return None
    parts = detail.split()
    try:
        i = parts.index("transport")
    except ValueError:
        return None
    if i + 1 >= len(parts):
        return None
    return parts[i + 1].rstrip(":,")


def _provisional_hb(
    e: Mapping[str, Any], *, connected: bool, transport: str | None
) -> dict[str, Any]:
    return {
        "t_ns": e.get("t_ns"),
        "kind": "heartbeat",
        "connected": connected,
        "transport": transport,
        "subscription": e.get("subscription"),
        "lag": {},
        "rt_dropped": e.get("rt_dropped", 0),
        "provisional": True,
    }


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def projectx_heartbeat(host: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The Live ProjectX tile: a dataplane ``heartbeat``, or a provisional status from
    ``market_hub_*`` / quotes ``feed_status`` until the first 5-minute heartbeat lands.

    The hub can be up within seconds of day open while ``DataPlane`` only writes
    ``kind=heartbeat`` after ``HEARTBEAT_INTERVAL_NS`` (and only while a bar source
    exists). Without this fallback the UI says "unavailable / no heartbeat yet"
    even when the activity log already shows ProjectX market data connected.
    """
    # Walk newest-first via slice (not reversed()): mypy types reversed(list[dict]) as Any.
    for e in host[::-1]:
        if e.get("kind") == "heartbeat":
            return e
    # Hub events beat feed_status: both appear at day open, and hub is the clearer signal.
    for e in host[::-1]:
        kind = e.get("kind")
        transport = _as_str(e.get("transport"))
        if kind == "market_hub_disconnected":
            return _provisional_hb(e, connected=False, transport=transport or "stream")
        if kind == "market_hub_connected":
            return _provisional_hb(e, connected=True, transport=transport or "stream")
        if kind in ("market_hub_down_for_day", "market_hub_connect_failed", "market_hub_failed"):
            return _provisional_hb(e, connected=False, transport=transport)
    for e in host[::-1]:
        if e.get("kind") == "feed_status" and e.get("feed") == "quotes":
            return _provisional_hb(
                e,
                connected=bool(e.get("up")),
                transport=_transport_from_detail(e.get("detail")),
            )
    return None


def live_snapshot(
    runs_dir: Path,
    live_state: Path,
    *,
    now_ns: int | None = None,
    runner_cfg: RunnerConfig | None = None,
) -> dict[str, Any]:
    """Everything the Live page shows, readable: per-plugin ``status.json`` (written by
    the host for every plugin) with its live P&L (:func:`live_pnl` over the host's
    ``marks.json``), plain-English log lines, today's Skylit plan, and the paper
    analytics of today (``paper``, ``accounts``: :func:`fse_ui.paper.today`;
    ``runner_cfg`` supplies the caps and the account list). The raw
    ``plugin.json`` is not included (see :func:`plugin_raw`)."""
    from fse_ui import paper as paper_mod

    now_ns = time.time_ns() if now_ns is None else now_ns
    run_dir = _latest(runs_dir, "run-*", "host_log.jsonl")
    host = [] if run_dir is None else tail_jsonl(run_dir / "host_log.jsonl", limit=400)
    heartbeat = projectx_heartbeat(host)
    faults = [e for e in host if e.get("kind") in ("plugin_faulted", "restore_failed")]
    marks = _read_json(live_state / "marks.json", MAX_MARKS_BYTES)
    plugins: dict[str, Any] = {}
    state_base = live_state / "plugins"
    if state_base.is_dir():
        for d in sorted(state_base.iterdir()):
            if not (d.is_dir() and PLUGIN_ID_RE.fullmatch(d.name)):
                continue
            item: dict[str, Any] = {"id": d.name, "status": None, "log": [], "has_raw": False}
            state = d / "plugin.json"
            with contextlib.suppress(OSError):
                item["updated_ns"] = int(state.stat().st_mtime * 1e9)
                item["has_raw"] = True
            item["status"] = _read_json(d / "status.json", MAX_STATUS_BYTES)
            item["pnl"] = live_pnl(item["status"], marks, now_ns)
            item["order_mode"] = None
            if run_dir is not None:
                plugin_log = run_dir / "plugins" / d.name / "plugin_log.jsonl"
                item["log"] = _said(tail_jsonl(plugin_log, limit=50))
                item["order_mode"] = last_order_mode(plugin_log)
            plugins[d.name] = item
    for e in host:
        pid = e.get("plugin")
        if isinstance(pid, str) and pid not in plugins and PLUGIN_ID_RE.fullmatch(pid):
            plugins[pid] = {
                "id": pid,
                "status": None,
                "log": [],
                "has_raw": False,
                "pnl": None,
                "order_mode": None
                if run_dir is None
                else last_order_mode(run_dir / "plugins" / pid / "plugin_log.jsonl"),
            }
    for pid, item in plugins.items():
        item["faulted"] = any(
            f.get("plugin") == pid for f in faults if f.get("kind") == "plugin_faulted"
        )
    today = paper_mod.today(runs_dir, live_state, run_dir=run_dir, runner_cfg=runner_cfg)
    return {
        "run_dir": None if run_dir is None else run_dir.name,
        "now_ns": now_ns,
        "host_log": _said(host[-200:]),
        "heartbeat": heartbeat,
        "skylit": _skylit(host, now_ns),
        "faults": faults[-50:],
        "plugins": list(plugins.values()),
        "marks": marks,
        "blocks": block_status(live_state),
        # Keyed maps, no "now": the payload only changes when a file does (§6.6).
        "paper": {"day": today["day"], "plugins": today["plugins"], "total": today["total"]},
        "accounts": today["accounts"],
    }


# ---------------------------------------------------------------- cards

DECISION_CARDS_FILE = "decision_cards.jsonl"
FINDING_CARDS_FILE = "cards.jsonl"
MAX_CARDS = 300


def card_publish(db: Db, store: ConfigStore) -> tuple[str | None, dict[str, bool]]:
    """The runner config the console would start and its ``plugins[].cards`` switches.

    A plugin missing from the map publishes (the default); any read error gives an
    empty map, so every plugin's cards show.
    """
    name: str | None = None
    try:
        name = str(runner_settings(db)["runner_config"])
        data = yaml.safe_load(store.current_path("runner", name).read_text(encoding="utf-8"))
        entries = data.get("plugins") if isinstance(data, dict) else None
        out: dict[str, bool] = {}
        for p in entries if isinstance(entries, list) else []:
            if isinstance(p, dict) and isinstance(p.get("id"), str):
                out[p["id"]] = p.get("cards", True) is not False
        return name, out
    except ConfigStoreError, OSError, KeyError, TypeError, ValueError, yaml.YAMLError:
        return name, {}


def _finding_row(plugin: str | None, line: dict[str, Any], source: str) -> dict[str, Any]:
    """A Finding_Card line (the fse.notify ``file`` sink), as a card row."""
    inner = line.get("card")
    body: dict[str, Any] = inner if isinstance(inner, dict) else line
    t_ns = _int(body.get("decision_time")) or 0
    return {
        "plugin": plugin,
        "t_ns": t_ns,
        "format": "finding_card",
        "title": "Premarket card" if body.get("kind") == "premarket" else "Finding_Card",
        "source": source,
        "raw": line,
    }


def _decision_rows(plugin: str, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Decision card rows; a ``result`` card joins the card its ``ref`` names."""
    rows: list[dict[str, Any]] = []
    for line in lines:
        card = line.get("card")
        if not isinstance(card, dict):
            continue
        rows.append(
            {
                "plugin": plugin,
                "t_ns": _int(line.get("t_ns")) or _int(card.get("as_of_ns")) or 0,
                "format": "decision",
                "kind": line.get("kind"),
                "card": card,
                "result": None,
                "raw": line,
            }
        )
    by_id = {r["card"].get("id"): r for r in rows if r["card"].get("action") != "result"}
    out: list[dict[str, Any]] = []
    for row in rows:
        card = row["card"]
        parent = by_id.get(card.get("ref")) if card.get("action") == "result" else None
        if parent is not None and parent["result"] is None:
            parent["result"] = {"card": card, "t_ns": row["t_ns"], "raw": row["raw"]}
        else:
            out.append(row)
    return out


def _run_day_prefix(run_name: str) -> str | None:
    """``run-20261015T135202Z-…`` → ``run-20261015``; else ``None``."""
    if len(run_name) >= 12 and run_name.startswith("run-") and run_name[4:12].isdigit():
        return run_name[:12]
    return None


def _runs_same_utc_day(runs_dir: Path, latest: Path) -> list[Path]:
    """Every ``run-*`` with ``host_log.jsonl`` on the same UTC calendar day as ``latest``.

    A runner restart starts a new ``run-*``; Cards used to show only the newest, so
    earlier plugins' decision cards vanished until their next slot. Same-day merge
    keeps the session visible.
    """
    prefix = _run_day_prefix(latest.name)
    try:
        dirs = [d for d in runs_dir.glob("run-*") if (d / "host_log.jsonl").is_file()]
    except OSError:
        return [latest]
    if prefix is None:
        return [latest]
    same = [d for d in dirs if d.name.startswith(prefix)]
    if not same:
        return [latest]
    same.sort(key=lambda d: (d / "host_log.jsonl").stat().st_mtime)
    return same


def _card_dedupe_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Stable identity so the same decision/Finding_Card from an older run is dropped."""
    plugin = row.get("plugin")
    if row.get("format") == "decision":
        card_obj = row.get("card")
        card: dict[str, Any] = card_obj if isinstance(card_obj, dict) else {}
        cid = card.get("id")
        # Prefer id (same slot after a restart); fall back to time when id is missing.
        return ("decision", plugin, cid if cid is not None else row.get("t_ns"))
    raw_obj = row.get("raw")
    raw: dict[str, Any] = raw_obj if isinstance(raw_obj, dict) else {}
    nested = raw.get("card")
    inner: dict[str, Any] = nested if isinstance(nested, dict) else raw
    decision_time = inner.get("decision_time")
    kind = inner.get("kind")
    t_ns = decision_time if decision_time is not None else row.get("t_ns")
    return ("finding_card", plugin, t_ns, kind)


def _collect_plugin_cards(
    runs_dir: Path, run_dir: Path, *, limit: int, seen: set[Path]
) -> list[dict[str, Any]]:
    """Decision + Finding_Card rows from one host run's ``plugins/<id>/`` dirs."""
    rows: list[dict[str, Any]] = []
    base = run_dir / "plugins"
    if not base.is_dir():
        return rows
    for d in sorted(base.iterdir()):
        if not (d.is_dir() and PLUGIN_ID_RE.fullmatch(d.name)):
            continue
        decision_path = d / DECISION_CARDS_FILE
        rows.extend(_decision_rows(d.name, tail_jsonl(decision_path, limit=limit)))
        finding = d / FINDING_CARDS_FILE
        seen.add(finding)
        source = str(finding.relative_to(runs_dir))
        rows.extend(_finding_row(d.name, ln, source) for ln in tail_jsonl(finding, limit=limit))
    return rows


def live_cards(
    runs_dir: Path,
    *,
    publish: dict[str, bool],
    plugin: str | None = None,
    limit: int = MAX_CARDS,
    runner_config: str | None = None,
) -> dict[str, Any]:
    """The Cards page: every plugin's cards from today's host runs, newest first.

    Per ``<run>/plugins/<id>/``: ``decision_cards.jsonl`` (the host's decision
    cards) and ``cards.jsonl`` (fse.notify Finding_Cards); plus the newest
    ``cards.jsonl`` anywhere under ``runs_dir`` (where fse.notify writes by
    default; its plugin is the ``plugins/<id>`` folder it is in, else ``null``).
    All ``run-*`` dirs on the same UTC day as the newest run are merged (a
    restart must not hide earlier plugins). Duplicate card ids across those
    runs are dropped, keeping the newest ``t_ns``. Cards of a plugin whose
    ``publish`` is false are left out and the plugin is reported ``hidden``.
    The filter lists every plugin in ``publish`` (count may be 0).
    """
    run_dir = _latest(runs_dir, "run-*", "host_log.jsonl")
    rows: list[dict[str, Any]] = []
    seen: set[Path] = set()
    day_runs = _runs_same_utc_day(runs_dir, run_dir) if run_dir is not None else []
    for one in day_runs:
        rows.extend(_collect_plugin_cards(runs_dir, one, limit=limit, seen=seen))
    legacy = _latest_file(runs_dir, FINDING_CARDS_FILE)
    if legacy is not None and legacy not in seen:
        parts = legacy.relative_to(runs_dir).parts
        owner = next(
            (
                parts[i + 1]
                for i in range(len(parts) - 2)
                if parts[i] == "plugins" and PLUGIN_ID_RE.fullmatch(parts[i + 1])
            ),
            None,
        )
        source = str(legacy.relative_to(runs_dir))
        rows.extend(_finding_row(owner, ln, source) for ln in tail_jsonl(legacy, limit=limit))
    rows.reverse()
    rows.sort(key=lambda r: r["t_ns"], reverse=True)
    deduped: list[dict[str, Any]] = []
    seen_keys: set[tuple[Any, ...]] = set()
    for r in rows:
        key = _card_dedupe_key(r)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(r)
    rows = deduped
    counts: dict[str, int] = {}
    for r in rows:
        if r["plugin"] is not None:
            counts[r["plugin"]] = counts.get(r["plugin"], 0) + 1
    ids = sorted(set(counts) | set(publish))
    plugins = [
        {
            "id": pid,
            "publish": publish.get(pid, True),
            "count": counts.get(pid, 0),
            "hidden": not publish.get(pid, True),
        }
        for pid in ids
    ]
    shown = [r for r in rows if r["plugin"] is None or publish.get(r["plugin"], True)]
    if plugin is not None:
        shown = [r for r in shown if r["plugin"] == plugin]
    return {
        "run_dir": None if run_dir is None else run_dir.name,
        "runner_config": runner_config,
        "plugins": plugins,
        "cards": shown[: max(0, limit)],
    }


def plugin_raw(live_state: Path, plugin_id: str) -> dict[str, Any] | None:
    """The plugin's raw ``plugin.json`` (book and plugin halves) and ``status.json``."""
    if not PLUGIN_ID_RE.fullmatch(plugin_id):
        return None
    d = live_state / "plugins" / plugin_id
    if not d.is_dir():
        return None
    return {
        "id": plugin_id,
        "state": _read_json(d / "plugin.json", MAX_RAW_BYTES),
        "status": _read_json(d / "status.json", MAX_STATUS_BYTES),
    }
