variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev or prod)"
  type        = string
}

variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
}

variable "cognito_domain_prefix" {
  description = "Cognito User Pool domain prefix for the token endpoint"
  type        = string
}

variable "cognito_user_pool_id" {
  description = "Cognito User Pool ID for JWT validation (used in listener rule)"
  type        = string
}

variable "enable_jwt_auth" {
  description = "Whether to enable ALB JWT validation"
  type        = bool
}

variable "certificate_arn" {
  description = "ACM certificate ARN for HTTPS listener"
  type        = string
}

variable "alb_arn" {
  description = "ARN of the Application Load Balancer (from networking module)"
  type        = string
}

variable "alb_target_group_gateway_arn" {
  description = "ARN of the gateway target group (from networking module)"
  type        = string
}
