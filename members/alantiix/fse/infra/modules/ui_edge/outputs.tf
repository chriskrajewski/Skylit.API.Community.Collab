# Never output client_secret or the origin secret.

locals {
  issuer      = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.ui.id}"
  hosted_auth = "https://${aws_cognito_user_pool_domain.ui.domain}.auth.${var.region}.amazoncognito.com"
}

output "oidc" {
  description = "OIDC endpoints and client id for the console (FSE_UI_OIDC_*)."
  value = {
    issuer        = local.issuer
    client_id     = aws_cognito_user_pool_client.ui.id
    authorize_url = "${local.hosted_auth}/oauth2/authorize"
    token_url     = "${local.hosted_auth}/oauth2/token"
    logout_url    = "${local.hosted_auth}/logout"
    jwks_url      = "${local.issuer}/.well-known/jwks.json"
  }
}

output "user_pool_id" {
  value = aws_cognito_user_pool.ui.id
}

output "user_pool_arn" {
  description = "Scope for the host's cognito-idp:DescribeUserPoolClient."
  value       = aws_cognito_user_pool.ui.arn
}

output "zone_id" {
  description = "Route 53 zone of domain_name (Caddy's DNS-01 ACME record)."
  value       = data.aws_route53_zone.this.zone_id
}

output "origin_domain" {
  value = var.origin_domain
}

output "public_url" {
  value = var.public_url
}

output "origin_secret_parameter_name" {
  value = aws_ssm_parameter.origin_secret.name
}

output "settings" {
  description = "Non-secret edge settings, asserted by tests/ui.tftest.hcl (child resources are not addressable there)."
  value = {
    certificate_domain           = aws_acm_certificate.ui.domain_name
    origin_record                = aws_route53_record.origin.records
    mfa_configuration            = aws_cognito_user_pool.ui.mfa_configuration
    software_token_mfa_enabled   = aws_cognito_user_pool.ui.software_token_mfa_configuration[0].enabled
    allow_admin_create_user_only = aws_cognito_user_pool.ui.admin_create_user_config[0].allow_admin_create_user_only
    deletion_protection          = aws_cognito_user_pool.ui.deletion_protection
    allowed_oauth_flows          = aws_cognito_user_pool_client.ui.allowed_oauth_flows
    generate_secret              = aws_cognito_user_pool_client.ui.generate_secret
    callback_urls                = aws_cognito_user_pool_client.ui.callback_urls
    logout_urls                  = aws_cognito_user_pool_client.ui.logout_urls
    token_validity_units         = aws_cognito_user_pool_client.ui.token_validity_units[0]
    aliases                      = aws_cloudfront_distribution.ui.aliases
    price_class                  = aws_cloudfront_distribution.ui.price_class
    viewer_protocol_policy       = aws_cloudfront_distribution.ui.default_cache_behavior[0].viewer_protocol_policy
    origin_protocol_policy       = one(aws_cloudfront_distribution.ui.origin).custom_origin_config[0].origin_protocol_policy
    origin_ssl_protocols         = one(aws_cloudfront_distribution.ui.origin).custom_origin_config[0].origin_ssl_protocols
  }
}

output "origin_secret_version" {
  description = "value_wo_version of the SSM copy: consumers order after the parameter write."
  value       = aws_ssm_parameter.origin_secret.value_wo_version
}
