data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  # Same name fse export-aws derives (fse.awsexport.options.default_bucket).
  data_bucket_name = "skylit-fse-data-${local.account_id}-${var.region}"

  ui_domain     = "${var.ui_subdomain}.${var.domain_name}"
  origin_domain = "origin.${local.ui_domain}"
  public_url    = "https://${local.ui_domain}"

  # With the UI on, the host also runs fse-ui and fse-edge (t4g.medium memory budget).
  effective_runner_type = var.enable_ui ? coalesce(var.ui_runner_instance_type, "t4g.medium") : var.runner_instance_type
}

# CloudFront origin-facing servers: the only source allowed into the runner SG.
data "aws_ec2_managed_prefix_list" "cloudfront" {
  count = var.enable_ui ? 1 : 0

  name = "com.amazonaws.global.cloudfront.origin-facing"
}

# The prefix list counts as about 55 rules against "Inbound or outbound rules
# per security group".
data "aws_servicequotas_service_quota" "sg_rules" {
  count = var.enable_ui ? 1 : 0

  service_code = "vpc"
  quota_code   = "L-0EA8095F"
}

resource "terraform_data" "sg_rules_quota_guard" {
  count = var.enable_ui ? 1 : 0

  # A failed precondition stops the plan, so nothing is created.
  lifecycle {
    precondition {
      condition     = data.aws_servicequotas_service_quota.sg_rules[0].value >= 60
      error_message = "The VPC quota L-0EA8095F (inbound or outbound rules per security group) must be at least 60: the CloudFront prefix list counts as about 55 rules. Request an increase in Service Quotas before setting enable_ui = true."
    }
  }
}

module "network" {
  source = "./modules/network"

  region                   = var.region
  ui_origin_prefix_list_id = var.enable_ui ? data.aws_ec2_managed_prefix_list.cloudfront[0].id : null
  existing_vpc_id          = var.existing_vpc_id
  existing_subnet_ids      = var.existing_subnet_ids
}

module "ecr" {
  source = "./modules/ecr"
}

module "params" {
  source = "./modules/params"

  plugin_orders_enabled = var.plugin_orders_enabled
}

module "data_bucket" {
  source = "./modules/data_bucket"

  bucket_name = local.data_bucket_name
}

module "alerts" {
  source = "./modules/alerts"

  account_id  = local.account_id
  alert_email = var.alert_email
}

module "runner" {
  source = "./modules/runner"

  region              = var.region
  account_id          = local.account_id
  subnet_id           = module.network.public_subnet_ids[0]
  availability_zone   = module.network.public_subnet_azs[0]
  security_group_id   = module.network.runner_security_group_id
  ecr_repository_url  = module.ecr.repository_url
  ecr_repository_arn  = module.ecr.repository_arn
  alerts_topic_arn    = module.alerts.topic_arn
  instance_type       = local.effective_runner_type
  data_volume_size_gb = var.data_volume_size_gb
  fault_backoff_s     = var.fault_backoff_s
}

module "cache_efs" {
  source = "./modules/cache_efs"

  subnet_ids            = module.network.public_subnet_ids
  efs_security_group_id = module.network.efs_security_group_id
}

module "batch" {
  source = "./modules/batch"

  region                  = var.region
  account_id              = local.account_id
  ecr_repository_url      = module.ecr.repository_url
  data_bucket_name        = module.data_bucket.bucket_name
  data_bucket_arn         = module.data_bucket.bucket_arn
  secret_parameter_arns   = module.params.secret_parameter_arns
  alerts_topic_arn        = module.alerts.topic_arn
  cpu                     = var.batch_cpu
  memory                  = var.batch_memory
  ephemeral_gb            = var.batch_ephemeral_gb
  efs_file_system_id      = module.cache_efs.file_system_id
  efs_file_system_arn     = module.cache_efs.file_system_arn
  efs_access_point_id     = module.cache_efs.access_point_id
  efs_access_point_arn    = module.cache_efs.access_point_arn
  subnet_ids              = module.network.public_subnet_ids
  batch_security_group_id = module.network.batch_security_group_id
}

module "ui_edge" {
  source = "./modules/ui_edge"
  count  = var.enable_ui ? 1 : 0

  providers = {
    aws           = aws
    aws.us_east_1 = aws.us_east_1
  }

  region                      = var.region
  account_id                  = local.account_id
  domain_name                 = var.domain_name
  ui_domain                   = local.ui_domain
  origin_domain               = local.origin_domain
  public_url                  = local.public_url
  origin_ip                   = module.runner.public_ip
  operator_email              = var.operator_email
  operator_invite_version     = var.operator_invite_version
  origin_secret_version       = var.origin_secret_version
  cognito_deletion_protection = var.ui_cognito_deletion_protection
}

# Console images (CI pushes fse-ui:<sha> and fse-edge:<sha> next to fse:<sha>).
module "ecr_ui" {
  source = "./modules/ecr"
  count  = var.enable_ui ? 1 : 0

  name = "fse-ui"
}

module "ecr_edge" {
  source = "./modules/ecr"
  count  = var.enable_ui ? 1 : 0

  name = "fse-edge"
}

# Console units, host scripts, IAM and parameters on the runner host.
module "ui_host" {
  source = "./modules/ui_host"
  count  = var.enable_ui ? 1 : 0

  instance_id           = module.runner.instance_id
  region                = var.region
  account_id            = local.account_id
  public_url            = module.ui_edge[0].public_url
  origin_domain         = module.ui_edge[0].origin_domain
  zone_id               = module.ui_edge[0].zone_id
  oidc                  = module.ui_edge[0].oidc
  origin_secret_version = module.ui_edge[0].origin_secret_version
  cognito_user_pool_id  = module.ui_edge[0].user_pool_id
  cognito_user_pool_arn = module.ui_edge[0].user_pool_arn

  data_bucket_name        = module.data_bucket.bucket_name
  data_bucket_arn         = module.data_bucket.bucket_arn
  cluster_name            = module.batch.cluster_name
  cluster_arn             = module.batch.cluster_arn
  task_family             = module.batch.task_family
  subnet_ids              = module.network.public_subnet_ids
  batch_security_group_id = module.network.batch_security_group_id
  batch_log_group         = module.batch.log_group_name
  runner_log_group        = module.runner.log_group_name
  runner_role_name        = module.runner.role_name
  batch_task_role_name    = module.batch.task_role_name
  # Built from the fixed names, so the policy is known at plan time.
  batch_task_role_arn = "arn:aws:iam::${local.account_id}:role/${module.batch.task_role_name}"
  batch_exec_role_arn = "arn:aws:iam::${local.account_id}:role/${module.batch.exec_role_name}"
  alarm_names         = module.runner.alarm_names

  ui_repository_url   = module.ecr_ui[0].repository_url
  ui_repository_arn   = module.ecr_ui[0].repository_arn
  edge_repository_url = module.ecr_edge[0].repository_url
  edge_repository_arn = module.ecr_edge[0].repository_arn

  # Exact secret ARNs for WriteParameters (no /secrets/* wildcard) and the
  # plugin-orders switch the console shows (it can never write it).
  secret_names              = module.params.secret_names
  plugin_orders_enabled     = var.plugin_orders_enabled
  plugin_orders_param_name  = module.params.plugin_orders_param_name
  topstep_terms_verified_on = var.topstep_terms_verified_on
}
