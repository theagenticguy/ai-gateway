variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev or prod)"
  type        = string
}

variable "enable_api_foundation" {
  description = "Whether to create the control-plane stage, WAF, token route, and monitoring"
  type        = bool
  default     = false
}

variable "aws_region" {
  description = "AWS region"
  type        = string
}

# ── References to the existing admin_api REST API (modules/admin_api) ──────────

variable "rest_api_id" {
  description = "ID of the admin REST API to layer the stage/route onto"
  type        = string
}

variable "rest_api_root_resource_id" {
  description = "Root resource ID of the admin REST API"
  type        = string
}

variable "rest_api_execution_arn" {
  description = "Execution ARN of the admin REST API (for Lambda permissions)"
  type        = string
}

variable "cognito_authorizer_id" {
  description = "ID of the Cognito authorizer on the admin REST API"
  type        = string
}

# ── AuthN / AuthZ ──────────────────────────────────────────────────────────────

variable "invoke_scope" {
  description = "OAuth scope required to call /auth/token (the invoke scope)"
  type        = string
  default     = "https://gateway.internal/invoke"
}

variable "token_issuer" {
  description = "Issuer claim for minted gateway tokens"
  type        = string
  default     = "https://gateway.internal"
}

variable "cognito_jwks_url" {
  description = "Cognito JWKS URL for admin_token verify mode (empty → authorizer-only)"
  type        = string
  default     = ""
}

variable "cognito_issuer" {
  description = "Cognito issuer URL for admin_token verify mode"
  type        = string
  default     = ""
}

variable "admin_token_package_path" {
  description = "Path to the admin_token Lambda deployment package (zip)"
  type        = string
  default     = ""
}

# ── Audit wiring (modules/audit_pipeline) ──────────────────────────────────────

variable "audit_firehose_stream_name" {
  description = "Name of the audit Firehose stream (gwcore.audit AUDIT_FIREHOSE_STREAM)"
  type        = string
  default     = ""
}

variable "audit_firehose_arn" {
  description = "ARN of the audit Firehose stream (grants admin_token firehose:PutRecord)"
  type        = string
  default     = ""
}

# ── Stage / caching / throttling ───────────────────────────────────────────────

variable "stage_name" {
  description = "Stage name for the control-plane deployment"
  type        = string
  default     = "v1"
}

variable "cache_enabled" {
  description = "Enable the API Gateway method cache for idempotent GET routes"
  type        = bool
  default     = true
}

variable "cache_cluster_size" {
  description = "API Gateway cache cluster size in GB"
  type        = string
  default     = "0.5"
}

variable "cache_ttl_seconds" {
  description = "TTL for cached GET responses"
  type        = number
  default     = 60
}

variable "cached_get_paths" {
  description = "Method paths to cache, e.g. [\"pricing/GET\", \"teams/GET\"]"
  type        = list(string)
  default     = ["pricing/GET"]
}

variable "throttle_rate_limit" {
  description = "Steady-state requests/second for the stage"
  type        = number
  default     = 50
}

variable "throttle_burst_limit" {
  description = "Burst request capacity for the stage"
  type        = number
  default     = 100
}

variable "quota_limit" {
  description = "Per-day request quota for the usage plan"
  type        = number
  default     = 100000
}

# ── WAF ──────────────────────────────────────────────────────────────────────

variable "waf_enabled" {
  description = "Attach a regional WAF Web ACL to the control-plane stage"
  type        = bool
  default     = true
}

variable "waf_rate_limit" {
  description = "WAF rate-based rule limit (requests per 5 min per IP)"
  type        = number
  default     = 2000
}

# ── Monitoring ─────────────────────────────────────────────────────────────────

variable "log_retention_days" {
  description = "Retention for access + Firehose log groups"
  type        = number
  default     = 365
}

variable "alarm_sns_topic_arn" {
  description = "SNS topic for alarm notifications (empty → no notifications)"
  type        = string
  default     = ""
}

variable "alarm_5xx_threshold" {
  description = "5xx count over 5 min that triggers the alarm"
  type        = number
  default     = 5
}

variable "alarm_latency_p99_ms" {
  description = "p99 latency (ms) that triggers the alarm"
  type        = number
  default     = 2000
}

variable "alarm_authz_denial_threshold" {
  description = "Authorization-denial count over 5 min that triggers the alarm"
  type        = number
  default     = 50
}
