# ALB JWT Validation Listener (A.2)
#
# When JWT auth is enabled, this raw aws_lb_listener replaces the ALB module's
# simple HTTPS forward listener on port 443. It validates JWTs issued by the
# Cognito User Pool before forwarding to the gateway target group.
#
# The ALB module's HTTPS listener is conditionally excluded when
# enable_jwt_auth = true (see alb.tf line 57).
#
# Ref: ADR-005 (ALB JWT over API Gateway), ADR-007 (provider v6.22+ for JWT)

resource "aws_lb_listener" "https_jwt" {
  count = var.certificate_arn != "" && var.enable_jwt_auth ? 1 : 0

  load_balancer_arn = module.alb.arn
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
    target_group_arn = module.alb.target_groups["gateway"].arn
  }
}
