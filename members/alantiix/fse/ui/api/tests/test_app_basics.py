from __future__ import annotations

from pathlib import Path

import pytest

from fse_ui.db import MIGRATIONS, Db
from fse_ui.settings import Settings, SettingsError
from tests.conftest import PROJECT, Console, make_settings


def test_health_is_public_and_api_needs_a_session(console: Console) -> None:
    fresh = console.client.__class__(console.app, base_url="http://localhost:5173")
    assert fresh.get("/api/health").json() == {"ok": True, "env": "local"}
    assert fresh.get("/api/session").status_code == 401
    assert fresh.get("/api/catalog").status_code == 401


def test_security_headers(console: Console) -> None:
    r = console.get("/api/health")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert "script-src 'self';" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_migrations_apply_once(tmp_path: Path) -> None:
    db = Db(tmp_path / "ui.sqlite")
    assert db.migrate() == len(MIGRATIONS)
    db.close()
    again = Db(tmp_path / "ui.sqlite")
    assert again.migrate() == len(MIGRATIONS)
    again.close()


def test_settings_from_env(tmp_path: Path) -> None:
    s = Settings.from_env(
        {"FSE_UI_HOME": str(tmp_path), "FSE_UI_DEV_AUTH": "1", "FSE_UI_PROJECT_DIR": str(PROJECT)}
    )
    assert s.data_dir == tmp_path / ".skylit-fse"
    assert s.cookie_name == "fse_session"
    assert s.enable_live is False
    s.check()


def test_dev_auth_refuses_a_public_bind(home: Path) -> None:
    with pytest.raises(SettingsError, match="loopback"):
        make_settings(home, bind_host="0.0.0.0").check()  # noqa: S104


def test_dev_auth_refuses_aws(home: Path) -> None:
    with pytest.raises(SettingsError, match="only with FSE_UI_ENV=local"):
        make_settings(home, env="aws", public_url="https://fse.example.com").check()


def test_no_login_configured_is_refused(home: Path) -> None:
    with pytest.raises(SettingsError, match="no login is configured"):
        make_settings(home, dev_auth=False).check()


def test_https_cookie_name(home: Path) -> None:
    s = make_settings(home, public_url="https://fse.example.com")
    assert s.cookie_name == "__Host-fse_session"
    assert s.secure_cookies
