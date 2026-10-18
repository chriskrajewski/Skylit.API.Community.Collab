"""``fse account reset``: archive a paper account so the next ``fse run`` starts it fresh.

``fse account reset <id> --state-dir DIR --reason TEXT [--config FILE]``
moves every file of ``<live-state>/accounts/<id>/`` (``account.json``, a
renamed bad copy, the Nexus outbox and status) into
``accounts/<id>/archive/<UTC stamp>/`` and appends one ``account_reset`` line
to ``<live-state>/accounts/resets.jsonl``. Nothing else is written: the next
``fse run`` finds no ``account.json`` and starts the account from the current
rules (a new ``starting_balance`` included).

The runner must be stopped: while another process holds
``<live-state>/runner.lock`` the command exits 3 and moves nothing.
``--config``, when given, must name the account in its ``accounts:``.

The command sends no network request.
"""

from __future__ import annotations

import argparse
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from fse.commands import CommandContext, SubParsers
from fse.data.path_guard import check_output_dir
from fse.live.host.accounts import ACCOUNTS_DIR_NAME, account_dir
from fse.live.host.config import ACCOUNT_ID_PATTERN, load_runner_config
from fse.live.host.lock import RunnerLock, RunnerLockHeld
from fse.settings import default_paths

__all__ = [
    "ACCOUNT_NAME",
    "ARCHIVE_DIR_NAME",
    "EXIT_FAILED",
    "EXIT_INVALID_INPUT",
    "EXIT_RUNNER_RUNNING",
    "REASON_MAX",
    "RESETS_FILE_NAME",
    "register",
    "run_account_reset",
]

ACCOUNT_NAME: Final = "account"
RESET_NAME: Final = "reset"
ARCHIVE_DIR_NAME: Final = "archive"
RESETS_FILE_NAME: Final = "resets.jsonl"
REASON_MAX: Final = 200
EXIT_OK: Final = 0
EXIT_FAILED: Final = 1
EXIT_INVALID_INPUT: Final = 2
EXIT_RUNNER_RUNNING: Final = 3

_ID_RE: Final = re.compile(ACCOUNT_ID_PATTERN)

_EPILOG = """\
exit status:
  0  archived; the next fse run starts the account fresh
  1  a file could not be archived or the reset could not be logged
  2  invalid input: id, reason, live-state directory or missing account
  3  the runner is running; stop it first"""


def register(subparsers: SubParsers) -> None:
    parser = subparsers.add_parser(
        ACCOUNT_NAME,
        help="paper account operations",
        description="Paper account tools of fse run.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = parser.add_subparsers(
        dest="account_action", metavar="<action>", title="actions", required=True
    )
    reset = actions.add_parser(
        RESET_NAME,
        help="archive a paper account so the next fse run starts it fresh",
        description=(
            "Archive a paper account's state (account.json, Nexus outbox and status) "
            "under accounts/<id>/archive/<UTC stamp>/ and log the reset. The runner "
            "must be stopped. The next fse run starts the account from its current rules."
        ),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # dest "id", not "account_id": no CLI argument may name an account (Req 24.7's
    # credential guard, test_repository_layout); this is a paper-account id.
    reset.add_argument("id", metavar="ID", help="the accounts[].id to reset")
    reset.add_argument(
        "--state-dir",
        type=Path,
        metavar="DIR",
        help="the live-state directory (default: ~/.skylit-fse/live-state)",
    )
    reset.add_argument(
        "--reason", metavar="TEXT", help=f"why (1 to {REASON_MAX} characters, logged)"
    )
    reset.add_argument(
        "--config", type=Path, metavar="FILE", help="a runner YAML that must name the account"
    )
    reset.set_defaults(handler=run_account_reset, failure_exit_code=EXIT_FAILED)


def _stamp(t_ns: int) -> str:
    return datetime.fromtimestamp(t_ns / 1e9, UTC).strftime("%Y%m%dT%H%M%SZ")


def _archive_dir(root: Path, stamp: str) -> Path:
    """A new, empty ``archive/<stamp>`` (``-2``, ``-3``, ... after a reset in the same second)."""
    base = root / ARCHIVE_DIR_NAME
    target = base / stamp
    n = 1
    while target.exists():
        n += 1
        target = base / f"{stamp}-{n}"
    target.mkdir(parents=True)
    return target


def _invalid(ctx: CommandContext, message: str) -> int:
    ctx.writer.error(f"error: {message}")
    return EXIT_INVALID_INPUT


def run_account_reset(args: argparse.Namespace, ctx: CommandContext) -> int:
    account_id: str = args.id
    reason: str | None = args.reason
    if reason is None or not 1 <= len(reason) <= REASON_MAX:
        return _invalid(ctx, f"--reason must be 1 to {REASON_MAX} characters")
    if _ID_RE.fullmatch(account_id) is None:
        return _invalid(
            ctx, f"{account_id!r} is not an account id (lowercase, digits, _; implicit: refused)"
        )
    given = default_paths().live_state if args.state_dir is None else args.state_dir
    state_dir = check_output_dir(given, label="live-state directory")
    if args.config is not None:
        cfg = load_runner_config(args.config)
        if account_id not in {a.id for a in cfg.accounts}:
            return _invalid(ctx, f"account {account_id!r} is not in {args.config}")
    root = account_dir(state_dir, account_id)
    if not root.is_dir():
        return _invalid(ctx, f"account {account_id!r} has no state in {root}")
    lock = RunnerLock(state_dir)
    try:
        lock.acquire()
    except RunnerLockHeld:
        ctx.writer.error(
            f"error: the runner is running; stop it before resetting account {account_id}"
        )
        return EXIT_RUNNER_RUNNING
    try:
        return _reset(ctx, state_dir, root, account_id, reason)
    finally:
        lock.release()


def _reset(ctx: CommandContext, state_dir: Path, root: Path, account_id: str, reason: str) -> int:
    t_ns = time.time_ns()
    try:
        target = _archive_dir(root, _stamp(t_ns))
    except OSError as exc:
        ctx.writer.error(f"error: cannot create the archive in {root}: {exc}")
        return EXIT_FAILED
    moved: list[str] = []
    for path in sorted(root.iterdir()):
        if path.name == ARCHIVE_DIR_NAME:
            continue
        try:
            path.replace(target / path.name)
        except OSError as exc:
            ctx.writer.error(f"error: cannot archive {path}: {exc}")
            return EXIT_FAILED
        moved.append(path.name)
    line = {
        "kind": "account_reset",
        "account": account_id,
        "reason": reason,
        "archived_to": str(target),
        "t_ns": t_ns,
    }
    resets = state_dir / ACCOUNTS_DIR_NAME / RESETS_FILE_NAME
    try:
        ctx.writer.append_json(resets, line, fsync=True)
    except OSError as exc:
        ctx.writer.error(f"error: archived to {target} but cannot log the reset in {resets}: {exc}")
        return EXIT_FAILED
    ctx.writer.echo(
        f"account {account_id} archived to {target} ({len(moved)} file(s)); "
        "the next fse run starts it fresh"
    )
    return EXIT_OK
