"""``fse day-loss-reset``: zero one plugin's paper day net for today's max-loss budget.

``fse day-loss-reset --plugin ID [--state-dir DIR] [--reason TEXT]`` writes
``day_net_reset.json`` under ``<live-state>/plugins/<id>/``. A running host
applies it within one decision or status tick (``PluginPaperBroker.reset_day_net``):
sizing again uses the full configured ``max_daily_loss`` until new closes.
The runner YAML is not edited. Journals, open positions and ``day_trades`` stay.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Final

from fse.commands import CommandContext, SubParsers
from fse.data.path_guard import check_output_dir
from fse.live.host.day_net_reset import write_day_net_reset
from fse.live.host.gate import plugin_dir
from fse.settings import default_paths

__all__ = ["DAY_LOSS_RESET_NAME", "REASON_MAX", "register", "run_day_loss_reset"]

DAY_LOSS_RESET_NAME: Final = "day-loss-reset"
REASON_MAX: Final = 200
EXIT_OK: Final = 0
EXIT_INVALID_INPUT: Final = 2

_PLUGIN_ID_RE: Final = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

_EPILOG = """\
exit status:
  0  request written; a running host applies it on the next tick
  2  invalid input: plugin id, reason or live-state directory

The host zeros that plugin's paper day_net only. max_daily_loss in the runner
YAML is unchanged. Open trades and the trade journal are not cleared."""


def register(subparsers: SubParsers) -> None:
    parser = subparsers.add_parser(
        DAY_LOSS_RESET_NAME,
        help="zero one plugin's paper day net (today's max-loss budget)",
        description=(
            "Ask the live host to zero one plugin's realized day net so the plugin "
            "(and host caps) size again to the full max_daily_loss. Does not edit "
            "the runner YAML or delete trades."
        ),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--plugin",
        required=True,
        metavar="ID",
        help="plugins[].id whose paper day_net to zero",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        metavar="DIR",
        help="the live-state directory (default: ~/.skylit-fse/live-state)",
    )
    parser.add_argument(
        "--reason",
        default="reset from the fse console",
        metavar="TEXT",
        help=f"a note stored with the request (at most {REASON_MAX} characters)",
    )
    parser.set_defaults(handler=run_day_loss_reset)


def run_day_loss_reset(args: argparse.Namespace, ctx: CommandContext) -> int:
    plugin_id = str(args.plugin).strip()
    if not _PLUGIN_ID_RE.fullmatch(plugin_id):
        ctx.writer.error(
            f"error: invalid plugin id {plugin_id!r} (a-z, 0-9, _; starts with a letter)"
        )
        return EXIT_INVALID_INPUT
    reason = str(args.reason).strip() or "reset from the fse console"
    if len(reason) > REASON_MAX or re.search(r"[\r\n\x00]", reason):
        ctx.writer.error(f"error: the reason must be one line of at most {REASON_MAX} characters")
        return EXIT_INVALID_INPUT
    state_dir = default_paths().live_state if args.state_dir is None else args.state_dir
    root = check_output_dir(state_dir, label="live-state directory")
    path = write_day_net_reset(plugin_dir(root, plugin_id), reason=reason)
    ctx.writer.echo(
        f"day_net reset requested for plugin {plugin_id} at {path}; "
        "a running host zeros day_net on the next tick (max_daily_loss unchanged)"
    )
    return EXIT_OK
