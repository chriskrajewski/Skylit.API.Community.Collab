# Shared secret CloudFront sends as X-Origin-Verify; Caddy rejects requests
# without it. The value lives in state through random_password and the
# distribution (decision c); the SSM copy is write-only, so no third copy.
resource "random_password" "origin" {
  length  = 48
  special = false

  keepers = {
    version = var.origin_secret_version
  }
}

resource "aws_ssm_parameter" "origin_secret" {
  name        = "/fse/ui/origin_secret"
  description = "CloudFront to origin shared secret (X-Origin-Verify). Rotate with origin_secret_version."
  type        = "SecureString"

  value_wo         = random_password.origin.result
  value_wo_version = var.origin_secret_version
}
