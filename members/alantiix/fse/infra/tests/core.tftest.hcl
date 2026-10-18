# Plan-only tests with a mocked AWS provider: no credentials, no AWS calls.
# Every run uses command = plan: prevent_destroy on the data bucket breaks the
# destroy that terraform test runs after an apply.

mock_provider "aws" {
  override_data {
    target = data.aws_caller_identity.current
    values = {
      account_id = "123456789012"
    }
  }

  override_data {
    target = module.network.data.aws_availability_zones.available
    values = {
      names = ["us-east-1a", "us-east-1b", "us-east-1c"]
    }
  }
}

# The root declares aws.us_east_1 and random (modules/ui_edge).
mock_provider "aws" {
  alias = "us_east_1"
}

mock_provider "random" {}

variables {
  alert_email = "ops@example.com"
  # Mock providers cannot import; the "adopt" run covers the import path.
  adopt_existing_data_bucket = false
}

run "core" {
  command = plan

  assert {
    condition     = output.data_bucket == "skylit-fse-data-123456789012-us-east-1"
    error_message = "Data bucket must be skylit-fse-data-<account>-us-east-1 (the name fse export-aws uses)."
  }

  assert {
    condition     = length(output.subnet_ids) == 2
    error_message = "Network must have exactly 2 public subnets."
  }

  assert {
    condition     = module.network.public_subnet_azs == tolist(["us-east-1a", "us-east-1b"])
    error_message = "Subnets must use the first two availability zones."
  }

  assert {
    condition     = module.network.default_route_nat_gateway_id == null
    error_message = "The default route must not go through a NAT gateway."
  }

  assert {
    condition     = module.ecr.scan_on_push
    error_message = "ECR must scan on push."
  }

  assert {
    condition     = length(output.ssm_parameter_names) == 10
    error_message = "Expected 8 secret parameters plus /fse/image_tag and /fse/control/plugin_orders."
  }

  assert {
    condition     = module.params.plugin_orders_param_name == "/fse/control/plugin_orders" && contains(output.ssm_parameter_names, module.params.plugin_orders_param_name)
    error_message = "The plugin-orders switch must be /fse/control/plugin_orders, created without the UI."
  }

  assert {
    condition     = module.params.plugin_orders_value == "disabled"
    error_message = "The plugin-orders switch must default to disabled."
  }

  assert {
    condition     = contains(output.ssm_parameter_names, "/fse/image_tag") && contains(output.ssm_parameter_names, "/fse/secrets/SKYLIT_API_KEY")
    error_message = "Parameter paths must be /fse/secrets/<NAME> and /fse/image_tag."
  }

  assert {
    condition     = module.network.security_group_ingress_counts.runner == 0 && module.network.security_group_ingress_counts.batch == 0
    error_message = "Runner and batch security groups must have no ingress rules."
  }
}

# Default (adopt = true): the import block targets the existing bucket. The
# override stands in for the imported state, since mocks cannot import.
run "adopt" {
  command = plan

  variables {
    adopt_existing_data_bucket = true
  }

  override_resource {
    target = module.data_bucket.aws_s3_bucket.data
    values = {
      id     = "skylit-fse-data-123456789012-us-east-1"
      bucket = "skylit-fse-data-123456789012-us-east-1"
    }
  }

  assert {
    condition     = output.data_bucket == "skylit-fse-data-123456789012-us-east-1"
    error_message = "The adopted bucket must keep its fse export-aws name."
  }
}

run "invalid_alert_email" {
  command = plan

  variables {
    alert_email = "not-an-email"
  }

  expect_failures = [var.alert_email]
}
