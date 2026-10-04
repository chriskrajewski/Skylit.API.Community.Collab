"""Unit tests for ``fse backtest`` (``fse.commands.backtest``): exits, output files and the seed.

Every run uses the synthetic Data_Cache and calendar files of
``tests/strategies/backtest_inputs`` (the pinned early-close session
2026-03-06) under ``tmp_path``. The command reads only that cache: no
network, no key.

- A completed run exits 0, writes every Backtester output and records the
  ``--seed`` (or a drawn seed it prints) and the config path in the
  Run_Manifest.
- Invalid input exits 2 before any output file: an end date before the
  start, a range without a session, a bad ``--seed``, a missing or invalid
  Strategy_Config (the shipped Playbook_Baseline lacks its Operator values).
- A run directory that already holds a backtest output is refused with exit
  2 and left unchanged.

**Validates: Requirements 18.1, 18.2, 18.4**
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from fse import cli
from fse.backtest.manifest import MANIFEST_FILE_NAME
from fse.backtest.runner import BACKTEST_OUTPUT_FILES
from fse.config.printer import dump
from tests.strategies.backtest_inputs import (
    early_close_hold,
    fixed_config,
    write_cache,
    write_calendars,
)

DAY = "2026-03-06"
BASELINE = Path(__file__).resolve().parents[2] / "configs" / "playbook_baseline.yaml"


@dataclass(frozen=True)
class Paths:
    root: Path
    config: Path
    cache: Path
    calendars: Path

    def run(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = cli.run(
            [
                "backtest",
                *argv,
                "--cache-dir",
                str(self.cache),
                "--calendar-dir",
                str(self.calendars),
            ],
            project_dir=None,
            environ={},
            stdout=out,
            stderr=err,
        )
        return code, out.getvalue(), err.getvalue()


@pytest.fixture(scope="module")
def paths(tmp_path_factory: pytest.TempPathFactory) -> Paths:
    root = tmp_path_factory.mktemp("backtest-command")
    config = root / "config.yaml"
    config.write_text(dump(fixed_config(1800)), encoding="utf-8")
    return Paths(
        root,
        config,
        write_cache(root / "cache", early_close_hold()),
        write_calendars(root / "calendars"),
    )


def manifest(run_dir: Path) -> dict[str, object]:
    loaded: dict[str, object] = json.loads((run_dir / MANIFEST_FILE_NAME).read_text("utf-8"))
    return loaded


def test_a_run_writes_every_output_and_records_the_seed(paths: Paths, tmp_path: Path) -> None:
    out = tmp_path / "run"
    code, stdout, err = paths.run(
        "--config", str(paths.config), "--start", DAY, "--end", DAY, "--seed", "7",
        "--out", str(out),
    )  # fmt: skip

    assert (code, err) == (0, "")
    assert sorted(p.name for p in out.iterdir()) == sorted(BACKTEST_OUTPUT_FILES)
    m = manifest(out)
    assert (m["status"], m["seed"], m["config_path"]) == ("completed", 7, str(paths.config))
    assert m["sessions_evaluated"] == [DAY]
    assert "seed 7" in stdout
    assert f"Run_Manifest: {out / MANIFEST_FILE_NAME}" in stdout


def test_without_a_seed_one_is_drawn_printed_and_recorded(paths: Paths, tmp_path: Path) -> None:
    out = tmp_path / "run"
    code, stdout, _ = paths.run(
        "--config", str(paths.config), "--start", DAY, "--end", DAY, "--out", str(out)
    )

    assert code == 0
    seed = manifest(out)["seed"]
    assert isinstance(seed, int)
    assert f"seed {seed};" in stdout


def test_offline_prints_the_sessions_with_absent_windows(paths: Paths, tmp_path: Path) -> None:
    code, stdout, _ = paths.run(
        "--config", str(paths.config), "--start", DAY, "--end", DAY, "--offline",
        "--seed", "1", "--out", str(tmp_path / "run"),
    )  # fmt: skip

    assert code == 0
    assert "requested sessions have absent or incomplete Cache_Windows" in stdout


def test_a_reused_run_directory_exits_2_and_is_left_unchanged(paths: Paths, tmp_path: Path) -> None:
    out = tmp_path / "run"
    argv = ("--config", str(paths.config), "--start", DAY, "--end", DAY, "--seed", "3")
    assert paths.run(*argv, "--out", str(out))[0] == 0
    before = {p.name: p.read_bytes() for p in out.iterdir()}

    code, _, err = paths.run(*argv, "--out", str(out))

    assert code == 2
    assert "already holds" in err
    assert {p.name: p.read_bytes() for p in out.iterdir()} == before


@pytest.mark.parametrize(
    ("argv", "reason"),
    [
        (["--start", DAY, "--end", "2026-03-05"], "is before the start date"),
        (["--start", "2026-03-07", "--end", "2026-03-08"], "contains no session"),
        (["--start", DAY, "--end", DAY, "--seed", "-1"], "is not a whole number"),
        (["--start", DAY, "--end", DAY, "--seed", "x"], "is not a whole number"),
        (["--start", "03/06/2026", "--end", DAY], "is not a YYYY-MM-DD date"),
    ],
)
def test_invalid_input_exits_2_and_writes_nothing(
    paths: Paths, tmp_path: Path, argv: list[str], reason: str
) -> None:
    out = tmp_path / "run"
    code, _, err = paths.run("--config", str(paths.config), *argv, "--out", str(out))

    assert code == 2
    assert reason in err
    assert not out.exists()


def test_the_shipped_baseline_exits_2_naming_the_operator_values(
    paths: Paths, tmp_path: Path
) -> None:
    out = tmp_path / "run"
    code, _, err = paths.run(
        "--config", str(BASELINE), "--start", DAY, "--end", DAY, "--out", str(out)
    )

    assert code == 2
    for key in (
        "regime.min_abs_value",
        "fills.costs.MES.commission",
        "fills.costs.MES.exchange_fee",
        "fills.costs.MNQ.commission",
        "fills.costs.MNQ.exchange_fee",
    ):
        assert key in err
    assert not out.exists()


def test_a_missing_config_file_exits_2(paths: Paths, tmp_path: Path) -> None:
    out = tmp_path / "run"
    code, _, _ = paths.run(
        "--config", str(tmp_path / "absent.yaml"), "--start", DAY, "--end", DAY,
        "--out", str(out),
    )  # fmt: skip

    assert code == 2
    assert not out.exists()
