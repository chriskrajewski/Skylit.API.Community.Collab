"""NF-3: a paper account and the trade journal add no measurable host-loop latency.

Three plugins share one account over the 240 MNQ bars of the BC-2 replay
fixture, against a control run of the same plugins with no account and the
journal step patched out (the pre-feature path). Most of a dispatch step is
the existing fsynced ``plugin.json`` writes, whose single-step latency follows
the disk, so the budget is on the median step: the feature adds at most half
of the control's median plus 2 ms, and stays far inside the per-hook warning
budget (``HOOK_LATENCY_WARN_NS``, 1 s). Wall-clock budgets are flaky under a
loaded test run, so this runs only with ``-m perf``, like the other budget
tests here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fse.live.host.host import HOOK_LATENCY_WARN_NS, PluginHost
from tests.unit.test_plugin_host_accounts import REPLAY_MNQ, host_log, run_three_members

pytestmark = pytest.mark.perf

_MS = 1_000_000


def _median(steps: list[int]) -> int:
    return sorted(steps)[len(steps) // 2]


def test_account_and_journal_add_no_measurable_dispatch_latency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with_feature: list[int] = []
    run_three_members(tmp_path / "on", with_feature)
    assert host_log(tmp_path / "on", "hook_latency") == []
    assert len(with_feature) >= len(REPLAY_MNQ)

    control: list[int] = []
    with monkeypatch.context() as m:
        m.setattr(PluginHost, "_journal", lambda self, slot, u, batch: None)
        run_three_members(tmp_path / "off", control, with_account=False)

    on, off = _median(with_feature), _median(control)
    assert on <= off * 3 // 2 + 2 * _MS, (on, off)
    assert on < HOOK_LATENCY_WARN_NS // 20
