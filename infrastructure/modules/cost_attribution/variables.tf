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

variable "audit_firehose_stream" {
  description = "Name of the audit Firehose delivery stream. Empty disables audit-record delivery (handler no-ops). Wires F.2 (previously-orphaned audit pipeline)."
  type        = string
  default     = ""
}

variable "pricing_table_name" {
  description = "DynamoDB table for the dynamic pricing overlay. Empty = static PRICING_TABLE only (F.5)."
  type        = string
  default     = ""
}

variable "jwt_auth_enforced" {
  description = "Whether the ALB enforces JWT (mirrors enable_jwt_auth). When false, the handler tags attribution identity unverified-* because the x-amzn-oidc-data header is not trustworthy (F.6, safe via F.4)."
  type        = bool
  default     = false
}
