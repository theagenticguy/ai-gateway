variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev or prod)"
  type        = string
}

variable "enable_audit_log" {
  description = "Whether to create the audit log pipeline (Firehose, S3, Glue)"
  type        = bool
  default     = false
}

variable "aws_region" {
  description = "AWS region for resource deployment"
  type        = string
  default     = "us-east-1"
}
