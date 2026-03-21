output "teams_table_name" {
  description = "Name of the teams DynamoDB table"
  value       = var.enable_team_registration ? aws_dynamodb_table.teams[0].name : null
}

output "teams_table_arn" {
  description = "ARN of the teams DynamoDB table"
  value       = var.enable_team_registration ? aws_dynamodb_table.teams[0].arn : null
}

output "lambda_function_arn" {
  description = "ARN of the team registration Lambda function"
  value       = var.enable_team_registration ? aws_lambda_function.team_registration[0].arn : null
}

output "lambda_function_name" {
  description = "Name of the team registration Lambda function"
  value       = var.enable_team_registration ? aws_lambda_function.team_registration[0].function_name : null
}

output "function_url" {
  description = "Function URL for the team registration API"
  value       = var.enable_team_registration ? aws_lambda_function_url.team_registration[0].function_url : null
}

output "kms_key_arn" {
  description = "ARN of the KMS key used for teams table encryption"
  value       = var.enable_team_registration ? aws_kms_key.teams[0].arn : null
}
