"""The Config_Loader: read, parse and validate a Strategy_Config file (design §17, D6).

:func:`load` returns :class:`LoadOk` (the config and its warnings) or
:class:`LoadErr` (every error found). It runs before any Decision_Time, so a
rejected file never reaches the engine (Req 17.3-17.4).

1. **Read.** A missing file or one that cannot be read as UTF-8 text is an
   error that names the path (Req 17.3).
2. **Parse** with :class:`UniqueKeySafeLoader`, a ``yaml.SafeLoader`` that
   records each repeated mapping key as a :class:`DuplicateKey` with its key
   path and line, keeps the first occurrence and goes on, so a repeated
   Pattern, Gate, Exit_Mode, Kill_Switch or account rule id is reported with
   every other error. A syntax error names the path and the line of the first
   error (``problem_mark.line + 1``). An empty file reads as ``{}``.
3. **Validate** with :class:`~fse.config.schema.StrategyConfig` (strict,
   unknown keys forbidden). Every pydantic error becomes one
   :class:`ConfigError` with its key path, its kind (unknown key, duplicate id,
   missing required key, wrong type, out of range) and, for wrong-type and
   out-of-range errors, the expected type, allowed values or range (Req 17.4).
   The account-rule ranges and the Maximum Loss Limit below the starting
   balance (Req 15.4) and the fee ranges (Req 13.8) are schema checks.
4. **Cross-field check** (Req 13.8): each instrument ``data.instruments``
   trades needs ``fills.costs.<instrument>``. It reads the parsed mapping, so
   it is reported even when other keys fail.
5. **Warnings** (Req 17.6) for a valid config: ``fse.config.warnings``.

Errors carry the line of their key when the file has one (the parent's line
for a missing key). The value of an unknown key or of a text that fails its
pattern is never shown: a credential put in the wrong place must not reach a
terminal. Other values are cut to :data:`SHOWN_CHARS` characters.
"""

from __future__ import annotations

import re
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Final, Literal

import yaml
from pydantic import ValidationError
from pydantic_core import ErrorDetails

from fse.config.schema import StrategyConfig
from fse.config.schema._base import DUPLICATE_ID
from fse.config.schema.fills import FEE_MAX_USD, FEE_MIN_USD, FILL_INSTRUMENTS
from fse.config.warnings import ConfigWarning, contradiction_warnings

__all__ = [
    "EXIT_INVALID_INPUT",
    "KIND_DUPLICATE_ID",
    "KIND_MISSING_FILE",
    "KIND_MISSING_KEY",
    "KIND_OUT_OF_RANGE",
    "KIND_SYNTAX_ERROR",
    "KIND_UNKNOWN_KEY",
    "KIND_UNREADABLE_FILE",
    "KIND_WRONG_TYPE",
    "SHOWN_CHARS",
    "ConfigError",
    "ConfigLoadError",
    "DuplicateKey",
    "ErrorKind",
    "LoadErr",
    "LoadOk",
    "LoadResult",
    "UniqueKeySafeLoader",
    "key_path",
    "load",
    "load_or_raise",
    "load_text",
    "validate_data",
]

# Design "Exit codes": 2 = invalid input, which includes a Strategy_Config.
EXIT_INVALID_INPUT: Final = 2

type ErrorKind = Literal[
    "missing file",
    "unreadable file",
    "syntax error",
    "unknown key",
    "duplicate id",
    "missing required key",
    "wrong type",
    "out of range",
]

KIND_MISSING_FILE: Final = "missing file"
KIND_UNREADABLE_FILE: Final = "unreadable file"
KIND_SYNTAX_ERROR: Final = "syntax error"
KIND_UNKNOWN_KEY: Final = "unknown key"
KIND_DUPLICATE_ID: Final = "duplicate id"
KIND_MISSING_KEY: Final = "missing required key"
KIND_WRONG_TYPE: Final = "wrong type"
KIND_OUT_OF_RANGE: Final = "out of range"

SHOWN_CHARS: Final = 40
"""The longest value text an error shows."""

# The expected type for each pydantic "wrong type" error (custom errors reuse these types).
_EXPECTED_TYPE: Final[Mapping[str, str]] = {
    "int_type": "a whole number",
    "int_parsing": "a whole number",
    "int_from_float": "a whole number",
    "float_type": "a number",
    "float_parsing": "a number",
    "string_type": "a string",
    "bool_type": "true or false",
    "bool_parsing": "true or false",
    "model_type": "a mapping",
    "model_attributes_type": "a mapping",
    "dict_type": "a mapping",
    "tuple_type": "a list",
    "list_type": "a list",
    "decimal_type": "a quoted decimal string such as '0.37'",
    "decimal_parsing": "a quoted decimal string such as '0.37'",
    "time_type": "a quoted 'HH:MM' time",
    "time_parsing": "a quoted 'HH:MM' time",
    "none_required": "null",
}

_UNKNOWN_KEY_TYPES: Final = frozenset({"extra_forbidden", "invalid_key"})
# A text that fails its pattern may be a credential in the wrong place (a URL
# with user info, a key pasted as an id), so its value is not shown.
_HIDDEN_VALUE_TYPES: Final = frozenset({"string_pattern_mismatch"})
_DECIMAL_REPR: Final = re.compile(r"Decimal\('([^']*)'\)")


# ---------------------------------------------------------------- results


@dataclass(frozen=True, slots=True)
class ConfigError:
    """One reason a Strategy_Config file is rejected.

    ``key_path`` is ``""`` for a file-level error (missing, unreadable, syntax)
    and for the document root. ``line`` is 1-based. ``value`` is the configured
    value as text, ``None`` when not shown.
    """

    file: str
    key_path: str
    kind: ErrorKind
    message: str
    expected: str | None = None
    value: str | None = None
    line: int | None = None

    def __str__(self) -> str:
        where = self.file if self.line is None else f"{self.file}:{self.line}"
        head = ": ".join(part for part in (where, self.key_path, self.kind) if part)
        text = f"{head}: {self.message}"
        if self.value is not None:
            text += f" (configured {self.value})"
        if self.expected is not None:
            text += f"; expected {self.expected}"
        return text


@dataclass(frozen=True, slots=True)
class LoadOk:
    """A valid Strategy_Config, with its contradiction warnings (Req 17.6)."""

    file: str
    config: StrategyConfig
    warnings: tuple[ConfigWarning, ...]


@dataclass(frozen=True, slots=True)
class LoadErr:
    """A rejected file: every error found, in file then schema order."""

    file: str
    errors: tuple[ConfigError, ...]


type LoadResult = LoadOk | LoadErr


class ConfigLoadError(Exception):
    """Raised by :func:`load_or_raise`; the message lists every error, one per line."""

    exit_code: ClassVar[int] = EXIT_INVALID_INPUT

    def __init__(self, errors: Sequence[ConfigError]) -> None:
        self.errors: tuple[ConfigError, ...] = tuple(errors)
        super().__init__("\n".join(str(e) for e in self.errors))


# ---------------------------------------------------------------- YAML


@dataclass(frozen=True, slots=True)
class DuplicateKey:
    """A mapping key that repeats an earlier key of the same mapping."""

    key_path: str
    line: int


class UniqueKeySafeLoader(yaml.SafeLoader):  # type: ignore[misc]  # PyYAML ships no types
    """A ``yaml.SafeLoader`` that records repeated keys instead of keeping the last one.

    Use one instance per document: ``loader = UniqueKeySafeLoader(text)``,
    ``data = loader.get_single_data()``, then read :attr:`duplicates` and
    :attr:`key_lines`, then ``loader.dispose()``. The first occurrence of a
    repeated key is kept. Key paths use ``.`` between keys and ``[i]`` for
    list items, like :func:`key_path`.
    """

    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self.duplicates: list[DuplicateKey] = []
        self.key_lines: dict[str, int] = {}
        self._mapping_paths: dict[int, str] = {}

    def get_single_data(self) -> object:
        node = self.get_single_node()
        if node is None:
            return None
        self._index(node)
        return self.construct_document(node)

    def construct_mapping(self, node: Any, deep: bool = False) -> Any:
        if isinstance(node, yaml.MappingNode):
            self._drop_repeats(node)
        return super().construct_mapping(node, deep=deep)

    def _index(self, root: Any) -> None:
        """Record the key path of every mapping node and the line of every key and item."""
        stack: list[tuple[Any, str]] = [(root, "")]
        seen: set[int] = set()
        while stack:
            node, path = stack.pop()
            if id(node) in seen:  # an alias: index each node once
                continue
            seen.add(id(node))
            if isinstance(node, yaml.MappingNode):
                self._mapping_paths[id(node)] = path
                for key_node, value_node in node.value:
                    if isinstance(key_node, yaml.ScalarNode):
                        child = _join(path, str(key_node.value))
                        self.key_lines.setdefault(child, key_node.start_mark.line + 1)
                        stack.append((value_node, child))
            elif isinstance(node, yaml.SequenceNode):
                for i, item in enumerate(node.value):
                    child = f"{path}[{i}]"
                    self.key_lines.setdefault(child, item.start_mark.line + 1)
                    stack.append((item, child))

    def _drop_repeats(self, node: Any) -> None:
        """Record and remove every explicit key that repeats an earlier one (merges excluded)."""
        seen: set[Hashable] = set()
        kept: list[tuple[Any, Any]] = []
        for key_node, value_node in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                kept.append((key_node, value_node))
                continue
            key = self.construct_object(key_node, deep=True)
            if not isinstance(key, Hashable):  # the base constructor reports it
                kept.append((key_node, value_node))
                continue
            if key in seen:
                path = _join(self._mapping_paths.get(id(node), ""), str(key))
                self.duplicates.append(DuplicateKey(path, key_node.start_mark.line + 1))
                continue
            seen.add(key)
            kept.append((key_node, value_node))
        node.value = kept


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


@dataclass(frozen=True, slots=True)
class _Parsed:
    data: object
    duplicates: tuple[DuplicateKey, ...]
    key_lines: Mapping[str, int]


def _parse(text: str, file: str) -> _Parsed | ConfigError:
    loader = UniqueKeySafeLoader(text)
    try:
        data = loader.get_single_data()
    except yaml.YAMLError as exc:
        return _syntax_error(exc, text, file)
    finally:
        loader.dispose()
    return _Parsed(
        data={} if data is None else data,
        duplicates=tuple(loader.duplicates),
        key_lines=dict(loader.key_lines),
    )


def _syntax_error(exc: yaml.YAMLError, text: str, file: str) -> ConfigError:
    # Only the problem and the mark are used: str(exc) quotes the file's text.
    mark: Any = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
    line: int | None = None
    if mark is not None:
        line = int(mark.line) + 1
    elif isinstance(getattr(exc, "position", None), int):  # a ReaderError
        line = text.count("\n", 0, exc.position) + 1
    problem: Any = getattr(exc, "problem", None) or getattr(exc, "context", None)
    reason = str(problem) if problem else getattr(exc, "reason", None) or "invalid YAML"
    return ConfigError(
        file=file,
        key_path="",
        kind=KIND_SYNTAX_ERROR,
        message=f"the file does not parse as YAML: {reason}",
        line=line,
    )


# ---------------------------------------------------------------- public API


def load(path: Path) -> LoadResult:
    """Read, parse and validate the Strategy_Config file at ``path``."""
    file = str(path)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return LoadErr(file, (ConfigError(file, "", KIND_MISSING_FILE, "the file does not exist"),))
    except OSError as exc:
        reason = exc.strerror or type(exc).__name__
        return LoadErr(
            file, (ConfigError(file, "", KIND_UNREADABLE_FILE, f"cannot read the file: {reason}"),)
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        message = f"the file is not UTF-8 text (byte {exc.start})"
        return LoadErr(file, (ConfigError(file, "", KIND_UNREADABLE_FILE, message),))
    return load_text(text, source=file)


def load_text(text: str, *, source: str = "<string>") -> LoadResult:
    """Parse and validate Strategy_Config YAML ``text``; ``source`` names it in errors."""
    parsed = _parse(text, source)
    if isinstance(parsed, ConfigError):
        return LoadErr(source, (parsed,))
    return validate_data(
        parsed.data, source=source, duplicates=parsed.duplicates, key_lines=parsed.key_lines
    )


def validate_data(
    data: object,
    *,
    source: str = "<data>",
    duplicates: Sequence[DuplicateKey] = (),
    key_lines: Mapping[str, int] | None = None,
) -> LoadResult:
    """Validate parsed YAML ``data`` (plain mappings, lists and scalars) as a Strategy_Config."""
    lines: Mapping[str, int] = {} if key_lines is None else key_lines
    errors = [
        ConfigError(
            file=source,
            key_path=d.key_path,
            kind=KIND_DUPLICATE_ID,
            message=f"{d.key_path} repeats an earlier key of the same mapping",
            line=d.line,
        )
        for d in duplicates
    ]
    config: StrategyConfig | None = None
    try:
        config = StrategyConfig.model_validate(data)
    except ValidationError as exc:
        errors.extend(_from_pydantic(e, source, lines) for e in exc.errors())
    errors.extend(_missing_costs(data, source, lines))
    if errors or config is None:
        return LoadErr(source, tuple(errors))
    return LoadOk(source, config, contradiction_warnings(config))


def load_or_raise(path: Path) -> LoadOk:
    """:func:`load`, raising :class:`ConfigLoadError` (exit code 2) on any error."""
    result = load(path)
    if isinstance(result, LoadErr):
        raise ConfigLoadError(result.errors)
    return result


def key_path(loc: Sequence[int | str]) -> str:
    """A pydantic ``loc`` as a key path: ``exits.per_regime.Whipsaw.mode``, ``zones[0].near``."""
    path = ""
    for part in loc:
        if isinstance(part, int) and not isinstance(part, bool):
            path += f"[{part}]"
        else:
            path = _join(path, str(part))
    return path


# ---------------------------------------------------------------- error mapping


def _from_pydantic(err: ErrorDetails, file: str, lines: Mapping[str, int]) -> ConfigError:
    kind = _kind(err)
    loc = tuple(err["loc"])
    if err["type"] == "invalid_key" and loc:
        # The loc ends with the offending key itself, which may be an int.
        path = _join(key_path(loc[:-1]), str(loc[-1]))
    else:
        path = key_path(loc)
    expected: str | None = None
    if kind == KIND_WRONG_TYPE:
        expected = _EXPECTED_TYPE.get(err["type"], err["msg"])
    elif kind == KIND_OUT_OF_RANGE:
        expected = _expected_range(err)
    value: str | None = None
    if kind in (KIND_WRONG_TYPE, KIND_OUT_OF_RANGE) and err["type"] not in _HIDDEN_VALUE_TYPES:
        ctx = err.get("ctx") or {}
        value = str(ctx["value"]) if "value" in ctx else _shown(err.get("input"))
    return ConfigError(
        file=file,
        key_path=path,
        kind=kind,
        message=err["msg"],
        expected=expected,
        value=value,
        line=_line_for(path, lines),
    )


def _kind(err: ErrorDetails) -> ErrorKind:
    kind = err["type"]
    if kind in _UNKNOWN_KEY_TYPES:
        return KIND_UNKNOWN_KEY
    if kind == DUPLICATE_ID:
        return KIND_DUPLICATE_ID
    if kind == "missing" and not err.get("ctx"):
        return KIND_MISSING_KEY
    if kind in _EXPECTED_TYPE or kind.endswith(("_type", "_parsing")):
        return KIND_WRONG_TYPE
    return KIND_OUT_OF_RANGE


def _expected_range(err: ErrorDetails) -> str:
    """The allowed values or range of an out-of-range error, from its context."""
    ctx: dict[str, Any] = dict(err.get("ctx") or {})
    if "range" in ctx:  # the account dollar amounts state their whole range
        return str(ctx["range"])
    match err["type"]:
        case "literal_error" if "where" in ctx:
            return f"one of {ctx['where']}: {ctx['expected']}"
        case "literal_error":
            return f"one of {ctx['expected']}"
        case "greater_than":
            return f"above {_bound(ctx['gt'])}"
        case "greater_than_equal":
            return f"at least {_bound(ctx['ge'])}"
        case "less_than":
            return f"below {_bound(ctx['lt'])}"
        case "less_than_equal":
            return f"at most {_bound(ctx['le'])}"
        case "too_short":
            return f"at least {ctx['min_length']} items"
        case "too_long":
            return f"at most {ctx['max_length']} items"
        case "string_too_short":
            return f"at least {ctx['min_length']} characters"
        case "string_too_long":
            return f"at most {ctx['max_length']} characters"
        case "string_pattern_mismatch":
            return f"text matching {ctx['pattern']}"
        case "decimal_max_places":
            return f"at most {ctx['decimal_places']} decimal places"
        case "finite_number":
            return "a finite number"
    return err["msg"]


def _bound(value: object) -> str:
    """A bound as plain text: pydantic gives a ``Decimal`` bound as ``"Decimal('25.00')"``."""
    text = str(value)
    match = _DECIMAL_REPR.fullmatch(text)
    return match[1] if match else text


def _shown(value: object) -> str:
    text = repr(value)
    return text if len(text) <= SHOWN_CHARS else text[: SHOWN_CHARS - 3] + "..."


def _line_for(path: str, lines: Mapping[str, int]) -> int | None:
    """The line of ``path``, else of its nearest ancestor in the file."""
    while path:
        if path in lines:
            return lines[path]
        cut = max(path.rfind("."), path.rfind("["))
        path = path[:cut] if cut > 0 else ""
    return None


# ---------------------------------------------------------------- cross-field checks


def _missing_costs(data: object, file: str, lines: Mapping[str, int]) -> list[ConfigError]:
    """``fills.costs.<instrument>`` missing for an instrument ``data.instruments`` trades.

    Reads the parsed mapping with the schema defaults (MES, MNQ) for omitted
    keys, so it runs even when other keys fail. A section of the wrong type is
    left to the schema errors.
    """
    if not isinstance(data, dict):
        return []
    section = data.get("data", {})
    instruments = section.get("instruments", {}) if isinstance(section, dict) else None
    fills = data.get("fills", {})
    costs = fills.get("costs", {}) if isinstance(fills, dict) else None
    if not isinstance(instruments, dict) or not isinstance(costs, dict):
        return []
    errors: list[ConfigError] = []
    for key, default in (("es_levels", "MES"), ("nq_levels", "MNQ")):
        instrument = instruments.get(key, default)
        if not isinstance(instrument, str) or instrument not in FILL_INSTRUMENTS:
            continue
        if costs.get(instrument) is not None:
            continue
        path = f"fills.costs.{instrument}"
        errors.append(
            ConfigError(
                file=file,
                key_path=path,
                kind=KIND_MISSING_KEY,
                message=(
                    f"the commission and exchange_fee of {instrument}, which "
                    f"data.instruments.{key} trades, are required and have no default"
                ),
                expected=(
                    f"{path}.commission and {path}.exchange_fee, each a quoted dollar amount "
                    f"from {FEE_MIN_USD} to {FEE_MAX_USD} per contract per side"
                ),
                line=_line_for(path, lines),
            )
        )
    return errors
