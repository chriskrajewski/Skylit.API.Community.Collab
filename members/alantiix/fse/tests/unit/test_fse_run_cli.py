"""``fse run`` command line: help, ``--until``, credentials, lock, the shipped config (design 11.2).

No test reaches the network: every case stops before the first request
(credentials are checked before any). ``project_dir=None`` keeps the Project
``.env`` unread.
"""

from __future__ import annotations

import argparse
import io
import json
from datetime import date, time, timedelta
from pathlib import Path
from typing import Any, Final

import pytest
import respx
import yaml

from fse.calendars import load_calendars
from fse.cli import run
from fse.commands import CommandContext
from fse.commands import run as run_command
from fse.commands.run import RunUsageError, parse_until
from fse.live.host.config import load_runner_config, parse_runner_config
from fse.live.host.host import InlineBlockingRunner
from fse.live.host.live_orders import JournalError
from fse.live.host.lock import RunnerLock
from fse.live.plugins.loader import load_plugin_classes, needs_problems
from fse.logio import LogWriter, Redactor
from fse.projectx.session import DEFAULT_BASE_URL
from fse.secrets.env import EnvView
from fse.timekit import ny_instant, parse_rfc3339
from tests.fakes.bar_api import RespxBarsGateway
from tests.fakes.clock import FakeClock
from tests.fakes.dataplane import CALENDAR_DIR, PROJECT_DIR, read_jsonl
from tests.fakes.market_hub import FakeMarketHub

RUNNER_YAML: Final = PROJECT_DIR / "configs" / "runner.yaml"
PAPER_YAML: Final = PROJECT_DIR / "configs" / "runner.paper.yaml"
SKYLIT_SECRET: Final = "fake-skylit-secret-0042"


def fse_run(
    tmp_path: Path, *args: str, environ: dict[str, str] | None = None
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    argv = [
        "run",
        *args,
        "--state-dir", str(tmp_path / "state"),
        "--recordings-root", str(tmp_path / "rec"),
        "--out", str(tmp_path / "run"),
        "--bar-store", str(tmp_path / "bars"),
        "--cache-dir", str(tmp_path / "cache"),
        "--calendar-dir", str(CALENDAR_DIR),
    ]  # fmt: skip
    code = run(argv, project_dir=None, environ=environ or {}, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_help_lists_every_flag() -> None:
    out, err = io.StringIO(), io.StringIO()
    assert run(["run", "--help"], project_dir=None, environ={}, stdout=out, stderr=err) == 0
    text = out.getvalue()
    for flag in (
        "--config", "--until", "--state-dir", "--recordings-root", "--out", "--bar-store",
        "--cache-dir", "--calendar-dir",
    ):  # fmt: skip
        assert flag in text
    assert "paper by default" in text
    for name in ("--plugin-orders", "--live-orders", "FSE_RUN_PLUGIN_ORDERS"):
        assert name in text
    assert "130" in text
    assert "4    the order journal" in text


def test_the_command_is_discovered() -> None:
    out, err = io.StringIO(), io.StringIO()
    run(["--help"], project_dir=None, environ={}, stdout=out, stderr=err)
    assert " run " in out.getvalue()


@pytest.mark.parametrize("until", ["2026-10-12T18:10:00", "tomorrow", "2026-13-01T00:00:00Z"])
def test_a_bad_until_exits_2(tmp_path: Path, until: str) -> None:
    code, _, err = fse_run(tmp_path, "--config", str(RUNNER_YAML), "--until", until)
    assert code == 2
    assert "--until" in err


def test_parse_until() -> None:
    assert parse_until(None) is None
    assert parse_until("2026-10-12T18:10:00-04:00") == parse_rfc3339("2026-10-12T22:10:00Z")
    assert parse_until("2026-10-12T22:10:00.5Z") == parse_rfc3339("2026-10-12T22:10:00.5Z")
    with pytest.raises(RunUsageError, match="offset"):
        parse_until("2026-10-12T18:10:00")


def test_blank_credentials_exit_3_without_a_secret_on_stderr(tmp_path: Path) -> None:
    environ = {"SKYLIT_API_KEY": SKYLIT_SECRET, "PROJECTX_USERNAME": "fake-user-0042"}
    code, out, err = fse_run(tmp_path, "--config", str(RUNNER_YAML), environ=environ)
    assert code == 3
    assert "PROJECTX_API_KEY is blank" in err
    assert SKYLIT_SECRET not in err + out
    assert "fake-user-0042" not in err + out
    assert not (tmp_path / "rec").exists()  # nothing was recorded


def test_skylit_key_is_required_only_with_skylit_enabled(tmp_path: Path) -> None:
    code, _, err = fse_run(tmp_path, "--config", str(RUNNER_YAML))
    assert code == 3
    assert "SKYLIT_API_KEY is blank" in err
    no_skylit = tmp_path / "no_skylit.yaml"
    no_skylit.write_text(
        "version: 1\n"
        "data_plane: {skylit: {enabled: false}}\n"
        "plugins: [{id: template, module: 'fse.live.plugins.template:TemplatePlugin'}]\n",
        encoding="utf-8",
    )
    code, _, err = fse_run(tmp_path, "--config", str(no_skylit))
    assert code == 3
    assert "PROJECTX_USERNAME is blank" in err
    assert "SKYLIT_API_KEY" not in err


def test_an_invalid_config_exits_2(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 1\nplugins: []\nsurprise: 1\n", encoding="utf-8")
    code, _, err = fse_run(tmp_path, "--config", str(bad))
    assert code == 2
    assert "surprise" in err
    assert "plugins" in err


def test_a_held_lock_exits_2(tmp_path: Path) -> None:
    with RunnerLock(tmp_path / "state"):
        code, _, err = fse_run(tmp_path, "--config", str(RUNNER_YAML))
    assert code == 2
    assert "another runner holds" in err


def test_the_shipped_runner_yaml_parses() -> None:
    cfg = load_runner_config(RUNNER_YAML)
    assert cfg.version == 1
    assert [p.id for p in cfg.enabled_plugins] == ["template"]
    template = cfg.plugins[0]
    assert template.module == "fse.live.plugins.template:TemplatePlugin"
    assert template.fills is not None
    assert template.risk is not None
    assert str(template.fills.commission_rt["MNQ"]) == "1.24"
    assert template.fills.stop_fill == "at_stop"
    assert template.risk.max_contracts == 1
    playbook = cfg.plugins[1]
    assert playbook.id == "playbook"
    assert not playbook.enabled
    assert playbook.config == {"strategy_config": "configs/playbook_baseline.yaml"}
    assert [p.id for p in cfg.plugins] == ["template", "playbook"]
    dp = cfg.data_plane
    assert dp.instruments == ("MNQ", "MES")
    assert dp.projectx.transport == "auto"
    assert dp.projectx.subscription == "sim"
    assert dp.skylit.enabled
    assert dp.skylit.mode == "stream"


@pytest.mark.parametrize("path", [RUNNER_YAML, PAPER_YAML])
def test_the_shipped_runner_yamls_run_every_plugin_on_paper(path: Path) -> None:
    """T3: no shipped config opts a plugin into broker orders."""
    cfg = load_runner_config(path)
    assert {p.execution.order_mode for p in cfg.plugins} == {"paper"}


def test_the_shipped_paper_runner_yaml_parses_and_loads() -> None:
    """``configs/runner.paper.yaml``: the template enabled, the playbook disabled."""
    paper = load_runner_config(PAPER_YAML)
    shipped = load_runner_config(RUNNER_YAML)
    assert [p.id for p in paper.enabled_plugins] == ["template"]
    assert paper.data_plane.instruments == shipped.data_plane.instruments
    assert paper.data_plane.projectx == shipped.data_plane.projectx
    assert paper.data_plane.skylit == shipped.data_plane.skylit
    assert paper.paths == shipped.paths
    by_id = {p.id: p for p in paper.plugins}
    assert len(by_id) == len(paper.plugins)  # distinct ids, so distinct state dirs
    assert set(by_id) == {"template", "playbook"}
    assert not by_id["playbook"].enabled
    # each entry keeps the module and the fills/risk block of the main config
    for entry in shipped.plugins:
        mine = by_id[entry.id]
        assert (mine.module, mine.fills, mine.risk) == (entry.module, entry.fills, entry.risk)
        assert mine.hold_through_break == entry.hold_through_break
    classes = load_plugin_classes(paper, project_dir=PROJECT_DIR)
    assert sorted(classes) == ["template"]
    template = by_id["template"]
    plugin = classes["template"]("template", template.config, None)  # type: ignore[arg-type]
    assert needs_problems(template, plugin.needs(), paper.data_plane) == []


# ---------------------------------------------------------------- T19 the order gates end to end


def _practice_doc(tmp_path: Path) -> dict[str, Any]:
    shipped = yaml.safe_load(RUNNER_YAML.read_text(encoding="utf-8"))
    entry = next(p for p in shipped["plugins"] if p["id"] == "template")
    entry["module"] = "tests.fakes.practice_plugin:PracticeTemplatePlugin"
    entry["risk"] = {**entry["risk"], "max_daily_loss": "500.00", "max_trades_per_day": 4}
    entry["hold_through_break"] = False
    entry["execution"] = {"order_mode": "practice"}
    return {
        "version": 1,
        "data_plane": {
            **shipped["data_plane"],
            "projectx": {"poll_interval_s": 30, "hot_poll_interval_s": 1, "backfill_sessions": 0},
            "skylit": {"enabled": False},
        },
        "plugins": [entry],
    }


SUN = date(2026, 10, 11)
GATE_ENV: Final = {
    "PROJECTX_USERNAME": "fake-projectx-user-0000",
    "PROJECTX_API_KEY": "fake-projectx-key-0000",
    "PRACTICE_ACCOUNT_ID": "424242",
}


@pytest.mark.parametrize(
    ("flag", "extra_env", "expected"),
    [
        (
            False,
            {"FSE_RUN_PLUGIN_ORDERS": "enabled"},
            "the start command has no --plugin-orders flag",
        ),
        (True, {}, "FSE_RUN_PLUGIN_ORDERS is not set to enabled"),
    ],
)
@pytest.mark.asyncio
async def test_a_practice_plugin_without_both_gates_runs_paper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flag: bool,
    extra_env: dict[str, str],
    expected: str,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    def refuse(self: object, *args: object, **kwargs: object) -> None:
        raise AssertionError("no BrokerAdapter without both local opt-ins")

    monkeypatch.setattr("fse.projectx.broker.BrokerAdapter.__init__", refuse)
    clock = FakeClock(ny_instant(SUN, time(18, 0)))
    doc = _practice_doc(tmp_path)
    config_path = tmp_path / "runner.yaml"
    config_path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    cfg = parse_runner_config(doc, config_path)
    classes = load_plugin_classes(cfg, project_dir=PROJECT_DIR)
    env = {**GATE_ENV, **extra_env}
    writer = LogWriter(Redactor(list(env.values())), stdout=io.StringIO(), stderr=io.StringIO())
    ctx = CommandContext(env=EnvView(env, {}), writer=writer, project_dir=PROJECT_DIR)
    args = argparse.Namespace(
        state_dir=tmp_path / "live-state",
        recordings_root=tmp_path / "rec",
        out=tmp_path / "run",
        bar_store=tmp_path / "live-bars",
        cache_dir=tmp_path / "cache",
        calendar_dir=CALENDAR_DIR,
    )
    dirs = run_command._dirs(args, cfg, ctx)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        RespxBarsGateway(clock, base=DEFAULT_BASE_URL).install(router)
        hub = FakeMarketHub(clock=clock)
        code = await clock.run(
            run_command._run(
                ctx,
                cfg,
                dirs,
                load_calendars(CALENDAR_DIR, SUN, SUN + timedelta(days=1)),
                classes,
                ny_instant(SUN, time(18, 5)),
                plugin_orders=flag,
                clock=clock,
                connect=hub.connect,
                blocking=InlineBlockingRunner(),
            ),
            max_steps=1_000_000,
        )
        paths = {c.request.url.path for c in router.calls}
    assert code == 0
    assert not any("/Account/" in p or "/Order/" in p for p in paths)
    cards = read_jsonl(tmp_path / "run" / "plugins" / "template" / "decision_cards.jsonl")
    first = cards[0]["card"]
    assert first["title"] == "Order_Mode practice was not used, running Paper"
    assert expected in first["reason"]
    assert "424242" not in json.dumps(cards)
    assert not (tmp_path / "live-state" / "plugins" / "template" / "order_journal.jsonl").exists()


def test_an_unreadable_journal_exits_4(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(*_a: object, **_k: object) -> int:
        raise JournalError("order_journal.jsonl: cannot read (IsADirectoryError)")

    monkeypatch.setattr(run_command, "_run", broken)
    monkeypatch.setattr(run_command, "load_plugin_classes", lambda *_a, **_k: {})
    env = {
        "SKYLIT_API_KEY": SKYLIT_SECRET,
        "PROJECTX_USERNAME": "fake-projectx-user-0000",
        "PROJECTX_API_KEY": "fake-projectx-key-0000",
    }
    code, _, err = fse_run(tmp_path, "--config", str(RUNNER_YAML), "--plugin-orders", environ=env)
    assert code == 4, err
    assert "order_journal.jsonl: cannot read" in err
