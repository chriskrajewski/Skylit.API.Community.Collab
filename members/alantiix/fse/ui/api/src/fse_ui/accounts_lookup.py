"""The ProjectX account lookup: parse ``fse accounts --json`` and set an account id.

The lookup runs as a console batch job (the credentials stay in the job's
environment). Its one JSON line lists the accounts; the console then annotates
each row with the role the stored ``PRACTICE_ACCOUNT_ID`` / ``COMBINE_ACCOUNT_ID``
give it and with the same diagnostics the engine prints. Only booleans and
shapes leave the secret store (:meth:`SecretStore.equals`, ``id_shape``,
``same``); no response holds a stored value.

The console redacts every stored secret value (account ids included) from job
logs, so the row of an account id that is already stored can arrive as
``[redacted]``. Such a row keeps the role the engine computed with the job's
environment and cannot be picked.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from fse_ui.secrets_store import SecretStore
from fse_ui.workflows import _STATUS

ACCOUNTS_COMMAND = "fse accounts"
JSON_KIND = "fse_accounts"
PRACTICE = "PRACTICE_ACCOUNT_ID"
COMBINE = "COMBINE_ACCOUNT_ID"
_VARIABLES: tuple[tuple[str, str], ...] = ((PRACTICE, "practice"), (COMBINE, "combine"))
# The engine's Redactor writes ``[REDACTED]``, the console's job-log Redactor ``[redacted]``.
_REDACTION_MARKERS = ("[REDACTED]", "[redacted]")
_ROLES = frozenset({"", "practice", "combine", "practice,combine"})

CONFIRM_NOT_PRACTICE = "this looks like a Combine/funded account; Practice mode would trade it"
SAME_ACCOUNT = "PRACTICE_ACCOUNT_ID and COMBINE_ACCOUNT_ID cannot be the same account"
PICK_FROM_LOOKUP = "pick an account from the lookup"
RESTART_NOTE = "Restart the runner for it to read the new value."

type Role = Literal["practice", "combine"]


class UseError(Exception):
    """An account the console refuses to store (``status`` and ``detail`` for HTTP)."""

    def __init__(self, status: int, detail: str | dict[str, str]) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def is_accounts_job(job: Mapping[str, Any]) -> bool:
    return job.get("program") == "fse" and list(job.get("command") or ()) == ["accounts"]


def _is_redacted(account_id: str) -> bool:
    return any(marker in account_id for marker in _REDACTION_MARKERS)


def _row(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    account_id, name, can_trade = raw.get("id"), raw.get("name"), raw.get("can_trade")
    if not isinstance(account_id, str) or not isinstance(name, str):
        return None
    if not isinstance(can_trade, bool):
        return None
    redacted = _is_redacted(account_id)
    engine_role = raw.get("role")
    return {
        "id": account_id,
        "name": name,
        "can_trade": can_trade,
        "redacted": redacted,
        # Only a redacted row needs the engine's role (the console cannot compare it).
        "engine_role": engine_role if redacted and engine_role in _ROLES else "",
    }


def parse_accounts_lookup(lines: Sequence[str]) -> dict[str, Any]:
    """``{"accounts": rows | None, "error": str | None}`` from a lookup job's log.

    Scans newest-first for the ``fse_accounts`` JSON line; the engine's roles and
    diagnostics are dropped (the console recomputes them from the secret store).
    """
    malformed = False
    for line in reversed(lines):
        if _STATUS.match(line) or "{" not in line:
            continue
        try:
            data = json.loads(line[line.index("{") :])
        except ValueError:
            malformed = malformed or JSON_KIND in line
            continue
        if not isinstance(data, dict) or data.get("kind") != JSON_KIND:
            continue
        accounts = data.get("accounts")
        if not isinstance(accounts, list):
            return {"accounts": None, "error": "the accounts line has no account list"}
        rows = [_row(a) for a in accounts]
        if any(r is None for r in rows):
            return {"accounts": None, "error": "the accounts line has a malformed account row"}
        return {"accounts": [r for r in rows if r is not None], "error": None}
    if malformed:
        return {"accounts": None, "error": "the accounts line is not valid JSON"}
    return {"accounts": None, "error": "the job printed no accounts line"}


def annotate(rows: Sequence[Mapping[str, Any]], store: SecretStore) -> list[dict[str, Any]]:
    """Public rows ``{id, name, can_trade, role, redacted}``; the role comes from the
    stored account ids (a redacted row keeps the engine's role).

    A batch task on AWS gets only the ProjectX credentials, not the account ids, so
    the engine cannot give a redacted row its role there. A redacted id is a stored
    value, so when exactly one redacted row has no role and exactly one stored
    numeric account id matches no other row, that row is that id's account.
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        if row["redacted"]:
            role = str(row.get("engine_role") or "")
        else:
            role = ",".join(
                label for name, label in _VARIABLES if store.equals(name, row["id"]) is True
            )
        out.append(
            {
                "id": row["id"],
                "name": row["name"],
                "can_trade": row["can_trade"],
                "role": role,
                "redacted": bool(row["redacted"]),
            }
        )
    unknown = [r for r in out if r["redacted"] and not r["role"]]
    unmatched = [
        label
        for name, label in _VARIABLES
        if store.id_shape(name) == "numeric"
        and not any(label in str(r["role"]).split(",") for r in out)
    ]
    if len(unknown) == 1 and len(unmatched) == 1:
        unknown[0]["role"] = unmatched[0]
    return out


def diagnostics(annotated: Sequence[Mapping[str, Any]], store: SecretStore) -> list[dict[str, str]]:
    """The engine's diagnostic codes and messages for :func:`annotate` rows (practice
    checks, combine checks, then same); no message holds a value.

    A redacted row without a role may be either stored id, so while one is listed
    no ``no_match`` is reported (it would be a false warning).
    """
    undecided = any(r["redacted"] and not r["role"] for r in annotated)
    out: list[dict[str, str]] = []
    for name, label in _VARIABLES:
        shape = store.id_shape(name)
        if shape == "unset":
            out.append({"code": "unset", "variable": name, "message": f"{name} is unset"})
        elif shape == "not_numeric":
            out.append(
                {
                    "code": "not_numeric",
                    "variable": name,
                    "message": f"{name} is not a numeric account id; "
                    "use the numeric id, not the account name",
                }
            )
        elif not undecided and not any(label in str(r["role"]).split(",") for r in annotated):
            out.append(
                {
                    "code": "no_match",
                    "variable": name,
                    "message": f"{name} matches no account the Broker_Adapter resolved",
                }
            )
    if store.same(PRACTICE, COMBINE) is True:
        message = f"{PRACTICE} equals {COMBINE}"
        out.append({"code": "same", "variable": PRACTICE, "message": message})
    return out


def check_use(
    rows: Sequence[Mapping[str, Any]],
    account_id: str,
    role: Role,
    confirm_not_practice: bool,
    store: SecretStore,
) -> str:
    """The secret name to store ``account_id`` in, or :class:`UseError`."""
    row = next((r for r in rows if r["id"] == account_id and not r["redacted"]), None)
    if row is None:
        raise UseError(422, PICK_FROM_LOOKUP)
    name, other = (PRACTICE, COMBINE) if role == "practice" else (COMBINE, PRACTICE)
    # Refuse the same account before asking for a confirmation it would not honour.
    if store.equals(other, account_id) is True:
        raise UseError(409, {"code": "same_account", "message": SAME_ACCOUNT})
    if (
        role == "practice"
        and not str(row["name"]).upper().startswith("PRAC")
        and not confirm_not_practice
    ):
        raise UseError(409, {"code": "confirm_required", "message": CONFIRM_NOT_PRACTICE})
    return name
