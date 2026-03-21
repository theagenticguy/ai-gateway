variable "project_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "gateway_log_group_name" {
  type = string
}

variable "gateway_log_group_arn" {
  type = string
}

variable "enable_cost_attribution" {
  type    = bool
  default = true
}

variable "account_id" {
  type = string
}

variable "usage_table" {
  description = "DynamoDB table name for accumulated usage records"
  type        = string
  default     = ""
}

variable "budgets_table" {
  description = "DynamoDB table name for budget configurations"
  type        = string
  default     = ""
}

variable "budget_alerts_sns_topic_arn" {
  description = "ARN of the SNS topic for budget alerts (E.6)"
  type        = string
  default     = ""
}
