output "vpc_id" {
  description = "ID of the VPC"
  value       = module.vpc.vpc_id
}

output "vpc_cidr_block" {
  description = "CIDR block of the VPC"
  value       = module.vpc.vpc_cidr_block
}

output "public_subnets" {
  description = "List of public subnet IDs"
  value       = module.vpc.public_subnets
}

output "private_subnets" {
  description = "List of private subnet IDs"
  value       = module.vpc.private_subnets
}

output "private_subnets_cidr_blocks" {
  description = "List of private subnet CIDR blocks"
  value       = module.vpc.private_subnets_cidr_blocks
}

output "alb_arn" {
  description = "ARN of the Application Load Balancer"
  value       = module.alb.arn
}

output "alb_dns_name" {
  description = "DNS name of the Application Load Balancer"
  value       = module.alb.dns_name
}

output "alb_arn_suffix" {
  description = "ARN suffix of the ALB (for autoscaling resource labels)"
  value       = module.alb.arn_suffix
}

output "alb_security_group_id" {
  description = "Security group ID of the ALB"
  value       = module.alb.security_group_id
}

output "alb_target_group_gateway_arn" {
  description = "ARN of the gateway target group"
  value       = module.alb.target_groups["gateway"].arn
}

output "alb_target_group_gateway_arn_suffix" {
  description = "ARN suffix of the gateway target group (for autoscaling resource labels)"
  value       = module.alb.target_groups["gateway"].arn_suffix
}
