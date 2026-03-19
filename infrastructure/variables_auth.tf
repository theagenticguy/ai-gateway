variable "cognito_user_pool_id" {
  description = "Cognito User Pool ID for JWT validation. Leave empty to disable JWT auth."
  type        = string
  default     = ""
}

variable "cognito_domain_prefix" {
  description = "Cognito User Pool domain prefix for the token endpoint. Leave empty to skip domain creation."
  type        = string
  default     = ""
}

variable "enable_jwt_auth" {
  description = "Whether to enable ALB JWT validation. Requires certificate_arn and cognito_user_pool_id."
  type        = bool
  default     = false
}
