"""Property 2: Redaction completeness.

*For any* set of non-blank secret values (overlapping, prefix-sharing and
URL-unsafe values included), any registered session tokens, and any text that
embeds them, every output produced through the Log_Writer (log lines, reports,
manifests, recordings, Finding_Cards, Narrator input, error and uncaught-error
output) contains none of those values or their URL-encoded forms, and contains
``[REDACTED]`` in place of each occurrence.

Each example builds a ``LogWriter`` with ``LogWriter.from_env`` over an
``EnvView`` of generated Secret_Variable values (no ``.env`` file is read),
installs the process hooks, registers the generated session tokens with
``add_secret`` after ``install()`` (as the Broker_Adapter does when a token
arrives mid-run), and sends the text through every sink:

- console: ``echo``, ``error``, ``exception`` and ``format_exception``;
- files: ``write_text`` (reports), ``write_json`` (manifests), ``append_line``,
  ``append_json`` (Fetch_Logs), ``open_lines`` plain (decision logs) and gzip
  (recordings, Req 23.8);
- payloads: ``json_text`` and ``json_bytes`` (Finding_Cards, webhook bodies)
  and ``redact_json`` (Narrator input, Req 25.10);
- ``logging``: a handler added after ``install()`` (message, an ``extra=``
  field and a traceback), and a raw record through ``RedactingFilter``;
- uncaught errors: ``sys.excepthook``, ``threading.excepthook``,
  ``sys.unraisablehook`` and the asyncio loop handler (a failing callback, and a
  context with a message, a value and an exception).

After each example the test checks that every process hook, the log record
factory, ``Logger.makeRecord``, the handler filters, the ``httpx``/``httpcore``
levels and the loop's exception handler are back to their values from before
``install()``.

Two generators:

1. ``test_each_occurrence_becomes_one_marker``: values and filler come from
   disjoint alphabets, so the expected output is known exactly: the text with
   each embedded occurrence (raw, URL-encoded in upper or lower hex, ``+`` for a
   space, or partly encoded; overlapping pairs included) replaced by one
   ``[REDACTED]``.
2. ``test_no_value_or_encoded_form_survives``: values and filler share one
   alphabet that includes the marker's own characters, filler may be empty,
   and occurrences may also be JSON- or repr-escaped (except in what the JSON
   sinks get; see the known limitation below), so occurrences overlap, touch
   and form at marker edges. No output may contain a value in raw,
   URL-encoded, JSON-escaped or repr-escaped form, JSON outputs also after
   decoding, and every output must contain ``[REDACTED]``.

Generator limits, each a case that no redactor can meet:

- A value that is itself part of ``[REDACTED]`` (such as ``ACT``), or whose
  stripped form is, is excluded: every marker contains it.
- Values hold no line break. Sinks and the ``logging`` formatter join
  separately redacted parts with a line break after redaction (the line end;
  message, extra field and traceback), so a value with a line break could be
  assembled from parts that never held it.
- No lone surrogates: sinks encode text with ``backslashreplace`` after
  redaction.

Known limitation (excluded by Operator decision, not fixed): in
``test_no_value_or_encoded_form_survives`` the JSON sinks (``write_json``,
``append_json``, the JSON lines of ``open_lines``, ``json_text`` and
``json_bytes``) get the text with each JSON- or repr-escaped segment written
raw, and an example is discarded when that text still holds a value's JSON- or
repr-escaped form that differs from the value (formed by filler or touching
segments). Reason: these sinks encode the text as JSON and then redact the
encoded text, so an occurrence that was already escaped in the input is escaped
twice there, while the Redactor matches each character raw, URL-encoded, or
JSON- or repr-escaped once. A JSON reader then sees the once-escaped value: for
the value ``b"``, the text ``b\\"`` is written as ``b\\\\\\"`` and read back as
``b\\"``. Only values with a ``"``, a ``\\``, a control or a non-ASCII
character have such a form. Every other sink, ``redact_json`` included, still
gets the escaped segments.

Generated values are short strings from small fixed alphabets: fake by
construction.

**Validates: Requirements 1.9, 23.8, 25.10**
"""

from __future__ import annotations

import asyncio
import gzip
import io
import json
import logging
import re
import sys
import tempfile
import threading
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn
from urllib.parse import quote, quote_plus

from hypothesis import assume, given
from hypothesis import strategies as st

from fse.logio import REDACTED, LogWriter, canonical_json
from fse.logio.canonical_json import JsonValue
from fse.logio.log_writer import HTTP_LOGGERS
from fse.secrets.env import SECRET_LIST_VARIABLE, SECRET_VARIABLES, EnvView

# ---------------------------------------------------------------- alphabets

_SYMBOLS = " /?&=+%#\"\\'@-_.~"  # URL-unsafe, JSON-escaped and repr-escaped characters
_NON_ASCII = "\u00e9\u20ac\U0001f600"  # 2, 3 and 4 UTF-8 bytes; the emoji is a JSON surrogate pair

# Exact test. Every value holds one of these anchors. No anchor occurs in the
# marker, in "ValueError: ", in "detail: " or in the JSON around the payload text,
# so an occurrence can only lie inside an embedded segment.
_ANCHORS_A = "bkmqz019"
_VALUES_A = _ANCHORS_A + _SYMBOLS + _NON_ASCII
# Filler shares no character with a value, with any URL or escape form ("%", "+",
# backslash, hex digits, escape letters) or with the marker, so no occurrence can
# reach into it.
_FILLER_A = "GHIJKLMNOPQSVWXYZ!*;<>|,()"
assert not set(_FILLER_A) & {
    *_VALUES_A,
    *REDACTED,
    *"%+\\0123456789abcdefABCDEFnrtuxU",
}

# Adversarial test: the marker's own characters, ":", a tab and a control
# character too, and filler drawn from the same characters plus line breaks.
_VALUES_B = _VALUES_A + "[]REDACT:\t\x01"
_FILLER_B = _VALUES_B + "\nGx"

_FORMS_A = ("raw", "url", "url-lower", "url-plus", "url-partial")
_ESCAPED_FORMS = ("json", "repr")  # written raw for the JSON sinks (known limitation)
_FORMS_B = (*_FORMS_A, *_ESCAPED_FORMS)

# ---------------------------------------------------------------- cases


@dataclass(frozen=True)
class Secrets:
    """Secret_Variable values, session tokens, and fused overlapping occurrences."""

    env_values: tuple[str, ...]
    tokens: tuple[str, ...]
    # Raw strings made of two overlapping occurrences: base + extra, where the
    # value base[-k:] + extra is registered too.
    fused: tuple[str, ...]

    @property
    def values(self) -> tuple[str, ...]:
        return (*self.env_values, *self.tokens)


@dataclass(frozen=True)
class Case:
    secrets: Secrets
    text: str
    # What the JSON sinks get: ``text`` with JSON- and repr-escaped segments raw.
    json_sink_text: str


@dataclass(frozen=True)
class ExactCase(Case):
    expected: str  # ``text`` with each embedded segment replaced by one marker


def _keep_exact(value: str) -> bool:
    return bool(value.strip()) and any(c in _ANCHORS_A for c in value)


def _keep_any(value: str) -> bool:
    stripped = value.strip()
    return bool(stripped) and value not in REDACTED and stripped not in REDACTED


@st.composite
def _secrets(draw: st.DrawFn, alphabet: str, anchors: str, keep: Callable[[str], bool]) -> Secrets:
    chars = st.text(alphabet, max_size=6)
    base_values = st.tuples(chars, st.sampled_from(anchors), chars).map("".join)
    bases = draw(st.lists(base_values, min_size=1, max_size=3))
    values = list(bases)
    fused: list[tuple[str, str, str]] = []  # (base, overlapping value, fused text)
    for _ in range(draw(st.integers(0, 3))):
        base = draw(st.sampled_from(bases))
        extra = draw(st.text(alphabet, min_size=1, max_size=6))
        kind = draw(st.sampled_from(("prefix", "suffix", "extend", "overlap")))
        if kind == "extend" or len(base) == 1:
            values.append(base + extra)
            continue
        cut = draw(st.integers(1, len(base) - 1))
        if kind == "prefix":
            values.append(base[:cut])
        elif kind == "suffix":
            values.append(base[cut:])
        else:
            overlapping = base[cut:] + extra
            values.append(overlapping)
            fused.append((base, overlapping, base + extra))
    kept = [value for value in dict.fromkeys(values) if keep(value)]
    assume(kept)
    as_token = draw(st.lists(st.booleans(), min_size=len(kept), max_size=len(kept)))
    return Secrets(
        env_values=tuple(v for v, t in zip(kept, as_token, strict=True) if not t),
        tokens=tuple(v for v, t in zip(kept, as_token, strict=True) if t),
        fused=tuple(text for base, w, text in fused if base in kept and w in kept),
    )


def _lower_hex(text: str) -> str:
    return re.sub(r"%[0-9A-F]{2}", lambda m: m.group().lower(), text)


def _encode(draw: st.DrawFn, value: str, form: str) -> str:
    """``value`` as it would appear in a log line, URL, JSON text or repr."""
    match form:
        case "raw":
            return value
        case "url":
            return quote(value, safe="")
        case "url-lower":
            return _lower_hex(quote(value, safe=""))
        case "url-plus":
            return quote_plus(value, safe="")
        case "url-partial":
            keep = draw(st.lists(st.booleans(), min_size=len(value), max_size=len(value)))
            return "".join(c if k else quote(c, safe="") for c, k in zip(value, keep, strict=True))
        case "json":
            return json.dumps(value)[1:-1]
        case "repr":
            return repr(value)[1:-1]
    raise AssertionError(form)


def _segments(draw: st.DrawFn, secrets: Secrets, forms: Sequence[str]) -> list[tuple[str, str]]:
    """Every value and fused string once, in any order, plus a few repeats, each encoded.

    Each segment is a pair: as written for every sink, and as written for the
    JSON sinks, which get a JSON- or repr-escaped segment raw (known limitation).
    """
    pieces = [*secrets.values, *secrets.fused]
    order = [*draw(st.permutations(pieces)), *draw(st.lists(st.sampled_from(pieces), max_size=2))]
    segments: list[tuple[str, str]] = []
    for piece in order:
        form = draw(st.sampled_from(forms))
        written = _encode(draw, piece, form)
        segments.append((written, piece if form in _ESCAPED_FORMS else written))
    return segments


def _interleave(fillers: Sequence[str], segments: Sequence[str]) -> str:
    return fillers[0] + "".join(s + f for s, f in zip(segments, fillers[1:], strict=True))


def _escaped_forms(value: str) -> set[str]:
    """The JSON- and repr-escaped forms of ``value`` that differ from it."""
    return {json.dumps(value)[1:-1], repr(value)[1:-1]} - {value}


@st.composite
def _exact_cases(draw: st.DrawFn) -> ExactCase:
    secrets = draw(_secrets(_VALUES_A, _ANCHORS_A, _keep_exact))
    segments = [written for written, _ in _segments(draw, secrets, _FORMS_A)]
    n = len(segments) + 1
    fillers = draw(st.lists(st.text(_FILLER_A, min_size=1, max_size=3), min_size=n, max_size=n))
    text = _interleave(fillers, segments)
    return ExactCase(
        secrets=secrets,
        text=text,
        json_sink_text=text,  # no escaped forms: _FORMS_A only, and no backslash in filler
        expected=_interleave(fillers, [REDACTED] * len(segments)),
    )


@st.composite
def _adversarial_cases(draw: st.DrawFn) -> Case:
    secrets = draw(_secrets(_VALUES_B, _VALUES_B, _keep_any))
    segments = _segments(draw, secrets, _FORMS_B)
    n = len(segments) + 1
    fillers = draw(st.lists(st.text(_FILLER_B, max_size=4), min_size=n, max_size=n))
    json_sink_text = _interleave(fillers, [for_json for _, for_json in segments])
    # Known limitation: no escaped form may reach the JSON sinks, from filler either.
    assume(
        not any(
            form in json_sink_text for value in secrets.values for form in _escaped_forms(value)
        )
    )
    return Case(
        secrets=secrets,
        text=_interleave(fillers, [written for written, _ in segments]),
        json_sink_text=json_sink_text,
    )


# ---------------------------------------------------------------- driving every sink

_LOG_FORMAT = "%(message)s\n%(detail)s"
_LOGGER = logging.getLogger("fse.tests.property.p02")
_THREAD_NAME = "p02-worker"


@dataclass(frozen=True)
class Run:
    outputs: dict[str, str]  # sink -> what it wrote, as text
    narrator: JsonValue  # what LogWriter.redact_json returned
    hooks_restored: bool


def _payload(text: str) -> dict[str, JsonValue]:
    """A Finding_Card or manifest shape. Fixed keys and ``null``: no anchor character."""
    return {"MSG": text, "LIST": [text, None]}


def _env_view(values: Sequence[str]) -> EnvView:
    """Values for the built-in Secret_Variables, then for names listed in FSE_SECRET_VARS."""
    extra = [f"FSE_P02_SECRET_{i}" for i in range(max(0, len(values) - len(SECRET_VARIABLES)))]
    shell = dict(zip((*SECRET_VARIABLES, *extra), values, strict=False))
    shell[SECRET_LIST_VARIABLE] = ",".join(extra)
    return EnvView(shell, {})


def _since(stream: io.StringIO, action: Callable[[], object]) -> str:
    """What ``action`` wrote to ``stream``."""
    mark = len(stream.getvalue())
    action()
    return stream.getvalue()[mark:]


def _raised(text: str) -> ValueError:
    try:
        raise ValueError(text)
    except ValueError as exc:
        return exc


def _raise_value_error(text: str) -> NoReturn:
    raise ValueError(text)


def _fail_in_thread(text: str) -> None:
    worker = threading.Thread(target=_raise_value_error, args=(text,), name=_THREAD_NAME)
    worker.start()
    worker.join()


class _Unraisable:
    """Raises from ``__del__``, so CPython reports the error through ``sys.unraisablehook``."""

    def __init__(self, text: str) -> None:
        self.text = text

    def __del__(self) -> None:
        raise ValueError(self.text)


def _lose_unraisable(text: str) -> None:
    leaky = _Unraisable(text)
    del leaky  # CPython runs __del__ here


def _fail_in_callback(loop: asyncio.AbstractEventLoop, text: str) -> None:
    loop.call_soon(_raise_value_error, text)
    loop.run_until_complete(asyncio.sleep(0))


def _hook_state(loop: asyncio.AbstractEventLoop) -> tuple[object, ...]:
    """Everything ``install()`` replaces, by identity."""
    handlers = [*logging.getLogger().handlers]
    if logging.lastResort is not None:
        handlers.append(logging.lastResort)
    return (
        sys.excepthook,
        sys.unraisablehook,
        threading.excepthook,
        logging.getLogRecordFactory(),
        logging.Logger.makeRecord,
        tuple((handler, tuple(handler.filters)) for handler in handlers),
        tuple(logging.getLogger(name).level for name in HTTP_LOGGERS),
        loop.get_exception_handler(),
    )


def _strings(value: JsonValue) -> Iterator[str]:
    """Every string in a JSON value, keys included."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)


def _read(path: Path, *, compressed: bool = False) -> str:
    data = path.read_bytes()
    return (gzip.decompress(data) if compressed else data).decode("utf-8")


def _file_sinks(writer: LogWriter, case: Case, workdir: Path, out: dict[str, str]) -> None:
    text = case.text
    payload = _payload(case.json_sink_text)
    writer.write_text(workdir / "report.md", text)
    out["write_text"] = _read(workdir / "report.md")
    writer.write_json(workdir / "manifest.json", payload)
    out["write_json"] = _read(workdir / "manifest.json")
    writer.append_line(workdir / "run.log", text)
    out["append_line"] = _read(workdir / "run.log")
    writer.append_json(workdir / "fetch_log.jsonl", payload, fsync=True)
    out["append_json"] = _read(workdir / "fetch_log.jsonl")
    for sink, name, compress in (
        ("open_lines", "decisions.jsonl", False),
        ("open_lines gzip", "session.jsonl.gz", True),
    ):
        with writer.open_lines(workdir / name, compress=compress) as lines:
            lines.write_line(text)
            lines.write_json(payload)
        out[sink] = _read(workdir / name, compressed=compress)


def _logging_sinks(writer: LogWriter, text: str, exc: ValueError, out: dict[str, str]) -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)  # added after install(), with no filter of its own
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    _LOGGER.addHandler(handler)
    _LOGGER.propagate = False
    _LOGGER.setLevel(logging.DEBUG)
    try:
        out["logging"] = _since(stream, lambda: _LOGGER.warning("%s", text, extra={"detail": text}))
        out["logging exc_info"] = _since(
            stream, lambda: _LOGGER.error("%s", text, exc_info=exc, extra={"detail": text})
        )
    finally:
        _LOGGER.removeHandler(handler)
        _LOGGER.propagate = True
        _LOGGER.setLevel(logging.NOTSET)

    stream = io.StringIO()
    filtered = logging.StreamHandler(stream)
    filtered.setFormatter(logging.Formatter(_LOG_FORMAT))
    filtered.addFilter(writer.logging_filter())
    # Built directly, so only the filter can redact it.
    record = logging.LogRecord(_LOGGER.name, logging.WARNING, __file__, 1, "%s", (text,), None)
    vars(record)["detail"] = text
    filtered.handle(record)
    out["logging filter"] = stream.getvalue()


def _run_every_sink(case: Case) -> Run:
    secrets, text = case.secrets, case.text
    stdout, stderr = io.StringIO(), io.StringIO()
    writer = LogWriter.from_env(_env_view(secrets.env_values), stdout=stdout, stderr=stderr)
    exc = _raised(text)
    out: dict[str, str] = {}
    narrator: JsonValue = None
    loop = asyncio.new_event_loop()
    try:
        before = _hook_state(loop)
        with tempfile.TemporaryDirectory(prefix="fse-p02-") as tmp, writer.install(loop=loop):
            for token in secrets.tokens:
                writer.add_secret(token)

            out["echo"] = _since(stdout, lambda: writer.echo(text))
            out["error"] = _since(stderr, lambda: writer.error(text))
            out["exception"] = _since(stderr, lambda: writer.exception(exc, message=text))
            out["format_exception"] = writer.format_exception(exc)

            _file_sinks(writer, case, Path(tmp), out)

            payload = _payload(case.json_sink_text)
            out["json_text"] = writer.json_text(payload)
            out["json_bytes"] = writer.json_bytes(payload).decode("utf-8")
            narrator = writer.redact_json({"MSG": text, text: [text, None]})
            out["redact_json"] = "\n".join(_strings(narrator))

            _logging_sinks(writer, text, exc, out)

            out["sys.excepthook"] = _since(
                stderr, lambda: sys.excepthook(type(exc), exc, exc.__traceback__)
            )
            out["threading.excepthook"] = _since(stderr, lambda: _fail_in_thread(text))
            out["sys.unraisablehook"] = _since(stderr, lambda: _lose_unraisable(text))
            out["asyncio callback"] = _since(stderr, lambda: _fail_in_callback(loop, text))
            out["asyncio context"] = _since(
                stderr,
                lambda: loop.call_exception_handler(
                    {"message": text, "exception": exc, "detail": text}
                ),
            )
        restored = _hook_state(loop) == before
    finally:
        loop.close()
    return Run(outputs=out, narrator=narrator, hooks_restored=restored)


# ---------------------------------------------------------------- checks


def _forms(value: str) -> dict[str, str]:
    """Each form of ``value`` that must not survive, mapped to its name."""
    url = quote(value, safe="")
    named = {
        "raw": value,
        "URL-encoded": url,
        "URL-encoded, lower-case hex": _lower_hex(url),
        "URL-encoded, '/' kept": quote(value),
        "URL-encoded, '+' for space": quote_plus(value, safe=""),
        "JSON-escaped": json.dumps(value)[1:-1],
        "repr-escaped": repr(value)[1:-1],
    }
    forms: dict[str, str] = {}
    for name, form in named.items():
        forms.setdefault(form, name)
    return forms


# Sinks whose last line is a canonical JSON object holding the text.
_JSON_SINKS = (
    "write_json",
    "append_json",
    "json_text",
    "json_bytes",
    "open_lines",
    "open_lines gzip",
)


def _decoded_json(output: str) -> str | None:
    """The strings of the last line of ``output`` as a JSON reader sees them, if it parses."""
    try:
        value = json.loads(output.rstrip("\n").rsplit("\n", 1)[-1])
    except ValueError:
        return None  # a structural character was redacted; the encoded text is still checked
    return "\n".join(_strings(value))


def _leaks(run: Run, values: Sequence[str]) -> list[str]:
    """Each output that holds a value in any form, or holds no marker.

    JSON outputs are checked twice: as written, and decoded, which is what a
    webhook receiver, the Narrator or a report reader sees.
    """
    problems = [
        f"{sink}: no {REDACTED} in {output!r}"
        for sink, output in run.outputs.items()
        if REDACTED not in output
    ]
    checked = dict(run.outputs)
    for sink in _JSON_SINKS:
        decoded = _decoded_json(run.outputs[sink])
        if decoded is not None:
            checked[f"{sink} (decoded)"] = decoded
    forms = [_forms(value) for value in values]
    for sink, output in checked.items():
        for i, value_forms in enumerate(forms):
            found = next((name for form, name in value_forms.items() if form in output), None)
            if found is not None:
                problems.append(f"{sink}: value {i} survives ({found}) in {output!r}")
    if not run.hooks_restored:
        problems.append("a process hook was not restored after uninstall()")
    return problems


_TRACEBACK_SINKS = (
    "exception",
    "format_exception",
    "logging exc_info",
    "sys.excepthook",
    "threading.excepthook",
    "sys.unraisablehook",
    "asyncio callback",
    "asyncio context",
)


def _mismatches(run: Run, case: ExactCase) -> list[str]:
    """Each sink output that differs from ``case.text`` with each occurrence replaced."""
    want = case.expected
    card = canonical_json.dumps(_payload(want))
    quote_char = repr(case.text)[0]
    exact = {
        "echo": f"{want}\n",
        "error": f"{want}\n",
        "write_text": want,
        "write_json": f"{card}\n",
        "append_line": f"{want}\n",
        "append_json": f"{card}\n",
        "open_lines": f"{want}\n{card}\n",
        "open_lines gzip": f"{want}\n{card}\n",
        "json_text": card,
        "json_bytes": card,
        "logging": f"{want}\n{want}\n",
        "logging filter": f"{want}\n{want}\n",
    }
    heads = {
        "exception": f"{want}\n",
        "logging exc_info": f"{want}\n{want}\n",
        "asyncio context": f"{want}\ndetail: {quote_char}{want}{quote_char}\n",
    }
    last_line = f"ValueError: {want}\n"
    problems = [
        f"{sink}: {run.outputs[sink]!r} != {expected!r}"
        for sink, expected in exact.items()
        if run.outputs[sink] != expected
    ]
    problems += [
        f"{sink}: does not start with {head!r}: {run.outputs[sink]!r}"
        for sink, head in heads.items()
        if not run.outputs[sink].startswith(head)
    ]
    problems += [
        f"{sink}: last line is not {last_line!r}: {run.outputs[sink][-200:]!r}"
        for sink in _TRACEBACK_SINKS
        if not run.outputs[sink].endswith(last_line)
    ]
    narrator_want = {"MSG": want, want: [want, None]}
    if run.narrator != narrator_want:
        problems.append(f"redact_json: {run.narrator!r} != {narrator_want!r}")
    return problems


# ---------------------------------------------------------------- properties


@given(case=_exact_cases())
def test_each_occurrence_becomes_one_marker(case: ExactCase) -> None:
    run = _run_every_sink(case)
    problems = _mismatches(run, case) + _leaks(run, case.secrets.values)
    assert not problems, "\n".join(problems)


@given(case=_adversarial_cases())
def test_no_value_or_encoded_form_survives(case: Case) -> None:
    run = _run_every_sink(case)
    problems = _leaks(run, case.secrets.values)
    assert not problems, "\n".join(problems)
