variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev or prod)"
  type        = string
}

variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
}

variable "cognito_domain_prefix" {
  description = "Cognito User Pool domain prefix for the token endpoint"
  type        = string
}

variable "cognito_user_pool_id" {
  description = "Cognito User Pool ID for JWT validation (used in listener rule)"
  type        = string
}

variable "enable_jwt_auth" {
  description = "Whether to enable ALB JWT validation"
  type        = bool
}

variable "certificate_arn" {
  description = "ACM certificate ARN for HTTPS listener"
  type        = string
}

variable "alb_arn" {
  description = "ARN of the Application Load Balancer (from networking module)"
  type        = string
}

variable "alb_target_group_gateway_arn" {
  description = "ARN of the gateway target group (from networking module)"
  type        = string
}

# -----------------------------------------------------------------------------
# Identity Center / SSO Variables (D.1)
# Ref: ADR-013
# -----------------------------------------------------------------------------

variable "identity_providers" {
  description = <<-EOT
    Map of external identity providers to federate with the Cognito User Pool.
    Each key is the provider name (e.g. "IdentityCenter", "Okta").

    Example:
      identity_providers = {
        IdentityCenter = {
          provider_type    = "SAML"
          metadata_url     = "https://portal.sso.us-east-1.amazonaws.com/saml/metadata/..."
          provider_details = {}
          attribute_mapping = {}
        }
        Okta = {
          provider_type    = "OIDC"
          metadata_url     = "https://dev-123456.okta.com"
          provider_details = {
            client_id        = "0oa..."
            client_secret    = "secret"
            authorize_scopes = "openid email profile groups"
          }
          attribute_mapping = {}
        }
      }
  EOT
  type = map(object({
    provider_type     = string
    metadata_url      = string
    provider_details  = map(string)
    attribute_mapping = map(string)
  }))
  default = {}

  validation {
    condition = alltrue([
      for k, v in var.identity_providers : contains(["SAML", "OIDC"], v.provider_type)
    ])
    error_message = "Each identity provider must have provider_type of 'SAML' or 'OIDC'."
  }
}

variable "enable_user_auth" {
  description = "Whether to enable user-facing SSO authentication (authorization_code flow)"
  type        = bool
  default     = false
}

variable "callback_urls" {
  description = "List of allowed callback URLs for the user SSO client"
  type        = list(string)
  default     = ["http://localhost:3000/callback"]
}

variable "logout_urls" {
  description = "List of allowed logout URLs for the user SSO client"
  type        = list(string)
  default     = ["http://localhost:3000/logout"]
}

variable "group_mapping" {
  description = <<-EOT
    Mapping from IdP group names to gateway claims.
    Each key is an IdP group name; the value contains the claims to inject.

    Example:
      group_mapping = {
        "aws-ai-gateway-admins" = {
          team        = "platform"
          org_unit    = "ai-engineering"
          cost_center = "CC-1234"
          tenant_tier = "admin"
        }
        "aws-ml-engineers" = {
          team        = "ml-eng"
          org_unit    = "ai-engineering"
          cost_center = "CC-5678"
          tenant_tier = "standard"
        }
      }
  EOT
  type = map(object({
    team        = string
    org_unit    = string
    cost_center = string
    tenant_tier = string
  }))
  default = {}
}
