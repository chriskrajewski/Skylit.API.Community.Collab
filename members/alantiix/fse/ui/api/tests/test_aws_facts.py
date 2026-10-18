"""The moto behaviors the AWS adapters rely on (design.md "Facts confirmed").

If one of these fails after a moto upgrade, the adapter tests that build on it
are no longer meaningful: switch those cases to botocore Stubber.
"""

from __future__ import annotations

import time

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

REGION = "us-east-1"


@mock_aws
def test_s3_if_none_match_on_a_held_key_is_412() -> None:
    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(Bucket="fse-data")
    s3.put_object(Bucket="fse-data", Key="locks/pull.lock", Body=b"job=a\n", IfNoneMatch="*")
    with pytest.raises(ClientError) as exc:
        s3.put_object(Bucket="fse-data", Key="locks/pull.lock", Body=b"job=b\n", IfNoneMatch="*")
    assert exc.value.response["Error"]["Code"] == "PreconditionFailed"
    assert exc.value.response["ResponseMetadata"]["HTTPStatusCode"] == 412


@mock_aws
def test_ssm_send_command_and_get_command_invocation() -> None:
    ec2 = boto3.client("ec2", region_name=REGION)
    image = ec2.describe_images()["Images"][0]["ImageId"]
    instance = ec2.run_instances(ImageId=image, MinCount=1, MaxCount=1)["Instances"][0]
    ssm = boto3.client("ssm", region_name=REGION)
    sent = ssm.send_command(
        InstanceIds=[instance["InstanceId"]],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": ["true"]},
    )["Command"]
    inv = ssm.get_command_invocation(CommandId=sent["CommandId"], InstanceId=instance["InstanceId"])
    assert inv["Status"] in ("Success", "InProgress", "Pending")


@mock_aws
def test_logs_get_log_events() -> None:
    logs = boto3.client("logs", region_name=REGION)
    logs.create_log_group(logGroupName="/fse/batch")
    logs.create_log_stream(logGroupName="/fse/batch", logStreamName="batch/fse/t1")
    logs.put_log_events(
        logGroupName="/fse/batch",
        logStreamName="batch/fse/t1",
        logEvents=[{"timestamp": int(time.time() * 1000), "message": "hello"}],
    )
    out = logs.get_log_events(
        logGroupName="/fse/batch", logStreamName="batch/fse/t1", startFromHead=True
    )
    assert [e["message"] for e in out["events"]] == ["hello"]
    assert out["nextForwardToken"]


@mock_aws
def test_cloudwatch_describe_alarms() -> None:
    cw = boto3.client("cloudwatch", region_name=REGION)
    cw.put_metric_alarm(
        AlarmName="fse-runner-log-silence",
        MetricName="IncomingLogEvents",
        Namespace="AWS/Logs",
        Statistic="Sum",
        Period=300,
        EvaluationPeriods=3,
        Threshold=1,
        ComparisonOperator="LessThanThreshold",
    )
    found = cw.describe_alarms(AlarmNames=["fse-runner-log-silence"])["MetricAlarms"]
    assert [a["AlarmName"] for a in found] == ["fse-runner-log-silence"]
    assert found[0]["StateValue"] in ("OK", "ALARM", "INSUFFICIENT_DATA")
