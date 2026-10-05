"""``fse halt`` and ``fse clear``: the Operator's entry block and clear command (Req 24.26-24.27).

``fse halt [--state-dir DIR] [--reason TEXT]`` adds a ``halt_command`` block
to ``blocks.json`` in the live-state directory (default
``~/.skylit-fse/live-state``). A running Live_Runner reads the blocks at
every Decision_Time, so within one Decision_Cadence it cancels every resting
entry order and places no new entry; stop-loss and target orders of open
positions stay in place. The block survives restarts.

``fse clear [--state-dir DIR]`` removes every persistent block
(``halt_command``, ``bracket_failure``, ``reconciliation``,
``restore_failed``), and rewrites an unreadable blocks file. It does not
touch the halt file: while ``HALT`` exists in the live-state directory,
entries stay blocked, and the command says so.

Both commands write through the Log_Writer and send no network request.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Final

from fse.commands import CommandContext, SubParsers
from fse.data.path_guard import check_output_dir
from fse.live.state_store import BlockRecord, BlockStore, BlocksUnreadable, halt_file
from fse.settings import default_paths

__all__ = ["CLEAR_NAME", "HALT_NAME", "register", "run_clear_command", "run_halt_command"]

HALT_NAME: Final = "halt"
CLEAR_NAME: Final = "clear"

_EPILOG = """\
exit status:
  0  done
  2  invalid input: the live-state directory
  1  the blocks file could not be written"""


def _add_state_dir(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--state-dir",
        type=Path,
        metavar="DIR",
        help="the live-state directory (default: ~/.skylit-fse/live-state)",
    )


def register(subparsers: SubParsers) -> None:
    halt = subparsers.add_parser(
        HALT_NAME,
        help="block new entries until fse clear",
        description=(
            "Block new entries in the Live_Runner until the Operator runs fse clear. "
            "Resting entry orders are cancelled within one Decision_Cadence; "
            "stop-loss and target orders of open positions stay in place."
        ),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_state_dir(halt)
    halt.add_argument(
        "--reason", default="fse halt", metavar="TEXT", help="a note stored with the block"
    )
    halt.set_defaults(handler=run_halt_command)
    clear = subparsers.add_parser(
        CLEAR_NAME,
        help="lift every persistent entry block",
        description=(
            "Remove every persistent entry block (halt command, bracket failure, "
            "reconciliation difference, failed restore). The halt file is not removed: "
            "while it exists, entries stay blocked."
        ),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_state_dir(clear)
    clear.set_defaults(handler=run_clear_command)


def _state_dir(given: Path | None) -> Path:
    path = default_paths().live_state if given is None else given
    return check_output_dir(path, label="live-state directory")


def run_halt_command(args: argparse.Namespace, ctx: CommandContext) -> int:
    directory = _state_dir(args.state_dir)
    store = BlockStore(ctx.writer, directory)
    added = store.add(BlockRecord("halt_command", None, args.reason, time.time_ns()))
    if added:
        ctx.writer.echo(f"halt_command block set in {store.path}; new entries are blocked")
    else:
        ctx.writer.echo(f"a halt_command block is already in force in {store.path}")
    return 0


def run_clear_command(args: argparse.Namespace, ctx: CommandContext) -> int:
    directory = _state_dir(args.state_dir)
    store = BlockStore(ctx.writer, directory)
    before = store.clear()
    if isinstance(before, BlocksUnreadable):
        ctx.writer.echo(f"the unreadable blocks file was replaced ({before.reason})")
    elif before:
        kinds = ", ".join(
            f"{b.kind}" + (f" ({b.instrument})" if b.instrument else "") for b in before
        )
        ctx.writer.echo(f"cleared {len(before)} block(s): {kinds}")
    else:
        ctx.writer.echo("no persistent block was in force")
    if halt_file(directory).exists():
        ctx.writer.echo(
            f"the halt file {halt_file(directory)} exists: entries stay blocked until it is removed"
        )
    return 0
