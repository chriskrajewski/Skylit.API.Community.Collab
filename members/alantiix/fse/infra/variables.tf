variable "region" {
  description = "AWS region. The data bucket fse export-aws created lives here."
  type        = string
  default     = "us-east-1"
}

variable "alert_email" {
  description = "Address subscribed to the fse-alerts SNS topic. AWS mails a confirmation link once."
  type        = string

  validation {
    condition     = can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.alert_email))
    error_message = "alert_email must be an email address."
  }
}

variable "enable_ui" {
  description = "Put the fse console on the runner host behind CloudFront and Cognito (modules/ui_edge, modules/ui_host, fse-ui/fse-edge ECR). With true the host uses ui_runner_instance_type and runner_instance_type is ignored. Turning it off: run the fse-ui-host-remove document first."
  type        = bool
  default     = false
}

variable "plugin_orders_enabled" {
  description = "Plugin-orders master switch (/fse/control/plugin_orders). true lets the console switch a broker_orders plugin to Practice or Combine orders (the runner then passes --plugin-orders and --live-orders); false keeps every plugin on paper. Only Terraform writes it. Needs enable_ui = true and topstep_terms_verified_on."
  type        = bool
  default     = false

  validation {
    condition     = !var.plugin_orders_enabled || var.enable_ui
    error_message = "plugin_orders_enabled = true needs enable_ui = true (the switch is used from the console)."
  }

  validation {
    condition     = !var.plugin_orders_enabled || var.topstep_terms_verified_on != ""
    error_message = "plugin_orders_enabled = true needs topstep_terms_verified_on (YYYY-MM-DD, the day you checked the Topstep automation terms)."
  }
}

variable "topstep_terms_verified_on" {
  description = "Date (YYYY-MM-DD) you last checked that Topstep's terms allow automated orders on the practice account. Required with plugin_orders_enabled = true; shown on the console. Defaults to the account owner's verification (2026-10-13, docs/account-rules.md); the repo variable FSE_TOPSTEP_TERMS_VERIFIED_ON overrides it. Enables nothing by itself."
  type        = string
  default     = "2026-10-13"

  validation {
    condition     = var.topstep_terms_verified_on == "" || can(regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}$", var.topstep_terms_verified_on))
    error_message = "topstep_terms_verified_on must be empty or a date like 2026-01-31."
  }
}

variable "domain_name" {
  description = "Public Route 53 zone (in this account) that holds the console records."
  type        = string
  default     = "example.com"
}

variable "ui_subdomain" {
  description = "Console host name under domain_name: https://<ui_subdomain>.<domain_name>."
  type        = string
  default     = "fse"
}

variable "ui_cognito_deletion_protection" {
  description = "Cognito user pool deletion protection. Apply INACTIVE first before turning enable_ui off."
  type        = string
  default     = "ACTIVE"

  validation {
    condition     = contains(["ACTIVE", "INACTIVE"], var.ui_cognito_deletion_protection)
    error_message = "ui_cognito_deletion_protection must be ACTIVE or INACTIVE."
  }
}

variable "operator_invite_version" {
  description = "Bump to recreate the Cognito operator user and mail a new invitation (drops its TOTP enrollment)."
  type        = number
  default     = 1

  validation {
    condition     = var.operator_invite_version >= 1
    error_message = "operator_invite_version must be at least 1."
  }
}

variable "operator_email" {
  description = "Email of the single Cognito operator user. Required when enable_ui is true."
  type        = string
  default     = null

  validation {
    condition     = !var.enable_ui || (var.operator_email != null && can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.operator_email)))
    error_message = "operator_email must be an email address when enable_ui is true."
  }
}

variable "origin_secret_version" {
  description = "Bump to rotate the CloudFront-to-origin secret (/fse/ui/origin_secret)."
  type        = number
  default     = 1

  validation {
    condition     = var.origin_secret_version >= 1
    error_message = "origin_secret_version must be at least 1."
  }
}

variable "ui_runner_instance_type" {
  description = "EC2 instance type for the runner host when enable_ui is true (t4g.medium when unset); runner_instance_type is then ignored. If you raised runner_instance_type, set this to the same size."
  type        = string
  default     = null
}

variable "existing_vpc_id" {
  description = "Put fse in this existing VPC instead of creating the dedicated one. Its subnets (existing_subnet_ids) must route 0.0.0.0/0 to an internet gateway; no NAT is used. Only the fse-runner and fse-batch security groups are created in it."
  type        = string
  default     = null

  validation {
    condition     = var.existing_vpc_id == null || can(regex("^vpc-[0-9a-f]{8,17}$", var.existing_vpc_id))
    error_message = "existing_vpc_id must look like vpc-0123456789abcdef0."
  }
}

variable "existing_subnet_ids" {
  description = "With existing_vpc_id: 1 to 6 public subnets of that VPC. The first holds the runner host and its data volume; Fargate batch tasks may use any of them. Two subnets in different AZs give Fargate Spot more capacity."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for s in var.existing_subnet_ids : can(regex("^subnet-[0-9a-f]{8,17}$", s))])
    error_message = "existing_subnet_ids entries must look like subnet-0123456789abcdef0."
  }

  validation {
    condition     = (var.existing_vpc_id == null) == (length(var.existing_subnet_ids) == 0) && length(var.existing_subnet_ids) <= 6
    error_message = "Set existing_vpc_id and 1 to 6 existing_subnet_ids together, or neither."
  }
}

variable "adopt_existing_data_bucket" {
  description = "Adopt the data bucket fse export-aws created (import block in imports.tf). Set false only in an account where that bucket does not exist yet."
  type        = bool
  default     = true
}

variable "runner_instance_type" {
  description = "EC2 instance type for the live runner host (arm64). With enable_ui = true the host uses ui_runner_instance_type (t4g.medium when unset) and this is ignored."
  type        = string
  default     = "t4g.small"
}

variable "data_volume_size_gb" {
  description = "Size of the runner's encrypted gp3 data volume mounted at /data."
  type        = number
  default     = 30
}

variable "batch_cpu" {
  description = "Fargate CPU units for the batch task (8192 = 8 vCPU)."
  type        = number
  default     = 8192
}

variable "batch_memory" {
  description = "Fargate memory (MiB) for the batch task."
  type        = number
  default     = 32768
}

variable "batch_ephemeral_gb" {
  description = "Fargate ephemeral storage (GiB) for the batch task."
  type        = number
  default     = 100
}

variable "fault_backoff_s" {
  description = "Seconds the runner entrypoint sleeps after fse exits 2 or 3, before systemd restarts it."
  type        = number
  default     = 900
}
