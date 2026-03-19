variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev or prod)"
  type        = string
}

variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
}

variable "account_id" {
  description = "AWS account ID (for KMS policy)"
  type        = string
}
