"""BC-2 / A-1: the two-phase replay reproduces the pre-feature golden (design §12).

The golden was captured from base ``0830838`` before any account code
existed (``tests/unit/fixtures/make_replay_v1_golden.py``). The new code has
no "feature off" switch, so the comparison is against that capture, not
against itself.

- Without ``accounts:`` the records (accepted/rejected intents and every
  ``OrderUpdate``) equal the golden line for line, and the file list is the
  base run's plus the journal files and the host's ``status.json`` and
  ``marks.json`` (main's plugin status and live P&L; no record changes).
- With both plugins on one unrestricted account (``preset: none``,
  ``flat_deadline_enabled: false``) the records are the same: an account
  whose rules are all off changes no intent and no fill, and the bar-major
  replay gives each plugin the same results. Only the account's state file
  is new.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from fse.live.host.accounts import ACCOUNT_CODEC, PaperAccountState
from fse.live.host.journal import journal_trades
from tests.unit.fixtures.make_replay_v1_golden import (
    PLUGIN_IDS,
    RUN_DIRS,
    read_golden,
    run_two_phase,
)

_JOURNAL_FILES: Final = frozenset(
    {
        *(f"runs/{run}/plugins/{pid}/trades.jsonl" for run in RUN_DIRS for pid in PLUGIN_IDS),
        *(f"live-state/plugins/{pid}/exit_reasons.json" for pid in PLUGIN_IDS),
    }
)
"""The journal of each plugin in each run dir and its ``exit_reasons.json`` sidecar."""

_STATUS_FILES: Final = frozenset(
    {
        *(f"live-state/plugins/{pid}/status.json" for pid in PLUGIN_IDS),
        "live-state/marks.json",
    }
)
"""Written by the host since main's plugin status and live P&L (``status.json`` per
plugin, ``marks.json``): new files only; the records are unchanged."""

EXPECTED_NEW_FILES: Final[dict[str, frozenset[str]]] = {
    "no_account": _JOURNAL_FILES | _STATUS_FILES,
    "open_account": _JOURNAL_FILES
    | _STATUS_FILES
    | {"live-state/accounts/open_acct/account.json"},
}
"""Files the new code writes beyond the base run's list, per scenario."""

OPEN_ACCOUNT: Final[dict[str, Any]] = {
    "id": "open_acct",
    "preset": "none",
    "rules": {"flat_deadline_enabled": False},
}


def _compare(records: list[dict[str, Any]], files: list[str], scenario: str) -> None:
    comment, golden = read_golden()
    assert len(records) == len(golden)
    for i, (got, want) in enumerate(zip(records, golden, strict=True)):
        assert got == want, f"record {i} differs"
    assert set(files) - set(comment["files"]) == EXPECTED_NEW_FILES[scenario]
    assert set(comment["files"]) <= set(files)


def test_replay_matches_pre_feature_golden(tmp_path: Path) -> None:
    nets: list[Decimal] = []
    records, files = run_two_phase(tmp_path, nets=nets)
    _compare(records, files, "no_account")
    assert not (tmp_path / "live-state" / "accounts").exists()
    # the journal holds every close once, with its engine net
    lines = [
        json.loads(text)
        for path in sorted((tmp_path / "runs").glob("*/plugins/*/trades.jsonl"))
        for text in path.read_text(encoding="utf-8").splitlines()
    ]
    trades = journal_trades(lines)
    assert len(trades) == len(nets)
    assert sorted(t.net for t in trades) == sorted(nets)
    assert {ln["account"] for ln in lines} == {None}


def test_replay_unrestricted_account_matches_golden(tmp_path: Path) -> None:
    nets: list[Decimal] = []
    records, files = run_two_phase(
        tmp_path, {"accounts": [OPEN_ACCOUNT]}, entry_extra={"account": "open_acct"}, nets=nets
    )
    _compare(records, files, "open_account")
    doc = json.loads((tmp_path / "live-state/accounts/open_acct/account.json").read_text())
    state = ACCOUNT_CODEC.decode(doc["account"])
    assert isinstance(state, PaperAccountState)
    # The account booked both plugins' closes and enforced nothing.
    assert state.status == "active"
    assert state.plugins == ("replay_a", "replay_b")
    closes = sum(1 for r in records if r.get("type") == "update" and r["status"] == "closed")
    assert len(state.applied) + len(state.applied_prev) == closes
    # ... and booked each close's own net: the realized sum is the closed trades' sum
    assert len(nets) == closes
    assert any(n != 0 for n in nets)
    assert state.realized == sum(nets, Decimal("0.00"))
    assert state.balance == state.starting_balance + state.realized
