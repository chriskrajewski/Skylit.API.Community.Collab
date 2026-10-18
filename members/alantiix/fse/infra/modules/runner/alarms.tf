# Host health alarms. Each one mails the alerts topic; the status-check alarms
# also recover (host fault) or reboot (guest fault) the instance.

resource "aws_cloudwatch_metric_alarm" "system_check" {
  alarm_name          = "${var.name}-system-check"
  alarm_description   = "EC2 system status check failed: AWS moves the instance to healthy hardware (same IP, same volumes)."
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed_System"
  dimensions          = { InstanceId = aws_instance.this.id }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  alarm_actions       = ["arn:aws:automate:${var.region}:ec2:recover", var.alerts_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "instance_check" {
  alarm_name          = "${var.name}-instance-check"
  alarm_description   = "EC2 instance status check failed: the instance is rebooted."
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed_Instance"
  dimensions          = { InstanceId = aws_instance.this.id }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  alarm_actions       = ["arn:aws:automate:${var.region}:ec2:reboot", var.alerts_topic_arn]
}

# No runner log line for 15 minutes. Missing data counts as breaching, so a
# dead container alarms too. It can fire on quiet weekends when fse logs
# nothing while markets are closed (documented in the runbook, not suppressed).
resource "aws_cloudwatch_metric_alarm" "log_silence" {
  alarm_name          = "${var.name}-log-silence"
  alarm_description   = "No log events in ${local.log_group_name} for 15 minutes."
  namespace           = "AWS/Logs"
  metric_name         = "IncomingLogEvents"
  dimensions          = { LogGroupName = aws_cloudwatch_log_group.runner.name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 3
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [var.alerts_topic_arn]
}
