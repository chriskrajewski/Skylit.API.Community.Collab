"""Generate the BC-2 replay inputs and capture the pre-feature golden (design §12).

Captured from base commit ``0830838`` (``feat(paper_ops): --partial mode for
the daily check``), before any paper-account or journal code existed. The
golden is not re-captured by CI: regenerating it is a reviewed change that
updates ``BASE_COMMIT`` and the comment record.

Run from the project root::

    PYTHONPATH=src:. python tests/unit/fixtures/make_replay_v1_golden.py [--out DIR]

It writes ``replay_bars_v1.jsonl`` (seeded 60 s MNQ and MES bars over Globex
days Tue 2026-10-13 and Wed 2026-10-14, one 18:00 rotation between them) and
``replay_v1_golden.jsonl`` next to this file, or into ``--out``.

The two-phase driver :func:`run_two_phase` is importable, so the BC-2 tests run
the same code over the committed bars:

- phase 1 starts Tue 09:29 and feeds every bar up to Wed 09:59 as live
  ``BarsEvent`` s, then stops the host at Wed 10:00:30;
- phase 2 starts a new host on the same state dir at Wed 10:20:30 with a data
  plane whose recording also holds the bars the host missed (Wed 10:00-10:19),
  so ``_startup`` replays them, then feeds Wed 10:20-10:59 live.

Golden format: line 1 is the comment record ``{"base_commit", "generator",
"files"}`` (``files``: sorted paths under the state dir and the two run dirs
after phase 2, run dirs written ``<run1>``/``<run2>``). Then every record of
``replay_a``, then of ``replay_b``, each plugin's in its own delivery order: its
``intent_accepted``/``intent_rejected`` ``plugin_log.jsonl`` lines without
``t_ns`` (``"type": "log"``) and every ``OrderUpdate`` it received
(``"type": "update"``).
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import random
import sys
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from datetime import time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from fse.calendars import load_calendars
from fse.engine.types import Bar
from fse.live.host.config import parse_runner_config
from fse.live.host.host import (
    PLUGIN_LOG_FILE_NAME,
    BarsEvent,
    HostPaths,
    HostResources,
    InlineBlockingRunner,
    PluginHost,
)
from fse.live.plugins import OrderUpdate
from fse.logio import LogWriter
from fse.logio.redact import Redactor
from fse.secrets.env import EnvView
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND, Instant, ny_datetime, ny_instant
from tests.fakes.clock import FakeClock
from tests.fakes.dataplane import CALENDAR_DIR, PROJECT_DIR, FakeDataPlane, host_entry
from tests.unit.fixtures.replay_plugins import DAY1, DAY2, ReplayA, ReplayB

__all__ = [
    "BARS_PATH",
    "BASE_COMMIT",
    "GOLDEN_PATH",
    "PLUGIN_IDS",
    "generate_bars",
    "load_bars",
    "read_golden",
    "run_two_phase",
]

BASE_COMMIT: Final = "0830838"
GENERATOR: Final = "make_replay_v1_golden.py"
HERE: Final = Path(__file__).resolve().parent
BARS_PATH: Final = HERE / "replay_bars_v1.jsonl"
GOLDEN_PATH: Final = HERE / "replay_v1_golden.jsonl"
SEED: Final = 20261013
PLUGIN_IDS: Final = ("replay_a", "replay_b")
RUN_DIRS: Final = ("<run1>", "<run2>")

TUE: Final = DAY1

PHASE1_START: Final = ny_instant(TUE, time(9, 29))
PHASE1_UNTIL: Final = ny_instant(DAY2, time(10, 0, 30))
PHASE2_START: Final = ny_instant(DAY2, time(10, 20, 30))
PHASE2_UNTIL: Final = ny_instant(DAY2, time(11, 0, 30))
MISSED: Final = (ny_instant(DAY2, time(10, 0)), ny_instant(DAY2, time(10, 20)))
"""``[first, end)`` bar opens the host is down for."""

RECEIVE_DELAY_NS: Final = 2 * NS_PER_SECOND
START_TICKS: Final = {"MNQ": 80_000, "MES": 23_000}

# Wall-clock bar windows ``(date, start, end)`` (bar opens, end exclusive).
WINDOWS: Final = (
    (TUE, time(9, 30), time(11, 0)),
    (TUE, time(16, 30), time(17, 0)),
    (TUE, time(18, 0), time(18, 30)),  # Globex day 2 opens
    (DAY2, time(9, 30), time(11, 0)),
)

# Drift in ticks per bar, ``(instrument, date, from, to)`` inclusive of both.
DRIFT: Final = (
    ("MNQ", TUE, time(9, 36), time(9, 45), 4),  # replay_a a1 reaches its target
    ("MNQ", TUE, time(10, 0), time(10, 25), -12),  # a3 fills, then stops out
    ("MNQ", DAY2, time(10, 0), time(10, 10), 4),  # a5 (replayed) reaches its target
    ("MNQ", DAY2, time(10, 31), time(10, 40), 3),  # a6 (live) reaches its target
)

ENTRIES: Final[Mapping[str, Mapping[str, Any]]] = {
    "replay_a": {
        "module": "tests.unit.fixtures.replay_plugins:ReplayA",
        "risk": {"max_contracts": 2, "max_daily_loss": "150.00"},
    },
    "replay_b": {
        "module": "tests.unit.fixtures.replay_plugins:ReplayB",
        "risk": {"max_contracts": 4},
    },
}
CLASSES: Final = {"replay_a": ReplayA, "replay_b": ReplayB}


# ---------------------------------------------------------------- bars


def _minutes() -> Iterator[Instant]:
    for d, start, end in WINDOWS:
        t = ny_instant(d, start)
        stop = ny_instant(d, end)
        while t < stop:
            yield t
            t += NS_PER_MINUTE


def _drift(instrument: str, open_ns: Instant) -> int:
    local = ny_datetime(open_ns)
    for inst, d, lo, hi, ticks in DRIFT:
        if inst == instrument and local.date() == d and lo <= local.time() <= hi:
            return ticks
    return 0


def generate_bars(seed: int = SEED) -> list[dict[str, int | str]]:
    """The seeded bar rows (``{instrument, open_ns, o_t, h_t, l_t, c_t, v}``), time-ordered."""
    rng = random.Random(seed)
    last = dict(START_TICKS)
    rows: list[dict[str, int | str]] = []
    for open_ns in _minutes():
        for instrument in ("MES", "MNQ"):
            o = last[instrument]
            c = o + _drift(instrument, open_ns) + rng.randint(-2, 2)
            h = max(o, c) + rng.randint(0, 3)
            low = min(o, c) - rng.randint(0, 3)
            v = rng.randint(50, 500)
            rows.append(
                {
                    "instrument": instrument,
                    "open_ns": open_ns,
                    "o_t": o,
                    "h_t": h,
                    "l_t": low,
                    "c_t": c,
                    "v": v,
                }
            )
            last[instrument] = c
    return rows


def _bar(row: Mapping[str, Any]) -> Bar:
    instrument = str(row["instrument"])
    o, h, low, c = (int(row[k]) for k in ("o_t", "h_t", "l_t", "c_t"))
    open_ns = int(row["open_ns"])
    return Bar(
        instrument=instrument,
        contract=f"{instrument}Z6",
        interval_s=60,
        open_ns=open_ns,
        close_ns=open_ns + NS_PER_MINUTE,
        o=o / 4,
        h=h / 4,
        l=low / 4,
        c=c / 4,
        v=float(row["v"]),
        o_t=o,
        h_t=h,
        l_t=low,
        c_t=c,
        source="projectx",
    )


def load_bars(path: Path = BARS_PATH) -> list[Bar]:
    return [_bar(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines()]


def _events(bars: Sequence[Bar]) -> list[tuple[Instant, BarsEvent]]:
    """One ``BarsEvent`` per minute (both instruments), received 2 s after the close."""
    by_open: dict[Instant, list[Bar]] = {}
    for bar in bars:
        by_open.setdefault(bar.open_ns, []).append(bar)
    out: list[tuple[Instant, BarsEvent]] = []
    for open_ns in sorted(by_open):
        batch = tuple(sorted(by_open[open_ns], key=lambda b: b.instrument))
        received = open_ns + NS_PER_MINUTE + RECEIVE_DELAY_NS
        out.append((received, BarsEvent(batch, received)))
    return out


# ---------------------------------------------------------------- the driver


class _CapturingWriter(LogWriter):
    """Records every ``plugin_log.jsonl`` line, in write order, into ``records``."""

    def __init__(self, records: list[tuple[str, dict[str, Any]]]) -> None:
        super().__init__(Redactor(), stdout=io.StringIO(), stderr=io.StringIO())
        self._records = records

    def append_json(self, path: Any, obj: object, *, fsync: bool = False) -> None:
        super().append_json(path, obj, fsync=fsync)
        if (
            Path(path).name == PLUGIN_LOG_FILE_NAME
            and isinstance(obj, dict)
            and obj.get("kind") in ("intent_accepted", "intent_rejected")
        ):
            line = {k: v for k, v in obj.items() if k != "t_ns"}
            self._records.append((str(obj["plugin"]), {"type": "log", **line}))


def _update_record(update: OrderUpdate) -> dict[str, Any]:
    fill = update.fill
    return {
        "type": "update",
        "tag": update.tag,
        "status": update.status,
        "reason": update.reason,
        "role": update.role,
        "at": update.at,
        "fill": None
        if fill is None
        else {
            "client_id": fill.client_id,
            "bar_open_ns": fill.bar_open_ns,
            "price_t": fill.price,
            "qty": fill.qty,
            "fees": str(fill.fees),
        },
    }


def _host(
    tmp_path: Path,
    run_name: str,
    clock: FakeClock,
    plane: FakeDataPlane,
    records: list[tuple[str, dict[str, Any]]],
    config_kwargs: Mapping[str, Any],
    entry_extra: Mapping[str, Any],
    nets: list[Decimal] | None = None,
) -> PluginHost:
    plugins = [host_entry(pid, **{**ENTRIES[pid], **entry_extra}) for pid in PLUGIN_IDS]
    doc: dict[str, Any] = {"version": 1, "plugins": plugins, **config_kwargs}
    cfg = parse_runner_config(doc, Path("runner.yaml"))
    today = ny_datetime(clock.now()).date()
    host = PluginHost(
        cfg,
        CLASSES,
        dataplane=plane,
        clock=clock,
        writer=_CapturingWriter(records),
        paths=HostPaths(
            state_root=tmp_path / "live-state",
            run_dir=tmp_path / "runs" / run_name,
            cache_dir=tmp_path / "cache",
            calendar_dir=CALENDAR_DIR,
            project_dir=PROJECT_DIR,
        ),
        calendars=load_calendars(CALENDAR_DIR, today - timedelta(days=3), today + timedelta(7)),
        resources=HostResources(env=EnvView({}, {})),
        blocking=InlineBlockingRunner(),
    )
    for pid in PLUGIN_IDS:
        plugin = host.plugin(pid)
        assert isinstance(plugin, ReplayA | ReplayB)

        def on_update(update: OrderUpdate, ctx: Any, pid: str = pid) -> None:
            records.append((pid, _update_record(update)))
            if nets is not None and update.status == "closed" and update.trade is not None:
                nets.append(update.trade.net)

        plugin.handlers["on_order_update"] = on_update
    return host


def _files(tmp_path: Path) -> list[str]:
    names = {"run1": RUN_DIRS[0], "run2": RUN_DIRS[1]}
    out: list[str] = []
    for root in (tmp_path / "live-state", tmp_path / "runs"):
        for path in root.rglob("*"):
            if path.is_file():
                parts = list(path.relative_to(tmp_path).parts)
                if parts[0] == "runs":
                    parts[1] = names.get(parts[1], parts[1])
                out.append("/".join(parts))
    return sorted(out)


def run_two_phase(
    tmp_path: Path,
    config_kwargs: Mapping[str, Any] | None = None,
    *,
    entry_extra: Mapping[str, Any] | None = None,
    bars_path: Path = BARS_PATH,
    nets: list[Decimal] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Run both phases over ``tmp_path``; ``(records, files)`` as the golden stores them.

    ``config_kwargs`` are extra top-level runner keys (e.g. ``accounts``) and
    ``entry_extra`` extra keys of both ``plugins[]`` entries (e.g. ``account``).
    ``records`` are grouped by plugin id in :data:`PLUGIN_IDS` order. When
    ``nets`` is given, the ``trade.net`` of every ``closed`` update either
    plugin received is appended to it (not part of the golden records).
    """
    extra_doc = dict(config_kwargs or {})
    extra_entry = dict(entry_extra or {})
    events = _events(load_bars(bars_path))
    first, end = MISSED
    phase1 = [(t, e) for t, e in events if e.bars[0].open_ns < first]
    missed = [e for _, e in events if first <= e.bars[0].open_ns < end]
    phase2 = [(t, e) for t, e in events if e.bars[0].open_ns >= end]
    records: list[tuple[str, dict[str, Any]]] = []

    clock = FakeClock(PHASE1_START)
    plane = FakeDataPlane(clock, phase1)
    host = _host(tmp_path, "run1", clock, plane, records, extra_doc, extra_entry, nets)
    code = asyncio.run(clock.run(host.run(until_ns=PHASE1_UNTIL), max_steps=1_000_000))
    if code != 0:
        raise RuntimeError(f"phase 1 exited {code}")

    clock2 = FakeClock(PHASE2_START)
    plane2 = FakeDataPlane(clock2, phase2, recorded=[*plane.recorded, *missed])
    host2 = _host(tmp_path, "run2", clock2, plane2, records, extra_doc, extra_entry, nets)
    code = asyncio.run(clock2.run(host2.run(until_ns=PHASE2_UNTIL), max_steps=1_000_000))
    if code != 0:
        raise RuntimeError(f"phase 2 exited {code}")

    grouped = [{"plugin": pid, **rec} for p in PLUGIN_IDS for pid, rec in records if pid == p]
    return grouped, _files(tmp_path)


def read_golden(path: Path = GOLDEN_PATH) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """``(comment record, records)`` of a golden file."""
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return lines[0], lines[1:]


def check_coverage(records: Sequence[Mapping[str, Any]]) -> list[str]:
    """What the golden must cover (design §12); the missing items."""
    missing: list[str] = []
    reasons = {r.get("reason") for r in records if r.get("kind") == "intent_rejected"}
    missing.extend(
        f"an intent_rejected {reason}"
        for reason in ("max_contracts", "max_daily_loss")
        if reason not in reasons
    )
    first, end = MISSED
    for pid in PLUGIN_IDS:
        replayed = [
            r
            for r in records
            if r["plugin"] == pid
            and r.get("fill") is not None
            and first <= r["fill"]["bar_open_ns"] < end
        ]
        if not replayed:
            missing.append(f"a replayed fill of {pid}")
    return missing


def _dump(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture the BC-2 replay golden.")
    parser.add_argument("--out", type=Path, default=HERE, help="output directory")
    args = parser.parse_args(argv)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    bars_path = out / BARS_PATH.name
    bars_path.write_text("".join(_dump(row) + "\n" for row in generate_bars()), encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="replay-v1-") as tmp:
        records, files = run_two_phase(Path(tmp), bars_path=bars_path)
    missing = check_coverage(records)
    if missing:
        print(f"the golden does not cover: {', '.join(missing)}", file=sys.stderr)
        return 1
    comment = {"base_commit": BASE_COMMIT, "generator": GENERATOR, "files": files}
    (out / GOLDEN_PATH.name).write_text(
        "".join(_dump(rec) + "\n" for rec in [comment, *records]), encoding="utf-8"
    )
    print(f"wrote {bars_path} and {out / GOLDEN_PATH.name} ({len(records)} records)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
