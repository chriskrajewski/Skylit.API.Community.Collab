"""``fse live``: today's session in the Strategy_Config's Order_Mode (design §24, Req 24).

``fse live --config FILE [--live-orders] [--cache-dir DIR] [--calendar-dir DIR]
[--state-dir DIR] [--recordings-root DIR] [--out DIR]`` runs the same
Live_Runner as ``fse paper``. The Order_Mode is resolved once, at start
(:func:`fse.live.order_router.resolve_order_mode`):

- ``order_mode: combine`` runs Combine only when ``COMBINE_ACCOUNT_ID`` is set
  and equals an account the Broker_Adapter resolves, and ``--live-orders`` is
  given (Req 24.1);
- ``order_mode: practice`` runs Practice only when ``PRACTICE_ACCOUNT_ID`` is
  set, resolved and different from ``COMBINE_ACCOUNT_ID`` (Req 24.6);
- anything else, or any failed condition, runs Paper, and the first
  Finding_Card names every failed condition (Req 24.3).

Account ids and credentials come only from the environment (the shell or the
Project ``.env``); there is no flag for them (Req 24.7). Practice and Combine
need the account in Auto OCO Brackets mode (OQ7); a rejected bracket closes
the position and blocks entries until ``fse clear``.

Outputs are those of ``fse paper``, plus ``order_journal.jsonl`` and
``client_seq.json`` in the live-state directory.
"""

from __future__ import annotations

import argparse
from typing import Final

from fse.clock import Clock
from fse.commands import CommandContext, SubParsers
from fse.commands.paper import BrokerSetup, Prepared, add_live_arguments, prepare, run_live
from fse.live.broker_safety import BrokerSafety, ClientIds, OrderJournal
from fse.live.order_router import (
    BROKER_MODES,
    LIVE_ORDERS_FLAG,
    PAPER,
    ModeDecision,
    resolve_order_mode,
)
from fse.live.state_store import BlockStore
from fse.projectx.broker import BrokerAdapter, BrokerModeError
from fse.projectx.session import ProjectXSession

__all__ = ["NAME", "register", "run_live_command"]

NAME: Final = "live"

_DESCRIPTION = """\
Run today's session on live data in the Strategy_Config's Order_Mode.
Combine Order_Mode needs all three Combine_Opt_In conditions: the config
selects combine, COMBINE_ACCOUNT_ID equals an account the Broker_Adapter
resolves, and --live-orders is given. Practice Order_Mode needs
PRACTICE_ACCOUNT_ID set, resolved and different from COMBINE_ACCOUNT_ID.
Otherwise the run is Paper and the first Finding_Card names each failed
condition. fse paper always runs Paper."""

_EPILOG = """\
exit status:
  0  the session ran to its end
  1  a feed stopped during the run, or an unexpected error
  2  invalid input: a flag, the Strategy_Config, a calendar file, a path, no
     session now, or no contract id
  3  SKYLIT_API_KEY or a ProjectX credential variable is blank
  4  the order journal in the live-state directory cannot be read"""


def register(subparsers: SubParsers) -> None:
    parser = subparsers.add_parser(
        NAME,
        help="run today's session in the Strategy_Config's Order_Mode",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_live_arguments(parser)
    parser.add_argument(
        LIVE_ORDERS_FLAG,
        action="store_true",
        help="the explicit opt-in Combine Order_Mode needs (Combine_Opt_In)",
    )
    parser.set_defaults(handler=run_live_command)


def run_live_command(args: argparse.Namespace, ctx: CommandContext) -> int:
    prep = prepare(args, ctx)
    return run_live(ctx, prep, broker_setup(ctx, live_orders=bool(args.live_orders)))


def broker_setup(ctx: CommandContext, *, live_orders: bool) -> BrokerSetup:
    """Resolve the Order_Mode and, for Practice or Combine, build the BrokerSafety."""

    async def setup(
        prep: Prepared, px: ProjectXSession, clock: Clock
    ) -> tuple[ModeDecision, BrokerSafety | None]:
        cfg = prep.cfg
        if cfg.order_mode not in BROKER_MODES:
            return ModeDecision(cfg.order_mode, PAPER), None
        adapter = BrokerAdapter(px, ctx.env, clock)
        accounts = await adapter.resolve_accounts()
        decision = resolve_order_mode(cfg.order_mode, ctx.env, accounts, live_orders=live_orders)
        if decision.mode not in BROKER_MODES:
            return decision, None
        try:
            bound = adapter.for_mode(decision.mode)
        except BrokerModeError as exc:
            return ModeDecision(cfg.order_mode, PAPER, (str(exc),)), None
        state_dir = prep.dirs["live-state directory"]
        journal = OrderJournal(ctx.writer, state_dir)
        safety = BrokerSafety(
            adapter=bound,
            live=cfg.live,
            instruments=prep.engine.params.instruments,
            expected_contracts={f.instrument: f.contract_id for f in prep.feeds},
            blocks=BlockStore(ctx.writer, state_dir),
            journal=journal,
            ids=ClientIds(ctx.writer, state_dir, journal),
            clock=clock,
            session_start_ns=prep.calendars.exchange.sessions.trading_day_start(prep.session),
        )
        return decision, safety

    return setup
