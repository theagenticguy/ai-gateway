variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev or prod)"
  type        = string
}

variable "aws_region" {
  description = "AWS region for the Athena workgroup + S3 Tables catalog"
  type        = string
}

variable "account_id" {
  description = "AWS account ID (for the s3tablescatalog child-catalog id + ARNs)"
  type        = string
}

variable "enable_audit_query" {
  description = "Whether to create the Athena audit query surface"
  type        = bool
  default     = false
}

variable "audit_table_bucket_name" {
  description = "Name of the S3 Tables audit table bucket (from the audit_pipeline module). Feeds the fully-qualified Athena catalog s3tablescatalog/<name>."
  type        = string
}

variable "audit_table_bucket_arn" {
  description = "ARN of the S3 Tables audit table bucket (for IAM scoping)."
  type        = string
}

variable "namespace" {
  description = "S3 Tables namespace = Glue database for the audit tables (control_plane)."
  type        = string
  default     = "control_plane"
}

variable "audit_table" {
  description = "Iceberg table name for the control-plane audit trail (lowercase)."
  type        = string
  default     = "audit_events"
}

variable "results_expiry_days" {
  description = "Lifecycle expiry (days) for the Athena query-results bucket."
  type        = number
  default     = 30
}
