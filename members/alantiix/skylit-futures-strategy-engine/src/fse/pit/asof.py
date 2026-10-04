"""As-of indexes over timestamped inputs (design §5 "Point-in-time layer", D8).

Every input record has two instants:

- ``observed_at``: its Observation_Time, computed once at ingest (design "Time
  model"): a Snapshot's ``asOf``, a bar's ``close_ns``, a dark-pool print's
  ``ts_ns``.
- ``available_at``: when the Strategy_Engine may first use it (D8). In a
  historical backtest ``available_at = observed_at``. In live and replay mode
  ``available_at = max(observed_at, receipt_time)``.

:class:`AsOfIndex` stores records sorted by ``(available_at, sequence)``, where
``sequence`` is the input position. Every query takes the view instant ``t`` and
sees only the prefix with ``available_at <= t`` (found with ``bisect_right``),
which is the "array truncated at ``t``" of the design. Within that prefix,
"latest" means the largest ``observed_at``, never the latest arrival, so Map_State
selects by the ``asOf`` Skylit returned (Req 5.1). Equal ``observed_at`` values go
to the record that became available later (then the later input position).

When ``observed_at`` never decreases in availability order (always true when
``available_at = observed_at``), every query is a bisection. Otherwise (late
receipts in replay) queries scan only the records that became available between
the two instants involved.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from fse.engine.types import Bar, DarkPoolPrint, Snapshot
from fse.timekit import Instant

__all__ = [
    "AsOfIndex",
    "Received",
    "available_at",
    "observation_time",
]


def _require_instant(name: str, value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer Instant, got {value!r}")
    return value


def available_at(observed_at: Instant, received_at: Instant | None = None) -> Instant:
    """D8: the Observation_Time, or ``max(Observation_Time, receipt_time)`` when received live."""
    _require_instant("observed_at", observed_at)
    if received_at is None:
        return observed_at
    return max(observed_at, _require_instant("received_at", received_at))


def observation_time(record: Snapshot | Bar | DarkPoolPrint) -> Instant:
    """The Observation_Time of a Snapshot (``asOf``), bar (close) or dark-pool print."""
    if isinstance(record, Snapshot):
        return record.as_of_ns
    if isinstance(record, Bar):
        return record.close_ns
    return record.ts_ns


@dataclass(frozen=True, slots=True)
class Received[T]:
    """A record with the instant it was received (live recording or replay)."""

    record: T
    received_ns: Instant

    def __post_init__(self) -> None:
        _require_instant("Received.received_ns", self.received_ns)


class AsOfIndex[T]:
    """Records sorted by ``(available_at, sequence)``, queried as of a view instant ``t``.

    Build one with :meth:`of` from plain records (historical: available at their
    Observation_Time) or :class:`Received` wrappers (replay and live), or pass
    ``(record, observed_at, available_at)`` triples directly. Raises
    ``ValueError`` if an instant is not an ``int`` or if a record would be
    available before it is observed.
    """

    __slots__ = ("_avail", "_best", "_monotone", "_obs", "_records")

    def __init__(self, entries: Iterable[tuple[T, Instant, Instant]] = ()) -> None:
        items = list(entries)
        for i, (_, observed, available) in enumerate(items):
            _require_instant(f"entries[{i}] observed_at", observed)
            _require_instant(f"entries[{i}] available_at", available)
            if available < observed:
                raise ValueError(
                    f"entries[{i}] is available at {available}, before it is observed at {observed}"
                )
        order = sorted(range(len(items)), key=lambda i: (items[i][2], i))
        self._records: tuple[T, ...] = tuple(items[i][0] for i in order)
        self._obs: list[int] = [items[i][1] for i in order]
        self._avail: list[int] = [items[i][2] for i in order]
        # _best[i]: position of the largest observed_at in [0, i]; ties go to the later one.
        best: list[int] = []
        for i, observed in enumerate(self._obs):
            best.append(i if not best or observed >= self._obs[best[-1]] else best[-1])
        self._best = best
        self._monotone = all(a <= b for a, b in zip(self._obs, self._obs[1:], strict=False))

    @classmethod
    def of(
        cls,
        records: Iterable[T | Received[T]],
        observed_at: Callable[[T], Instant],
    ) -> AsOfIndex[T]:
        """Index ``records``, applying the D8 availability rule to each."""
        entries: list[tuple[T, Instant, Instant]] = []
        for item in records:
            if isinstance(item, Received):
                record: T = item.record
                observed = observed_at(record)
                entries.append((record, observed, available_at(observed, item.received_ns)))
            else:
                observed = observed_at(item)
                entries.append((item, observed, observed))
        return cls(entries)

    def __len__(self) -> int:
        return len(self._records)

    def count_upto(self, t: Instant) -> int:
        """The number of records with ``available_at <= t``."""
        return bisect_right(self._avail, t)

    def upto(self, t: Instant) -> tuple[T, ...]:
        """The records available at ``t``, in availability order."""
        return self._records[: self.count_upto(t)]

    def latest(self, t: Instant) -> T | None:
        """The available record with the largest ``observed_at``, else ``None``."""
        k = self.count_upto(t)
        return self._records[self._best[k - 1]] if k else None

    def latest_at_or_before(self, t: Instant, at: Instant) -> T | None:
        """The available record with the largest ``observed_at <= at``, else ``None``."""
        k = self.count_upto(t)
        if self._monotone:
            j = bisect_right(self._obs, at, 0, k)
            return self._records[j - 1] if j else None
        if at >= t:
            return self.latest(t)
        # Records available by `at` are observed by `at`; later arrivals need a scan.
        k_at = self.count_upto(at)
        pick = self._best[k_at - 1] if k_at else -1
        for i in range(k_at, k):
            if self._obs[i] <= at and (pick < 0 or self._obs[i] >= self._obs[pick]):
                pick = i
        return self._records[pick] if pick >= 0 else None

    def between(self, t: Instant, lo: Instant, hi: Instant) -> list[T]:
        """Available records with ``lo <= observed_at <= hi``, in ``observed_at`` order."""
        if lo > hi:
            return []
        k = self.count_upto(t)
        if self._monotone:
            return list(
                self._records[bisect_left(self._obs, lo, 0, k) : bisect_right(self._obs, hi, 0, k)]
            )
        # observed_at >= lo implies available_at >= lo, so earlier arrivals are skipped.
        picked = [
            i for i in range(bisect_left(self._avail, lo, 0, k), k) if lo <= self._obs[i] <= hi
        ]
        picked.sort(key=lambda i: (self._obs[i], i))
        return [self._records[i] for i in picked]
