"""Jobs see ``FSE_RECORDINGS_S3`` when the console has a data bucket (offloaded recordings)."""

from __future__ import annotations

from pathlib import Path

import pytest

from fse_ui.app import child_env_factory
from fse_ui.secrets_store import LocalSecretStore
from tests.conftest import make_settings


def test_child_env_points_the_replay_at_the_data_bucket(tmp_path: Path) -> None:
    secrets = LocalSecretStore(tmp_path / "secrets.env")
    env = child_env_factory(make_settings(tmp_path, data_bucket="fse-data-x"), secrets)()
    assert env["FSE_RECORDINGS_S3"] == "s3://fse-data-x/"
    assert "FSE_RECORDINGS_S3" not in child_env_factory(make_settings(tmp_path), secrets)()


def test_an_operator_value_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FSE_RECORDINGS_S3", "s3://other/prefix")
    secrets = LocalSecretStore(tmp_path / "secrets.env")
    env = child_env_factory(make_settings(tmp_path, data_bucket="fse-data-x"), secrets)()
    assert env["FSE_RECORDINGS_S3"] == "s3://other/prefix"
