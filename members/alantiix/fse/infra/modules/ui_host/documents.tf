# SSM Command documents. The console controls the runner through
# fse-runner-control only (never the AWS shell document); the bundle installs the
# console on the host through an association; the remove document is the
# off-switch the operator runs once from Run Command.

locals {
  control_document_name = "fse-runner-control"
  bundle_document_name  = "fse-ui-host-bundle"
  remove_document_name  = "fse-ui-host-remove"
  ui_log_group_name     = "/fse/ui"

  # /etc/fse/ui.env (0644, non-secret). The UI container and the host units
  # both read it; the host-only keys are ignored by the console.
  ui_env = join("\n", [
    "FSE_UI_ENV=aws",
    "FSE_UI_PUBLIC_URL=${var.public_url}",
    "FSE_UI_OIDC_ISSUER=${var.oidc.issuer}",
    "FSE_UI_OIDC_CLIENT_ID=${var.oidc.client_id}",
    "FSE_UI_OIDC_AUTHORIZE_URL=${var.oidc.authorize_url}",
    "FSE_UI_OIDC_TOKEN_URL=${var.oidc.token_url}",
    "FSE_UI_OIDC_JWKS_URL=${var.oidc.jwks_url}",
    "FSE_UI_OIDC_LOGOUT_URL=${var.oidc.logout_url}",
    "FSE_UI_SECRET_STORE=ssm",
    "FSE_UI_EXECUTOR=ecs",
    "FSE_UI_RUNNER_CONTROLLER=ssm",
    "FSE_UI_DATA_BUCKET=${var.data_bucket_name}",
    "FSE_UI_ECS_CLUSTER=${var.cluster_name}",
    "FSE_UI_ECS_TASK_FAMILY=${var.task_family}",
    "FSE_UI_ECS_SUBNETS=${join(",", var.subnet_ids)}",
    "FSE_UI_ECS_SECURITY_GROUP=${var.batch_security_group_id}",
    "FSE_UI_BATCH_LOG_GROUP=${var.batch_log_group}",
    "FSE_UI_RUNNER_LOG_GROUP=${var.runner_log_group}",
    "FSE_UI_RUNNER_DOCUMENT=${local.control_document_name}",
    "FSE_UI_ALARM_NAMES=${join(",", var.alarm_names)}",
    "FSE_UI_HEAVY_JOBS=1",
    "FSE_UI_ECS_MAX_TASKS=4",
    "FSE_UI_ENABLE_LIVE=false",
    # Plugin-orders switch, read-only for the console (Terraform owns the parameter).
    "FSE_UI_PLUGIN_ORDERS=${var.plugin_orders_enabled ? "enabled" : "disabled"}",
    "FSE_UI_PLUGIN_ORDERS_PARAM=${var.plugin_orders_param_name}",
    "FSE_UI_TOPSTEP_VERIFIED_ON=${var.topstep_terms_verified_on}",
    "FSE_UI_AWS_REGION=${var.region}",
    # Host-only keys (fse-ui-fetch-env.sh, the start scripts).
    "FSE_UI_COGNITO_POOL_ID=${var.cognito_user_pool_id}",
    "FSE_EDGE_SITE=${var.origin_domain}",
    "FSE_EDGE_ZONE_ID=${var.zone_id}",
    "FSE_EDGE_AWS_REGION=${var.region}",
    "FSE_UI_IMAGE_REPO=${var.ui_repository_url}",
    "FSE_EDGE_IMAGE_REPO=${var.edge_repository_url}",
    "FSE_UI_LOG_GROUP=${local.ui_log_group_name}",
    # Read by no script: a rotation changes the document, so the association
    # re-runs and the units restart with the new secret.
    "FSE_EDGE_SECRET_VERSION=${var.origin_secret_version}",
  ])

  bundle_script = templatefile("${path.module}/templates/bundle.sh.tftpl", {
    region           = var.region
    ssm_prefix       = var.ssm_prefix
    ui_env           = local.ui_env
    fetch_env        = chomp(file("${path.module}/files/fse-ui-fetch-env.sh"))
    ui_start         = chomp(file("${path.module}/files/fse-ui-start.sh"))
    edge_start       = chomp(file("${path.module}/files/fse-edge-start.sh"))
    runner_mode      = chomp(file("${path.module}/files/fse-runner-mode.sh"))
    console_start    = chomp(file("${path.module}/files/fse-console-start.sh"))
    runner_state     = chomp(file("${path.module}/files/fse-runner-state.sh"))
    sync_cache       = chomp(file("${path.module}/files/fse-sync-cache.sh"))
    cache_sync_unit  = chomp(file("${path.module}/files/fse-cache-sync.service"))
    cache_sync_timer = chomp(file("${path.module}/files/fse-cache-sync.timer"))
    offload_script   = chomp(file("${path.module}/files/fse-offload-recordings.sh"))
    offload_unit     = chomp(file("${path.module}/files/fse-offload-recordings.service"))
    offload_timer    = chomp(file("${path.module}/files/fse-offload-recordings.timer"))
    ui_unit          = chomp(file("${path.module}/files/fse-ui.service"))
    edge_unit        = chomp(file("${path.module}/files/fse-edge.service"))
    dropin           = chomp(file("${path.module}/files/20-console.conf"))
  })

  # SSM Command documents are limited to 64 KB (asserted < 60000 in ui.tftest.hcl).
  bundle_content = jsonencode({
    schemaVersion = "2.2"
    description   = "fse console: install the fse-ui and fse-edge units, host scripts and the fse-runner drop-in."
    mainSteps = [
      {
        action = "aws:runShellScript"
        name   = "installConsole"
        inputs = {
          timeoutSeconds = "600"
          runCommand     = [local.bundle_script]
        }
      },
    ]
  })

  remove_content = jsonencode({
    schemaVersion = "2.2"
    description   = "fse console off-switch: remove the console units, scripts, drop-in and host state. Run once before applying enable_ui = false (that apply deletes this document)."
    mainSteps = [
      {
        action = "aws:runShellScript"
        name   = "removeConsole"
        inputs = {
          timeoutSeconds = "300"
          runCommand     = [file("${path.module}/templates/ui-host-remove.sh")]
        }
      },
    ]
  })
}

resource "aws_ssm_document" "runner_control" {
  name            = local.control_document_name
  document_type   = "Command"
  document_format = "YAML"
  content         = file("${path.module}/templates/runner-control.yaml")
}

resource "aws_ssm_document" "ui_host" {
  name            = local.bundle_document_name
  document_type   = "Command"
  document_format = "JSON"
  content         = local.bundle_content
}

# No association: the operator runs it once (Run Command, instance tagged
# Role=fse-runner) before the apply that sets enable_ui = false, which deletes
# this document with the rest of the module.
resource "aws_ssm_document" "ui_host_remove" {
  name            = local.remove_document_name
  document_type   = "Command"
  document_format = "JSON"
  content         = local.remove_content
}
