# =============================================================================
# Budgets — Root-level variables for budget and usage tracking
# =============================================================================

variable "enable_budgets" {
  description = "Whether to deploy the budget and usage tracking DynamoDB tables"
  type        = bool
  default     = false
}

