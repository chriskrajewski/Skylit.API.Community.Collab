# Runner host: plan-only with a mocked AWS provider (no credentials, no AWS
# calls). command = plan everywhere: prevent_destroy on the data volume breaks
# the destroy terraform test runs after an apply. prevent_destroy itself and
# the absence of key_name are asserted by tests/unit/test_infra_static.py.
mock_provider "aws" {}

# The root declares aws.us_east_1 and random (modules/ui_edge).
mock_provider "aws" {
  alias = "us_east_1"
}

mock_provider "random" {}

variables {
  alert_email = "ops@example.com"
  # Mock providers cannot import (core.tftest.hcl covers the import path).
  adopt_existing_data_bucket = false
}

# The module on its own, with known inputs, so the rendered cloud-init is known.
run "runner_module" {
  command = plan

  module {
    source = "./modules/runner"
  }

  variables {
    region             = "us-east-1"
    account_id         = "123456789012"
    subnet_id          = "subnet-0123456789abcdef0"
    availability_zone  = "us-east-1a"
    security_group_id  = "sg-0123456789abcdef0"
    ecr_repository_url = "123456789012.dkr.ecr.us-east-1.amazonaws.com/fse"
    ecr_repository_arn = "arn:aws:ecr:us-east-1:123456789012:repository/fse"
    alerts_topic_arn   = "arn:aws:sns:us-east-1:123456789012:fse-alerts"
  }

  override_data {
    target = data.aws_ssm_parameter.ami
    values = {
      insecure_value = "ami-0123456789abcdef0"
    }
  }

  # A volume id at plan time, so the cloud-init that embeds it is known.
  override_resource {
    target          = aws_ebs_volume.data
    override_during = plan
    values = {
      id = "vol-0123456789abcdef0"
    }
  }

  assert {
    condition     = aws_instance.this.metadata_options[0].http_tokens == "required"
    error_message = "IMDSv2 must be required."
  }

  assert {
    condition     = aws_instance.this.metadata_options[0].http_put_response_hop_limit == 1
    error_message = "IMDS hop limit must be 1 (containers get no instance credentials)."
  }

  assert {
    condition     = aws_instance.this.root_block_device[0].encrypted
    error_message = "Root volume must be encrypted."
  }

  assert {
    condition     = aws_ebs_volume.data.encrypted && aws_ebs_volume.data.type == "gp3"
    error_message = "Data volume must be encrypted gp3."
  }

  assert {
    condition     = aws_ebs_volume.data.availability_zone == "us-east-1a"
    error_message = "Data volume must be in the instance subnet's AZ."
  }

  assert {
    condition     = aws_instance.this.ami == "ami-0123456789abcdef0"
    error_message = "AMI must come from the AL2023 arm64 SSM parameter."
  }

  assert {
    condition     = aws_instance.this.instance_type == "t4g.small"
    error_message = "Runner host must default to t4g.small."
  }

  assert {
    condition     = aws_instance.this.tags["Role"] == "fse-runner" && aws_instance.this.tags["Patch Group"] == "fse"
    error_message = "Instance needs Role = fse-runner (deploy SendCommand condition) and Patch Group = fse."
  }

  assert {
    condition     = output.user_data_size < 16384
    error_message = "Rendered cloud-init must stay under the 16 KB EC2 user data limit."
  }

  assert {
    condition     = strcontains(local.user_data, "fse-mount-data.sh vol-0123456789abcdef0")
    error_message = "cloud-init must mount the data volume by its id."
  }

  assert {
    condition     = strcontains(local.user_data, "FSE_ALERT_TOPIC_ARN=arn:aws:sns:us-east-1:123456789012:fse-alerts")
    error_message = "host.env must carry the alerts topic ARN."
  }

  assert {
    condition     = strcontains(local.user_data, "\"123456789012.dkr.ecr.us-east-1.amazonaws.com\":\"ecr-login\"")
    error_message = "Docker must use the ECR credential helper for the registry."
  }

  assert {
    condition     = output.maintenance_window_timezone == "America/New_York"
    error_message = "Patch window must be scheduled in America/New_York."
  }

  assert {
    condition     = aws_ssm_maintenance_window.patch.schedule == "cron(0 6 ? * SAT *)"
    error_message = "Patch window must be Saturday 06:00."
  }

  assert {
    condition     = aws_volume_attachment.data.stop_instance_before_detaching
    error_message = "Detaching the data volume must stop the instance first."
  }

  assert {
    condition     = aws_cloudwatch_metric_alarm.system_check.alarm_actions == toset(["arn:aws:automate:us-east-1:ec2:recover", "arn:aws:sns:us-east-1:123456789012:fse-alerts"])
    error_message = "System status check alarm must recover the instance and alert."
  }

  assert {
    condition     = contains(tolist(aws_cloudwatch_metric_alarm.instance_check.alarm_actions), "arn:aws:automate:us-east-1:ec2:reboot")
    error_message = "Instance status check alarm must reboot the instance."
  }

  assert {
    condition     = aws_cloudwatch_metric_alarm.log_silence.treat_missing_data == "breaching"
    error_message = "Log silence alarm must treat missing data as breaching."
  }

  assert {
    condition     = aws_dlm_lifecycle_policy.data.policy_details[0].schedule[0].retain_rule[0].count == 7
    error_message = "DLM must keep 7 daily snapshots."
  }

  assert {
    condition     = aws_iam_role.runner.name == "fse-runner"
    error_message = "Runner role must be named fse-runner."
  }
}

# The whole stack: the runner is wired in and nothing accepts inbound traffic.
run "runner_in_stack" {
  command = plan

  override_data {
    target = data.aws_caller_identity.current
    values = {
      account_id = "123456789012"
    }
  }

  override_data {
    target = module.network.data.aws_availability_zones.available
    values = {
      names = ["us-east-1a", "us-east-1b"]
    }
  }

  override_data {
    target = module.runner.data.aws_ssm_parameter.ami
    values = {
      insecure_value = "ami-0123456789abcdef0"
    }
  }

  assert {
    condition     = module.network.security_group_ingress_counts.runner == 0
    error_message = "Runner security group must have no ingress rules."
  }

  assert {
    condition     = module.network.default_route_nat_gateway_id == null
    error_message = "No NAT gateway on the default route."
  }

  assert {
    condition     = module.runner.role_name == "fse-runner"
    error_message = "Root stack must create the fse-runner role."
  }

  assert {
    condition     = module.runner.log_group_name == "/fse/runner"
    error_message = "Runner log group must be /fse/runner."
  }

  assert {
    condition     = module.runner.maintenance_window_timezone == "America/New_York"
    error_message = "Patch window must be scheduled in America/New_York."
  }
}
