# Asserted by tests/ui.tftest.hcl (child resources are not addressable there).
# Nothing here is secret: ui.env holds no secret.

output "bundle_content" {
  description = "fse-ui-host-bundle document content (SSM limit 64 KB)."
  value       = aws_ssm_document.ui_host.content
}

output "document_names" {
  value = {
    control = aws_ssm_document.runner_control.name
    bundle  = aws_ssm_document.ui_host.name
    remove  = aws_ssm_document.ui_host_remove.name
  }
}

output "control_document_content" {
  value = aws_ssm_document.runner_control.content
}

output "association" {
  value = {
    document                         = aws_ssm_association.ui_host.name
    targets                          = aws_ssm_association.ui_host.targets
    max_errors                       = aws_ssm_association.ui_host.max_errors
    compliance_severity              = aws_ssm_association.ui_host.compliance_severity
    wait_for_success_timeout_seconds = aws_ssm_association.ui_host.wait_for_success_timeout_seconds
  }
}

output "parameter_names" {
  value = [aws_ssm_parameter.runner_mode.name, aws_ssm_parameter.runner_config.name]
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.ui.name
}

output "runner_console_policy" {
  description = "JSON of the console policy on the runner role."
  value       = aws_iam_role_policy.runner_console.policy
}

output "batch_console_policy" {
  description = "JSON of the console policy on the batch task role."
  value       = aws_iam_role_policy.batch_console.policy
}
