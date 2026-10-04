"""Unit tests for ``fse experiment`` (``fse.commands.experiment``) and the experiment reports.

Every run uses the synthetic Data_Cache and calendar files of
``tests/strategies/backtest_inputs`` under ``tmp_path`` and the real
Backtester: no network, no key.

- Each kind exits 0 and writes its JSON output, its Run_Manifest and its
  Markdown report, whose header states the Holdout_Period inclusion and the
  measured-not-forecast statement (Req 20.15).
- The ablation report gives base, variant and change per metric and the
  distinct configuration count (Req 19.14, 22.15); the frontier report the
  Pareto line and the reference statement (Req 20.9-20.11); each sweep
  configuration's backtest draws its bootstrap intervals once, with the
  sweep's resample count.
- The pass estimate is labeled "insufficient sample" below the minimum
  sessions (Req 21.11); a run that is missing or holds a non-finite session
  value is rejected with exit 4 and nothing is written (Req 21.12).
- A second holdout evaluation warns and the Holdout_Log holds two lines
  (Req 22.5, 22.7).
- The walk-forward test runs the real Backtester on 16 sessions; its pooled
  Combine_Pass estimates equal the Monte_Carlo_Simulator estimate over the
  session outcomes the test-window and training-window backtests wrote
  (Req 22.14).
- Invalid input (a bad sweep definition, a cadence no session supports, no
  walk-forward candidate) exits 2 and writes nothing.

**Validates: Requirements 18.10, 19.14, 20.9, 20.10, 20.11, 21.6, 21.7, 21.11, 22.5,
22.14, 22.15**
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from fse import cli
from fse.analytics.montecarlo import estimate
from fse.backtest.manifest import MANIFEST_FILE_NAME
from fse.backtest.runner import SESSION_OUTCOMES_FILE_NAME, SessionOutcome
from fse.config.printer import dump
from fse.config.schema import StrategyConfig
from fse.reports.markdown import MEASURED_STATEMENT, PASS_INSUFFICIENT_LABEL
from tests.strategies.backtest_inputs import (
    MarketInputs,
    bar_opens,
    build_session,
    fixed_config,
    funnel_case,
    write_cache,
    write_calendars,
)
from tests.strategies.experiment_inputs import ALL_SESSIONS

START, END = "2026-03-02", "2026-03-13"
WF_SESSIONS = ALL_SESSIONS[-16:]


@dataclass(frozen=True)
class Paths:
    root: Path
    config: Path
    cache: Path
    calendars: Path

    def run(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = cli.run(list(argv), project_dir=None, environ={}, stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def experiment(self, kind: str, *argv: str) -> tuple[int, str, str]:
        return self.run(
            "experiment", kind, *argv, "--cache-dir", str(self.cache),
            "--calendar-dir", str(self.calendars),
        )  # fmt: skip


@pytest.fixture(scope="module")
def paths(tmp_path_factory: pytest.TempPathFactory) -> Paths:
    root = tmp_path_factory.mktemp("experiment-command")
    market, cfg = funnel_case()
    config = root / "config.yaml"
    config.write_text(dump(cfg), encoding="utf-8")
    return Paths(
        root, config, write_cache(root / "cache", market), write_calendars(root / "calendars")
    )


def _walkforward_config(cfg: StrategyConfig) -> StrategyConfig:
    data = cfg.model_dump(mode="python", by_alias=True)
    data["experiments"].update(
        {
            "holdout_fraction": 0.05,
            "walkforward": {"train": 10, "test": 5, "min_trades": 1},
            "montecarlo": {"paths": 1000, "min_sessions": 1},
        }
    )
    return StrategyConfig.model_validate(data)


@pytest.fixture(scope="module")
def wf_paths(tmp_path_factory: pytest.TempPathFactory) -> Paths:
    root = tmp_path_factory.mktemp("walkforward-command")
    market = MarketInputs(
        tuple(
            build_session(d, 2000 + i, bar_opens(d, 60, (100, 40)))
            for i, d in enumerate(WF_SESSIONS)
        )
    )
    _, gated = funnel_case()
    base = root / "base.yaml"
    base.write_text(dump(_walkforward_config(fixed_config(300))), encoding="utf-8")
    (root / "gated.yaml").write_text(dump(_walkforward_config(gated)), encoding="utf-8")
    return Paths(root, base, write_cache(root / "cache", market), write_calendars(root / "cal"))


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def assert_header(md: str, included: str) -> None:
    assert "## Run" in md
    assert f"- Holdout_Period sessions included: {included}" in md
    assert MEASURED_STATEMENT in md
    assert md.index(MEASURED_STATEMENT) < md.index("| ")  # before the first table


def test_ablation_writes_the_table_and_the_report(paths: Paths, tmp_path: Path) -> None:
    out = tmp_path / "ablation"
    code, stdout, err = paths.experiment(
        "ablation", "--config", str(paths.config), "--start", START, "--end", END,
        "--seed", "5", "--out", str(out),
    )  # fmt: skip

    assert (code, err) == (0, "")
    for name in ("ablation.json", "comparison.json", MANIFEST_FILE_NAME, "ablation.md"):
        assert (out / name).is_file(), name
    md = (out / "ablation.md").read_text(encoding="utf-8")
    assert_header(md, "no; every Holdout_Period session (2026-03-03 to 2026-03-03, 1 sessions)")
    assert "Distinct configurations evaluated: 4" in md
    ablation = read(out / "ablation.json")
    for variant in ablation["variants"]:
        assert f"| {variant['gate_id']} | Combine_Pass probability |" in md
        assert f"| {variant['gate_id']} | Trades per day |" in md
    assert f"Report: {out / 'ablation.md'}" in stdout


def test_sweep_writes_the_frontier_report_and_draws_intervals_once(
    paths: Paths, tmp_path: Path
) -> None:
    sweep = tmp_path / "sweep.yaml"
    sweep.write_text(
        "exit_modes: [fixed_r, next_node]\nr_multiples: [1, 2]\nbootstrap_resamples: 1000\n",
        encoding="utf-8",
    )
    out = tmp_path / "sweep"
    code, _, err = paths.experiment(
        "sweep", "--config", str(paths.config), "--sweep", str(sweep), "--start", START,
        "--end", END, "--seed", "6", "--out", str(out),
    )  # fmt: skip

    assert (code, err) == (0, "")
    frontier = read(out / "frontier.json")
    md = (out / "frontier.md").read_text(encoding="utf-8")
    assert_header(md, "no; every Holdout_Period session (2026-03-03 to 2026-03-03, 1 sessions)")
    assert "Pareto set (expectancy in R, Primary_Win_Rate, Combine_Pass probability): " in md
    assert "No configuration met the reference win rate." in md
    assert len(frontier["rows"]) == 3
    for row in frontier["rows"]:
        backtest = out / "configs" / f"{row['index']:03d}-{row['name']}" / "backtest"
        assert read(backtest / MANIFEST_FILE_NAME)["bootstrap"]["resamples"] == 1000
        stored = read(backtest / "report_inputs.json")["bootstrap"]
        assert stored["primary_win_rate_pct"] == row["intervals"]["primary_win_rate_pct"]
        assert stored["expectancy_r"] == row["intervals"]["expectancy_r"]


def test_an_invalid_sweep_definition_exits_2_and_writes_nothing(
    paths: Paths, tmp_path: Path
) -> None:
    sweep = tmp_path / "sweep.yaml"
    sweep.write_text("exit_modes: []\nr_multiples: [0, 11]\n", encoding="utf-8")
    out = tmp_path / "sweep"
    code, _, err = paths.experiment(
        "sweep", "--config", str(paths.config), "--sweep", str(sweep), "--start", START,
        "--end", END, "--out", str(out),
    )  # fmt: skip

    assert code == 2
    for problem in ("no Exit_Mode", "r_multiples[0]", "r_multiples[1]"):
        assert problem in err
    assert not out.exists()


def test_cadence_comparison_reports_shorter_minus_longer(paths: Paths, tmp_path: Path) -> None:
    out = tmp_path / "cadence"
    code, _, err = paths.experiment(
        "cadence", "--config", str(paths.config), "--start", START, "--end", END,
        "--cadences", "60,300", "--seed", "7", "--out", str(out),
    )  # fmt: skip

    assert (code, err) == (0, "")
    md = (out / "cadence.md").read_text(encoding="utf-8")
    assert_header(md, "no; every Holdout_Period session (2026-03-03 to 2026-03-03, 1 sessions)")
    assert "| 60 s minus 300 s |" in md
    assert "Included sessions: 2026-03-02" in md
    assert "Distinct configurations evaluated: 2" in md


def test_a_cadence_no_session_supports_exits_2_and_writes_nothing(
    paths: Paths, tmp_path: Path
) -> None:
    out = tmp_path / "cadence"
    code, _, err = paths.experiment(
        "cadence", "--config", str(paths.config), "--start", START, "--end", END,
        "--out", str(out),
    )  # fmt: skip

    assert code == 2
    assert "supports the shortest compared cadence of 5 s" in err
    assert not out.exists() or not any(out.iterdir())


def test_two_holdout_evaluations_append_two_entries_and_warn(paths: Paths, tmp_path: Path) -> None:
    log = tmp_path / "logs" / "holdout_log.jsonl"
    log.parent.mkdir()
    argv = ("--config", str(paths.config), "--holdout-log", str(log))
    first = paths.experiment("holdout", *argv, "--seed", "1", "--out", str(tmp_path / "h1"))
    second = paths.experiment("holdout", *argv, "--seed", "2", "--out", str(tmp_path / "h2"))

    assert first[0] == second[0] == 0
    assert "no longer unseen" not in first[2]
    assert "no longer unseen: 1 earlier Holdout_Log entry" in second[2]
    assert len(log.read_text(encoding="utf-8").splitlines()) == 2
    md = (tmp_path / "h2" / "holdout.md").read_text(encoding="utf-8")
    assert_header(md, "yes; every session is a Holdout_Period session (2026-03-03 to 2026-03-03")
    assert "Warning: the Holdout_Period (2026-03-03 to 2026-03-03) is no longer unseen" in md
    assert "| Overlapping Holdout_Log entries | 1 |" in md


def _backtest(paths: Paths, out: Path) -> None:
    code, _, err = paths.run(
        "backtest", "--config", str(paths.config), "--start", START, "--end", END,
        "--seed", "3", "--out", str(out), "--cache-dir", str(paths.cache),
        "--calendar-dir", str(paths.calendars),
    )  # fmt: skip
    assert (code, err) == (0, "")


def test_montecarlo_labels_an_insufficient_sample(paths: Paths, tmp_path: Path) -> None:
    _backtest(paths, tmp_path / "bt")
    out = tmp_path / "mc"
    code, _, err = paths.run(
        "experiment", "montecarlo", "--run", str(tmp_path / "bt"), "--config", str(paths.config),
        "--seed", "9", "--out", str(out),
    )  # fmt: skip

    assert (code, err) == (0, "")
    est = read(out / "pass_estimate.json")
    assert est["insufficient_sample"] is True
    assert est["passed"] + est["failed"] + est["unresolved"] == est["paths"]
    md = (out / "pass_estimate.md").read_text(encoding="utf-8")
    assert_header(md, "yes, 1 session(s): 2026-03-03")
    assert f"| Pass probability | {Decimal(est['passed']) / est['paths']:.4f} " in md
    assert PASS_INSUFFICIENT_LABEL in md


@pytest.mark.parametrize("damage", ["missing", "nan"])
def test_montecarlo_of_a_bad_run_exits_4_and_writes_nothing(
    paths: Paths, tmp_path: Path, damage: str
) -> None:
    run = tmp_path / "bt"
    if damage == "nan":
        _backtest(paths, run)
        outcomes = read(run / SESSION_OUTCOMES_FILE_NAME)
        outcomes[0]["net"] = "NaN"
        (run / SESSION_OUTCOMES_FILE_NAME).write_text(json.dumps(outcomes), encoding="utf-8")
    out = tmp_path / "mc"
    code, _, err = paths.run(
        "experiment", "montecarlo", "--run", str(run), "--config", str(paths.config),
        "--out", str(out),
    )  # fmt: skip

    assert code == 4
    assert "does not exist" in err if damage == "missing" else "not finite" in err
    assert not out.exists()


def _outcomes(run_dir: Path) -> list[SessionOutcome]:
    return [
        SessionOutcome(
            date.fromisoformat(o["session"]), Decimal(o["net"]), Decimal(o["intraday_low"]),
            o["truncated_by_run_account"],
        )
        for o in read(run_dir / SESSION_OUTCOMES_FILE_NAME)
    ]  # fmt: skip


def test_walkforward_pools_the_real_backtester_outcomes(wf_paths: Paths, tmp_path: Path) -> None:
    out = tmp_path / "wf"
    code, _, err = wf_paths.experiment(
        "walkforward", "--config", str(wf_paths.config), "--candidate", str(wf_paths.config),
        "--candidate", str(wf_paths.root / "gated.yaml"), "--start", str(WF_SESSIONS[0]),
        "--end", str(WF_SESSIONS[-1]), "--seed", "4", "--out", str(out),
    )  # fmt: skip

    assert (code, err) == (0, "")
    wf = read(out / "walkforward.json")
    (pair,) = wf["pairs"]
    assert (pair["train"]["sessions"], pair["test"]["sessions"]) == (10, 5)
    assert pair["selection"] == "selected"
    chosen = pair["selected"]
    names = [c["name"] for c in read(out / pair["train_dir"] / "comparison.json")["configurations"]]
    position = names.index(chosen)
    train_bt = out / pair["train_dir"] / "configs" / f"{position:03d}-{chosen}" / "backtest"
    test_bt = out / pair["test_dir"] / "configs" / f"000-{chosen}" / "backtest"
    cfg = _walkforward_config(fixed_config(300))
    for pooled, run_dir in ((wf["in_sample"], train_bt), (wf["out_of_sample"], test_bt)):
        expected = estimate(
            _outcomes(run_dir), cfg.account, paths=1000, max_days=60, seed=4, min_sessions=1
        )
        assert pooled["pass_probability"] == str(expected.pass_probability)
        assert pooled["pass_estimate"]["passed"] == expected.passed
        assert pooled["trade_count"] == len(read(run_dir / "trades.json"))
    md = (out / "walkforward.md").read_text(encoding="utf-8")
    assert_header(md, "no; every Holdout_Period session (2026-03-13 to 2026-03-13, 1 sessions)")
    assert '"No selection" window pairs: 0' in md
    assert "| Out-of-sample (concatenated test windows) |" in md


def test_walkforward_without_a_candidate_exits_2(wf_paths: Paths, tmp_path: Path) -> None:
    out = tmp_path / "wf"
    code, _, err = wf_paths.experiment(
        "walkforward", "--config", str(wf_paths.config), "--start", str(WF_SESSIONS[0]),
        "--end", str(WF_SESSIONS[-1]), "--out", str(out),
    )  # fmt: skip

    assert code == 2
    assert "--candidate FILE or a --sweep FILE" in err
    assert not out.exists()


def test_a_reused_experiment_directory_exits_2_and_is_left_unchanged(
    paths: Paths, tmp_path: Path
) -> None:
    out = tmp_path / "cadence"
    argv = ("--config", str(paths.config), "--start", START, "--end", END, "--cadences",
            "60,300", "--seed", "8", "--out", str(out))  # fmt: skip
    assert paths.experiment("cadence", *argv)[0] == 0
    before = {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}

    code, _, err = paths.experiment("cadence", *argv)

    assert code == 2
    assert "already holds" in err
    assert {p: p.read_bytes() for p in out.rglob("*") if p.is_file()} == before
