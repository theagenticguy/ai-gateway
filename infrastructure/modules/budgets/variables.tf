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

variable "tier_defaults" {
  description = "Tier defaults as a map of tier name to config (E.4)"
  type = map(object({
    rpm            = number
    tokens_per_day = number
    monthly_usd    = number
  }))
  default = {
    sandbox   = { rpm = 20, tokens_per_day = 100000, monthly_usd = 25 }
    standard  = { rpm = 100, tokens_per_day = 500000, monthly_usd = 100 }
    high      = { rpm = 500, tokens_per_day = 5000000, monthly_usd = 1000 }
    unlimited = { rpm = 2000, tokens_per_day = -1, monthly_usd = 10000 }
  }
}

# ── Audit query surface wiring (GET /audit on the budget_admin Lambda) ─────────
# When the Athena audit surface (modules/audit_query) is enabled, root passes
# the workgroup / catalog / results-bucket + the least-priv policy ARN here so
# the budget_admin Lambda's GET /audit route can run Athena queries. Empty by
# default → the route returns a clean 502 "not configured" instead of failing.

variable "audit_athena_workgroup" {
  description = "Athena workgroup for the GET /audit route (from modules/audit_query)"
  type        = string
  default     = ""
}

variable "audit_athena_catalog" {
  description = "S3 Tables child catalog (s3tablescatalog/<bucket>) for the audit query context"
  type        = string
  default     = ""
}

variable "audit_athena_database" {
  description = "Glue database (= S3 Tables namespace) holding audit_events"
  type        = string
  default     = ""
}

variable "audit_query_policy_arn" {
  description = "ARN of the least-priv IAM policy granting the budget_admin Lambda Athena + S3 Tables read (from modules/audit_query)"
  type        = string
  default     = ""
}
