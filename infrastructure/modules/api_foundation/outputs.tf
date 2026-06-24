output "stage_invoke_url" {
  description = "Invoke URL of the control-plane stage"
  value       = var.enable_api_foundation ? aws_api_gateway_stage.control[0].invoke_url : ""
}

output "stage_name" {
  description = "Deployed stage name"
  value       = var.enable_api_foundation ? aws_api_gateway_stage.control[0].stage_name : ""
}

output "token_route" {
  description = "Path of the token-exchange route (gateway refresh --audience target)"
  value       = var.enable_api_foundation ? "/auth/token" : ""
}

output "token_signing_secret_arn" {
  description = "ARN of the HS256 signing secret minted gateway tokens are signed with"
  value       = var.enable_api_foundation ? aws_secretsmanager_secret.token_signing[0].arn : ""
}

output "admin_token_function_name" {
  description = "Name of the admin_token Lambda"
  value       = var.enable_api_foundation ? aws_lambda_function.admin_token[0].function_name : ""
}

output "waf_web_acl_arn" {
  description = "ARN of the control-plane WAF Web ACL (empty if disabled)"
  value       = var.enable_api_foundation && var.waf_enabled ? aws_wafv2_web_acl.control[0].arn : ""
}

output "dashboard_name" {
  description = "CloudWatch dashboard name for the control plane"
  value       = var.enable_api_foundation ? aws_cloudwatch_dashboard.control[0].dashboard_name : ""
}
