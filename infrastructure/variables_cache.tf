# =============================================================================
# Cache Variables
# =============================================================================

variable "enable_cache" {
  description = "Whether to deploy an ElastiCache Redis cluster for response caching"
  type        = bool
  default     = false
}

variable "cache_node_type" {
  description = "ElastiCache node instance type"
  type        = string
  default     = "cache.t4g.micro"
}
