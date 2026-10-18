"""The decision log: one canonical JSON line per Decision_Time (design §18, Req 18.6-18.7).

:func:`decision_entry` turns the :class:`~fse.engine.step.DecisionPayload` of
one Decision_Time into the entry below; :class:`DecisionLog` appends one entry
per payload through the Log_Writer, so every line is redacted before it
reaches disk (Req 1.9), and refuses a payload that is not after the previous
one, so the file is in Decision_Time order (Req 18.6).

Entry fields (Req 18.7):

- ``session`` (``YYYY-MM-DD``), ``t`` and ``t_ny``: the session and the
  Decision_Time;
- ``map``: one object per configured (symbol, metric), keyed
  ``"SYMBOL/metric"``: ``asOf`` and ``asOf_ny``, ``king``, ``floor`` and
  ``ceiling`` of its Snapshot, or ``{"missing": true}`` with no Snapshot at or
  before ``t``; ``snapshot_age_ns``;
- ``futures``: each configured instrument's Futures_Price in ticks;
- ``regime`` and ``map_grade``: the labels; ``regime_measures`` the
  measurements behind the Regime (``null`` with no Regime);
- ``setups``: each Candidate_Setup in detection order with its ``key``, the
  setup fields, ``gates`` (every Gate's ``id``, ``result``, ``measured`` and
  ``threshold``; disabled Gates appear as ``disabled``), ``rejections`` (the
  Rejection_Reasons), ``grade``, ``sizing`` and ``placement``; ``skips`` the
  Detection_Skips;
- ``orders``: the orders submitted, modified or cancelled at ``t``;
  ``management``: the Order_Planner events behind them;
- ``fills``: the fills and exits after the previous Decision_Time and at or
  before ``t``; ``bar_events``: the other bar-phase events (stop moves, the
  loss stop, rejected entries);
- ``external_blocks``, ``lockouts`` and ``cards`` (Finding_Card triggers).

Values are canonical JSON (``fse.logio.canonical_json``): sorted keys,
Instants as ints, Decimals as strings, dates as ISO dates. A missing value is
the engine's sentinel object (``{"reason": ...}`` for ``Unavailable``,
``{"names": [...]}`` for ``MissingInput``). An item of a union-typed list
(``orders``, ``management``, ``bar_events``, ``sizing``, ``placement``)
carries its class name as ``type``. Equal payloads give equal bytes, so
reruns give byte-identical logs (Req 18.5).

The file must not exist when the log is opened, so two runs never share one
log. Each line is flushed to the OS as it is written; :meth:`DecisionLog.close`
fsyncs. Path checks (Req 1.11) belong to the caller that chooses the run
directory (see :func:`fse.backtest.manifest.run_manifest`).
"""

from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Final, Self

from fse.engine.gates.registry import GateResult
from fse.engine.regime import RegimeResult
from fse.engine.step import DecisionPayload, MapEntryLog, SetupDecision
from fse.logio import LineSink, LogWriter
from fse.logio.canonical_json import JsonValue, instant_fields, to_jsonable
from fse.timekit import Instant

__all__ = ["DECISION_LOG_FILE_NAME", "DecisionLog", "decision_entry", "map_key"]

DECISION_LOG_FILE_NAME: Final = "decision_log.jsonl"

_TYPE: Final = "type"


def map_key(symbol: str, metric: str) -> str:
    """The ``map`` key of one configured (symbol, metric): ``"SPX/gamma"``."""
    return f"{symbol}/{metric}"


def decision_entry(payload: DecisionPayload) -> dict[str, JsonValue]:
    """The decision-log entry of ``payload`` as plain JSON values (Req 18.7)."""
    regime: JsonValue
    measures: JsonValue
    if isinstance(payload.regime, RegimeResult):
        regime, measures = payload.regime.regime, _regime_measures(payload.regime)
    else:
        regime, measures = to_jsonable(payload.regime), None
    return {
        "session": payload.session.isoformat(),
        **instant_fields("t", payload.t),
        "map": {map_key(e.symbol, e.metric): _map_entry(e) for e in payload.map_entries},
        "snapshot_age_ns": to_jsonable(payload.snapshot_age_ns),
        "futures": {f.instrument: to_jsonable(f.price) for f in payload.futures},
        "regime": regime,
        "regime_measures": measures,
        "map_grade": to_jsonable(payload.map_grade),
        "setups": [_setup(d) for d in payload.setups],
        "skips": [to_jsonable(s) for s in payload.skips],
        "orders": [_tagged(i) for i in payload.intents],
        "management": [_tagged(e) for e in payload.management],
        "fills": [to_jsonable(f) for f in payload.fills],
        "bar_events": [_tagged(e) for e in payload.bar_events],
        "external_blocks": [to_jsonable(b) for b in payload.external_blocks],
        "lockouts": [to_jsonable(lo) for lo in payload.lockouts],
        "cards": to_jsonable(payload.cards),
    }


class DecisionLog:
    """Appends one :func:`decision_entry` line per Decision_Time through a :class:`LogWriter`."""

    __slots__ = ("_closed", "_count", "_last_t", "_path", "_sink")

    def __init__(self, writer: LogWriter, path: str | Path) -> None:
        """Create the log file; ``FileExistsError`` when ``path`` already exists."""
        self._path = Path(path)
        if self._path.exists():
            raise FileExistsError(
                f"a decision log already exists at {self._path}; use a new run directory"
            )
        self._sink: LineSink = writer.open_lines(self._path)
        self._count = 0
        self._last_t: Instant | None = None
        self._closed = False

    @property
    def path(self) -> Path:
        return self._path

    @property
    def count(self) -> int:
        """Entries written so far."""
        return self._count

    @property
    def last_t(self) -> Instant | None:
        """The Decision_Time of the latest entry, or ``None`` before the first."""
        return self._last_t

    def write(self, payload: DecisionPayload) -> None:
        """Write the entry of ``payload`` and flush it to the OS.

        Raises ``ValueError`` when the log is closed or ``payload.t`` is not
        after the previous entry's Decision_Time.
        """
        if self._closed:
            raise ValueError(f"the decision log {self._path} is closed")
        if self._last_t is not None and payload.t <= self._last_t:
            raise ValueError(
                f"Decision_Time {payload.t} is not after the previous entry's {self._last_t}"
            )
        entry = decision_entry(payload)
        self._sink.write_json(entry)
        self._sink.flush()
        self._last_t = payload.t
        self._count += 1

    def close(self) -> None:
        """Fsync and close the file. Safe to call more than once."""
        if self._closed:
            return
        self._closed = True
        try:
            self._sink.flush(fsync=True)
        finally:
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
        return f"DecisionLog({str(self._path)!r}, {self._count} entries)"


# ---------------------------------------------------------------- entry parts


def _map_entry(e: MapEntryLog) -> dict[str, JsonValue]:
    if e.missing or e.as_of_ns is None:
        return {"missing": True}
    return {
        **instant_fields("asOf", e.as_of_ns),
        "king": e.king,
        "floor": e.floor,
        "ceiling": e.ceiling,
    }


def _regime_measures(r: RegimeResult) -> dict[str, JsonValue]:
    return {
        "raw_gex": r.raw_gex,
        "raw_vex": r.raw_vex,
        "normalized_gex": r.normalized_gex,
        "normalized_vex": r.normalized_vex,
        "net_gex": r.net_gex,
        "vanna_withheld": r.vanna_withheld,
    }


def _gate(g: GateResult) -> dict[str, JsonValue]:
    return {
        "id": g.gate_id,
        "result": g.result,
        "measured": to_jsonable(g.measured),
        "threshold": to_jsonable(g.threshold),
    }


def _setup(d: SetupDecision) -> dict[str, JsonValue]:
    c, ev = d.setup, d.evaluation
    return {
        "key": to_jsonable(c.key),
        "detector_id": c.detector_id,
        "entry": c.entry,
        "stop": c.stop,
        "targets": list(c.targets),
        "exit_mode": c.exit_mode,
        "source": to_jsonable(c.source),
        "inputs": to_jsonable(c.inputs),
        "gates": [_gate(g) for g in ev.results],
        "rejections": [to_jsonable(r) for r in ev.rejections],
        "grade": ev.grade,
        "sizing": None if d.sizing is None else _tagged(d.sizing),
        "placement": None if d.placement is None else _tagged(d.placement),
    }


def _tagged(obj: object) -> dict[str, JsonValue]:
    """A dataclass value as its fields plus ``type``: its class name."""
    fields = to_jsonable(obj)
    if not isinstance(fields, dict) or _TYPE in fields:
        raise TypeError(f"cannot tag {type(obj).__qualname__} with a {_TYPE!r} field")
    return {_TYPE: type(obj).__name__, **fields}
