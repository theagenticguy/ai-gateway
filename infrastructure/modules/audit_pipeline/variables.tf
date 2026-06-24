variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev or prod)"
  type        = string
}

variable "enable_audit_pipeline" {
  description = "Whether to create the Firehose → Iceberg audit pipeline"
  type        = bool
  default     = false
}

variable "aws_region" {
  description = "AWS region for the audit pipeline"
  type        = string
}

variable "account_id" {
  description = "AWS account ID (for the Glue catalog ARN)"
  type        = string
}

variable "log_retention_days" {
  description = "Retention for the Firehose CloudWatch log group"
  type        = number
  default     = 365
}
