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
  default     = "us-east-1"
}

variable "enable_team_registration" {
  description = "Whether to create the team registration Lambda and DynamoDB table"
  type        = bool
  default     = true
}

# ── Cognito ──────────────────────────────────────────────────────────────────

variable "cognito_user_pool_id" {
  description = "Cognito User Pool ID for creating team app clients"
  type        = string
}

variable "cognito_user_pool_arn" {
  description = "Cognito User Pool ARN (for IAM policy)"
  type        = string
}

variable "cognito_token_endpoint" {
  description = "Cognito token endpoint URL to return in registration responses"
  type        = string
}

variable "resource_server_identifier" {
  description = "Cognito resource server identifier (e.g. https://gateway.internal)"
  type        = string
  default     = "https://gateway.internal"
}

# ── Budget tables (from budgets module) ──────────────────────────────────────

variable "budgets_table_name" {
  description = "Name of the budgets DynamoDB table"
  type        = string
  default     = "gateway-budgets"
}

variable "budgets_table_arn" {
  description = "ARN of the budgets DynamoDB table (for IAM policy)"
  type        = string
  default     = ""
}

variable "usage_table_name" {
  description = "Name of the usage DynamoDB table"
  type        = string
  default     = "gateway-usage"
}

variable "usage_table_arn" {
  description = "ARN of the usage DynamoDB table (for IAM policy)"
  type        = string
  default     = ""
}

# ── Tags ─────────────────────────────────────────────────────────────────────

variable "tags" {
  description = "Additional tags to apply to all resources"
  type        = map(string)
  default     = {}
}
