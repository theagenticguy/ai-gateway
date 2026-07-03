# =============================================================================
# Audit Log — Root-level variables for the Firehose-to-S3 audit pipeline
# =============================================================================

variable "enable_audit_log" {
  description = "Enable audit logging via Firehose to S3"
  type        = bool
  default     = false
}

# =============================================================================
# Audit Pipeline (ADR-016/017) — Firehose → Apache Iceberg on S3 Tables
# =============================================================================
# The successor to the legacy Parquet+Glue audit_log pipeline. Kept OFF by
# default: enabling it stands up real S3 Tables + Firehose streams and requires
# the one-time per-Region "Integration with AWS analytics services" toggle
# before Athena can query the Iceberg tables (see modules/audit_query).

variable "enable_audit_pipeline" {
  description = "Enable the Firehose → Iceberg (S3 Tables) audit pipeline (ADR-016/017)"
  type        = bool
  default     = false
}

variable "enable_audit_query" {
  description = "Enable the Athena audit query surface (workgroup + named queries + results bucket)"
  type        = bool
  default     = false
}

variable "athena_results_expiry_days" {
  description = "Lifecycle expiry (days) for objects in the Athena query-results bucket"
  type        = number
  default     = 30
}
