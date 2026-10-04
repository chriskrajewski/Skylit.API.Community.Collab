"""The Config_Schema: the root Strategy_Config model (design §17, "Strategy_Config shape").

:class:`StrategyConfig` assembles every section model, in the design's YAML
key order, which is also the Config_Printer's output order (Req 17.7):

``schema_version``, ``config_id``, ``order_mode``, ``time``, ``data``,
``nodes``, ``regime``, ``levels``, ``chart``, ``patterns``, ``gates``,
``exits``, ``orders``, ``fills``, ``sizing``, ``kill_switches``,
``account``, ``live``, ``notify``, ``reporting``, ``experiments``.

- ``schema_version``: 1, the only version this code reads.
- ``config_id``: a name for the config, shown in reports and manifests
  (default ``unnamed``; letters, digits, ``_``, ``.`` and ``-``, at most 64).
- ``order_mode``: ``paper`` (default), ``practice`` or ``combine``
  (Req 17.2, 23.1). Combine still needs the other Combine_Opt_In conditions
  at start (Req 24.1).

Every key has a default except the two the requirements leave to the
Operator: ``regime.min_abs_value`` (Req 7.4) and the commission and exchange
fee of each traded instrument (``fills.costs``, Req 13.8). An omitted
``regime`` section counts as ``{}``, so the error names
``regime.min_abs_value``. Whether every traded instrument has costs is a
cross-field check of the Config_Loader (``fse.config.loader``).

Like the section modules, this package does no I/O and reads no clock or
environment: the engine imports it.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import StringConstraints, model_validator

from fse.config.schema._base import SchemaModel
from fse.config.schema.account import AccountConfig
from fse.config.schema.chart import ChartConfig
from fse.config.schema.data import DataConfig
from fse.config.schema.exits import ExitsConfig
from fse.config.schema.experiments import ExperimentsConfig
from fse.config.schema.fills import FillsConfig
from fse.config.schema.gates import GatesConfig
from fse.config.schema.kill_switches import KillSwitchesConfig
from fse.config.schema.levels import LevelsConfig
from fse.config.schema.live import LiveConfig
from fse.config.schema.nodes import NodesConfig
from fse.config.schema.notify import NotifyConfig
from fse.config.schema.orders import OrdersConfig
from fse.config.schema.patterns import PatternsConfig
from fse.config.schema.regime import RegimeConfig
from fse.config.schema.reporting import ReportingConfig
from fse.config.schema.sizing import SizingConfig
from fse.config.schema.time import TimeConfig

__all__ = [
    "CONFIG_ID_DEFAULT",
    "ORDER_MODES",
    "SCHEMA_VERSION",
    "SECTION_KEYS",
    "ConfigId",
    "OrderMode",
    "StrategyConfig",
]

type OrderMode = Literal["paper", "practice", "combine"]
"""Paper (the in-process Paper_Broker, the default), Practice or Combine (Glossary)."""

ORDER_MODES: Final[tuple[str, ...]] = ("paper", "practice", "combine")

SCHEMA_VERSION: Final = 1
CONFIG_ID_DEFAULT: Final = "unnamed"

type ConfigId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")]

SECTION_KEYS: Final[tuple[str, ...]] = (
    "time",
    "data",
    "nodes",
    "regime",
    "levels",
    "chart",
    "patterns",
    "gates",
    "exits",
    "orders",
    "fills",
    "sizing",
    "kill_switches",
    "account",
    "live",
    "notify",
    "reporting",
    "experiments",
)
"""The section keys, in schema order, after ``schema_version``, ``config_id`` and ``order_mode``."""


class StrategyConfig(SchemaModel):
    """Every rule and parameter of one strategy (Req 17.1): strict, closed and frozen."""

    schema_version: Literal[1] = SCHEMA_VERSION
    config_id: ConfigId = CONFIG_ID_DEFAULT
    order_mode: OrderMode = "paper"
    time: TimeConfig = TimeConfig()
    data: DataConfig = DataConfig()
    nodes: NodesConfig = NodesConfig()
    regime: RegimeConfig
    levels: LevelsConfig = LevelsConfig()
    chart: ChartConfig = ChartConfig()
    patterns: PatternsConfig = PatternsConfig()
    gates: GatesConfig = GatesConfig()
    exits: ExitsConfig = ExitsConfig()
    orders: OrdersConfig = OrdersConfig()
    fills: FillsConfig = FillsConfig()
    sizing: SizingConfig = SizingConfig()
    kill_switches: KillSwitchesConfig = KillSwitchesConfig()
    account: AccountConfig = AccountConfig()
    live: LiveConfig = LiveConfig()
    notify: NotifyConfig = NotifyConfig()
    reporting: ReportingConfig = ReportingConfig()
    experiments: ExperimentsConfig = ExperimentsConfig()

    @model_validator(mode="before")
    @classmethod
    def _omitted_regime_is_empty(cls, value: object) -> object:
        # ``regime`` has a required key; reporting ``regime.min_abs_value`` missing
        # is more useful than ``regime`` missing.
        if isinstance(value, dict) and "regime" not in value:
            return {**value, "regime": {}}
        return value

    def traded_instruments(self) -> tuple[str, str]:
        """The instruments traded for ES levels and for NQ levels (``data.instruments``)."""
        return (self.data.instruments.es_levels, self.data.instruments.nq_levels)
