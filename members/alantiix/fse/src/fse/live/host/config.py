"""The runner YAML of ``fse run`` (design 11.1): :class:`RunnerConfig` and its loader.

Every model is strict, closed (``extra="forbid"``) and frozen. Invalid YAML,
unknown keys, wrong types, out-of-range values and the cross-field rules
(window order, ``hot_poll_interval_s < poll_interval_s``, unique plugin ids,
at least one enabled plugin) are reported together as one
:class:`RunnerConfigError` listing every problem with its YAML path (exit 2),
before any network request. A plugin's ``config`` block is opaque here; the
plugin validates it. Rules that need the plugin's ``needs()`` (``fills``/``risk``
on a self-brokered plugin, ``risk`` required for a host-brokered one) are
checked by the loader (:mod:`fse.live.plugins.loader`).

Paper accounts (``accounts:``, ``plugins[].account``) are optional. Their
cross-references (unknown or duplicate ids, an account on a self-brokered
entry, the Nexus netting rules) are checked by :func:`account_problems` once
every field is valid, so field errors are reported first.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from datetime import time
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, ClassVar, Final, Literal

import yaml
from pydantic import (
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from fse.config.loader import key_path
from fse.config.schema._base import ClockTime, SchemaModel, YamlList, require_unique
from fse.config.schema.account import PaperAccountRules
from fse.config.schema.data import HeatmapViewConfig
from fse.config.schema.fills import FILL_TICKS_MAX, CostsConfig, FillInstrument, FillsConfig
from fse.config.schema.fills import InstrumentCosts as _InstrumentCosts
from fse.config.schema.live import LiveConfig
from fse.config.schema.sizing import YamlMoney
from fse.live.host.accounts_config import PRESETS, PresetName
from fse.sim.fills import TICK_VALUE_USD, StopFill

__all__ = [
    "ACCOUNTS_MAX",
    "ACCOUNT_ID_PATTERN",
    "COMMISSION_RT_MAX",
    "IMPLICIT_ACCOUNT_PREFIX",
    "NEXUS_ACCOUNT_PATTERN",
    "PLUGIN_ID_PATTERN",
    "AccountEntry",
    "DarkPoolConfig",
    "DataPlaneConfig",
    "ExecutionBlock",
    "FillsBlock",
    "NexusBlock",
    "PathsConfig",
    "PluginEntry",
    "PluginOrderMode",
    "ProjectXConfig",
    "RiskBlock",
    "RunnerConfig",
    "RunnerConfigError",
    "SkylitConfig",
    "StreamSymbol",
    "WindowConfig",
    "account_brokerage_problem",
    "account_problems",
    "execution_brokerage_problem",
    "execution_problems",
    "load_runner_config",
    "parse_runner_config",
]

PLUGIN_ID_PATTERN: Final = r"^[a-z][a-z0-9_]{0,31}$"
ACCOUNT_ID_PATTERN: Final = PLUGIN_ID_PATTERN
IMPLICIT_ACCOUNT_PREFIX: Final = "implicit:"
"""Prefix of a plugin's implicit account id (``implicit:<plugin id>``); never valid in YAML."""
NEXUS_ACCOUNT_PATTERN: Final = r"^(practice|evaluation|funded|agent|env:[A-Z][A-Z0-9_]{0,63})$"
ACCOUNTS_MAX: Final = 8
COMMISSION_RT_MAX: Final = Decimal("50.00")
EXIT_INVALID_INPUT: Final = 2


def _to_path(value: object) -> object:
    if isinstance(value, str):
        if not value.strip():
            raise ValueError("a path must not be blank")
        return Path(value).expanduser()
    return value


type YamlPath = Annotated[Path, BeforeValidator(_to_path)]
"""A path string in YAML (``~`` expanded), a ``Path`` in the model."""


class PathsConfig(SchemaModel):
    """Output and input roots; every one optional (CLI flags override)."""

    state_dir: YamlPath | None = None
    recordings_root: YamlPath | None = None
    out_root: YamlPath | None = None
    bar_store: YamlPath | None = None
    cache_dir: YamlPath | None = None
    calendar_dir: YamlPath | None = None


class ProjectXConfig(SchemaModel):
    """The ProjectX market data transport (design 4.2-4.4)."""

    model_config = ConfigDict(use_attribute_docstrings=True)
    transport: Literal["auto", "stream", "polling"] = "auto"
    """How bars arrive: ``stream`` (real-time hub), ``polling`` (REST), or ``auto``
    (stream, falling back to polling)."""
    subscription: Literal["auto", "live", "sim"] = "auto"
    """The ProjectX data subscription: ``live``, ``sim`` (simulated accounts, real-time
    bars), or ``auto`` (detected at start)."""
    poll_interval_s: int = Field(5, ge=5, le=60)
    """Seconds between bar polls while polling."""
    hot_poll_interval_s: int = Field(1, ge=1, le=5)
    """Faster poll interval around bars a plugin needs as early as possible."""
    rtc_url: str = "https://rtc.topstepx.com/hubs/market"
    """The ProjectX real-time market hub URL."""
    record_quotes: Literal["off", "sampled_1s", "all"] = "sampled_1s"
    """Quote recording: ``off``, one per second (``sampled_1s``), or every quote."""
    backfill_sessions: int = Field(20, ge=0, le=60)
    """Past sessions of 1m bars fetched at start to fill the bar store."""
    expected_contracts: dict[str, str] = Field(default_factory=dict)
    """Instrument -> ProjectX contract id to use when the contract lookup fails."""

    @field_validator("rtc_url")
    @classmethod
    def _rtc_scheme(cls, value: str) -> str:
        if not value.startswith(("https://", "wss://")):
            raise ValueError("rtc_url must start with https:// or wss://")
        return value

    @model_validator(mode="after")
    def _hot_below_poll(self) -> ProjectXConfig:
        if self.hot_poll_interval_s >= self.poll_interval_s:
            raise ValueError(
                f"hot_poll_interval_s ({self.hot_poll_interval_s}) must be below "
                f"poll_interval_s ({self.poll_interval_s})"
            )
        return self


class WindowConfig(SchemaModel):
    """A NY wall-time window on one day; ``start < end``."""

    model_config = ConfigDict(use_attribute_docstrings=True)
    start: ClockTime = time(9, 0)
    """Start, New York time (HH:MM)."""
    end: ClockTime = time(16, 15)
    """End, New York time (HH:MM)."""

    @model_validator(mode="after")
    def _ordered(self) -> WindowConfig:
        if not self.start < self.end:
            raise ValueError(f"window start {self.start:%H:%M} must be before end {self.end:%H:%M}")
        return self

    def pair(self) -> tuple[time, time]:
        return self.start, self.end


class DarkPoolConfig(SchemaModel):
    """Dark-pool prints (Skylit)."""

    model_config = ConfigDict(use_attribute_docstrings=True)
    enabled: bool = False
    """Fetch dark-pool prints (costs extra Skylit credits)."""
    tickers: YamlList[Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9.]{0,9}$")]] = ()
    """Stock tickers whose dark-pool prints are fetched, e.g. ``SPY``."""

    @field_validator("tickers")
    @classmethod
    def _unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique(value)


type StreamSymbol = Literal["SPX", "SPXW", "SPY", "QQQ", "NDX", "NDXP"]
"""Symbols the data plane may stream: the Level_Converter's symbols plus SPXW, the
PM-settled S&P 500 root with every daily expiry (0DTE). The data plane only subscribes;
a plugin that converts SPXW levels does so itself (FSE's converter has no SPXW family)."""


class SkylitConfig(SchemaModel):
    """The Skylit plane: map stream or polling, and optionally dark-pool prints."""

    model_config = ConfigDict(use_attribute_docstrings=True)
    enabled: bool = True
    """Fetch Skylit option-exposure maps at all (off: no Skylit credits are used)."""
    mode: Literal["stream", "polling"] = "stream"
    """``stream`` (pushed updates) or ``polling`` (one request per refresh)."""
    refresh_interval_s: int = Field(15, ge=5, le=3600)
    """Seconds between map refreshes; credits scale with refreshes."""
    symbols: Annotated[YamlList[StreamSymbol], Field(min_length=1, max_length=10)] = (
        "QQQ",
        "SPY",
    )
    """Option symbols to fetch maps for (each adds credits per day)."""
    heatmap_view: HeatmapViewConfig = HeatmapViewConfig()
    """How much of each map: strikes, expirations, empty strikes."""
    layout: Literal["net", "matrix"] = "net"
    """``net`` (one total per strike) or ``matrix`` (a per-expiration grid as well)."""
    window: WindowConfig = WindowConfig()
    """New York hours when maps are fetched; nothing is fetched outside them."""
    dark_pool: DarkPoolConfig = DarkPoolConfig()
    """Optional dark-pool prints."""

    @field_validator("symbols")
    @classmethod
    def _unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique(value)


def _known_instrument(value: str) -> str:
    if value not in TICK_VALUE_USD:
        raise ValueError(
            f"{value!r} is not an instrument with a tick value: {sorted(TICK_VALUE_USD)}"
        )
    return value


class DataPlaneConfig(SchemaModel):
    """The always-on data plane (design 4)."""

    model_config = ConfigDict(use_attribute_docstrings=True)
    instruments: Annotated[
        YamlList[Annotated[str, BeforeValidator(_known_instrument)]],
        Field(min_length=1, max_length=4),
    ] = ("MNQ", "MES")
    """Futures whose 1m bars are kept up (every plugin's bars must be listed)."""
    projectx: ProjectXConfig = ProjectXConfig()
    """ProjectX market data (bars and quotes)."""
    skylit: SkylitConfig = SkylitConfig()
    """Skylit option-exposure maps."""

    @field_validator("instruments")
    @classmethod
    def _unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique(value)


def _commission(value: Decimal) -> Decimal:
    if not Decimal("0.00") <= value <= COMMISSION_RT_MAX:
        raise ValueError(f"commission_rt {value} must be from 0.00 to {COMMISSION_RT_MAX}")
    cents = value * 100
    if cents != cents.to_integral_value() or int(cents) % 2:
        raise ValueError(f"commission_rt {value} must be a whole even number of cents")
    return value


class FillsBlock(SchemaModel):
    """A host-brokered plugin's paper fill rules (design 7.4)."""

    model_config = ConfigDict(use_attribute_docstrings=True)
    trade_through_ticks: int = Field(1, ge=0, le=FILL_TICKS_MAX)
    """Ticks the market must trade past a limit (entry or target) before it fills; 0
    fills on a touch."""
    slippage_ticks: int = Field(0, ge=0, le=FILL_TICKS_MAX)
    """Ticks of slippage against you on market and stop fills."""
    stop_fill: StopFill = "gap_open"
    """Where a stop fills: ``at_stop`` (its price) or ``gap_open`` (the bar open when the
    bar opens past the stop)."""
    commission_rt: dict[FillInstrument, YamlMoney] = Field(default_factory=dict)
    """Round-turn commission per contract, per instrument, e.g. ``MNQ: "1.24"``."""

    @field_validator("commission_rt")
    @classmethod
    def _rt(cls, value: dict[FillInstrument, Decimal]) -> dict[FillInstrument, Decimal]:
        for amount in value.values():
            _commission(amount)
        return value

    def fills_config(self) -> FillsConfig:
        """The ``FillsConfig``: commission = rt / 2 per side, no exchange fee."""
        costs = {
            instrument: _InstrumentCosts(commission=rt / 2, exchange_fee=Decimal("0.00"))
            for instrument, rt in self.commission_rt.items()
        }
        return FillsConfig(
            trade_through_ticks=self.trade_through_ticks,
            slippage_ticks=self.slippage_ticks,
            costs=CostsConfig(**costs),
        )


class RiskBlock(SchemaModel):
    """A host-brokered plugin's caps (design 7.4)."""

    model_config = ConfigDict(use_attribute_docstrings=True)
    max_contracts: int = Field(ge=1, le=100)
    """Most contracts open or working at once; larger entries are refused."""
    max_open_trades: int = Field(1, ge=1, le=10)
    """Most trades open at once."""
    max_trades_per_day: int | None = Field(None, ge=1, le=500)
    """Entries allowed per Globex day; empty for no cap."""
    max_daily_loss: Annotated[YamlMoney, Field(gt=0)] | None = None
    """Dollar loss on the day that blocks new entries; empty for no cap."""


type PluginOrderMode = Literal["paper", "practice", "combine"]


class ExecutionBlock(SchemaModel):
    """A host-brokered plugin's order execution (``plugins[].execution``)."""

    model_config = ConfigDict(use_attribute_docstrings=True)
    order_mode: PluginOrderMode = "paper"
    """paper (default): the in-process paper book only. practice: also send orders to
    the ProjectX account named by PRACTICE_ACCOUNT_ID. combine: the account named by
    COMBINE_ACCOUNT_ID, only with --live-orders. Both need the fse run flag
    --plugin-orders and FSE_RUN_PLUGIN_ORDERS=enabled."""


class PluginEntry(SchemaModel):
    """One ``plugins[]`` entry."""

    model_config = ConfigDict(use_attribute_docstrings=True)
    id: str = Field(pattern=PLUGIN_ID_PATTERN)
    """A unique name: lowercase letters, digits and ``_``; its state folder and logs."""
    module: str
    """The plugin class, ``package.module:Class``."""
    enabled: bool = True
    """Load it at the next runner start."""
    config: dict[str, Any] = Field(default_factory=dict)
    """The plugin's own settings (see its form)."""
    fills: FillsBlock | None = None
    """Paper fill rules (host-booked plugins only)."""
    risk: RiskBlock | None = None
    """Risk caps (host-booked plugins only; required for them)."""
    hold_through_break: bool = True
    """Keep positions open through the 17:00-18:00 ET break if the plugin allows it;
    off: the host closes them before the break."""
    """Effective value = this AND the plugin's ``needs().hold_through_break``."""
    cards: bool = True
    """Publish this plugin's decision cards to the Finding Cards page (the plugin log keeps
    them either way; trading is unaffected)."""
    account: str | None = Field(None, pattern=ACCOUNT_ID_PATTERN)
    """The paper account (an id under accounts) this plugin trades on, with its rules;
    empty: its own implicit, unrestricted account (host-booked plugins only)."""
    execution: ExecutionBlock = ExecutionBlock()
    """Order execution: paper unless the plugin supports broker orders and this opts in."""


class NexusBlock(SchemaModel):
    """An account's opt-in Skylit Nexus mirror (``accounts[].nexus``).

    ``account`` is a Nexus account type or ``env:<VAR>``, resolved at ``fse run``;
    never a literal account id. There is no ``instrument_map``: the engine ticker
    is sent as is.
    """

    enabled: bool = False
    account: Annotated[str, Field(pattern=NEXUS_ACCOUNT_PATTERN)] = "practice"
    test_only: bool = False


def _deep_merge(base: Mapping[str, object], over: Mapping[str, object]) -> dict[str, object]:
    """``over`` merged over ``base`` at leaf level; neither is mutated."""
    out: dict[str, object] = copy.deepcopy(dict(base))
    for key, value in over.items():
        below = out.get(key)
        if isinstance(below, Mapping) and isinstance(value, Mapping):
            out[key] = _deep_merge(below, value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class AccountEntry(SchemaModel):
    """One ``accounts[]`` entry: an id, a rule preset, overrides and the Nexus mirror."""

    id: str = Field(pattern=ACCOUNT_ID_PATTERN)
    preset: PresetName
    rules: PaperAccountRules
    """The preset with the YAML ``rules:`` deep-merged over it, validated."""
    nexus: NexusBlock = NexusBlock()

    @model_validator(mode="before")
    @classmethod
    def _merge_preset(cls, data: object) -> object:
        # Anything unexpected is left as is, so pydantic reports it at its own path.
        if not isinstance(data, Mapping):
            return data
        preset = data.get("preset")
        if not isinstance(preset, str) or preset not in PRESETS:
            return data
        rules = data.get("rules")
        if rules is None:
            # ``rules:`` with nothing under it is YAML for "no overrides".
            rules = {}
        if not isinstance(rules, Mapping):
            return data
        return {**data, "rules": _deep_merge(PRESETS[preset], rules)}


class RunnerConfig(SchemaModel):
    """The whole runner YAML."""

    VERSION: ClassVar[int] = 1

    version: Literal[1]
    paths: PathsConfig = PathsConfig()
    data_plane: DataPlaneConfig = DataPlaneConfig()
    plugins: Annotated[YamlList[PluginEntry], Field(min_length=1, max_length=8)]
    accounts: Annotated[YamlList[AccountEntry], Field(max_length=ACCOUNTS_MAX)] = ()

    @field_validator("plugins")
    @classmethod
    def _plugins(cls, value: tuple[PluginEntry, ...]) -> tuple[PluginEntry, ...]:
        require_unique(tuple(p.id for p in value))
        if not any(p.enabled for p in value):
            raise ValueError("no plugin is enabled")
        return value

    @property
    def enabled_plugins(self) -> tuple[PluginEntry, ...]:
        return tuple(p for p in self.plugins if p.enabled)


class RunnerConfigError(Exception):
    """An invalid runner YAML: every problem, each with its YAML path (exit 2)."""

    exit_code: ClassVar[int] = EXIT_INVALID_INPUT

    def __init__(self, path: Path, problems: list[str]) -> None:
        self.path = path
        self.problems = problems
        lines = "\n".join(f"  {p}" for p in problems)
        super().__init__(f"runner config {path}: {len(problems)} problem(s)\n{lines}")


def _problems(exc: ValidationError) -> list[str]:
    out: list[str] = []
    for err in exc.errors():
        path = key_path(tuple(err["loc"])) or "(top level)"
        msg = err["msg"].removeprefix("Value error, ")
        out.append(f"{path}: {msg}")
    return out


def account_problems(cfg: RunnerConfig) -> list[str]:
    """Cross-reference problems of accounts and plugins, each prefixed with its YAML path.

    Run only on a config whose fields validated, so field errors come first.
    """
    out: list[str] = []
    known: dict[str, int] = {}
    for j, account in enumerate(cfg.accounts):
        if account.id in known:
            out.append(f"accounts[{j}].id: duplicate account id {account.id!r}")
        else:
            known[account.id] = j
    for i, entry in enumerate(cfg.plugins):
        if entry.account is None:
            continue
        if entry.account not in known:
            out.append(
                f"plugins[{i}].account: unknown account {entry.account!r}; "
                f"accounts are {list(known)}"
            )
        if entry.risk is None:
            out.append(
                f"plugins[{i}].account: an account needs a host-brokered plugin (a risk block)"
            )
    targets: dict[str, str] = {}
    for j, account in enumerate(cfg.accounts):
        if not account.nexus.enabled or known[account.id] != j:
            continue
        members = [p.id for p in cfg.enabled_plugins if p.account == account.id]
        if len(members) > 1:
            out.append(
                f"accounts[{j}].nexus: mirrored account {account.id!r} has {len(members)} "
                f"enabled plugins ({', '.join(members)}); a mirrored account takes one"
            )
        target = account.nexus.account
        if target in targets:
            out.append(
                f"accounts[{j}].nexus.account: accounts {targets[target]!r} and "
                f"{account.id!r} both mirror to {target!r}"
            )
        else:
            targets[target] = account.id
    return out


def account_brokerage_problem(cfg: RunnerConfig, entry: PluginEntry, brokerage: str) -> str | None:
    """The ``plugins[i].account`` problem of a self-brokered plugin on an account, else ``None``.

    ``i`` is the entry's YAML index in ``cfg.plugins`` (disabled entries included).
    """
    if entry.account is None or brokerage == "host":
        return None
    i = next(k for k, e in enumerate(cfg.plugins) if e.id == entry.id)
    return (
        f"plugins[{i}].account: plugin {entry.id!r} is self-brokered; "
        'an account needs brokerage "host"'
    )


def execution_problems(cfg: RunnerConfig) -> list[str]:
    """The ``plugins[].execution`` cross-field problems (design D3), each with its YAML path.

    Every entry counts, enabled or not, so flipping ``enabled`` cannot add a
    second broker plugin. Run only on a config whose fields validated.
    """
    out: list[str] = []
    accounts = {a.id: a for a in cfg.accounts}
    brokered: list[str] = []
    for i, entry in enumerate(cfg.plugins):
        mode = entry.execution.order_mode
        if mode == "paper":
            continue
        brokered.append(entry.id)
        if entry.risk is None:
            out.append(
                f"plugins[{i}].execution.order_mode: order_mode {mode} needs a "
                "host-brokered plugin (a risk block)"
            )
        else:
            if entry.risk.max_daily_loss is None:
                out.append(
                    f"plugins[{i}].risk.max_daily_loss: required when execution.order_mode "
                    f"is {mode}"
                )
            if entry.risk.max_trades_per_day is None:
                out.append(
                    f"plugins[{i}].risk.max_trades_per_day: required when "
                    f"execution.order_mode is {mode}"
                )
        if entry.hold_through_break:
            out.append(
                f"plugins[{i}].hold_through_break: must be false when execution.order_mode "
                f"is {mode} (Topstep accounts are flat by the close)"
            )
        account = None if entry.account is None else accounts.get(entry.account)
        if mode == "combine" and (
            entry.account is None
            or (account is not None and account.rules.flat_deadline_enabled is not True)
        ):
            out.append(
                f"plugins[{i}].account: combine needs a paper account with the flat "
                "deadline on"
            )
    if len(brokered) > 1:
        out.append(
            "plugins: at most one plugin may have a non-paper execution.order_mode; "
            f"found {', '.join(brokered)}"
        )
    return out


def execution_brokerage_problem(
    cfg: RunnerConfig,
    entry: PluginEntry,
    *,
    brokerage: str,
    broker_orders: bool,
    instruments: frozenset[str],
) -> str | None:
    """The ``plugins[i].execution`` problem only the plugin's ``needs()`` reveals, else ``None``.

    A non-paper entry needs a host-brokered plugin that declares
    ``broker_orders`` and no instrument the broker path ignores.
    """
    mode = entry.execution.order_mode
    if mode == "paper":
        return None
    i = next(k for k, e in enumerate(cfg.plugins) if e.id == entry.id)
    where = f"plugins[{i}].execution.order_mode"
    if brokerage == "self":
        return (
            f"{where}: plugin {entry.id!r} is self-brokered; "
            f'order_mode {mode} needs brokerage "host"'
        )
    if not broker_orders:
        return (
            f"{where}: plugin {entry.id!r} does not support broker orders "
            "(needs().broker_orders is false)"
        )
    ignored = sorted(instruments & frozenset(LiveConfig().ignored_instruments))
    if ignored:
        return (
            f"{where}: plugin {entry.id!r} declares {', '.join(ignored)}, which the broker "
            f"path ignores (live.ignored_instruments); order_mode {mode} needs instruments "
            "the broker trades"
        )
    return None


def parse_runner_config(obj: object, path: Path) -> RunnerConfig:
    """Validate an already-parsed YAML document; :class:`RunnerConfigError` on any problem.

    Field errors are reported first; the account and execution cross-reference
    problems (:func:`account_problems`, :func:`execution_problems`) once every
    field is valid.
    """
    if not isinstance(obj, dict):
        raise RunnerConfigError(path, ["(top level): the runner config must be a mapping"])
    try:
        cfg = RunnerConfig.model_validate(obj)
    except ValidationError as exc:
        raise RunnerConfigError(path, _problems(exc)) from None
    problems = account_problems(cfg) + execution_problems(cfg)
    if problems:
        raise RunnerConfigError(path, problems)
    return cfg


def load_runner_config(path: Path) -> RunnerConfig:
    """Read and validate the runner YAML at ``path``."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RunnerConfigError(path, [f"(file): cannot read it ({type(exc).__name__})"]) from None
    try:
        obj = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RunnerConfigError(path, [f"(file): invalid YAML: {exc}"]) from None
    return parse_runner_config(obj, path)
