# Networking for fse, in one of two modes:
#
# - Dedicated (existing_vpc_id = null, the default): a VPC with two public subnets
#   and no NAT gateway, an internet gateway, and an S3 gateway endpoint.
# - Existing VPC (existing_vpc_id set): the given VPC and subnets are only read.
#   The module creates nothing in them but the fse-runner and fse-batch security
#   groups: no subnet, route, gateway, endpoint or default-SG change. The subnets
#   must route 0.0.0.0/0 to an internet gateway (no NAT is used), since the runner
#   reaches the internet through its Elastic IP and batch tasks through
#   assignPublicIp.
#
# Workloads reach AWS APIs and the internet over HTTPS. Nothing accepts inbound
# traffic, except HTTPS from CloudFront to the runner when ui_origin_prefix_list_id
# is set (enable_ui).

locals {
  create = var.existing_vpc_id == null
}

data "aws_availability_zones" "available" {
  count = local.create ? 1 : 0
  state = "available"
}

data "aws_vpc" "existing" {
  count = local.create ? 0 : 1
  id    = var.existing_vpc_id
}

data "aws_subnet" "existing" {
  count = local.create ? 0 : length(var.existing_subnet_ids)
  id    = var.existing_subnet_ids[count.index]

  lifecycle {
    postcondition {
      condition     = self.vpc_id == var.existing_vpc_id
      error_message = "Subnet ${self.id} is in ${self.vpc_id}, not existing_vpc_id ${var.existing_vpc_id}."
    }
  }
}

locals {
  azs        = local.create ? slice(data.aws_availability_zones.available[0].names, 0, 2) : []
  vpc_id     = local.create ? aws_vpc.this[0].id : data.aws_vpc.existing[0].id
  subnet_ids = tolist(local.create ? aws_subnet.public[*].id : data.aws_subnet.existing[*].id)
  subnet_azs = tolist(local.create ? aws_subnet.public[*].availability_zone : data.aws_subnet.existing[*].availability_zone)
  # S3 traffic uses the gateway endpoint only in the dedicated VPC; in an existing
  # VPC it goes over the HTTPS-to-internet rule.
  s3_prefix_list_ids = local.create ? [aws_vpc_endpoint.s3[0].prefix_list_id] : []
  # Batch NFS egress targets the VPC CIDR (not the efs SG) so the two security
  # groups do not form an inline-rule cycle; efs ingress still admits only fse-batch.
  vpc_cidr_block = local.create ? var.vpc_cidr : data.aws_vpc.existing[0].cidr_block
}

resource "aws_vpc" "this" {
  count = local.create ? 1 : 0

  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = var.name_prefix }
}

# Strip the default security group: no ingress, no egress (dedicated VPC only;
# an existing VPC's default group is never touched).
resource "aws_default_security_group" "this" {
  count  = local.create ? 1 : 0
  vpc_id = aws_vpc.this[0].id

  tags = { Name = "${var.name_prefix}-default-unused" }
}

resource "aws_internet_gateway" "this" {
  count  = local.create ? 1 : 0
  vpc_id = aws_vpc.this[0].id

  tags = { Name = var.name_prefix }
}

resource "aws_subnet" "public" {
  count = length(local.azs)

  vpc_id                  = aws_vpc.this[0].id
  availability_zone       = local.azs[count.index]
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  map_public_ip_on_launch = false

  tags = { Name = "${var.name_prefix}-public-${local.azs[count.index]}" }
}

resource "aws_route_table" "public" {
  count  = local.create ? 1 : 0
  vpc_id = aws_vpc.this[0].id

  tags = { Name = "${var.name_prefix}-public" }
}

# The only default route goes to the internet gateway (no NAT).
resource "aws_route" "public_default" {
  count                  = local.create ? 1 : 0
  route_table_id         = aws_route_table.public[0].id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this[0].id
}

resource "aws_route_table_association" "public" {
  count = length(aws_subnet.public)

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public[0].id
}

# S3 traffic (data bucket sync, ECR layers) stays on the AWS network for free.
resource "aws_vpc_endpoint" "s3" {
  count             = local.create ? 1 : 0
  vpc_id            = aws_vpc.this[0].id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.public[0].id]

  tags = { Name = "${var.name_prefix}-s3" }
}

# Egress is HTTPS only. Amazon Linux 2023 dnf repos, ECR, SSM, Skylit, ProjectX
# and webhooks are all HTTPS, so port 80 stays closed. DNS (VPC resolver) and
# NTP (169.254.169.123) are link-local and not filtered by security groups.
# Inline rules on purpose: never add aws_vpc_security_group_ingress_rule or
# aws_security_group_rule resources for these groups.
resource "aws_security_group" "runner" {
  name        = "${var.name_prefix}-runner"
  description = "fse live runner host: no inbound, HTTPS outbound"
  vpc_id      = local.vpc_id

  # The description above predates the console and is ForceNew: keep it.
  # With enable_ui the only inbound source is CloudFront (attribute syntax, so
  # an empty list still removes rules made out of band).
  ingress = var.ui_origin_prefix_list_id == null ? [] : [{
    description      = "HTTPS from CloudFront origin-facing servers only"
    from_port        = 443
    to_port          = 443
    protocol         = "tcp"
    cidr_blocks      = []
    ipv6_cidr_blocks = []
    security_groups  = []
    prefix_list_ids  = [var.ui_origin_prefix_list_id]
    self             = false
  }]

  egress {
    description = "HTTPS to the internet"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"] #trivy:ignore:AVD-AWS-0104 outbound HTTPS to third-party APIs
  }

  dynamic "egress" {
    for_each = local.s3_prefix_list_ids
    content {
      description     = "HTTPS to S3 via the gateway endpoint"
      from_port       = 443
      to_port         = 443
      protocol        = "tcp"
      prefix_list_ids = [egress.value]
    }
  }

  tags = { Name = "${var.name_prefix}-runner" }
}

resource "aws_security_group" "batch" {
  name        = "${var.name_prefix}-batch"
  description = "fse Fargate batch tasks: no inbound, HTTPS and NFS outbound"
  vpc_id      = local.vpc_id

  ingress = []

  egress {
    description = "HTTPS to the internet"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"] #trivy:ignore:AVD-AWS-0104 outbound HTTPS to third-party APIs
  }

  dynamic "egress" {
    for_each = local.s3_prefix_list_ids
    content {
      description     = "HTTPS to S3 via the gateway endpoint"
      from_port       = 443
      to_port         = 443
      protocol        = "tcp"
      prefix_list_ids = [egress.value]
    }
  }

  # NFS to EFS mount targets. CIDR (not efs SG) avoids an inline-rule cycle with
  # aws_security_group.efs; only mount targets listen on 2049, and efs ingress
  # still requires this group.
  egress {
    description = "NFS to fse-efs mount targets in the VPC"
    from_port   = 2049
    to_port     = 2049
    protocol    = "tcp"
    cidr_blocks = [local.vpc_cidr_block]
  }

  tags = { Name = "${var.name_prefix}-batch" }
}

# Mount-target SG: NFS from fse-batch only. No egress (targets need none).
resource "aws_security_group" "efs" {
  name        = "${var.name_prefix}-efs"
  description = "fse EFS mount targets: NFS from fse-batch only"
  vpc_id      = local.vpc_id

  ingress = [{
    description      = "NFS from fse-batch"
    from_port        = 2049
    to_port          = 2049
    protocol         = "tcp"
    cidr_blocks      = []
    ipv6_cidr_blocks = []
    prefix_list_ids  = []
    security_groups  = [aws_security_group.batch.id]
    self             = false
  }]

  egress = []

  tags = { Name = "${var.name_prefix}-efs" }
}
