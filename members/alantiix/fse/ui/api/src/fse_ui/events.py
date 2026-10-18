"""Plain-English sentences for the runner's host-log and plugin-log lines.

Every ``kind`` the fse runner core writes (``src/fse/live``, ``src/fse/projectx``)
has an entry here; ``tests/test_events.py`` fails when the engine adds one
without a sentence. Plugin-specific kinds (``<plugin>_entry`` ...) are not
listed: no plugin is named in the console. They get the generic rendering
(the kind in words plus its short fields), so a new plugin's log is readable on
day one.

``describe(entry)`` -> ``{"text", "severity", "category"}``; severity is the
line's ``level`` (``info``/``warning``/``error``) when it has one.
"""

from __future__ import annotations

import re
import string
from collections.abc import Mapping
from typing import Any, Final

__all__ = ["EVENTS", "describe"]

# kind -> (category, default severity, sentence template). Templates use {field}
# placeholders from the log line; a missing field renders empty and is tidied away.
EVENTS: Final[Mapping[str, tuple[str, str, str]]] = {
    # ---- plugin lifecycle
    "plugin_loaded": ("lifecycle", "info", "Plugin {plugin} loaded ({module})"),
    "plugin_restored": ("lifecycle", "info", "Plugin {plugin} restored its saved state"),
    "plugin_fresh_start": ("lifecycle", "info", "Plugin {plugin} started fresh (no saved state)"),
    "restore_failed": (
        "lifecycle",
        "error",
        "Plugin {plugin} could not restore its state; entries blocked until cleared: {detail}",
    ),
    "restart_stale": (
        "orders",
        "error",
        "Plugin {plugin} had a position from {trade_session} at restart; it was closed",
    ),
    "plugin_faulted": (
        "lifecycle",
        "error",
        "Plugin {plugin} stopped on an error in {hook}: {error}",
    ),
    "spawned_task_failed": (
        "lifecycle",
        "error",
        "A background task of {plugin} ({task}) failed",
    ),
    "state_write_failed": (
        "health",
        "error",
        "Could not save the state of {plugin} ({failures} failures): {detail}",
    ),
    "plugin_log_failed": ("health", "error", "Could not write the log of {plugin}"),
    "card_invalid": (
        "health",
        "warning",
        "Plugin {plugin} logged a card the Cards page cannot show: {detail}",
    ),
    "card_write_failed": ("health", "error", "Could not write the decision cards of {plugin}"),
    "marks_write_failed": (
        "health",
        "warning",
        "Could not write the live marks (live P&L stops updating): {detail}",
    ),
    "status_failed": (
        "health",
        "warning",
        "Plugin {plugin} gave no usable status: {detail}",
    ),
    "hook_latency": (
        "health",
        "warning",
        "Plugin {plugin} took {elapsed_ms} ms in {hook} (limit 1000 ms)",
    ),
    "intents_ignored": (
        "orders",
        "warning",
        "Orders from {plugin} were ignored: {detail}",
    ),
    "template_start": ("lifecycle", "info", "Template plugin {plugin} started"),
    "rules": ("orders", "info", "Rules {event}: {reason}"),
    "rules_entry": (
        "orders",
        "info",
        "Rules entry: {side} {qty} {instrument} {tag} ({reason})",
    ),
    # ---- days and calendars
    "day_net_reset": (
        "risk",
        "info",
        "Paper day net of {plugin} reset for today's max-loss budget ({reason})",
    ),
    "day_open": ("session", "info", "Globex day {day} opened"),
    "host_break_backstop": (
        "orders",
        "error",
        "Plugin {plugin} still had exposure at the daily break; the host closed it",
    ),
    "host_stopped": ("lifecycle", "info", "Runner stopped"),
    "calendar_reloaded": ("session", "info", "Exchange calendars reloaded"),
    "calendar_stale": (
        "session",
        "error",
        "Exchange calendars could not be reloaded; entries are blocked: {detail}",
    ),
    "session_open_late": ("session", "warning", "Session opened late: {detail}"),
    "session_over": ("session", "info", "Session over"),
    "session_skipped": ("session", "info", "Session skipped: {reason}"),
    "entry_skipped": ("orders", "info", "Entry skipped: {reason}"),
    # ---- orders (host broker)
    "intent_accepted": ("orders", "info", "Order accepted: {tag} ({intent})"),
    "intent_rejected": ("orders", "warning", "Order refused ({reason}): {tag}"),
    "order_update": ("orders", "info", "Order {tag}: {status}"),
    "flatten_no_price": (
        "orders",
        "warning",
        "Could not close {tag}: no price yet; retrying on the next bar",
    ),
    "post_close_bar": (
        "data",
        "info",
        "A {instrument} bar arrived after the session close and was not traded",
    ),
    "root_block_ignored": ("risk", "info", "Block {block_kind} does not apply here: {detail}"),
    "root_block_unknown": (
        "risk",
        "warning",
        "Unknown block {block_kind}: every entry is refused until it is cleared",
    ),
    # ---- host broker orders (fse.live.host.live_orders, fse.live.host.host)
    "broker": (
        "orders",
        "info",
        "Broker for {plugin}: {event} ({instrument}, {outcome}) {reason} {detail}",
    ),
    "broker_carried": (
        "orders",
        "info",
        "Broker action {action} of {plugin} held until the broker session starts",
    ),
    "broker_rollover": (
        "orders",
        "warning",
        "Broker day rollover for {plugin}: {tag} {detail} ({rollover_counts})",
    ),
    "broker_flatten_skipped": (
        "orders",
        "warning",
        "Broker exit for {tag} not sent: the paper close was refused ({reason})",
    ),
    "broker_exit_dup_skipped": (
        "orders",
        "info",
        "Broker exit for {tag} is already open; not sent again",
    ),
    "broker_cancel_ignored": (
        "orders",
        "info",
        "Cancel of {tag} ignored: its market entry was already sent to the broker",
    ),
    "broker_exit_deferred": (
        "orders",
        "info",
        "Broker exit for {instrument} waits for its entry to fill ({engine_id})",
    ),
    "order_mode": ("orders", "info", "Order mode of {plugin}: {mode} ({requested_text}) {detail}"),
    "broker_note": ("orders", "warning", "Broker note for {plugin}: {detail}"),
    "broker_task_stopped": (
        "orders",
        "error",
        "The broker task stopped; the run will exit with an error: {detail}",
    ),
    "broker_task_failed": ("orders", "error", "The broker task of {plugin} failed ({error})"),
    "broker_withdraw_late": (
        "orders",
        "warning",
        "The broker never got the entry of {plugin} {tag}, but its paper entry had already filled",
    ),
    "broker_restart_skipped": (
        "orders",
        "warning",
        "The broker task of {plugin} was not restarted (the runner is stopping or the day changed)",
    ),
    "broker_stop_unsent": (
        "orders",
        "warning",
        "At stop, {count} broker actions of {plugin} were never sent",
    ),
    # ---- data plane
    "heartbeat": (
        "data",
        "info",
        "Heartbeat: ProjectX {connected_word} ({transport}, {subscription})",
    ),
    "subscription": ("data", "info", "ProjectX data subscription: {subscription}"),
    "subscription_probe": ("data", "info", "Probing the ProjectX data subscription"),
    "projectx_sim_subscription": (
        "data",
        "info",
        "ProjectX has no live data subscription; using sim bars (real time)",
    ),
    "backfill_day": ("data", "info", "Backfilled {instrument} bars for {day}"),
    "backfill_skipped": ("data", "info", "Backfill skipped: {detail}"),
    "backfill_failed": ("data", "warning", "Backfill failed: {detail}"),
    "feed_status": ("data", "info", "{feed} feed {up_word}: {detail}"),
    "feed_stopped": ("data", "error", "A data feed stopped: {detail}"),
    "credit_projection": (
        "data",
        "info",
        "Skylit plan for {day}: {total_credits} credits ({symbols_text}, every "
        "{refresh_interval_s} s, {window_start_ny}-{window_end_ny} ET)",
    ),
    "bar_feed_skipped": ("data", "info", "Bar feed skipped: {detail}"),
    "bar_store_skipped": ("data", "warning", "A bar was not stored: {detail}"),
    "recording_unreadable": ("data", "warning", "A recording could not be read: {detail}"),
    "recording_flush_failed": ("health", "error", "Could not write the recording: {detail}"),
    "recording_failed": ("health", "error", "Recording failed; the runner stops: {detail}"),
    "market_hub_connected": ("data", "info", "ProjectX market data connected"),
    "market_hub_disconnected": ("data", "warning", "ProjectX market data disconnected"),
    "market_hub_connect_failed": (
        "data",
        "warning",
        "ProjectX market data could not connect: {reason}",
    ),
    "market_hub_down_for_day": (
        "data",
        "error",
        "ProjectX market data is down for the day; polling instead",
    ),
    "market_hub_token_rotated": ("data", "info", "ProjectX login token renewed"),
    "market_hub_token_check_failed": ("data", "warning", "ProjectX token check failed"),
    "market_hub_rotation_failed": (
        "data",
        "warning",
        "ProjectX token renewal failed: {reason}",
    ),
    # ---- feed events (fse.live feeds, logged through the data plane)
    "bar_fetch_failed": ("data", "warning", "Fetching bars failed: {cause}"),
    "map_refresh_failed": ("data", "warning", "A Skylit map refresh failed: {cause}"),
    "dark_pool_fetch_failed": ("data", "warning", "Fetching dark-pool prints failed: {cause}"),
    "dark_pool_offset_cap": ("data", "warning", "Dark-pool prints capped for {ticker}"),
    "levels_compare": ("data", "info", "Skylit levels compared"),
    "levels_compare_failed": ("data", "warning", "Skylit levels compare failed: {cause}"),
    "stream_connected": ("data", "info", "Skylit stream connected"),
    "stream_reconnect": ("data", "warning", "Skylit stream reconnecting"),
    "stream_resumed": ("data", "info", "Skylit stream resumed"),
    "stream_event_malformed": ("data", "warning", "A Skylit stream message was malformed"),
    "stream_symbol_unavailable": ("data", "warning", "Skylit has no stream for {symbol}"),
    "market_hub_failed": ("data", "warning", "ProjectX market data failed: {reason}"),
    "contract_check": ("data", "info", "Contract check: {detail}"),
    # ---- paper accounts (fse.live.host.accounts)
    "account_fresh_start": ("lifecycle", "info", "Paper account {account} started fresh"),
    "account_restored": (
        "lifecycle",
        "info",
        "Paper account {account} restored its saved state ({status})",
    ),
    "account_restore_failed": (
        "lifecycle",
        "error",
        "Paper account {account} could not restore its state; entries blocked: {detail}",
    ),
    "account_unused": (
        "lifecycle",
        "info",
        "Paper account {account} is not used by any enabled plugin",
    ),
    "account_status": (
        "risk",
        "warning",
        "Paper account {account}: {old} -> {new} ({reason}); balance {balance}, equity {equity}",
    ),
    "account_flatten": (
        "risk",
        "warning",
        "Paper account {account} closed the positions of {plugins} ({reason})",
    ),
    "account_state_write_failed": (
        "health",
        "error",
        "Could not save paper account {account} ({failures} failures); entries refused: {detail}",
    ),
    # ---- the host trade journal (fse.live.host.journal)
    "journal_write_failed": (
        "health",
        "error",
        "Could not write the trade journal of {plugin}: {detail}",
    ),
    "journal_write_recovered": ("health", "info", "The trade journal of {plugin} is written again"),
    "exit_reasons_write_failed": (
        "health",
        "warning",
        "Could not save the exit reasons of {plugin}: {detail}",
    ),
    "exit_reasons_restore_failed": (
        "lifecycle",
        "warning",
        "Could not restore the exit reasons of {plugin}: {detail}",
    ),
    "fill": ("orders", "info", "Journal fill: {plugin} {tag} {leg}"),
    "trade": ("orders", "info", "Journal trade: {plugin} {tag} net {net} ({exit_reason})"),
    # ---- the Skylit Nexus mirror (fse.live.host.nexus)
    "nexus_disabled": (
        "health",
        "warning",
        "Nexus mirror of {account} is off: {reason}",
    ),
    "nexus_result": (
        "orders",
        "info",
        "Nexus {op} for {account} ({plugin} {tag} {leg}): {result}",
    ),
    "nexus_opens_unblocked": (
        "orders",
        "info",
        "Nexus mirror of {account} opens positions again ({day})",
    ),
    "nexus_mirror_failed": ("health", "error", "The Nexus mirror stopped: {detail}"),
    "nexus_outbox_read_failed": (
        "health",
        "error",
        "Could not read the Nexus outbox of {account}",
    ),
    "nexus_archive_failed": (
        "health",
        "warning",
        "Could not archive the Nexus outbox of {account}: {detail}",
    ),
    # ---- the Playbook session (self-brokered) and its broker
    "start": ("lifecycle", "info", "Session started"),
    "session_end": ("session", "info", "Session ended"),
    "decision": ("orders", "info", "Decision at {at_ny}"),
    "order_sent": ("orders", "info", "Order sent: {client_id}"),
    "sent": ("orders", "info", "Sent: {client_id}"),
    "ack": ("orders", "info", "Order acknowledged: {client_id}"),
    "order_cancel": ("orders", "info", "Order cancelled: {client_id}"),
    "order_modify": ("orders", "info", "Order changed: {client_id}"),
    "entry_filled": ("orders", "info", "Entry filled: {client_id}"),
    "adopt": ("orders", "info", "Existing order adopted: {client_id}"),
    "reconciliation": ("orders", "info", "Orders reconciled with the broker"),
    "resubmitted": ("orders", "info", "Order resubmitted: {client_id}"),
    "resubmit_skipped": ("orders", "info", "Resubmit skipped: {reason}"),
    "resubmit_dropped": ("orders", "warning", "Resubmit dropped: {reason}"),
    "broker_skip": ("orders", "info", "Broker skipped an order: {reason}"),
    "broker_failure": ("orders", "error", "Broker call failed: {reason}"),
}

_HIDDEN: Final = frozenset({"t_ns", "kind", "level", "plugin", "source", "traceback"})
_MAX: Final = 240


class _Fields(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return ""


def _tidy(text: str) -> str:
    """Drop what a missing field leaves behind: ``()``, ``(, x)``, a trailing ``:``."""
    text = re.sub(r"\(\s*,\s*", "(", text)
    text = re.sub(r",\s*\)", ")", text)
    text = re.sub(r"\s*\(\s*\)", "", text)
    text = re.sub(r"\s+", " ", text)
    return re.sub(r"[:\s]+$", "", text).strip()


def _short(value: Any) -> str:
    if isinstance(value, list | tuple):
        text = ", ".join(str(v) for v in value[:8])
    elif isinstance(value, dict):
        text = ", ".join(f"{k} {v}" for k, v in list(value.items())[:6])
    else:
        text = str(value)
    return text if len(text) <= 80 else text[:77] + "..."


def _derived(entry: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "connected" in entry:
        out["connected_word"] = "connected" if entry.get("connected") else "disconnected"
    if "up" in entry:
        out["up_word"] = "up" if entry.get("up") else "down"
    symbols = entry.get("symbols")
    if isinstance(symbols, list):
        out["symbols_text"] = ", ".join(str(s) for s in symbols)
    if "requested" in entry:
        out["requested_text"] = f"requested {entry.get('requested')}"
    if "carried_pending" in entry:
        counts = (
            f"carried {entry.get('carried_pending')}, withdrawn {entry.get('withdrawn')}, "
            f"dropped {entry.get('dropped')}, open positions {entry.get('open_positions')}"
        )
        out["rollover_counts"] = counts + (", forced" if entry.get("forced") else "")
    return out


def _generic(kind: str, entry: Mapping[str, Any]) -> str:
    words = kind.replace("_", " ").strip().capitalize() or "Event"
    parts = [
        f"{k.replace('_', ' ')} {_short(v)}"
        for k, v in entry.items()
        if k not in _HIDDEN and v not in (None, "", [], {})
    ][:5]
    return words + (": " + "; ".join(parts) if parts else "")


def describe(entry: Mapping[str, Any]) -> dict[str, str]:
    """``{"text", "severity", "category"}`` of one log line (any kind)."""
    raw_kind = entry.get("kind", entry.get("event"))
    kind = raw_kind if isinstance(raw_kind, str) else ""
    level = entry.get("level")
    # Host broker path logs kind=broker with event=reconciliation (not kind=reconciliation).
    if kind == "broker" and entry.get("event") == "reconciliation":
        plugin = entry.get("plugin") or "plugin"
        diffs = entry.get("differences") or []
        if isinstance(diffs, list | tuple) and diffs:
            detail = _short(diffs)
            text = f"Broker for {plugin}: reconciliation differences ({detail})"
            severity = level if level in ("info", "warning", "error") else "warning"
            return {"text": text[:_MAX], "severity": severity, "category": "orders"}
        text = f"Broker for {plugin}: orders match the broker"
        severity = level if level in ("info", "warning", "error") else "info"
        return {"text": text[:_MAX], "severity": severity, "category": "orders"}
    known = EVENTS.get(kind)
    if known is None:
        severity = level if level in ("info", "warning", "error") else "info"
        return {"text": _generic(kind, entry)[:_MAX], "severity": severity, "category": "plugin"}
    category, default, template = known
    fields = _Fields({k: _short(v) for k, v in entry.items()})
    fields.update(_derived(entry))
    text = _tidy(string.Formatter().vformat(template, (), fields))
    severity = level if level in ("info", "warning", "error") else default
    return {"text": text[:_MAX], "severity": severity, "category": category}
