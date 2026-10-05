"""Live guards: entry blocks the Live_Runner passes to ``Engine.step`` (design "Engine state").

At every Decision_Time :meth:`Guards.at` returns the blocks in force, in this
order:

1. **Stale map** (Req 23.7): Snapshot_Age above ``live.live_max_snapshot_age_s``
   (or a Map_State with no Snapshot, whose age is undefined) gives a
   ``stale_map`` block. The Live_Runner also runs a Decision_Time at
   :meth:`StaleMapGuard.stale_at`, the first instant the current Map_State is
   too old, so resting entries are cancelled within 1 s of the age passing
   the maximum, whatever the Decision_Cadence. The block lifts at the first
   Decision_Time whose age is back at or below the maximum.
2. **Halt file** (Req 24.26-24.27): while ``HALT`` exists in the live-state
   directory, a ``halt_file`` block.
3. **Persistent blocks** (Req 24.10, 24.18, 24.27, 16.10): each record in
   ``blocks.json``, as its kind; an unreadable blocks file gives a
   ``blocks_unreadable`` block.

``Engine.step`` cancels each resting entry of a blocked instrument and
withholds new entries; stop-loss, target and exit orders of open positions
keep working. The Live_Runner records each Decision_Time's blocks as a
``guard_event``, so replay applies them at the same instants (Req 23.9).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fse.engine.step import ExternalBlock
from fse.engine.types import Unavailable
from fse.live.state_store import HALT_FILE_BLOCK, BlockStore, BlocksUnreadable, halt_file
from fse.pit.protocols import MapState
from fse.timekit import NS_PER_SECOND, Instant

__all__ = ["STALE_MAP_BLOCK", "Guards", "StaleMapGuard"]

STALE_MAP_BLOCK = "stale_map"


@dataclass(frozen=True, slots=True)
class StaleMapGuard:
    """Snapshot_Age above ``max_age_s`` blocks entries (Req 23.7)."""

    max_age_s: int

    def __post_init__(self) -> None:
        if isinstance(self.max_age_s, bool) or self.max_age_s < 1:
            raise ValueError(f"max_age_s must be a whole number of at least 1: {self.max_age_s!r}")

    @property
    def max_age_ns(self) -> int:
        return self.max_age_s * NS_PER_SECOND

    def block(self, map_state: MapState) -> ExternalBlock | None:
        """The ``stale_map`` block at ``map_state.t``, or ``None`` when the map is fresh."""
        age = map_state.snapshot_age_ns()
        if isinstance(age, Unavailable):
            return ExternalBlock(STALE_MAP_BLOCK, None, age.reason)
        if age > self.max_age_ns:
            return ExternalBlock(
                STALE_MAP_BLOCK,
                None,
                f"Snapshot_Age {age / NS_PER_SECOND:.3f} s is above the live maximum "
                f"of {self.max_age_s} s",
            )
        return None

    def stale_at(self, map_state: MapState) -> Instant | None:
        """The first instant the current Map_State's age is above the maximum.

        ``None`` when it holds no Snapshot (it is stale already).
        """
        snapshots = map_state.snapshots()
        if not snapshots:
            return None
        return min(s.as_of_ns for s in snapshots) + self.max_age_ns + 1


class Guards:
    """The stale-map guard, the halt file and the persistent blocks (see the module notes)."""

    __slots__ = ("_blocks", "_halt", "_stale")

    def __init__(self, stale: StaleMapGuard, blocks: BlockStore, state_dir: Path) -> None:
        self._stale = stale
        self._blocks = blocks
        self._halt = halt_file(state_dir)

    @property
    def stale(self) -> StaleMapGuard:
        return self._stale

    def at(self, map_state: MapState) -> tuple[ExternalBlock, ...]:
        """Every block in force at ``map_state.t``."""
        out: list[ExternalBlock] = []
        stale = self._stale.block(map_state)
        if stale is not None:
            out.append(stale)
        if self._halt.exists():
            out.append(
                ExternalBlock(HALT_FILE_BLOCK, None, f"the halt file {self._halt.name} exists")
            )
        found = self._blocks.read()
        if isinstance(found, BlocksUnreadable):
            out.append(found.external())
        else:
            out.extend(r.external() for r in found)
        return tuple(out)
