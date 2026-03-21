output "alb_dns_name" {
  description = "DNS name of the Application Load Balancer"
  value       = module.networking.alb_dns_name
}

output "ecs_cluster_name" {
  description = "Name of the ECS cluster"
  value       = module.compute.ecs_cluster_name
}

output "ecs_service_name" {
  description = "Name of the ECS service"
  value       = module.compute.ecs_service_name
}

output "ecr_repository_url" {
  description = "URL of the ECR repository"
  value       = module.compute.ecr_repository_url
}

output "vpc_id" {
  description = "ID of the VPC"
  value       = module.networking.vpc_id
}

# -----------------------------------------------------------------------------
# Cognito Outputs
# -----------------------------------------------------------------------------

output "cognito_user_pool_id" {
  description = "Cognito User Pool ID"
  value       = module.auth.cognito_user_pool_id
}

output "cognito_user_pool_arn" {
  description = "Cognito User Pool ARN"
  value       = module.auth.cognito_user_pool_arn
}

output "cognito_client_id" {
  description = "Cognito M2M client ID"
  value       = module.auth.cognito_client_id
}

output "cognito_token_endpoint" {
  description = "Cognito token endpoint URL"
  value       = module.auth.cognito_token_endpoint
}

# User SSO Outputs (D.1)
# -----------------------------------------------------------------------------

output "user_client_id" {
  description = "Cognito User SSO client ID (empty if user auth is disabled)"
  value       = module.auth.user_client_id
}

output "hosted_ui_url" {
  description = "Cognito Hosted UI URL for SSO login (empty if user auth is disabled)"
  value       = module.auth.hosted_ui_url
}

# Multi-Client Outputs
# -----------------------------------------------------------------------------

output "team_client_ids" {
  description = "Map of team name to Cognito app client ID (empty if no client_configs)"
  value       = length(module.clients) > 0 ? module.clients[0].client_ids : {}
}

output "team_client_secrets" {
  description = "Map of team name to Cognito app client secret (empty if no client_configs)"
  sensitive   = true
  value       = length(module.clients) > 0 ? module.clients[0].client_secrets : {}
}

# -----------------------------------------------------------------------------
# Guardrails Outputs
# -----------------------------------------------------------------------------

output "guardrail_id" {
  description = "Bedrock Guardrail ID"
  value       = module.guardrails.guardrail_id
}

output "guardrail_arn" {
  description = "Bedrock Guardrail ARN"
  value       = module.guardrails.guardrail_arn
}

# -----------------------------------------------------------------------------
# Budget Outputs
# -----------------------------------------------------------------------------

output "budgets_table_name" {
  description = "Name of the budgets DynamoDB table"
  value       = length(module.budgets) > 0 ? module.budgets[0].budgets_table_name : null
}

output "budgets_table_arn" {
  description = "ARN of the budgets DynamoDB table"
  value       = length(module.budgets) > 0 ? module.budgets[0].budgets_table_arn : null
}

output "usage_table_name" {
  description = "Name of the usage DynamoDB table"
  value       = length(module.budgets) > 0 ? module.budgets[0].usage_table_name : null
}

output "usage_table_arn" {
  description = "ARN of the usage DynamoDB table"
  value       = length(module.budgets) > 0 ? module.budgets[0].usage_table_arn : null
}

output "budgets_kms_key_arn" {
  description = "ARN of the KMS key used for budget table encryption"
  value       = length(module.budgets) > 0 ? module.budgets[0].kms_key_arn : null
}

output "budgets_lambda_policy_arn" {
  description = "ARN of the IAM policy for Lambda access to budget tables"
  value       = length(module.budgets) > 0 ? module.budgets[0].lambda_policy_arn : null
}
