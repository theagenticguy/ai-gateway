variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment (dev or prod)"
  type        = string

  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "Environment must be 'dev' or 'prod'."
  }
}

variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
  default     = "ai-gateway"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "portkey_image" {
  description = "Docker image for the Portkey AI Gateway"
  type        = string
  default     = "portkeyai/gateway:1.15.2"
}

variable "gateway_desired_count" {
  description = "Desired number of gateway ECS tasks"
  type        = number
  default     = 2
}

variable "gateway_cpu" {
  description = "Total CPU units for the gateway ECS task"
  type        = number
  default     = 1024
}

variable "gateway_memory" {
  description = "Total memory (MiB) for the gateway ECS task"
  type        = number
  default     = 2048
}

variable "autoscaling_min_capacity" {
  description = "Minimum number of ECS tasks for autoscaling"
  type        = number
  default     = 2
}

variable "autoscaling_max_capacity" {
  description = "Maximum number of ECS tasks for autoscaling"
  type        = number
  default     = 6
}

variable "certificate_arn" {
  description = "ACM certificate ARN for HTTPS listener"
  type        = string
  default     = ""
}

variable "enable_waf" {
  description = "Whether to enable WAF on the ALB"
  type        = bool
  default     = true
}

# Authentication

variable "cognito_user_pool_id" {
  description = "Cognito User Pool ID for JWT validation. Leave empty to disable JWT auth."
  type        = string
  default     = ""
}

variable "cognito_domain_prefix" {
  description = "Cognito User Pool domain prefix for the token endpoint. Leave empty to skip domain creation."
  type        = string
  default     = ""
}

variable "enable_jwt_auth" {
  description = "Whether to enable ALB JWT validation. Requires certificate_arn and cognito_user_pool_id."
  type        = bool
  default     = false
}
