"""Live state: the saved EngineState, the paper state and the persistent blocks (design §24).

Everything lives in the live-state directory (default
``~/.skylit-fse/live-state/``, outside the repository):

- ``engine_state.json``: the EngineState after the latest Decision_Time
  (:meth:`EngineState.to_canonical_json <fse.engine.state.EngineState.to_canonical_json>`);
- ``paper_state.json``: the Paper_Broker's book and account, the bar-phase
  events not yet passed to a step, and the session's equity low
  (:class:`PaperState`);
- ``blocks.json``: the persistent entry blocks (:class:`BlockRecord`), kinds
  ``halt_command``, ``bracket_failure``, ``reconciliation`` and
  ``restore_failed`` (:data:`BLOCK_KINDS`). Only the Operator's clear command
  (``fse clear``) removes them (Req 24.10, 24.18, 24.27, 16.10);
- ``HALT``: the halt file. While it exists, entries are blocked (Req 24.26-24.27).

**Writes** go through the Log_Writer's atomic write: a temp file in the same
directory, fsync, rename (Req 16.7). :meth:`StateStore.save` writes the
EngineState, then the paper state, after every Decision_Time.

**Reads.** A missing state file means a first start. A state file that cannot
be read or decoded is a :class:`RestoreFailure`: the Live_Runner adds a
``restore_failed`` block and reports it in the first Finding_Card (Req 16.10).
A blocks file that cannot be read counts as blocked (:data:`UNREADABLE_BLOCKS`
names that block) until ``fse clear`` rewrites it.

The paper state uses the EngineState JSON rules (:mod:`fse.engine.state`) with
the simulator's and the step's dataclasses added, so only those classes can be
decoded from it.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final, Literal, cast

from fse.engine import step as step_module
from fse.engine.state import (
    ENGINE_CODEC,
    EngineState,
    StateDecodeError,
    dataclass_registry,
)
from fse.engine.step import ExternalBlock, StepEvent
from fse.engine.types import Money
from fse.logio import LogWriter
from fse.logio.canonical_json import ny_iso
from fse.sim import account as account_module
from fse.sim import fills as fills_module
from fse.sim import paper_broker as paper_module
from fse.sim.paper_broker import PaperBrokerSnapshot
from fse.timekit import Instant

__all__ = [
    "BLOCKS_FILE_NAME",
    "BLOCK_KINDS",
    "ENGINE_STATE_FILE_NAME",
    "HALT_FILE_BLOCK",
    "HALT_FILE_NAME",
    "PAPER_STATE_FILE_NAME",
    "UNREADABLE_BLOCKS",
    "BlockKind",
    "BlockRecord",
    "BlockStore",
    "BlocksUnreadable",
    "PaperState",
    "RestoreFailure",
    "Restored",
    "StateStore",
    "halt_file",
]

ENGINE_STATE_FILE_NAME: Final = "engine_state.json"
PAPER_STATE_FILE_NAME: Final = "paper_state.json"
BLOCKS_FILE_NAME: Final = "blocks.json"
HALT_FILE_NAME: Final = "HALT"

type BlockKind = Literal["halt_command", "bracket_failure", "reconciliation", "restore_failed"]

BLOCK_KINDS: Final[tuple[BlockKind, ...]] = (
    "halt_command",
    "bracket_failure",
    "reconciliation",
    "restore_failed",
)
"""The persistent block kinds; each stays until the Operator's clear command."""

UNREADABLE_BLOCKS: Final = "blocks_unreadable"
"""The ExternalBlock kind of a blocks file that cannot be read (it counts as blocked)."""
HALT_FILE_BLOCK: Final = "halt_file"
"""The ExternalBlock kind of the halt file."""

_BLOCKS_FORMAT: Final = "fse.live_blocks"
_PAPER_FORMAT: Final = "fse.paper_state"
_VERSION: Final = 1


def halt_file(directory: Path) -> Path:
    """The halt file in the live-state ``directory``."""
    return Path(directory) / HALT_FILE_NAME


# ---------------------------------------------------------------- the saved state


@dataclass(frozen=True, slots=True)
class PaperState:
    """What the Paper_Broker side of a live session needs to continue after a restart.

    ``session`` is the session in progress when it was saved (``None``
    between sessions); ``pending`` the bar-phase events and refused entries
    the next ``Engine.step`` receives; ``day_low`` and ``truncated`` the
    session's equity low and whether the account failed in it.
    """

    broker: PaperBrokerSnapshot
    session: date | None
    pending: tuple[StepEvent, ...]
    day_low: Money
    truncated: bool
    t: Instant | None = None
    """The Decision_Time it was saved after; it must equal the EngineState's."""


_PAPER_CODEC: Final = ENGINE_CODEC.extended(
    dataclass_registry(
        fills_module, account_module, paper_module, step_module, sys.modules[__name__]
    )
)
"""The engine state types plus the simulator's, the step's and :class:`PaperState`."""


@dataclass(frozen=True, slots=True)
class Restored:
    """A readable saved state: the EngineState and, when saved, the paper state."""

    engine: EngineState
    paper: PaperState | None


@dataclass(frozen=True, slots=True)
class RestoreFailure:
    """A saved state that cannot be read: entries stay blocked until clear (Req 16.10)."""

    reason: str


class StateStore:
    """Saves and restores the live state in one live-state directory."""

    __slots__ = ("_dir", "_writer")

    def __init__(self, writer: LogWriter, directory: Path) -> None:
        self._writer = writer
        self._dir = Path(directory)

    @property
    def directory(self) -> Path:
        return self._dir

    @property
    def engine_path(self) -> Path:
        return self._dir / ENGINE_STATE_FILE_NAME

    @property
    def paper_path(self) -> Path:
        return self._dir / PAPER_STATE_FILE_NAME

    def save(self, state: EngineState, paper: PaperState | None = None) -> None:
        """Write the EngineState, then the paper state: temp file, fsync, rename each."""
        self._writer.write_text(self.engine_path, state.to_canonical_json() + "\n")
        if paper is not None:
            doc = {
                "format": _PAPER_FORMAT,
                "version": _VERSION,
                "state": _PAPER_CODEC.encode(paper),
            }
            text = json.dumps(
                doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
            )
            self._writer.write_text(self.paper_path, text + "\n")

    def load(self) -> Restored | RestoreFailure | None:
        """The saved state; ``None`` when nothing was saved; a failure when unreadable."""
        engine_path, paper_path = self.engine_path, self.paper_path
        if not engine_path.exists() and not paper_path.exists():
            return None
        try:
            engine = EngineState.from_json(_read(engine_path))
        except (OSError, UnicodeDecodeError, StateDecodeError) as exc:
            return RestoreFailure(f"{engine_path.name}: {_reason(exc)}")
        if not paper_path.exists():
            return Restored(engine, None)
        try:
            paper = _decode_paper(_read(paper_path))
        except (OSError, UnicodeDecodeError, StateDecodeError) as exc:
            return RestoreFailure(f"{paper_path.name}: {_reason(exc)}")
        return Restored(engine, paper)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _reason(exc: BaseException) -> str:
    if isinstance(exc, FileNotFoundError):
        return "the file is missing"
    if isinstance(exc, OSError):
        return f"cannot read the file ({type(exc).__name__})"
    return str(exc)


def _decode_paper(text: str) -> PaperState:
    try:
        obj = json.loads(text)
    except ValueError as exc:
        raise StateDecodeError(f"not valid JSON: {exc}") from exc
    if (
        not isinstance(obj, dict)
        or set(obj) != {"format", "version", "state"}
        or obj["format"] != _PAPER_FORMAT
        or obj["version"] != _VERSION
    ):
        raise StateDecodeError(f"not a paper state of format {_PAPER_FORMAT!r} version {_VERSION}")
    state = _PAPER_CODEC.decode(obj["state"])
    if not isinstance(state, PaperState):
        raise StateDecodeError(f"the document holds a {type(state).__name__}, not a paper state")
    return state


# ---------------------------------------------------------------- persistent blocks


@dataclass(frozen=True, slots=True)
class BlockRecord:
    """One persistent block: its kind, the instrument (``None``: all) and why, and when set."""

    kind: BlockKind
    instrument: str | None
    detail: str
    set_at_ns: Instant

    def external(self) -> ExternalBlock:
        """The block as ``Engine.step`` takes it."""
        return ExternalBlock(self.kind, self.instrument, self.detail)


@dataclass(frozen=True, slots=True)
class BlocksUnreadable:
    """A blocks file that cannot be read; it counts as blocked (design §24 "Halt")."""

    reason: str

    def external(self) -> ExternalBlock:
        return ExternalBlock(UNREADABLE_BLOCKS, None, self.reason)


class BlockStore:
    """The persistent blocks in ``blocks.json`` (see the module notes)."""

    __slots__ = ("_path", "_writer")

    def __init__(self, writer: LogWriter, directory: Path) -> None:
        self._writer = writer
        self._path = Path(directory) / BLOCKS_FILE_NAME

    @property
    def path(self) -> Path:
        return self._path

    def read(self) -> tuple[BlockRecord, ...] | BlocksUnreadable:
        """The blocks in force; none when the file is missing."""
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ()
        except (OSError, UnicodeDecodeError) as exc:
            return BlocksUnreadable(f"{self._path.name}: {_reason(exc)}")
        try:
            return _parse_blocks(text)
        except ValueError as exc:
            return BlocksUnreadable(f"{self._path.name}: {exc}")

    def add(self, record: BlockRecord) -> bool:
        """Add ``record`` unless a block of its kind and instrument is in force.

        An unreadable file is replaced by one holding ``record`` only: the
        unreadable file blocked entries, and the new file still does.
        Returns whether the record was added.
        """
        found = self.read()
        current = () if isinstance(found, BlocksUnreadable) else found
        if any((b.kind, b.instrument) == (record.kind, record.instrument) for b in current):
            return False
        self._write((*current, record))
        return True

    def clear(self) -> tuple[BlockRecord, ...] | BlocksUnreadable:
        """Remove every block (the Operator's clear command); what was in force before."""
        before = self.read()
        self._write(())
        return before

    def _write(self, records: Iterable[BlockRecord]) -> None:
        self._writer.write_json(
            self._path,
            {
                "format": _BLOCKS_FORMAT,
                "version": _VERSION,
                "blocks": [
                    {
                        "kind": r.kind,
                        "instrument": r.instrument,
                        "detail": r.detail,
                        "set_at_ns": r.set_at_ns,
                        "set_at_ny": ny_iso(r.set_at_ns),
                    }
                    for r in records
                ],
            },
        )


def _parse_blocks(text: str) -> tuple[BlockRecord, ...]:
    try:
        obj = json.loads(text)
    except ValueError:
        raise ValueError("not valid JSON") from None
    if (
        not isinstance(obj, dict)
        or obj.get("format") != _BLOCKS_FORMAT
        or obj.get("version") != _VERSION
        or not isinstance(obj.get("blocks"), list)
    ):
        raise ValueError(f"not a blocks file of format {_BLOCKS_FORMAT!r} version {_VERSION}")
    out: list[BlockRecord] = []
    for i, item in enumerate(cast("list[object]", obj["blocks"])):
        if not isinstance(item, dict):
            raise ValueError(f"block {i} is not an object")
        kind, instrument = item.get("kind"), item.get("instrument")
        detail, at = item.get("detail"), item.get("set_at_ns")
        if kind not in BLOCK_KINDS:
            raise ValueError(f"block {i} has an unknown kind {kind!r}")
        if instrument is not None and (not isinstance(instrument, str) or not instrument.strip()):
            raise ValueError(f"block {i} has a bad instrument")
        if not isinstance(detail, str):
            raise ValueError(f"block {i} has no detail text")
        if isinstance(at, bool) or not isinstance(at, int):
            raise ValueError(f"block {i} has no integer set_at_ns")
        out.append(BlockRecord(cast("BlockKind", kind), instrument, detail, at))
    return tuple(out)
