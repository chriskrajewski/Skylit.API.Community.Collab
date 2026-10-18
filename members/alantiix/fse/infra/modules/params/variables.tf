variable "name_prefix" {
  description = "Top-level parameter path segment (/fse/...)."
  type        = string
  default     = "fse"
}

variable "secret_names" {
  description = "Environment variable names stored as SecureString under /fse/secrets/."
  type        = list(string)
  default = [
    "SKYLIT_API_KEY",
    "PROJECTX_USERNAME",
    "PROJECTX_API_KEY",
    "PRACTICE_ACCOUNT_ID",
    "COMBINE_ACCOUNT_ID",
    "NOTIFIER_WEBHOOK_URL",
    "NARRATOR_API_KEY",
    "FSE_SECRET_VARS",
  ]
}

variable "plugin_orders_enabled" {
  description = "Plugin-orders master switch: true writes enabled to /fse/control/plugin_orders, false writes disabled."
  type        = bool
  default     = false
}

variable "placeholder_value" {
  description = "Initial value for every secret. Must start with PLACEHOLDER."
  type        = string
  default     = "PLACEHOLDER-set-by-hand"

  validation {
    condition     = startswith(var.placeholder_value, "PLACEHOLDER")
    error_message = "placeholder_value must start with PLACEHOLDER so the entrypoint treats it as blank."
  }
}
