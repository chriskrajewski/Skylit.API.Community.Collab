"""Secret redaction (design §1 "Redaction", Req 1.9).

A :class:`Redactor` holds every non-blank Secret_Variable value plus each
ProjectX session token registered with :meth:`Redactor.add`. :meth:`Redactor.redact`
replaces each occurrence of a registered value with ``[REDACTED]``.

Forms. A value is found in any mix of these per-character forms, so a partly
encoded copy is found too:

- raw;
- URL-encoded: ``%XX`` for each UTF-8 byte, either hex case, and ``+`` for a
  space;
- JSON-escaped, as ``json.dumps`` writes it (``\\"``, ``\\\\``, ``\\n``,
  ``\\u00e9``), because canonical JSON lines are redacted after encoding;
- ``repr``-escaped (``\\x01``, ``\\'``), as tracebacks and ``!r`` messages show it.

Overlaps. Values are kept longest first. Every occurrence of every value is
located in the original text, overlapping ones included, and each run of
overlapping occurrences becomes one ``[REDACTED]``. No character of any
occurrence survives, whichever value starts first.

Joins. A replacement can create a new occurrence where a marker meets its
neighbours (value ``]x`` after ``[REDACTED]`` and ``x``). The text is
re-checked until no such occurrence is left, at most ``_MAX_PASSES`` times;
past that, the whole text becomes ``[REDACTED]`` (fail closed). A value that is
itself part of ``[REDACTED]`` (such as ``ACT``) is replaced in the input, but
the markers are not re-checked for it.

A value with leading or trailing whitespace is also registered stripped.
Nothing here ever puts a value in a repr or an error message.
"""

from __future__ import annotations

import functools
import json
import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from fse.logio.canonical_json import JsonValue
    from fse.secrets.env import EnvView

__all__ = ["REDACTED", "Redactor"]

REDACTED: Final = "[REDACTED]"

# Re-checks of a redacted text for occurrences formed at marker edges.
_MAX_PASSES: Final = 8


@dataclass(frozen=True, slots=True)
class _Entry:
    value: str
    pattern: re.Pattern[str]  # one occurrence in any mix of forms
    has_space: bool  # a space may appear as "+"
    in_marker: bool  # the value is a substring of REDACTED


@dataclass(frozen=True, slots=True)
class _State:
    entries: tuple[_Entry, ...]  # longest value first
    strict: tuple[_Entry, ...]  # entries re-checked after replacement


_EMPTY: Final = _State((), ())


class Redactor:
    """Replaces registered secret values with ``[REDACTED]``. Safe to share between threads."""

    __slots__ = ("_lock", "_state")

    def __init__(self, values: Iterable[str] = ()) -> None:
        self._lock = threading.Lock()
        self._state = _EMPTY
        for value in values:
            self.add(value)

    @classmethod
    def from_env(cls, env: EnvView) -> Redactor:
        """A Redactor for every non-blank Secret_Variable value in ``env``."""
        return cls(env.secret_values())

    def __reduce__(self) -> tuple[type[Redactor], tuple[tuple[str, ...]]]:
        """Pickle by value, so an Experiment_Runner worker process redacts the same values."""
        return (Redactor, (tuple(e.value for e in self._state.entries),))

    def add(self, value: str) -> None:
        """Register ``value``, for example a ProjectX session token as it arrives.

        A blank value (empty or whitespace only) is ignored. Registering a value
        twice has no effect.
        """
        if not isinstance(value, str):
            raise TypeError(f"a secret value must be a str, not {type(value).__name__}")
        if not value.strip():
            return
        with self._lock:
            known = {e.value for e in self._state.entries}
            fresh = [_entry(v) for v in dict.fromkeys((value, value.strip())) if v not in known]
            if not fresh:
                return
            entries = tuple(
                sorted((*self._state.entries, *fresh), key=lambda e: (-len(e.value), e.value))
            )
            self._state = _State(entries, tuple(e for e in entries if not e.in_marker))

    def redact(self, text: str) -> str:
        """``text`` with every occurrence of every registered value replaced."""
        if not isinstance(text, str):
            raise TypeError(f"can only redact a str, not {type(text).__name__}")
        state = self._state  # one snapshot, so a concurrent add() cannot mix states
        out = _replace_pass(text, state.entries)
        if out is text:
            return text
        for _ in range(_MAX_PASSES):
            again = _replace_pass(out, state.strict)
            if again is out:
                return out
            out = again
        return REDACTED

    def redact_json(self, value: JsonValue) -> JsonValue:
        """A copy of a JSON value with every string, keys included, redacted.

        For payloads that another serializer encodes (Notifier webhook bodies,
        Narrator input). Two keys that redact to the same text are kept apart
        with a ``#2``, ``#3`` ... suffix, so no entry is lost.
        """
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, list):
            return [self.redact_json(item) for item in value]
        if isinstance(value, dict):
            out: dict[str, JsonValue] = {}
            for key, item in value.items():
                out[_unused_key(out, self.redact(key))] = self.redact_json(item)
            return out
        return value

    def __repr__(self) -> str:
        # Never show values: a repr can reach a log line or a traceback.
        return f"Redactor({len(self._state.entries)} values)"


# ---------------------------------------------------------------- matching


def _entry(value: str) -> _Entry:
    return _Entry(
        value=value,
        pattern=re.compile("".join(_char_pattern(c) for c in value)),
        has_space=" " in value,
        in_marker=value in REDACTED,
    )


@functools.lru_cache(maxsize=4096)
def _char_pattern(c: str) -> str:
    """A regex group matching ``c`` in each of its forms, longest form first."""
    forms: dict[str, int] = {re.escape(c): 1}
    encoded = _utf8(c)
    forms["".join("%" + _hex_any_case(b) for b in encoded)] = 3 * len(encoded)
    if c == " ":
        forms[re.escape("+")] = 1
    escapes = [json.dumps(c)[1:-1], repr(c)[1:-1]]
    if c == "'":
        escapes.append("\\'")
    for escaped in escapes:
        if escaped != c:
            forms.setdefault(re.escape(escaped), len(escaped))
    ordered = sorted(forms.items(), key=lambda item: -item[1])
    return "(?:" + "|".join(regex for regex, _ in ordered) + ")"


def _utf8(c: str) -> bytes:
    try:
        return c.encode("utf-8", "surrogateescape")
    except UnicodeEncodeError:
        return c.encode("utf-8", "surrogatepass")  # a lone surrogate outside U+DC80-U+DCFF


def _hex_any_case(byte: int) -> str:
    return "".join(f"[{d.upper()}{d.lower()}]" if d.isalpha() else d for d in f"{byte:02X}")


def _replace_pass(text: str, entries: tuple[_Entry, ...]) -> str:
    """One replacement pass; returns ``text`` itself when nothing matched."""
    spans = _spans(text, entries)
    if not spans:
        return text
    spans.sort()
    parts: list[str] = []
    kept_until = 0
    run_start, run_end = spans[0]
    for start, end in spans[1:]:
        if start < run_end:  # overlaps the current run: extend it
            run_end = max(run_end, end)
            continue
        parts += (text[kept_until:run_start], REDACTED)
        kept_until = run_end
        run_start, run_end = start, end
    parts += (text[kept_until:run_start], REDACTED, text[run_end:])
    return "".join(parts)


def _spans(text: str, entries: tuple[_Entry, ...]) -> list[tuple[int, int]]:
    """Every occurrence of every entry, overlapping occurrences included."""
    # Any non-raw form needs a "%" or a backslash, or a "+" for a space.
    escaped = "%" in text or "\\" in text
    plus = "+" in text
    spans: list[tuple[int, int]] = []
    for entry in entries:
        if escaped or (plus and entry.has_space):
            pos = 0
            while (m := entry.pattern.search(text, pos)) is not None:
                spans.append(m.span())
                pos = m.start() + 1
        else:
            size = len(entry.value)
            pos = text.find(entry.value)
            while pos >= 0:
                spans.append((pos, pos + size))
                pos = text.find(entry.value, pos + 1)
    return spans


def _unused_key(taken: dict[str, JsonValue], key: str) -> str:
    if key not in taken:
        return key
    n = 2
    while f"{key}#{n}" in taken:
        n += 1
    return f"{key}#{n}"
