"""``fse backtest-plugin`` and ``fse.live.host.replay``: any plugin, replayed through the host.

Synthetic Data_Cache and data-plane recordings in ``tmp_path``. The Template plugin
stands in for "any plugin"; ``SkyPlugin`` (below) needs Skylit matrix-layout boards.
"""

from __future__ import annotations

import io
import json
import re
from collections.abc import Sequence
from dataclasses import replace as dc_replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pytest
import yaml

from fse.cli import run as cli_run
from fse.config.schema.data import HeatmapViewConfig
from fse.data.cache import DataCache
from fse.data.cache_io import CacheReadError
from fse.engine.types import Bar, Snapshot
from fse.live.host.dataplane import dataplane_recording_path
from fse.live.host.dayclock import globex_day
from fse.live.host.replay import Coverage, DayCoverage, ReplayNeeds
from fse.live.plugins import (
    BarNeed,
    DataNeeds,
    Instant,
    Intent,
    PluginContext,
    SkylitNeed,
    StrategyPlugin,
)
from fse.live.recorder import Recorder
from fse.logio import LogWriter
from fse.logio.redact import Redactor
from fse.pit.market_view import heatmap_view
from fse.timekit import NS_PER_MINUTE
from tests.fakes.dataplane import PROJECT_DIR, make_bar, ny

SRC = Path(__file__).resolve().parents[2] / "src" / "fse"
WED, THU = date(2026, 10, 14), date(2026, 10, 15)
VIEW = HeatmapViewConfig(max_strikes=3, max_expirations=1, include_empty=True)


def session_bars(d: date, base: int = 80_000) -> list[Bar]:
    """1m MNQ bars of Globex day ``d`` (18:00 the day before to 17:00), a gentle walk."""
    out: list[Bar] = []
    t = globex_day(d).open_ns
    end = globex_day(d).rotate_ns - 60 * NS_PER_MINUTE
    i = 0
    while t < end:
        p = base + (i % 40) - 20
        out.append(make_bar("MNQ", t, p, p + 2, p - 2, p + 1))
        t += NS_PER_MINUTE
        i += 1
    return out


def write_cache(root: Path, days: Sequence[date]) -> None:
    with DataCache(root) as cache:
        for d in days:
            bars = session_bars(d)
            cache.bars.write_session("MNQ", 60, d, bars, contract="MNQZ6", source="projectx")


def history_days(first: date, n: int) -> list[date]:
    out: list[date] = []
    d = first - timedelta(days=1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out


def runner_yaml(
    path: Path, plugins: list[dict[str, Any]], skylit: dict[str, Any] | None = None
) -> Path:
    doc: dict[str, Any] = {
        "version": 1,
        "data_plane": {
            "instruments": ["MNQ"],
            "skylit": skylit or {"enabled": False},
        },
        "plugins": plugins,
    }
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return path


TEMPLATE = {
    "id": "tmpl",
    "module": "fse.live.plugins.template:TemplatePlugin",
    "config": {"instrument": "MNQ", "entry_time": "10:00", "flatten_time": "15:55"},
    "fills": {"commission_rt": {"MNQ": "1.24"}, "slippage_ticks": 1},
    "risk": {"max_contracts": 1},
    "hold_through_break": False,
}


def fse(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    code = cli_run(list(argv), project_dir=PROJECT_DIR, environ={}, stdout=out, stderr=err)
    return code, out.getvalue() + err.getvalue()


def test_template_is_backtested_through_the_host(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    write_cache(cache, [*history_days(WED, 6), WED, THU])
    cfg = runner_yaml(tmp_path / "runner.yaml", [TEMPLATE])
    out = tmp_path / "run1"
    code, text = fse(
        "backtest-plugin",
        "--config",
        str(cfg),
        "--first",
        str(WED),
        "--last",
        str(THU),
        "--out",
        str(out),
        "--cache-dir",
        str(cache),
        "--recordings-root",
        str(tmp_path / "none"),
        "--history",
        "6",
    )
    assert code == 0, text
    assert "coverage: complete" in text
    assert "replay 1/" in text
    assert "bars=" in text
    cov = json.loads((out / "coverage.json").read_text())
    assert cov["complete"] is True
    assert [d["sources"] for d in cov["days"]] == [{"bars MNQ": "cache"}] * 2
    trades = json.loads((out / "trades.json").read_text())
    assert len(trades) == 2  # one entry per day at 10:00, flattened at 15:55
    manifest = json.loads((out / "run_manifest.json").read_text())
    assert manifest["kind"] == "plugin_backtest"
    assert manifest["status"] == "completed"
    summary = json.loads((out / "summary.json").read_text())
    assert summary["plugins"]["tmpl"]["trades"] == 2
    # the fse backtest trades.json shape (the console's run metrics read it)
    assert [t["setup_key"]["session"] for t in trades] == [str(WED), str(THU)]
    assert {"entry_fill", "exits", "net", "r", "r_multiple", "shadow"} <= set(trades[0])
    # the run layout: status.json for the plugin, its log, outputs inside the run folder
    assert (out / "live-state" / "plugins" / "tmpl" / "status.json").is_file()
    assert (out / "run" / "host_log.jsonl").is_file()


def test_no_lookahead_the_history_cache_ends_before_first(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    write_cache(cache, [*history_days(WED, 4), WED, THU])
    cfg = runner_yaml(tmp_path / "runner.yaml", [TEMPLATE])
    out = tmp_path / "run"
    code, text = fse(
        "backtest-plugin",
        "--config",
        str(cfg),
        "--first",
        str(WED),
        "--last",
        str(WED),
        "--out",
        str(out),
        "--cache-dir",
        str(cache),
        "--history",
        "4",
        "--recordings-root",
        str(tmp_path / "none"),
    )
    assert code == 0, text
    with DataCache(out / "cache") as hist:
        assert hist.bars.read_session("MNQ", 60, history_days(WED, 1)[0])
        for d in (WED, THU):
            with pytest.raises(CacheReadError):  # absent: never copied
                hist.bars.read_session("MNQ", 60, d)


def test_missing_input_stops_before_running(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    write_cache(cache, history_days(WED, 3))  # no WED bars
    cfg = runner_yaml(tmp_path / "runner.yaml", [TEMPLATE])
    out = tmp_path / "run"
    code, text = fse(
        "backtest-plugin",
        "--config",
        str(cfg),
        "--first",
        str(WED),
        "--last",
        str(WED),
        "--out",
        str(out),
        "--cache-dir",
        str(cache),
        "--history",
        "3",
        "--recordings-root",
        str(tmp_path / "none"),
    )
    assert code == 3, text
    assert "bars MNQ=missing" in text
    assert not (out / "trades.json").exists()
    code, _ = fse(
        "backtest-plugin",
        "--config",
        str(cfg),
        "--first",
        str(WED),
        "--last",
        str(WED),
        "--out",
        str(tmp_path / "cov"),
        "--cache-dir",
        str(cache),
        "--coverage-only",
        "--recordings-root",
        str(tmp_path / "none"),
    )
    assert code == 0
    assert json.loads((tmp_path / "cov" / "coverage.json").read_text())["complete"] is False


def test_coverage_lines_collapse_a_run_of_missing_days() -> None:
    needs = ReplayNeeds(
        instruments=("MNQ",),
        skylit_symbols=(),
        layout="net",
        view_id=None,
        window=None,
        dark_pool_tickers=(),
        quotes=False,
    )
    days = tuple(
        DayCoverage(date(2024, 1, d), True, {"bars MNQ": "missing"}) for d in (2, 3, 4)
    )
    text = "\n".join(Coverage(needs, days).lines())
    assert "2024-01-02..2024-01-04 (3 sessions): bars MNQ=missing" in text
    assert text.count("bars MNQ=missing") == 1


def test_an_unknown_plugin_id_is_refused(tmp_path: Path) -> None:
    cfg = runner_yaml(tmp_path / "runner.yaml", [TEMPLATE])
    code, text = fse(
        "backtest-plugin",
        "--config",
        str(cfg),
        "--plugin",
        "nope",
        "--first",
        str(WED),
        "--last",
        str(WED),
        "--out",
        str(tmp_path / "x"),
    )
    assert code == 2
    assert "nope" in text


# ---------------------------------------------------------------- recorded Skylit boards


class SkyPlugin(StrategyPlugin):
    """Needs MNQ bars and SPXW matrix boards; logs what it receives (no trades)."""

    label = "Sky test"
    description = "Counts the Skylit boards it receives, for the replay tests."
    VIEW_ID: ClassVar[str] = heatmap_view(VIEW).view_id()

    def needs(self) -> DataNeeds:
        return DataNeeds(
            bars=(BarNeed("MNQ"),),
            hours="eth",
            skylit=SkylitNeed(
                symbols=("SPXW",),
                view_id=self.VIEW_ID,
                mode="stream",
                refresh_interval_s=15,
                window=(
                    __import__("datetime").time(9, 30),
                    __import__("datetime").time(16, 0),
                ),
                layout="matrix",
            ),
            brokerage="host",
        )

    def on_snapshots(
        self, snaps: Sequence[Snapshot], received_ns: Instant, ctx: PluginContext
    ) -> Sequence[Intent]:
        for s in snaps:
            ctx.log(
                {
                    "kind": "board",
                    "symbol": s.symbol,
                    "grid": "grid" in s.extra_json,
                    "received_ns": received_ns,
                }
            )
        return ()


def board(t: int) -> Snapshot:
    return Snapshot(
        symbol="SPXW",
        metric="gamma",
        view_id=SkyPlugin.VIEW_ID,
        as_of_ns=t,
        as_of_raw=str(t),
        spot=5800.0,
        previous_close=None,
        strikes=(5795.0, 5800.0, 5805.0),
        values=(1.0, 2.0, 3.0),
        node_types=None,
        expirations=("2026-10-14",),
        resolution="1s",
        source_endpoint="stream",
        extra_json=json.dumps({"grid": [[1.0, 2.0, 3.0]]}),
    )


def test_recorded_matrix_boards_replay_with_their_receipt_times(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    write_cache(cache, [*history_days(WED, 2), WED])
    rec_root = tmp_path / "data"
    path = dataplane_recording_path(rec_root, WED)
    path.parent.mkdir(parents=True)
    times = [ny(WED, 10, 0, 3), ny(WED, 10, 0, 18), ny(WED, 8, 0, 0)]  # 08:00 is outside
    with Recorder(LogWriter(Redactor(), stdout=io.StringIO(), stderr=io.StringIO()), path) as r:
        for t in times:
            r.snapshot(board(t - 2_000_000_000), t)
    plugin = {
        "id": "sky",
        "module": "tests.unit.test_backtest_plugin:SkyPlugin",
        "fills": {"commission_rt": {"MNQ": "1.24"}},
        "risk": {"max_contracts": 1},
    }
    sky = {
        "enabled": True,
        "mode": "stream",
        "refresh_interval_s": 15,
        "symbols": ["SPXW"],
        "layout": "matrix",
        "heatmap_view": {"max_strikes": 3, "max_expirations": 1, "include_empty": True},
        "window": {"start": "09:00", "end": "16:15"},
    }
    cfg = runner_yaml(tmp_path / "runner.yaml", [plugin], skylit=sky)
    out = tmp_path / "run"
    code, text = fse(
        "backtest-plugin",
        "--config",
        str(cfg),
        "--first",
        str(WED),
        "--last",
        str(WED),
        "--out",
        str(out),
        "--cache-dir",
        str(cache),
        "--recordings-root",
        str(rec_root),
        "--history",
        "2",
    )
    assert code == 0, text
    assert "skylit SPXW (matrix)=recording" in text
    log = [
        json.loads(x)
        for x in (out / "run" / "plugins" / "sky" / "plugin_log.jsonl").read_text().splitlines()
    ]
    boards = [e for e in log if e["kind"] == "board"]
    assert [e["received_ns"] for e in boards] == times[:2]  # in the window, receipt times
    assert all(e["grid"] for e in boards)  # the matrix grid arrives intact


def test_the_core_names_no_plugin() -> None:
    names = re.compile(
        r"research\.\w+\.plugin|\b(Playbook|Template|Rules)Plugin\b|\b(SPXW|NDXP)\b"
    )
    for f in (SRC / "live" / "host" / "replay.py", SRC / "commands" / "backtest_plugin.py"):
        found = names.search(f.read_text(encoding="utf-8"))
        assert found is None, f"{f.name} names a plugin: {found}"


def test_pulled_matrix_boards_replay_from_the_cache(tmp_path: Path) -> None:
    """``fse pull --layout matrix`` stores its own view; the replay serves it as the live view."""
    from dataclasses import replace as dc_replace

    from fse.data.cache import CacheWindowKey

    cache = tmp_path / "cache"
    write_cache(cache, [*history_days(WED, 2), WED])
    matrix_view = dc_replace(heatmap_view(VIEW), layout="matrix")
    start = ny(WED, 10, 0)
    key = CacheWindowKey("SPXW", "gamma", matrix_view.view_id(), WED, start)
    snaps = [dc_replace(board(start + 60_000_000_000), view_id=matrix_view.view_id())]
    with DataCache(cache) as c:
        c.write_window(key, snaps, "historical", view=matrix_view)
    plugin = {
        "id": "sky",
        "module": "tests.unit.test_backtest_plugin:SkyPlugin",
        "fills": {"commission_rt": {"MNQ": "1.24"}},
        "risk": {"max_contracts": 1},
    }
    sky = {
        "enabled": True, "mode": "stream", "refresh_interval_s": 15, "symbols": ["SPXW"],
        "layout": "matrix",
        "heatmap_view": {"max_strikes": 3, "max_expirations": 1, "include_empty": True},
        "window": {"start": "09:00", "end": "16:15"},
    }
    cfg = runner_yaml(tmp_path / "runner.yaml", [plugin], skylit=sky)
    out = tmp_path / "run"
    code, text = fse(
        "backtest-plugin", "--config", str(cfg), "--first", str(WED), "--last", str(WED),
        "--out", str(out), "--cache-dir", str(cache), "--recordings-root", str(tmp_path / "none"),
        "--history", "2",
    )
    assert code == 0, text
    assert "skylit SPXW (matrix)=cache" in text
    log = [json.loads(x) for x in (out / "run" / "plugins" / "sky" / "plugin_log.jsonl")
           .read_text().splitlines()]
    boards = [e for e in log if e["kind"] == "board"]
    assert len(boards) == 1
    assert boards[0]["grid"] is True


# ---------------------------------------------------------------- streamed replay, S3 days

SKY_PLUGIN = {
    "id": "sky",
    "module": "tests.unit.test_backtest_plugin:SkyPlugin",
    "fills": {"commission_rt": {"MNQ": "1.24"}},
    "risk": {"max_contracts": 1},
}
SKY_PLANE = {
    "enabled": True,
    "mode": "stream",
    "refresh_interval_s": 15,
    "symbols": ["SPXW"],
    "layout": "matrix",
    "heatmap_view": {"max_strikes": 3, "max_expirations": 1, "include_empty": True},
    "window": {"start": "09:00", "end": "16:15"},
}


def record_boards(path: Path, times: Sequence[int], grid: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Recorder(LogWriter(Redactor(), stdout=io.StringIO(), stderr=io.StringIO()), path) as r:
        for t in times:
            snap = board(t - 2_000_000_000)
            if grid:
                snap = dc_replace(snap, extra_json=json.dumps({"grid": grid}))
            r.snapshot(snap, t)


def boards_seen(out: Path) -> list[dict[str, Any]]:
    log = out / "run" / "plugins" / "sky" / "plugin_log.jsonl"
    return [e for e in map(json.loads, log.read_text().splitlines()) if e["kind"] == "board"]


def test_receipt_order_is_restored_from_a_slightly_out_of_order_recording(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "cache"
    write_cache(cache, [*history_days(WED, 2), WED])
    rec_root = tmp_path / "data"
    # file order is only roughly receipt order (a write raced another): 10:00:33 first
    times = [ny(WED, 10, 0, 33), ny(WED, 10, 0, 3), ny(WED, 10, 0, 18), ny(WED, 10, 0, 48)]
    record_boards(dataplane_recording_path(rec_root, WED), times)
    cfg = runner_yaml(tmp_path / "runner.yaml", [SKY_PLUGIN], skylit=SKY_PLANE)
    out = tmp_path / "run"
    code, text = fse(
        "backtest-plugin", "--config", str(cfg), "--first", str(WED), "--last", str(WED),
        "--out", str(out), "--cache-dir", str(cache), "--recordings-root", str(rec_root),
        "--history", "2",
    )  # fmt: skip
    assert code == 0, text
    assert [e["received_ns"] for e in boards_seen(out)] == sorted(times)


def test_split_part_files_replay_before_the_main_recording(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    write_cache(cache, [*history_days(WED, 2), WED])
    rec_root = tmp_path / "data"
    main = dataplane_recording_path(rec_root, WED)
    record_boards(main.with_name(f"{WED}.part1-1300.jsonl.gz"), [ny(WED, 10, 0, 3)])
    record_boards(main, [ny(WED, 11, 0, 3)])
    cfg = runner_yaml(tmp_path / "runner.yaml", [SKY_PLUGIN], skylit=SKY_PLANE)
    out = tmp_path / "run"
    code, text = fse(
        "backtest-plugin", "--config", str(cfg), "--first", str(WED), "--last", str(WED),
        "--out", str(out), "--cache-dir", str(cache), "--recordings-root", str(rec_root),
        "--history", "2",
    )  # fmt: skip
    assert code == 0, text
    assert [e["received_ns"] for e in boards_seen(out)] == [ny(WED, 10, 0, 3), ny(WED, 11, 0, 3)]


class FakeS3:
    """``list_objects_v2`` and ``download_file`` over ``root/<bucket>/<key>`` (no AWS)."""

    def __init__(self, root: Path, on_download: Any = None) -> None:
        self.root = root
        self.listed: list[str] = []
        self.downloaded: list[Path] = []
        self.on_download = on_download

    def list_objects_v2(self, *, Bucket: str, Prefix: str, **_: Any) -> dict[str, Any]:
        self.listed.append(Prefix)
        base = self.root / Bucket
        keys = sorted(p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file())
        return {"Contents": [{"Key": k} for k in keys if k.startswith(Prefix)]}

    def download_file(self, Bucket: str, Key: str, Filename: str) -> None:
        Path(Filename).write_bytes((self.root / Bucket / Key).read_bytes())
        self.downloaded.append(Path(Filename))
        if self.on_download is not None:
            self.on_download(Path(Filename))


def test_a_day_offloaded_to_s3_is_covered_listed_only_then_replayed_and_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fse.live.host import replay as replay_mod
    from fse.live.host.recordings_s3 import S3Recordings

    cache = tmp_path / "cache"
    write_cache(cache, [*history_days(WED, 2), WED])
    bucket = tmp_path / "s3"
    times = [ny(WED, 10, 0, 3), ny(WED, 10, 0, 18)]
    # the host's offload: s3://data/fse/recordings/dataplane/<day>.jsonl.gz
    record_boards(bucket / "data" / "fse" / "recordings" / "dataplane" / f"{WED}.jsonl.gz", times)
    fake = FakeS3(bucket)
    monkeypatch.setattr(replay_mod, "S3Recordings", lambda uri: S3Recordings(uri, client=fake))
    cfg = runner_yaml(tmp_path / "runner.yaml", [SKY_PLUGIN], skylit=SKY_PLANE)
    local = tmp_path / "data"  # nothing recorded locally
    base = (
        "backtest-plugin", "--config", str(cfg), "--first", str(WED), "--last", str(WED),
        "--cache-dir", str(cache), "--recordings-root", str(local), "--history", "2",
        "--recordings-s3", "s3://data/fse",
    )  # fmt: skip
    code, text = fse(*base, "--out", str(tmp_path / "cov"), "--coverage-only")
    assert code == 0, text
    cov = json.loads((tmp_path / "cov" / "coverage.json").read_text())
    assert cov["days"][0]["sources"] == {
        "bars MNQ": "recording (s3)",
        "skylit SPXW (matrix)": "recording (s3)",
    }
    assert fake.listed == ["fse/recordings/dataplane/"]
    assert fake.downloaded == []  # coverage lists only
    out = tmp_path / "run"
    code, text = fse(*base, "--out", str(out))
    assert code == 0, text
    folder = out / "recordings-s3" / "recordings" / "dataplane"
    assert fake.downloaded == [folder / f"{WED}.jsonl.gz"]
    assert not list(folder.iterdir())  # deleted after the day's replay
    assert [e["received_ns"] for e in boards_seen(out)] == times
    # the file had no MNQ bars: the replay fell back to the cache for them
    assert "replay_input_failed" not in (out / "run" / "host_log.jsonl").read_text()


def test_a_failed_s3_download_is_a_run_fault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fse.live.host import replay as replay_mod
    from fse.live.host.recordings_s3 import S3Recordings

    cache = tmp_path / "cache"
    write_cache(cache, [*history_days(WED, 2), WED])
    bucket = tmp_path / "s3"
    record_boards(bucket / "b" / "recordings" / "dataplane" / f"{WED}.jsonl.gz", [ny(WED, 10)])

    def boom(path: Path) -> None:
        raise OSError("connection reset")

    fake = FakeS3(bucket, on_download=boom)
    monkeypatch.setattr(replay_mod, "S3Recordings", lambda uri: S3Recordings(uri, client=fake))
    cfg = runner_yaml(tmp_path / "runner.yaml", [SKY_PLUGIN], skylit=SKY_PLANE)
    out = tmp_path / "run"
    code, text = fse(
        "backtest-plugin", "--config", str(cfg), "--first", str(WED), "--last", str(WED),
        "--out", str(out), "--cache-dir", str(cache), "--recordings-root", str(tmp_path / "x"),
        "--history", "2", "--recordings-s3", "s3://b",
    )  # fmt: skip
    assert code == 1, text
    log = (out / "run" / "host_log.jsonl").read_text()
    assert "replay_input_failed" in log
    assert "connection reset" in log
    assert not list((out / "recordings-s3" / "recordings" / "dataplane").iterdir())


def test_a_covered_session_with_no_bars_is_a_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Coverage called the day present, then the download held no bars.

    A recording name makes scan treat every needed instrument as covered
    without reading the file. An empty-of-bars file plus an empty cache used
    to finish the day as ``bars=0`` and keep going.
    """
    from fse.live.host import replay as replay_mod
    from fse.live.host.recordings_s3 import S3Recordings

    cache = tmp_path / "cache"
    with DataCache(cache):
        pass
    bucket = tmp_path / "s3"
    record_boards(bucket / "b" / "recordings" / "dataplane" / f"{WED}.jsonl.gz", [ny(WED, 10)])
    fake = FakeS3(bucket)
    monkeypatch.setattr(replay_mod, "S3Recordings", lambda uri: S3Recordings(uri, client=fake))
    cfg = runner_yaml(tmp_path / "runner.yaml", [TEMPLATE])
    out = tmp_path / "run"
    code, text = fse(
        "backtest-plugin", "--config", str(cfg), "--first", str(WED), "--last", str(WED),
        "--out", str(out), "--cache-dir", str(cache), "--recordings-root", str(tmp_path / "x"),
        "--history", "0", "--recordings-s3", "s3://b",
    )  # fmt: skip
    assert code == 1, text
    assert f"error: {WED} replayed 0 bars but coverage said bars MNQ=recording (s3)" in text
    assert "replay_input_failed" in (out / "run" / "host_log.jsonl").read_text()


def test_a_missing_session_is_not_the_zero_bar_fault(tmp_path: Path) -> None:
    """``allow_gaps`` with no source is a hole, not a covered session that came back empty."""
    cache = tmp_path / "cache"
    with DataCache(cache):
        pass
    cfg = runner_yaml(tmp_path / "runner.yaml", [TEMPLATE])
    out = tmp_path / "run"
    code, text = fse(
        "backtest-plugin", "--config", str(cfg), "--first", str(WED), "--last", str(WED),
        "--out", str(out), "--cache-dir", str(cache), "--recordings-root", str(tmp_path / "x"),
        "--history", "0", "--allow-gaps",
    )  # fmt: skip
    assert code == 0, text
    assert "error:" not in text
    assert "bars=0" in text
    cov = json.loads((out / "coverage.json").read_text())
    assert cov["days"][0]["sources"]["bars MNQ"] == "missing"


def test_replay_memory_stays_far_below_the_recording_size(tmp_path: Path) -> None:
    import os
    import tracemalloc

    from fse.live.host.config import load_runner_config
    from fse.live.host.replay import run_replay
    from fse.live.plugins.loader import load_plugin_classes

    cache = tmp_path / "cache"
    write_cache(cache, [*history_days(WED, 2), WED])
    rec_root = tmp_path / "data"
    times = [ny(WED, 10, 0, 3) + i * 15_000_000_000 for i in range(240)]  # 1 h of boards
    path = dataplane_recording_path(rec_root, WED)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = 0
    with Recorder(LogWriter(Redactor(), stdout=io.StringIO(), stderr=io.StringIO()), path) as r:
        for t in times:
            grid = os.urandom(100_000).hex()  # ~200 KB per board line
            raw += len(grid)
            snap = board(t - 2_000_000_000)
            r.snapshot(dc_replace(snap, extra_json=json.dumps({"g": grid})), t)
    cfg_path = runner_yaml(tmp_path / "runner.yaml", [SKY_PLUGIN], skylit=SKY_PLANE)
    cfg = load_runner_config(cfg_path)
    classes = load_plugin_classes(cfg, project_dir=PROJECT_DIR)
    tracemalloc.start()
    try:
        result = run_replay(
            cfg,
            classes,
            first=WED,
            last=WED,
            work=tmp_path / "run",
            source_cache=cache,
            recordings_root=rec_root,
            calendar_dir=PROJECT_DIR / "calendars",
            project_dir=PROJECT_DIR,
            writer=LogWriter(Redactor(), stdout=io.StringIO(), stderr=io.StringIO()),
            history=2,
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result.exit_code == 0
    assert len(boards_seen(tmp_path / "run")) == len(times)
    assert raw > 45 * (1 << 20)
    print(f"replay peak {peak / (1 << 20):.1f} MB for {raw / (1 << 20):.0f} MB")
    assert peak < raw / 4, f"peak {peak / (1 << 20):.1f} MB for {raw / (1 << 20):.0f} MB"


# ---------------------------------------------------------------- paper accounts in a replay

FLAT_BY_NOON = {
    "id": "acct",
    "preset": "none",
    "rules": {"flat_deadline_enabled": True, "flat_deadline": "12:00"},
    # opt-in mirror: a replay has no Nexus transport, so nothing is ever sent
    "nexus": {"enabled": True, "account": "practice", "test_only": True},
}


def _account_yaml(path: Path) -> Path:
    # brackets wider than the walk (+-22 ticks): only the account can close before 15:55
    config = {**TEMPLATE["config"], "stop_ticks": 200, "target_ticks": 200}  # type: ignore[dict-item]
    runner_yaml(path, [{**TEMPLATE, "config": config, "account": "acct"}])
    doc = yaml.safe_load(path.read_text())
    doc["accounts"] = [FLAT_BY_NOON]
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return path


def _assert_flat_by_noon(out: Path, days: Sequence[date]) -> None:
    trades = json.loads((out / "trades.json").read_text())
    assert [t["setup_key"]["session"] for t in trades] == [str(d) for d in days]
    for t, d in zip(trades, days, strict=True):
        # the account's flat-by (12:00) closes what the plugin would hold to 15:55
        assert t["exits"][-1]["bar_open_ns"] < ny(d, 12, 1), t["exits"]
    lines = [
        json.loads(x)
        for x in (out / "run" / "plugins" / "tmpl" / "trades.jsonl").read_text().splitlines()
    ]
    closes = [x for x in lines if x["kind"] == "trade"]
    assert {x["exit_reason"] for x in closes} == {"account_flat_by"}
    assert {x["account"] for x in closes} == {"acct"}
    doc = json.loads((out / "live-state" / "accounts" / "acct" / "account.json").read_text())
    assert doc["account"]["trading_days"] == len(days)
    host = (out / "run" / "host_log.jsonl").read_text()
    assert "nexus_disabled" in host  # no transport in a replay: "no api key"
    assert "nexus_result" not in host


def test_account_rules_are_enforced_in_the_streamed_replay(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    write_cache(cache, [*history_days(WED, 6), WED, THU])
    cfg = _account_yaml(tmp_path / "runner.yaml")
    out = tmp_path / "run"
    code, text = fse(
        "backtest-plugin", "--config", str(cfg), "--first", str(WED), "--last", str(THU),
        "--out", str(out), "--cache-dir", str(cache), "--recordings-root", str(tmp_path / "none"),
        "--history", "6",
    )  # fmt: skip
    assert code == 0, text
    _assert_flat_by_noon(out, [WED, THU])


def test_account_rules_are_enforced_on_bars_replayed_from_s3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fse.live.host import replay as replay_mod
    from fse.live.host.recordings_s3 import S3Recordings

    cache = tmp_path / "cache"
    write_cache(cache, history_days(WED, 2))  # history only: WED's bars are in S3
    bucket = tmp_path / "s3"
    path = bucket / "data" / "fse" / "recordings" / "dataplane" / f"{WED}.jsonl.gz"
    path.parent.mkdir(parents=True)
    with Recorder(LogWriter(Redactor(), stdout=io.StringIO(), stderr=io.StringIO()), path) as r:
        for b in session_bars(WED):
            r.bar(b, b.close_ns + 2_000_000_000)
    fake = FakeS3(bucket)
    monkeypatch.setattr(replay_mod, "S3Recordings", lambda uri: S3Recordings(uri, client=fake))
    cfg = _account_yaml(tmp_path / "runner.yaml")
    out = tmp_path / "run"
    code, text = fse(
        "backtest-plugin", "--config", str(cfg), "--first", str(WED), "--last", str(WED),
        "--out", str(out), "--cache-dir", str(cache), "--recordings-root", str(tmp_path / "data"),
        "--history", "2", "--recordings-s3", "s3://data/fse",
    )  # fmt: skip
    assert code == 0, text
    cov = json.loads((out / "coverage.json").read_text())
    assert cov["days"][0]["sources"] == {"bars MNQ": "recording (s3)"}
    _assert_flat_by_noon(out, [WED])
