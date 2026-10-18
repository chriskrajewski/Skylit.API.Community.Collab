"""Validated declarative spec for a rules strategy.

The expressible language is indicators, conditions, entries, stops, scale-outs,
add-backs, a timeframe (including daily), a session, a side, and fixed-risk
sizing. :func:`validate_document` reports rules the language cannot express
instead of dropping them.
"""

from __future__ import annotations

import hashlib
from datetime import time
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from fse.engine.sizing import POINT_VALUE_USD
from fse.logio.canonical_json import dumps as canonical_dumps

INDICATOR_KINDS = ("ema", "dema", "sma", "supertrend", "atr", "vwap", "sigma_bands")
CONDITION_OPS = (
    "crosses_above",
    "crosses_below",
    "crosses",
    "close_above",
    "close_below",
    "n_consecutive_closes",
    "higher_low",
    "lower_high",
    "range_breakout",
    "supertrend_up",
    "supertrend_down",
    "after",
)
ENTRY_KINDS = ("market", "pullback_limit", "breakout_stop")
STOP_KINDS = ("swing_low", "swing_high", "ticks", "atr")
BAR_SIZES = ("1m", "2m", "3m", "5m", "15m", "30m", "60m", "1d")
PRICE_FIELDS = frozenset({"open", "high", "low", "close"})

# Names that mean a rule outside this language. A document that carries one
# does not validate, and the report names it.
_DENIED_KEYS = frozenset(
    {
        "options",
        "option",
        "premium",
        "greeks",
        "delta",
        "gamma",
        "iv",
        "implied_vol",
        "news",
        "discretionary",
        "orderflow",
        "order_flow",
        "footprint",
        "market_profile",
        "volume_profile",
        "renko",
        "heikin",
        "heikin_ashi",
        "other_symbol",
        "divergence",
        "trailing",
        "trail",
        "time_stop",
        "tick_bars",
        "range_bars",
    }
)
_TOP_KEYS = frozenset(
    {
        "name",
        "side",
        "symbols",
        "timeframe",
        "session",
        "indicators",
        "conditions",
        "context",
        "entry",
        "stop",
        "scale_outs",
        "add_backs",
        "sizing",
    }
)
_INDICATOR_KEYS = frozenset({"id", "kind", "length", "multiplier", "source", "k", "anchor"})
_CONDITION_KEYS = frozenset({"id", "op", "left", "right", "n", "side", "lookback"})
_ENTRY_KEYS = frozenset({"kind", "when_all", "short_when_all", "limit_at", "lookback"})
_STOP_KEYS = frozenset(
    {"kind", "ticks", "atr", "atr_mult", "swing_lookback", "buffer_ticks"}
)
_SCALE_KEYS = frozenset({"pct", "at_r", "when"})
_ADDBACK_KEYS = frozenset({"pct", "when"})
_TIMEFRAME_KEYS = frozenset({"bar"})
_SESSION_KEYS = frozenset({"start", "end", "flatten_at"})
_SIZING_KEYS = frozenset({"mode", "risk_usd", "max_contracts"})

_LENGTH_KINDS = frozenset({"ema", "dema", "sma", "atr", "supertrend"})


def limitations() -> list[str]:
    """Rules this language does not express. The card schema publishes the same list."""
    return [
        "Only EMA, DEMA, SMA, Supertrend, ATR, session VWAP, and session VWAP sigma bands.",
        "Conditions are crosses, close above or below, N consecutive closes, "
        "confirmed higher-low and lower-high pivots, range breakouts, Supertrend "
        "direction, and an 'after' latch (a condition true only once another has "
        "already fired since the fill).",
        "Entries are market, a pullback limit at an indicator, or a breakout stop "
        "beyond the prior range. The host books each scale-out as its own bracket "
        "(one protective stop and at most one target). Four partials are four tags, "
        "not one order with four targets.",
        "Stops are a confirmed swing low or high, a fixed tick distance, or an ATR "
        "multiple. A stop that trails or loosens is not expressed.",
        "Scale-outs are a percent of the entry, at an R multiple or when a condition "
        "is true. Add-backs are one extra entry of a percent when a condition is true.",
        "Bars are resampled from the host's 1-minute bars: 1m, 2m, 3m, 5m, 15m, 30m, "
        "60m, or one daily bar per session. Renko, range, tick, and Heikin-Ashi bars "
        "are not expressed.",
        "One instrument per card. Cross-symbol divergence, options premium, greeks, "
        "news, order flow, footprint, and market profile are not expressed.",
        "Sizing is fixed dollar risk divided by the stop distance and the contract's "
        "point value (MES, MNQ, ES, NQ). A leg whose percent rounds to zero contracts "
        "is folded into the largest leg.",
        "side 'both' needs entry.short_when_all. The long rules are not mirrored.",
        "A restart rebuilds indicators only from bars that arrive after the snapshot. "
        "An open trade is kept, and new entries wait until the indicators are warm again.",
    ]


class IndicatorSpec(BaseModel):
    """One series the conditions can name."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
    kind: Literal["ema", "dema", "sma", "supertrend", "atr", "vwap", "sigma_bands"]
    length: int | None = Field(default=None, ge=1, le=500)
    multiplier: float | None = Field(default=None, gt=0, le=20)
    source: Literal["close", "hlc3", "ohlc4"] = "close"
    """Price the moving averages read. Supertrend, ATR, VWAP, and sigma bands ignore it."""
    k: float | None = Field(default=None, gt=0, le=10)
    """Sigma-band distance, in session-VWAP standard deviations."""
    anchor: Literal["session"] = "session"
    """VWAP and sigma bands reset at the session open. That is the only anchor."""

    @model_validator(mode="after")
    def _required(self) -> IndicatorSpec:
        if self.kind in _LENGTH_KINDS and self.length is None:
            raise ValueError(f"{self.kind} needs length")
        if self.kind == "supertrend" and self.multiplier is None:
            raise ValueError("supertrend needs multiplier")
        if self.kind == "sigma_bands" and self.k is None:
            raise ValueError("sigma_bands needs k")
        return self


class ConditionSpec(BaseModel):
    """A named boolean. ``left`` and ``right`` are indicator ids or open/high/low/close."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
    op: Literal[
        "crosses_above",
        "crosses_below",
        "crosses",
        "close_above",
        "close_below",
        "n_consecutive_closes",
        "higher_low",
        "lower_high",
        "range_breakout",
        "supertrend_up",
        "supertrend_down",
        "after",
    ]
    left: str | None = None
    right: str | None = None
    n: int | None = Field(default=None, ge=1, le=50)
    side: Literal["above", "below"] | None = None
    lookback: int | None = Field(default=None, ge=1, le=50)


class EntrySpec(BaseModel):
    """How a flat book gets into a trade once ``when_all`` is true."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["market", "pullback_limit", "breakout_stop"]
    when_all: list[str] = Field(min_length=1)
    short_when_all: list[str] | None = None
    """Required when the strategy side is ``both``. Long rules are not mirrored."""
    limit_at: str | None = None
    """Indicator id the pullback limit rests at."""
    lookback: int | None = Field(default=None, ge=1, le=200)
    """Bars of the range a breakout stop sits just beyond."""


class StopSpec(BaseModel):
    """Initial protective stop. It does not trail."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["swing_low", "swing_high", "ticks", "atr"]
    ticks: int | None = Field(default=None, ge=1, le=5000)
    atr: str | None = None
    """Id of an ATR indicator, used when ``kind`` is ``atr``."""
    atr_mult: float = Field(default=1.0, gt=0, le=20)
    swing_lookback: int = Field(default=5, ge=1, le=50)
    """Bars on each side that confirm a swing, and the fallback window."""
    buffer_ticks: int = Field(default=1, ge=0, le=100)
    """Ticks beyond the swing price, on the loss side."""


class ScaleOutSpec(BaseModel):
    """Close ``pct`` percent of the entry at ``at_r`` or when ``when`` is true."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pct: float = Field(gt=0, le=100)
    at_r: float | None = Field(default=None, gt=0, le=50)
    when: str | None = None

    @model_validator(mode="after")
    def _one_exit(self) -> ScaleOutSpec:
        if self.at_r is None and self.when is None:
            raise ValueError("a scale-out needs at_r or when")
        return self


class AddBackSpec(BaseModel):
    """Add ``pct`` percent of the original size once, when ``when`` is true."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pct: float = Field(gt=0, le=100)
    when: str


class TimeframeSpec(BaseModel):
    """Resample the host's 1-minute bars. ``1d`` is one bar per session."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bar: Literal["1m", "2m", "3m", "5m", "15m", "30m", "60m", "1d"] = "1m"


class SessionSpec(BaseModel):
    """New York wall clock. ``start > end`` means the window crosses midnight."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start: time = time(9, 30)
    end: time = time(16, 0)
    flatten_at: time | None = None


class SizingSpec(BaseModel):
    """Contracts = floor(risk_usd / (stop points * point value)), capped."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["fixed_risk"] = "fixed_risk"
    risk_usd: Decimal = Field(default=Decimal("100"), gt=0, le=100_000)
    max_contracts: int = Field(default=8, ge=1, le=50)


class StrategySpec(BaseModel):
    """The whole strategy. Unknown keys are refused."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=80)
    side: Literal["long", "short", "both"]
    symbols: list[str] = Field(min_length=1, max_length=1)
    timeframe: TimeframeSpec = TimeframeSpec()
    session: SessionSpec = SessionSpec()
    indicators: list[IndicatorSpec] = Field(default_factory=list)
    conditions: list[ConditionSpec] = Field(default_factory=list)
    context: list[str] = Field(default_factory=list)
    """Condition ids that must all be true before an entry signal counts."""
    entry: EntrySpec
    stop: StopSpec
    scale_outs: list[ScaleOutSpec] = Field(default_factory=list)
    add_backs: list[AddBackSpec] = Field(default_factory=list)
    sizing: SizingSpec = SizingSpec()


class UnsupportedRule(BaseModel):
    """One rule the language will not trade."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    rule: str
    reason: str


class ValidationReport(BaseModel):
    """The result of :func:`validate_document`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool
    errors: list[str]
    unsupported: list[UnsupportedRule]
    spec_hash: str | None = None
    limitations: list[str]


def spec_hash(spec: StrategySpec) -> str:
    """SHA-256 of the canonical normalized spec. Same rules, same hash."""
    payload = spec.model_dump(mode="json")
    return hashlib.sha256(canonical_dumps(payload).encode("ascii")).hexdigest()


def describe_rules(spec: StrategySpec) -> list[str]:
    """Short lines a card can show. The same facts the spec already stores."""
    lines = [
        f"{spec.side} {' '.join(spec.symbols)} on {spec.timeframe.bar} bars",
        (
            f"session {spec.session.start.isoformat()}-{spec.session.end.isoformat()}"
            + (
                ""
                if spec.session.flatten_at is None
                else f", flat at {spec.session.flatten_at.isoformat()}"
            )
        ),
    ]
    for item in spec.indicators:
        bits: list[str] = [item.kind]
        if item.length is not None:
            bits.append(str(item.length))
        if item.multiplier is not None:
            bits.append(f"x{item.multiplier:g}")
        if item.k is not None:
            bits.append(f"{item.k:g} sigma")
        lines.append(f"indicator {item.id}: {' '.join(bits)}")
    for cond in spec.conditions:
        lines.append(f"when {cond.id}: {cond.op}")
    if spec.context:
        lines.append("context: " + ", ".join(spec.context))
    entry = spec.entry
    lines.append(
        f"enter {entry.kind} when " + ", ".join(entry.when_all or ["(always)"])
    )
    lines.append(f"stop {spec.stop.kind}")
    for scale in spec.scale_outs:
        if scale.at_r is not None:
            lines.append(f"scale out {scale.pct:g}% at {scale.at_r:g}R")
        else:
            lines.append(f"scale out {scale.pct:g}% when {scale.when}")
    for add in spec.add_backs:
        lines.append(f"add back {add.pct:g}% when {add.when}")
    lines.append(
        f"risk ${spec.sizing.risk_usd} per trade, at most {spec.sizing.max_contracts} contracts"
    )
    return lines


def example_spec() -> StrategySpec:
    """10/21 EMA cross, Supertrend(10, 3), pullback, swing stop, four scale-outs."""
    return StrategySpec.model_validate(EXAMPLE_SPEC)


EXAMPLE_SPEC: dict[str, object] = {
    "name": "ema-cross-supertrend-pullback",
    "side": "long",
    "symbols": ["MNQ"],
    "timeframe": {"bar": "1m"},
    "session": {"start": "09:30:00", "end": "16:00:00", "flatten_at": "15:55:00"},
    "indicators": [
        {"id": "ema10", "kind": "ema", "length": 10},
        {"id": "ema21", "kind": "ema", "length": 21},
        {"id": "st", "kind": "supertrend", "length": 10, "multiplier": 3},
    ],
    "conditions": [
        {"id": "bull_cross", "op": "crosses_above", "left": "ema10", "right": "ema21"},
        {"id": "bear_cross", "op": "crosses_below", "left": "ema10", "right": "ema21"},
        {"id": "st_up", "op": "supertrend_up", "left": "st"},
        {
            "id": "under_10",
            "op": "n_consecutive_closes",
            "left": "close",
            "right": "ema10",
            "n": 2,
            "side": "below",
        },
        {
            "id": "under_21",
            "op": "n_consecutive_closes",
            "left": "close",
            "right": "ema21",
            "n": 2,
            "side": "below",
        },
        {"id": "lower_high", "op": "lower_high", "lookback": 2},
        {"id": "runner_exit", "op": "after", "left": "lower_high", "right": "bear_cross"},
    ],
    "context": ["st_up"],
    "entry": {"kind": "pullback_limit", "when_all": ["bull_cross"], "limit_at": "ema10"},
    "stop": {"kind": "swing_low", "swing_lookback": 5, "buffer_ticks": 1},
    "scale_outs": [
        {"pct": 25, "at_r": 4},
        {"pct": 25, "when": "under_10"},
        {"pct": 25, "when": "under_21"},
        {"pct": 25, "when": "runner_exit"},
    ],
    "add_backs": [],
    "sizing": {"mode": "fixed_risk", "risk_usd": "100", "max_contracts": 8},
}


def validate_document(doc: object) -> ValidationReport:
    """Validate ``doc``. Unsupported rules fail the report; they are not dropped."""
    unsupported = _unsupported(doc) if isinstance(doc, dict) else []
    covered = {item.path for item in unsupported}
    errors: list[str] = []
    spec: StrategySpec | None = None
    if not isinstance(doc, dict):
        errors.append("spec must be an object")
    else:
        try:
            spec = StrategySpec.model_validate(doc)
        except ValidationError as exc:
            errors.extend(_pydantic_errors(exc, covered))
    if spec is not None and not unsupported:
        errors.extend(_semantic(spec))
    ok = spec is not None and not errors and not unsupported
    return ValidationReport(
        ok=ok,
        errors=errors,
        unsupported=unsupported,
        spec_hash=spec_hash(spec) if ok and spec is not None else None,
        limitations=limitations(),
    )


def _pydantic_errors(exc: ValidationError, covered: set[str]) -> list[str]:
    out: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err["loc"])
        if any(loc == path or loc.startswith(path + ".") for path in covered):
            continue
        prefix = f"spec.{loc}" if loc else "spec"
        out.append(f"{prefix}: {err['msg']}")
    return out


def _unsupported(doc: dict[str, object]) -> list[UnsupportedRule]:
    found: list[UnsupportedRule] = []
    _walk(doc, "spec", found)
    for key in sorted(set(doc) - _TOP_KEYS):
        if key.lower() not in _DENIED_KEYS:
            found.append(
                UnsupportedRule(
                    path=f"spec.{key}",
                    rule=str(key),
                    reason="unknown strategy field; it is not part of the rules language",
                )
            )
    _scan_section(
        doc.get("indicators"), "spec.indicators", _INDICATOR_KEYS, "kind", INDICATOR_KINDS, found
    )
    _scan_section(
        doc.get("conditions"), "spec.conditions", _CONDITION_KEYS, "op", CONDITION_OPS, found
    )
    entry = doc.get("entry")
    if isinstance(entry, dict):
        _scan_object(entry, "spec.entry", _ENTRY_KEYS, "kind", ENTRY_KINDS, found)
    stop = doc.get("stop")
    if isinstance(stop, dict):
        _scan_object(stop, "spec.stop", _STOP_KEYS, "kind", STOP_KINDS, found)
    timeframe = doc.get("timeframe")
    if isinstance(timeframe, dict):
        _scan_object(timeframe, "spec.timeframe", _TIMEFRAME_KEYS, "bar", BAR_SIZES, found)
    session = doc.get("session")
    if isinstance(session, dict):
        _extra_keys(session, "spec.session", _SESSION_KEYS, found)
    sizing = doc.get("sizing")
    if isinstance(sizing, dict):
        _scan_object(sizing, "spec.sizing", _SIZING_KEYS, "mode", ("fixed_risk",), found)
    _scan_list(doc.get("scale_outs"), "spec.scale_outs", _SCALE_KEYS, found)
    _scan_list(doc.get("add_backs"), "spec.add_backs", _ADDBACK_KEYS, found)
    return _unique(found)


def _walk(node: object, path: str, found: list[UnsupportedRule]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}"
            if key.lower() in _DENIED_KEYS:
                found.append(
                    UnsupportedRule(
                        path=child,
                        rule=str(key),
                        reason="this rule is outside the futures rules language",
                    )
                )
            else:
                _walk(value, child, found)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _walk(value, f"{path}[{index}]", found)


def _scan_section(
    node: object,
    path: str,
    allowed: frozenset[str],
    value_key: str,
    allow_values: tuple[str, ...],
    found: list[UnsupportedRule],
) -> None:
    if not isinstance(node, list):
        return
    for index, item in enumerate(node):
        if isinstance(item, dict):
            _scan_object(item, f"{path}[{index}]", allowed, value_key, allow_values, found)


def _scan_list(
    node: object, path: str, allowed: frozenset[str], found: list[UnsupportedRule]
) -> None:
    if not isinstance(node, list):
        return
    for index, item in enumerate(node):
        if isinstance(item, dict):
            _extra_keys(item, f"{path}[{index}]", allowed, found)


def _scan_object(
    item: dict[str, object],
    path: str,
    allowed: frozenset[str],
    value_key: str,
    allow_values: tuple[str, ...],
    found: list[UnsupportedRule],
) -> None:
    _extra_keys(item, path, allowed, found)
    value = item.get(value_key)
    if isinstance(value, str) and value not in allow_values:
        found.append(
            UnsupportedRule(
                path=f"{path}.{value_key}",
                rule=value,
                reason=f"{value_key} {value!r} is not in the rules language",
            )
        )


def _extra_keys(
    item: dict[str, object], path: str, allowed: frozenset[str], found: list[UnsupportedRule]
) -> None:
    for key in sorted(set(item) - allowed):
        if key.lower() in _DENIED_KEYS:
            continue
        found.append(
            UnsupportedRule(
                path=f"{path}.{key}",
                rule=str(key),
                reason="unknown field; it is not part of the rules language",
            )
        )


def _unique(found: list[UnsupportedRule]) -> list[UnsupportedRule]:
    seen: set[tuple[str, str]] = set()
    out: list[UnsupportedRule] = []
    for item in found:
        key = (item.path, item.rule)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _semantic(spec: StrategySpec) -> list[str]:
    errors: list[str] = []
    symbols = spec.symbols
    if len(set(symbols)) != len(symbols):
        errors.append("spec.symbols: duplicate symbol")
    for symbol in symbols:
        if symbol not in POINT_VALUE_USD:
            errors.append(
                f"spec.symbols: {symbol} has no point value "
                "(sizing expresses MES, MNQ, ES, NQ)"
            )
    ind_ids = [item.id for item in spec.indicators]
    cond_ids = [item.id for item in spec.conditions]
    if len(set(ind_ids)) != len(ind_ids):
        errors.append("spec.indicators: duplicate id")
    if len(set(cond_ids)) != len(cond_ids):
        errors.append("spec.conditions: duplicate id")
    price_clash = (set(ind_ids) | set(cond_ids)) & PRICE_FIELDS
    if price_clash:
        errors.append(f"spec: ids collide with price fields: {sorted(price_clash)}")
    if set(ind_ids) & set(cond_ids):
        errors.append("spec: an indicator id and a condition id are the same")
    known_series = set(ind_ids) | PRICE_FIELDS
    cond_set = set(cond_ids)
    indicators = {item.id: item for item in spec.indicators}
    for cond in spec.conditions:
        errors.extend(_condition_refs(cond, known_series, cond_set, indicators))
    errors.extend(_entry_refs(spec, indicators, cond_set))
    errors.extend(_stop_refs(spec, indicators))
    for index, scale in enumerate(spec.scale_outs):
        if scale.when is not None and scale.when not in cond_set:
            errors.append(f"spec.scale_outs[{index}].when: unknown condition {scale.when!r}")
    for index, add in enumerate(spec.add_backs):
        if add.when not in cond_set:
            errors.append(f"spec.add_backs[{index}].when: unknown condition {add.when!r}")
    if spec.scale_outs:
        total = sum(item.pct for item in spec.scale_outs)
        if abs(total - 100) > 0.01:
            errors.append(f"spec.scale_outs: percents sum to {total}, not 100")
    legs = max(1, len(spec.scale_outs)) + len(spec.add_backs)
    if legs > 10:
        errors.append(
            "spec: the host keeps at most 10 open trades; "
            f"this spec can hold {legs} scale-out and add-back legs at once"
        )
    return errors


def _condition_refs(
    cond: ConditionSpec,
    series: set[str],
    conds: set[str],
    indicators: dict[str, IndicatorSpec],
) -> list[str]:
    path = f"spec.conditions.{cond.id}"
    errors: list[str] = []
    if cond.op in {"crosses_above", "crosses_below", "crosses", "close_above", "close_below"}:
        errors.extend(_need_series(cond.left, f"{path}.left", series))
        errors.extend(_need_series(cond.right, f"{path}.right", series))
    elif cond.op == "n_consecutive_closes":
        if cond.n is None or cond.side is None:
            errors.append(f"{path}: n_consecutive_closes needs n and side")
        errors.extend(_need_series(cond.right, f"{path}.right", series))
    elif cond.op in {"higher_low", "lower_high"}:
        if cond.lookback is None:
            errors.append(f"{path}: {cond.op} needs lookback")
    elif cond.op == "range_breakout":
        if cond.lookback is None or cond.side is None:
            errors.append(f"{path}: range_breakout needs lookback and side")
    elif cond.op in {"supertrend_up", "supertrend_down"}:
        if cond.left not in indicators or indicators[cond.left].kind != "supertrend":
            errors.append(f"{path}.left: {cond.op} names a supertrend indicator")
    elif cond.op == "after":
        if cond.left not in conds or cond.right not in conds:
            errors.append(f"{path}: after names two condition ids")
        if cond.left == cond.id or cond.right == cond.id:
            errors.append(f"{path}: after cannot name itself")
    return errors


def _need_series(name: str | None, path: str, series: set[str]) -> list[str]:
    if name is None or name not in series:
        return [f"{path}: unknown series {name!r}"]
    return []


def _entry_refs(
    spec: StrategySpec, indicators: dict[str, IndicatorSpec], conds: set[str]
) -> list[str]:
    errors: list[str] = []
    entry = spec.entry
    for name in entry.when_all:
        if name not in conds:
            errors.append(f"spec.entry.when_all: unknown condition {name!r}")
    for name in spec.context:
        if name not in conds:
            errors.append(f"spec.context: unknown condition {name!r}")
    if spec.side == "both":
        if not entry.short_when_all:
            errors.append("spec.entry.short_when_all: required when side is both")
        for name in entry.short_when_all or []:
            if name not in conds:
                errors.append(f"spec.entry.short_when_all: unknown condition {name!r}")
    if entry.kind == "pullback_limit" and (
        entry.limit_at is None or entry.limit_at not in indicators
    ):
        errors.append("spec.entry.limit_at: pullback_limit names an indicator")
    if entry.kind == "breakout_stop" and entry.lookback is None:
        errors.append("spec.entry.lookback: breakout_stop needs lookback")
    return errors


def _stop_refs(spec: StrategySpec, indicators: dict[str, IndicatorSpec]) -> list[str]:
    stop = spec.stop
    if stop.kind == "ticks" and stop.ticks is None:
        return ["spec.stop.ticks: required when kind is ticks"]
    atr = None if stop.atr is None else indicators.get(stop.atr)
    if stop.kind == "atr" and (atr is None or atr.kind != "atr"):
        return ["spec.stop.atr: atr stop names an atr indicator"]
    if spec.side == "both" and stop.kind in {"swing_low", "swing_high"}:
        return ["spec.stop: side both cannot use a one-sided swing; use ticks or atr"]
    if stop.kind == "swing_high" and spec.side == "long":
        return ["spec.stop: a long stop under a swing uses swing_low, not swing_high"]
    if stop.kind == "swing_low" and spec.side == "short":
        return ["spec.stop: a short stop above a swing uses swing_high, not swing_low"]
    return []
