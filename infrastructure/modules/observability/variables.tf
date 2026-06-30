variable "project_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "account_id" {
  type = string
}

# ---------------------------------------------------------------------------
# Dashboard toggles
# ---------------------------------------------------------------------------

variable "enable_cost_widgets" {
  description = "Whether to include cost and budget widgets on the dashboard"
  type        = bool
  default     = false
}

# ---------------------------------------------------------------------------
# SNS / Notifications
# ---------------------------------------------------------------------------

variable "alarm_sns_topic_arns" {
  description = "List of SNS topic ARNs for alarm notifications. If empty, a default topic is created."
  type        = list(string)
  default     = []
}

# ---------------------------------------------------------------------------
# Budget alarm thresholds
# ---------------------------------------------------------------------------

variable "budget_limit_daily_usd" {
  description = "Daily budget limit in USD for the gauge widget and budget alarm"
  type        = number
  default     = 1000
}

variable "budget_alarm_threshold_pct" {
  description = "Percentage of daily budget that triggers the budget utilization alarm"
  type        = number
  default     = 80

  validation {
    condition     = var.budget_alarm_threshold_pct > 0 && var.budget_alarm_threshold_pct <= 100
    error_message = "budget_alarm_threshold_pct must be between 1 and 100."
  }
}

# ---------------------------------------------------------------------------
# Error rate alarm thresholds
# ---------------------------------------------------------------------------

variable "error_rate_threshold_pct" {
  description = "Error rate percentage threshold that triggers the high error rate alarm"
  type        = number
  default     = 5
}

variable "error_rate_evaluation_minutes" {
  description = "Number of 1-minute evaluation periods for the error rate alarm"
  type        = number
  default     = 5
}

# ---------------------------------------------------------------------------
# Latency alarm thresholds
# ---------------------------------------------------------------------------

variable "p99_latency_threshold_ms" {
  description = "P99 latency threshold in milliseconds that triggers the high latency alarm"
  type        = number
  default     = 30000
}

variable "latency_evaluation_minutes" {
  description = "Number of 1-minute evaluation periods for the latency alarm"
  type        = number
  default     = 5
}

# ---------------------------------------------------------------------------
# Provider down alarm thresholds
# ---------------------------------------------------------------------------

variable "provider_down_minutes" {
  description = "Number of consecutive 1-minute periods with zero requests before declaring a provider down"
  type        = number
  default     = 10
}
