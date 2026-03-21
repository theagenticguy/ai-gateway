# =============================================================================
# Chargeback — Root-level variables for monthly chargeback report pipeline
# =============================================================================

variable "enable_chargeback" {
  description = "Whether to deploy the monthly chargeback report pipeline (requires enable_budgets)"
  type        = bool
  default     = false
}
