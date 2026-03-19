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
