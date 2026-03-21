variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev or prod)"
  type        = string
}

variable "enable_budgets" {
  description = "Whether to create budget DynamoDB tables and supporting resources"
  type        = bool
  default     = true
}

variable "tags" {
  description = "Additional tags to apply to all resources"
  type        = map(string)
  default     = {}
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "account_id" {
  description = "AWS account ID"
  type        = string
  default     = ""
}

variable "enable_budget_enforcement" {
  description = "Whether to deploy the budget enforcement Lambda"
  type        = bool
  default     = true
}

variable "budgets_table" {
  description = "DynamoDB table name for budget configurations"
  type        = string
  default     = "gateway-budgets"
}

variable "usage_table" {
  description = "DynamoDB table name for accumulated usage records"
  type        = string
  default     = "gateway-usage"
}

variable "tier_default_free" {
  type    = string
  default = "10"
}

variable "tier_default_standard" {
  type    = string
  default = "1000"
}

variable "tier_default_premium" {
  type    = string
  default = "10000"
}

variable "tier_default_enterprise" {
  type    = string
  default = "100000"
}
