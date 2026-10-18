resource "aws_iam_role" "runner" {
  name = var.name
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "ec2.amazonaws.com" }
        Action    = "sts:AssumeRole"
      },
    ]
  })
}

# Session Manager shell, Run Command (deploys) and Patch Manager.
resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.runner.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

locals {
  log_group_arn = "arn:aws:logs:${var.region}:${var.account_id}:log-group:${local.log_group_name}"
}

resource "aws_iam_role_policy" "runner" {
  name = "${var.name}-host"
  role = aws_iam_role.runner.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "EcrAuth"
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*"
      },
      {
        Sid      = "EcrPull"
        Effect   = "Allow"
        Action   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"]
        Resource = var.ecr_repository_arn
      },
      {
        # SecureStrings use the aws/ssm key, which SSM decrypts for any caller
        # allowed to read the parameter: no kms:Decrypt grant needed.
        Sid      = "ReadFseParameters"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
        Resource = "arn:aws:ssm:${var.region}:${var.account_id}:parameter${var.ssm_prefix}/*"
      },
      {
        # dockerd's awslogs driver ships the container log.
        Sid      = "RunnerLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
        Resource = [local.log_group_arn, "${local.log_group_arn}:*"]
      },
      {
        Sid      = "Alerts"
        Effect   = "Allow"
        Action   = "sns:Publish"
        Resource = var.alerts_topic_arn
      },
    ]
  })
}

resource "aws_iam_instance_profile" "runner" {
  name = var.name
  role = aws_iam_role.runner.name
}

# Data Lifecycle Manager snapshots the data volume (patching.tf has the policy).
resource "aws_iam_role" "dlm" {
  name = "${var.name}-dlm"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "dlm.amazonaws.com" }
        Action    = "sts:AssumeRole"
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "dlm" {
  role       = aws_iam_role.dlm.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSDataLifecycleManagerServiceRole"
}
