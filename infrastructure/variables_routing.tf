variable "enable_provider_fallback" {
  description = "Whether the gateway serves the multi-provider OpenAI/Claude lanes. Consumed by the guards.tf precondition that requires JWT auth whenever this is true (an unauthenticated multi-provider gateway has no per-team attribution). ADR-017: routing is rendered into the agentgateway config, not injected as container env vars."
  type        = bool
  default     = false
}
