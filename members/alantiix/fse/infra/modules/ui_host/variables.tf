# Every domain, region and endpoint comes in from the root (locals, ui_edge
# and the Phase 1 modules): this module holds no domain or region literal.

variable "instance_id" {
  description = "Runner host instance id (association target)."
  type        = string
}

variable "region" {
  description = "AWS region (ARNs, FSE_UI_AWS_REGION, FSE_EDGE_AWS_REGION)."
  type        = string
}

variable "account_id" {
  description = "AWS account id (ARNs built at plan time)."
  type        = string
}

variable "public_url" {
  description = "Console URL (FSE_UI_PUBLIC_URL), https://<ui_subdomain>.<domain_name>."
  type        = string
}

variable "origin_domain" {
  description = "Origin host name Caddy serves and gets a certificate for (FSE_EDGE_SITE)."
  type        = string
}

variable "zone_id" {
  description = "Route 53 zone holding origin_domain (Caddy's DNS-01 record)."
  type        = string
}

variable "oidc" {
  description = "OIDC endpoints and client id from ui_edge (never the client secret)."
  type = object({
    issuer        = string
    client_id     = string
    authorize_url = string
    token_url     = string
    logout_url    = string
    jwks_url      = string
  })
}

variable "origin_secret_version" {
  description = "Origin secret version from ui_edge. Written to ui.env as FSE_EDGE_SECRET_VERSION so a rotation changes the bundle and re-runs the association."
  type        = number
}

variable "cognito_user_pool_id" {
  description = "Cognito user pool id (the host script reads the app client secret)."
  type        = string
}

variable "cognito_user_pool_arn" {
  description = "Cognito user pool ARN (cognito-idp:DescribeUserPoolClient scope)."
  type        = string
}

variable "data_bucket_name" {
  description = "Data bucket (FSE_UI_DATA_BUCKET)."
  type        = string
}

variable "data_bucket_arn" {
  description = "Data bucket ARN."
  type        = string
}

variable "cluster_name" {
  description = "ECS cluster of the batch task (FSE_UI_ECS_CLUSTER)."
  type        = string
}

variable "cluster_arn" {
  description = "ECS cluster ARN (ecs:cluster condition)."
  type        = string
}

variable "task_family" {
  description = "Batch task definition family (FSE_UI_ECS_TASK_FAMILY)."
  type        = string
}

variable "subnet_ids" {
  description = "Subnets for the batch task (FSE_UI_ECS_SUBNETS)."
  type        = list(string)
}

variable "batch_security_group_id" {
  description = "Batch task security group (FSE_UI_ECS_SECURITY_GROUP)."
  type        = string
}

variable "batch_log_group" {
  description = "Batch task log group (FSE_UI_BATCH_LOG_GROUP)."
  type        = string
}

variable "runner_log_group" {
  description = "Runner container log group (FSE_UI_RUNNER_LOG_GROUP)."
  type        = string
}

variable "runner_role_name" {
  description = "Runner instance role. It is also the instance's Role tag (the SendCommand condition) and the alarm name prefix."
  type        = string
}

variable "batch_task_role_name" {
  description = "Batch task role (gets the batch_console policy)."
  type        = string
}

variable "batch_task_role_arn" {
  description = "Batch task role ARN (iam:PassRole for RunTask)."
  type        = string
}

variable "batch_exec_role_arn" {
  description = "Batch execution role ARN (iam:PassRole for RunTask)."
  type        = string
}

variable "alarm_names" {
  description = "Runner alarms shown on the Health page (FSE_UI_ALARM_NAMES)."
  type        = list(string)
}

variable "ui_repository_url" {
  description = "fse-ui ECR repository URL (FSE_UI_IMAGE_REPO)."
  type        = string
}

variable "ui_repository_arn" {
  description = "fse-ui ECR repository ARN (pull permissions)."
  type        = string
}

variable "edge_repository_url" {
  description = "fse-edge ECR repository URL (FSE_EDGE_IMAGE_REPO)."
  type        = string
}

variable "edge_repository_arn" {
  description = "fse-edge ECR repository ARN (pull permissions)."
  type        = string
}

variable "secret_names" {
  description = "Secret names under <ssm_prefix>/secrets/ (modules/params): the console may write exactly these ARNs."
  type        = list(string)
}

variable "plugin_orders_enabled" {
  description = "Plugin-orders master switch value (FSE_UI_PLUGIN_ORDERS=enabled|disabled). The console only shows it."
  type        = bool
  default     = false
}

variable "plugin_orders_param_name" {
  description = "Switch parameter name (FSE_UI_PLUGIN_ORDERS_PARAM), from modules/params."
  type        = string
}

variable "topstep_terms_verified_on" {
  description = "Date the Topstep automation terms were checked (FSE_UI_TOPSTEP_VERIFIED_ON), or empty."
  type        = string
  default     = ""
}

variable "ssm_prefix" {
  description = "SSM parameter path prefix (/fse/image_tag, /fse/runner_mode, /fse/secrets/*)."
  type        = string
  default     = "/fse"
}

variable "log_retention_days" {
  description = "Retention of the /fse/ui log group."
  type        = number
  default     = 30
}
