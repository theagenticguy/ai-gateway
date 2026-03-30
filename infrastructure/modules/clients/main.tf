terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.22"
    }
  }
}

# =============================================================================
# Clients — Per-team Cognito App Clients for Multi-Tenant M2M Access
#
# Creates isolated Cognito user pool clients from a configurable map.
# Each team gets its own client_credentials grant with scoped OAuth access.
# =============================================================================

resource "aws_cognito_user_pool_client" "team" {
  # checkov:skip=CKV_TF_1: Using local modules, not registry
  for_each = var.client_configs

  name         = "${var.project_name}-${each.key}-${var.environment}"
  user_pool_id = var.user_pool_id

  generate_secret                      = true
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["client_credentials"]

  allowed_oauth_scopes = [
    for scope in each.value.allowed_scopes :
    contains(var.resource_server_scope_identifiers, scope) ? scope : scope
  ]

  access_token_validity = 1

  token_validity_units {
    access_token = "hours"
  }
}
