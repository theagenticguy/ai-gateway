# =============================================================================
# Admin API — Root-level variables for the admin API Gateway plane
# =============================================================================

variable "enable_admin_api" {
  description = "Enable the API Gateway admin plane (also enables team_registration and routing modules)"
  type        = bool
  default     = false
}
