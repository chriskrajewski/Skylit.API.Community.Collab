# One operator, invited by an administrator, MFA (TOTP) required.
resource "aws_cognito_user_pool" "ui" {
  name                     = "fse-console"
  user_pool_tier           = "ESSENTIALS"
  deletion_protection      = var.cognito_deletion_protection
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  mfa_configuration        = "ON"

  software_token_mfa_configuration {
    enabled = true
  }

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length                   = 14
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 3
  }

  # Passkeys (WebAuthn) were enabled in the console; Cognito refuses to drop
  # WebAuthn while MFA is required, so Terraform now declares them.
  sign_in_policy {
    allowed_first_auth_factors = ["PASSWORD", "WEB_AUTHN"]
  }
  web_authn_configuration {
    user_verification = "required"
  }
  device_configuration {
    challenge_required_on_new_device      = false
    device_only_remembered_on_user_prompt = true
  }
  # Single operator, so recovery goes through the admin, not email.
  account_recovery_setting {
    recovery_mechanism {
      name     = "admin_only"
      priority = 1
    }
  }

  email_configuration {
    email_sending_account = "COGNITO_DEFAULT"
  }
}

# A prefix domain that does not reveal the account id.
resource "aws_cognito_user_pool_domain" "ui" {
  domain                = "fse-console-${substr(sha1(var.account_id), 0, 8)}"
  user_pool_id          = aws_cognito_user_pool.ui.id
  managed_login_version = 2
}

# client_secret is computed and lands in state (decision c); it is never an
# output. The host reads it with DescribeUserPoolClient at unit start.
resource "aws_cognito_user_pool_client" "ui" {
  name         = "fse-console"
  user_pool_id = aws_cognito_user_pool.ui.id

  generate_secret                      = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["openid", "email"]
  callback_urls                        = ["${var.public_url}/auth/callback"]
  logout_urls                          = ["${var.public_url}/"]
  supported_identity_providers         = ["COGNITO"]
  explicit_auth_flows                  = ["ALLOW_REFRESH_TOKEN_AUTH"]
  prevent_user_existence_errors        = "ENABLED"
  enable_token_revocation              = true

  access_token_validity  = 60
  id_token_validity      = 60
  refresh_token_validity = 1

  # Without units the values are hours, and 60 h exceeds Cognito's 1-day maximum.
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
}

resource "aws_cognito_managed_login_branding" "ui" {
  user_pool_id = aws_cognito_user_pool.ui.id
  client_id    = aws_cognito_user_pool_client.ui.id

  use_cognito_provided_values = true
}

resource "terraform_data" "operator_invite" {
  input = var.operator_invite_version
}

# No password: Cognito mails a temporary one (valid 3 days), so nothing lands
# in state. Bump operator_invite_version to recreate the user and re-invite.
resource "aws_cognito_user" "operator" {
  user_pool_id = aws_cognito_user_pool.ui.id
  username     = var.operator_email

  attributes = {
    email          = var.operator_email
    email_verified = "true"
  }

  desired_delivery_mediums = ["EMAIL"]

  lifecycle {
    replace_triggered_by = [terraform_data.operator_invite]
  }
}
