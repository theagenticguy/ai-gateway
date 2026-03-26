# =============================================================================
# AppConfig — Root-level variables for feature flag management
# =============================================================================

variable "enable_appconfig" {
  description = "Enable AWS AppConfig for feature flag management (scanner toggle)"
  type        = bool
  default     = false
}
