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
