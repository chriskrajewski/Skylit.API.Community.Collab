variable "region" {
  description = "AWS region (awslogs, ARNs)."
  type        = string
}

variable "account_id" {
  description = "AWS account id (ARNs built at plan time)."
  type        = string
}

variable "ecr_repository_url" {
  description = "ECR repository URL. The Terraform-managed revision points at :bootstrap; CI registers real revisions."
  type        = string
}

variable "data_bucket_name" {
  description = "Data bucket holding cache/, runs/ and locks/ (DATA_BUCKET in the task)."
  type        = string
}

variable "data_bucket_arn" {
  description = "Data bucket ARN."
  type        = string
}

variable "secret_parameter_arns" {
  description = "SSM SecureString ARNs keyed by environment variable name (from the params module)."
  type        = map(string)
}

variable "batch_secret_names" {
  description = "Secrets injected into the batch task. pull/backtest/experiment only need Skylit and ProjectX credentials."
  type        = list(string)
  default     = ["SKYLIT_API_KEY", "PROJECTX_USERNAME", "PROJECTX_API_KEY", "FSE_SECRET_VARS"]
}

variable "alerts_topic_arn" {
  description = "SNS topic for stopped-task events."
  type        = string
}

variable "cpu" {
  description = "Fargate CPU units (8192 = 8 vCPU)."
  type        = number
  default     = 8192
}

variable "memory" {
  description = "Fargate memory in MiB."
  type        = number
  default     = 32768
}

variable "ephemeral_gb" {
  description = "Fargate ephemeral storage in GiB."
  type        = number
  default     = 100
}

variable "cluster_name" {
  description = "ECS cluster name."
  type        = string
  default     = "fse"
}

variable "family" {
  description = "Task definition family (scripts/aws-batch.sh runs it)."
  type        = string
  default     = "fse-batch"
}

variable "ssm_prefix" {
  description = "SSM parameter path prefix."
  type        = string
  default     = "/fse"
}

variable "log_retention_days" {
  description = "Retention of the /fse/batch log group."
  type        = number
  default     = 30
}

variable "efs_file_system_id" {
  description = "EFS file system id for the batch Data_Cache mount."
  type        = string
}

variable "efs_file_system_arn" {
  description = "EFS file system ARN (task-role ClientMount/ClientWrite)."
  type        = string
}

variable "efs_access_point_id" {
  description = "EFS access point id (uid/gid 10001, path /cache)."
  type        = string
}

variable "efs_access_point_arn" {
  description = "EFS access point ARN (IAM auth condition)."
  type        = string
}

variable "subnet_ids" {
  description = "Batch subnets (EventBridge S3→EFS reconcile RunTask network)."
  type        = list(string)
}

variable "batch_security_group_id" {
  description = "fse-batch security group id (EventBridge reconcile RunTask network)."
  type        = string
}

variable "efs_reconcile_schedule" {
  description = "EventBridge schedule for S3→EFS cache reconcile (rate or cron)."
  type        = string
  default     = "rate(6 hours)"
}
