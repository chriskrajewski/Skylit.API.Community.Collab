# Plan-only tests with a mocked AWS provider: no credentials, no AWS calls.
# Every run uses command = plan because prevent_destroy on the state bucket
# breaks the destroy that terraform test runs after an apply.

mock_provider "aws" {
  override_data {
    target = data.aws_caller_identity.current
    values = {
      account_id = "123456789012"
    }
  }
}

run "defaults" {
  command = plan

  assert {
    condition     = aws_s3_bucket.state.bucket == "skylit-fse-tfstate-123456789012-us-east-1"
    error_message = "State bucket name must be skylit-fse-tfstate-<account>-us-east-1."
  }

  assert {
    condition     = aws_s3_bucket_versioning.state.versioning_configuration[0].status == "Enabled"
    error_message = "State bucket versioning must be Enabled."
  }

  assert {
    condition     = aws_s3_bucket_ownership_controls.state.rule[0].object_ownership == "BucketOwnerEnforced"
    error_message = "State bucket must use BucketOwnerEnforced."
  }

  assert {
    condition = alltrue([
      aws_s3_bucket_public_access_block.state.block_public_acls,
      aws_s3_bucket_public_access_block.state.block_public_policy,
      aws_s3_bucket_public_access_block.state.ignore_public_acls,
      aws_s3_bucket_public_access_block.state.restrict_public_buckets,
    ])
    error_message = "State bucket must block all public access."
  }

  assert {
    condition     = jsondecode(aws_s3_bucket_policy.state.policy).Statement[0].Condition.Bool["aws:SecureTransport"] == "false"
    error_message = "State bucket policy must deny insecure transport."
  }

  assert {
    condition = (
      jsondecode(aws_iam_role.plan_push.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"]
      == [
        "repo:OWNER/REPO:ref:refs/heads/main",
        "repo:OWNER@0/REPO@0:ref:refs/heads/main",
        "repo:OWNER/REPO:environment:fse-production",
        "repo:OWNER@0/REPO@0:environment:fse-production",
      ]
    )
    error_message = "fse-gha-plan-push must trust exactly the main branch and the fse-production environment."
  }

  assert {
    condition = (
      jsondecode(aws_iam_role.deploy.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"]
      == ["repo:OWNER/REPO:environment:fse-production", "repo:OWNER@0/REPO@0:environment:fse-production"]
    )
    error_message = "fse-gha-deploy must trust only the fse-production environment."
  }

  assert {
    condition = alltrue([
      for p in [aws_iam_role.plan_push.assume_role_policy, aws_iam_role.deploy.assume_role_policy] :
      length(jsondecode(p).Statement) == 1
      && jsondecode(p).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"
      && jsondecode(p).Statement[0].Principal.Federated == "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"
    ])
    error_message = "Trust policies must have one statement, audience sts.amazonaws.com and the GitHub OIDC provider."
  }

  assert {
    condition     = aws_iam_role.plan_push.name == "fse-gha-plan-push" && aws_iam_role.deploy.name == "fse-gha-deploy"
    error_message = "Role names must be fse-gha-plan-push and fse-gha-deploy."
  }

  assert {
    condition     = length(aws_iam_role.apply) == 1 && length(aws_iam_role_policy_attachment.apply_admin) == 1
    error_message = "fse-gha-apply must exist by default (an apply without it destroys the live role)."
  }

  assert {
    condition = contains(
      [for s in jsondecode(aws_iam_role_policy.deploy.policy).Statement : s.Sid if s.Sid == "PassBatchRoles" && s.Condition.StringEquals["iam:PassedToService"] == "ecs-tasks.amazonaws.com"],
      "PassBatchRoles"
    )
    error_message = "Deploy PassRole must be limited to ecs-tasks.amazonaws.com."
  }

  assert {
    condition = [
      for s in jsondecode(aws_iam_role_policy.deploy.policy).Statement : [s.Resource, s.Condition.StringEquals["ecs:CreateAction"]]
      if s.Sid == "TagNewBatchRevisions"
      ] == [[
        "arn:aws:ecs:us-east-1:123456789012:task-definition/fse-batch:*",
        "RegisterTaskDefinition",
    ]]
    error_message = "Deploy may tag only new fse-batch revisions, and only while registering them."
  }

  assert {
    condition = [
      for s in jsondecode(aws_iam_role_policy.plan_push.policy).Statement : s.Resource if s.Sid == "EcrPush"
      ] == [[
        "arn:aws:ecr:us-east-1:123456789012:repository/fse",
        "arn:aws:ecr:us-east-1:123456789012:repository/fse-ui",
        "arn:aws:ecr:us-east-1:123456789012:repository/fse-edge",
    ]]
    error_message = "EcrPush must cover exactly the fse, fse-ui and fse-edge repositories."
  }

  assert {
    condition = alltrue([
      for s in jsondecode(aws_iam_role_policy.plan_push.policy).Statement :
      contains(s.Action, "ecr:GetDownloadUrlForLayer") && contains(s.Action, "ecr:PutImage") if s.Sid == "EcrPush"
    ])
    error_message = "EcrPush must allow ecr:GetDownloadUrlForLayer (the fse-ui build pulls fse:<sha> layers)."
  }
}

run "apply_role_disabled" {
  command = plan

  variables {
    create_apply_role = false
  }

  assert {
    condition     = length(aws_iam_role.apply) == 0 && length(aws_iam_role_policy_attachment.apply_admin) == 0
    error_message = "create_apply_role = false must not create fse-gha-apply."
  }
}

run "apply_role_default" {
  command = plan

  assert {
    condition     = aws_iam_role.apply[0].name == "fse-gha-apply"
    error_message = "The default must create fse-gha-apply."
  }

  assert {
    condition = (
      jsondecode(aws_iam_role.apply[0].assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"]
      == ["repo:OWNER/REPO:environment:fse-production", "repo:OWNER@0/REPO@0:environment:fse-production"]
    )
    error_message = "fse-gha-apply must trust only the fse-production environment."
  }
}
