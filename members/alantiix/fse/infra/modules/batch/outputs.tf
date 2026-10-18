output "cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "cluster_arn" {
  description = "Built from the name, so it is known at plan time (ecs:cluster conditions)."
  value       = local.cluster_arn
}

output "task_family" {
  value = aws_ecs_task_definition.batch.family
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.batch.name
}

output "task_role_name" {
  value = aws_iam_role.task.name
}

output "exec_role_name" {
  value = aws_iam_role.exec.name
}

output "event_pattern" {
  description = "EventBridge pattern of the stopped-task alert."
  value       = aws_cloudwatch_event_rule.batch_stopped.event_pattern
}

output "efs_reconcile_rule_name" {
  description = "EventBridge rule that runs seed-efs-cache on a schedule."
  value       = aws_cloudwatch_event_rule.efs_cache_reconcile.name
}

output "efs_reconcile_schedule" {
  value = aws_cloudwatch_event_rule.efs_cache_reconcile.schedule_expression
}

output "events_role_name" {
  value = aws_iam_role.events.name
}
