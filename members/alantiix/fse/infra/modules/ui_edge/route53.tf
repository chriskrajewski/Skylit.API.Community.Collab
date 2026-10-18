# Assumption: the public zone already exists in this account.
data "aws_route53_zone" "this" {
  name         = var.domain_name
  private_zone = false
}

resource "aws_route53_record" "ui_a" {
  zone_id = data.aws_route53_zone.this.zone_id
  name    = var.ui_domain
  type    = "A"

  alias {
    name                   = aws_cloudfront_distribution.ui.domain_name
    zone_id                = aws_cloudfront_distribution.ui.hosted_zone_id
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "ui_aaaa" {
  zone_id = data.aws_route53_zone.this.zone_id
  name    = var.ui_domain
  type    = "AAAA"

  alias {
    name                   = aws_cloudfront_distribution.ui.domain_name
    zone_id                = aws_cloudfront_distribution.ui.hosted_zone_id
    evaluate_target_health = false
  }
}

# The origin CloudFront connects to. The runner SG admits only CloudFront, so
# hitting this address (or the EIP) directly fails.
resource "aws_route53_record" "origin" {
  zone_id = data.aws_route53_zone.this.zone_id
  name    = var.origin_domain
  type    = "A"
  ttl     = 300
  records = [var.origin_ip]
}
