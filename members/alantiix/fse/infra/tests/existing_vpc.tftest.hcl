# Plan-only: fse in an existing VPC. Nothing VPC-level is created; only the two
# security groups land in the given VPC, and the runner and batch use the given
# subnets. Mock providers: no credentials, no AWS calls.

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
      names = ["us-east-1a", "us-east-1b"]
    }
  }
  override_data {
    target = module.network.data.aws_vpc.existing
    values = {
      id         = "vpc-0123456789abcdef0"
      cidr_block = "10.20.0.0/16"
    }
  }
  override_data {
    target = module.network.data.aws_subnet.existing
    values = {
      id                = "subnet-0123456789abcdef0"
      vpc_id            = "vpc-0123456789abcdef0"
      availability_zone = "us-east-1a"
    }
  }
}

mock_provider "aws" {
  alias = "us_east_1"
}

mock_provider "random" {}

variables {
  alert_email                = "ops@example.com"
  adopt_existing_data_bucket = false
  existing_vpc_id            = "vpc-0123456789abcdef0"
  existing_subnet_ids        = ["subnet-0123456789abcdef0"]
}

run "existing_vpc" {
  command = plan

  assert {
    condition = alltrue([
      for k, v in module.network.created_network_resource_counts : v == 0
    ])
    error_message = "No VPC, subnet, gateway, route table, route, endpoint or default SG may be created in an existing VPC."
  }

  assert {
    condition     = module.network.uses_existing_vpc && output.vpc_id == "vpc-0123456789abcdef0"
    error_message = "The stack must use existing_vpc_id."
  }

  assert {
    condition     = output.subnet_ids == tolist(["subnet-0123456789abcdef0"])
    error_message = "The stack must use existing_subnet_ids."
  }

  assert {
    condition     = module.network.security_group_ingress_counts.runner == 0 && module.network.security_group_ingress_counts.batch == 0
    error_message = "Runner and batch security groups must have no ingress rules."
  }

  assert {
    condition     = module.network.s3_endpoint_id == null && module.network.default_route_nat_gateway_id == null
    error_message = "No S3 endpoint or route is created in an existing VPC."
  }
}

run "subnet_without_vpc" {
  command = plan

  variables {
    existing_vpc_id = null
  }

  expect_failures = [var.existing_subnet_ids]
}

run "vpc_without_subnets" {
  command = plan

  variables {
    existing_subnet_ids = []
  }

  expect_failures = [var.existing_subnet_ids]
}
