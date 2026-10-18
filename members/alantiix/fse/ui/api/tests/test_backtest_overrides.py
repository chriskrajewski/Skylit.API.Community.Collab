"""Backtest override configs never write the live/prod runner YAML."""

from __future__ import annotations

from pathlib import Path

import yaml

from fse_ui.backtest_overrides import (
    OverrideError,
    apply_plugin_patches,
    materialize_override,
    paperize_order_modes,
)
from fse_ui.configs import ConfigStore
from fse_ui.order_modes import apply_order_mode
from tests.conftest import PROJECT, Console
from tests.runner_fixture import runner_text

RUNNER = runner_text(PROJECT)


def test_paperize_forces_practice_to_paper() -> None:
    text = apply_order_mode(RUNNER, "alpha", "practice", ("800", 30))
    data = yaml.safe_load(text)
    out = paperize_order_modes(data)
    alpha = next(p for p in out["plugins"] if p["id"] == "alpha")
    assert alpha["execution"]["order_mode"] == "paper"
    # source document unchanged
    assert next(p for p in data["plugins"] if p["id"] == "alpha")["execution"]["order_mode"] == (
        "practice"
    )


def test_apply_plugin_patches_risk_and_fills() -> None:
    data = yaml.safe_load(RUNNER)
    out = apply_plugin_patches(
        data,
        {
            "alpha": {
                "risk": {"max_daily_loss": "800", "max_contracts": 8, "max_trades_per_day": 30},
                "fills": {"slippage_ticks": 2},
                "hold_through_break": False,
            }
        },
    )
    alpha = next(p for p in out["plugins"] if p["id"] == "alpha")
    assert alpha["risk"]["max_daily_loss"] == "800"
    assert alpha["risk"]["max_contracts"] == 8
    assert alpha["fills"]["slippage_ticks"] == 2
    assert alpha["hold_through_break"] is False


def test_unknown_risk_field_is_refused() -> None:
    data = yaml.safe_load(RUNNER)
    try:
        apply_plugin_patches(data, {"alpha": {"risk": {"secret_key": "x"}}})
    except OverrideError as exc:
        assert "unknown plugins.alpha.risk field" in str(exc)
        assert "secret_key" in str(exc)
    else:
        raise AssertionError("expected OverrideError")


def test_materialize_upload_leaves_source_sha(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "configs")
    store.seed(PROJECT)
    before = store.get("runner", "runner")
    uploads = tmp_path / "uploads"
    result, payload = materialize_override(
        store,
        source="runner",
        uploads_root=uploads,
        author="t@example.com",
        keep=False,
        plugins={
            "alpha": {
                "risk": {"max_daily_loss": "800", "max_contracts": 4},
                "hold_through_break": False,
            }
        },
    )
    assert result.ok and payload is not None
    assert payload["storage"] == "upload"
    assert payload["live_untouched"] is True
    assert payload["source_sha"] == before["sha"]
    assert store.get("runner", "runner")["sha"] == before["sha"]
    assert store.get("runner", "runner")["text"] == before["text"]
    written = Path(payload["path"]).read_text(encoding="utf-8")
    loaded = yaml.safe_load(written)
    alpha = next(p for p in loaded["plugins"] if p["id"] == "alpha")
    assert alpha["risk"]["max_daily_loss"] == "800"
    assert alpha["risk"]["max_contracts"] == 4
    # path is under uploads, not the live current.yaml
    assert "uploads" in payload["path"]
    assert Path(payload["path"]).resolve() != Path(before["path"]).resolve()


def test_materialize_kept_draft_from_practice_source(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "configs")
    store.seed(PROJECT)
    practice = apply_order_mode(RUNNER, "alpha", "practice", ("375", 5))
    store.save("runner", "runner", practice, author="t", allow_order_mode=True)
    before = store.get("runner", "runner")
    result, payload = materialize_override(
        store,
        source="runner",
        uploads_root=tmp_path / "uploads",
        author="t@example.com",
        keep=True,
        name="bt-alpha-800",
        plugins={"alpha": {"risk": {"max_daily_loss": "800", "max_trades_per_day": 30}}},
    )
    assert result.ok and payload is not None
    assert payload["storage"] == "runner"
    assert payload["name"] == "bt-alpha-800"
    assert store.get("runner", "runner")["sha"] == before["sha"]
    draft = store.get("runner", "bt-alpha-800")
    alpha = next(p for p in yaml.safe_load(draft["text"])["plugins"] if p["id"] == "alpha")
    assert alpha["execution"]["order_mode"] == "paper"
    assert alpha["risk"]["max_daily_loss"] == "800"


def test_override_config_api_isolation(console: Console) -> None:
    before = console.get("/api/configs/runner/runner").json()
    r = console.post(
        "/api/backtest-plugin/override-config",
        json={
            "source": "runner",
            "keep": False,
            "plugins": {
                "alpha": {
                    "risk": {
                        "max_contracts": 8,
                        "max_open_trades": 1,
                        "max_daily_loss": "800",
                        "max_trades_per_day": 30,
                    },
                    "fills": {"slippage_ticks": 1, "trade_through_ticks": 1},
                    "hold_through_break": False,
                }
            },
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["override"]["live_untouched"] is True
    assert body["override"]["source_sha"] == before["sha"]
    after = console.get("/api/configs/runner/runner").json()
    assert after["sha"] == before["sha"]
    assert after["text"] == before["text"]
    path = Path(body["override"]["path"])
    assert path.is_file()
    alpha = next(p for p in yaml.safe_load(path.read_text())["plugins"] if p["id"] == "alpha")
    assert alpha["risk"]["max_daily_loss"] == "800"
    assert alpha["fills"]["slippage_ticks"] == 1


def test_override_config_api_kept_draft(console: Console) -> None:
    before = console.get("/api/configs/runner/runner").json()
    r = console.post(
        "/api/backtest-plugin/override-config",
        json={
            "source": "runner",
            "keep": True,
            "name": "bt-ab-loss-500",
            "plugins": {"alpha": {"risk": {"max_daily_loss": "500"}}},
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["override"]["name"] == "bt-ab-loss-500"
    assert console.get("/api/configs/runner/runner").json()["sha"] == before["sha"]
    draft = console.get("/api/configs/runner/bt-ab-loss-500").json()
    assert "500" in draft["text"]


def test_override_then_job_uses_override_path(console: Console) -> None:
    ov = console.post(
        "/api/backtest-plugin/override-config",
        json={
            "source": "runner",
            "keep": False,
            "plugins": {"alpha": {"risk": {"max_daily_loss": "800"}}},
        },
    )
    assert ov.status_code == 200, ov.text
    path = ov.json()["override"]["path"]
    live_path = console.get("/api/configs/runner/runner").json()["path"]
    assert path != live_path
    job = console.post(
        "/api/jobs",
        json={
            "command": "fse backtest-plugin",
            "values": {
                "config": path,
                "plugin": ["alpha"],
                "first": "2026-10-01",
                "last": "2026-10-01",
                "allow_gaps": True,
            },
        },
    )
    assert job.status_code == 201, job.text
    argv = job.json()["argv"]
    assert f"--config={path}" in argv
    assert f"--config={live_path}" not in argv
    assert not any(a == live_path or a.endswith("runner/runner/current.yaml") for a in argv)
