"""``fse accounts``: list the ProjectX accounts the credentials can see (read-only).

Output. Table mode prints on stdout a header and one row per active account
(``ID``, ``NAME``, ``CAN TRADE``, ``ROLE``), sorted by id; stderr holds one
``warning:`` line per diagnostic of ``PRACTICE_ACCOUNT_ID`` and
``COMBINE_ACCOUNT_ID``, or one line saying both match listed accounts.
``--json`` prints exactly one stdout line instead::

    {"accounts":[{"can_trade":true,"id":"101","name":"...","role":"practice"}],
     "diagnostics":[{"code":"...","message":"...","variable":"..."}],
     "kind":"fse_accounts"}

Ids are strings, so a redacted id (``[REDACTED]``) keeps the JSON valid. The
role is ``practice``, ``combine``, ``practice,combine`` or ``""``. No line
holds a credential, the session token or an account-id variable's value.

The command only logs in and calls ``Account/search``; it sends no order.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

import httpx

from fse.clock import Clock, SystemClock
from fse.commands import CommandContext, SubParsers
from fse.logio.log_writer import LogWriter
from fse.projectx.broker import ACCOUNT_VARIABLES, BrokerAdapter, BrokerFailure
from fse.projectx.models import AccountRef
from fse.projectx.session import DEFAULT_BASE_URL, EXIT_CREDENTIALS, ProjectXSession
from fse.secrets.env import EnvView

__all__ = [
    "EXIT_BROKER",
    "JSON_KIND",
    "NAME",
    "Diagnostic",
    "diagnose",
    "lookup",
    "register",
    "roles",
    "run",
]

NAME: Final = "accounts"
JSON_KIND: Final = "fse_accounts"
EXIT_BROKER: Final = 4

_PRACTICE: Final = "practice"
_COMBINE: Final = "combine"
_PLACEHOLDER: Final = "PLACEHOLDER"

_DESCRIPTION = """\
Log in to ProjectX with PROJECTX_USERNAME and PROJECTX_API_KEY and list the
active accounts they can see: the numeric id, the name, whether it can trade,
and the role PRACTICE_ACCOUNT_ID or COMBINE_ACCOUNT_ID gives it. Then check
both variables: set, a numeric id (not the account name), listed, and not
equal to each other. Read-only: no order is sent."""

_EPILOG = """\
exit status:
  0  the accounts were listed (a warning about an account-id variable does
     not fail the lookup)
  3  PROJECTX_USERNAME or PROJECTX_API_KEY is blank
  4  the ProjectX login or Account/search failed"""

type Shape = Literal["unset", "not_numeric", "ok"]


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One finding about an account-id variable. ``message`` never holds its value."""

    code: str
    variable: str
    message: str


def register(subparsers: SubParsers) -> None:
    parser = subparsers.add_parser(
        NAME,
        help="list the ProjectX accounts the credentials can see (read-only)",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json",
        help="print one JSON line instead of the table",
    )
    parser.set_defaults(handler=run, failure_exit_code=EXIT_BROKER)


def run(args: argparse.Namespace, ctx: CommandContext) -> int:
    return asyncio.run(lookup(ctx.env, ctx.writer, SystemClock(), as_json=bool(args.json)))


# ---------------------------------------------------------------- pure helpers


def _shape(raw: str | None) -> Shape:
    """``unset`` (blank or a placeholder), ``not_numeric``, or ``ok`` (the for_mode rule)."""
    if raw is None or raw.startswith(_PLACEHOLDER):
        return "unset"
    if raw.isascii() and raw.isdigit() and str(int(raw)) == raw:
        return "ok"
    return "not_numeric"


def roles(account_id: int, env: EnvView) -> str:
    """The roles the account-id variables give ``account_id``, comma-separated."""
    shown = str(account_id)
    return ",".join(
        mode for mode in (_PRACTICE, _COMBINE) if env.get(ACCOUNT_VARIABLES[mode]) == shown
    )


def diagnose(env: EnvView, accounts: Sequence[AccountRef]) -> list[Diagnostic]:
    """Practice checks, Combine checks, then the equality check."""
    listed = {str(a.id) for a in accounts}
    out: list[Diagnostic] = []
    shapes: dict[str, Shape] = {}
    for mode in (_PRACTICE, _COMBINE):
        name = ACCOUNT_VARIABLES[mode]
        raw = env.get(name)
        shape = shapes[mode] = _shape(raw)
        if shape == "unset":
            out.append(Diagnostic("unset", name, f"{name} is unset"))
        elif shape == "not_numeric":
            out.append(
                Diagnostic(
                    "not_numeric",
                    name,
                    f"{name} is not a numeric account id; use the numeric id, not the account name",
                )
            )
        elif raw not in listed:
            out.append(
                Diagnostic(
                    "no_match", name, f"{name} matches no account the Broker_Adapter resolved"
                )
            )
    practice, combine = ACCOUNT_VARIABLES[_PRACTICE], ACCOUNT_VARIABLES[_COMBINE]
    if (
        shapes[_PRACTICE] != "unset"
        and shapes[_COMBINE] != "unset"
        and env.get(practice) == env.get(combine)
    ):
        out.append(Diagnostic("same", practice, f"{practice} equals {combine}"))
    return out


# ---------------------------------------------------------------- the lookup


async def lookup(
    env: EnvView,
    writer: LogWriter,
    clock: Clock,
    *,
    as_json: bool,
    base_url: str = DEFAULT_BASE_URL,
) -> int:
    """List the accounts and print them; returns 0, 3 (blank credentials) or 4."""
    async with httpx.AsyncClient(follow_redirects=False) as http:
        session = ProjectXSession(env, writer.redactor, http, clock, base_url=base_url)
        missing = session.missing_credentials()
        if missing:
            verb = "is" if len(missing) == 1 else "are"
            writer.error(
                f"error: {' and '.join(missing)} {verb} blank; "
                "set them in Settings -> Secrets or the Project .env"
            )
            return EXIT_CREDENTIALS
        adapter = BrokerAdapter(session, env, clock)
        accounts = await adapter.resolve_accounts()
    if isinstance(accounts, BrokerFailure):
        writer.error(f"error: cannot list the ProjectX accounts: {accounts}")
        return EXIT_BROKER
    listed = sorted(accounts, key=lambda a: a.id)
    findings = diagnose(env, listed)
    if as_json:
        _print_json(writer, env, listed, findings)
    else:
        _print_table(writer, env, listed, findings)
    return 0


def _print_json(
    writer: LogWriter,
    env: EnvView,
    accounts: Sequence[AccountRef],
    findings: Sequence[Diagnostic],
) -> None:
    writer.echo(
        writer.json_text(
            {
                "kind": JSON_KIND,
                "accounts": [
                    {
                        "id": str(a.id),
                        "name": a.name,
                        "can_trade": a.can_trade,
                        "role": roles(a.id, env),
                    }
                    for a in accounts
                ],
                "diagnostics": [
                    {"code": d.code, "variable": d.variable, "message": d.message} for d in findings
                ],
            }
        )
    )


def _print_table(
    writer: LogWriter,
    env: EnvView,
    accounts: Sequence[AccountRef],
    findings: Sequence[Diagnostic],
) -> None:
    rows = [("ID", "NAME", "CAN TRADE", "ROLE")]
    rows += [
        (str(a.id), a.name, "yes" if a.can_trade else "no", roles(a.id, env) or "-")
        for a in accounts
    ]
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    for row in rows:
        writer.echo("  ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=True)).rstrip())
    if not accounts:
        writer.echo("no active accounts")
    for d in findings:
        writer.error(f"warning: {d.message}")
    if not findings:
        writer.error(
            f"{NAME}: {ACCOUNT_VARIABLES[_PRACTICE]} and {ACCOUNT_VARIABLES[_COMBINE]} "
            "match listed accounts"
        )
