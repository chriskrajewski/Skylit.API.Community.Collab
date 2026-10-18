variable "region" {
  description = "Region of the Cognito user pool (the stack region). Only the ACM certificate is pinned to us-east-1."
  type        = string
}

variable "account_id" {
  description = "AWS account id (hashed into the Cognito domain prefix, never shown in it)."
  type        = string
}

variable "domain_name" {
  description = "Public Route 53 zone that holds the console records."
  type        = string
}

variable "ui_domain" {
  description = "Console host name, e.g. fse.example.com (CloudFront alias and certificate name)."
  type        = string
}

variable "origin_domain" {
  description = "Origin host name CloudFront connects to, e.g. origin.fse.example.com (A record to the runner EIP)."
  type        = string
}

variable "public_url" {
  description = "https://<ui_domain>: base of the Cognito callback and logout URLs."
  type        = string
}

variable "origin_ip" {
  description = "Elastic IP of the runner host (the origin)."
  type        = string
}

variable "operator_email" {
  description = "Email of the single Cognito operator user."
  type        = string
}

variable "operator_invite_version" {
  description = "Bumping it recreates the operator user and mails a new invitation."
  type        = number
}

variable "origin_secret_version" {
  description = "Bumping it rotates the origin secret (random_password keeper and value_wo_version)."
  type        = number
}

variable "cognito_deletion_protection" {
  description = "ACTIVE or INACTIVE (apply INACTIVE before removing the pool)."
  type        = string
}
