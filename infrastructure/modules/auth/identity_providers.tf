# -----------------------------------------------------------------------------
# Identity Providers — SAML 2.0 + OIDC federation
#
# Allows Cognito User Pool to federate with external IdPs such as
# AWS Identity Center (SAML) or Okta/Entra ID (OIDC).
#
# Ref: ADR-013 (Identity Center SAML/OIDC Federation)
# -----------------------------------------------------------------------------

locals {
  saml_providers = {
    for k, v in var.identity_providers : k => v if v.provider_type == "SAML"
  }
  oidc_providers = {
    for k, v in var.identity_providers : k => v if v.provider_type == "OIDC"
  }
}

resource "aws_cognito_identity_provider" "saml" {
  for_each = var.enable_user_auth ? local.saml_providers : {}

  user_pool_id  = aws_cognito_user_pool.gateway.id
  provider_name = each.key
  provider_type = "SAML"

  provider_details = {
    MetadataURL             = each.value.metadata_url
    SLOEnabled              = tostring(lookup(each.value.provider_details, "slo_enabled", false))
    IDPSignout              = tostring(lookup(each.value.provider_details, "idp_signout", false))
    RequestSigningAlgorithm = lookup(each.value.provider_details, "request_signing_algorithm", "rsa-sha256")
  }

  attribute_mapping = merge(
    {
      email    = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress"
      name     = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name"
      username = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier"
    },
    each.value.attribute_mapping,
  )

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_cognito_identity_provider" "oidc" {
  for_each = var.enable_user_auth ? local.oidc_providers : {}

  user_pool_id  = aws_cognito_user_pool.gateway.id
  provider_name = each.key
  provider_type = "OIDC"

  provider_details = {
    oidc_issuer                   = each.value.metadata_url
    client_id                     = lookup(each.value.provider_details, "client_id", "")
    client_secret                 = lookup(each.value.provider_details, "client_secret", "")
    attributes_request_method     = lookup(each.value.provider_details, "attributes_request_method", "GET")
    authorize_scopes              = lookup(each.value.provider_details, "authorize_scopes", "openid email profile")
    attributes_url_add_attributes = tostring(lookup(each.value.provider_details, "attributes_url_add_attributes", true))
  }

  attribute_mapping = merge(
    {
      email    = "email"
      name     = "name"
      username = "sub"
    },
    each.value.attribute_mapping,
  )

  lifecycle {
    create_before_destroy = true
  }
}
