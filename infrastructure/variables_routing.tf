variable "enable_provider_fallback" {
  description = "Whether to enable provider fallback routing. When true, routing configs are injected into the gateway container as environment variables."
  type        = bool
  default     = false
}

variable "routing_configs" {
  description = "Map of named routing configurations as JSON strings. Keys are config names (e.g. 'anthropic', 'openai'), values are Portkey-compatible routing JSON."
  type        = map(string)
  default     = {}
}
