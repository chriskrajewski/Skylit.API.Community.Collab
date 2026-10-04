"""EngineState: the frozen state tree ``Engine.step`` reads and returns (design "Engine state").

:class:`EngineState` holds everything one Decision_Time hands to the next:

- ``taps``: the Tap tracker (:class:`~fse.engine.taps.TapState`);
- ``lifecycle``: lifecycle maxima, deliveries and Sloppy_Seconds labels
  (:class:`~fse.engine.lifecycle.LifecycleState`);
- ``chart``: swings, BOS_Legs and session windows
  (:class:`~fse.engine.chart.ChartState`, with its ``chart`` config);
- ``book``: resting entries, open positions, every Setup_Key placed this
  session and the client-id sequence (:class:`~fse.engine.planner.OrderBook`);
- ``risk``: session counters, the Loss_Streak and the Lockouts
  (:class:`~fse.engine.risk.RiskState`);
- ``trade_totals``: the filled trades of the latest sessions, summed, for the
  big-win sizing rule (Req 14.5);
- ``cards``: what the next Finding_Card triggers compare against (Req 25.6, 25.8);
- ``t`` and ``session``: the latest Decision_Time evaluated and its session.

Only ``Engine.step`` and the bar-phase hooks in :mod:`fse.engine.step` change it,
from the ordered inputs and config, so a restored state continues exactly as
an uninterrupted run (Req 16.7).

**Serialization.** :meth:`EngineState.to_jsonable` gives plain JSON values
(``dict``, ``list``, ``str``, ``int``, finite ``float``, ``bool``, ``None``) and
:meth:`EngineState.from_jsonable` rebuilds an equal state. Encoded with sorted
keys and ``separators=(",", ":")`` (:meth:`EngineState.to_canonical_json`, the
same bytes as ``fse.logio.canonical_json.dumps``) and parsed back, the state is
equal field by field, and re-encoding gives the same text. Each value maps to
one JSON shape:

- ``None``, ``bool``, ``int`` (ns Instants included, exact at any size), ``str``
  and finite ``float`` (``repr`` round-trips exactly) are themselves;
- a ``tuple`` is an array;
- ``{"$float": "inf"}`` or ``"-inf"``; ``{"$decimal": "1.50"}`` (trailing zeros
  kept); ``{"$fraction": "1/3"}``; ``{"$date": "2026-03-05"}``;
- ``{"$frozenset": [...]}`` with items in canonical-text order;
  ``{"$map": [[key, value], ...]}`` in insertion order;
- ``{"$model": "ChartConfig", "data": ...}`` for the chart config;
- ``{"$type": "<module>.<Class>", <field>: ...}`` for an engine dataclass, by
  its init fields; fields computed in ``__post_init__`` are rebuilt.

Only the engine dataclasses of the state modules can be decoded, so a state
file cannot construct anything else. NaN, lists, ``datetime`` and any other
type are refused when encoding. A text that does not decode raises
:class:`StateDecodeError`; the Live_Runner then blocks entries (Req 16.10).
"""

from __future__ import annotations

import dataclasses
import json
import math
import numbers
import operator
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from types import MappingProxyType, ModuleType
from typing import Final

from fse.config.schema.chart import ChartConfig
from fse.engine import chart as chart_module
from fse.engine import lifecycle as lifecycle_module
from fse.engine import planner as planner_module
from fse.engine import risk as risk_module
from fse.engine import sizing as sizing_module
from fse.engine import taps as taps_module
from fse.engine import targets as targets_module
from fse.engine import types as types_module
from fse.engine.chart import ChartState
from fse.engine.gates.registry import Grade
from fse.engine.lifecycle import LifecycleState
from fse.engine.planner import OrderBook
from fse.engine.risk import RiskState
from fse.engine.sizing import PriorSession
from fse.engine.taps import TapState
from fse.engine.types import SetupKey, Trade
from fse.pit.protocols import SymMetric
from fse.timekit import Instant

__all__ = [
    "STATE_FORMAT",
    "STATE_VERSION",
    "TRADE_SESSIONS_KEPT",
    "CardMemory",
    "EngineState",
    "JsonValue",
    "SessionTrades",
    "StateDecodeError",
    "decode_value",
    "encode_value",
]

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
"""A plain JSON value, as ``json.loads`` returns it."""

STATE_FORMAT: Final = "fse.engine_state"
STATE_VERSION: Final = 1
TRADE_SESSIONS_KEPT: Final = 2
"""``trade_totals`` keeps the latest sessions: the current one and the one before it."""

_TYPE: Final = "$type"
_MODEL: Final = "$model"
_MODEL_DATA: Final = "data"
_FLOAT: Final = "$float"
_DECIMAL: Final = "$decimal"
_FRACTION: Final = "$fraction"
_DATE: Final = "$date"
_FROZENSET: Final = "$frozenset"
_MAP: Final = "$map"
_INFINITIES: Final[Mapping[str, float]] = MappingProxyType({"inf": math.inf, "-inf": -math.inf})


class StateDecodeError(ValueError):
    """A saved EngineState that cannot be read back (Req 16.10)."""


# ---------------------------------------------------------------- state parts


@dataclass(frozen=True, slots=True)
class SessionTrades:
    """The filled trades of one session (Shadow_Trades excluded), summed (Req 14.5)."""

    session: date
    totals: PriorSession = field(default_factory=PriorSession)


@dataclass(frozen=True, slots=True)
class CardMemory:
    """The previous Decision_Time's values the Finding_Card triggers compare against.

    - ``grades``: the Grade of each Setup_Key emitted at the previous
      Decision_Time, in emission order (an absent key counts as ``Pass``, Req 25.6);
    - ``kings``: the King strike of each Map_State (symbol, metric) at the
      previous Decision_Time, ``None`` with no Snapshot or no King;
    - ``alerted``: the Setup_Keys that already had an Alert_2R this session (Req 25.8).

    Everything resets when a new session starts.
    """

    session: date | None = None
    grades: tuple[tuple[SetupKey, Grade], ...] = ()
    kings: tuple[tuple[SymMetric, float | None], ...] = ()
    alerted: tuple[SetupKey, ...] = ()


def _default_chart() -> ChartState:
    return ChartState.initial()


@dataclass(frozen=True, slots=True)
class EngineState:
    """Everything one Decision_Time hands to the next (see the module notes)."""

    t: Instant | None = None
    session: date | None = None
    taps: TapState = field(default_factory=TapState)
    lifecycle: LifecycleState = field(default_factory=LifecycleState)
    chart: ChartState = field(default_factory=_default_chart)
    book: OrderBook = field(default_factory=OrderBook)
    risk: RiskState = field(default_factory=RiskState)
    trade_totals: tuple[SessionTrades, ...] = ()
    cards: CardMemory = field(default_factory=CardMemory)

    @classmethod
    def initial(cls, chart_config: ChartConfig | None = None) -> EngineState:
        """The state before the first input; ``chart_config`` defaults to the section defaults."""
        return cls(chart=ChartState.initial(chart_config))

    # ---------------------------------------------------------------- trade totals

    def trades_of(self, session: date) -> PriorSession:
        """The summed filled trades of ``session``; ``PriorSession()`` when it has none."""
        for entry in self.trade_totals:
            if entry.session == session:
                return entry.totals
        return PriorSession()

    def with_trade(self, trade: Trade) -> EngineState:
        """Add one closed, filled trade to its session's totals (Shadow_Trades refused)."""
        if trade.shadow:
            raise ValueError("a Shadow_Trade never counts toward the big-win totals (Req 14.5)")
        session = trade.setup_key.session
        totals = {entry.session: entry.totals for entry in self.trade_totals}
        prev = totals.get(session, PriorSession())
        totals[session] = PriorSession(prev.net + trade.net, prev.r_sum + trade.r_multiple)
        kept = sorted(totals.items())[-TRADE_SESSIONS_KEPT:]
        return replace(self, trade_totals=tuple(SessionTrades(d, s) for d, s in kept))

    # ---------------------------------------------------------------- serialization

    def to_jsonable(self) -> dict[str, JsonValue]:
        """The state as plain JSON values, in a versioned envelope."""
        return {"format": STATE_FORMAT, "version": STATE_VERSION, "state": encode_value(self)}

    def to_canonical_json(self) -> str:
        """Canonical JSON text: sorted keys, no whitespace, ASCII, no NaN (design D7)."""
        return json.dumps(
            self.to_jsonable(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )

    @classmethod
    def from_jsonable(cls, obj: object) -> EngineState:
        """The state :meth:`to_jsonable` encoded; :class:`StateDecodeError` otherwise."""
        if not isinstance(obj, dict):
            raise StateDecodeError("an EngineState document must be a JSON object")
        if obj.get("format") != STATE_FORMAT or obj.get("version") != STATE_VERSION:
            raise StateDecodeError(
                f"not an EngineState document of format {STATE_FORMAT!r} version {STATE_VERSION}"
            )
        if set(obj) != {"format", "version", "state"}:
            raise StateDecodeError("an EngineState document has only format, version and state")
        state = decode_value(obj["state"])
        if not isinstance(state, EngineState):
            raise StateDecodeError(f"the document holds a {type(state).__name__}, not a state")
        return state

    @classmethod
    def from_json(cls, text: str) -> EngineState:
        """The state :meth:`to_canonical_json` wrote; :class:`StateDecodeError` otherwise."""
        try:
            obj = json.loads(text, parse_constant=_refuse_constant)
        except ValueError as exc:
            raise StateDecodeError(f"the EngineState text is not valid JSON: {exc}") from exc
        return cls.from_jsonable(obj)


def _refuse_constant(name: str) -> object:
    raise ValueError(f"{name} is not a JSON number")


# ---------------------------------------------------------------- the registry


@dataclass(frozen=True, slots=True)
class _Registered:
    """A decodable dataclass: its constructor and the names of its init fields."""

    cls: Callable[..., object]
    fields: frozenset[str]


def _dataclasses_of(
    short: str, objects: Iterable[object], module_name: str
) -> dict[str, _Registered]:
    """Every dataclass in ``objects`` defined in ``module_name``, by ``<short>.<Class>``."""
    out: dict[str, _Registered] = {}
    for obj in objects:
        if (
            isinstance(obj, type)
            and dataclasses.is_dataclass(obj)
            and obj.__module__ == module_name
        ):
            names = frozenset(f.name for f in dataclasses.fields(obj) if f.init)
            out[f"{short}.{obj.__qualname__}"] = _Registered(obj, names)
    return out


def _module_dataclasses(*modules: ModuleType) -> dict[str, _Registered]:
    out: dict[str, _Registered] = {}
    for module in modules:
        short = module.__name__.rpartition(".")[2]
        out.update(_dataclasses_of(short, vars(module).values(), module.__name__))
    return out


_CLASSES: Final[Mapping[str, _Registered]] = MappingProxyType(
    {
        **_module_dataclasses(
            types_module,
            taps_module,
            lifecycle_module,
            chart_module,
            targets_module,
            sizing_module,
            risk_module,
            planner_module,
        ),
        **_dataclasses_of("state", (SessionTrades, CardMemory, EngineState), __name__),
    }
)
"""The engine dataclasses a state can hold: the only classes :func:`decode_value` builds."""

_NAMES: Final[Mapping[object, str]] = MappingProxyType({r.cls: n for n, r in _CLASSES.items()})

_MODELS: Final[Mapping[str, type[ChartConfig]]] = MappingProxyType({"ChartConfig": ChartConfig})


# ---------------------------------------------------------------- encoding


def encode_value(value: object) -> JsonValue:
    """``value`` as plain JSON values by the module rules; ``TypeError`` or ``ValueError``."""
    return _encode(value, "$")


def _text(value: JsonValue) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _encode(value: object, path: str) -> JsonValue:
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, int | numbers.Integral):
        return operator.index(value)
    if isinstance(value, float):
        if math.isfinite(value):
            return float(value)
        if math.isnan(value):
            raise ValueError(f"cannot encode NaN at {path}")
        return {_FLOAT: "inf" if value > 0 else "-inf"}
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"cannot encode the non-finite Decimal {value} at {path}")
        return {_DECIMAL: str(value)}
    if isinstance(value, Fraction):
        return {_FRACTION: str(value)}
    if isinstance(value, datetime):
        raise TypeError(f"cannot encode a datetime at {path}; states hold Instants")
    if isinstance(value, date):
        return {_DATE: value.isoformat()}
    if isinstance(value, tuple):
        return [_encode(item, f"{path}[{i}]") for i, item in enumerate(value)]
    if isinstance(value, frozenset):
        items = [_encode(item, f"{path}[]") for item in value]
        return {_FROZENSET: sorted(items, key=_text)}
    if isinstance(value, ChartConfig):
        return {_MODEL: "ChartConfig", _MODEL_DATA: _encode(value.model_dump(), f"{path}.data")}
    if isinstance(value, Mapping):
        return {
            _MAP: [
                [_encode(k, f"{path}{{key}}"), _encode(v, f"{path}[{k!r}]")]
                for k, v in value.items()
            ]
        }
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        name = _NAMES.get(type(value))
        if name is None:
            raise TypeError(f"{type(value).__qualname__} at {path} is not an EngineState type")
        out: dict[str, JsonValue] = {_TYPE: name}
        for f in dataclasses.fields(value):
            if f.init:
                out[f.name] = _encode(getattr(value, f.name), f"{path}.{f.name}")
        return out
    raise TypeError(f"cannot encode {type(value).__qualname__} at {path} in an EngineState")


# ---------------------------------------------------------------- decoding


def decode_value(obj: object) -> object:
    """The value :func:`encode_value` encoded; :class:`StateDecodeError` otherwise."""
    try:
        return _decode(obj, "$")
    except StateDecodeError:
        raise
    except (TypeError, ValueError, KeyError, InvalidOperation, ZeroDivisionError) as exc:
        raise StateDecodeError(f"cannot decode the EngineState: {exc}") from exc


def _decode(obj: object, path: str) -> object:
    if obj is None or isinstance(obj, bool | int | str):
        return obj
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise StateDecodeError(f"a non-finite number at {path}")
        return obj
    if isinstance(obj, list):
        return tuple(_decode(item, f"{path}[{i}]") for i, item in enumerate(obj))
    if not isinstance(obj, dict):
        raise StateDecodeError(f"unexpected {type(obj).__name__} at {path}")
    if _TYPE in obj:
        return _decode_dataclass(obj, path)
    if _MODEL in obj:
        return _decode_model(obj, path)
    if len(obj) != 1:
        raise StateDecodeError(f"an object at {path} has no type tag")
    ((tag, payload),) = obj.items()
    return _decode_tagged(tag, payload, path)


def _decode_tagged(tag: str, payload: object, path: str) -> object:
    if tag == _FLOAT and isinstance(payload, str) and payload in _INFINITIES:
        return _INFINITIES[payload]
    if tag == _DECIMAL and isinstance(payload, str):
        value = Decimal(payload)
        if not value.is_finite():
            raise StateDecodeError(f"a non-finite Decimal at {path}")
        return value
    if tag == _FRACTION and isinstance(payload, str):
        return Fraction(payload)
    if tag == _DATE and isinstance(payload, str):
        return date.fromisoformat(payload)
    if tag == _FROZENSET and isinstance(payload, list):
        return frozenset(_decode(item, f"{path}[]") for item in payload)
    if tag == _MAP and isinstance(payload, list):
        out: dict[object, object] = {}
        for i, pair in enumerate(payload):
            if not isinstance(pair, list) or len(pair) != 2:
                raise StateDecodeError(f"a map entry at {path}[{i}] is not a [key, value] pair")
            key = _decode(pair[0], f"{path}[{i}].key")
            if key in out:
                raise StateDecodeError(f"a map at {path} repeats the key {key!r}")
            out[key] = _decode(pair[1], f"{path}[{i}].value")
        return out
    raise StateDecodeError(f"unknown or malformed tag {tag!r} at {path}")


def _decode_dataclass(obj: dict[str, object], path: str) -> object:
    name = obj[_TYPE]
    registered = _CLASSES.get(name) if isinstance(name, str) else None
    if registered is None:
        raise StateDecodeError(f"{name!r} at {path} is not an EngineState type")
    given = set(obj) - {_TYPE}
    if not given <= registered.fields:
        unknown = sorted(given - registered.fields)
        raise StateDecodeError(f"{name} at {path} has unknown fields {unknown}")
    kwargs = {k: _decode(v, f"{path}.{k}") for k, v in obj.items() if k != _TYPE}
    return registered.cls(**kwargs)


def _decode_model(obj: dict[str, object], path: str) -> object:
    name = obj[_MODEL]
    model = _MODELS.get(name) if isinstance(name, str) else None
    if model is None or set(obj) != {_MODEL, _MODEL_DATA}:
        raise StateDecodeError(f"a malformed or unknown config model at {path}")
    return model.model_validate(_decode(obj[_MODEL_DATA], f"{path}.data"))
