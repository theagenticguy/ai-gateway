variable "enable_appconfig" {
  description = "Whether to create AppConfig resources for scanner feature flags"
  type        = bool
  default     = false
}

variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev or prod)"
  type        = string
}

variable "rollback_alarm_arn" {
  description = "CloudWatch alarm ARN for automatic rollback monitoring (leave empty to disable)"
  type        = string
  default     = ""
}

variable "initial_scanner_config" {
  description = "Initial JSON configuration for the scanner feature flag"
  type        = string
  default     = <<-EOT
    {
      "enabled": false,
      "timeout_ms": 5000,
      "deny_on_block": true,
      "team_overrides": {}
    }
  EOT
}

variable "tags" {
  description = "Additional tags to apply to all resources"
  type        = map(string)
  default     = {}
}
