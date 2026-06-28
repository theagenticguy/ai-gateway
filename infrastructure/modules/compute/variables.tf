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

variable "gateway_image" {
  description = "Docker image for the agentgateway data-plane proxy (ADR-017)"
  type        = string
}

variable "gateway_desired_count" {
  description = "Desired number of gateway ECS tasks"
  type        = number
}

variable "gateway_cpu" {
  description = "Total CPU units for the gateway ECS task"
  type        = number
}

variable "gateway_memory" {
  description = "Total memory (MiB) for the gateway ECS task"
  type        = number
}

variable "autoscaling_min_capacity" {
  description = "Minimum number of ECS tasks for autoscaling"
  type        = number
}

variable "autoscaling_max_capacity" {
  description = "Maximum number of ECS tasks for autoscaling"
  type        = number
}

variable "account_id" {
  description = "AWS account ID (for IAM and KMS policies)"
  type        = string
}

variable "private_subnets" {
  description = "List of private subnet IDs (from networking module)"
  type        = list(string)
}

variable "alb_security_group_id" {
  description = "Security group ID of the ALB (from networking module)"
  type        = string
}

variable "alb_target_group_gateway_arn" {
  description = "ARN of the gateway target group (from networking module)"
  type        = string
}

variable "alb_arn_suffix" {
  description = "ARN suffix of the ALB (from networking module, for autoscaling resource labels)"
  type        = string
}

variable "alb_target_group_gateway_arn_suffix" {
  description = "ARN suffix of the gateway target group (from networking module, for autoscaling resource labels)"
  type        = string
}

variable "gateway_log_group_name" {
  description = "Name of the gateway CloudWatch log group (from observability module)"
  type        = string
}

variable "otel_log_group_name" {
  description = "Name of the OpenTelemetry CloudWatch log group (from observability module)"
  type        = string
}

variable "otel_config_content" {
  description = "Content of the OpenTelemetry Collector configuration YAML"
  type        = string
}

# ADR-017: routing now lives in the rendered agentgateway config, not in
# Portkey env configs; the LLM response cache (Redis) is removed entirely. The
# portkey_routing_configs / cache_enabled / redis_url variables were dropped.

variable "budget_enforcement_webhook_url" {
  description = "Function URL for the budget enforcement Lambda (agentgateway promptGuard webhook)"
  type        = string
  default     = ""
}

variable "content_scanner_webhook_url" {
  description = "Function URL for the content scanner Lambda (agentgateway promptGuard webhook)"
  type        = string
  default     = ""
}
