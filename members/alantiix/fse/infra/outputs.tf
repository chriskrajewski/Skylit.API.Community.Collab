output "vpc_id" {
  value = module.network.vpc_id
}

output "subnet_ids" {
  description = "Public subnets (FSE_SUBNETS for scripts/aws-batch.sh)."
  value       = module.network.public_subnet_ids
}

output "runner_security_group_id" {
  value = module.network.runner_security_group_id
}

output "batch_security_group_id" {
  description = "FSE_SECURITY_GROUP for scripts/aws-batch.sh."
  value       = module.network.batch_security_group_id
}

output "ecr_repository_url" {
  value = module.ecr.repository_url
}

output "data_bucket" {
  description = "DATA_BUCKET for the batch task and scripts/aws-batch.sh."
  value       = module.data_bucket.bucket_name
}

output "alerts_topic_arn" {
  value = module.alerts.topic_arn
}

output "ssm_parameter_names" {
  value = module.params.parameter_names
}

output "runner_instance_id" {
  description = "Target for aws ssm start-session and the deploy's SendCommand."
  value       = module.runner.instance_id
}

output "runner_public_ip" {
  description = "Elastic IP of the runner host (outbound only)."
  value       = module.runner.public_ip
}

output "batch_cluster_name" {
  description = "FSE_CLUSTER for scripts/aws-batch.sh."
  value       = module.batch.cluster_name
}

output "batch_task_family" {
  description = "FSE_TASK_FAMILY for scripts/aws-batch.sh."
  value       = module.batch.task_family
}

output "batch_log_group" {
  value = module.batch.log_group_name
}

output "efs_file_system_id" {
  description = "EFS file system id for the batch Data_Cache (scripts/aws-seed-efs-cache.sh)."
  value       = module.cache_efs.file_system_id
}

output "efs_access_point_id" {
  description = "EFS access point id (uid/gid 10001, path /cache)."
  value       = module.cache_efs.access_point_id
}

output "runner_user_data_sha" {
  description = "sha256 of the runner cloud-init. enable_ui must not change it (the instance keeps its first-boot user data)."
  value       = module.runner.user_data_sha
}

output "ui_url" {
  description = "Console URL behind CloudFront and Cognito (null when enable_ui is false)."
  value       = var.enable_ui ? module.ui_edge[0].public_url : null
}

output "ui_origin_domain" {
  description = "Origin host name CloudFront connects to (null when enable_ui is false)."
  value       = var.enable_ui ? module.ui_edge[0].origin_domain : null
}

output "cognito_user_pool_id" {
  description = "Cognito user pool of the console (null when enable_ui is false)."
  value       = var.enable_ui ? module.ui_edge[0].user_pool_id : null
}
