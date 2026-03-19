# =============================================================================
# Authentication — Cognito + ALB JWT Listener
# =============================================================================

# -----------------------------------------------------------------------------
# Cognito User Pool — M2M (Machine-to-Machine) Authentication
# ADR-005: ALB JWT validation with Cognito as IdP
# -----------------------------------------------------------------------------

resource "aws_cognito_user_pool" "gateway" {
  name                = "${var.project_name}-${var.environment}"
  deletion_protection = "ACTIVE"

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  tags = {
    Name = "${var.project_name}-${var.environment}"
  }
}

# -----------------------------------------------------------------------------
# Resource Server — defines custom OAuth scopes for the gateway API
# -----------------------------------------------------------------------------

resource "aws_cognito_resource_server" "gateway" {
  identifier   = "https://gateway.internal"
  name         = "${var.project_name}-${var.environment}"
  user_pool_id = aws_cognito_user_pool.gateway.id

  scope {
    scope_name        = "invoke"
    scope_description = "Invoke gateway API endpoints"
  }

  scope {
    scope_name        = "admin"
    scope_description = "Administer gateway configuration"
  }
}

# -----------------------------------------------------------------------------
# User Pool Client — M2M client_credentials grant
# -----------------------------------------------------------------------------

resource "aws_cognito_user_pool_client" "gateway_m2m" {
  name         = "${var.project_name}-m2m-${var.environment}"
  user_pool_id = aws_cognito_user_pool.gateway.id

  generate_secret                      = true
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["client_credentials"]

  allowed_oauth_scopes = aws_cognito_resource_server.gateway.scope_identifiers

  access_token_validity = 1

  token_validity_units {
    access_token = "hours"
  }

  depends_on = [aws_cognito_resource_server.gateway]
}

# -----------------------------------------------------------------------------
# User Pool Domain — provides the /oauth2/token endpoint
# -----------------------------------------------------------------------------

resource "aws_cognito_user_pool_domain" "gateway" {
  domain       = var.cognito_domain_prefix != "" ? var.cognito_domain_prefix : "${var.project_name}-${var.environment}"
  user_pool_id = aws_cognito_user_pool.gateway.id
}

# -----------------------------------------------------------------------------
# ALB JWT Validation Listener (A.2)
#
# When JWT auth is enabled, this raw aws_lb_listener replaces the ALB module's
# simple HTTPS forward listener on port 443. It validates JWTs issued by the
# Cognito User Pool before forwarding to the gateway target group.
#
# The ALB module's HTTPS listener is conditionally excluded when
# enable_jwt_auth = true (see networking module).
#
# Ref: ADR-005 (ALB JWT over API Gateway), ADR-007 (provider v6.22+ for JWT)
# -----------------------------------------------------------------------------

resource "aws_lb_listener" "https_jwt" {
  count = var.certificate_arn != "" && var.enable_jwt_auth ? 1 : 0

  load_balancer_arn = var.alb_arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  # Action 1: Validate the JWT — returns 401 automatically on invalid tokens
  default_action {
    type  = "jwt-validation"
    order = 1

    jwt_validation {
      issuer        = "https://cognito-idp.${var.aws_region}.amazonaws.com/${var.cognito_user_pool_id}"
      jwks_endpoint = "https://cognito-idp.${var.aws_region}.amazonaws.com/${var.cognito_user_pool_id}/.well-known/jwks.json"

      additional_claim {
        format = "string-array"
        name   = "scope"
        values = ["https://gateway.internal/invoke"]
      }
    }
  }

  # Action 2: Forward valid requests to the gateway target group
  default_action {
    type             = "forward"
    order            = 2
    target_group_arn = var.alb_target_group_gateway_arn
  }
}
