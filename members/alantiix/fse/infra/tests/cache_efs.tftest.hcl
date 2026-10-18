# Plan-only: cache_efs module + network NFS rules for batch-only EFS.
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

mock_provider "aws" {
  alias = "us_east_1"
}

mock_provider "random" {}

variables {
  alert_email                = "ops@example.com"
  adopt_existing_data_bucket = false
}

run "cache_efs_root" {
  command = plan

  assert {
    condition     = length(module.cache_efs.mount_target_ids) == 2
    error_message = "EFS must have one mount target per public subnet (2 AZs)."
  }

  assert {
    condition     = module.network.security_group_ingress_counts.efs == 1
    error_message = "Network must create fse-efs SG with NFS ingress from fse-batch."
  }

  assert {
    condition     = module.batch.efs_reconcile_schedule == "rate(6 hours)" && module.batch.events_role_name == "fse-batch-events"
    error_message = "Root batch wiring must schedule S3→EFS reconcile via fse-batch-events."
  }
}

run "cache_efs_module" {
  command = plan

  module {
    source = "./modules/cache_efs"
  }

  variables {
    subnet_ids            = ["subnet-aaa11111", "subnet-bbb22222"]
    efs_security_group_id = "sg-efs0000000000001"
  }

  assert {
    condition     = aws_efs_file_system.cache.throughput_mode == "elastic" && aws_efs_file_system.cache.performance_mode == "generalPurpose" && aws_efs_file_system.cache.encrypted
    error_message = "EFS must be encrypted Regional Elastic / General Purpose."
  }

  assert {
    condition     = aws_efs_access_point.cache.posix_user[0].uid == 10001 && aws_efs_access_point.cache.posix_user[0].gid == 10001 && aws_efs_access_point.cache.root_directory[0].path == "/cache"
    error_message = "Access point must be uid/gid 10001 with path /cache."
  }

  assert {
    condition     = length(aws_efs_mount_target.cache) == 2
    error_message = "Mount targets must match subnet count."
  }
}
