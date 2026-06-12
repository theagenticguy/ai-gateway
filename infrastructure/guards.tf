# Deploy-time guard rails enforced at plan/apply via lifecycle preconditions.
#
# These fail the plan (rather than silently producing an unsafe deployment) when
# a dangerous combination of flags is set. terraform_data is a no-op resource
# whose only job is to carry the precondition.

# F.4: the OpenAI lane (and the existing Claude lane) must not run on an
# unauthenticated gateway. enable_provider_fallback is what injects the routing
# configs (incl. the gpt-oss configs) into the gateway, so "OpenAI lane enabled"
# is observable as enable_provider_fallback = true. If that is on while JWT auth
# is off, fail the plan — per-team attribution and access control both depend on
# the ALB-verified identity.
resource "terraform_data" "jwt_auth_guard" {
  lifecycle {
    precondition {
      condition     = !var.enable_provider_fallback || var.enable_jwt_auth
      error_message = "enable_jwt_auth must be true when enable_provider_fallback is true: routing the OpenAI/Claude lanes through an unauthenticated gateway has no per-team attribution or access control. Set enable_jwt_auth = true (requires certificate_arn + cognito_user_pool_id)."
    }

    # Secure-by-default: enable_jwt_auth is true by default, but the ALB JWT
    # listener is count-gated on certificate_arn != "" (modules/auth/main.tf) and
    # its issuer/JWKS URLs interpolate cognito_user_pool_id. Without both, a
    # default apply would silently stand up an ALB with NO authenticated listener
    # — worse than an explicit opt-out. Fail the plan loudly instead, and make the
    # error message the turnkey fix. Opt out explicitly with enable_jwt_auth = false.
    precondition {
      condition     = !var.enable_jwt_auth || (var.certificate_arn != "" && var.cognito_user_pool_id != "")
      error_message = "enable_jwt_auth is true (the secure default) but certificate_arn and/or cognito_user_pool_id is empty. JWT validation cannot stand up without both: set certificate_arn (ACM cert for the HTTPS listener) and cognito_user_pool_id (issuer/JWKS source). To run a deliberately unauthenticated gateway (e.g. a local smoke test), set enable_jwt_auth = false explicitly. See docs/admin-guide/security.md."
    }
  }
}
