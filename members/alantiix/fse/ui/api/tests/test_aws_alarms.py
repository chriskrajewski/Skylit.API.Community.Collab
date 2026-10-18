"""CloudWatch alarms on the Health page (design.md 10.8) and the /api/health/daily shape."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from fse_ui.aws.alarms import AlarmsReader
from tests.aws_support import Aws, FakeTime
from tests.conftest import Console

NAMES = ("fse-runner-log-silence", "fse-runner-faults")


def _alarm(aws: Aws, name: str) -> None:
    aws.cloudwatch.put_metric_alarm(
        AlarmName=name,
        MetricName="IncomingLogEvents",
        Namespace="AWS/Logs",
        Statistic="Sum",
        Period=300,
        EvaluationPeriods=3,
        Threshold=1,
        ComparisonOperator="LessThanThreshold",
    )


@pytest.mark.asyncio
async def test_reader_lists_named_alarms_in_order_and_caches(aws: Aws, fake_time: FakeTime) -> None:
    for name in (*reversed(NAMES), "someone-else"):
        _alarm(aws, name)
    aws.cloudwatch.set_alarm_state(
        AlarmName="fse-runner-log-silence", StateValue="ALARM", StateReason="no logs for 15 min"
    )
    reader = AlarmsReader(aws.cloudwatch, NAMES, clock=fake_time.clock)
    rows = await reader.read()
    assert isinstance(rows, list)
    assert [r["name"] for r in rows] == list(NAMES)
    assert rows[0]["state"] == "ALARM" and rows[0]["reason"] == "no logs for 15 min"
    assert isinstance(rows[0]["updated_at"], float)
    aws.cloudwatch.set_alarm_state(
        AlarmName="fse-runner-log-silence", StateValue="OK", StateReason="back"
    )
    assert (await reader.read()) == rows  # cached for 60 s
    fake_time.now += 61
    fresh = await reader.read()
    assert isinstance(fresh, list) and fresh[0]["state"] == "OK"


@pytest.mark.asyncio
async def test_reader_turns_errors_into_an_error_entry(aws: Aws) -> None:
    def deny(**_: object) -> None:
        from botocore.exceptions import ClientError

        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "x"}}, "DescribeAlarms")

    aws.cloudwatch.meta.events.register("before-call.cloudwatch.DescribeAlarms", deny)
    assert await AlarmsReader(aws.cloudwatch, NAMES).read() == {
        "error": "cannot read CloudWatch alarms: AccessDenied"
    }


def test_health_daily_shape(
    aws_console: Callable[..., Console], aws: Aws, console: Console
) -> None:
    assert console.get("/api/health/daily").json() == {"reports": [], "alarms": None}
    _alarm(aws, NAMES[0])
    on_aws = aws_console(alarm_names=NAMES)
    body = on_aws.get("/api/health/daily").json()
    assert body["reports"] == []
    assert [a["name"] for a in body["alarms"]] == [NAMES[0]]  # the other name has no alarm
    assert set(body["alarms"][0]) == {"name", "state", "reason", "updated_at"}


def test_session_and_system_report_the_executor(
    aws_console: Callable[..., Console], aws: Aws, console: Console
) -> None:
    local = console.get("/api/session").json()
    assert (local["executor"], local["runner_controller"]) == ("local", "process")
    assert "aws" not in console.get("/api/system").json()
    on_aws = aws_console()
    session = on_aws.get("/api/session").json()
    assert (session["executor"], session["runner_controller"]) == ("ecs", "process")
    info = on_aws.get("/api/system").json()
    assert info["executor"] == "ecs"
    assert info["aws"] == {"bucket": "fse-data-test", "cluster": "fse", "region": "us-east-1"}
