# Inspector — Amazon Inspector enhanced ECR scanning

variable "enable_inspector" {
  description = "Whether to enable Amazon Inspector enhanced scanning for ECR repositories"
  type        = bool
  default     = false
}
