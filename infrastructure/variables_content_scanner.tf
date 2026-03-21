# =============================================================================
# Content Scanner — Root-level variables for PII redaction and injection detection
# =============================================================================

variable "enable_content_scanner" {
  description = "Whether to deploy the content scanner Lambda (PII redaction + injection detection)"
  type        = bool
  default     = false
}

variable "content_scanner_default_pii_mode" {
  description = "Default PII scan mode when team config is missing (off, detect, redact, block)"
  type        = string
  default     = "detect"

  validation {
    condition     = contains(["off", "detect", "redact", "block"], var.content_scanner_default_pii_mode)
    error_message = "content_scanner_default_pii_mode must be off, detect, redact, or block."
  }
}

variable "content_scanner_default_injection_mode" {
  description = "Default injection scan mode when team config is missing (off, detect, redact, block)"
  type        = string
  default     = "detect"

  validation {
    condition     = contains(["off", "detect", "redact", "block"], var.content_scanner_default_injection_mode)
    error_message = "content_scanner_default_injection_mode must be off, detect, redact, or block."
  }
}
