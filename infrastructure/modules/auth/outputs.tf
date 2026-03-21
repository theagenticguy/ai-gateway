output "cognito_user_pool_id" {
  description = "Cognito User Pool ID"
  value       = aws_cognito_user_pool.gateway.id
}

output "cognito_user_pool_arn" {
  description = "Cognito User Pool ARN"
  value       = aws_cognito_user_pool.gateway.arn
}

output "cognito_client_id" {
  description = "Cognito M2M client ID"
  value       = aws_cognito_user_pool_client.gateway_m2m.id
}

output "cognito_token_endpoint" {
  description = "Cognito token endpoint URL"
  value       = "https://${aws_cognito_user_pool_domain.gateway.domain}.auth.${var.aws_region}.amazoncognito.com/oauth2/token"
}

output "resource_server_scope_identifiers" {
  description = "List of fully-qualified scope identifiers from the Cognito resource server"
  value       = aws_cognito_resource_server.gateway.scope_identifiers
}

# -----------------------------------------------------------------------------
# User SSO Outputs (D.1)
# -----------------------------------------------------------------------------

output "user_client_id" {
  description = "Cognito User SSO client ID (empty if user auth is disabled)"
  value       = var.enable_user_auth ? aws_cognito_user_pool_client.user_sso[0].id : ""
}

output "hosted_ui_url" {
  description = "Cognito Hosted UI URL for SSO login (empty if user auth is disabled)"
  value = var.enable_user_auth ? join("", [
    "https://${aws_cognito_user_pool_domain.gateway.domain}.auth.${var.aws_region}.amazoncognito.com",
    "/login?client_id=${aws_cognito_user_pool_client.user_sso[0].id}",
    "&response_type=code&scope=openid+email+profile",
    "&redirect_uri=${urlencode(var.callback_urls[0])}",
  ]) : ""
}
