output "api_id" {
  description = "ID of the Admin REST API"
  value       = var.enable_admin_api ? aws_api_gateway_rest_api.admin[0].id : ""
}

output "api_url" {
  description = "Invoke URL of the Admin REST API stage"
  value       = var.enable_admin_api ? aws_api_gateway_stage.admin[0].invoke_url : ""
}

output "api_execution_arn" {
  description = "Execution ARN of the Admin REST API (for Lambda permissions)"
  value       = var.enable_admin_api ? aws_api_gateway_rest_api.admin[0].execution_arn : ""
}

output "authorizer_id" {
  description = "ID of the Cognito authorizer (for adding methods later)"
  value       = var.enable_admin_api ? aws_api_gateway_authorizer.cognito[0].id : ""
}

output "root_resource_id" {
  description = "Root resource ID of the REST API (for adding resources later)"
  value       = var.enable_admin_api ? aws_api_gateway_rest_api.admin[0].root_resource_id : ""
}
