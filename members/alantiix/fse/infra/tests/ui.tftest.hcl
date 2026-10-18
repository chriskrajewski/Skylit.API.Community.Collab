# fse console edge (modules/ui_edge, enable_ui): plan-only with mocked
# providers (no credentials, no AWS calls). Computed values the assertions
# read are made known with override_during = plan. Targets inside the counted
# ui_edge module need the instance key (module.ui_edge[0].…): without it
# Terraform 1.15 silently ignores the override.

mock_provider "aws" {}

mock_provider "aws" {
  alias = "us_east_1"
}

mock_provider "random" {}

variables {
  alert_email = "ops@example.com"
  # Mock providers cannot import (core.tftest.hcl covers the import path).
  adopt_existing_data_bucket = false
}

override_data {
  target = data.aws_caller_identity.current
  values = {
    account_id = "123456789012"
  }
}

override_data {
  target = module.network.data.aws_availability_zones.available
  values = {
    names = ["us-east-1a", "us-east-1b"]
  }
}

override_data {
  target = module.runner.data.aws_ssm_parameter.ami
  values = {
    insecure_value = "ami-0123456789abcdef0"
  }
}

override_data {
  target = data.aws_ec2_managed_prefix_list.cloudfront
  values = {
    id = "pl-3b927c52"
  }
}

override_data {
  target = data.aws_servicequotas_service_quota.sg_rules
  values = {
    value = 60
  }
}

override_data {
  target = module.ui_edge[0].data.aws_route53_zone.this
  values = {
    zone_id = "Z0000000000TEST"
    name    = "example.com"
  }
}

override_data {
  target = module.ui_edge[0].data.aws_cloudfront_cache_policy.caching_optimized
  values = {
    id = "658327ea-f89d-4fab-a63d-7e88639e58f6"
  }
}

override_data {
  target = module.ui_edge[0].data.aws_cloudfront_cache_policy.caching_disabled
  values = {
    id = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
  }
}

override_data {
  target = module.ui_edge[0].data.aws_cloudfront_origin_request_policy.all_viewer_except_host
  values = {
    id = "b689b0a8-53d0-40ab-baf2-68738e2966ac"
  }
}

override_resource {
  target          = module.runner.aws_ebs_volume.data
  override_during = plan
  values = {
    id = "vol-0123456789abcdef0"
  }
}

override_resource {
  target          = module.runner.aws_instance.this
  override_during = plan
  values = {
    id = "i-0123456789abcdef0"
  }
}

override_resource {
  target          = module.runner.aws_eip.runner
  override_during = plan
  values = {
    public_ip = "198.51.100.10"
  }
}

# The runner cloud-init embeds the fse repository URL and the alerts topic:
# known values make module.runner.user_data_size and user_data_sha known.
override_resource {
  target          = module.ecr.aws_ecr_repository.this
  override_during = plan
  values = {
    arn            = "arn:aws:ecr:us-east-1:123456789012:repository/fse"
    repository_url = "123456789012.dkr.ecr.us-east-1.amazonaws.com/fse"
  }
}

override_resource {
  target          = module.alerts.aws_sns_topic.alerts
  override_during = plan
  values = {
    arn = "arn:aws:sns:us-east-1:123456789012:fse-alerts"
  }
}

# ui_host inputs that ui.env and the policies embed.
override_resource {
  target          = module.network.aws_subnet.public
  override_during = plan
  values = {
    id = "subnet-0123456789abcdef0"
  }
}

override_resource {
  target          = module.network.aws_security_group.batch
  override_during = plan
  values = {
    id = "sg-0123456789abcdef0"
  }
}

override_resource {
  target          = module.ecr_ui[0].aws_ecr_repository.this
  override_during = plan
  values = {
    arn            = "arn:aws:ecr:us-east-1:123456789012:repository/fse-ui"
    repository_url = "123456789012.dkr.ecr.us-east-1.amazonaws.com/fse-ui"
  }
}

override_resource {
  target          = module.ecr_edge[0].aws_ecr_repository.this
  override_during = plan
  values = {
    arn            = "arn:aws:ecr:us-east-1:123456789012:repository/fse-edge"
    repository_url = "123456789012.dkr.ecr.us-east-1.amazonaws.com/fse-edge"
  }
}

override_resource {
  target          = module.ui_edge[0].aws_cognito_user_pool.ui
  override_during = plan
  values = {
    id       = "us-east-1_TEST"
    arn      = "arn:aws:cognito-idp:us-east-1:123456789012:userpool/us-east-1_TEST"
    endpoint = "cognito-idp.us-east-1.amazonaws.com/us-east-1_TEST"
  }
}

override_resource {
  target          = module.ui_edge[0].aws_cognito_user_pool_client.ui
  override_during = plan
  values = {
    id = "testclientid0123456789"
  }
}

override_resource {
  target          = module.ui_edge[0].aws_cognito_user_pool_domain.ui
  override_during = plan
  values = {
    id = "fse-console-test"
  }
}

override_resource {
  target          = module.ui_edge[0].aws_acm_certificate.ui
  override_during = plan
  values = {
    arn = "arn:aws:acm:us-east-1:123456789012:certificate/00000000-0000-0000-0000-000000000000"
    domain_validation_options = [{
      domain_name           = "fse.example.com"
      resource_record_name  = "_x.fse.example.com."
      resource_record_type  = "CNAME"
      resource_record_value = "_y.acm-validations.aws."
    }]
  }
}

run "ui_off" {
  command = plan

  assert {
    condition     = length(module.ui_edge) == 0
    error_message = "enable_ui = false must create nothing from ui_edge."
  }

  assert {
    condition     = length(data.aws_ec2_managed_prefix_list.cloudfront) == 0 && length(data.aws_servicequotas_service_quota.sg_rules) == 0 && length(terraform_data.sg_rules_quota_guard) == 0
    error_message = "enable_ui = false must read no prefix list or quota."
  }

  assert {
    condition     = module.network.security_group_ingress_counts.runner == 0 && length(module.network.runner_ingress) == 0
    error_message = "Runner security group must have no ingress with the UI off."
  }

  assert {
    condition     = module.runner.instance_type == "t4g.small"
    error_message = "Runner host must stay t4g.small with the UI off."
  }

  assert {
    condition     = output.ui_url == null && output.ui_origin_domain == null && output.cognito_user_pool_id == null
    error_message = "UI outputs must be null with the UI off."
  }

  assert {
    condition     = length(module.ui_host) == 0 && length(module.ecr_ui) == 0 && length(module.ecr_edge) == 0
    error_message = "enable_ui = false must create nothing from ui_host and no console repositories."
  }

  assert {
    condition     = module.runner.user_data_size < 16384
    error_message = "Runner cloud-init must stay under the 16384-byte EC2 user data limit."
  }
}

run "ui_on" {
  command = plan

  variables {
    enable_ui      = true
    operator_email = "operator@example.com"
  }

  assert {
    condition     = module.runner.instance_type == "t4g.medium"
    error_message = "With the UI on the runner host must default to t4g.medium."
  }

  assert {
    condition     = module.network.security_group_ingress_counts.runner == 1 && module.network.security_group_ingress_counts.batch == 0
    error_message = "Runner SG must have exactly one ingress rule; batch none."
  }

  assert {
    condition     = toset(one(module.network.runner_ingress).prefix_list_ids) == toset(["pl-3b927c52"])
    error_message = "Runner ingress must come from the CloudFront origin-facing prefix list only."
  }

  assert {
    condition     = length(one(module.network.runner_ingress).cidr_blocks) == 0 && length(one(module.network.runner_ingress).ipv6_cidr_blocks) == 0 && length(one(module.network.runner_ingress).security_groups) == 0
    error_message = "Runner ingress must have no CIDR or security group sources."
  }

  assert {
    condition     = one(module.network.runner_ingress).from_port == 443 && one(module.network.runner_ingress).to_port == 443 && one(module.network.runner_ingress).protocol == "tcp"
    error_message = "Runner ingress must be TCP 443 only."
  }

  assert {
    condition     = module.ui_edge[0].settings.mfa_configuration == "ON" && module.ui_edge[0].settings.software_token_mfa_enabled
    error_message = "Cognito must require TOTP MFA."
  }

  assert {
    condition     = module.ui_edge[0].settings.allow_admin_create_user_only
    error_message = "Cognito self-signup must be off."
  }

  assert {
    condition     = module.ui_edge[0].settings.deletion_protection == "ACTIVE"
    error_message = "Cognito deletion protection must default to ACTIVE."
  }

  assert {
    condition     = toset(module.ui_edge[0].settings.allowed_oauth_flows) == toset(["code"]) && module.ui_edge[0].settings.generate_secret
    error_message = "App client must use the code flow with a secret."
  }

  assert {
    condition     = toset(module.ui_edge[0].settings.callback_urls) == toset(["https://fse.example.com/auth/callback"]) && toset(module.ui_edge[0].settings.logout_urls) == toset(["https://fse.example.com/"])
    error_message = "Callback and logout URLs must be on https://fse.example.com."
  }

  assert {
    condition     = module.ui_edge[0].settings.token_validity_units.access_token == "minutes" && module.ui_edge[0].settings.token_validity_units.id_token == "minutes"
    error_message = "Access and id token validity must be in minutes."
  }

  assert {
    condition     = module.ui_edge[0].settings.viewer_protocol_policy == "redirect-to-https"
    error_message = "CloudFront must redirect viewers to HTTPS."
  }

  assert {
    condition     = module.ui_edge[0].settings.origin_protocol_policy == "https-only" && toset(module.ui_edge[0].settings.origin_ssl_protocols) == toset(["TLSv1.2"])
    error_message = "CloudFront must reach the origin over TLS 1.2 only."
  }

  assert {
    condition     = module.ui_edge[0].settings.price_class == "PriceClass_100"
    error_message = "CloudFront must use PriceClass_100."
  }

  assert {
    condition     = toset(module.ui_edge[0].settings.aliases) == toset(["fse.example.com"])
    error_message = "CloudFront alias must be fse.example.com."
  }

  assert {
    condition     = module.ui_edge[0].settings.certificate_domain == "fse.example.com"
    error_message = "ACM certificate must be for fse.example.com."
  }

  assert {
    condition     = output.runner_public_ip == "198.51.100.10" && toset(module.ui_edge[0].settings.origin_record) == toset([output.runner_public_ip])
    error_message = "origin.fse must point at the runner Elastic IP."
  }

  assert {
    condition     = strcontains(module.ui_edge[0].oidc.issuer, "cognito-idp.us-east-1.amazonaws.com/us-east-1_TEST")
    error_message = "OIDC issuer must be the pool in var.region."
  }

  assert {
    condition     = module.ui_edge[0].oidc.authorize_url == "https://fse-console-${substr(sha1("123456789012"), 0, 8)}.auth.us-east-1.amazoncognito.com/oauth2/authorize"
    error_message = "Authorize URL must use the hashed prefix domain."
  }

  assert {
    condition     = module.ui_edge[0].origin_secret_parameter_name == "/fse/ui/origin_secret" && module.ui_edge[0].origin_secret_version == 1
    error_message = "Origin secret must be /fse/ui/origin_secret at version 1."
  }

  assert {
    condition     = output.ui_url == "https://fse.example.com" && output.ui_origin_domain == "origin.fse.example.com" && output.cognito_user_pool_id == "us-east-1_TEST"
    error_message = "Root UI outputs must be set with the UI on."
  }

  # ---------------------------------------------------------------- ui_host

  assert {
    condition     = module.runner.user_data_size < 16384
    error_message = "Runner cloud-init must stay under the 16384-byte EC2 user data limit."
  }

  assert {
    condition     = run.ui_off.runner_user_data_sha == output.runner_user_data_sha
    error_message = "enable_ui must not change the runner cloud-init."
  }

  assert {
    condition     = length(module.ui_host) == 1 && module.ecr_ui[0].repository_name == "fse-ui" && module.ecr_edge[0].repository_name == "fse-edge"
    error_message = "enable_ui = true must create ui_host and the fse-ui and fse-edge repositories."
  }

  assert {
    condition     = module.ecr_ui[0].scan_on_push && module.ecr_edge[0].scan_on_push
    error_message = "Console repositories must scan on push like fse."
  }

  assert {
    condition     = length(module.ui_host[0].bundle_content) < 60000
    error_message = "The bundle document must stay under the 64 KB SSM document limit."
  }

  assert {
    condition = alltrue([
      for s in [
        "/etc/systemd/system/fse-ui.service",
        "/etc/systemd/system/fse-edge.service",
        "/etc/systemd/system/fse-runner.service.d/20-console.conf",
        "FSE_EDGE_SECRET_VERSION=1",
        "FSE_UI_PUBLIC_URL=https://fse.example.com",
        "FSE_EDGE_SITE=origin.fse.example.com",
        "FSE_EDGE_ZONE_ID=Z0000000000TEST",
        "FSE_UI_OIDC_CLIENT_ID=testclientid0123456789",
        "FSE_UI_COGNITO_POOL_ID=us-east-1_TEST",
        "FSE_UI_ECS_SUBNETS=subnet-0123456789abcdef0",
        "FSE_UI_ECS_SECURITY_GROUP=sg-0123456789abcdef0",
        "FSE_UI_RUNNER_DOCUMENT=fse-runner-control",
        "FSE_UI_ALARM_NAMES=fse-runner-system-check,fse-runner-instance-check,fse-runner-log-silence",
        "FSE_UI_ENABLE_LIVE=false",
        "FSE_UI_IMAGE_REPO=123456789012.dkr.ecr.us-east-1.amazonaws.com/fse-ui",
        "FSE_EDGE_IMAGE_REPO=123456789012.dkr.ecr.us-east-1.amazonaws.com/fse-edge",
        "reset-failed fse-ui.service fse-edge.service",
      ] : strcontains(module.ui_host[0].bundle_content, s)
    ])
    error_message = "The bundle must install the units and drop-in and write ui.env from the module inputs."
  }

  assert {
    condition     = !strcontains(module.ui_host[0].bundle_content, "restart fse-runner") && !strcontains(module.ui_host[0].bundle_content, "AWS-RunShellScript")
    error_message = "The bundle must never restart fse-runner."
  }

  assert {
    condition     = module.ui_host[0].association.document == "fse-ui-host-bundle" && one(module.ui_host[0].association.targets).key == "InstanceIds" && toset(one(module.ui_host[0].association.targets).values) == toset(["i-0123456789abcdef0"])
    error_message = "The bundle association must target the runner instance id."
  }

  assert {
    condition     = module.ui_host[0].association.max_errors == "0" && module.ui_host[0].association.compliance_severity == "HIGH" && module.ui_host[0].association.wait_for_success_timeout_seconds == 600
    error_message = "The association must fail on any error and make apply wait up to 600 s."
  }

  assert {
    condition     = module.ui_host[0].document_names.remove == "fse-ui-host-remove" && module.ui_host[0].association.document != module.ui_host[0].document_names.remove
    error_message = "fse-ui-host-remove must exist and have no association."
  }

  assert {
    condition     = module.ui_host[0].document_names.control == "fse-runner-control" && strcontains(module.ui_host[0].control_document_content, "allowedValues")
    error_message = "fse-runner-control must exist with a restricted action parameter."
  }

  assert {
    condition     = toset(module.ui_host[0].parameter_names) == toset(["/fse/runner_mode", "/fse/runner_config"]) && module.ui_host[0].log_group_name == "/fse/ui"
    error_message = "ui_host must own /fse/runner_mode, /fse/runner_config and the /fse/ui log group."
  }

  assert {
    condition = toset([for s in jsondecode(module.ui_host[0].runner_console_policy).Statement : s.Sid]) == toset([
      "EcsRunBatch", "EcsBatchTasks", "EcsFindTasks", "PassBatchRoles", "ListDataBucket", "ReadRuns",
      "ReadRunnerCache", "SeedMissingCache", "WriteConsoleInputs", "Locks", "ReadLogs", "UiLogs", "RunnerControlDoc", "RunnerControlInstance",
      "CommandResult", "WriteParameters", "Alarms", "CognitoClient", "AcmeRecord", "AcmeRead", "AcmeChange",
      "EcrPullUi", "OffloadRecordings",
    ])
    error_message = "runner_console must have exactly the statements in design 11.5."
  }

  assert {
    condition = alltrue([
      for s in jsondecode(module.ui_host[0].runner_console_policy).Statement : !contains(keys(s), "Condition") if s.Sid == "ListDataBucket"
    ])
    error_message = "ListDataBucket must have no condition (NoSuchKey on a missing lock)."
  }

  assert {
    condition     = [for s in jsondecode(module.ui_host[0].runner_console_policy).Statement : s.Action if s.Sid == "ReadLogs"] == [["logs:GetLogEvents"]]
    error_message = "ReadLogs must grant logs:GetLogEvents only."
  }

  assert {
    condition = (
      !contains(flatten([for s in jsondecode(module.ui_host[0].runner_console_policy).Statement : s.Action]), "logs:FilterLogEvents")
      && !contains(flatten([for s in jsondecode(module.ui_host[0].runner_console_policy).Statement : s.Action]), "s3:GetBucketLocation")
    )
    error_message = "The console must not get logs:FilterLogEvents or s3:GetBucketLocation."
  }

  assert {
    condition     = [for s in jsondecode(module.ui_host[0].runner_console_policy).Statement : [s.Resource, s.Condition.ArnEquals["ecs:cluster"]] if contains(s.Action, "ecs:RunTask")] == [[["arn:aws:ecs:us-east-1:123456789012:task-definition/fse-batch:*"], "arn:aws:ecs:us-east-1:123456789012:cluster/fse"]]
    error_message = "RunTask must be limited to fse-batch revisions on the fse cluster."
  }

  assert {
    condition     = [for s in jsondecode(module.ui_host[0].runner_console_policy).Statement : [s.Resource, s.Condition.ArnEquals["ecs:cluster"]] if s.Sid == "EcsFindTasks"] == [[["*"], "arn:aws:ecs:us-east-1:123456789012:cluster/fse"]]
    error_message = "ListTasks must be scoped to the fse cluster by condition."
  }

  assert {
    condition = [for s in jsondecode(module.ui_host[0].runner_console_policy).Statement : [toset(s.Resource), s.Condition.StringEquals["iam:PassedToService"]] if contains(s.Action, "iam:PassRole")] == [[
      toset(["arn:aws:iam::123456789012:role/fse-batch-task", "arn:aws:iam::123456789012:role/fse-batch-exec"]),
      "ecs-tasks.amazonaws.com",
    ]]
    error_message = "PassRole must cover only the batch roles, passed to ecs-tasks."
  }

  assert {
    condition = [
      for r in flatten([for s in jsondecode(module.ui_host[0].runner_console_policy).Statement : s.Resource if contains(s.Action, "ssm:SendCommand")]) : r if strcontains(r, ":document/")
    ] == ["arn:aws:ssm:us-east-1:123456789012:document/fse-runner-control"]
    error_message = "SendCommand must name the fse-runner-control document only."
  }

  assert {
    condition = toset([
      for r in flatten([for s in jsondecode(module.ui_host[0].runner_console_policy).Statement : s.Resource]) :
      regex("^arn:aws:s3:::[^/]+/([^/]+)/", r)[0] if startswith(r, "arn:aws:s3:::") && strcontains(r, "/")
    ]) == toset(["runs", "configs", "uploads", "locks", "cache", "recordings"])
    error_message = "Console S3 object access must stay under runs/, configs/, uploads/, locks/, cache/ and recordings/."
  }

  assert {
    condition = toset(flatten([for s in jsondecode(module.ui_host[0].runner_console_policy).Statement : s.Resource if contains(s.Action, "s3:GetObject")])) == toset([
      "arn:aws:s3:::skylit-fse-data-123456789012-us-east-1/runs/*",
      "arn:aws:s3:::skylit-fse-data-123456789012-us-east-1/locks/*",
      "arn:aws:s3:::skylit-fse-data-123456789012-us-east-1/cache/catalog.sqlite",
      "arn:aws:s3:::skylit-fse-data-123456789012-us-east-1/cache/bars/*",
      "arn:aws:s3:::skylit-fse-data-123456789012-us-east-1/cache/equity_bars/*",
      "arn:aws:s3:::skylit-fse-data-123456789012-us-east-1/cache/derived/*",
      "arn:aws:s3:::skylit-fse-data-123456789012-us-east-1/cache/darkpool/*",
      "arn:aws:s3:::skylit-fse-data-123456789012-us-east-1/cache/vix/*",
      "arn:aws:s3:::skylit-fse-data-123456789012-us-east-1/recordings/*",
    ])
    error_message = "The host may read runs/*, recordings/*, locks/* and the named bars part of cache/ (never configs/*, uploads/*, heatmaps/ or plugin_books/)."
  }
  assert {
    condition     = !strcontains(module.ui_host[0].runner_console_policy, "cache/*") && !strcontains(module.ui_host[0].runner_console_policy, "heatmaps") && !strcontains(module.ui_host[0].runner_console_policy, "plugin_books")
    error_message = "The cache grant must name its prefixes; no cache/* and never the 20 GB heatmaps."
  }
  assert {
    condition = alltrue([
      for s in [
        "/usr/local/bin/fse-sync-cache.sh",
        "/etc/systemd/system/fse-cache-sync.service",
        "/etc/systemd/system/fse-cache-sync.timer",
        "systemctl enable --now fse-cache-sync.timer",
        "/usr/local/bin/fse-offload-recordings.sh",
        "/etc/systemd/system/fse-offload-recordings.service",
        "/etc/systemd/system/fse-offload-recordings.timer",
        "systemctl enable --now fse-offload-recordings.timer",
      ] : strcontains(module.ui_host[0].bundle_content, s)
    ])
    error_message = "The bundle must install the cache sync and recordings offload scripts, services and timers and enable the timers."
  }
  assert {
    condition = [for s in jsondecode(module.ui_host[0].runner_console_policy).Statement : [toset(s.Action), s.Resource] if s.Sid == "OffloadRecordings"] == [[
      toset(["s3:PutObject", "s3:GetObject", "s3:AbortMultipartUpload"]),
      ["arn:aws:s3:::skylit-fse-data-123456789012-us-east-1/recordings/*"],
    ]]
    error_message = "OffloadRecordings must grant Put/Get/AbortMultipartUpload on recordings/* only (no DeleteObject)."
  }
  assert {
    condition = [for s in jsondecode(module.ui_host[0].runner_console_policy).Statement : [toset(s.Action), s.Resource] if s.Sid == "SeedMissingCache"] == [[
      toset(["s3:PutObject", "s3:AbortMultipartUpload"]),
      [
        "arn:aws:s3:::skylit-fse-data-123456789012-us-east-1/cache/catalog.sqlite",
        "arn:aws:s3:::skylit-fse-data-123456789012-us-east-1/cache/bars/*",
        "arn:aws:s3:::skylit-fse-data-123456789012-us-east-1/cache/equity_bars/*",
        "arn:aws:s3:::skylit-fse-data-123456789012-us-east-1/cache/derived/*",
        "arn:aws:s3:::skylit-fse-data-123456789012-us-east-1/cache/darkpool/*",
        "arn:aws:s3:::skylit-fse-data-123456789012-us-east-1/cache/vix/*",
      ],
    ]]
    error_message = "SeedMissingCache must grant Put and AbortMultipartUpload on the named cache prefixes only."
  }
  assert {
    condition = [for s in jsondecode(module.ui_host[0].batch_console_policy).Statement : [toset(s.Action), s.Resource] if s.Sid == "ReadRecordings"] == [[
      toset(["s3:GetObject"]),
      ["arn:aws:s3:::skylit-fse-data-123456789012-us-east-1/recordings/*"],
    ]]
    error_message = "batch_console ReadRecordings must grant GetObject on recordings/* only."
  }
  assert {
    condition     = strcontains(module.ui_host[0].bundle_content, "ExecStartPre=-/usr/local/bin/fse-sync-cache.sh")
    error_message = "The runner drop-in must run the cache sync before start, non-fatally (- prefix)."
  }

  assert {
    condition     = toset(flatten([for s in jsondecode(module.ui_host[0].runner_console_policy).Statement : s.Resource if contains(s.Action, "s3:DeleteObject")])) == toset(["arn:aws:s3:::skylit-fse-data-123456789012-us-east-1/locks/*"])
    error_message = "The console may delete only locks/*."
  }

  assert {
    condition     = toset(flatten([for s in jsondecode(module.ui_host[0].batch_console_policy).Statement : s.Resource if contains(s.Action, "s3:DeleteObject")])) == toset(["arn:aws:s3:::skylit-fse-data-123456789012-us-east-1/fse/*"])
    error_message = "batch_console may delete only fse/* (export-aws)."
  }

  assert {
    condition = [
      for s in jsondecode(module.ui_host[0].runner_console_policy).Statement : [
        s.Resource,
        s.Condition["ForAllValues:StringEquals"]["route53:ChangeResourceRecordSetsNormalizedRecordNames"],
        s.Condition["ForAllValues:StringEquals"]["route53:ChangeResourceRecordSetsRecordTypes"],
        s.Condition.Null["route53:ChangeResourceRecordSetsNormalizedRecordNames"],
      ] if s.Sid == "AcmeRecord"
      ] == [[
        ["arn:aws:route53:::hostedzone/Z0000000000TEST"],
        ["_acme-challenge.origin.fse.example.com"],
        ["TXT"],
        "false",
    ]]
    error_message = "Route 53 changes must be limited to the origin's _acme-challenge TXT record."
  }

  assert {
    condition     = [for s in jsondecode(module.ui_host[0].runner_console_policy).Statement : s.Condition.StringEquals["ssm:resourceTag/Role"] if s.Sid == "RunnerControlInstance"] == ["fse-runner"]
    error_message = "SendCommand must target only instances tagged Role = fse-runner."
  }

  # ------------------------------------------------------- plugin-orders switch

  assert {
    condition     = module.params.plugin_orders_value == "disabled"
    error_message = "The plugin-orders switch must default to disabled."
  }

  assert {
    condition = alltrue([
      for s in [
        "FSE_UI_PLUGIN_ORDERS=disabled\n",
        "FSE_UI_PLUGIN_ORDERS_PARAM=${module.params.plugin_orders_param_name}\n",
        "FSE_UI_TOPSTEP_VERIFIED_ON=2026-10-13\n",
        "FSE_UI_ENABLE_LIVE=false\n",
      ] : strcontains(jsondecode(module.ui_host[0].bundle_content).mainSteps[0].inputs.runCommand[0], s)
    ])
    error_message = "ui.env must show the switch as disabled with the default terms date (2026-10-13), and keep FSE_UI_ENABLE_LIVE=false."
  }

  assert {
    condition = alltrue([
      for s in [
        "install -m 0755 -o root -g root /dev/stdin /usr/local/bin/fse-console-start.sh",
        "-v \"$FSE_CONTROL_DIR:/run/fse-control:ro\"",
        "ExecStart=\nExecStart=/usr/local/bin/fse-console-start.sh\n",
        "\"$FSE_SSM_PREFIX/control/plugin_orders\"",
      ] : strcontains(jsondecode(module.ui_host[0].bundle_content).mainSteps[0].inputs.runCommand[0], s)
    ])
    error_message = "The bundle must install fse-console-start.sh, override ExecStart in the drop-in and read the switch in fse-runner-mode.sh."
  }

  assert {
    condition = length([for s in jsondecode(module.ui_host[0].runner_console_policy).Statement : s if s.Sid == "WriteParameters"]) == 1 && alltrue([
      for r in flatten([for s in jsondecode(module.ui_host[0].runner_console_policy).Statement : s.Resource if s.Sid == "WriteParameters"]) :
      !endswith(r, "*") && !strcontains(r, "/control/") && !strcontains(r, "FSE_RUN_PLUGIN_ORDERS")
    ])
    error_message = "WriteParameters must name exact parameters: no wildcard, no /control/ and no FSE_RUN_PLUGIN_ORDERS."
  }

  assert {
    condition = length(module.params.secret_names) == 8 && toset(flatten([
      for s in concat(
        jsondecode(module.ui_host[0].runner_console_policy).Statement,
        jsondecode(module.ui_host[0].batch_console_policy).Statement,
      ) : s.Resource
      if length(setintersection(toset(s.Action), toset(["ssm:PutParameter", "ssm:DeleteParameter", "ssm:DeleteParameters", "ssm:LabelParameterVersion", "ssm:*"]))) > 0
      ])) == toset(concat(
      [for n in module.params.secret_names : "arn:aws:ssm:us-east-1:123456789012:parameter/fse/secrets/${n}"],
      ["arn:aws:ssm:us-east-1:123456789012:parameter/fse/runner_mode", "arn:aws:ssm:us-east-1:123456789012:parameter/fse/runner_config"],
    ))
    error_message = "The console may write exactly the 8 secret ARNs, /fse/runner_mode and /fse/runner_config."
  }
}

run "plugin_orders_on" {
  command = plan

  variables {
    enable_ui                 = true
    operator_email            = "operator@example.com"
    plugin_orders_enabled     = true
    topstep_terms_verified_on = "2026-01-31"
  }

  assert {
    condition     = module.params.plugin_orders_value == "enabled"
    error_message = "plugin_orders_enabled = true must write enabled to the switch."
  }

  assert {
    condition = alltrue([
      for s in [
        "FSE_UI_PLUGIN_ORDERS=enabled\n",
        "FSE_UI_TOPSTEP_VERIFIED_ON=2026-01-31\n",
        "FSE_UI_ENABLE_LIVE=false\n",
      ] : strcontains(jsondecode(module.ui_host[0].bundle_content).mainSteps[0].inputs.runCommand[0], s)
    ])
    error_message = "ui.env must show the switch as enabled with the terms date, and keep FSE_UI_ENABLE_LIVE=false."
  }

  assert {
    condition     = !strcontains(module.ui_host[0].runner_console_policy, "/control/")
    error_message = "The console must never be able to write the switch."
  }
}

run "plugin_orders_on_with_the_default_terms_date" {
  command = plan

  variables {
    enable_ui             = true
    operator_email        = "operator@example.com"
    plugin_orders_enabled = true
  }

  assert {
    condition     = module.params.plugin_orders_value == "enabled" && strcontains(jsondecode(module.ui_host[0].bundle_content).mainSteps[0].inputs.runCommand[0], "FSE_UI_TOPSTEP_VERIFIED_ON=2026-10-13\n")
    error_message = "Only FSE_PLUGIN_ORDERS_ENABLED is needed: the default terms date (2026-10-13) is the default."
  }
}

run "plugin_orders_without_date" {
  command = plan

  variables {
    enable_ui                 = true
    operator_email            = "operator@example.com"
    plugin_orders_enabled     = true
    topstep_terms_verified_on = ""
  }

  expect_failures = [var.plugin_orders_enabled]
}

run "plugin_orders_without_ui" {
  command = plan

  variables {
    enable_ui                 = false
    plugin_orders_enabled     = true
    topstep_terms_verified_on = "2026-01-31"
  }

  expect_failures = [var.plugin_orders_enabled]
}

run "terms_date_format" {
  command = plan

  variables {
    topstep_terms_verified_on = "31/01/2026"
  }

  expect_failures = [var.topstep_terms_verified_on]
}

run "ui_on_rotated" {
  command = plan

  variables {
    enable_ui             = true
    operator_email        = "operator@example.com"
    origin_secret_version = 2
  }

  assert {
    condition     = strcontains(module.ui_host[0].bundle_content, "FSE_EDGE_SECRET_VERSION=2") && !strcontains(module.ui_host[0].bundle_content, "FSE_EDGE_SECRET_VERSION=1")
    error_message = "A rotation must change the bundle document so the association re-runs."
  }
}

run "ui_on_unprotected" {
  command = plan

  variables {
    enable_ui                      = true
    operator_email                 = "operator@example.com"
    ui_cognito_deletion_protection = "INACTIVE"
  }

  assert {
    condition     = module.ui_edge[0].settings.deletion_protection == "INACTIVE"
    error_message = "ui_cognito_deletion_protection = INACTIVE must reach the pool."
  }
}

run "ui_on_other_domain" {
  command = plan

  variables {
    enable_ui      = true
    operator_email = "operator@example.com"
    domain_name    = "example.org"
    ui_subdomain   = "console"
  }

  override_data {
    target = module.ui_edge[0].data.aws_route53_zone.this
    values = {
      zone_id = "Z0000000000OTHER"
      name    = "example.org"
    }
  }

  override_resource {
    target          = module.ui_edge[0].aws_acm_certificate.ui
    override_during = plan
    values = {
      arn = "arn:aws:acm:us-east-1:123456789012:certificate/00000000-0000-0000-0000-000000000001"
      domain_validation_options = [{
        domain_name           = "console.example.org"
        resource_record_name  = "_x.console.example.org."
        resource_record_type  = "CNAME"
        resource_record_value = "_y.acm-validations.aws."
      }]
    }
  }

  assert {
    condition     = toset(module.ui_edge[0].settings.callback_urls) == toset(["https://console.example.org/auth/callback"])
    error_message = "Callback URL must follow domain_name and ui_subdomain."
  }

  assert {
    condition     = toset(module.ui_edge[0].settings.logout_urls) == toset(["https://console.example.org/"])
    error_message = "Logout URL must follow domain_name and ui_subdomain."
  }

  assert {
    condition     = module.ui_edge[0].settings.certificate_domain == "console.example.org"
    error_message = "ACM certificate must follow domain_name and ui_subdomain."
  }

  assert {
    condition     = toset(module.ui_edge[0].settings.aliases) == toset(["console.example.org"])
    error_message = "CloudFront alias must follow domain_name and ui_subdomain."
  }

  assert {
    condition     = output.ui_origin_domain == "origin.console.example.org" && module.ui_edge[0].zone_id == "Z0000000000OTHER"
    error_message = "Origin record must be origin.<ui domain> in the example.org zone."
  }

  assert {
    condition = [
      for s in jsondecode(module.ui_host[0].runner_console_policy).Statement : [
        s.Resource,
        s.Condition["ForAllValues:StringEquals"]["route53:ChangeResourceRecordSetsNormalizedRecordNames"],
      ] if s.Sid == "AcmeRecord"
      ] == [[
        ["arn:aws:route53:::hostedzone/Z0000000000OTHER"],
        ["_acme-challenge.origin.console.example.org"],
    ]]
    error_message = "The ACME record permission must follow domain_name and ui_subdomain."
  }

  assert {
    condition = alltrue([
      for s in [
        "FSE_UI_PUBLIC_URL=https://console.example.org\n",
        "FSE_EDGE_SITE=origin.console.example.org\n",
        "FSE_EDGE_ZONE_ID=Z0000000000OTHER\n",
      ] : strcontains(jsondecode(module.ui_host[0].bundle_content).mainSteps[0].inputs.runCommand[0], s)
    ])
    error_message = "ui.env must follow domain_name and ui_subdomain."
  }

  assert {
    condition     = !strcontains(module.ui_host[0].bundle_content, "example.com")
    error_message = "ui_host must hold no domain literal."
  }
}

run "ui_on_custom_type" {
  command = plan

  variables {
    enable_ui               = true
    operator_email          = "operator@example.com"
    ui_runner_instance_type = "t4g.large"
  }

  assert {
    condition     = module.runner.instance_type == "t4g.large"
    error_message = "ui_runner_instance_type must set the host type with the UI on."
  }
}

run "ui_off_custom_type" {
  command = plan

  variables {
    enable_ui            = false
    runner_instance_type = "t4g.medium"
  }

  assert {
    condition     = module.runner.instance_type == "t4g.medium"
    error_message = "runner_instance_type must set the host type with the UI off."
  }
}

run "quota_too_low" {
  command = plan

  variables {
    enable_ui      = true
    operator_email = "operator@example.com"
  }

  override_data {
    target = data.aws_servicequotas_service_quota.sg_rules
    values = {
      value = 50
    }
  }

  expect_failures = [terraform_data.sg_rules_quota_guard]
}

run "operator_email_required" {
  command = plan

  variables {
    enable_ui      = true
    operator_email = null
  }

  expect_failures = [var.operator_email]
}
