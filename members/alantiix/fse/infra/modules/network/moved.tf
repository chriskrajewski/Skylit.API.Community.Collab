# The dedicated-VPC resources gained count = 1 when existing_vpc_id was added;
# these keep an already-applied network in place instead of replacing it.
moved {
  from = aws_vpc.this
  to   = aws_vpc.this[0]
}

moved {
  from = aws_default_security_group.this
  to   = aws_default_security_group.this[0]
}

moved {
  from = aws_internet_gateway.this
  to   = aws_internet_gateway.this[0]
}

moved {
  from = aws_route_table.public
  to   = aws_route_table.public[0]
}

moved {
  from = aws_route.public_default
  to   = aws_route.public_default[0]
}

moved {
  from = aws_vpc_endpoint.s3
  to   = aws_vpc_endpoint.s3[0]
}
