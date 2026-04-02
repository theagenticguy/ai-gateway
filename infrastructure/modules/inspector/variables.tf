variable "project_name" {
  description = "Project name used for ECR repository filter"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev or prod). Prod gets continuous scanning; dev gets scan-on-push."
  type        = string
}

variable "enable_inspector" {
  description = "Whether to enable Amazon Inspector enhanced scanning for ECR"
  type        = bool
  default     = false
}
