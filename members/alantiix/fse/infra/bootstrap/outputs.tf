output "state_bucket_name" {
  description = "Bucket for the main stack's state. Put it in infra/backend.hcl."
  value       = aws_s3_bucket.state.bucket
}

output "oidc_provider_arn" {
  description = "GitHub Actions OIDC provider."
  value       = aws_iam_openid_connect_provider.github.arn
}

output "plan_push_role_arn" {
  description = "Role for terraform plan and ECR push (github_branch pushes or the github_environment environment)."
  value       = aws_iam_role.plan_push.arn
}

output "deploy_role_arn" {
  description = "Role for deploys (github_environment environment only)."
  value       = aws_iam_role.deploy.arn
}

output "apply_role_arn" {
  description = "Role for terraform apply from CI; null when create_apply_role = false."
  value       = one(aws_iam_role.apply[*].arn)
}
