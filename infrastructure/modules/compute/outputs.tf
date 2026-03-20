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

output "ecs_security_group_id" {
  description = "Security group ID of the ECS service"
  value       = module.ecs_service.security_group_id
}
