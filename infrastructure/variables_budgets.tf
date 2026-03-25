# =============================================================================
# Budgets — Root-level variables for budget and usage tracking
# =============================================================================

variable "enable_budgets" {
  description = "Whether to deploy the budget and usage tracking DynamoDB tables"
  type        = bool
  default     = false
}

variable "budget_tier_defaults" {
  description = "Default budget limits per tier"
  type = map(object({
    rpm            = number
    tokens_per_day = number
    monthly_usd    = number
  }))
  default = {
    sandbox   = { rpm = 20, tokens_per_day = 100000, monthly_usd = 25 }
    standard  = { rpm = 100, tokens_per_day = 500000, monthly_usd = 100 }
    premium   = { rpm = 500, tokens_per_day = 5000000, monthly_usd = 1000 }
    unlimited = { rpm = 2000, tokens_per_day = -1, monthly_usd = 10000 }
  }
}
