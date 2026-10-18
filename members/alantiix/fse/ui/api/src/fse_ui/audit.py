"""The audit log: one row per mutation (who, what, the details a handler adds, result).

:class:`AuditMiddleware` writes the row after the response status is known, so
every ``POST``/``PUT``/``PATCH``/``DELETE`` is recorded without each handler
remembering to. Handlers add details with :func:`note`; secret values must
never be passed to it.
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from fse_ui.auth import UNSAFE_METHODS
from fse_ui.db import Db

MAX_DETAIL_CHARS = 4000


def note(request: Request, **detail: Any) -> None:
    """Add ``detail`` to this request's audit row."""
    current: dict[str, Any] = getattr(request.state, "audit", None) or {}
    current.update(detail)
    request.state.audit = current


def record(db: Db, *, user: str, action: str, detail: dict[str, Any], result: str) -> None:
    text = json.dumps(detail, default=str, sort_keys=True)
    if len(text) > MAX_DETAIL_CHARS:
        text = text[: MAX_DETAIL_CHARS - 1] + "…"
    db.execute(
        "INSERT INTO audit (at, user, action, detail, result) VALUES (?, ?, ?, ?, ?)",
        (time.time(), user, action, text, result),
    )


def recent(db: Db, *, limit: int = 200, before: int | None = None) -> list[dict[str, Any]]:
    if before is None:
        rows = db.all(
            "SELECT id, at, user, action, detail, result FROM audit ORDER BY id DESC LIMIT ?",
            (limit,),
        )
    else:
        rows = db.all(
            "SELECT id, at, user, action, detail, result FROM audit WHERE id < ? "
            "ORDER BY id DESC LIMIT ?",
            (before, limit),
        )
    return [
        {
            "id": r["id"],
            "at": r["at"],
            "user": r["user"],
            "action": r["action"],
            "detail": json.loads(r["detail"]),
            "result": r["result"],
        }
        for r in rows
    ]


class AuditMiddleware:
    """Records every mutating request under ``/api`` and ``/auth/logout``."""

    def __init__(self, app: ASGIApp, db: Db) -> None:
        self.app = app
        self.db = db

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in UNSAFE_METHODS:
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]
        if not (path.startswith("/api/") or path == "/auth/logout"):
            await self.app(scope, receive, send)
            return
        state: dict[str, Any] = scope.setdefault("state", {})
        status = {"code": 500}

        async def capture(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, capture)
        finally:
            principal = state.get("principal")
            user = getattr(principal, "email", None) or "anonymous"
            code = status["code"]
            # Unauthenticated noise (401/403 before a session) is still recorded:
            # it is a mutation attempt the operator may want to see.
            record(
                self.db,
                user=user,
                action=f"{scope['method']} {path}",
                detail=dict(state.get("audit") or {}),
                result="ok" if code < 400 else f"http {code}",
            )
