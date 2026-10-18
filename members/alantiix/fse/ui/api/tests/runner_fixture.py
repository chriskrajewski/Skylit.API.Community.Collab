"""The runner config the console tests start from.

The shipped ``configs/runner.yaml`` runs the paper-only ``template`` plugin. Most console
tests exercise Order mode, which needs a plugin that may send broker orders, so they start
from the shipped file with its ``template`` entry turned into ``alpha``: a plugin that
declares ``broker_orders`` (``tests.order_plugins:AlphaPlugin``), may hold through the
break and has a larger contract cap. Everything else (data plane, comments, playbook entry)
is the shipped text.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fse_ui.configs import ConfigStore

__all__ = ["baseline_text", "runner_text", "seed_configs"]

_EXTRA_PLUGINS = """\
  - id: template
    module: fse.live.plugins.template:TemplatePlugin
    enabled: false
    config: {instrument: MNQ, entry_time: "10:00", direction: long, qty: 1,
             stop_ticks: 40, target_ticks: 40, flatten_time: "15:55", warmup_bars: 5}
    fills:
      trade_through_ticks: 1
      slippage_ticks: 0
      stop_fill: at_stop
      commission_rt: {MNQ: "1.24"}
    risk:
      max_contracts: 1
      max_open_trades: 1
    hold_through_break: false
  - id: bravo
    module: fse.live.plugins.template:TemplatePlugin
    enabled: false
    config: {instrument: MNQ, entry_time: "11:00", direction: short, qty: 1,
             stop_ticks: 40, target_ticks: 40, flatten_time: "15:55", warmup_bars: 5}
    fills:
      trade_through_ticks: 0
      slippage_ticks: 0
      stop_fill: at_stop
      commission_rt: {MNQ: "1.24"}
    risk: {max_contracts: 3, max_open_trades: 1}
    hold_through_break: false
  - id: charlie
    module: fse.live.plugins.template:TemplatePlugin
    enabled: false
    config: {instrument: MES, entry_time: "12:00", direction: long, qty: 1,
             stop_ticks: 40, target_ticks: 40, flatten_time: "15:55", warmup_bars: 5}
    fills:
      trade_through_ticks: 1
      slippage_ticks: 0
      stop_fill: at_stop
      commission_rt: {MES: "1.90"}
    risk: {max_contracts: 8, max_open_trades: 4}
    hold_through_break: false
"""


def runner_text(project: Path) -> str:
    """The shipped ``runner.yaml`` with its first plugin entry replaced by ``alpha``."""
    text = (project / "configs" / "runner.yaml").read_text(encoding="utf-8")
    pairs = (
        ("  - id: template\n", "  - id: alpha\n"),
        (
            "module: fse.live.plugins.template:TemplatePlugin",
            "module: tests.order_plugins:AlphaPlugin",
        ),
        (
            "      max_contracts: 1\n",
            "      max_contracts: 16                     # the largest Alpha qty\n",
        ),
        ("    hold_through_break: false\n", "    hold_through_break: true\n"),
    )
    for old, new in pairs:
        assert old in text, old
        text = text.replace(old, new, 1)
    return text + _EXTRA_PLUGINS


def baseline_text(project: Path) -> str:
    """The shipped ``playbook_baseline.yaml`` with the values the Operator must enter.

    The shipped file leaves out ``regime.min_abs_value`` and the fees, so the engine
    rejects it. The console tests need a baseline that loads: this adds fake values.
    """
    text = (project / "configs" / "playbook_baseline.yaml").read_text(encoding="utf-8")
    fees = '{commission: "0.00", exchange_fee: "1.00"}'
    pairs = (
        ("\nregime:\n", "\nregime:\n  min_abs_value: 1000000.0\n"),
        ("MES: {} # OPERATOR: required", f"MES: {fees}"),
        ("MNQ: {} # OPERATOR: required", f"MNQ: {fees}"),
    )
    for old, new in pairs:
        assert old in text, old
        text = text.replace(old, new, 1)
    return text


def seed_configs(store: ConfigStore, project: Path) -> None:
    """Put the test runner and baseline configs in the console's store.

    The console seeds only names its store does not hold yet, so these replace the
    shipped files (see :func:`runner_text` and :func:`baseline_text`).
    """
    for kind, name, text in (
        ("runner", "runner", runner_text(project)),
        ("strategy", "playbook_baseline", baseline_text(project)),
    ):
        store._write_revision(  # noqa: SLF001
            kind,
            name,
            text,
            author="seed",
            message=f"from configs/{name}.yaml",
            origin=f"configs/{name}.yaml",
        )
