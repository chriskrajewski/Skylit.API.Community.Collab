"""Halt files and persistent blocks for host-brokered plugins (design 10.4).

:meth:`EntryGate.blocks` reads, before every entry and on the host's
once-per-second tick while entries are working:

- the root live-state directory: its ``HALT`` file, the ``blocks.json``
  records of kind ``halt_command`` (what ``fse halt`` writes) and an
  unreadable root ``blocks.json`` block **every** host-brokered plugin. Root
  records of kind ``restore_failed``, ``bracket_failure`` and
  ``reconciliation`` describe the Playbook's or ``fse live``'s own state and
  are ignored here, with one ``root_block_ignored`` info log per kind per
  Globex day. Any other root kind (one added to ``BLOCK_KINDS`` later and not
  sorted into either set) fails closed: it blocks every plugin, with one
  ``root_block_unknown`` warning per kind per Globex day;
- ``<live-state>/plugins/<id>/``: its ``HALT`` file and every ``blocks.json``
  record (any kind), or the file being unreadable, block that plugin only.

The reasons are the block kinds: ``halt_file``, ``halt_command``,
``blocks_unreadable``, ``restore_failed``, ...; ``fse halt|clear --state-dir``
work on either directory unchanged.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path
from typing import Final

from fse.live.state_store import (
    HALT_FILE_BLOCK,
    UNREADABLE_BLOCKS,
    BlockStore,
    BlocksUnreadable,
    halt_file,
)
from fse.logio import LogWriter
from fse.logio.canonical_json import JsonValue

__all__ = ["GLOBAL_ROOT_KINDS", "IGNORED_ROOT_KINDS", "PLUGINS_DIR_NAME", "EntryGate", "plugin_dir"]

PLUGINS_DIR_NAME: Final = "plugins"
GLOBAL_ROOT_KINDS: Final = frozenset({"halt_command"})
IGNORED_ROOT_KINDS: Final = frozenset({"restore_failed", "bracket_failure", "reconciliation"})


def plugin_dir(state_root: Path, plugin_id: str) -> Path:
    """``<live-state>/plugins/<id>``: a host-brokered plugin's own state directory."""
    return Path(state_root) / PLUGINS_DIR_NAME / plugin_id


class EntryGate:
    """The root rule plus each plugin's own halt file and blocks."""

    __slots__ = ("_ignored_logged", "_log", "_root", "_root_blocks", "_writer")

    def __init__(
        self,
        writer: LogWriter,
        state_root: Path,
        log: Callable[[Mapping[str, JsonValue]], None],
    ) -> None:
        self._writer = writer
        self._root = Path(state_root)
        self._root_blocks = BlockStore(writer, self._root)
        self._log = log
        self._ignored_logged: set[tuple[date | None, str]] = set()

    @property
    def root(self) -> Path:
        return self._root

    def plugin_dir(self, plugin_id: str) -> Path:
        return plugin_dir(self._root, plugin_id)

    def plugin_blocks(self, plugin_id: str) -> BlockStore:
        """The plugin's own ``blocks.json`` (where ``restore_failed`` is written)."""
        return BlockStore(self._writer, self.plugin_dir(plugin_id))

    def global_blocks(self, day: date | None) -> tuple[str, ...]:
        """Root reasons that block every host-brokered plugin."""
        out: list[str] = []
        if halt_file(self._root).exists():
            out.append(HALT_FILE_BLOCK)
        found = self._root_blocks.read()
        if isinstance(found, BlocksUnreadable):
            out.append(UNREADABLE_BLOCKS)
            return tuple(out)
        for record in found:
            if record.kind in GLOBAL_ROOT_KINDS:
                if record.kind not in out:
                    out.append(record.kind)
            elif record.kind in IGNORED_ROOT_KINDS:
                self._log_once(day, record.kind, "root_block_ignored", "info", record.detail)
            else:
                # Fail closed: a kind this gate does not know blocks everyone.
                self._log_once(day, record.kind, "root_block_unknown", "warning", record.detail)
                if record.kind not in out:
                    out.append(record.kind)
        return tuple(out)

    def _log_once(
        self, day: date | None, block_kind: str, kind: str, level: str, detail: str
    ) -> None:
        key = (day, block_kind)
        if key in self._ignored_logged:
            return
        self._ignored_logged.add(key)
        self._log({"kind": kind, "level": level, "block_kind": block_kind, "detail": detail})

    def own_blocks(self, plugin_id: str) -> tuple[str, ...]:
        """Reasons from the plugin's own directory."""
        directory = self.plugin_dir(plugin_id)
        out: list[str] = []
        if halt_file(directory).exists():
            out.append(HALT_FILE_BLOCK)
        found = self.plugin_blocks(plugin_id).read()
        if isinstance(found, BlocksUnreadable):
            out.append(UNREADABLE_BLOCKS)
        else:
            for record in found:
                if record.kind not in out:
                    out.append(record.kind)
        return tuple(out)

    def blocks(self, plugin_id: str, day: date | None) -> tuple[str, ...]:
        """Every reason in force for ``plugin_id`` (``()`` when entries may go)."""
        out = list(self.global_blocks(day))
        for reason in self.own_blocks(plugin_id):
            if reason not in out:
                out.append(reason)
        return tuple(out)
