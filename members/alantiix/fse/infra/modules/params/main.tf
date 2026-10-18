# SSM parameters the runner host and batch task read. Terraform creates them
# with placeholder values and then leaves the value alone: set the real
# secrets by hand (aws ssm put-parameter --overwrite) and CI sets image_tag.
# A value starting with PLACEHOLDER is treated as blank by the entrypoint, so
# fse exits 3 (blank credential) until the real value is set.
#
# The secrets use the write-only value_wo (Terraform >= 1.11): Terraform sends
# the placeholder once (value_wo_version 1) and never stores a value, so the
# hand-set secrets stay out of the state file. Bumping value_wo_version would
# overwrite every secret with the placeholder again.
#
# SecureString uses the aws/ssm default KMS key; no kms grant is needed for it.

resource "aws_ssm_parameter" "secret" {
  for_each = toset(var.secret_names)

  name             = "/${var.name_prefix}/secrets/${each.key}"
  description      = "fse secret ${each.key}; value set by hand"
  type             = "SecureString"
  value_wo         = var.placeholder_value
  value_wo_version = 1
}

resource "aws_ssm_parameter" "image_tag" {
  name        = "/${var.name_prefix}/image_tag"
  description = "fse image tag the runner and batch task run; set by CI. none = not deployed yet."
  type        = "String"
  value       = "none"

  lifecycle {
    ignore_changes = [value]
  }
}

# Plugin-orders master switch (plugin_orders_enabled). Terraform owns the
# value (no ignore_changes): an apply resets any hand edit. No console or
# instance-role policy can write /control/*. fse-runner-mode.sh copies it to
# the read-only /run/fse-control/plugin_orders mount the runner image reads.
resource "aws_ssm_parameter" "plugin_orders" {
  name        = "/${var.name_prefix}/control/plugin_orders"
  description = "fse plugin-orders master switch (enabled or disabled); set by Terraform only."
  type        = "String"
  value       = var.plugin_orders_enabled ? "enabled" : "disabled"
}
