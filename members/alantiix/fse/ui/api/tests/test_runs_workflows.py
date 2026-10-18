from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from fse_ui.catalog import Catalog, build_args
from fse_ui.runs import LocalArtifactStore, RunError, compare, equity_curve, run_metrics
from fse_ui.workflows import UploadError, parse_pull_estimate, save_upload
from tests.conftest import Console
from tests.fixtures import backtest_run


def test_equity_and_drawdown_on_a_known_sequence(tmp_path: Path) -> None:
    run = backtest_run(tmp_path, "bt-1", ["2", "-1", "-1", "3", "-1"], shadow=2)
    trades = json.loads((run / "trades.json").read_text())
    curve = equity_curve(trades)
    cum = [Decimal(p["cum_r"]) for p in curve["points"]]
    assert cum == [2, 1, 0, 3, 2]  # shadow trades are not in the curve
    assert Decimal(curve["max_drawdown_r"]) == -2
    assert Decimal(curve["max_drawdown_usd"]) == -50
    assert [Decimal(p["dd_r"]) for p in curve["points"]] == [0, -1, -2, 0, -1]


def test_metrics_come_from_the_engine(tmp_path: Path) -> None:
    backtest_run(tmp_path, "bt-1", ["2", "-1"])
    store = LocalArtifactStore(tmp_path)
    m = run_metrics(store, "bt-1")
    assert m["source"] == "trades.json"
    assert m["metrics"]["trade_count"] == 2
    assert m["metrics"]["session_count"] == 2
    assert Decimal(m["metrics"]["expectancy_r"]) == Decimal("0.5")


def test_missing_artifacts_are_unavailable_not_errors(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    store = LocalArtifactStore(tmp_path)
    assert run_metrics(store, "empty") == {"source": None, "metrics": None}


def test_path_traversal_is_refused(tmp_path: Path) -> None:
    backtest_run(tmp_path, "bt-1", ["1"])
    store = LocalArtifactStore(tmp_path)
    for bad in ("../x", "a/../../b", "/etc/passwd"):
        with pytest.raises(RunError):
            store.read_bytes("bt-1", bad)
    with pytest.raises(RunError):
        store.read_bytes("..", "trades.json")


def test_compare_deltas(tmp_path: Path) -> None:
    backtest_run(tmp_path, "a", ["2", "-1"])
    backtest_run(tmp_path, "b", ["1", "1", "1"])
    result = compare(LocalArtifactStore(tmp_path), ["a", "b"])
    row = next(r for r in result["rows"] if r["metric"] == "trade_count")
    assert row["cells"][1]["value"] == 3 and Decimal(row["cells"][1]["delta"]) == 1


def test_runs_api(console: Console) -> None:
    runs_dir = console.app.state.settings.runs_dir
    backtest_run(runs_dir, "bt-1", ["2", "-1"], shadow=1)
    listed = console.get("/api/runs").json()
    assert listed[0]["name"] == "bt-1" and listed[0]["kind"] == "backtest"
    detail = console.get("/api/runs/bt-1").json()
    assert "not a forecast" in detail["disclaimer"]
    assert console.get("/api/runs/bt-1/trades").json()["total"] == 2
    assert console.get("/api/runs/bt-1/trades", params={"shadow": True}).json()["total"] == 1
    eq = console.get("/api/runs/bt-1/equity").json()
    assert len(eq["points"]) == 2 and eq["histogram"]
    csv = console.get("/api/runs/bt-1/file", params={"name": "shadow_trades.csv"}).json()
    assert csv["total"] == 2 and csv["columns"] == ["setup", "gate", "r"]
    assert console.get("/api/runs/bt-1/file", params={"name": "../x.json"}).status_code == 404
    assert console.get("/api/runs/nope").status_code == 404


def _report(directory: Path, trade_count: int) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "report.json").write_text(json.dumps({"metrics": {"trade_count": trade_count}}))
    (directory / "report.md").write_text(f"# Report ({trade_count})\n")


def test_a_report_under_report_dir_is_found(console: Console) -> None:
    """G-1: ``fse report`` writes ``<run>/report/report.{json,md}``."""
    runs_dir = console.app.state.settings.runs_dir
    run = backtest_run(runs_dir, "bt-sub", ["2", "-1"])
    _report(run / "report", 99)
    assert console.get("/api/runs").json()[0]["has_report"] is True
    m = console.get("/api/runs/bt-sub/metrics").json()
    assert m == {"source": "report.json", "metrics": {"trade_count": 99}}
    md = console.get("/api/runs/bt-sub/file", params={"name": "report/report.md"})
    assert md.status_code == 200 and "Report (99)" in md.text


def test_a_report_written_into_an_existing_report_dir_is_seen(console: Console) -> None:
    """A write inside ``report/`` changes only that folder's mtime; the index sees it."""
    import os

    runs_dir = console.app.state.settings.runs_dir
    run = backtest_run(runs_dir, "bt-late", ["2", "-1"])
    (run / "report").mkdir()
    (run / "report" / "notes.txt").write_text("x\n")
    assert console.get("/api/runs").json()[0]["has_report"] is False
    root_mtime = run.stat().st_mtime_ns
    _report(run / "report", 7)
    later = root_mtime + 5_000_000_000
    os.utime(run / "report", ns=(later, later))  # coarse-mtime filesystems
    os.utime(run, ns=(root_mtime, root_mtime))  # the root did not change
    assert console.get("/api/runs").json()[0]["has_report"] is True


def test_the_root_report_wins_over_report_dir(tmp_path: Path) -> None:
    from fse_ui.runs import summarize_run

    run = backtest_run(tmp_path, "bt-both", ["2", "-1"])
    _report(run, 1)
    _report(run / "report", 2)
    store = LocalArtifactStore(tmp_path)
    assert summarize_run(store, "bt-both")["has_report"] is True
    assert run_metrics(store, "bt-both") == {"source": "report.json", "metrics": {"trade_count": 1}}


def test_a_run_without_a_report_still_has_none(tmp_path: Path) -> None:
    from fse_ui.runs import summarize_run

    backtest_run(tmp_path, "bt-none", ["2"])
    store = LocalArtifactStore(tmp_path)
    assert summarize_run(store, "bt-none")["has_report"] is False
    assert run_metrics(store, "bt-none")["source"] == "trades.json"
    assert run_metrics(store, "no-such-run") == {"source": None, "metrics": None}


# ---------------------------------------------------------------- workflows


WORKFLOW_EXAMPLES: list[tuple[str, dict[str, object]]] = [
    (
        "fse pull",
        {
            "start": "2026-09-01",
            "end": "2026-09-30",
            "symbols": "SPX,QQQ",
            "instruments": "ES,NQ",
            "dark_pool": True,
            "size_limit_gb": 1e-9,
        },
    ),
    (
        "fse backtest",
        {
            "config": "/data/configs/strategy/x/current.yaml",
            "start": "2026-01-02",
            "end": "2026-06-30",
            "seed": 42,
            "offline": True,
        },
    ),
    (
        "fse experiment ablation",
        {"config": "c.yaml", "start": "2026-01-02", "end": "2026-03-31", "workers": 2},
    ),
    (
        "fse experiment sweep",
        {"config": "c.yaml", "sweep": "s.yaml", "start": "2026-01-02", "end": "2026-03-31"},
    ),
    ("fse experiment montecarlo", {"run": "bt-1", "config": "c.yaml"}),
    ("fse experiment holdout", {"config": "c.yaml"}),
    (
        "fse experiment walkforward",
        {
            "config": "c.yaml",
            "start": "2026-01-02",
            "end": "2026-06-30",
            "candidate": ["a.yaml", "b.yaml"],
            "objective": "expectancy",
        },
    ),
    (
        "fse experiment cadence",
        {"config": "c.yaml", "start": "2026-01-02", "end": "2026-03-31", "cadences": "5,60"},
    ),
    (
        "fse calibrate regime-min-abs",
        {"start": "2026-01-02", "end": "2026-03-31", "percentile": 50},
    ),
    ("fse drafts build", {"chosen": "c.yaml", "start": "2026-01-02", "end": "2026-03-31"}),
    ("fse drafts promote", {"draft": "/tmp/d.md"}),
    ("fse report", {"run": "bt-1"}),
    ("fse import-vix", {"csv": "/tmp/vix.csv"}),
    ("fse export-aws", {"dry_run": True, "datasets": "bars"}),
    ("fse halt", {"reason": "operator pause"}),
    ("fse clear", {}),
    ("fse scan-secrets", {}),
    ("fse skilldocs check", {}),
    ("research.example.check", {"date": "2026-10-09"}),
]


@pytest.mark.parametrize(("command", "values"), WORKFLOW_EXAMPLES)
def test_workflow_values_are_accepted_by_the_real_parser(
    command: str, values: dict[str, object]
) -> None:
    cmd = Catalog().get(command)
    args = build_args(cmd, values)  # build_args runs parse_args; it raises on a usage error
    assert all(isinstance(a, str) for a in args)


def test_every_research_subcommand_accepts_its_required_args() -> None:
    catalog = Catalog()
    for cmd in catalog.commands():
        if not cmd.program.startswith("research."):
            continue
        values: dict[str, object] = {}
        group_dests = {d for g in cmd.groups for d in g["dests"]}
        for a in cmd.args:
            if not a["required"] or a["dest"] in group_dests:
                continue
            if a["choices"]:
                values[a["dest"]] = a["choices"][0]
            elif a["type"] == "int":
                values[a["dest"]] = 1
            elif a["type"] == "float":
                values[a["dest"]] = 1.0
            elif a["type"] == "date":
                values[a["dest"]] = "2026-01-02"
            else:
                values[a["dest"]] = "x"
        for g in cmd.groups:
            if g["required"]:
                values[g["dests"][0]] = "x"
        build_args(cmd, values)


def test_pull_estimate_parsing() -> None:
    lines = [
        "Pull estimate for the Cache_Windows this pull requests from Skylit: 21 sessions",
        "  scope  requests  disk GB",
        "  total       420   12.345",
        "error: Pull stopped: the estimated disk size 12.345 GB exceeds the size limit 1e-09 GB, "
        "and a non-interactive run cannot confirm it.",
        "exit 2 (invalid input)",
    ]
    result = parse_pull_estimate(lines)
    assert result["estimated_gb"] == pytest.approx(12.345)
    assert result["stopped_at_limit"] and len(result["lines"]) == 3
    assert result["suggested_limit_gb"] == pytest.approx(13.58)


def test_pull_estimate_parsing_skips_status_and_heartbeat_lines() -> None:
    table = [
        "Pull estimate for the Cache_Windows this pull requests from Skylit: 21 sessions",
        "  scope  requests  disk GB",
        "  total       420   12.345",
    ]
    lines = [
        "task e66f65bee16f4e45: RUNNING",
        "fse-entrypoint: downloaded cache in 4m05s (22.4 GB, 167654 files)",
        table[0],
        "task e66f65bee16f4e45: still RUNNING, no new output for 1m00s",
        table[1],
        "fse-entrypoint: still uploading cache, 30s elapsed",
        table[2],
        "error: Pull stopped: the estimated disk size 12.345 GB exceeds the size limit 1e-09 GB, "
        "and a non-interactive run cannot confirm it.",
    ]
    result = parse_pull_estimate(lines)
    assert result["lines"] == table
    assert result["estimated_gb"] == pytest.approx(12.345)
    assert result["suggested_limit_gb"] == pytest.approx(13.58)


def test_pull_estimate_submits_a_probe(console: Console) -> None:
    r = console.post(
        "/api/workflows/pull-estimate",
        json={"values": {"start": "2026-09-01", "end": "2026-09-02"}},
    )
    assert r.status_code == 201, r.text
    assert "--size-limit-gb=1e-09" in r.json()["argv"]


def test_upload_validation(tmp_path: Path) -> None:
    ok = save_upload(tmp_path, "vix.csv", b"date,open,high,low,close\n2026-01-02,1,2,0.5,1.5\n")
    assert len(ok["id"]) == 16
    with pytest.raises(UploadError, match=r"\.csv"):
        save_upload(tmp_path, "vix.exe", b"date,x\n")
    with pytest.raises(UploadError, match="larger than"):
        save_upload(tmp_path, "big.csv", b"a,b\n" + b"0" * (50 * 1024 * 1024))
    with pytest.raises(UploadError, match="UTF-8"):
        save_upload(tmp_path, "bin.csv", b"\xff\xfe\x00")
    with pytest.raises(UploadError, match="look like a CSV"):
        save_upload(tmp_path, "x.csv", b"just text\n")


def test_upload_then_import_vix_uses_the_stored_path(console: Console) -> None:
    files = {"file": ("vix.csv", b"date,close\n2026-01-02,15.2\n", "text/csv")}
    up = console.post("/api/uploads", files=files)
    assert up.status_code == 201, up.text
    r = console.post(
        "/api/jobs",
        json={"command": "fse import-vix", "values": {"csv": {"upload": up.json()["id"]}}},
    )
    assert r.status_code == 201, r.text
    assert r.json()["argv"][-1].endswith("/vix.csv")
    bad = console.post(
        "/api/jobs", json={"command": "fse import-vix", "values": {"csv": {"upload": "../../etc"}}}
    )
    assert bad.status_code == 422
