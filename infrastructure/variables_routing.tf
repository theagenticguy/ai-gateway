variable "enable_provider_fallback" {
  description = "Whether to enable provider fallback routing. When true, routing configs are injected into the gateway container as environment variables."
  type        = bool
  default     = false
}

variable "default_routing_strategy" {
  description = "Default routing strategy for the gateway. Options: single (one provider), fallback (try next on failure), loadbalance (weighted distribution)."
  type        = string
  default     = "single"

  validation {
    condition     = contains(["single", "fallback", "loadbalance"], var.default_routing_strategy)
    error_message = "default_routing_strategy must be one of: single, fallback, loadbalance."
  }
}

variable "routing_configs" {
  description = "Map of named routing configurations as JSON strings. Keys are config names (e.g. 'anthropic', 'openai'), values are Portkey-compatible routing JSON."
  type        = map(string)
  default     = {}
}
