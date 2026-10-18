# Role names are fixed: the bootstrap deploy role may only pass
# role/fse-batch-task and role/fse-batch-exec to ecs-tasks.amazonaws.com.

locals {
  ecs_tasks_trust = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "ecs-tasks.amazonaws.com" }
        Action    = "sts:AssumeRole"
        Condition = {
          StringEquals = { "aws:SourceAccount" = var.account_id }
        }
      },
    ]
  })
  data_prefixes = ["cache", "runs", "locks"]
}

# Execution role: ECS pulls the image, writes the log and resolves secrets.
resource "aws_iam_role" "exec" {
  name               = "fse-batch-exec"
  assume_role_policy = local.ecs_tasks_trust
}

resource "aws_iam_role_policy_attachment" "exec" {
  role       = aws_iam_role.exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "exec_secrets" {
  name = "fse-batch-secrets"
  role = aws_iam_role.exec.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # aws/ssm key: SSM decrypts for the caller, no kms:Decrypt grant needed.
        Sid      = "ReadBatchSecrets"
        Effect   = "Allow"
        Action   = "ssm:GetParameters"
        Resource = "arn:aws:ssm:${var.region}:${var.account_id}:parameter${var.ssm_prefix}/secrets/*"
      },
    ]
  })
}

# Task role: what fse inside the container may do (the S3 cache/runs sync + EFS).
resource "aws_iam_role" "task" {
  name               = "fse-batch-task"
  assume_role_policy = local.ecs_tasks_trust
}

resource "aws_iam_role_policy" "task_data" {
  name = "fse-batch-data"
  role = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ListDataPrefixes"
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = var.data_bucket_arn
        Condition = {
          StringLike = { "s3:prefix" = [for p in local.data_prefixes : "${p}/*"] }
        }
      },
      {
        Sid      = "ReadWriteDataPrefixes"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]
        Resource = [for p in local.data_prefixes : "${var.data_bucket_arn}/${p}/*"]
      },
      {
        # Access-point IAM auth; no ClientRootAccess. S3 cache/* stays for
        # FSE_CACHE_BACKEND=s3 rollback and seed-efs-cache (S3→EFS).
        Sid      = "EfsCacheAccessPoint"
        Effect   = "Allow"
        Action   = ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"]
        Resource = var.efs_file_system_arn
        Condition = {
          StringEquals = {
            "elasticfilesystem:AccessPointArn" = var.efs_access_point_arn
          }
        }
      },
    ]
  })
}
