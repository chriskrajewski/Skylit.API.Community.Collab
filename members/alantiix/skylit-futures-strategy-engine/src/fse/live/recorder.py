"""The live recorder: every live input with its receipt time (design §23 "Recorder", Req 23.8).

A recording is one gzip JSON Lines file per session,
``recordings/{session}.jsonl.gz`` (:func:`recording_path`), written through
the Log_Writer (:meth:`~fse.logio.LogWriter.open_lines`, so each line is
redacted before it is written). Each line is one canonical JSON object
``{kind, received_ns, payload}`` (design "Storage formats"), with ``kind`` one
of :data:`RECORDING_KINDS`:

- ``snapshot``: a :class:`~fse.engine.types.Snapshot`, every field as received;
- ``bar``: a futures :class:`~fse.engine.types.Bar`;
- ``dark_pool``: ``{ticker, prints}``, one fetch of a ticker (an empty list
  still marks the ticker as fetched);
- ``vix``: ``{daily: {today, prior}}`` (two
  :class:`~fse.data.aux_stores.VixDailyRecord` or ``null``) or ``{bar}``, a
  1-minute VIX bar;
- ``broker_event`` and ``operator_event``: the event as a JSON object;
- ``guard_event``: ``{at, blocks}``, the
  :class:`~fse.engine.step.ExternalBlock` list passed to the step at Decision_Time ``at``;
- ``decision_time``: ``{t}``, one Decision_Time, recorded with
  ``received_ns = t``.

Only payloads go into a line: no request, header, URL or key is ever passed
to the recorder, and the Log_Writer's Redactor removes any registered secret
value that a payload might still hold. The file is flushed after every
``decision_time`` line, so a crash loses at most the inputs of the Decision_Time
in progress.

:func:`read_recording` reads a recording back for replay
(:mod:`fse.backtest.replay`). A file cut off mid-member (the process died
before closing it) is read up to its last whole line and marked
``truncated``. Any other problem (not gzip, a line that is not a recording
entry) is a :class:`RecordingError` (exit 4) naming the file and line.
The ``decode_*`` functions rebuild each payload's value exactly: floats are
written by ``repr`` and read back to the same value.
"""

from __future__ import annotations

import json
import zlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import TracebackType
from typing import ClassVar, Final, Literal, Self, cast

from fse.data.aux_stores import VixDailyRecord
from fse.engine.step import ExternalBlock
from fse.engine.types import Bar, DarkPoolPrint, Snapshot
from fse.logio import LogWriter
from fse.logio.canonical_json import JsonValue
from fse.timekit import Instant

__all__ = [
    "RECORDINGS_DIR_NAME",
    "RECORDING_KINDS",
    "RecordedInput",
    "Recorder",
    "Recording",
    "RecordingError",
    "RecordingKind",
    "decode_bar",
    "decode_blocks",
    "decode_dark_pool",
    "decode_snapshot",
    "decode_vix",
    "read_recording",
    "recording_path",
    "recording_session",
]

type RecordingKind = Literal[
    "snapshot",
    "bar",
    "broker_event",
    "dark_pool",
    "vix",
    "guard_event",
    "operator_event",
    "decision_time",
]

RECORDING_KINDS: Final[tuple[RecordingKind, ...]] = (
    "snapshot",
    "bar",
    "broker_event",
    "dark_pool",
    "vix",
    "guard_event",
    "operator_event",
    "decision_time",
)
RECORDINGS_DIR_NAME: Final = "recordings"
_SUFFIX: Final = ".jsonl.gz"
_GZIP_WBITS: Final = 31  # zlib: a gzip header and trailer


class RecordingError(ValueError):
    """A recording that cannot be read: exit 4 (data or I/O failure)."""

    exit_code: ClassVar[int] = 4


def recording_path(root: Path, session: date) -> Path:
    """``root/recordings/{session}.jsonl.gz``; ``root`` is the live output directory."""
    return Path(root) / RECORDINGS_DIR_NAME / f"{session.isoformat()}{_SUFFIX}"


def recording_session(path: Path) -> date | None:
    """The session date in a recording's file name, or ``None`` for another name."""
    name = Path(path).name
    if not name.endswith(_SUFFIX):
        return None
    try:
        return date.fromisoformat(name[: -len(_SUFFIX)])
    except ValueError:
        return None


class Recorder:
    """Appends one session's live inputs to its recording (see the module notes)."""

    __slots__ = ("_sink",)

    def __init__(self, writer: LogWriter, path: Path) -> None:
        self._sink = writer.open_lines(path, compress=True)

    @property
    def path(self) -> Path:
        return self._sink.path

    def _write(self, kind: RecordingKind, received_ns: Instant, payload: object) -> None:
        if isinstance(received_ns, bool) or not isinstance(received_ns, int):
            raise ValueError(f"received_ns must be an integer Instant, got {received_ns!r}")
        self._sink.write_json({"kind": kind, "received_ns": received_ns, "payload": payload})

    def snapshot(self, snapshot: Snapshot, received_ns: Instant) -> None:
        self._write("snapshot", received_ns, snapshot)

    def bar(self, bar: Bar, received_ns: Instant) -> None:
        self._write("bar", received_ns, bar)

    def dark_pool(self, ticker: str, prints: Iterable[DarkPoolPrint], received_ns: Instant) -> None:
        self._write("dark_pool", received_ns, {"ticker": ticker, "prints": list(prints)})

    def vix_daily(
        self, today: VixDailyRecord | None, prior: VixDailyRecord | None, received_ns: Instant
    ) -> None:
        self._write("vix", received_ns, {"daily": {"today": today, "prior": prior}})

    def vix_bar(self, bar: Bar, received_ns: Instant) -> None:
        self._write("vix", received_ns, {"bar": bar})

    def broker_event(self, event: Mapping[str, object], received_ns: Instant) -> None:
        self._write("broker_event", received_ns, dict(event))

    def operator_event(self, event: Mapping[str, object], received_ns: Instant) -> None:
        self._write("operator_event", received_ns, dict(event))

    def guard_event(
        self, at: Instant, blocks: Iterable[ExternalBlock], received_ns: Instant
    ) -> None:
        self._write("guard_event", received_ns, {"at": at, "blocks": list(blocks)})

    def decision_time(self, t: Instant) -> None:
        """Record Decision_Time ``t`` and flush the file."""
        self._write("decision_time", t, {"t": t})
        self._sink.flush()

    def flush(self) -> None:
        self._sink.flush()

    def close(self) -> None:
        self._sink.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"Recorder({str(self.path)!r})"


# ---------------------------------------------------------------- reading


@dataclass(frozen=True, slots=True)
class RecordedInput:
    """One recording line, in file order."""

    kind: RecordingKind
    received_ns: Instant
    payload: JsonValue


@dataclass(frozen=True, slots=True)
class Recording:
    """A whole recording; ``truncated`` when the file ended inside a gzip member."""

    path: Path
    entries: tuple[RecordedInput, ...]
    truncated: bool


def _lines(path: Path) -> tuple[list[bytes], bool]:
    """The whole lines of a gzip recording, and whether it was cut off."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise RecordingError(f"{path}: cannot read the recording ({type(exc).__name__})") from None
    out = bytearray()
    truncated = False
    while data:  # one gzip member per Recorder session
        member = zlib.decompressobj(wbits=_GZIP_WBITS)
        try:
            out += member.decompress(data)
        except zlib.error as exc:
            raise RecordingError(f"{path}: not a gzip recording ({exc})") from None
        if not member.eof:
            truncated = True
            break
        data = member.unused_data
    lines = bytes(out).split(b"\n")
    if lines.pop():  # text after the last newline is a partial line
        truncated = True
    return lines, truncated


def read_recording(path: Path) -> Recording:
    """Every whole line of the recording at ``path`` (see the module notes)."""
    p = Path(path)
    lines, truncated = _lines(p)
    entries: list[RecordedInput] = []
    for number, line in enumerate(lines, start=1):
        try:
            obj = json.loads(line)
        except ValueError:
            raise RecordingError(f"{p} line {number}: not a JSON object") from None
        if not isinstance(obj, dict) or set(obj) != {"kind", "received_ns", "payload"}:
            raise RecordingError(f"{p} line {number}: not a recording entry")
        kind, received = obj["kind"], obj["received_ns"]
        if kind not in RECORDING_KINDS:
            raise RecordingError(f"{p} line {number}: unknown kind {kind!r}")
        if isinstance(received, bool) or not isinstance(received, int):
            raise RecordingError(f"{p} line {number}: received_ns is not an integer")
        entries.append(RecordedInput(kind, received, cast("JsonValue", obj["payload"])))
    return Recording(p, tuple(entries), truncated)


# ---------------------------------------------------------------- payloads


def _obj(payload: JsonValue, what: str) -> dict[str, JsonValue]:
    if not isinstance(payload, dict):
        raise RecordingError(f"a {what} payload must be a JSON object")
    return payload


def _floats(value: JsonValue, what: str) -> tuple[float, ...]:
    if not isinstance(value, list):
        raise RecordingError(f"{what} must be a list")
    return tuple(cast("float", v) for v in value)


def _build[T](kind: type[T], fields: dict[str, JsonValue], what: str) -> T:
    try:
        return kind(**fields)
    except (TypeError, ValueError) as exc:
        raise RecordingError(f"a {what} payload is not valid: {exc}") from None


def decode_snapshot(payload: JsonValue) -> Snapshot:
    """The :class:`Snapshot` of a ``snapshot`` payload."""
    fields = dict(_obj(payload, "snapshot"))
    for name in ("strikes", "values"):
        fields[name] = cast("JsonValue", _floats(fields.get(name), f"snapshot {name}"))
    types = fields.get("node_types")
    if types is not None:
        if not isinstance(types, list):
            raise RecordingError("snapshot node_types must be a list or null")
        fields["node_types"] = cast("JsonValue", tuple(types))
    exps = fields.get("expirations")
    if not isinstance(exps, list):
        raise RecordingError("snapshot expirations must be a list")
    fields["expirations"] = cast("JsonValue", tuple(exps))
    return _build(Snapshot, fields, "snapshot")


def decode_bar(payload: JsonValue) -> Bar:
    """The :class:`Bar` of a ``bar`` payload (or of a ``vix`` payload's ``bar``)."""
    return _build(Bar, dict(_obj(payload, "bar")), "bar")


def decode_dark_pool(payload: JsonValue) -> tuple[str, tuple[DarkPoolPrint, ...]]:
    """The ticker and prints of a ``dark_pool`` payload."""
    obj = _obj(payload, "dark_pool")
    ticker, prints = obj.get("ticker"), obj.get("prints")
    if not isinstance(ticker, str) or not isinstance(prints, list):
        raise RecordingError("a dark_pool payload needs a ticker and a prints list")
    return ticker, tuple(
        _build(DarkPoolPrint, dict(_obj(p, "dark-pool print")), "dark-pool print") for p in prints
    )


def _vix_record(value: JsonValue) -> VixDailyRecord | None:
    if value is None:
        return None
    fields = dict(_obj(value, "VIX daily record"))
    session = fields.get("session")
    if not isinstance(session, str):
        raise RecordingError("a VIX daily record needs a session date")
    try:
        day = date.fromisoformat(session)
    except ValueError:
        raise RecordingError(f"a VIX daily record has a bad session date {session!r}") from None
    fields["session"] = cast("JsonValue", day)
    return _build(VixDailyRecord, fields, "VIX daily record")


def decode_vix(
    payload: JsonValue,
) -> tuple[VixDailyRecord | None, VixDailyRecord | None] | Bar:
    """A ``vix`` payload: the (today, prior) daily records, or a 1-minute VIX bar."""
    obj = _obj(payload, "vix")
    if set(obj) == {"bar"}:
        return decode_bar(obj["bar"])
    if set(obj) == {"daily"}:
        daily = _obj(obj["daily"], "vix daily")
        if set(daily) != {"today", "prior"}:
            raise RecordingError("a vix daily payload needs today and prior")
        return _vix_record(daily["today"]), _vix_record(daily["prior"])
    raise RecordingError("a vix payload holds either bar or daily")


def decode_blocks(payload: JsonValue) -> tuple[Instant, tuple[ExternalBlock, ...]]:
    """The Decision_Time and the blocks of a ``guard_event`` payload."""
    obj = _obj(payload, "guard_event")
    at, blocks = obj.get("at"), obj.get("blocks")
    if isinstance(at, bool) or not isinstance(at, int) or not isinstance(blocks, list):
        raise RecordingError("a guard_event payload needs an integer at and a blocks list")
    return at, tuple(
        _build(ExternalBlock, dict(_obj(b, "external block")), "external block") for b in blocks
    )
