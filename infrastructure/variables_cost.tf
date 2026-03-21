variable "enable_cost_attribution" {
  description = "Whether to deploy the cost attribution Lambda pipeline"
  type        = bool
  default     = false
}

# =============================================================================
# Observability / Alarm Variables
# =============================================================================

variable "alarm_sns_topic_arns" {
  description = "List of SNS topic ARNs for CloudWatch alarm notifications. If empty, a default topic is created."
  type        = list(string)
  default     = []
}

variable "budget_limit_daily_usd" {
  description = "Daily budget limit in USD for dashboard gauge and budget alarm"
  type        = number
  default     = 1000
}

variable "budget_alarm_threshold_pct" {
  description = "Percentage of daily budget that triggers the budget utilization alarm"
  type        = number
  default     = 80
}

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

variable "provider_down_minutes" {
  description = "Number of consecutive 1-minute periods with zero requests before declaring a provider down"
  type        = number
  default     = 10
}
