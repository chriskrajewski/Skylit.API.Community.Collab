"""A ring of recent Skylit and ProjectX calls for the console.

The runner and the console share one JSONL file (``api_calls.jsonl`` in the
live-state directory). Each line is one attempt: time, API, method, path,
status, latency, a truncated error, the caller when one is known, and a
truncated request and response so a row can expand. The file keeps the newest
500 calls from the last 24 hours and is rewritten under a lock, so a refresh
of the console still shows them.

Authorization and other credential headers are dropped, not stored. Bodies
and any remaining header values pass through :func:`sanitize` (bearer tokens,
``sk_`` keys, and credential-shaped assignments) and any extra secret values
the caller still has in hand. A body longer than :data:`BODY_LIMIT` is cut,
and the row says it was truncated. Event-stream frames are not stored.
"""

from __future__ import annotations

import contextvars
import fcntl
import json
import os
import re
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Final, Literal, assert_never

__all__ = [
    "BODY_LIMIT",
    "FILE_NAME",
    "MAX_CALLS",
    "PATH_ENV",
    "STREAM_BODY_NOTE",
    "WINDOW_NS",
    "active_path",
    "bind_caller",
    "body_text",
    "current_caller",
    "dump_body",
    "enable",
    "projectx_outcome",
    "query",
    "record",
    "reset",
    "safe_caller",
    "safe_headers",
    "sanitize",
    "skylit_error_text",
    "unbind_caller",
]

FILE_NAME: Final = "api_calls.jsonl"
PATH_ENV: Final = "FSE_API_LOG_PATH"
MAX_CALLS: Final = 500
WINDOW_NS: Final = 24 * 60 * 60 * 1_000_000_000
ERROR_LIMIT: Final = 240
BODY_LIMIT: Final = 4_000
"""Characters kept from a request or response body. The row records that the rest was cut."""
STREAM_BODY_NOTE: Final = "Event stream. Frames are not stored."

_DROP_HEADER: Final = (
    "authorization",
    "token",
    "secret",
    "password",
    "api-key",
    "apikey",
    "cookie",
)

type ApiName = Literal["skylit", "projectx"]
type ApiFilter = Literal["all", "skylit", "projectx"]
type StatusFilter = Literal["all", "errors"]

_APIS: Final = frozenset({"skylit", "projectx"})
_METHODS: Final = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"})
_CALLER: Final = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_PATH: Final = re.compile(r"/[A-Za-z0-9_./-]{1,180}")
_BEARER: Final = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{6,}")
_SK_KEY: Final = re.compile(r"\bsk_(?:live|test)?_?[A-Za-z0-9_-]{8,}\b")
_ASSIGN: Final = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|token|authorization|password|secret|username|userName)"
    r"(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|\S+)"
)
_JSON_SECRET: Final = re.compile(
    r'(?i)"(token|apiKey|api_key|authorization|password|userName|username|secret)"'
    r'\s*:\s*"(?:\\.|[^"\\])*"'
)

_caller: contextvars.ContextVar[str | None] = contextvars.ContextVar("fse_api_caller", default=None)
_configured = False
_path: Path | None = None
_seq = 0


def current_caller() -> str | None:
    """The caller bound for this task, if :func:`bind_caller` set one."""
    return _caller.get()


def bind_caller(name: str) -> contextvars.Token[str | None]:
    """Remember ``name`` as the caller of Skylit calls on this task."""
    safe = safe_caller(name) or "caller"
    return _caller.set(safe)


def unbind_caller(token: contextvars.Token[str | None]) -> None:
    _caller.reset(token)


@contextmanager
def caller_scope(name: str) -> Iterator[None]:
    token = bind_caller(name)
    try:
        yield
    finally:
        unbind_caller(token)


def enable(path: Path, *, force: bool = False) -> None:
    """Log to ``path``. An existing ``FSE_API_LOG_PATH`` wins unless ``force``.

    The console sets the variable for itself and every child it starts. A
    runner started on its own uses its live-state directory.
    """
    global _configured, _path
    current = os.environ.get(PATH_ENV)
    if current and not force:
        return
    os.environ[PATH_ENV] = str(path)
    _path = path
    _configured = True


def reset() -> None:
    """Drop the process override. Tests use this; the environment is left as it is."""
    global _configured, _path
    _configured = False
    _path = None


def active_path() -> Path | None:
    """Where :func:`record` writes, or ``None`` when logging is off."""
    if _configured:
        return _path
    raw = os.environ.get(PATH_ENV)
    if not raw:
        return None
    return Path(raw)


def record(
    *,
    api: ApiName,
    method: str,
    path: str,
    status: int | None,
    latency_ms: float | None,
    error: str | None,
    caller: str | None,
    ok: bool,
    redact: tuple[str, ...] = (),
    t_ns: int | None = None,
    url: str | None = None,
    req_headers: Mapping[str, str] | None = None,
    req_body: str | None = None,
    res_body: str | None = None,
    res_note: str | None = None,
) -> None:
    """Append one call. A missing log path or a disk error does not raise.

    ``req_headers`` drops credential names before anything is written.
    Bodies are sanitized and cut at :data:`BODY_LIMIT`. ``res_note`` is a
    short stand-in when the body itself must not be stored (an event stream).
    """
    dest = active_path()
    if dest is None:
        return
    when = time.time_ns() if t_ns is None else t_ns
    req_text, req_cut = body_text(req_body, redact)
    res_text, res_cut = body_text(res_body, redact)
    shown_url, _url_cut = body_text(url, redact)
    row = {
        "id": _next_id(when),
        "t_ns": when,
        "api": api if api in _APIS else "skylit",
        "method": _method(method),
        "path": _path_of(path),
        "status": status if isinstance(status, int) and not isinstance(status, bool) else None,
        "latency_ms": None if latency_ms is None else round(max(0.0, latency_ms), 3),
        "error": sanitize(error, redact),
        "caller": safe_caller(caller),
        "ok": bool(ok),
        "url": shown_url,
        "req_headers": safe_headers(req_headers, redact),
        "req_body": req_text,
        "req_truncated": req_cut,
        "res_body": res_text,
        "res_truncated": res_cut,
        "res_note": sanitize(res_note, redact),
    }
    try:
        _append(dest, row)
    except OSError:
        return


def query(
    path: Path,
    *,
    now_ns: int,
    api: str = "all",
    status: str = "all",
    endpoint: str = "",
    limit: int = MAX_CALLS,
) -> dict[str, object]:
    """The retained calls, newest first, after the API, status and endpoint filters.

    ``total`` counts calls in the window before the status and endpoint
    filters (and after the API filter), so the console can tell an empty log
    from a filter that matches nothing. ``endpoints`` is the paths in that
    same set.
    """
    wanted_api = _api_filter(api)
    wanted_status = _status_filter(status)
    wanted_endpoint = endpoint.strip()
    if wanted_endpoint and _PATH.fullmatch(wanted_endpoint) is None:
        raise ValueError("endpoint must be a path")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_CALLS:
        raise ValueError(f"limit must be from 1 to {MAX_CALLS}")
    rows = _load(path, now_ns)
    scoped = [row for row in rows if _matches_api(str(row["api"]), wanted_api)]
    endpoints = sorted({str(row["path"]) for row in scoped})
    shown = [
        row
        for row in scoped
        if _matches_status(row, wanted_status)
        and (not wanted_endpoint or row["path"] == wanted_endpoint)
    ]
    shown.sort(key=_stamp, reverse=True)
    return {
        "calls": [_public(row) for row in shown[:limit]],
        "total": len(scoped),
        "endpoints": endpoints,
    }


def skylit_error_text(body: object) -> str | None:
    """``error.code`` and ``error.message`` from a Skylit error envelope."""
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if isinstance(error, str) and error.strip():
        return error.strip()
    if not isinstance(error, dict):
        return None
    parts: list[str] = []
    code = error.get("code")
    message = error.get("message")
    if isinstance(code, str) and code.strip():
        parts.append(code.strip())
    if isinstance(message, str) and message.strip():
        parts.append(message.strip())
    return " — ".join(parts) if parts else None


def projectx_outcome(
    status: int | None, body: object, *, include_message: bool
) -> tuple[bool, str | None]:
    """Whether a ProjectX answer succeeded, and a short error when it did not.

    HTTP 200 with ``success: false`` or a non-zero ``errorCode`` is an error.
    ``errorMessage`` is included only when ``include_message`` is set (order
    calls). Login leaves it out: that text can echo the username.
    """
    parts: list[str] = []
    app_error = False
    if isinstance(body, dict):
        success = body.get("success")
        code = body.get("errorCode")
        bad_code = isinstance(code, int) and not isinstance(code, bool) and code != 0
        if success is False or bad_code:
            app_error = True
            if isinstance(code, int) and not isinstance(code, bool):
                parts.append(f"errorCode {code}")
            if include_message:
                message = body.get("errorMessage")
                if isinstance(message, str) and message.strip():
                    parts.append(message.strip())
    http_error = status is None or status < 200 or status > 299
    if http_error and status is not None and not parts:
        parts.append(f"HTTP {status}")
    text = " — ".join(parts) if parts else None
    return (not http_error and not app_error), text


def sanitize(text: str | None, secrets: tuple[str, ...] = ()) -> str | None:
    """Drop credential-shaped text and truncate. ``None`` stays ``None``."""
    cleaned = _redact(text, secrets)
    if not cleaned:
        return None
    if len(cleaned) > ERROR_LIMIT:
        cleaned = cleaned[: ERROR_LIMIT - 1] + "…"
    return cleaned


def body_text(text: str | None, secrets: tuple[str, ...] = ()) -> tuple[str | None, bool]:
    """Sanitize a body and cut it at :data:`BODY_LIMIT`.

    The bool is true when characters were cut. The kept text ends with an ellipsis.
    """
    cleaned = _redact(text, secrets)
    if not cleaned:
        return None, False
    if len(cleaned) > BODY_LIMIT:
        return cleaned[: BODY_LIMIT - 1] + "…", True
    return cleaned, False


def dump_body(body: object) -> str | None:
    """Text for a captured body. Objects and lists become compact JSON.

    ``None``, an empty string and an empty ``bytes`` stay ``None``. Anything
    else that is not text or JSON is dropped rather than stored with ``repr``.
    """
    if body is None:
        return None
    if isinstance(body, str):
        text = body.strip()
        return text or None
    if isinstance(body, bytes):
        if not body:
            return None
        return body.decode("utf-8", errors="replace")
    if isinstance(body, dict | list):
        try:
            return json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        except (TypeError, ValueError):
            return None
    return None


def safe_headers(
    headers: Mapping[str, str] | None, secrets: tuple[str, ...] = ()
) -> dict[str, str]:
    """Headers safe to store. Credential names are omitted, not redacted in place."""
    if not headers:
        return {}
    kept: dict[str, str] = {}
    for name, value in headers.items():
        lowered = name.lower()
        if any(part in lowered for part in _DROP_HEADER):
            continue
        cleaned = sanitize(value, secrets)
        if cleaned is None:
            continue
        kept[name] = cleaned
    return kept


def _redact(text: str | None, secrets: tuple[str, ...]) -> str | None:
    if text is None:
        return None
    cleaned = text
    for secret in secrets:
        if isinstance(secret, str) and len(secret) >= 4:
            cleaned = cleaned.replace(secret, "[REDACTED]")
    cleaned = _JSON_SECRET.sub(lambda match: f'"{match.group(1)}":"[REDACTED]"', cleaned)
    cleaned = _BEARER.sub("Bearer [REDACTED]", cleaned)
    cleaned = _SK_KEY.sub("[REDACTED]", cleaned)
    cleaned = _ASSIGN.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", cleaned)
    cleaned = cleaned.strip()
    return cleaned or None


def _matches_api(api: str, wanted: ApiFilter) -> bool:
    if wanted == "all":
        return True
    if wanted == "skylit":
        return api == "skylit"
    if wanted == "projectx":
        return api == "projectx"
    assert_never(wanted)


def _matches_status(row: Mapping[str, object], wanted: StatusFilter) -> bool:
    if wanted == "all":
        return True
    if wanted == "errors":
        return row.get("ok") is not True
    assert_never(wanted)


def _api_filter(value: str) -> ApiFilter:
    match value:
        case "all" | "skylit" | "projectx":
            return value
        case _:
            raise ValueError("api must be all, skylit or projectx")


def _status_filter(value: str) -> StatusFilter:
    match value:
        case "all" | "errors":
            return value
        case _:
            raise ValueError("status must be all or errors")


def _stamp(row: Mapping[str, object]) -> int:
    t_ns = row.get("t_ns")
    if isinstance(t_ns, int) and not isinstance(t_ns, bool):
        return t_ns
    return 0


def _public(row: Mapping[str, object]) -> dict[str, object]:
    """One console row. Headers and bodies are sanitized again on the way out."""
    t_ns = row["t_ns"]
    seconds = (int(t_ns) / 1_000_000_000) if isinstance(t_ns, int) else None
    req_body, req_cut = body_text(_text(row.get("req_body")))
    res_body, res_cut = body_text(_text(row.get("res_body")))
    shown_url, _url_cut = body_text(_text(row.get("url")))
    return {
        "id": row["id"],
        "t": seconds,
        "api": row["api"],
        "method": row["method"],
        "path": row["path"],
        "status": row["status"],
        "latency_ms": row["latency_ms"],
        "error": row["error"],
        "caller": row["caller"],
        "ok": row["ok"],
        "url": shown_url,
        "req_headers": safe_headers(_header_map(row.get("req_headers"))),
        "req_body": req_body,
        "req_truncated": row.get("req_truncated") is True or req_cut,
        "res_body": res_body,
        "res_truncated": row.get("res_truncated") is True or res_cut,
        "res_note": sanitize(_text(row.get("res_note"))),
    }


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _header_map(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    kept: dict[str, str] = {}
    for name, item in value.items():
        if isinstance(name, str) and isinstance(item, str):
            kept[name] = item
    return kept


def _load(path: Path, now_ns: int) -> list[dict[str, object]]:
    try:
        lines = _read_lines(path)
    except FileNotFoundError:
        return []
    cutoff = now_ns - WINDOW_NS
    rows: list[dict[str, object]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        t_ns = row.get("t_ns")
        if not isinstance(t_ns, int) or isinstance(t_ns, bool) or t_ns < cutoff:
            continue
        rows.append(row)
    rows.sort(key=_stamp)
    return rows[-MAX_CALLS:]


def _read_lines(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_SH)
        try:
            return handle.read().splitlines()
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _append(path: Path, row: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    line = json.dumps(row, separators=(",", ":"), ensure_ascii=False)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            handle.write(line + "\n")
            handle.flush()
            handle.seek(0)
            raw = handle.read().splitlines()
            if len(raw) <= MAX_CALLS and not _oldest_is_stale(raw):
                return
            kept = _retain(raw, time.time_ns())
            handle.seek(0)
            handle.truncate()
            if kept:
                handle.write("\n".join(kept) + "\n")
            handle.flush()
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _oldest_is_stale(lines: list[str]) -> bool:
    if not lines:
        return False
    try:
        row = json.loads(lines[0])
    except ValueError:
        return True
    if not isinstance(row, dict):
        return True
    t_ns = row.get("t_ns")
    if not isinstance(t_ns, int) or isinstance(t_ns, bool):
        return True
    return t_ns < time.time_ns() - WINDOW_NS


def _retain(lines: list[str], now_ns: int) -> list[str]:
    cutoff = now_ns - WINDOW_NS
    kept: list[tuple[int, str]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        t_ns = row.get("t_ns")
        if not isinstance(t_ns, int) or isinstance(t_ns, bool) or t_ns < cutoff:
            continue
        kept.append((t_ns, line))
    kept.sort(key=lambda item: item[0])
    return [line for _, line in kept[-MAX_CALLS:]]


def _next_id(t_ns: int) -> str:
    global _seq
    _seq += 1
    return f"{t_ns:x}-{os.getpid():x}-{_seq:x}"


def _method(method: str) -> str:
    upper = method.upper()
    return upper if upper in _METHODS else "GET"


def _path_of(path: str) -> str:
    text = path.strip()
    if _PATH.fullmatch(text):
        return text
    return "/"


def safe_caller(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if _CALLER.fullmatch(text):
        return text
    return None
