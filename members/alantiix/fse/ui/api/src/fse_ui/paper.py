"""Paper analytics: today and history per plugin, per account and in total (design §6).

Pure readers over the runner's files, no FastAPI:

- books: ``<live-state>/plugins/<id>/plugin.json`` (``BROKER_CODEC``);
- accounts: ``<live-state>/accounts/<id>/account.json`` (``ACCOUNT_CODEC``) and
  ``nexus_status.json``; a plugin in no account's ``plugins`` is on its
  implicit account ``implicit:<id>``;
- history: every ``<runs>/run-*/plugins/<id>/trades.jsonl`` (the host journal),
  deduped on ``line_id``; trades rebuilt with :func:`fse.live.host.journal.journal_trades`
  so metrics come from :func:`fse.analytics.metrics.summarize` and the curve
  from :func:`fse_ui.runs.equity_curve` (no metric math here);
- the host log of each run, scanned incrementally (``day_open`` days for the
  metrics sessions, ``journal_write_failed``/``journal_write_recovered`` for
  :func:`journal_health`);
- open P&L: :func:`fse_ui.runner.live_pnl`, the Live page's computation, over the
  host's ``marks.json`` (latest quote or 1-minute close) and each plugin's
  ``status.json`` positions (the broker's exit fee), so /live and /analytics show
  one number for the same position.

Money leaves this module as plain decimal strings (``"50000.00"``). Nothing
here raises on a bad file: a corrupt line is skipped and counted in
``skipped_lines``, an unreadable book or account becomes an item with an
``error``, a missing directory is empty (C-F7). Nothing here reads the wall
clock, so the live snapshot is stable while the files are.
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import re
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal, TypedDict

if TYPE_CHECKING:
    from fse.engine.types import Trade
    from fse.live.host.broker import PluginBrokerState
    from fse.live.host.config import RunnerConfig

log = logging.getLogger(__name__)

PLUGIN_ID_RE: Final = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
ACCOUNT_ID_RE: Final = re.compile(r"^(implicit:)?[a-z][a-z0-9_]{0,31}$")
DATE_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")
IMPLICIT_PREFIX: Final = "implicit:"
MAX_SPAN_DAYS: Final = 366
DEFAULT_SPAN_DAYS: Final = 365
MAX_STATE_READ_BYTES: Final = 8 * 1024 * 1024
HOST_LOG: Final = "host_log.jsonl"
JOURNAL: Final = "trades.jsonl"
_ZERO: Final = Decimal("0.00")
_TICK_POINTS: Final = Decimal("0.25")
_SKIP: Final = (ValueError, KeyError, TypeError, ArithmeticError)

type Line = Mapping[str, Any]


class JournalHealth(TypedDict):
    failed: bool
    failed_at_ns: int | None


@dataclass(frozen=True, slots=True)
class Scope:
    """Whose trades: one plugin, one named or implicit account, or every plugin."""

    kind: Literal["plugin", "account", "implicit", "total"]
    id: str | None = None

    @staticmethod
    def account(account_id: str) -> Scope:
        if account_id.startswith(IMPLICIT_PREFIX):
            return Scope("implicit", account_id.removeprefix(IMPLICIT_PREFIX))
        return Scope("account", account_id)

    def holds(self, line: Line) -> bool:
        if self.kind == "total":
            return True
        if self.kind == "plugin":
            return bool(line["plugin"] == self.id)
        if self.kind == "account":
            return bool(line["account"] == self.id)
        return bool(line["plugin"] == self.id and line["account"] is None)


def _money(d: Decimal) -> str:
    return format(d, ".2f")


def _price(ticks: int) -> str:
    return _money(Decimal(ticks) * _TICK_POINTS)


def _mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


# ---------------------------------------------------------------- dates


def globex_today(now_ns: int) -> date:
    """The Globex day holding ``now_ns``; in the weekend gap the last closed one."""
    from fse.live.host.dayclock import globex_day_at, next_open, previous_globex_day

    found = globex_day_at(now_ns)
    return found.day if found is not None else previous_globex_day(next_open(now_ns)).day


def _day(text: str | None, name: str) -> date | None:
    if text is None:
        return None
    if not DATE_RE.fullmatch(text):
        raise ValueError(f"{name} must be YYYY-MM-DD, got {text!r}")
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{name} {text!r} is not a date") from None


def parse_range(start: str | None, end: str | None, today: date) -> tuple[date, date]:
    """``(from, to)``: defaults ``to`` = ``today``, ``from`` = ``to`` - 365 days.

    ``ValueError`` (a 422) for a bad date, ``from > to`` or a span over 366 days.
    """
    to = _day(end, "to") or today
    frm = _day(start, "from") or to - timedelta(days=DEFAULT_SPAN_DAYS)
    if frm > to:
        raise ValueError(f"from {frm} is after to {to}")
    if (to - frm).days > MAX_SPAN_DAYS:
        raise ValueError(f"the range is {(to - frm).days} days; at most {MAX_SPAN_DAYS}")
    return frm, to


# ---------------------------------------------------------------- host log scan


@dataclass(slots=True)
class _Scan:
    inode: int
    offset: int = 0
    days: set[str] = field(default_factory=set)
    health: dict[str, JournalHealth] = field(default_factory=dict)


_SCANS: dict[str, _Scan] = {}
_SCAN_LOCK: Final = threading.Lock()
"""REST and the WS poll scan from worker threads; a scan advances a shared offset."""


def _read_from(path: Path, offset: int) -> bytes:
    """The bytes of ``path`` from ``offset`` to its end (a seam for the tests)."""
    with path.open("rb") as fh:
        fh.seek(offset)
        return fh.read()


def _apply(scan: _Scan, raw: bytes) -> None:
    if b'"day_open"' not in raw and b"journal_write_" not in raw:
        return
    try:
        obj = json.loads(raw)
    except ValueError:
        return
    if not isinstance(obj, dict):
        return
    kind, plugin = obj.get("kind"), obj.get("plugin")
    if kind == "day_open" and isinstance(obj.get("day"), str):
        scan.days.add(obj["day"])
    elif kind == "journal_write_failed" and isinstance(plugin, str):
        t_ns = obj.get("t_ns")
        at = t_ns if isinstance(t_ns, int) and not isinstance(t_ns, bool) else None
        scan.health[plugin] = {"failed": True, "failed_at_ns": at}
    elif kind == "journal_write_recovered" and isinstance(plugin, str):
        scan.health[plugin] = {"failed": False, "failed_at_ns": None}


def _scan(path: Path) -> _Scan | None:
    """The host log's state, reading only the bytes added since the last call.

    Cached by path as ``(inode, offset, state)``: a different inode or a file
    shorter than the offset (rotation, truncation) is rescanned from 0. Only
    complete lines are consumed (a torn last line waits). ``None`` when the
    file is missing or unreadable.
    """
    with _SCAN_LOCK:
        return _scan_locked(path)


def _scan_locked(path: Path) -> _Scan | None:
    key = str(path)
    try:
        st = path.stat()
    except FileNotFoundError:
        _SCANS.pop(key, None)
        return None
    except OSError as exc:
        log.warning("cannot read %s: %s", path, type(exc).__name__)
        return None
    cur = _SCANS.get(key)
    if cur is None or cur.inode != st.st_ino or st.st_size < cur.offset:
        cur = _Scan(st.st_ino)
    if st.st_size > cur.offset:
        try:
            data = _read_from(path, cur.offset)
        except OSError as exc:
            log.warning("cannot read %s: %s", path, type(exc).__name__)
            return None
        end = data.rfind(b"\n")
        if end >= 0:
            for raw in data[:end].split(b"\n"):
                _apply(cur, raw)
            cur.offset += end + 1
    _SCANS[key] = cur
    return cur


def newest_run(runs_dir: Path) -> Path | None:
    """The ``run-*`` dir whose host log changed last (the run ``live_snapshot`` shows)."""
    try:
        dirs = [d for d in runs_dir.glob("run-*") if (d / HOST_LOG).is_file()]
    except OSError:
        return None
    best: tuple[float, Path] | None = None
    for d in dirs:
        with contextlib.suppress(OSError):
            m = (d / HOST_LOG).stat().st_mtime
            if best is None or m > best[0]:
                best = (m, d)
    return None if best is None else best[1]


def journal_health(run_dir: Path | None) -> dict[str, JournalHealth]:
    """Per plugin, whether the run's last journal line event is a failure (§4.3).

    Read from the run's whole host log (incrementally), not from a tail, so a
    ``journal_write_failed`` stays visible however many heartbeats follow it.
    """
    if run_dir is None:
        return {}
    scan = _scan(run_dir / HOST_LOG)
    if scan is None:
        return {}
    return {
        k: {"failed": v["failed"], "failed_at_ns": v["failed_at_ns"]}
        for k, v in sorted(scan.health.items())
    }


def _session_days(runs_dir: Path) -> set[str]:
    days: set[str] = set()
    try:
        logs = sorted(runs_dir.glob(f"run-*/{HOST_LOG}"))
    except OSError:
        return days
    for path in logs:
        scan = _scan(path)
        if scan is not None:
            days |= scan.days
    return days


# ---------------------------------------------------------------- journals


_JOURNALS: dict[str, tuple[int, int, tuple[Line, ...], int]] = {}


def _decimal(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return Decimal(value).is_finite()
    except InvalidOperation:
        return False


def _trade_line_ok(obj: Mapping[str, Any]) -> bool:
    exit_ns = obj.get("exit_ns")
    account = obj.get("account")
    plugin = obj.get("plugin")
    day = obj.get("globex_day")
    if not (
        isinstance(obj.get("line_id"), str)
        and isinstance(obj.get("trade_id"), str)
        and isinstance(plugin, str)
        and PLUGIN_ID_RE.fullmatch(plugin)
        and (account is None or isinstance(account, str))
        and isinstance(exit_ns, int)
        and not isinstance(exit_ns, bool)
        and isinstance(day, str)
        and DATE_RE.fullmatch(day)
        and _decimal(obj.get("net"))
    ):
        return False
    try:
        date.fromisoformat(day)
    except ValueError:
        return False
    return True


def _journal_file(path: Path) -> tuple[tuple[Line, ...], int]:
    """``(trade lines, skipped lines)`` of one journal, cached by ``(path, mtime_ns, size)``."""
    key = str(path)
    try:
        st = path.stat()
    except OSError:
        _JOURNALS.pop(key, None)
        return (), 0
    hit = _JOURNALS.get(key)
    if hit is not None and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2], hit[3]
    try:
        data = path.read_bytes()
    except OSError as exc:
        log.warning("cannot read %s: %s", path, type(exc).__name__)
        return (), 0
    lines: list[Line] = []
    skipped = 0
    for raw in data.splitlines():
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            skipped += 1
            continue
        if not isinstance(obj, dict):
            skipped += 1
            continue
        if obj.get("kind") != "trade":
            continue  # fill lines: not needed for analytics
        if not _trade_line_ok(obj):
            skipped += 1
            continue
        lines.append(obj)
    out = tuple(lines)
    _JOURNALS[key] = (st.st_mtime_ns, st.st_size, out, skipped)
    return out, skipped


@dataclass(frozen=True, slots=True)
class _Journals:
    lines: tuple[Line, ...]
    """Every trade line of every run, deduped on ``line_id`` (first seen wins)."""
    skipped: int
    plugins: frozenset[str]
    """Plugins with a journal file in some run."""
    mtime_ns: int


def _journals(runs_dir: Path) -> _Journals:
    try:
        paths = sorted(runs_dir.glob(f"run-*/plugins/*/{JOURNAL}"))
    except OSError:
        paths = []
    seen: dict[str, Line] = {}
    skipped, newest = 0, 0
    plugins: set[str] = set()
    for path in paths:
        plugins.add(path.parent.name)
        lines, bad = _journal_file(path)
        skipped += bad
        newest = max(newest, _mtime_ns(path))
        for line in lines:
            seen.setdefault(line["line_id"], line)
    return _Journals(tuple(seen.values()), skipped, frozenset(plugins), newest)


def _in_range(line: Line, frm: date | None, to: date | None) -> bool:
    day: str = line["globex_day"]
    return (frm is None or day >= frm.isoformat()) and (to is None or day <= to.isoformat())


def _groups(lines: Iterable[Line]) -> list[list[Line]]:
    """Legs grouped by ``trade_id``, each in exit order (a multi-leg trade counts once)."""
    by_id: dict[str, list[Line]] = {}
    for line in lines:
        by_id.setdefault(line["trade_id"], []).append(line)
    return [sorted(g, key=lambda x: (x["exit_ns"], x["line_id"])) for g in by_id.values()]


def _nets(lines: Iterable[Line]) -> list[Decimal]:
    return [sum((Decimal(x["net"]) for x in g), _ZERO) for g in _groups(lines)]


def _win_rate(nets: Sequence[Decimal]) -> float | None:
    """Closed trades with net > 0 over closed trades (a ratio, not money); ``None`` with none."""
    if not nets:
        return None
    return round(sum(1 for n in nets if n > 0) / len(nets), 4)


def _rebuild(lines: Iterable[Line]) -> tuple[list[Trade], int]:
    """Engine trades of the lines; a group that does not rebuild is skipped and counted."""
    from fse.live.host.journal import journal_trades

    trades: list[Trade] = []
    skipped = 0
    for legs in _groups(lines):
        try:
            trades.extend(journal_trades(legs))
        except _SKIP:
            skipped += len(legs)
    return trades, skipped


# ---------------------------------------------------------------- history routes


def _select(runs_dir: Path, scope: Scope, frm: date, to: date) -> tuple[list[Line], int]:
    j = _journals(runs_dir)
    return [x for x in j.lines if scope.holds(x) and _in_range(x, frm, to)], j.skipped


def history_trades(runs_dir: Path, scope: Scope, frm: date, to: date, limit: int) -> dict[str, Any]:
    """Journal trade lines in ``[frm, to]``, newest exit first, cut to ``limit``."""
    lines, skipped = _select(runs_dir, scope, frm, to)
    lines.sort(key=lambda x: (x["exit_ns"], x["line_id"]), reverse=True)
    return {
        "trades": [dict(x) for x in lines[:limit]],
        "total": len(lines),
        "skipped_lines": skipped,
        "truncated": len(lines) > limit,
    }


def history_equity(runs_dir: Path, scope: Scope, frm: date, to: date) -> dict[str, Any]:
    """:func:`fse_ui.runs.equity_curve` over the scope's rebuilt trades."""
    from fse.logio.canonical_json import to_jsonable

    from fse_ui.runs import equity_curve

    lines, skipped = _select(runs_dir, scope, frm, to)
    trades, bad = _rebuild(lines)
    items: list[dict[str, Any]] = [to_jsonable(t) for t in trades]  # type: ignore[misc]
    return {**equity_curve(items), "skipped_lines": skipped + bad}


def history_metrics(runs_dir: Path, scope: Scope, frm: date, to: date) -> dict[str, Any]:
    """``summarize`` over the scope's trades; the sessions are the run days in range plus
    every trade's own session (a trade entered before ``frm`` or held over the break)."""
    from fse.analytics.metrics import MetricsCfg, MetricsError, metrics_to_jsonable, summarize

    lines, skipped = _select(runs_dir, scope, frm, to)
    trades, bad = _rebuild(lines)
    lo, hi = frm.isoformat(), to.isoformat()
    sessions = {date.fromisoformat(d) for d in _session_days(runs_dir) if lo <= d <= hi}
    sessions |= {t.setup_key.session for t in trades}
    out: dict[str, Any] = {"source": "journal", "skipped_lines": skipped + bad}
    try:
        metrics = summarize(trades, sorted(sessions), MetricsCfg())
    except MetricsError as exc:
        log.warning("paper metrics: %s", exc)
        return {**out, "metrics": None, "error": str(exc)}
    return {**out, "metrics": metrics_to_jsonable(metrics, places=6)}


# ---------------------------------------------------------------- state files


@dataclass(frozen=True, slots=True)
class _Book:
    state: PluginBrokerState | None
    error: str | None
    mtime_ns: int


_BOOKS: dict[str, tuple[int, int, _Book]] = {}


def _read_book(path: Path) -> _Book | None:
    """The decoded ``plugin.json`` (cached by mtime and size); ``None`` when missing."""
    from fse.live.host.broker import BROKER_CODEC, PluginBrokerState

    key = str(path)
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return _Book(None, f"cannot read plugin.json: {type(exc).__name__}", 0)
    hit = _BOOKS.get(key)
    if hit is not None and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    if st.st_size > MAX_STATE_READ_BYTES:
        book = _Book(None, f"plugin.json is {st.st_size} bytes", st.st_mtime_ns)
    else:
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            state = BROKER_CODEC.decode(obj["book"])
            if not isinstance(state, PluginBrokerState):
                raise TypeError(type(state).__name__)
            book = _Book(state, None, st.st_mtime_ns)
        except (OSError, *_SKIP) as exc:
            log.warning("cannot decode %s: %s", path, type(exc).__name__)
            book = _Book(None, f"plugin.json is unreadable: {type(exc).__name__}", st.st_mtime_ns)
    _BOOKS[key] = (st.st_mtime_ns, st.st_size, book)
    return book


@dataclass(frozen=True, slots=True)
class _AccountFile:
    id: str
    state: Any
    """The decoded ``PaperAccountState``, or ``None`` when unreadable."""
    view: Mapping[str, Any] | None
    error: str | None
    mtime_ns: int


def _read_account(path: Path, account_id: str) -> _AccountFile:
    from fse.live.host.accounts import ACCOUNT_CODEC, PaperAccountState

    mtime = _mtime_ns(path)
    try:
        if path.stat().st_size > MAX_STATE_READ_BYTES:
            raise ValueError("too large")
        doc = json.loads(path.read_text(encoding="utf-8"))
        state = ACCOUNT_CODEC.decode(doc["account"])
        if not isinstance(state, PaperAccountState):
            raise TypeError(type(state).__name__)
        view = doc.get("view")
        return _AccountFile(
            account_id, state, view if isinstance(view, dict) else None, None, mtime
        )
    except (OSError, *_SKIP) as exc:
        log.warning("cannot decode %s: %s", path, type(exc).__name__)
        return _AccountFile(
            account_id, None, None, f"account.json is unreadable: {type(exc).__name__}", mtime
        )


def _accounts_on_disk(live_state: Path) -> dict[str, _AccountFile]:
    from fse.live.host.accounts import ACCOUNT_STATE_FILE_NAME, ACCOUNTS_DIR_NAME

    root = live_state / ACCOUNTS_DIR_NAME
    out: dict[str, _AccountFile] = {}
    try:
        dirs = sorted(d for d in root.iterdir() if d.is_dir() and PLUGIN_ID_RE.fullmatch(d.name))
    except OSError:
        return out
    for d in dirs:
        path = d / ACCOUNT_STATE_FILE_NAME
        if path.is_file():
            out[d.name] = _read_account(path, d.name)
    return out


def _nexus_status(live_state: Path, account_id: str) -> dict[str, Any] | None:
    from fse.live.host.accounts import ACCOUNTS_DIR_NAME
    from fse.live.host.nexus import STATUS_FILE_NAME

    path = live_state / ACCOUNTS_DIR_NAME / account_id / STATUS_FILE_NAME
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        return {"error": f"nexus_status.json is unreadable: {type(exc).__name__}"}
    if not isinstance(doc, dict):
        return {"error": "nexus_status.json is not an object"}
    return {k: v for k, v in doc.items() if k not in ("local", "nexus")}


def _plugin_block_kinds(directory: Path) -> list[str]:
    from fse.live.state_store import BlockStore, BlocksUnreadable
    from fse.logio.log_writer import LogWriter
    from fse.secrets.env import EnvView

    writer = LogWriter.from_env(EnvView({}, {}), stdout=io.StringIO(), stderr=io.StringIO())
    found = BlockStore(writer, directory).read()
    if isinstance(found, BlocksUnreadable):
        return ["blocks_unreadable"]
    return [b.kind for b in found]


# ---------------------------------------------------------------- marks


def _open_pnl(
    state: PluginBrokerState, status: Mapping[str, Any] | None, marks: Mapping[str, Any] | None
) -> dict[str, Any]:
    """:func:`fse_ui.runner.live_pnl` of the book's open positions: the Live page's
    computation (``realized + ticks x tick value x qty - exit fee x qty`` at the
    latest mark), so both pages agree. Positions come from the book itself
    (:func:`fse.live.host.status.book_view`) and the exit fee per instrument from
    the host's ``status.json`` (``None``, as there, without one). ``now_ns`` is 0:
    the mark's age is the Live page's concern, not a number here."""
    from fse.live.host.status import book_view

    from fse_ui.runner import live_pnl

    fees: dict[str, Decimal] = {}
    for pos in (status or {}).get("positions") or []:
        if isinstance(pos, Mapping) and isinstance(pos.get("instrument"), str):
            with contextlib.suppress(*_SKIP):
                if pos.get("exit_fee") is not None:
                    fees[pos["instrument"]] = Decimal(str(pos["exit_fee"]))
    view = book_view(state, fees.get)
    found = live_pnl({"today": {"net": str(state.day_net)}, **view}, dict(marks or {}), 0)
    assert found is not None  # a dict with "today" always values
    return found


# ---------------------------------------------------------------- today


def _cfg_plugins(cfg: RunnerConfig | None) -> dict[str, Any]:
    """Enabled host-brokered plugin entries of the runner config, by id."""
    if cfg is None:
        return {}
    return {p.id: p for p in cfg.enabled_plugins if p.risk is not None}


def _plugin_ids(live_state: Path, cfg: RunnerConfig | None) -> list[str]:
    ids = set(_cfg_plugins(cfg))
    base = live_state / "plugins"
    with contextlib.suppress(OSError):
        for d in base.iterdir():
            if d.is_dir() and PLUGIN_ID_RE.fullmatch(d.name) and (d / "plugin.json").is_file():
                ids.add(d.name)
    return sorted(ids)


def _members(files: Mapping[str, _AccountFile], cfg: RunnerConfig | None) -> dict[str, str]:
    """Plugin -> named account: each account file's ``plugins`` (the config's members
    for a file that does not decode)."""
    out: dict[str, str] = {}
    for acct in files.values():
        if acct.state is not None:
            plugins: Iterable[str] = acct.state.plugins
        elif cfg is not None:
            plugins = [p.id for p in cfg.enabled_plugins if p.account == acct.id]
        else:
            plugins = ()
        for p in plugins:
            out.setdefault(p, acct.id)
    return out


def _plugin_today(
    pid: str,
    *,
    live_state: Path,
    account: str,
    lines: Sequence[Line],
    day: date | None,
    health: Mapping[str, JournalHealth],
    entry: Any,
    marks: Mapping[str, Any] | None,
    account_blocks: Sequence[str],
) -> tuple[dict[str, Any], list[Decimal], int]:
    """``(PluginToday, today's closed-trade nets, newest mtime of its plugin.json,
    blocks.json and status.json)``; open P&L at ``marks`` (:func:`_open_pnl`)."""
    from fse.live.host.accounts import FORWARD_TEST_START
    from fse.live.host.broker import tag_of
    from fse.live.state_store import BLOCKS_FILE_NAME

    from fse_ui.runner import MAX_STATUS_BYTES, _read_json

    directory = live_state / "plugins" / pid
    book = _read_book(directory / "plugin.json")
    mine = [x for x in lines if x["plugin"] == pid]
    today_nets = [] if day is None else _nets(x for x in mine if x["globex_day"] == day.isoformat())
    cumulative = sum(
        (Decimal(x["net"]) for x in mine if _in_range(x, FORWARD_TEST_START, day)), _ZERO
    )
    item: dict[str, Any] = {
        "id": pid,
        "account": account,
        "realized": "0.00",
        "unrealized": "0.00",
        "open_positions": [],
        "working_orders": [],
        "day_trades": len(today_nets),
        "win_rate_today": _win_rate(today_nets),
        "cumulative_net": _money(cumulative),
        "caps": None,
        "blocks": [*_plugin_block_kinds(directory), *account_blocks],
        "price_source": {},
        "journal_failed": pid in health and health[pid]["failed"],
    }
    state = None if book is None else book.state
    if book is not None and book.error is not None:
        item["error"] = book.error
    used = 0
    newest = max(
        0 if book is None else book.mtime_ns,
        _mtime_ns(directory / BLOCKS_FILE_NAME),
        _mtime_ns(directory / "status.json"),
    )
    unrealized: str | None = "0.00"
    if state is not None:
        positions = []
        sources: dict[str, Any] = {}
        pnl = _open_pnl(state, _read_json(directory / "status.json", MAX_STATUS_BYTES), marks)
        unrealized = pnl["day"]["open"]
        for trade in state.book.trades.values():
            tag = tag_of(trade.setup_key)
            at = pnl["positions"].get(tag) or {}
            if at.get("mark_source") is not None:
                sources[trade.instrument] = {
                    "source": at["mark_source"],
                    "t_ns": at["mark_received_ns"],
                }
            positions.append(
                {
                    "tag": tag,
                    "instrument": trade.instrument,
                    "side": trade.direction,
                    "qty": trade.open_qty,
                    "entry_price": _price(trade.entry_fill.price),
                    "open_pnl": at.get("open_net"),
                }
            )
        orders = []
        for order in state.book.working_orders():
            tag = (
                tag_of(order.setup_key)
                if order.setup_key is not None
                else order.client_id.split(":")[1]
                if order.client_id.count(":") >= 2
                else order.client_id
            )
            orders.append(
                {
                    "tag": tag,
                    "leg": order.client_id.rsplit(":", 1)[-1],
                    "type": order.kind,
                    "side": order.side,
                    "qty": order.qty,
                    "price": None if order.price is None else _price(order.price),
                }
            )
        used = sum(abs(q) for q in state.book.positions().values())
        used += sum(state.book.working_entry_contracts().values())
        # A book left on an earlier Globex day (a disabled, removed or stopped plugin)
        # has realized nothing today: its ``day_net`` belongs to that day.
        current = state.day is not None and state.day == day
        item.update(
            realized=_money(state.day_net) if current else "0.00",
            unrealized=unrealized,
            open_positions=positions,
            working_orders=orders,
            price_source=sources,
        )
    risk = None if entry is None else entry.risk
    if risk is not None:
        item["caps"] = {
            "max_contracts": risk.max_contracts,
            "used": used,
            "max_daily_loss": None if risk.max_daily_loss is None else _money(risk.max_daily_loss),
            "day_net": item["realized"],
        }
    return item, today_nets, newest


def _named_account(
    acct: _AccountFile, live_state: Path, cfg: RunnerConfig | None, members: Sequence[str]
) -> dict[str, Any]:
    entry = None if cfg is None else next((a for a in cfg.accounts if a.id == acct.id), None)
    unused = (
        not any(p.account == acct.id for p in cfg.enabled_plugins)
        if cfg is not None
        else acct.state is not None and not acct.state.plugins
    )
    s = acct.state
    item: dict[str, Any] = {
        "id": acct.id,
        "implicit": False,
        "unused": unused,
        "plugins": list(members) if s is None else list(s.plugins),
        "preset": None if entry is None else entry.preset,
    }
    if s is None:
        for name in _STATE_FIELDS:
            item[name] = None
        item.update(view=None, nexus=_nexus_status(live_state, acct.id), error=acct.error)
        return item
    equity = s.balance + s.unrealized

    def opt(m: Decimal | None) -> str | None:
        return None if m is None else _money(m)

    item.update(
        starting_balance=_money(s.starting_balance),
        balance=_money(s.balance),
        realized=_money(s.realized),
        unrealized=_money(s.unrealized),
        equity=_money(equity),
        hwm=_money(s.hwm),
        eod_high=_money(s.eod_high),
        drawdown=_money(max(_ZERO, s.hwm - equity)),
        mll_floor=opt(s.mll_floor),
        day_net=_money(s.day_net),
        trading_days=s.trading_days,
        profit_target=_money(s.profit_target),
        status=s.status,
        breach_reason=s.breach_reason,
        breached_at=s.breached_at,
        passed_at=s.passed_at,
        rules_changed_at=s.rules_changed_at,
        view=None if acct.view is None else dict(acct.view),
        nexus=_nexus_status(live_state, acct.id),
    )
    return item


_STATE_FIELDS: Final = (
    "starting_balance",
    "balance",
    "realized",
    "unrealized",
    "equity",
    "hwm",
    "eod_high",
    "drawdown",
    "mll_floor",
    "day_net",
    "trading_days",
    "profit_target",
    "status",
    "breach_reason",
    "breached_at",
    "passed_at",
    "rules_changed_at",
)


def _implicit_account(plugin: Mapping[str, Any]) -> dict[str, Any]:
    balance = Decimal(plugin["cumulative_net"])
    open_ = plugin["unrealized"]
    unrealized = None if open_ is None else Decimal(open_)
    item: dict[str, Any] = {
        "id": f"{IMPLICIT_PREFIX}{plugin['id']}",
        "implicit": True,
        "unused": False,
        "plugins": [plugin["id"]],
        "preset": None,
    }
    for name in _STATE_FIELDS:
        item[name] = None
    item.update(
        starting_balance="0.00",
        balance=_money(balance),
        realized=_money(balance),
        unrealized=None if unrealized is None else _money(unrealized),
        equity=None if unrealized is None else _money(balance + unrealized),
        day_net=plugin["realized"],
        status="active",
        view=None,
        nexus=None,
    )
    return item


def _sum_open(values: Iterable[str | None]) -> str | None:
    """The open P&L total; ``None`` when any plugin's open P&L has no mark (as live_pnl)."""
    total = _ZERO
    for v in values:
        if v is None:
            return None
        total += Decimal(v)
    return _money(total)


def today(
    runs_dir: Path,
    live_state: Path,
    *,
    run_dir: Path | None,
    runner_cfg: RunnerConfig | None = None,
) -> dict[str, Any]:
    """Today per plugin, in total and per account (§6.1).

    ``{"day", "plugins": {id: PluginToday}, "total": Today, "accounts": {id: AccountToday},
    "as_of_ns", "skipped_lines"}``. ``day`` is the books' Globex day (``None`` in
    the weekend gap); ``as_of_ns`` the newest mtime among the files read.
    """
    files = _accounts_on_disk(live_state)
    members = _members(files, runner_cfg)
    journals = _journals(runs_dir)
    health = journal_health(run_dir)
    cfg_entries = _cfg_plugins(runner_cfg)
    from fse_ui.runner import MAX_MARKS_BYTES, _read_json

    marks = _read_json(live_state / "marks.json", MAX_MARKS_BYTES)
    ids = _plugin_ids(live_state, runner_cfg)
    days = [
        b.state.day
        for pid in ids
        if (b := _read_book(live_state / "plugins" / pid / "plugin.json")) is not None
        and b.state is not None
        and b.state.day is not None
    ]
    day = max(days, default=None)
    plugins: dict[str, Any] = {}
    all_nets: list[Decimal] = []
    newest = max(
        [
            journals.mtime_ns,
            _mtime_ns(live_state / "marks.json"),
            *(a.mtime_ns for a in files.values()),
        ]
        + ([] if run_dir is None else [_mtime_ns(run_dir / HOST_LOG)])
    )
    for pid in ids:
        named = members.get(pid)
        blocks: list[str] = []
        if named is not None:
            view = files[named].view
            found = None if view is None else view.get("blocks")
            blocks = [b for b in found if isinstance(b, str)] if isinstance(found, list) else []
        item, nets, mtime = _plugin_today(
            pid,
            live_state=live_state,
            account=named or f"{IMPLICIT_PREFIX}{pid}",
            lines=journals.lines,
            day=day,
            health=health,
            entry=cfg_entries.get(pid),
            marks=marks,
            account_blocks=blocks,
        )
        plugins[pid] = item
        all_nets += nets
        newest = max(newest, mtime)
    total = {
        "realized": _money(sum((Decimal(p["realized"]) for p in plugins.values()), _ZERO)),
        "unrealized": _sum_open(p["unrealized"] for p in plugins.values()),
        "day_trades": len(all_nets),
        "win_rate_today": _win_rate(all_nets),
        "cumulative_net": _money(
            sum((Decimal(p["cumulative_net"]) for p in plugins.values()), _ZERO)
        ),
    }
    accounts: dict[str, Any] = {}
    for acct_id, acct in files.items():
        mine = sorted(p for p, a in members.items() if a == acct_id)
        accounts[acct_id] = _named_account(acct, live_state, runner_cfg, mine)
    for pid, item in plugins.items():
        if pid not in members:
            implicit = _implicit_account(item)
            accounts[implicit["id"]] = implicit
    return {
        "day": None if day is None else day.isoformat(),
        "plugins": plugins,
        "total": total,
        "accounts": accounts,
        "as_of_ns": newest or None,
        "skipped_lines": journals.skipped,
    }


# ---------------------------------------------------------------- 404 checks


def plugin_known(runs_dir: Path, live_state: Path, plugin_id: str) -> bool:
    """A state dir, a journal in some run, or a member of some account file."""
    if (live_state / "plugins" / plugin_id).is_dir():
        return True
    if plugin_id in _journals(runs_dir).plugins:
        return True
    return plugin_id in _members(_accounts_on_disk(live_state), None)


def account_known(
    runs_dir: Path, live_state: Path, account_id: str, runner_cfg: RunnerConfig | None
) -> bool:
    """An implicit account of a known plugin; a named one with a state dir, in the
    runner config, or named by some journal line."""
    from fse.live.host.accounts import ACCOUNTS_DIR_NAME

    if account_id.startswith(IMPLICIT_PREFIX):
        return plugin_known(runs_dir, live_state, account_id.removeprefix(IMPLICIT_PREFIX))
    if (live_state / ACCOUNTS_DIR_NAME / account_id).is_dir():
        return True
    if runner_cfg is not None and any(a.id == account_id for a in runner_cfg.accounts):
        return True
    return any(x["account"] == account_id for x in _journals(runs_dir).lines)


def _clear_caches() -> None:
    """Forget every cached file (tests)."""
    for cache in (_SCANS, _JOURNALS, _BOOKS):
        cache.clear()


__all__ = [
    "ACCOUNT_ID_RE",
    "IMPLICIT_PREFIX",
    "PLUGIN_ID_RE",
    "JournalHealth",
    "Scope",
    "account_known",
    "globex_today",
    "history_equity",
    "history_metrics",
    "history_trades",
    "journal_health",
    "newest_run",
    "parse_range",
    "plugin_known",
    "today",
]
