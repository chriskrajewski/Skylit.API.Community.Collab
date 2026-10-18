# Regional EFS Data_Cache for Fargate batch only. Runner/UI stay on EBS.
# Access point path /cache is mounted at /home/fse/.skylit-fse/cache (uid 10001).

resource "aws_efs_file_system" "cache" {
  creation_token   = "${var.name_prefix}-batch-cache"
  encrypted        = true
  throughput_mode  = "elastic"
  performance_mode = "generalPurpose"

  tags = { Name = "${var.name_prefix}-batch-cache" }
}

resource "aws_efs_access_point" "cache" {
  file_system_id = aws_efs_file_system.cache.id

  posix_user {
    uid = 10001
    gid = 10001
  }

  root_directory {
    path = "/cache"
    creation_info {
      owner_uid   = 10001
      owner_gid   = 10001
      permissions = "755"
    }
  }

  tags = { Name = "${var.name_prefix}-batch-cache" }
}

resource "aws_efs_mount_target" "cache" {
  count = length(var.subnet_ids)

  file_system_id  = aws_efs_file_system.cache.id
  subnet_id       = var.subnet_ids[count.index]
  security_groups = [var.efs_security_group_id]
}
