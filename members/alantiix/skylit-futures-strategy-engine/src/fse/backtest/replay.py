"""Replay inputs: a live recording as one Backtester session (design §18 step 6, Req 23.9).

:func:`load_replay` turns one recording (:mod:`fse.live.recorder`) into a
:class:`ReplaySession` for ``run_backtest(mode="replay")``:

- **Availability (D8).** Every Snapshot, bar, VIX bar and dark-pool print is
  wrapped in :class:`~fse.pit.asof.Received` with its recorded receipt time,
  so :class:`~fse.pit.market_view.HistoricalInputs` makes it available at
  ``max(Observation_Time, receipt_time)``, as the live ring buffers did. Each
  series keeps recording order, so every query answers as the live view did.
  The VIX daily records go through the same slot rule as
  :meth:`LiveInputs.set_vix_daily <fse.pit.market_view.LiveInputs.set_vix_daily>`;
  the last value of each slot is used.
- **Decision_Times** are the recorded ``decision_time`` entries, which must
  strictly increase and lie in the session's trading day.
- **Guard events.** The blocks of the ``guard_event`` recorded for a
  Decision_Time (the last one, if several) are passed to ``Engine.step`` as
  ``external_blocks`` at that Decision_Time.
- **Bar phase.** The futures 1-minute bars of the configured instruments, with
  their availability, so the runner puts each bar through the bar phase at the
  first recorded Decision_Time at or after it became available.
- ``broker_event`` and ``operator_event`` entries are kept in order for the
  callers that need them; the bar phase fills orders with the Fill_Simulator
  rules, as the Paper_Broker does.

A recorded session with no Snapshot for a configured symbol and metric, or no
1-minute bar for a configured instrument, is reported as missing that data,
so the Backtester skips it (Req 18.3).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from itertools import pairwise
from pathlib import Path
from types import MappingProxyType

from fse.backtest.decision_log import map_key
from fse.data.aux_stores import VixDailyRecord
from fse.engine.step import ExternalBlock
from fse.engine.taps import BASE_INTERVAL_S, session_of
from fse.engine.types import Bar, DarkPoolPrint, EconomicEvent, Metric, Snapshot
from fse.live.recorder import (
    RecordedInput,
    Recording,
    RecordingError,
    decode_bar,
    decode_blocks,
    decode_dark_pool,
    decode_snapshot,
    decode_vix,
    read_recording,
    recording_session,
)
from fse.pit.asof import Received, available_at
from fse.pit.market_view import MAP_METRICS, HistoricalInputs, vix_daily_slot
from fse.timekit import Instant

__all__ = ["ReplaySession", "load_replay", "replay_files"]


@dataclass(frozen=True, slots=True)
class ReplaySession:
    """One recorded session, ready for the Backtester's loop (see the module notes)."""

    session: date
    inputs: HistoricalInputs
    bars: tuple[Bar, ...]
    """The configured instruments' 1-minute bars, in (open, instrument) order."""
    bar_available: tuple[Instant, ...]
    """When each of ``bars`` became available, in the same order."""
    decision_times: tuple[Instant, ...]
    blocks: Mapping[Instant, tuple[ExternalBlock, ...]]
    snapshots: tuple[Snapshot, ...]
    """The configured view's Snapshots of the configured symbols and metrics, recorded order."""
    missing_snapshots: tuple[str, ...]
    missing_bars: tuple[str, ...]
    broker_events: tuple[RecordedInput, ...] = ()
    operator_events: tuple[RecordedInput, ...] = ()
    truncated: bool = False
    path: Path | None = field(default=None, compare=False)


def replay_files(directory: Path) -> dict[date, Path]:
    """Every ``{session}.jsonl.gz`` recording in ``directory``, by session date."""
    folder = Path(directory).expanduser()
    if not folder.is_dir():
        raise RecordingError(f"{folder} is not a directory of recordings")
    out: dict[date, Path] = {}
    for path in sorted(folder.iterdir()):
        session = recording_session(path)
        if session is not None and path.is_file():
            out[session] = path
    return out


def load_replay(
    recording: Recording | Path,
    *,
    session: date,
    symbols: Sequence[str],
    view_id: str,
    instruments: Sequence[str],
    events: Iterable[EconomicEvent] = (),
    metrics: Sequence[Metric] = MAP_METRICS,
) -> ReplaySession:
    """The :class:`ReplaySession` of ``recording`` for ``session`` (see the module notes)."""
    rec = recording if isinstance(recording, Recording) else read_recording(Path(recording))
    snapshots: list[Received[Snapshot]] = []
    bars: list[Received[Bar]] = []
    vix_bars: list[Received[Bar]] = []
    dark_pool: dict[str, list[Received[DarkPoolPrint]]] = {}
    today: tuple[VixDailyRecord | None, VixDailyRecord | None] = (None, None)
    prior: tuple[VixDailyRecord | None, VixDailyRecord | None] = (None, None)
    times: list[Instant] = []
    blocks: dict[Instant, tuple[ExternalBlock, ...]] = {}
    broker: list[RecordedInput] = []
    operator: list[RecordedInput] = []
    for number, entry in enumerate(rec.entries, start=1):
        at = entry.received_ns
        try:
            match entry.kind:
                case "snapshot":
                    snapshots.append(Received(decode_snapshot(entry.payload), at))
                case "bar":
                    bars.append(Received(decode_bar(entry.payload), at))
                case "dark_pool":
                    ticker, prints = decode_dark_pool(entry.payload)
                    dark_pool.setdefault(ticker, []).extend(Received(p, at) for p in prints)
                case "vix":
                    value = decode_vix(entry.payload)
                    if isinstance(value, Bar):
                        vix_bars.append(Received(value, at))
                    else:
                        today = vix_daily_slot(*today, value[0], at)
                        prior = vix_daily_slot(*prior, value[1], at)
                case "guard_event":
                    t, found = decode_blocks(entry.payload)
                    blocks[t] = found
                case "decision_time":
                    t = _decision_time(entry, session)
                    if times and t <= times[-1]:
                        raise RecordingError(f"Decision_Time {t} does not follow {times[-1]}")
                    times.append(t)
                case "broker_event":
                    broker.append(entry)
                case "operator_event":
                    operator.append(entry)
        except (RecordingError, ValueError) as exc:
            raise RecordingError(f"{rec.path} entry {number}: {exc}") from None
    inputs = HistoricalInputs(
        symbols=symbols,
        view_id=view_id,
        metrics=metrics,
        snapshots=snapshots,
        bars=bars,
        vix_today=today[0],
        vix_prior=prior[0],
        vix_bars=vix_bars,
        dark_pool=dark_pool,
        events=events,
    )
    keys = set(inputs.keys)
    kept = tuple(
        r.record
        for r in snapshots
        if (r.record.symbol, r.record.metric) in keys and r.record.view_id == view_id
    )
    futures = sorted(
        (
            (r.record, available_at(r.record.close_ns, r.received_ns))
            for r in bars
            if r.record.instrument in instruments and r.record.interval_s == BASE_INTERVAL_S
        ),
        key=lambda pair: (pair[0].open_ns, pair[0].instrument),
    )
    for (a, _), (b, _) in pairwise(futures):
        if (a.open_ns, a.instrument) == (b.open_ns, b.instrument):
            raise RecordingError(
                f"{rec.path}: the {a.instrument} bar at {a.open_ns} is recorded twice"
            )
    seen = {(s.symbol, s.metric) for s in kept}
    with_bars = {b.instrument for b, _ in futures}
    recorded = frozenset(times)
    return ReplaySession(
        session=session,
        inputs=inputs,
        bars=tuple(b for b, _ in futures),
        bar_available=tuple(a for _, a in futures),
        decision_times=tuple(times),
        blocks=MappingProxyType({t: v for t, v in blocks.items() if t in recorded}),
        snapshots=kept,
        missing_snapshots=tuple(map_key(s, m) for s, m in inputs.keys if (s, m) not in seen),
        missing_bars=tuple(i for i in instruments if i not in with_bars),
        broker_events=tuple(broker),
        operator_events=tuple(operator),
        truncated=rec.truncated,
        path=rec.path,
    )


def _decision_time(entry: RecordedInput, session: date) -> Instant:
    payload = entry.payload
    if not isinstance(payload, dict) or set(payload) != {"t"}:
        raise RecordingError("a decision_time payload is {t}")
    t = payload["t"]
    if isinstance(t, bool) or not isinstance(t, int) or t != entry.received_ns:
        raise RecordingError("a decision_time is recorded with received_ns equal to t")
    if session_of(t) != session:
        raise RecordingError(f"Decision_Time {t} is not in the {session} trading day")
    return t
