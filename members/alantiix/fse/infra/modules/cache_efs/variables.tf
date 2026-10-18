variable "name_prefix" {
  description = "Prefix for Name tags and the EFS creation token."
  type        = string
  default     = "fse"
}

variable "subnet_ids" {
  description = "Subnets that receive EFS mount targets (one per AZ used by batch)."
  type        = list(string)
}

variable "efs_security_group_id" {
  description = "Security group on every mount target (NFS from fse-batch only)."
  type        = string
}
