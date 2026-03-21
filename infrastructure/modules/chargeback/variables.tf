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

variable "account_id" {
  description = "AWS account ID"
  type        = string
}

variable "enable_chargeback" {
  description = "Whether to deploy the chargeback report pipeline"
  type        = bool
  default     = false
}

variable "usage_table_name" {
  description = "Name of the DynamoDB usage tracking table"
  type        = string
}

variable "usage_table_arn" {
  description = "ARN of the DynamoDB usage tracking table"
  type        = string
}

variable "budgets_table_name" {
  description = "Name of the DynamoDB budgets table"
  type        = string
}

variable "budgets_table_arn" {
  description = "ARN of the DynamoDB budgets table"
  type        = string
}

variable "sns_topic_arn" {
  description = "SNS topic ARN for report notifications"
  type        = string
}

variable "tags" {
  description = "Additional tags to apply to all resources"
  type        = map(string)
  default     = {}
}
