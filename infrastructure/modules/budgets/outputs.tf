output "budgets_table_name" {
  description = "Name of the budgets DynamoDB table"
  value       = var.enable_budgets ? aws_dynamodb_table.budgets[0].name : null
}

output "budgets_table_arn" {
  description = "ARN of the budgets DynamoDB table"
  value       = var.enable_budgets ? aws_dynamodb_table.budgets[0].arn : null
}

output "usage_table_name" {
  description = "Name of the usage DynamoDB table"
  value       = var.enable_budgets ? aws_dynamodb_table.usage[0].name : null
}

output "usage_table_arn" {
  description = "ARN of the usage DynamoDB table"
  value       = var.enable_budgets ? aws_dynamodb_table.usage[0].arn : null
}

output "kms_key_arn" {
  description = "ARN of the KMS key used for budget table encryption"
  value       = var.enable_budgets ? aws_kms_key.budgets[0].arn : null
}

output "lambda_policy_arn" {
  description = "ARN of the IAM policy for Lambda access to budget tables"
  value       = var.enable_budgets ? aws_iam_policy.budget_lambda[0].arn : null
}
