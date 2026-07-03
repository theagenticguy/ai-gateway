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

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
}

variable "azs" {
  description = "List of availability zones to use"
  type        = list(string)
}

variable "certificate_arn" {
  description = "ACM certificate ARN for HTTPS listener"
  type        = string
}

variable "single_nat_gateway" {
  description = "Use a single shared NAT gateway (true = cheapest, dev). Set false for one NAT gateway per AZ (multi-AZ HA, prod)."
  type        = bool
  default     = true
}

variable "enable_waf" {
  description = "Whether to enable WAF on the ALB"
  type        = bool
}

variable "enable_jwt_auth" {
  description = "Whether to enable ALB JWT validation (suppresses module HTTPS listener)"
  type        = bool
}

variable "waf_log_kms_key_arn" {
  description = "ARN of the KMS key for WAF log group encryption (from observability module)"
  type        = string
}
