# OS patching with SSM Patch Manager, and daily snapshots of the data volume.

resource "aws_ssm_patch_baseline" "al2023" {
  name             = "${var.name}-al2023"
  description      = "fse runner host: security and bugfix updates, auto-approved after 7 days"
  operating_system = "AMAZON_LINUX_2023"

  approval_rule {
    approve_after_days = 7
    patch_filter {
      key    = "CLASSIFICATION"
      values = ["Security", "Bugfix"]
    }
  }
}

resource "aws_ssm_patch_group" "fse" {
  baseline_id = aws_ssm_patch_baseline.al2023.id
  patch_group = var.patch_group
}

# Saturday 06:00 New York time: futures markets are closed. The reboot (if a
# patch needs one) restarts fse-runner.service, which mails a runner alert.
resource "aws_ssm_maintenance_window" "patch" {
  name              = "${var.name}-patch"
  schedule          = "cron(0 6 ? * SAT *)"
  schedule_timezone = "America/New_York"
  duration          = 3
  cutoff            = 1
}

resource "aws_ssm_maintenance_window_target" "runner" {
  window_id     = aws_ssm_maintenance_window.patch.id
  name          = "${var.name}-patch-group"
  resource_type = "INSTANCE"

  targets {
    key    = "tag:Patch Group"
    values = [var.patch_group]
  }
}

resource "aws_ssm_maintenance_window_task" "patch" {
  window_id       = aws_ssm_maintenance_window.patch.id
  name            = "${var.name}-run-patch-baseline"
  task_type       = "RUN_COMMAND"
  task_arn        = "AWS-RunPatchBaseline"
  priority        = 1
  max_concurrency = "1"
  max_errors      = "1"

  targets {
    key    = "WindowTargetIds"
    values = [aws_ssm_maintenance_window_target.runner.id]
  }

  task_invocation_parameters {
    run_command_parameters {
      parameter {
        name   = "Operation"
        values = ["Install"]
      }
      parameter {
        name   = "RebootOption"
        values = ["RebootIfNeeded"]
      }
    }
  }
}

# Daily snapshot of every volume tagged Snapshot = fse-data, 7 kept.
resource "aws_dlm_lifecycle_policy" "data" {
  description        = "fse runner data volume daily snapshots 7 kept"
  execution_role_arn = aws_iam_role.dlm.arn
  state              = "ENABLED"

  policy_details {
    resource_types = ["VOLUME"]
    target_tags    = { Snapshot = "fse-data" }

    schedule {
      name      = "daily-7"
      copy_tags = true

      create_rule {
        interval      = 24
        interval_unit = "HOURS"
        times         = ["08:00"]
      }

      retain_rule {
        count = 7
      }
    }
  }
}
