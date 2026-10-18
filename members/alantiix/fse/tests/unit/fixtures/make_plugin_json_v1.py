"""Capture ``plugin_json_v1_pre_accounts.json`` (design §12, BC-3).

Captured from base commit ``0830838`` (``feat(paper_ops): --partial mode for
the daily check``), before paper accounts existed: the file is exactly the
``plugin.json`` that base ``PluginHost`` wrote (``BROKER_CODEC`` book), so a
restore test on newer code proves the old format still loads. It holds one
open trade (``t1``, MNQ long 1 with its stop and target working), one working
resting entry (``t2``) and a non-empty bar ring.

Run from the project root::

    PYTHONPATH=src:. python tests/unit/fixtures/make_plugin_json_v1.py [--out DIR]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Final

from fse.live.host.host import PLUGIN_STATE_FILE_NAME, BarsEvent
from fse.live.plugins import EnterMarket, EnterResting, Intent, TargetLeg
from fse.timekit import NS_PER_MINUTE, NS_PER_SECOND
from tests.fakes.clock import FakeClock
from tests.fakes.dataplane import FakeDataPlane, host_entry, make_bar, make_host, ny
from tests.fakes.plugins import RecordingPlugin

__all__ = ["BASE_COMMIT", "FIXTURE_PATH", "PLUGIN_ID", "T0", "capture"]

BASE_COMMIT: Final = "0830838"
HERE: Final = Path(__file__).resolve().parent
FIXTURE_PATH: Final = HERE / "plugin_json_v1_pre_accounts.json"
PLUGIN_ID: Final = "p1"
WED: Final = date(2026, 10, 14)
T0: Final = ny(WED, 10, 0)
"""The first bar opens here; the capture stops after the 10:03 bar."""


def _bars(minute: int) -> BarsEvent:
    price = 80_000 + 2 * minute
    bar = make_bar("MNQ", T0 + minute * NS_PER_MINUTE, price, price + 4, price - 4, price + 1)
    return BarsEvent((bar,), bar.close_ns + 2 * NS_PER_SECOND)


def _at(minute: int) -> int:
    return T0 + minute * NS_PER_MINUTE + 2 * NS_PER_SECOND


def capture(tmp_path: Path) -> str:
    """Run the base host over four bars; the ``plugin.json`` text it left."""
    clock = FakeClock(T0)
    script = [(_at(m + 1), _bars(m)) for m in range(4)]
    host = make_host(
        tmp_path,
        {PLUGIN_ID: RecordingPlugin},
        [host_entry(PLUGIN_ID)],
        clock,
        FakeDataPlane(clock, script),
    )
    plugin = host.plugin(PLUGIN_ID)
    assert isinstance(plugin, RecordingPlugin)

    def on_bar(bar: Any, *_: Any) -> list[Intent]:
        if bar.open_ns == T0:
            return [
                EnterMarket("t1", "MNQ", "long", 1, 40, (TargetLeg("tp1", 1, 80),), bar.close_ns)
            ]
        if bar.open_ns == T0 + 2 * NS_PER_MINUTE:
            price = bar.c_t - 60
            return [
                EnterResting(
                    "t2", "MNQ", "long", 1, "limit", price, price - 40, (("tp1", 1, price + 40),)
                )
            ]
        return []

    plugin.handlers["on_bar"] = on_bar
    code = asyncio.run(clock.run(host.run(until_ns=_at(4) + NS_PER_SECOND)))
    if code != 0:
        raise RuntimeError(f"the host exited {code}")
    broker = host.broker(PLUGIN_ID)
    assert broker is not None
    if broker.open_tags() != ("t1",) or "t2" not in broker.working_tags():
        raise RuntimeError(f"unexpected book: {broker.open_tags()} {broker.working_tags()}")
    if not broker.state.ring.get("MNQ"):
        raise RuntimeError("the bar ring is empty")
    path = tmp_path / "live-state" / "plugins" / PLUGIN_ID / PLUGIN_STATE_FILE_NAME
    return path.read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture the BC-3 plugin.json fixture.")
    parser.add_argument("--out", type=Path, default=HERE, help="output directory")
    args = parser.parse_args(argv)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="plugin-json-v1-") as tmp:
        text = capture(Path(tmp))
    target = out / FIXTURE_PATH.name
    target.write_text(text, encoding="utf-8")
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
