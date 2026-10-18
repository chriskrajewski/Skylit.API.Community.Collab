data "aws_cloudfront_cache_policy" "caching_optimized" {
  name = "Managed-CachingOptimized"
}

data "aws_cloudfront_cache_policy" "caching_disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

# The CSP string must equal fse_ui.app.CSP (ui/api/tests/test_infra_csp.py).
resource "aws_cloudfront_response_headers_policy" "ui" {
  name    = "fse-console"
  comment = "fse console security headers"

  security_headers_config {
    strict_transport_security {
      access_control_max_age_sec = 63072000
      include_subdomains         = true
      override                   = true
    }

    content_security_policy {
      content_security_policy = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
      override                = true
    }

    frame_options {
      frame_option = "DENY"
      override     = true
    }

    content_type_options {
      override = true
    }

    referrer_policy {
      referrer_policy = "same-origin"
      override        = true
    }
  }
}

resource "aws_cloudfront_distribution" "ui" {
  enabled         = true
  is_ipv6_enabled = true
  http_version    = "http2and3"
  price_class     = "PriceClass_100"
  comment         = "fse console"
  aliases         = [var.ui_domain]

  origin {
    origin_id   = "origin"
    domain_name = var.origin_domain

    custom_origin_config {
      http_port                = 80
      https_port               = 443
      origin_protocol_policy   = "https-only"
      origin_ssl_protocols     = ["TLSv1.2"]
      origin_read_timeout      = 60
      origin_keepalive_timeout = 5
    }

    custom_header {
      name  = "X-Origin-Verify"
      value = random_password.origin.result
    }
  }

  # API, WebSockets and the SPA shell: never cached.
  default_cache_behavior {
    target_origin_id           = "origin"
    allowed_methods            = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods             = ["GET", "HEAD"]
    cache_policy_id            = data.aws_cloudfront_cache_policy.caching_disabled.id
    origin_request_policy_id   = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.ui.id
    viewer_protocol_policy     = "redirect-to-https"
    compress                   = false
  }

  # Hashed Vite build output.
  ordered_cache_behavior {
    path_pattern               = "/assets/*"
    target_origin_id           = "origin"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    cache_policy_id            = data.aws_cloudfront_cache_policy.caching_optimized.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.ui.id
    viewer_protocol_policy     = "redirect-to-https"
    compress                   = true
  }

  # Do not cache origin errors while the UI restarts (default 10 s).
  dynamic "custom_error_response" {
    for_each = [502, 503, 504]

    content {
      error_code            = custom_error_response.value
      error_caching_min_ttl = 0
    }
  }

  viewer_certificate {
    acm_certificate_arn      = aws_acm_certificate_validation.ui.certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }
}
