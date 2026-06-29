# =============================================================================
# AppConfig — Root-level variables for feature flag management
# =============================================================================

variable "enable_appconfig" {
  description = "Enable AWS AppConfig for feature flag and dynamic configuration management"
  type        = bool
  default     = false
}
