# fse console on the runner host (enable_ui): the bundle association, the
# runner mode/config parameters and the /fse/ui log group. CloudFront, Cognito
# and the origin secret live in modules/ui_edge.

# A content change makes a new document version, and the association re-runs.
# Apply waits for it, so a failed bundle fails the apply visibly.
resource "aws_ssm_association" "ui_host" {
  name                             = aws_ssm_document.ui_host.name
  association_name                 = "fse-ui-host"
  document_version                 = aws_ssm_document.ui_host.latest_version
  max_errors                       = "0"
  compliance_severity              = "HIGH"
  wait_for_success_timeout_seconds = 600

  targets {
    key    = "InstanceIds"
    values = [var.instance_id]
  }

  # The units it restarts need the log group (awslogs), the parameters and the
  # host permissions (Cognito client secret, ECR pulls) to exist first.
  depends_on = [
    aws_cloudwatch_log_group.ui,
    aws_ssm_parameter.runner_mode,
    aws_ssm_parameter.runner_config,
    aws_iam_role_policy.runner_console,
  ]
}

# Written by the console (SsmRunnerController) before each start/restart; read
# by fse-runner-mode.sh. Terraform creates them and never resets the value.
resource "aws_ssm_parameter" "runner_mode" {
  name        = "${var.ssm_prefix}/runner_mode"
  description = "fse runner mode (run or paper), set from the console."
  type        = "String"
  value       = "run"

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "runner_config" {
  name        = "${var.ssm_prefix}/runner_config"
  description = "fse runner config path, set from the console."
  type        = "String"
  value       = "configs/runner.yaml"

  lifecycle {
    ignore_changes = [value]
  }
}

# fse-ui (stream ui) and fse-edge (stream edge), shipped by dockerd's awslogs driver.
resource "aws_cloudwatch_log_group" "ui" {
  name              = local.ui_log_group_name
  retention_in_days = var.log_retention_days
}
