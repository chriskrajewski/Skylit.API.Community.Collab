# Fargate Spot batch: plan-only with a mocked AWS provider (no credentials,
# no AWS calls).
mock_provider "aws" {
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
}

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

run "batch" {
  command = plan

  assert {
    condition     = output.batch_cluster_name == "fse"
    error_message = "ECS cluster must be named fse."
  }

  assert {
    condition     = output.batch_task_family == "fse-batch"
    error_message = "Task family must be fse-batch (scripts/aws-batch.sh runs it)."
  }

  assert {
    condition     = output.batch_log_group == "/fse/batch"
    error_message = "Batch log group must be /fse/batch."
  }

  assert {
    condition     = module.batch.task_role_name == "fse-batch-task" && module.batch.exec_role_name == "fse-batch-exec"
    error_message = "Role names must match the bootstrap deploy role's PassRole ARNs."
  }

  assert {
    condition     = strcontains(module.batch.event_pattern, "family:fse-batch") && strcontains(module.batch.event_pattern, "STOPPED")
    error_message = "EventBridge rule must match stopped fse-batch tasks."
  }

  assert {
    condition     = strcontains(module.batch.event_pattern, "arn:aws:ecs:us-east-1:123456789012:cluster/fse")
    error_message = "EventBridge rule must be scoped to the fse cluster."
  }

  assert {
    condition     = module.network.security_group_ingress_counts.batch == 0
    error_message = "Batch security group must have no ingress rules."
  }

  assert {
    condition     = module.network.security_group_ingress_counts.efs == 1
    error_message = "EFS security group must allow NFS from fse-batch only."
  }

  assert {
    condition     = length(module.cache_efs.mount_target_ids) == 2
    error_message = "EFS must have one mount target per public subnet."
  }

  assert {
    condition     = module.batch.efs_reconcile_schedule == "rate(6 hours)"
    error_message = "Batch module must schedule S3→EFS reconcile every 6 hours."
  }
}

# The module on its own: task definition and capacity provider details.
run "batch_module" {
  command = plan

  module {
    source = "./modules/batch"
  }

  variables {
    region                  = "us-east-1"
    account_id              = "123456789012"
    ecr_repository_url      = "123456789012.dkr.ecr.us-east-1.amazonaws.com/fse"
    data_bucket_name        = "skylit-fse-data-123456789012-us-east-1"
    data_bucket_arn         = "arn:aws:s3:::skylit-fse-data-123456789012-us-east-1"
    alerts_topic_arn        = "arn:aws:sns:us-east-1:123456789012:fse-alerts"
    efs_file_system_id      = "fs-0123456789abcdef0"
    efs_file_system_arn     = "arn:aws:elasticfilesystem:us-east-1:123456789012:file-system/fs-0123456789abcdef0"
    efs_access_point_id     = "fsap-0123456789abcdef0"
    efs_access_point_arn    = "arn:aws:elasticfilesystem:us-east-1:123456789012:access-point/fsap-0123456789abcdef0"
    subnet_ids              = ["subnet-aaa11111", "subnet-bbb22222"]
    batch_security_group_id = "sg-batch00000000001"
    secret_parameter_arns = {
      SKYLIT_API_KEY    = "arn:aws:ssm:us-east-1:123456789012:parameter/fse/secrets/SKYLIT_API_KEY"
      PROJECTX_USERNAME = "arn:aws:ssm:us-east-1:123456789012:parameter/fse/secrets/PROJECTX_USERNAME"
      PROJECTX_API_KEY  = "arn:aws:ssm:us-east-1:123456789012:parameter/fse/secrets/PROJECTX_API_KEY"
      FSE_SECRET_VARS   = "arn:aws:ssm:us-east-1:123456789012:parameter/fse/secrets/FSE_SECRET_VARS"
    }
  }

  assert {
    condition     = aws_ecs_task_definition.batch.cpu == "8192" && aws_ecs_task_definition.batch.memory == "32768"
    error_message = "Batch task must be 8 vCPU / 32 GB."
  }

  assert {
    condition     = aws_ecs_task_definition.batch.ephemeral_storage[0].size_in_gib == 100
    error_message = "Batch task must have 100 GiB ephemeral storage."
  }

  assert {
    condition     = aws_ecs_task_definition.batch.runtime_platform[0].cpu_architecture == "ARM64" && aws_ecs_task_definition.batch.runtime_platform[0].operating_system_family == "LINUX"
    error_message = "Batch task must run Linux on ARM64 (the image is arm64)."
  }

  assert {
    condition     = aws_ecs_task_definition.batch.requires_compatibilities == toset(["FARGATE"]) && aws_ecs_task_definition.batch.network_mode == "awsvpc"
    error_message = "Batch task must be a Fargate awsvpc task."
  }

  assert {
    condition     = aws_ecs_task_definition.batch.track_latest && aws_ecs_task_definition.batch.skip_destroy
    error_message = "Task definition must track CI's latest revision and keep old ones."
  }

  assert {
    condition     = contains(aws_ecs_cluster_capacity_providers.this.capacity_providers, "FARGATE_SPOT")
    error_message = "Cluster must offer FARGATE_SPOT."
  }

  assert {
    condition     = one(aws_ecs_cluster_capacity_providers.this.default_capacity_provider_strategy).capacity_provider == "FARGATE_SPOT"
    error_message = "Default capacity provider must be FARGATE_SPOT."
  }

  assert {
    condition     = contains(jsondecode(aws_ecs_task_definition.batch.container_definitions)[0].environment, { name = "DATA_BUCKET", value = "skylit-fse-data-123456789012-us-east-1" })
    error_message = "Batch container must get DATA_BUCKET."
  }

  assert {
    condition     = contains(jsondecode(aws_ecs_task_definition.batch.container_definitions)[0].environment, { name = "FSE_CACHE_BACKEND", value = "efs" })
    error_message = "Batch container must default FSE_CACHE_BACKEND=efs."
  }

  assert {
    condition     = jsondecode(aws_ecs_task_definition.batch.container_definitions)[0].mountPoints[0] == { sourceVolume = "fse-cache", containerPath = "/home/fse/.skylit-fse/cache", readOnly = false }
    error_message = "Batch container must mount EFS at /home/fse/.skylit-fse/cache."
  }

  assert {
    condition = length([
      for v in aws_ecs_task_definition.batch.volume : v
      if v.name == "fse-cache" && one(v.efs_volume_configuration).file_system_id == "fs-0123456789abcdef0" && one(v.efs_volume_configuration).transit_encryption == "ENABLED" && one(one(v.efs_volume_configuration).authorization_config).access_point_id == "fsap-0123456789abcdef0" && one(one(v.efs_volume_configuration).authorization_config).iam == "ENABLED"
    ]) == 1
    error_message = "Batch task must mount EFS with transit encryption and IAM auth via the access point."
  }

  assert {
    condition     = length(jsondecode(aws_ecs_task_definition.batch.container_definitions)[0].secrets) == 4
    error_message = "Batch container must get exactly the four batch secrets."
  }

  assert {
    condition     = aws_cloudwatch_event_target.batch_stopped.arn == "arn:aws:sns:us-east-1:123456789012:fse-alerts"
    error_message = "Stopped-task events must go to the alerts topic."
  }

  assert {
    condition     = aws_cloudwatch_event_target.batch_stopped.input_transformer[0].input_paths["exitCode"] == "$.detail.containers[0].exitCode" && aws_cloudwatch_event_target.batch_stopped.input_transformer[0].input_paths["stoppedReason"] == "$.detail.stoppedReason"
    error_message = "Alert must carry the exit code and stop reason."
  }

  assert {
    condition     = aws_cloudwatch_event_rule.efs_cache_reconcile.schedule_expression == "rate(6 hours)"
    error_message = "EFS cache reconcile must run every 6 hours."
  }

  assert {
    condition     = strcontains(aws_cloudwatch_event_target.efs_cache_reconcile.input, "seed-efs-cache") && aws_cloudwatch_event_target.efs_cache_reconcile.ecs_target[0].task_definition_arn == "arn:aws:ecs:us-east-1:123456789012:task-definition/fse-batch"
    error_message = "EFS reconcile must RunTask seed-efs-cache on the fse-batch family (latest ACTIVE)."
  }

  assert {
    condition     = one(aws_cloudwatch_event_target.efs_cache_reconcile.ecs_target[0].capacity_provider_strategy).capacity_provider == "FARGATE"
    error_message = "EFS reconcile must use Fargate On-Demand (not Spot) for long S3→EFS syncs."
  }

  assert {
    condition     = strcontains(aws_iam_role_policy.task_data.policy, "elasticfilesystem:ClientMount") && strcontains(aws_iam_role_policy.task_data.policy, "elasticfilesystem:AccessPointArn")
    error_message = "Task role must allow EFS ClientMount/ClientWrite conditioned on the access point."
  }

  assert {
    condition     = terraform_data.efs_mount.input == "fs-0123456789abcdef0:fsap-0123456789abcdef0:/home/fse/.skylit-fse/cache"
    error_message = "efs_mount trigger must track FS id, AP id and container mount path."
  }
}
