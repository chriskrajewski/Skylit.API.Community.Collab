"""Ephemeral runner configs for plugin backtests.

A backtest may use a copy of a runner YAML with different risk/fills. The copy is
written under ``uploads/`` (one-shot) or as a new ConfigStore runner name (kept draft).
The source (live/prod) config is never written.
"""

from __future__ import annotations

import copy
import secrets
from pathlib import Path
from typing import Any

import yaml

from fse_ui.configs import NAME_RE, ConfigStore, ConfigStoreError, Validation, validate
from fse_ui.workflows import UPLOAD_ID_RE, UploadError

# Host risk / fills / hold_through_break — the knobs Operators edit on Plugins.
ALLOWED_RISK = frozenset(
    {"max_contracts", "max_open_trades", "max_daily_loss", "max_trades_per_day"}
)
ALLOWED_FILLS = frozenset(
    {"trade_through_ticks", "slippage_ticks", "stop_fill", "commission_rt"}
)


class OverrideError(ValueError):
    """Bad source, name, or patch (HTTP 422)."""


def paperize_order_modes(data: dict[str, Any]) -> dict[str, Any]:
    """Force every plugin to paper so a create/upload is allowed and backtests stay paper."""
    out = copy.deepcopy(data)
    plugins = out.get("plugins")
    if not isinstance(plugins, list):
        return out
    for entry in plugins:
        if not isinstance(entry, dict):
            continue
        execution = entry.get("execution")
        if isinstance(execution, dict):
            execution["order_mode"] = "paper"
        elif "execution" in entry:
            entry["execution"] = {"order_mode": "paper"}
    return out


def _filter_map(raw: dict[str, Any] | None, allowed: frozenset[str], label: str) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise OverrideError(f"{label} must be a mapping")
    bad = sorted(set(raw) - allowed)
    if bad:
        raise OverrideError(f"unknown {label} field(s): {', '.join(bad)}")
    return {k: v for k, v in raw.items() if v is not None}


def apply_plugin_patches(
    data: dict[str, Any], patches: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Merge per-plugin risk/fills/hold_through_break/config into a runner document."""
    out = copy.deepcopy(data)
    plugins = out.get("plugins")
    if not isinstance(plugins, list):
        raise OverrideError("runner YAML has no plugins list")
    by_id = {
        p["id"]: p
        for p in plugins
        if isinstance(p, dict) and isinstance(p.get("id"), str)
    }
    for pid, patch in patches.items():
        if not isinstance(patch, dict):
            raise OverrideError(f"plugins[{pid!r}] must be a mapping")
        entry = by_id.get(pid)
        if entry is None:
            raise OverrideError(f"plugin {pid!r} is not in the source config")
        risk = _filter_map(patch.get("risk"), ALLOWED_RISK, f"plugins.{pid}.risk")
        if risk:
            block = entry.setdefault("risk", {})
            if not isinstance(block, dict):
                raise OverrideError(f"plugins.{pid}.risk is not a mapping")
            block.update(risk)
        fills = _filter_map(patch.get("fills"), ALLOWED_FILLS, f"plugins.{pid}.fills")
        if fills:
            block = entry.setdefault("fills", {})
            if not isinstance(block, dict):
                raise OverrideError(f"plugins.{pid}.fills is not a mapping")
            if (
                "commission_rt" in fills
                and fills["commission_rt"] is not None
                and not isinstance(fills["commission_rt"], dict)
            ):
                raise OverrideError(f"plugins.{pid}.fills.commission_rt must be a mapping")
            block.update(fills)
        if "hold_through_break" in patch and patch["hold_through_break"] is not None:
            if not isinstance(patch["hold_through_break"], bool):
                raise OverrideError(f"plugins.{pid}.hold_through_break must be a boolean")
            entry["hold_through_break"] = patch["hold_through_break"]
        if "config" in patch and patch["config"] is not None:
            if not isinstance(patch["config"], dict):
                raise OverrideError(f"plugins.{pid}.config must be a mapping")
            cfg = entry.setdefault("config", {})
            if not isinstance(cfg, dict):
                raise OverrideError(f"plugins.{pid}.config is not a mapping")
            # Opaque plugin config (no secrets in a plugin's config schema); shallow merge only.
            cfg.update(patch["config"])
        unknown = set(patch) - {"risk", "fills", "hold_through_break", "config"}
        if unknown:
            raise OverrideError(
                f"unknown plugins.{pid} field(s): {', '.join(sorted(unknown))}"
            )
    return out


def dump_runner(data: dict[str, Any]) -> str:
    text = str(yaml.safe_dump(data, sort_keys=False, default_flow_style=False))
    return text if text.endswith("\n") else text + "\n"


def _draft_name(source: str, name: str | None) -> str:
    if name:
        if not NAME_RE.fullmatch(name):
            raise OverrideError(f"invalid draft name {name!r}")
        if name == source:
            raise OverrideError("draft name must differ from the source config")
        return name
    # short, unique, clearly a backtest fork
    base = f"bt-{source}"[:50]
    return f"{base}-{secrets.token_hex(3)}"


def materialize_override(
    store: ConfigStore,
    *,
    source: str,
    uploads_root: Path,
    author: str,
    keep: bool = False,
    name: str | None = None,
    text: str | None = None,
    plugins: dict[str, dict[str, Any]] | None = None,
) -> tuple[Validation, dict[str, Any] | None]:
    """Build a paperized override and write it without touching ``source``.

    Returns ``(validation, payload)``. ``payload`` is ``None`` when validation fails.
    """
    try:
        doc = store.get("runner", source)
    except ConfigStoreError as exc:
        raise OverrideError(str(exc)) from exc
    source_sha = str(doc["sha"])
    source_text = str(doc["text"])

    if text is not None and plugins:
        raise OverrideError("pass either text or plugins patches, not both")
    if text is not None:
        try:
            loaded = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise OverrideError(f"override YAML does not parse: {exc}") from exc
        if not isinstance(loaded, dict):
            raise OverrideError("override YAML must be a mapping")
        data = paperize_order_modes(loaded)
    else:
        try:
            loaded = yaml.safe_load(source_text)
        except yaml.YAMLError as exc:
            raise OverrideError(f"source YAML does not parse: {exc}") from exc
        if not isinstance(loaded, dict):
            raise OverrideError("source YAML must be a mapping")
        data = paperize_order_modes(loaded)
        if plugins:
            data = apply_plugin_patches(data, plugins)

    out_text = dump_runner(data)
    result = validate("runner", out_text, baseline_text=store.baseline_text())
    if not result.ok:
        return result, None

    if keep:
        draft = _draft_name(source, name)
        # create compares to all-paper; we already paperized, so order_mode is fine
        saved_result, saved = store.save(
            "runner",
            draft,
            out_text,
            author=author,
            message=f"backtest override from {source}@{source_sha}; live unchanged",
            create=True,
        )
        if saved is None:
            return saved_result, None
        # Prove isolation: source sha still matches what we read.
        after = store.get("runner", source)
        if after["sha"] != source_sha:
            raise OverrideError("source config changed during override create; retry")
        return result, {
            "storage": "runner",
            "name": draft,
            "path": str(saved["path"]),
            "sha": saved["sha"],
            "source": source,
            "source_sha": source_sha,
            "source_unchanged": True,
            "live_untouched": True,
        }

    upload_id = secrets.token_hex(8)
    assert UPLOAD_ID_RE.fullmatch(upload_id)
    folder = uploads_root / upload_id
    try:
        folder.mkdir(parents=True, mode=0o700)
        path = folder / "runner.override.yaml"
        path.write_text(out_text, encoding="utf-8")
    except OSError as exc:
        raise UploadError(f"cannot write override upload: {exc}") from exc
    after = store.get("runner", source)
    if after["sha"] != source_sha:
        raise OverrideError("source config changed during override write; retry")
    return result, {
        "storage": "upload",
        "name": None,
        "path": str(path),
        "upload_id": upload_id,
        "sha": None,
        "source": source,
        "source_sha": source_sha,
        "source_unchanged": True,
        "live_untouched": True,
    }
