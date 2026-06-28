variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev or prod)"
  type        = string
}

variable "enable_guardrails" {
  description = "Whether to create Bedrock Guardrails resources"
  type        = bool
  default     = true
}

variable "enforce_guardrails" {
  description = "ADR-017: when false (default), all filters run in DETECT/LOG-ONLY mode (input_action/output_action = NONE) — ApplyGuardrail evaluates and returns assessments but does not block or anonymize. When true, filters BLOCK and topic filters are attached. Set per environment."
  type        = bool
  default     = false
}

variable "content_filter_strength" {
  description = "Strength of content filters (LOW, MEDIUM, HIGH)"
  type        = string
  default     = "HIGH"

  validation {
    condition     = contains(["LOW", "MEDIUM", "HIGH"], var.content_filter_strength)
    error_message = "content_filter_strength must be LOW, MEDIUM, or HIGH."
  }
}

variable "blocked_topics" {
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

variable "blocked_words" {
  description = "List of words or phrases to block in inputs and outputs"
  type        = list(string)
  default     = []
}

variable "blocked_pii_types" {
  description = "List of PII entity types to block (e.g., SSN, CREDIT_DEBIT_CARD_NUMBER, PHONE, EMAIL)"
  type        = list(string)
  default     = ["SSN", "CREDIT_DEBIT_CARD_NUMBER", "PHONE", "EMAIL"]
}

variable "guardrail_blocked_message" {
  description = "Message returned when content is blocked by the guardrail"
  type        = string
  default     = "This request was blocked by content safety filters."
}
