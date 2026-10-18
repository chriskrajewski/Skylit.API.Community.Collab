"""``EntryGate`` (design 10.4): the root rule and per-plugin halt files and blocks."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from fse.live.host import gate as gate_module
from fse.live.host.gate import GLOBAL_ROOT_KINDS, IGNORED_ROOT_KINDS, EntryGate
from fse.live.state_store import BLOCK_KINDS, BlockRecord, BlockStore, halt_file
from fse.logio import LogWriter
from fse.logio.redact import Redactor

WED, THU = date(2026, 10, 14), date(2026, 10, 15)


def gate(root: Path) -> tuple[EntryGate, list[dict[str, Any]], LogWriter]:
    logs: list[dict[str, Any]] = []
    writer = LogWriter(Redactor())
    return EntryGate(writer, root, lambda e: logs.append(dict(e))), logs, writer


def add(writer: LogWriter, directory: Path, kind: Any) -> None:
    BlockStore(writer, directory).add(BlockRecord(kind, None, f"{kind} set", 1))


def test_no_files_no_blocks(tmp_path: Path) -> None:
    g, logs, _ = gate(tmp_path)
    assert g.blocks("alpha", WED) == ()
    assert logs == []


def test_root_halt_file_and_halt_command_block_everyone(tmp_path: Path) -> None:
    g, _, writer = gate(tmp_path)
    halt_file(tmp_path).write_text("")
    assert g.blocks("alpha", WED) == ("halt_file",)
    assert g.blocks("other", WED) == ("halt_file",)
    halt_file(tmp_path).unlink()
    add(writer, tmp_path, "halt_command")
    assert g.blocks("alpha", WED) == ("halt_command",)


def test_unreadable_root_blocks_file_blocks_everyone(tmp_path: Path) -> None:
    g, _, _ = gate(tmp_path)
    (tmp_path / "blocks.json").write_text("{not json")
    assert g.blocks("alpha", WED) == ("blocks_unreadable",)


@pytest.mark.parametrize("kind", ["restore_failed", "bracket_failure", "reconciliation"])
def test_root_records_of_other_runners_are_ignored_and_logged_once_per_day(
    tmp_path: Path, kind: str
) -> None:
    g, logs, writer = gate(tmp_path)
    add(writer, tmp_path, kind)
    assert g.blocks("alpha", WED) == ()
    assert g.blocks("other", WED) == ()
    assert [(e["kind"], e["block_kind"], e["level"]) for e in logs] == [
        ("root_block_ignored", kind, "info")
    ]
    assert g.blocks("alpha", THU) == ()
    assert len(logs) == 2  # once more on the next Globex day


def test_every_known_block_kind_is_sorted() -> None:
    assert set(BLOCK_KINDS) == GLOBAL_ROOT_KINDS | IGNORED_ROOT_KINDS
    assert not GLOBAL_ROOT_KINDS & IGNORED_ROOT_KINDS


def test_unknown_root_kind_fails_closed_and_logs_once_per_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Simulate a kind added to BLOCK_KINDS later but sorted into neither set.
    monkeypatch.setattr(gate_module, "IGNORED_ROOT_KINDS", frozenset({"restore_failed"}))
    g, logs, writer = gate(tmp_path)
    add(writer, tmp_path, "reconciliation")
    add(writer, tmp_path, "bracket_failure")
    assert g.blocks("alpha", WED) == ("reconciliation", "bracket_failure")
    assert g.blocks("other", WED) == ("reconciliation", "bracket_failure")
    assert [(e["kind"], e["block_kind"], e["level"]) for e in logs] == [
        ("root_block_unknown", "reconciliation", "warning"),
        ("root_block_unknown", "bracket_failure", "warning"),
    ]
    assert g.blocks("alpha", THU) == ("reconciliation", "bracket_failure")
    assert len(logs) == 4  # once more per kind on the next Globex day


def test_per_plugin_blocks_only_that_plugin(tmp_path: Path) -> None:
    g, _, writer = gate(tmp_path)
    add(writer, g.plugin_dir("alpha"), "restore_failed")
    assert g.blocks("alpha", WED) == ("restore_failed",)
    assert g.blocks("other", WED) == ()
    halt_file(g.plugin_dir("other")).parent.mkdir(parents=True, exist_ok=True)
    halt_file(g.plugin_dir("other")).write_text("")
    assert g.blocks("other", WED) == ("halt_file",)
    (g.plugin_dir("third")).mkdir(parents=True)
    (g.plugin_dir("third") / "blocks.json").write_text("[]")
    assert g.blocks("third", WED) == ("blocks_unreadable",)
    assert g.plugin_dir("alpha") == tmp_path / "plugins" / "alpha"
