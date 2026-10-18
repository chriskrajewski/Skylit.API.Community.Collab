# Mail every stopped fse-batch task with its exit code and stop reason
# (Spot interruptions show stopCode SpotInterruption).

resource "aws_cloudwatch_event_rule" "batch_stopped" {
  name        = "${var.family}-stopped"
  description = "fse-batch task stopped"
  event_pattern = jsonencode({
    source        = ["aws.ecs"]
    "detail-type" = ["ECS Task State Change"]
    detail = {
      lastStatus = ["STOPPED"]
      group      = ["family:${var.family}"]
      clusterArn = [local.cluster_arn]
    }
  })
}

resource "aws_cloudwatch_event_target" "batch_stopped" {
  rule      = aws_cloudwatch_event_rule.batch_stopped.name
  target_id = "fse-alerts"
  arn       = var.alerts_topic_arn

  input_transformer {
    input_paths = {
      taskArn       = "$.detail.taskArn"
      stoppedReason = "$.detail.stoppedReason"
      stopCode      = "$.detail.stopCode"
      exitCode      = "$.detail.containers[0].exitCode"
    }
    input_template = "\"fse-batch task stopped: exit <exitCode>, <stopCode>: <stoppedReason> (<taskArn>)\""
  }
}

# S3→EFS cache reconcile: same seed-efs-cache entrypoint as the manual seed
# script, under the S3 pull lock (skip if the lock is held). Family ARN without
# a revision so EventBridge always runs the latest ACTIVE fse-batch revision.

locals {
  # Family ARN (no :revision): CI registers new revisions; Terraform's tracked
  # revision may lag. EventBridge resolves the latest ACTIVE revision.
  task_definition_family_arn = "arn:aws:ecs:${var.region}:${var.account_id}:task-definition/${var.family}"
  events_trust = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "events.amazonaws.com" }
        Action    = "sts:AssumeRole"
        Condition = {
          StringEquals = { "aws:SourceAccount" = var.account_id }
        }
      },
    ]
  })
}

resource "aws_iam_role" "events" {
  name               = "fse-batch-events"
  assume_role_policy = local.events_trust
}

resource "aws_iam_role_policy" "events_run_task" {
  name = "fse-batch-events-run-task"
  role = aws_iam_role.events.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RunBatchTask"
        Effect = "Allow"
        Action = "ecs:RunTask"
        Resource = [
          local.task_definition_family_arn,
          "${local.task_definition_family_arn}:*",
        ]
        Condition = {
          ArnEquals = { "ecs:cluster" = local.cluster_arn }
        }
      },
      {
        Sid      = "PassBatchRoles"
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = [aws_iam_role.task.arn, aws_iam_role.exec.arn]
        Condition = {
          StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" }
        }
      },
    ]
  })
}

resource "aws_cloudwatch_event_rule" "efs_cache_reconcile" {
  name                = "${var.family}-efs-cache-reconcile"
  description         = "S3→EFS Data_Cache reconcile (seed-efs-cache under pull lock)"
  schedule_expression = var.efs_reconcile_schedule
}

resource "aws_cloudwatch_event_target" "efs_cache_reconcile" {
  rule      = aws_cloudwatch_event_rule.efs_cache_reconcile.name
  target_id = "fse-efs-cache-reconcile"
  arn       = local.cluster_arn
  role_arn  = aws_iam_role.events.arn

  ecs_target {
    task_count          = 1
    task_definition_arn = local.task_definition_family_arn
    propagate_tags      = "TASK_DEFINITION"

    # On-Demand: full-tree S3→EFS sync can run 30–60+ min; Spot reclaim leaves a
    # half-synced EFS tree and (historically) a stuck pull.lock.
    capacity_provider_strategy {
      capacity_provider = "FARGATE"
      weight            = 1
    }

    network_configuration {
      subnets          = var.subnet_ids
      security_groups  = [var.batch_security_group_id]
      assign_public_ip = true
    }
  }

  input = jsonencode({
    containerOverrides = [
      {
        name    = "fse"
        command = ["seed-efs-cache"]
      },
    ]
  })
}
