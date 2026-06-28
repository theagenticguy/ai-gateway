# =============================================================================
# Cache Variables — DECOMMISSIONED (ADR-017, supersedes ADR-012)
# =============================================================================
# The LLM response cache is removed with the agentgateway data-plane swap.
# These variables are retained only so existing tfvars/automation that still
# set them do not error; enable_cache is forced false via validation and the
# cache module is no longer instantiated (see main.tf).

variable "enable_cache" {
  description = "DECOMMISSIONED (ADR-017): LLM response cache removed. Must be false."
  type        = bool
  default     = false

  validation {
    condition     = var.enable_cache == false
    error_message = "enable_cache is decommissioned (ADR-017). The LLM response cache was removed with the agentgateway data-plane swap."
  }
}

variable "cache_node_type" {
  description = "DECOMMISSIONED (ADR-017): unused; the cache module is no longer instantiated."
  type        = string
  default     = "cache.t4g.micro"
}
