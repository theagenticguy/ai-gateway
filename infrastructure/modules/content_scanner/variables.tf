variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev or prod)"
  type        = string
}

variable "aws_region" {
  description = "AWS region for the Lambda and Comprehend calls"
  type        = string
}

variable "account_id" {
  description = "AWS account ID (for IAM resource ARNs)"
  type        = string
}

variable "enable_content_scanner" {
  description = "Whether to create content scanner resources"
  type        = bool
  default     = false
}

variable "default_pii_mode" {
  description = "Default PII scan mode when team config is missing (off, detect, redact, block)"
  type        = string
  default     = "detect"

  validation {
    condition     = contains(["off", "detect", "redact", "block"], var.default_pii_mode)
    error_message = "default_pii_mode must be off, detect, redact, or block."
  }
}

variable "default_injection_mode" {
  description = "Default injection scan mode when team config is missing (off, detect, redact, block)"
  type        = string
  default     = "detect"

  validation {
    condition     = contains(["off", "detect", "redact", "block"], var.default_injection_mode)
    error_message = "default_injection_mode must be off, detect, redact, or block."
  }
}

variable "appconfig_path" {
  description = "AppConfig path for Lambda extension (e.g., /applications/ai-gateway/environments/prod/configurations/scanner-config)"
  type        = string
  default     = ""
}

variable "tags" {
  description = "Additional tags to apply to all resources"
  type        = map(string)
  default     = {}
}
