"""AWS settings (design.md 10.1) and the wiring create_app picks from them (10.2)."""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path
from typing import Any

import pytest

from fse_ui.app import create_app
from fse_ui.aws.alarms import AlarmsReader
from fse_ui.aws.clients import AwsClients
from fse_ui.aws.ecs import EcsExecutor
from fse_ui.aws.runner_ssm import SsmRunnerController
from fse_ui.aws.s3store import CompositeArtifactStore
from fse_ui.runner import ProcessRunnerController
from fse_ui.runs import LocalArtifactStore
from fse_ui.settings import Settings, SettingsError
from tests.aws_support import BUCKET, INSTANCE, SG, SUBNET, Aws
from tests.conftest import PROJECT

ECS_ENV = {
    "FSE_UI_EXECUTOR": "ecs",
    "FSE_UI_SECRET_STORE": "ssm",
    "FSE_UI_DATA_BUCKET": BUCKET,
    "FSE_UI_ECS_SUBNETS": SUBNET,
    "FSE_UI_ECS_SECURITY_GROUP": SG,
}


def _settings(tmp_path: Path, **env: str) -> Settings:
    base = {
        "FSE_UI_PROJECT_DIR": str(PROJECT),
        "FSE_UI_HOME": str(tmp_path),
        "FSE_UI_DEV_AUTH": "1",
    }
    return Settings.from_env({**base, **env})


def _checked(tmp_path: Path, **env: str) -> Settings:
    s = _settings(tmp_path, **env)
    s.check()
    return s


def test_defaults_are_local_and_need_no_aws(tmp_path: Path) -> None:
    s = _checked(tmp_path)
    assert (s.executor, s.runner_controller, s.alarm_names) == ("local", "process", ())
    assert (s.ecs_cluster, s.ecs_task_family, s.ecs_container) == ("fse", "fse-batch", "fse")
    assert (s.batch_log_group, s.runner_log_group) == ("/fse/batch", "/fse/runner")
    assert (s.runner_document, s.ecs_max_tasks) == ("fse-runner-control", 4)
    assert s.enable_live is False
    assert s.runner_state_file == s.data_dir / "host" / "runner-state.json"
    assert s.runner_held_file == s.data_dir / "host" / "runner-held"
    assert s.uploads_dir == s.ui_dir / "uploads"


def test_lists_are_comma_split_and_stripped(tmp_path: Path) -> None:
    env = {
        **ECS_ENV,
        "FSE_UI_ECS_SUBNETS": f" {SUBNET}, subnet-0123456789abcdef1 ,,",
        "FSE_UI_ALARM_NAMES": "fse-runner-log-silence, fse-runner-faults",
    }
    s = _checked(tmp_path, **env)
    assert s.ecs_subnets == (SUBNET, "subnet-0123456789abcdef1")
    assert s.alarm_names == ("fse-runner-log-silence", "fse-runner-faults")


def test_a_valid_ecs_and_ssm_setup(tmp_path: Path) -> None:
    s = _checked(
        tmp_path,
        **ECS_ENV,
        FSE_UI_RUNNER_CONTROLLER="ssm",
        FSE_UI_RUNNER_INSTANCE_ID=INSTANCE,
        FSE_UI_ECS_MAX_TASKS="16",
    )
    assert s.executor == "ecs" and s.ecs_max_tasks == 16


@pytest.mark.parametrize(
    ("env", "match"),
    [
        ({"FSE_UI_EXECUTOR": "fargate"}, "invalid FSE_UI_"),
        ({"FSE_UI_RUNNER_CONTROLLER": "shell"}, "invalid FSE_UI_"),
        ({"FSE_UI_ECS_MAX_TASKS": "many"}, "invalid FSE_UI_"),
    ],
)
def test_literals_and_numbers_fail_at_load(tmp_path: Path, env: dict[str, str], match: str) -> None:
    with pytest.raises(SettingsError, match=match):
        _settings(tmp_path, **env)


@pytest.mark.parametrize(
    ("env", "match"),
    [
        ({"FSE_UI_DATA_BUCKET": ""}, "FSE_UI_DATA_BUCKET"),
        ({"FSE_UI_DATA_BUCKET": "Bad_Bucket"}, "FSE_UI_DATA_BUCKET"),
        ({"FSE_UI_DATA_BUCKET": "ab"}, "FSE_UI_DATA_BUCKET"),
        ({"FSE_UI_ECS_CLUSTER": "a b"}, "FSE_UI_ECS_CLUSTER"),
        ({"FSE_UI_ECS_TASK_FAMILY": "x" * 256}, "FSE_UI_ECS_TASK_FAMILY"),
        ({"FSE_UI_ECS_CONTAINER": "f/se"}, "FSE_UI_ECS_CONTAINER"),
        ({"FSE_UI_ECS_SUBNETS": ""}, "FSE_UI_ECS_SUBNETS needs 1 to 16"),
        ({"FSE_UI_ECS_SUBNETS": ",".join([SUBNET] * 17)}, "FSE_UI_ECS_SUBNETS needs 1 to 16"),
        ({"FSE_UI_ECS_SUBNETS": "subnet-xyz"}, "FSE_UI_ECS_SUBNETS"),
        ({"FSE_UI_ECS_SECURITY_GROUP": ""}, "FSE_UI_ECS_SECURITY_GROUP"),
        ({"FSE_UI_ECS_SECURITY_GROUP": "sg-123"}, "FSE_UI_ECS_SECURITY_GROUP"),
        ({"FSE_UI_BATCH_LOG_GROUP": "/fse/batch logs"}, "FSE_UI_BATCH_LOG_GROUP"),
        ({"FSE_UI_RUNNER_LOG_GROUP": "x" * 513}, "FSE_UI_RUNNER_LOG_GROUP"),
        ({"FSE_UI_RUNNER_DOCUMENT": "ab"}, "FSE_UI_RUNNER_DOCUMENT"),
        ({"FSE_UI_ALARM_NAMES": "ok,bad name"}, "FSE_UI_ALARM_NAMES"),
        ({"FSE_UI_ALARM_NAMES": ",".join(f"a{i}" for i in range(21))}, "more than 20"),
        ({"FSE_UI_ECS_MAX_TASKS": "0"}, "1..16"),
        ({"FSE_UI_ECS_MAX_TASKS": "17"}, "1..16"),
        ({"FSE_UI_SECRET_STORE": "local"}, "needs FSE_UI_SECRET_STORE=ssm"),
    ],
)
def test_ecs_validation(tmp_path: Path, env: dict[str, str], match: str) -> None:
    s = _settings(tmp_path, **{**ECS_ENV, **env})
    with pytest.raises(SettingsError, match=match):
        s.check()


@pytest.mark.parametrize(
    ("env", "match"),
    [
        ({}, "FSE_UI_RUNNER_INSTANCE_ID"),
        ({"FSE_UI_RUNNER_INSTANCE_ID": "i-xyz"}, "FSE_UI_RUNNER_INSTANCE_ID"),
        (
            {"FSE_UI_RUNNER_INSTANCE_ID": INSTANCE, "FSE_UI_ENABLE_LIVE": "true"},
            "FSE_UI_ENABLE_LIVE must stay false",
        ),
    ],
)
def test_ssm_runner_validation(tmp_path: Path, env: dict[str, str], match: str) -> None:
    s = _settings(tmp_path, FSE_UI_RUNNER_CONTROLLER="ssm", **env)
    with pytest.raises(SettingsError, match=match):
        s.check()


# ---------------------------------------------------------------- wiring


def test_create_app_picks_the_aws_classes(
    aws: Aws, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(logging.getLogger("botocore"), "level", logging.DEBUG)
    s = _settings(
        tmp_path,
        **ECS_ENV,
        FSE_UI_RUNNER_CONTROLLER="ssm",
        FSE_UI_RUNNER_INSTANCE_ID=INSTANCE,
        FSE_UI_ALARM_NAMES="fse-runner-log-silence",
    )
    app = create_app(s, aws=aws.clients)
    assert isinstance(app.state.jobs._executors["ecs"], EcsExecutor)
    assert isinstance(app.state.runs.store, CompositeArtifactStore)
    assert app.state.configs._mirror is not None
    assert isinstance(app.state.runner, SsmRunnerController)
    assert isinstance(app.state.alarms, AlarmsReader)
    assert set(app.state.locks) == {"pull", "holdout", "recordings"}
    assert app.state.catalog.target == "local"  # FSE_UI_ENV=local
    for name in ("botocore", "boto3", "urllib3"):
        assert logging.getLogger(name).level == logging.WARNING


def test_create_app_stays_local_without_aws_settings(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    assert set(app.state.jobs._executors) == {"local"}
    assert isinstance(app.state.runs.store, LocalArtifactStore)
    assert isinstance(app.state.runner, ProcessRunnerController)
    assert app.state.alarms is None and app.state.locks == {} and app.state.aws is None


def test_env_aws_builds_the_aws_catalog(aws: Aws, tmp_path: Path) -> None:
    s = Settings.from_env(
        {
            "FSE_UI_PROJECT_DIR": str(PROJECT),
            "FSE_UI_HOME": str(tmp_path),
            "FSE_UI_ENV": "aws",
            "FSE_UI_PUBLIC_URL": "https://fse.example.com",
            "FSE_UI_OIDC_ISSUER": "https://issuer.example.com",
            "FSE_UI_OIDC_CLIENT_ID": "client",
            "FSE_UI_OIDC_CLIENT_SECRET": "not-a-real-secret",
            "FSE_UI_OIDC_AUTHORIZE_URL": "https://issuer.example.com/authorize",
            "FSE_UI_OIDC_TOKEN_URL": "https://issuer.example.com/token",
            "FSE_UI_OIDC_JWKS_URL": "https://issuer.example.com/jwks",
            **ECS_ENV,
        }
    )
    app = create_app(s, aws=aws.clients)
    assert app.state.catalog.target == "aws"


def test_aws_clients_have_bounded_retries_and_no_cognito(tmp_path: Path) -> None:
    fields = {f.name for f in dataclasses.fields(AwsClients)}
    assert fields == {"ecs", "s3", "logs", "ssm", "cloudwatch"}
    clients = AwsClients.from_settings(_settings(tmp_path, FSE_UI_AWS_REGION="us-west-2"))
    config: Any = clients.ecs.meta.config
    # botocore stores max_attempts=5 retries as 6 attempts in total
    assert config.retries == {"mode": "standard", "total_max_attempts": 6}
    assert (config.connect_timeout, config.read_timeout) == (5, 20)
    assert clients.s3.meta.region_name == "us-west-2"


# ---------------------------------------------------------------- plugin orders (FEAT-002)


def test_plugin_order_settings(tmp_path: Path) -> None:
    s = _checked(tmp_path)
    assert (s.plugin_orders, s.plugin_orders_param, s.topstep_verified_on) == (False, "", None)
    s = _checked(
        tmp_path,
        FSE_UI_PLUGIN_ORDERS="enabled",
        FSE_UI_PLUGIN_ORDERS_PARAM="/fse/control/plugin_orders",
        FSE_UI_TOPSTEP_VERIFIED_ON="2026-10-13",
    )
    assert (s.plugin_orders, s.plugin_orders_param, s.topstep_verified_on) == (
        True,
        "/fse/control/plugin_orders",
        "2026-10-13",
    )
    for value in ("disabled", "true", "1", "ENABLED"):
        assert _checked(tmp_path, FSE_UI_PLUGIN_ORDERS=value).plugin_orders is False
    assert _checked(tmp_path, FSE_UI_TOPSTEP_VERIFIED_ON="").topstep_verified_on is None
    assert _checked(tmp_path, FSE_UI_TOPSTEP_VERIFIED_ON="  ").topstep_verified_on is None


@pytest.mark.parametrize("value", ["2026/10/13", "yesterday", "2026-10-13x", "13-10-2026"])
def test_a_bad_verified_date_is_refused(tmp_path: Path, value: str) -> None:
    with pytest.raises(SettingsError, match="FSE_UI_TOPSTEP_VERIFIED_ON must be YYYY-MM-DD"):
        _settings(tmp_path, FSE_UI_TOPSTEP_VERIFIED_ON=value)


def test_plugin_orders_do_not_lift_the_enable_live_refusal(tmp_path: Path) -> None:
    s = _settings(
        tmp_path,
        FSE_UI_RUNNER_CONTROLLER="ssm",
        FSE_UI_RUNNER_INSTANCE_ID=INSTANCE,
        FSE_UI_ENABLE_LIVE="true",
        FSE_UI_PLUGIN_ORDERS="enabled",
        FSE_UI_TOPSTEP_VERIFIED_ON="2026-10-13",
    )
    with pytest.raises(SettingsError, match="FSE_UI_ENABLE_LIVE must stay false"):
        s.check()


def test_the_session_reports_plugin_orders(console_factory: Any) -> None:
    body = console_factory().get("/api/session").json()
    assert (body["plugin_orders"], body["topstep_verified_on"]) == (False, None)
    body = (
        console_factory(plugin_orders=True, topstep_verified_on="2026-10-13")
        .get("/api/session")
        .json()
    )
    assert (body["plugin_orders"], body["topstep_verified_on"]) == (True, "2026-10-13")
    assert body["enable_live"] is False
