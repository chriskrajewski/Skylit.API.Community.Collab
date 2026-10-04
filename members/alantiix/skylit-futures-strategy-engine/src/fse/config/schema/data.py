"""The ``data`` section of the Strategy_Config (design "Strategy_Config shape").

Which Skylit data the run reads and how it maps to futures:

- ``symbols``: the configured symbols; Map_State holds one entry per symbol and
  metric (Req 5.1).
- ``heatmap_view``: the Heatmap_View every Snapshot is fetched and selected with
  (Glossary; Skylit defaults 92 strikes, 5 expirations, ``include_empty`` false).
- ``regime_symbol``: the Regime_Symbol (Req 7).
- ``es_source_symbol`` / ``nq_source_symbol``: the source symbols of ES and NQ
  levels (Req 10.5).
- ``nq_sources``: the NQ_Sources (Glossary, Req 11.9).
- ``instruments``: the instrument traded for ES and NQ levels (Req 10.6).
- ``velocity_window_s``: the Node_Velocity window (Req 5.9).

Every symbol the section names must be one of ``symbols``, and
``nq_source_symbol`` must be one of ``nq_sources``.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import Field, PlainValidator, StringConstraints, ValidationInfo, field_validator
from pydantic_core import PydanticCustomError

from fse.config.schema._base import SchemaModel, YamlList, not_allowed_error, require_unique

__all__ = [
    "DEFAULT_MAX_EXPIRATIONS",
    "DEFAULT_MAX_STRIKES",
    "DEFAULT_NQ_SOURCES",
    "DEFAULT_SYMBOLS",
    "MAX_EXPIRATIONS_CAP",
    "MAX_STRIKES_CAP",
    "VELOCITY_WINDOW_MAX_S",
    "DataConfig",
    "EsInstrument",
    "HeatmapViewConfig",
    "InstrumentsConfig",
    "NqInstrument",
    "Symbol",
    "ViewLimit",
]

type Symbol = Literal["SPX", "SPY", "QQQ", "NDX", "NDXP"]
"""The symbols the Level_Converter knows (Req 8.1-8.3)."""

type EsInstrument = Literal["ES", "MES"]
type NqInstrument = Literal["NQ", "MNQ"]

DEFAULT_SYMBOLS: Final[tuple[Symbol, ...]] = ("SPX", "SPY", "QQQ", "NDX", "NDXP")
DEFAULT_NQ_SOURCES: Final[tuple[Symbol, ...]] = ("QQQ", "NDX", "NDXP")

# Skylit limits (fse.skylit.endpoints) and defaults (fse.data.cache), repeated here
# because the engine may import this package but not the adapters; a test keeps them equal.
MAX_STRIKES_CAP: Final = 1000
MAX_EXPIRATIONS_CAP: Final = 60
DEFAULT_MAX_STRIKES: Final = 92
DEFAULT_MAX_EXPIRATIONS: Final = 5

VELOCITY_WINDOW_MAX_S: Final = 3600


def _view_limit(name: str, cap: int) -> PlainValidator:
    """One error per bad value: a whole number from 1 to ``cap``, or ``"all"``."""

    def check(value: object) -> int | Literal["all"]:
        if value == "all":
            return "all"
        if not isinstance(value, int) or isinstance(value, bool):
            raise PydanticCustomError(
                "int_type",
                "{name} should be a whole number from 1 to {le} or 'all'",
                {"name": name, "le": cap},
            )
        if value < 1:
            raise PydanticCustomError(
                "greater_than_equal", "{name} should be at least 1", {"name": name, "ge": 1}
            )
        if value > cap:
            raise PydanticCustomError(
                "less_than_equal", "{name} should be at most {le}", {"name": name, "le": cap}
            )
        return value

    return PlainValidator(check)


type ViewLimit = int | Literal["all"]

type IsoDate = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")]


class HeatmapViewConfig(SchemaModel):
    """The Heatmap_View; ``expirations``, when set, supersedes ``max_expirations``."""

    max_strikes: Annotated[ViewLimit, _view_limit("max_strikes", MAX_STRIKES_CAP)] = (
        DEFAULT_MAX_STRIKES
    )
    max_expirations: Annotated[ViewLimit, _view_limit("max_expirations", MAX_EXPIRATIONS_CAP)] = (
        DEFAULT_MAX_EXPIRATIONS
    )
    expirations: Annotated[YamlList[IsoDate], Field(min_length=1)] | None = None
    include_empty: bool = False

    @field_validator("expirations")
    @classmethod
    def _unique_expirations(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        return None if value is None else require_unique(value)


class InstrumentsConfig(SchemaModel):
    """The instrument traded for ES levels and for NQ levels (default MES and MNQ)."""

    es_levels: EsInstrument = "MES"
    nq_levels: NqInstrument = "MNQ"


class DataConfig(SchemaModel):
    """Symbols, Heatmap_View, source symbols, instruments and the velocity window."""

    symbols: Annotated[YamlList[Symbol], Field(min_length=1)] = DEFAULT_SYMBOLS
    heatmap_view: HeatmapViewConfig = HeatmapViewConfig()
    regime_symbol: Symbol = Field("SPX", validate_default=True)
    es_source_symbol: Symbol = Field("SPX", validate_default=True)
    nq_source_symbol: Symbol = Field("QQQ", validate_default=True)
    nq_sources: Annotated[YamlList[Symbol], Field(min_length=1)] = Field(
        DEFAULT_NQ_SOURCES, validate_default=True
    )
    instruments: InstrumentsConfig = InstrumentsConfig()
    velocity_window_s: int = Field(60, ge=1, le=VELOCITY_WINDOW_MAX_S)

    @field_validator("symbols")
    @classmethod
    def _unique_symbols(cls, value: tuple[Symbol, ...]) -> tuple[Symbol, ...]:
        return require_unique(value)

    @field_validator("regime_symbol", "es_source_symbol", "nq_source_symbol")
    @classmethod
    def _configured_symbol(cls, value: Symbol, info: ValidationInfo) -> Symbol:
        symbols: tuple[Symbol, ...] | None = info.data.get("symbols")
        if symbols is not None and value not in symbols:
            raise not_allowed_error(value, symbols, "data.symbols")
        return value

    @field_validator("nq_sources")
    @classmethod
    def _configured_nq_sources(
        cls, value: tuple[Symbol, ...], info: ValidationInfo
    ) -> tuple[Symbol, ...]:
        require_unique(value)
        symbols: tuple[Symbol, ...] | None = info.data.get("symbols")
        if symbols is not None:
            for item in value:
                if item not in symbols:
                    raise not_allowed_error(item, symbols, "data.symbols")
        source: Symbol | None = info.data.get("nq_source_symbol")
        if source is not None and source not in value:
            raise PydanticCustomError(
                "literal_error",
                "nq_sources should include nq_source_symbol {expected}",
                {"expected": repr(source)},
            )
        return value
