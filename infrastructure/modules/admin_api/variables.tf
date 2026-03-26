variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev or prod)"
  type        = string
}

variable "enable_admin_api" {
  description = "Whether to provision the admin API Gateway and related resources"
  type        = bool
  default     = false
}

variable "cognito_user_pool_arn" {
  description = "ARN of the Cognito User Pool for the API Gateway authorizer"
  type        = string
}

variable "required_scope" {
  description = "OAuth scope required for admin API access"
  type        = string
  default     = "https://gateway.internal/admin"
}

# ---------------------------------------------------------------------------
# Lambda ARNs — one per admin handler
# ---------------------------------------------------------------------------

variable "team_registration_invoke_arn" {
  description = "Invoke ARN of the team-registration Lambda"
  type        = string
  default     = ""
}

variable "team_registration_function_name" {
  description = "Function name of the team-registration Lambda"
  type        = string
  default     = ""
}

variable "budget_admin_invoke_arn" {
  description = "Invoke ARN of the budget-admin Lambda"
  type        = string
  default     = ""
}

variable "budget_admin_function_name" {
  description = "Function name of the budget-admin Lambda"
  type        = string
  default     = ""
}

variable "routing_config_invoke_arn" {
  description = "Invoke ARN of the routing-config Lambda"
  type        = string
  default     = ""
}

variable "routing_config_function_name" {
  description = "Function name of the routing-config Lambda"
  type        = string
  default     = ""
}

variable "content_scanner_invoke_arn" {
  description = "Invoke ARN of the content-scanner Lambda"
  type        = string
  default     = ""
}

variable "content_scanner_function_name" {
  description = "Function name of the content-scanner Lambda"
  type        = string
  default     = ""
}

variable "pricing_admin_invoke_arn" {
  description = "Invoke ARN of the pricing-admin Lambda"
  type        = string
  default     = ""
}

variable "pricing_admin_function_name" {
  description = "Function name of the pricing-admin Lambda"
  type        = string
  default     = ""
}
