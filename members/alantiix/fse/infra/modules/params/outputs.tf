output "secret_parameter_arns" {
  description = "Secret parameter ARNs keyed by environment variable name."
  value       = { for k, p in aws_ssm_parameter.secret : k => p.arn }
}

output "image_tag_parameter_name" {
  value = aws_ssm_parameter.image_tag.name
}

output "parameter_names" {
  description = "Every parameter this module manages."
  value = concat(
    [for p in aws_ssm_parameter.secret : p.name],
    [aws_ssm_parameter.image_tag.name, aws_ssm_parameter.plugin_orders.name],
  )
}

output "plugin_orders_param_name" {
  description = "Plugin-orders master switch parameter (read by fse-runner-mode.sh, shown by the console)."
  value       = aws_ssm_parameter.plugin_orders.name
}

output "plugin_orders_value" {
  description = "Value Terraform writes to the switch: enabled or disabled."
  value       = aws_ssm_parameter.plugin_orders.value
}

output "secret_names" {
  description = "Secret names under /fse/secrets/ (the console's exact write set)."
  value       = var.secret_names
}
