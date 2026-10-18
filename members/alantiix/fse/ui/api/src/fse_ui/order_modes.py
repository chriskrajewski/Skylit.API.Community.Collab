"""Live → Order mode: Paper, Practice or Combine for a broker plugin of the selected
runner config.

Generic for every plugin: a broker plugin is an enabled entry whose ``needs()``
declares ``broker_orders``; no plugin id appears here. Practice or Combine can be
chosen only when :func:`disabled_reasons` (the deployment, secrets, runner switch
and mode) and the plugin's :func:`mode_blocked` (an engine dry run) are both empty.
Combine also needs ``COMBINE_ACCOUNT_ID`` and a paper account with the flat deadline
on (engine ``execution_problems``); the AWS runner passes ``--live-orders`` with
``--plugin-orders`` so Combine can resolve.

:func:`apply_order_mode` edits the runner YAML text in place (comments kept) on the
``yaml.compose`` node marks; PyYAML has no round-trip mode and no new dependency is
taken. Every edit is checked: the edited text must load to exactly the old document
with the intended keys changed.
"""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import yaml
from fse.live.host.config import (
    RunnerConfig,
    RunnerConfigError,
    execution_brokerage_problem,
    parse_runner_config,
)
from fse.live.plugins.api import DataNeeds
from fse.live.plugins.loader import resolve_plugin_class
from yaml import MappingNode, Node, ScalarNode, SequenceNode

from fse_ui.aws.errors import AWS_ERRORS, error_code
from fse_ui.configs import parse_yaml
from fse_ui.plugins import _services
from fse_ui.runner import MAX_STATUS_BYTES, PLUGIN_ID_RE, last_order_mode, latest_run_dir
from fse_ui.secrets_store import SecretStore
from fse_ui.settings import Settings

type OrderMode = Literal["paper", "practice", "combine"]
ORDER_MODES: tuple[OrderMode, ...] = ("paper", "practice", "combine")
BROKER_ORDER_MODES: tuple[OrderMode, ...] = ("practice", "combine")
MONEY_RE = re.compile(r"^\d{1,7}(\.\d{1,2})?$")
MAX_TRADES_PER_DAY = 500
PLACEHOLDER_CAPS = ("1", 1)
"""The caps of the :func:`mode_blocked` dry run (the user's caps come with the POST)."""
NY = ZoneInfo("America/New_York")

CANNOT_EDIT = "cannot edit this runner config automatically; edit it on the Configs page"
REASON_SWITCH_OFF = (
    "plugin orders are off for this deployment (Terraform plugin_orders_enabled=false)"
)
REASON_TERMS = "the TopstepX terms are not marked verified (Terraform topstep_terms_verified_on)"
REASON_PRACTICE_ID = "PRACTICE_ACCOUNT_ID is not set (Settings → Secrets)"
REASON_COMBINE_ID = "COMBINE_ACCOUNT_ID is not set (Settings → Secrets)"
REASON_PROJECTX = "PROJECTX_USERNAME or PROJECTX_API_KEY is not set (Settings → Secrets)"
REASON_SAME_ACCOUNT = (
    "PRACTICE_ACCOUNT_ID equals COMBINE_ACCOUNT_ID; practice needs its own account"
)
REASON_RUNNER_SWITCH = "the runner switch control/plugin_orders is not enabled"
REASON_PAPER_MODE = "the runner mode is fse paper; plugin orders need fse run"
REASON_LOCAL = "console plugin orders run only on the AWS runner"
PAPER_NOTE = (
    "Paper keeps hold_through_break: false; set it on the Configs page if you want it back."
)


class OrderModeError(ValueError):
    """The edit cannot be made (HTTP 422); the message is shown as is."""


# ---------------------------------------------------------------- broker plugins


@dataclass(frozen=True, slots=True)
class BrokerPlugin:
    id: str
    label: str
    order_mode: str
    max_daily_loss: str | None
    max_trades_per_day: int | None
    hold_through_break: bool
    has_risk: bool
    brokerage: str
    instruments: frozenset[str]


def _parse(text: str) -> RunnerConfig | None:
    parsed = parse_yaml(text)
    if parsed.problems:
        return None
    try:
        return parse_runner_config(parsed.data, Path("runner.yaml"))
    except RunnerConfigError:
        return None


def broker_plugins(text: str, project_dir: Path, scratch: Path) -> list[BrokerPlugin]:
    """The enabled entries of ``text`` whose plugin declares ``needs().broker_orders``.

    Each is constructed as :func:`fse_ui.plugins.check_plugins` does (no network);
    one that cannot be loaded is left out. ``[]`` for a text that does not parse.
    """
    cfg = _parse(text)
    if cfg is None:
        return []
    out: list[BrokerPlugin] = []
    for entry in cfg.enabled_plugins:
        try:
            cls = resolve_plugin_class(entry.id, entry.module, project_dir=project_dir)
            plugin = cls(entry.id, dict(entry.config), _services(entry, cfg, project_dir, scratch))
            needs = plugin.needs()
        except Exception:  # noqa: S112 (not offered; the Configs page check reports it)
            continue
        if not isinstance(needs, DataNeeds) or not needs.broker_orders:
            continue
        instruments = needs.instruments  # as host.py builds a slot
        risk = entry.risk
        out.append(
            BrokerPlugin(
                id=entry.id,
                label=cls.display_label(),
                order_mode=entry.execution.order_mode,
                max_daily_loss=None
                if risk is None or risk.max_daily_loss is None
                else str(risk.max_daily_loss),
                max_trades_per_day=None if risk is None else risk.max_trades_per_day,
                hold_through_break=entry.hold_through_break,
                has_risk=risk is not None,
                brokerage=needs.brokerage,
                instruments=instruments,
            )
        )
    return out


# ---------------------------------------------------------------- reasons


@dataclass(frozen=True, slots=True)
class Switch:
    """The runner switch ``control/plugin_orders`` as read now."""

    value: str | None
    error: str | None = None

    @property
    def enabled(self) -> bool:
        return self.value == "enabled"


def read_switch(settings: Settings, ssm: Any) -> Switch:
    """A live read of the SSM switch (catches an apply that has not reached the host
    hooks yet). Without an SSM client (local console): the deployment setting."""
    if ssm is None:
        return Switch("enabled" if settings.plugin_orders else "disabled")
    if not settings.plugin_orders_param:
        return Switch(None, "FSE_UI_PLUGIN_ORDERS_PARAM is not set")
    try:
        found = ssm.get_parameter(Name=settings.plugin_orders_param)
    except AWS_ERRORS as exc:
        return Switch(None, error_code(exc))
    return Switch(str(found["Parameter"].get("Value", "")))


def another_plugin_reason(plugin_id: str | None, non_paper: Sequence[str]) -> str | None:
    others = [pid for pid in non_paper if pid != plugin_id]
    if not others:
        return None
    return f"another plugin ({', '.join(others)}) already uses a non-paper order mode"


def disabled_reasons(
    settings: Settings,
    secrets: SecretStore,
    ssm: Any,
    *,
    runner_mode: str,
    switch: Switch | None = None,
    non_paper: Sequence[str] = (),
    plugin_id: str | None = None,
    for_mode: OrderMode = "practice",
) -> list[str]:
    """Why ``for_mode`` (Practice or Combine) cannot be chosen now, in order; empty: it can.

    Shared deployment gates always apply. Practice also needs ``PRACTICE_ACCOUNT_ID``
    (≠ combine); Combine needs ``COMBINE_ACCOUNT_ID``. Reason 8 (another non-paper
    plugin) needs ``plugin_id``: without it (the GET's global list) it is reported
    per plugin in :func:`mode_blocked`.
    """
    if for_mode not in BROKER_ORDER_MODES:
        raise ValueError(f"for_mode must be practice or combine, not {for_mode!r}")
    if settings.runner_controller != "ssm":
        return [REASON_LOCAL]
    out: list[str] = []
    if not settings.plugin_orders:
        out.append(REASON_SWITCH_OFF)
    if settings.topstep_verified_on is None:
        out.append(REASON_TERMS)
    statuses = secrets.statuses()
    if for_mode == "practice":
        if statuses.get("PRACTICE_ACCOUNT_ID") != "set":
            out.append(REASON_PRACTICE_ID)
        if secrets.same("PRACTICE_ACCOUNT_ID", "COMBINE_ACCOUNT_ID") is True:
            out.append(REASON_SAME_ACCOUNT)
    else:
        if statuses.get("COMBINE_ACCOUNT_ID") != "set":
            out.append(REASON_COMBINE_ID)
    if statuses.get("PROJECTX_USERNAME") != "set" or statuses.get("PROJECTX_API_KEY") != "set":
        out.append(REASON_PROJECTX)
    switch = switch if switch is not None else read_switch(settings, ssm)
    if switch.error is not None:
        out.append(f"cannot read control/plugin_orders: {switch.error}")
    elif not switch.enabled:
        out.append(REASON_RUNNER_SWITCH)
    if runner_mode == "paper":
        out.append(REASON_PAPER_MODE)
    if plugin_id is not None:
        another = another_plugin_reason(plugin_id, non_paper)
        if another is not None:
            out.append(another)
    return out


def mode_blocked(
    text: str, plugin: BrokerPlugin, mode: OrderMode, *, non_paper: Sequence[str] = ()
) -> list[str]:
    """Why this plugin cannot go to ``mode``: an engine dry run of the edit (nothing saved)."""
    if mode not in BROKER_ORDER_MODES:
        raise ValueError(f"mode must be practice or combine, not {mode!r}")
    if not plugin.has_risk:
        return [f"{plugin.id} has no risk block (max_contracts); add one on the Configs page"]
    another = another_plugin_reason(plugin.id, non_paper)
    if another is not None:
        return [another]
    try:
        edited = apply_order_mode(text, plugin.id, mode, PLACEHOLDER_CAPS)
    except OrderModeError as exc:
        return [str(exc)]
    parsed = parse_yaml(edited)
    try:
        cfg = parse_runner_config(parsed.data, Path("runner.yaml"))
    except RunnerConfigError as exc:
        return list(exc.problems)
    entry = next(e for e in cfg.plugins if e.id == plugin.id)
    problem = execution_brokerage_problem(
        cfg,
        entry,
        brokerage=plugin.brokerage,
        broker_orders=True,
        instruments=plugin.instruments,
    )
    return [] if problem is None else [problem]


def practice_blocked(
    text: str, plugin: BrokerPlugin, *, non_paper: Sequence[str] = ()
) -> list[str]:
    """Why this plugin cannot go to Practice (:func:`mode_blocked` for practice)."""
    return mode_blocked(text, plugin, "practice", non_paper=non_paper)


def combine_blocked(
    text: str, plugin: BrokerPlugin, *, non_paper: Sequence[str] = ()
) -> list[str]:
    """Why this plugin cannot go to Combine (:func:`mode_blocked` for combine)."""
    return mode_blocked(text, plugin, "combine", non_paper=non_paper)


# ---------------------------------------------------------------- visibility


def effective_modes(runs_dir: Path) -> dict[str, dict[str, Any] | None]:
    """Each plugin's last ``order_mode`` line in the newest run (``None``: no line)."""
    run_dir = latest_run_dir(runs_dir)
    base = None if run_dir is None else run_dir / "plugins"
    out: dict[str, dict[str, Any] | None] = {}
    if base is not None and base.is_dir():
        for d in sorted(base.iterdir()):
            if d.is_dir() and PLUGIN_ID_RE.fullmatch(d.name):
                out[d.name] = last_order_mode(d / "plugin_log.jsonl")
    return out


def busy_plugins(live_state: Path) -> dict[str, int | None]:
    """Plugins whose ``status.json`` shows an open position or a working order, with
    the file's ``updated_ns``. The console cannot see the broker, only these files."""
    out: dict[str, int | None] = {}
    base = live_state / "plugins"
    if not base.is_dir():
        return out
    for d in sorted(base.iterdir()):
        if not (d.is_dir() and PLUGIN_ID_RE.fullmatch(d.name)):
            continue
        status = None
        with contextlib.suppress(OSError, ValueError):
            path = d / "status.json"
            if path.stat().st_size <= MAX_STATUS_BYTES:
                status = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(status, dict):
            continue
        if status.get("positions") or status.get("working"):
            updated = status.get("updated_ns")
            out[d.name] = updated if isinstance(updated, int) else None
    return out


def busy_message(ids: Sequence[str], busy: dict[str, int | None]) -> str:
    stamps = [busy[i] for i in ids if busy.get(i) is not None]
    when = "unknown time"
    if stamps:
        latest = max(s for s in stamps if s is not None)
        when = datetime.fromtimestamp(latest / 1e9, NY).strftime("%Y-%m-%d %H:%M:%S ET")
    return (
        f"{', '.join(ids)} have an open position or working order (as of {when}); "
        "flatten or wait, then retry"
    )


def switch_pending(switch: Switch, effective: dict[str, dict[str, Any] | None]) -> bool:
    """The switch is not enabled but a plugin still runs (or asked for) a broker mode."""
    if switch.enabled:
        return False
    return any(line is not None and line.get("mode") != "paper" for line in effective.values())


# ---------------------------------------------------------------- the YAML edit


def _find(mapping: MappingNode, key: str) -> Node | None:
    for k, v in mapping.value:
        if isinstance(k, ScalarNode) and k.value == key:
            return v
    return None


def _last_end(node: Node) -> int:
    """The text index where ``node``'s last value ends (a block collection's own end
    mark is the next token, after any comments)."""
    if isinstance(node, MappingNode) and not node.flow_style and node.value:
        return _last_end(node.value[-1][1])
    if isinstance(node, SequenceNode) and not node.flow_style and node.value:
        return _last_end(node.value[-1])
    return int(node.end_mark.index)


def _after_line(text: str, node: Node) -> int:
    end = _last_end(node)
    if end > 0 and text[end - 1] == "\n":  # a block scalar ends at the next line
        return end
    nl = text.find("\n", end)
    return len(text) if nl < 0 else nl + 1


type Edit = tuple[int, int, int, str]
"""``(start, end, order, replacement)``; an insert has ``start == end``."""


def _set_keys(
    text: str, mapping: MappingNode, pairs: list[tuple[str, str]], edits: list[Edit]
) -> None:
    """Set each ``key: value`` (value as YAML text) in ``mapping``: replace an existing
    scalar in place, insert a missing key after the mapping's last value; a flow
    mapping is regenerated as a flow mapping."""
    if mapping.flow_style:
        wanted = dict(pairs)
        items: list[str] = []
        for k, v in mapping.value:
            if not isinstance(k, ScalarNode):
                raise OrderModeError(CANNOT_EDIT)
            key_text = text[k.start_mark.index : k.end_mark.index]
            value_text = wanted.pop(k.value, None)
            if value_text is None:
                value_text = text[v.start_mark.index : v.end_mark.index]
            items.append(f"{key_text}: {value_text}")
        items.extend(f"{k}: {v}" for k, v in wanted.items())
        edits.append(
            (
                mapping.start_mark.index,
                mapping.end_mark.index,
                len(edits),
                "{" + ", ".join(items) + "}",
            )
        )
        return
    if not mapping.value:
        raise OrderModeError(CANNOT_EDIT)
    indent = " " * mapping.value[0][0].start_mark.column
    missing: list[str] = []
    for key, value in pairs:
        node = _find(mapping, key)
        if node is None:
            sep = "" if value.startswith("\n") else " "
            missing.append(f"{indent}{key}:{sep}{value}\n")
            continue
        if not isinstance(node, ScalarNode) or node.start_mark.index == node.end_mark.index:
            raise OrderModeError(CANNOT_EDIT)
        edits.append((node.start_mark.index, node.end_mark.index, len(edits), value))
    if missing:
        at = _after_line(text, mapping)
        lead = "" if at == 0 or text[at - 1] == "\n" else "\n"
        edits.append((at, at, len(edits), lead + "".join(missing)))


def _entry_node(text: str, plugin_id: str) -> tuple[int, MappingNode]:
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        raise OrderModeError(CANNOT_EDIT) from None
    if not isinstance(root, MappingNode):
        raise OrderModeError(CANNOT_EDIT)
    seq = _find(root, "plugins")
    if not isinstance(seq, SequenceNode):
        raise OrderModeError(CANNOT_EDIT)
    for i, item in enumerate(seq.value):
        if isinstance(item, MappingNode):
            found = _find(item, "id")
            if isinstance(found, ScalarNode) and found.value == plugin_id:
                return i, item
    raise OrderModeError(f"{plugin_id} is not a plugin of this runner config")


def _unshared(obj: Any, depth: int = 0) -> Any:
    """A copy where no two places share one dict or list (YAML aliases do)."""
    if depth > 64:
        raise OrderModeError(CANNOT_EDIT)
    if isinstance(obj, dict):
        return {k: _unshared(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_unshared(v, depth + 1) for v in obj]
    return obj


def apply_order_mode(
    text: str, plugin_id: str, mode: str, caps: tuple[str, int] | None = None
) -> str:
    """``text`` with ``plugins[<plugin_id>].execution.order_mode = mode``; for Practice
    or Combine also ``risk.max_daily_loss`` (quoted money), ``risk.max_trades_per_day``
    and ``hold_through_break: false``. Comments and every other line are kept.

    A missing ``risk`` is never inserted (:class:`OrderModeError` with the per-plugin
    reason); a text this cannot edit exactly raises :data:`CANNOT_EDIT`.
    """
    if mode not in ORDER_MODES:
        raise OrderModeError(f"order_mode {mode!r} is not available in the console")
    i, entry = _entry_node(text, plugin_id)
    edits: list[Edit] = []
    loss_text: str | None = None
    brokered = mode in BROKER_ORDER_MODES
    if brokered:
        if caps is None:
            raise OrderModeError(f"{mode} needs max_daily_loss and max_trades_per_day")
        risk = _find(entry, "risk")
        if not isinstance(risk, MappingNode):
            raise OrderModeError(
                f"{plugin_id} has no risk block (max_contracts); add one on the Configs page"
            )
        loss_text = str(Decimal(caps[0]))
        _set_keys(
            text,
            risk,
            [("max_daily_loss", f'"{loss_text}"'), ("max_trades_per_day", str(int(caps[1])))],
            edits,
        )
    execution = _find(entry, "execution")
    entry_pairs: list[tuple[str, str]] = []
    if execution is not None:
        if not isinstance(execution, MappingNode):
            raise OrderModeError(CANNOT_EDIT)
        _set_keys(text, execution, [("order_mode", mode)], edits)
    elif mode != "paper":  # a missing execution block is paper already
        if entry.flow_style:
            entry_pairs.append(("execution", f"{{order_mode: {mode}}}"))
        else:
            step = " " * (entry.value[0][0].start_mark.column + 2)
            entry_pairs.append(("execution", f"\n{step}order_mode: {mode}"))
    if brokered:
        entry_pairs.insert(0, ("hold_through_break", "false"))
    if entry_pairs:
        _set_keys(text, entry, entry_pairs, edits)
    new = text
    for start, end, _, replacement in sorted(edits, key=lambda e: (e[0], e[2]), reverse=True):
        new = new[:start] + replacement + new[end:]
    # Self-check: the edited text loads to the old document with only these keys changed.
    # Unshared, so an anchored block edited through one plugin shows up in the other.
    expected = _unshared(yaml.safe_load(text))
    target = expected["plugins"][i]
    if execution is not None or mode != "paper":
        if not isinstance(target.get("execution", {}), dict):
            raise OrderModeError(CANNOT_EDIT)
        target.setdefault("execution", {})["order_mode"] = mode
    if brokered and caps is not None:
        target["risk"]["max_daily_loss"] = loss_text
        target["risk"]["max_trades_per_day"] = int(caps[1])
        target["hold_through_break"] = False
    try:
        if yaml.safe_load(new) != expected:
            raise OrderModeError(CANNOT_EDIT)
    except yaml.YAMLError:
        raise OrderModeError(CANNOT_EDIT) from None
    return new


def check_caps(max_daily_loss: str | None, max_trades_per_day: int | None) -> tuple[str, int]:
    """The Practice/Combine caps from the POST body (:class:`OrderModeError` when unusable)."""
    if max_daily_loss is None or max_trades_per_day is None:
        raise OrderModeError("practice and combine need max_daily_loss and max_trades_per_day")
    loss = max_daily_loss.strip()
    if not MONEY_RE.fullmatch(loss) or Decimal(loss) <= 0:
        raise OrderModeError(
            "max_daily_loss must be a dollar amount above 0 with at most 2 decimals"
        )
    if isinstance(max_trades_per_day, bool) or not 1 <= max_trades_per_day <= MAX_TRADES_PER_DAY:
        raise OrderModeError(f"max_trades_per_day must be 1..{MAX_TRADES_PER_DAY}")
    return loss, max_trades_per_day
