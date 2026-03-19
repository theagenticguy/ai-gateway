output "alb_dns_name" {
  description = "DNS name of the Application Load Balancer"
  value       = module.alb.dns_name
}

output "ecs_cluster_name" {
  description = "Name of the ECS cluster"
  value       = module.ecs_cluster.name
}

output "ecs_service_name" {
  description = "Name of the ECS service"
  value       = module.ecs_service.name
}

output "ecr_repository_url" {
  description = "URL of the ECR repository"
  value       = aws_ecr_repository.gateway.repository_url
}

output "vpc_id" {
  description = "ID of the VPC"
  value       = module.vpc.vpc_id
}

# -----------------------------------------------------------------------------
# Cognito Outputs
# -----------------------------------------------------------------------------

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
