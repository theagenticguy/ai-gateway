# -----------------------------------------------------------------------------
# User App Client — authorization_code flow for interactive SSO
#
# Public client (no secret) for browser-based authorization_code + PKCE.
# Count-gated on var.enable_user_auth so it is only created when user
# authentication is explicitly enabled.
#
# Ref: ADR-013 (Identity Center SAML/OIDC Federation)
# -----------------------------------------------------------------------------

locals {
  # Build supported IdP list: always include COGNITO, plus all configured IdPs
  configured_idp_names = [for k, _ in var.identity_providers : k]
  supported_idps       = var.enable_user_auth ? concat(["COGNITO"], local.configured_idp_names) : []
}

resource "aws_cognito_user_pool_client" "user_sso" {
  count = var.enable_user_auth ? 1 : 0

  name         = "${var.project_name}-user-sso-${var.environment}"
  user_pool_id = aws_cognito_user_pool.gateway.id

  # Public client — no secret (PKCE enforced by Cognito for authorization_code)
  generate_secret = false

  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]

  supported_identity_providers = local.supported_idps

  callback_urls = var.callback_urls
  logout_urls   = var.logout_urls

  # Token validity
  access_token_validity  = 1
  id_token_validity      = 1
  refresh_token_validity = 30

  token_validity_units {
    access_token  = "hours"
    id_token      = "hours"
    refresh_token = "days"
  }

  # Prevent user-existence errors from leaking
  prevent_user_existence_errors = "ENABLED"

  depends_on = [
    aws_cognito_identity_provider.saml,
    aws_cognito_identity_provider.oidc,
  ]
}
