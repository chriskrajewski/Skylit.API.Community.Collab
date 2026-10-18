variable "region" {
  description = "AWS region (awslogs driver, ARNs)."
  type        = string
}

variable "account_id" {
  description = "AWS account id (ARNs in the instance role policy)."
  type        = string
}

variable "subnet_id" {
  description = "Public subnet the runner host lives in."
  type        = string
}

variable "availability_zone" {
  description = "AZ of subnet_id. The data volume must be in the same AZ as the instance."
  type        = string
}

variable "security_group_id" {
  description = "Runner security group (no ingress, HTTPS egress)."
  type        = string
}

variable "ecr_repository_url" {
  description = "ECR repository URL the host pulls the fse image from."
  type        = string
}

variable "ecr_repository_arn" {
  description = "ECR repository ARN (pull permissions)."
  type        = string
}

variable "alerts_topic_arn" {
  description = "SNS topic for host, alarm and runner exit alerts."
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type (arm64)."
  type        = string
  default     = "t4g.small"
}

variable "root_volume_size_gb" {
  description = "Root volume size (OS, Docker images)."
  type        = number
  default     = 20
}

variable "data_volume_size_gb" {
  description = "Size of the gp3 data volume mounted at /data."
  type        = number
  default     = 30
}

variable "fault_backoff_s" {
  description = "FSE_FAULT_BACKOFF_S passed to the runner container."
  type        = number
  default     = 900
}

variable "ssm_prefix" {
  description = "SSM parameter path prefix (/fse/secrets/*, /fse/image_tag)."
  type        = string
  default     = "/fse"
}

variable "name" {
  description = "Name for the instance, role, log group suffix and alarms. The Role tag uses it too (the deploy role's SendCommand condition matches Role = fse-runner)."
  type        = string
  default     = "fse-runner"
}

variable "patch_group" {
  description = "Patch Group tag value targeted by the maintenance window."
  type        = string
  default     = "fse"
}

variable "log_retention_days" {
  description = "Retention of the /fse/runner log group."
  type        = number
  default     = 30
}
