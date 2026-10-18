variable "account_id" {
  description = "Account allowed as aws:SourceAccount for service publishes."
  type        = string
}

variable "alert_email" {
  description = "Email address subscribed to the topic."
  type        = string
}

variable "topic_name" {
  description = "SNS topic name."
  type        = string
  default     = "fse-alerts"
}
