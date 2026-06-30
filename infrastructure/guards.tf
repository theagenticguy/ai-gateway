# Deploy-time guard rails enforced at plan/apply via lifecycle preconditions.
#
# These fail the plan (rather than silently producing an unsafe deployment) when
# a dangerous combination of flags is set. terraform_data is a no-op resource
# whose only job is to carry the precondition.

# F.4: the OpenAI lane (and the existing Claude lane) must not run on an
# unauthenticated gateway. enable_provider_fallback signals that the
# multi-provider lanes are served (ADR-017: the lanes are rendered into the
# agentgateway config), so "OpenAI lane enabled" is observable as
# enable_provider_fallback = true. If that is on while JWT auth is off, fail the
# plan — per-team attribution and access control both depend on the
# ALB-verified identity.
resource "terraform_data" "jwt_auth_guard" {
  lifecycle {
    precondition {
      condition     = !var.enable_provider_fallback || var.enable_jwt_auth
      error_message = "enable_jwt_auth must be true when enable_provider_fallback is true: routing the OpenAI/Claude lanes through an unauthenticated gateway has no per-team attribution or access control. Set enable_jwt_auth = true (requires certificate_arn + cognito_user_pool_id)."
    }
  }
}
