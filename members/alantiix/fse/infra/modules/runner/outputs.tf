output "instance_id" {
  value = aws_instance.this.id
}

output "public_ip" {
  description = "Elastic IP of the runner host (outbound only; nothing listens)."
  value       = aws_eip.runner.public_ip
}

output "instance_type" {
  value = aws_instance.this.instance_type
}

output "data_volume_id" {
  value = aws_ebs_volume.data.id
}

output "role_name" {
  value = aws_iam_role.runner.name
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.runner.name
}

output "user_data_size" {
  description = "Rendered cloud-init size in bytes. EC2 rejects user data over 16384 bytes."
  value       = length(local.user_data)
}

output "user_data_sha" {
  description = "sha256 of the rendered cloud-init (tests assert it does not change with enable_ui)."
  value       = sha256(local.user_data)
}

output "alarm_names" {
  description = "Host alarms (the console Health page lists them)."
  value = [
    aws_cloudwatch_metric_alarm.system_check.alarm_name,
    aws_cloudwatch_metric_alarm.instance_check.alarm_name,
    aws_cloudwatch_metric_alarm.log_silence.alarm_name,
  ]
}

output "maintenance_window_timezone" {
  value = aws_ssm_maintenance_window.patch.schedule_timezone
}
