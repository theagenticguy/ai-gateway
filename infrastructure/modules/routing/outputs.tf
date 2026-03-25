output "routing_configs_table_name" {
  description = "Name of the routing configs DynamoDB table"
  value       = var.enable_routing_api ? aws_dynamodb_table.routing_configs[0].name : null
}

output "routing_configs_table_arn" {
  description = "ARN of the routing configs DynamoDB table"
  value       = var.enable_routing_api ? aws_dynamodb_table.routing_configs[0].arn : null
}

output "kms_key_arn" {
  description = "ARN of the KMS key used for routing config encryption"
  value       = var.enable_routing_api ? aws_kms_key.routing[0].arn : null
}

output "lambda_function_arn" {
  description = "ARN of the routing config Lambda function"
  value       = var.enable_routing_api ? aws_lambda_function.routing_config[0].arn : null
}

output "lambda_function_name" {
  description = "Name of the routing config Lambda function"
  value       = var.enable_routing_api ? aws_lambda_function.routing_config[0].function_name : null
}

output "function_url" {
  description = "Function URL for the routing config API"
  value       = var.enable_routing_api ? aws_lambda_function_url.routing_config[0].function_url : null
}
