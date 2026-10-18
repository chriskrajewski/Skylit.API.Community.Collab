"""Plugin manager: every discoverable plugin, its config schema and the start-time check.

:func:`check_plugins` does what ``fse run`` does before it starts any feed:
resolve each enabled class from its ``module``, construct it with the same
fills / risk / broker-terms services the host builds (constructors do no
network I/O; some read their own config files), call ``needs()`` and compare
it with the data plane (``needs_problems``). Every problem of every plugin is
returned together.
"""

from __future__ import annotations

import io
import threading
from datetime import date, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any

from fse.live.host.config import (
    FillsBlock,
    PluginEntry,
    RunnerConfig,
    RunnerConfigError,
    account_brokerage_problem,
    execution_brokerage_problem,
    parse_runner_config,
)
from fse.live.plugins.api import BrokerTerms, DataNeeds, PaperFills, PaperRisk, PluginServices
from fse.live.plugins.loader import (
    PluginInfo,
    PluginLoadError,
    discover_plugins,
    needs_problems,
    resolve_plugin_class,
)

from fse_ui.configs import parse_yaml

_lock = threading.Lock()
_cache: dict[str, tuple[list[PluginInfo], list[str]]] = {}


def discovered(project_dir: Path | None) -> tuple[list[PluginInfo], list[str]]:
    """Every plugin class fse can load (``discover_plugins``), cached per Project folder.

    No plugin is named here: a new ``research/<name>/plugin.py`` (or an fse plugin)
    shows up in the console with its form, checks and status on the next start.
    """
    key = str(project_dir)
    with _lock:
        found = _cache.get(key)
        if found is None:
            found = _cache[key] = discover_plugins(project_dir)
        return found


def _info(module: str, project_dir: Path | None) -> PluginInfo | None:
    infos, _ = discovered(project_dir)
    for info in infos:
        if info.module == module:
            return info
    # a module path the scan does not list (another export path): load it directly
    try:
        cls = resolve_plugin_class("x", module, project_dir=project_dir)
    except PluginLoadError:
        return None
    return PluginInfo(module=module, cls=cls)


def known_plugins(project_dir: Path | None) -> list[dict[str, Any]]:
    infos, _problems = discovered(project_dir)
    return [
        {
            "module": i.module,
            "label": i.label,
            "brokerage": i.cls.brokerage,
            "note": i.cls.description,
            "has_schema": i.cls.config_model is not None,
        }
        for i in infos
    ]


def config_schema(module: str, project_dir: Path | None) -> dict[str, Any] | None:
    info = _info(module, project_dir)
    if info is None or info.cls.config_model is None:
        return None
    return info.cls.config_model.model_json_schema()


def validate_plugin_config(
    module: str, config: dict[str, Any], project_dir: Path | None
) -> list[str]:
    """Problems with ``config`` against the plugin's own validation (empty if unknown)."""
    info = _info(module, project_dir)
    if info is None:
        return []
    return list(info.cls.validate_config(config))


# ---------------------------------------------------------------- the check


class _Tasks:
    def run_blocking(self, name: str, fn: Any, *args: object) -> Any:
        raise RuntimeError("background tasks do not run during a plugin check")

    def spawn(self, name: str, coro: Any) -> Any:
        coro.close()
        raise RuntimeError("background tasks do not run during a plugin check")


class _NullStore:
    @property
    def root(self) -> Path:
        return Path()


_calendar_lock = threading.Lock()


def _services(
    entry: PluginEntry, cfg: RunnerConfig, project_dir: Path, scratch: Path
) -> PluginServices:
    from fse.calendars import load_calendars, project_calendar_dir
    from fse.clock import SystemClock
    from fse.logio.log_writer import LogWriter
    from fse.secrets.env import EnvView

    sky = cfg.data_plane.skylit
    calendar_dir = cfg.paths.calendar_dir or project_calendar_dir(project_dir)
    cached: list[Any] = []

    def calendars() -> Any:
        with _calendar_lock:
            if not cached:
                today = date.today()
                cached.append(load_calendars(calendar_dir, today, today + timedelta(days=7)))
            return cached[0]

    fills: PaperFills | None = None
    risk: PaperRisk | None = None
    terms: BrokerTerms | None = None
    if entry.risk is not None:
        block = entry.fills if entry.fills is not None else FillsBlock()
        fills = PaperFills(
            trade_through_ticks=block.trade_through_ticks,
            slippage_ticks=block.slippage_ticks,
            stop_fill=block.stop_fill,
            commission_rt=MappingProxyType({str(k): v for k, v in block.commission_rt.items()}),
        )
        caps = entry.risk
        risk = PaperRisk(
            caps.max_contracts, caps.max_open_trades, caps.max_trades_per_day, caps.max_daily_loss
        )
        terms = BrokerTerms(
            fills.trade_through_ticks,
            fills.slippage_ticks,
            fills.stop_fill,
            fills.commission_rt,
            risk.max_contracts,
            risk.max_open_trades,
            risk.max_trades_per_day,
            risk.max_daily_loss,
        )
    env = EnvView({}, {})
    return PluginServices(
        tasks=_Tasks(),
        calendars=calendars,
        writer=LogWriter.from_env(env, stdout=io.StringIO(), stderr=io.StringIO()),
        clock=SystemClock(),
        calendar_dir=calendar_dir,
        state_dir=scratch / "state" / entry.id,
        out_dir=scratch / "out" / entry.id,
        project_dir=project_dir,
        bar_store=_NullStore(),  # type: ignore[arg-type]
        cache_dir=cfg.paths.cache_dir or scratch / "cache",
        env=env,
        skylit_levels=None,
        skylit_window=sky.window.pair() if sky.enabled else None,
        fills=fills,
        recordings_root=None,
        risk=risk,
        broker_terms=terms,
    )


def check_plugins(text: str, *, project_dir: Path, scratch: Path) -> dict[str, Any]:
    """Every problem ``fse run`` would report at start for the runner YAML ``text``."""
    parsed = parse_yaml(text)
    if parsed.problems:
        return {"ok": False, "problems": [p.message for p in parsed.problems], "plugins": []}
    try:
        cfg = parse_runner_config(parsed.data, Path("runner.yaml"))
    except RunnerConfigError as exc:
        return {"ok": False, "problems": list(exc.problems), "plugins": []}
    problems: list[str] = []
    report: list[dict[str, Any]] = []
    for entry in cfg.plugins:
        item: dict[str, Any] = {
            "id": entry.id,
            "module": entry.module,
            "enabled": entry.enabled,
            "brokerage": None,
            "problems": [],
        }
        report.append(item)
        own = validate_plugin_config(entry.module, dict(entry.config), project_dir)
        if not entry.enabled:
            # Disabled entries are not loaded by fse run; only their config is checked.
            item["problems"] = [f"plugin {entry.id!r}: {p}" for p in own]
            continue
        try:
            cls = resolve_plugin_class(entry.id, entry.module, project_dir=project_dir)
        except PluginLoadError as exc:
            item["problems"].extend(exc.problems)
            continue
        try:
            plugin = cls(entry.id, dict(entry.config), _services(entry, cfg, project_dir, scratch))
            needs = plugin.needs()
        except Exception as exc:  # what the host reports as "cannot start"
            item["problems"].append(
                f"plugin {entry.id!r}: cannot start: {type(exc).__name__}: {exc}"
            )
            continue
        item["brokerage"] = getattr(needs, "brokerage", None)
        item["problems"].extend(needs_problems(entry, needs, cfg.data_plane))
        if isinstance(item["brokerage"], str):
            brokerage = account_brokerage_problem(cfg, entry, item["brokerage"])
            if brokerage is not None:
                item["problems"].append(brokerage)
        if isinstance(needs, DataNeeds):  # as fse run checks it (host.py)
            execution = execution_brokerage_problem(
                cfg,
                entry,
                brokerage=needs.brokerage,
                broker_orders=needs.broker_orders,
                instruments=needs.instruments,
            )
            if execution is not None:
                item["problems"].append(execution)
    for item in report:
        if item["enabled"]:  # fse run never loads a disabled entry
            problems.extend(item["problems"])
    return {"ok": not problems, "problems": problems, "plugins": report}


# ---------------------------------------------------------------- backtest coverage


def plugin_coverage(
    text: str,
    plugin_ids: list[str] | None,
    first: date,
    last: date,
    *,
    project_dir: Path,
    cache_dir: Path,
    recordings_root: Path,
    scratch: Path,
    recordings_s3: str | None = None,
) -> dict[str, Any]:
    """Which inputs ``fse backtest-plugin`` would find for each day (no replay is run).

    The plugins are constructed as in :func:`check_plugins` (no host, no network), their
    ``needs()`` merged, and every Globex day checked against the Data_Cache and the
    data-plane recordings, exactly as the replay does before it starts. With
    ``recordings_s3`` (``s3://bucket[/prefix]``), a day missing locally is looked up
    there (listed only, never downloaded) and reported ``recording (s3)``.
    """
    from fse.calendars import load_calendars, project_calendar_dir
    from fse.data.cache import DataCache
    from fse.live.host.recordings_s3 import S3Recordings
    from fse.live.host.replay import DEFAULT_LAG_S, coverage, globex_days, replay_needs

    parsed = parse_yaml(text)
    if parsed.problems:
        return {"ok": False, "problems": [p.message for p in parsed.problems]}
    try:
        cfg = parse_runner_config(parsed.data, Path("runner.yaml"))
    except RunnerConfigError as exc:
        return {"ok": False, "problems": list(exc.problems)}
    enabled = [e for e in cfg.plugins if e.enabled]
    chosen = [e for e in enabled if plugin_ids is None or e.id in plugin_ids]
    unknown = sorted(set(plugin_ids or []) - {e.id for e in enabled})
    if unknown or not chosen:
        return {"ok": False, "problems": [f"not an enabled plugin: {', '.join(unknown) or '-'}"]}
    needs = {}
    problems: list[str] = []
    for entry in chosen:
        try:
            cls = resolve_plugin_class(entry.id, entry.module, project_dir=project_dir)
            plugin = cls(entry.id, dict(entry.config), _services(entry, cfg, project_dir, scratch))
            needs[entry.id] = plugin.needs()
        except PluginLoadError as exc:
            problems.extend(exc.problems)
        except Exception as exc:
            problems.append(f"plugin {entry.id!r}: cannot start: {type(exc).__name__}: {exc}")
    if problems:
        return {"ok": False, "problems": problems}
    calendar_dir = cfg.paths.calendar_dir or project_calendar_dir(project_dir)
    with _calendar_lock:
        cals = load_calendars(calendar_dir, first - timedelta(days=7), last + timedelta(days=7))
    days = globex_days(first, last, cals)
    if not days:
        return {"ok": False, "problems": [f"no exchange session between {first} and {last}"]}
    if not (cache_dir / "catalog.sqlite").is_file():
        return {"ok": False, "problems": [f"no Data_Cache at {cache_dir}"]}
    try:
        s3 = None if recordings_s3 is None else S3Recordings(recordings_s3)
    except Exception as exc:  # a bad URI, or boto3 cannot start
        return {"ok": False, "problems": [f"recordings in S3: {type(exc).__name__}: {exc}"]}
    with DataCache(cache_dir) as cache:
        cov, _ = coverage(
            replay_needs(cfg, needs),
            days,
            cache=cache,
            recordings_root=recordings_root,
            lag_ns=DEFAULT_LAG_S * 1_000_000_000,
            s3=s3,
        )
    return {"ok": True, "problems": [], "plugins": [e.id for e in chosen], **cov.to_json()}
