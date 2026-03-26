# =============================================================================
# Audit Log — Root-level variables for the Firehose-to-S3 audit pipeline
# =============================================================================

variable "enable_audit_log" {
  description = "Enable audit logging via Firehose to S3"
  type        = bool
  default     = false
}
