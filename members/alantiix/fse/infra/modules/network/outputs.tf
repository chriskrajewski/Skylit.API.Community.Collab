output "vpc_id" {
  value = local.vpc_id
}

output "public_subnet_ids" {
  value = local.subnet_ids
}

output "public_subnet_azs" {
  value = local.subnet_azs
}

output "runner_security_group_id" {
  value = aws_security_group.runner.id
}

output "batch_security_group_id" {
  value = aws_security_group.batch.id
}

output "efs_security_group_id" {
  description = "Security group on EFS mount targets (NFS from fse-batch only)."
  value       = aws_security_group.efs.id
}

output "security_group_ingress_counts" {
  description = "Ingress rule count per security group (0, or 1 for the runner when enable_ui; efs is 1)."
  value = {
    runner = length(aws_security_group.runner.ingress)
    batch  = length(aws_security_group.batch.ingress)
    efs    = length(aws_security_group.efs.ingress)
  }
}

output "runner_ingress" {
  description = "Runner security group ingress rules (empty, or the CloudFront rule when enable_ui)."
  value       = aws_security_group.runner.ingress
}

output "default_route_nat_gateway_id" {
  description = "NAT gateway on the default route the module creates. null: internet gateway, or an existing VPC (no route is created)."
  value       = local.create ? aws_route.public_default[0].nat_gateway_id : null
}

output "s3_endpoint_id" {
  description = "The S3 gateway endpoint (dedicated VPC only; null with existing_vpc_id)."
  value       = local.create ? aws_vpc_endpoint.s3[0].id : null
}

output "uses_existing_vpc" {
  value = !local.create
}

output "created_network_resource_counts" {
  description = "How many VPC-level resources the module creates (all 0 with existing_vpc_id)."
  value = {
    vpc               = length(aws_vpc.this)
    subnets           = length(aws_subnet.public)
    internet_gateways = length(aws_internet_gateway.this)
    route_tables      = length(aws_route_table.public)
    routes            = length(aws_route.public_default)
    s3_endpoints      = length(aws_vpc_endpoint.s3)
    default_sg        = length(aws_default_security_group.this)
  }
}
