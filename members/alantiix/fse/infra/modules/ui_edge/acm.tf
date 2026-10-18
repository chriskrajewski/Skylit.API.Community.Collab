# CloudFront only accepts certificates from us-east-1.
resource "aws_acm_certificate" "ui" {
  provider = aws.us_east_1

  domain_name       = var.ui_domain
  validation_method = "DNS"
  key_algorithm     = "RSA_2048"

  lifecycle {
    create_before_destroy = true
  }
}

# Keyed by the domain (known at plan); the certificate has exactly one name.
resource "aws_route53_record" "cert_validation" {
  for_each = { (var.ui_domain) = one(aws_acm_certificate.ui.domain_validation_options) }

  zone_id         = data.aws_route53_zone.this.zone_id
  name            = each.value.resource_record_name
  type            = each.value.resource_record_type
  records         = [each.value.resource_record_value]
  ttl             = 60
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "ui" {
  provider = aws.us_east_1

  certificate_arn         = aws_acm_certificate.ui.arn
  validation_record_fqdns = [for r in aws_route53_record.cert_validation : r.fqdn]
}
