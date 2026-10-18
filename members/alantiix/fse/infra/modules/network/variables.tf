variable "region" {
  description = "AWS region (used for the S3 gateway endpoint service name)."
  type        = string
}

variable "vpc_cidr" {
  description = "VPC CIDR. Two /24 public subnets are carved from it."
  type        = string
  default     = "10.40.0.0/16"
}

variable "ui_origin_prefix_list_id" {
  description = "CloudFront origin-facing prefix list allowed to reach the runner on 443 (enable_ui). null: no ingress."
  type        = string
  default     = null
}

variable "name_prefix" {
  description = "Prefix for Name tags and security group names."
  type        = string
  default     = "fse"
}

variable "existing_vpc_id" {
  description = "Use this existing VPC instead of creating one. null: create the dedicated fse VPC."
  type        = string
  default     = null
}

variable "existing_subnet_ids" {
  description = "With existing_vpc_id: the public subnets to use (routed to an internet gateway, no NAT). The first holds the runner; batch tasks use all of them."
  type        = list(string)
  default     = []

  validation {
    condition     = length(var.existing_subnet_ids) <= 6
    error_message = "existing_subnet_ids takes at most 6 subnets."
  }
}
