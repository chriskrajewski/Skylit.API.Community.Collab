"""Unit tests for ``fse report`` on a run with trades (task 22.5).

The pinned :func:`~tests.strategies.backtest_inputs.funnel_case` run (every
final status, Shadow_Trades, two sessions with full data) goes through
``fse report``:

- the header says Holdout_Period sessions are included, and which (Req 20.15);
- the per-Gate rows and the co-rejection matrix match ``gate_funnel.json``,
  with win rate and mean R_Multiple "not available" at 0 filled (Req 19.10);
- the title carries the touch-fills label only at a trade-through of 0
  ticks (Req 13.2);
- the King and Gatekeeper agreement rates come from :func:`node_agreement`
  on labelled Snapshots (Req 6.22-6.23);
- a bad ``--run`` exits 2, a missing or aborted run exits 4.

**Validates: Requirements 6.22, 6.23, 13.2, 19.6, 19.7, 19.10, 20.15**
"""

from __future__ import annotations

import io
import json
import shutil
from dataclasses import asdict, replace
from datetime import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from fse import cli
from fse.backtest.manifest import MANIFEST_FILE_NAME
from fse.backtest.runner import FUNNEL_FILE_NAME, REPORT_INPUTS_FILE_NAME
from fse.backtest.stats import NodeAgreement, node_agreement
from fse.commands.report import REPORT_DIR_NAME, REPORT_MD_FILE_NAME
from fse.config.schema import StrategyConfig
from fse.engine.nodes import NodeParams
from fse.engine.types import Snapshot
from fse.reports.markdown import NOT_AVAILABLE
from fse.reports.tables import TOUCH_FILLS_LABEL
from fse.timekit import ny_instant
from tests.strategies.backtest_inputs import (
    SESSIONS,
    funnel_case,
    make_snapshot,
    run,
    write_cache,
    write_calendars,
)

RUN_ID = "funnel"
TOUCH_RUN_ID = "touch"


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The funnel_case run and its 0-tick trade-through variant, in one runs folder."""
    root = tmp_path_factory.mktemp("report")
    market, cfg = funnel_case()
    cache = write_cache(root / "cache", market)
    calendars = write_calendars(root / "calendars")
    run(cache, calendars, cfg, market.data_range, root / "runs" / RUN_ID)
    data = cfg.model_dump(mode="python", by_alias=True)
    data["fills"]["trade_through_ticks"] = 0
    touch = StrategyConfig.model_validate(data)
    run(cache, calendars, touch, market.data_range, root / "runs" / TOUCH_RUN_ID)
    return root / "runs"


def copied(runs: Path, tmp_path: Path, run_id: str = RUN_ID) -> Path:
    """A copy of the runs folder holding ``run_id``, so a test can change its files."""
    shutil.copytree(runs / run_id, tmp_path / run_id)
    return tmp_path


def report(runs_dir: Path, run_id: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = cli.run(
        ["report", "--run", run_id, "--runs-dir", str(runs_dir)],
        project_dir=None,
        environ={},
        stdout=out,
        stderr=err,
    )
    return code, out.getvalue(), err.getvalue()


def markdown(runs_dir: Path, run_id: str) -> list[str]:
    code, _, err = report(runs_dir, run_id)
    assert (code, err) == (0, "")
    path = runs_dir / run_id / REPORT_DIR_NAME / REPORT_MD_FILE_NAME
    return path.read_text(encoding="utf-8").splitlines()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def table_after(lines: list[str], heading: str) -> list[list[str]]:
    """The cells of the first Markdown table after ``heading``, without its header rows."""
    start = lines.index(heading)
    first = next(i for i in range(start, len(lines)) if lines[i].startswith("| "))
    rows: list[list[str]] = []
    for line in lines[first + 2 :]:
        if not line.startswith("| "):
            break
        rows.append([c.strip() for c in line.strip("|").split(" | ")])
    return rows


def two_places(value: str, suffix: str) -> str:
    return f"{Decimal(value).quantize(Decimal('0.01'))}{suffix}"


# ---------------------------------------------------------------- the header


def test_the_header_lists_the_holdout_sessions_included(runs: Path, tmp_path: Path) -> None:
    folder = copied(runs, tmp_path)
    held = read_json(folder / RUN_ID / REPORT_INPUTS_FILE_NAME)["holdout"]
    assert held["included"] == [SESSIONS[1].isoformat()]  # the newer of the two sessions

    code, out, err = report(folder, RUN_ID)
    md = folder / RUN_ID / REPORT_DIR_NAME / REPORT_MD_FILE_NAME
    lines = md.read_text(encoding="utf-8").splitlines()

    assert (code, err) == (0, "")
    assert "Holdout_Period sessions included: 1" in out
    line = next(x for x in lines if x.startswith("- Holdout_Period sessions included:"))
    assert line.startswith(f"- Holdout_Period sessions included: yes, 1 session(s): {SESSIONS[1]}")
    assert (
        f"(Holdout_Period {held['first']} to {held['last']}, {held['sessions']} sessions)" in line
    )


def test_only_a_zero_tick_trade_through_carries_the_touch_fills_label(
    runs: Path, tmp_path: Path
) -> None:
    folder = copied(runs, tmp_path)
    copied(runs, tmp_path, TOUCH_RUN_ID)

    plain = markdown(folder, RUN_ID)
    touch = markdown(folder, TOUCH_RUN_ID)

    assert TOUCH_FILLS_LABEL not in plain[0]
    assert touch[0].endswith(f": {TOUCH_FILLS_LABEL}")
    fills = next(x for x in touch if x.startswith("- Fill_Simulator:"))
    assert "trade-through 0 tick(s)" in fills
    assert fills.endswith(TOUCH_FILLS_LABEL)


# ---------------------------------------------------------------- the funnel


def test_the_gate_table_and_co_rejection_matrix_match_the_funnel(
    runs: Path, tmp_path: Path
) -> None:
    folder = copied(runs, tmp_path)
    funnel = read_json(folder / RUN_ID / FUNNEL_FILE_NAME)
    lines = markdown(folder, RUN_ID)

    expected = []
    for g in funnel["gates"]:
        sh = g["only_rejected_shadows"]
        if sh["filled"] == 0:
            rate, mean = NOT_AVAILABLE, NOT_AVAILABLE
        else:
            rate, mean = two_places(sh["win_rate_pct"], "%"), two_places(sh["mean_r"], " R")
        expected.append(
            [
                g["gate_id"],
                str(g["failing"]),
                str(g["only_failing"]),
                str(g["first_failing"]),
                str(sh["filled"]),
                str(sh["not_filled"]),
                rate,
                mean,
            ]
        )
    rows = table_after(lines, "### Gates")
    assert [r[:8] for r in rows] == expected
    assert any(r[6:8] == [NOT_AVAILABLE, NOT_AVAILABLE] for r in rows)  # a Gate with 0 filled
    assert any(r[6] != NOT_AVAILABLE for r in rows)  # and one with filled shadows

    matrix = funnel["co_rejection"]
    assert table_after(lines, "### Co-rejection matrix") == [
        [g, *(str(n) for n in row)] for g, row in zip(matrix["gates"], matrix["rows"], strict=True)
    ]


# ---------------------------------------------------------------- agreement


PARAMS = NodeParams(0.20, 0.30, 0.5, 1.0)
PAIRS = ((5700.0, 100.0), (5750.0, 50.0), (5850.0, 40.0), (5900.0, 60.0))
"""Spot 5800: King 5700; Gatekeepers 5750 (below spot) and 5850 (above spot)."""


def labelled(node_types: tuple[str | None, ...] | None) -> Snapshot:
    at = ny_instant(SESSIONS[0], time(10, 0))
    return replace(
        make_snapshot(SESSIONS[0], "SPX", "gamma", at, 5800.0, PAIRS), node_types=node_types
    )


def test_agreement_rates_come_from_the_labelled_snapshots(runs: Path, tmp_path: Path) -> None:
    snapshots = (
        labelled(("King", "Gatekeeper", None, "gatekeeper")),  # King agrees; 1 of 3 Gatekeepers
        labelled((None, None, None, " KING ")),  # King disagrees; 0 of 2 Gatekeepers
        labelled(None),  # a range frame: no labels
        labelled((None, "", None, None)),  # labels all empty
    )
    agreement = node_agreement(snapshots, PARAMS)
    assert agreement == NodeAgreement(
        compared=2, without_labels=2, king_agree=1, gatekeeper_both=1, gatekeeper_either=5
    )

    folder = copied(runs, tmp_path)
    path = folder / RUN_ID / REPORT_INPUTS_FILE_NAME
    inputs = read_json(path)
    inputs["node_agreement"] = asdict(agreement)
    path.write_text(json.dumps(inputs), encoding="utf-8")
    lines = markdown(folder, RUN_ID)

    assert "- Snapshots compared: 2; Snapshots without nodeType labels: 2" in lines
    assert "- King agreement: 50.00% (1 of 2 Snapshots)" in lines
    assert "- Gatekeeper agreement: 20.00% (1 of 5 strikes)" in lines


def test_agreement_is_not_available_without_labels(runs: Path, tmp_path: Path) -> None:
    folder = copied(runs, tmp_path)
    lines = markdown(folder, RUN_ID)

    assert f"- King agreement: {NOT_AVAILABLE}" in lines
    assert f"- Gatekeeper agreement: {NOT_AVAILABLE}" in lines


# ---------------------------------------------------------------- exit codes


@pytest.mark.parametrize("run_id", ["", "../funnel", "a/b"])
def test_a_bad_run_exits_2(runs: Path, tmp_path: Path, run_id: str) -> None:
    folder = copied(runs, tmp_path)

    code, out, err = report(folder, run_id)

    assert (code, out) == (2, "")
    assert err.startswith("error:")
    assert not (folder / RUN_ID / REPORT_DIR_NAME).exists()


def test_a_missing_run_exits_4(tmp_path: Path) -> None:
    code, _, err = report(tmp_path, "no-such-run")

    assert code == 4
    assert MANIFEST_FILE_NAME in err


def test_an_aborted_run_exits_4(runs: Path, tmp_path: Path) -> None:
    folder = copied(runs, tmp_path)
    path = folder / RUN_ID / MANIFEST_FILE_NAME
    manifest = read_json(path)
    manifest["status"] = "aborted"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    code, out, err = report(folder, RUN_ID)

    assert (code, out) == (4, "")
    assert "status aborted" in err
    assert not (folder / RUN_ID / REPORT_DIR_NAME).exists()
