# GitHub Actions OIDC provider and the CI roles.
#
# Trust and permission policies use jsonencode() instead of policy data sources
# so `terraform test` can assert on them at plan time.

locals {
  oidc_host = "token.actions.githubusercontent.com"
  # Built from the account id (known at plan time). depends_on below keeps the
  # roles after the provider.
  oidc_provider_arn = "arn:aws:iam::${local.account_id}:oidc-provider/${local.oidc_host}"

  # GitHub sends one of two subject formats: the legacy "repo:<owner>/<name>" and,
  # when the repository uses immutable subjects (its OIDC sub customization has
  # use_immutable_subject = true), "repo:<owner>@<owner id>/<name>@<repo id>".
  # Trust both, so the roles keep working if the setting changes either way.
  sub_prefixes = compact([
    "repo:${var.github_repository}",
    var.github_immutable_subject_prefix,
  ])
  sub_branch      = [for p in local.sub_prefixes : "${p}:ref:refs/heads/${var.github_branch}"]
  sub_environment = [for p in local.sub_prefixes : "${p}:environment:${var.github_environment}"]
}

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://${local.oidc_host}"
  client_id_list = ["sts.amazonaws.com"]
  # thumbprint_list is optional in provider v6: IAM verifies GitHub's
  # certificate against its own trusted CAs.
}

# ---------------------------------------------------------------------------
# fse-gha-plan-push: terraform plan (read-only) and ECR image push.
# Trusted from github_branch pushes and the github_environment environment.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "plan_push" {
  name        = "${var.name_prefix}-gha-plan-push"
  description = "GitHub Actions: terraform plan and ECR push for ${var.github_repository}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Federated = local.oidc_provider_arn }
        Action    = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "${local.oidc_host}:aud" = "sts.amazonaws.com"
            "${local.oidc_host}:sub" = concat(local.sub_branch, local.sub_environment)
          }
        }
      },
    ]
  })

  depends_on = [aws_iam_openid_connect_provider.github]
}

resource "aws_iam_role_policy_attachment" "plan_push_readonly" {
  role       = aws_iam_role.plan_push.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

resource "aws_iam_role_policy" "plan_push" {
  name = "state-and-ecr-push"
  role = aws_iam_role.plan_push.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "StateList"
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = local.state_bucket_arn
      },
      {
        Sid      = "StateRead"
        Effect   = "Allow"
        Action   = "s3:GetObject"
        Resource = "${local.state_bucket_arn}/infra/*"
      },
      {
        # terraform plan takes the S3-native lock (use_lockfile) but never
        # writes state.
        Sid      = "StateLock"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:DeleteObject"]
        Resource = "${local.state_bucket_arn}/infra/*.tflock"
      },
      {
        Sid      = "EcrAuth"
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*"
      },
      {
        Sid    = "EcrPush"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer", # fse-ui builds FROM fse:<sha> pulled from ECR
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:PutImage",
          "ecr:BatchGetImage",
          "ecr:DescribeImages",
        ]
        Resource = [for n in var.ecr_repository_names : "arn:aws:ecr:${var.region}:${local.account_id}:repository/${n}"]
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# fse-gha-deploy: roll a pushed image out to the runner and the batch task.
# Trusted from the github_environment environment only.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "deploy" {
  name        = "${var.name_prefix}-gha-deploy"
  description = "GitHub Actions: deploy fse images for ${var.github_repository} (${var.github_environment} environment)"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Federated = local.oidc_provider_arn }
        Action    = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "${local.oidc_host}:aud" = "sts.amazonaws.com"
            "${local.oidc_host}:sub" = local.sub_environment
          }
        }
      },
    ]
  })

  depends_on = [aws_iam_openid_connect_provider.github]
}

resource "aws_iam_role_policy" "deploy" {
  name = "deploy"
  role = aws_iam_role.deploy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ImageTag"
        Effect   = "Allow"
        Action   = ["ssm:PutParameter", "ssm:GetParameter"]
        Resource = "arn:aws:ssm:${var.region}:${local.account_id}:parameter/${var.name_prefix}/image_tag"
      },
      {
        # AWS-owned documents have no account id in their ARN.
        Sid      = "RunShellScriptDocument"
        Effect   = "Allow"
        Action   = "ssm:SendCommand"
        Resource = "arn:aws:ssm:${var.region}::document/AWS-RunShellScript"
      },
      {
        Sid      = "RunnerInstanceOnly"
        Effect   = "Allow"
        Action   = "ssm:SendCommand"
        Resource = "arn:aws:ec2:${var.region}:${local.account_id}:instance/*"
        Condition = {
          StringEquals = { "ssm:resourceTag/Role" = "${var.name_prefix}-runner" }
        }
      },
      {
        # Not resource-scopable.
        Sid    = "CommandStatus"
        Effect = "Allow"
        Action = [
          "ssm:GetCommandInvocation",
          "ssm:ListCommandInvocations",
          "ec2:DescribeInstances",
        ]
        Resource = "*"
      },
      {
        # Not resource-scopable.
        Sid      = "TaskDefinitions"
        Effect   = "Allow"
        Action   = ["ecs:RegisterTaskDefinition", "ecs:DescribeTaskDefinition"]
        Resource = "*"
      },
      {
        # The deploy copies Terraform's tags onto each new revision (tag on create).
        Sid      = "TagNewBatchRevisions"
        Effect   = "Allow"
        Action   = "ecs:TagResource"
        Resource = "arn:aws:ecs:${var.region}:${local.account_id}:task-definition/${var.name_prefix}-batch:*"
        Condition = {
          StringEquals = { "ecs:CreateAction" = "RegisterTaskDefinition" }
        }
      },
      {
        Sid    = "PassBatchRoles"
        Effect = "Allow"
        Action = "iam:PassRole"
        Resource = [
          "arn:aws:iam::${local.account_id}:role/${var.name_prefix}-batch-task",
          "arn:aws:iam::${local.account_id}:role/${var.name_prefix}-batch-exec",
        ]
        Condition = {
          StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" }
        }
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# fse-gha-apply (on by default): terraform apply from CI.
# AdministratorAccess, so it trusts the github_environment environment only.
# create_apply_role = false removes it; then apply locally.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "apply" {
  count = var.create_apply_role ? 1 : 0

  name        = "${var.name_prefix}-gha-apply"
  description = "GitHub Actions: terraform apply for ${var.github_repository} (${var.github_environment} environment)"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Federated = local.oidc_provider_arn }
        Action    = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "${local.oidc_host}:aud" = "sts.amazonaws.com"
            "${local.oidc_host}:sub" = local.sub_environment
          }
        }
      },
    ]
  })

  depends_on = [aws_iam_openid_connect_provider.github]
}

resource "aws_iam_role_policy_attachment" "apply_admin" {
  count = var.create_apply_role ? 1 : 0

  role       = aws_iam_role.apply[0].name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}
