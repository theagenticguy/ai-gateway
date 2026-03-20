# =============================================================================
# Guardrails — Root-level variables for Bedrock Guardrails configuration
# =============================================================================

variable "enable_guardrails" {
  description = "Whether to enable Bedrock Guardrails for content safety filtering"
  type        = bool
  default     = false
}

variable "guardrails_blocked_topics" {
  description = "List of topics to block, each with a name and definition"
  type = list(object({
    name       = string
    definition = string
    examples   = optional(list(string), [])
  }))
  default = [
    {
      name       = "competitor_products"
      definition = "Discussions or recommendations about competitor products and services."
      examples   = ["Tell me about competing AI platforms"]
    },
    {
      name       = "internal_financials"
      definition = "Internal financial data, revenue figures, or unreleased business metrics."
      examples   = ["What is the company revenue this quarter"]
    }
  ]
}

variable "guardrails_blocked_words" {
  description = "List of words or phrases to block in inputs and outputs"
  type        = list(string)
  default     = []
}

variable "guardrails_content_filter_strength" {
  description = "Strength of content filters (LOW, MEDIUM, HIGH)"
  type        = string
  default     = "HIGH"

  validation {
    condition     = contains(["LOW", "MEDIUM", "HIGH"], var.guardrails_content_filter_strength)
    error_message = "guardrails_content_filter_strength must be LOW, MEDIUM, or HIGH."
  }
}
