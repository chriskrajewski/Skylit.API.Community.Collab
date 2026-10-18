# On-demand batch work (fse pull, backtest, experiment, report) on Fargate
# Spot. Nothing runs until scripts/aws-batch.sh calls ecs run-task, so the
# idle cost is the log group and nothing else.

locals {
  log_group_name = "/fse/batch"
  # Built from known values so the EventBridge pattern is known at plan time.
  cluster_arn = "arn:aws:ecs:${var.region}:${var.account_id}:cluster/${var.cluster_name}"
}

resource "aws_ecs_cluster" "this" {
  name = var.cluster_name

  # Container Insights costs per metric; the batch log is enough.
  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = ["FARGATE_SPOT", "FARGATE"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE_SPOT"
    weight            = 1
  }
}

resource "aws_cloudwatch_log_group" "batch" {
  name              = local.log_group_name
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_task_definition" "batch" {
  family                   = var.family
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = aws_iam_role.exec.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  # Kept at 100 GiB while FSE_CACHE_BACKEND=s3 rollback still needs ephemeral cache.
  ephemeral_storage {
    size_in_gib = var.ephemeral_gb
  }

  # Owned by Terraform (not in container_definitions), so CI's describe→register
  # clones it onto every new image revision.
  volume {
    name = "fse-cache"

    efs_volume_configuration {
      file_system_id     = var.efs_file_system_id
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = var.efs_access_point_id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([
    {
      name      = "fse"
      image     = "${var.ecr_repository_url}:bootstrap"
      essential = true
      command   = ["batch", "--help"]
      # Fargate maximum. On a Spot reclaim it covers fse stopping and the S3
      # uploads; a long pull's cache upload can be cut off (README, Batch commands).
      stopTimeout = 120
      environment = [
        { name = "DATA_BUCKET", value = var.data_bucket_name },
        { name = "FSE_CACHE_BACKEND", value = "efs" },
      ]
      mountPoints = [
        {
          sourceVolume  = "fse-cache"
          containerPath = "/home/fse/.skylit-fse/cache"
          readOnly      = false
        },
      ]
      secrets = [
        for name in var.batch_secret_names : { name = name, valueFrom = var.secret_parameter_arns[name] }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-region        = var.region
          awslogs-group         = local.log_group_name
          awslogs-stream-prefix = "batch"
        }
      }
    },
  ])

  # CI registers a new revision per image (aws ecs register-task-definition).
  # track_latest makes Terraform follow the newest revision instead of
  # reverting it; ignore_changes keeps CI's image and command; skip_destroy
  # keeps old revisions registered so running tasks are never orphaned.
  #
  # replace_triggered_by: ignore_changes would otherwise let an apply add the
  # EFS volume without planting mountPoints/FSE_CACHE_BACKEND inside
  # container_definitions (the field Terraform then refuses to update). Replacing
  # the task definition when the EFS mount identity changes re-plants them.
  # fse-deploy also re-asserts mountPoints on every register (belt and braces).
  track_latest = true
  skip_destroy = true

  lifecycle {
    ignore_changes       = [container_definitions]
    replace_triggered_by = [terraform_data.efs_mount]
  }
}

# Stable id for the EFS mount contract; changing it replaces the task definition
# so mountPoints + FSE_CACHE_BACKEND land despite ignore_changes.
resource "terraform_data" "efs_mount" {
  input = "${var.efs_file_system_id}:${var.efs_access_point_id}:/home/fse/.skylit-fse/cache"
}
