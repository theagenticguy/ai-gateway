variable "user_pool_id" {
  description = "Cognito User Pool ID to create clients in"
  type        = string
}

variable "resource_server_scope_identifiers" {
  description = "List of valid scope identifiers from the Cognito resource server (e.g. https://gateway.internal/invoke)"
  type        = list(string)
}

variable "client_configs" {
  description = <<-EOT
    Map of team configurations for Cognito app clients.
    Each key is the team identifier used in resource naming.

    Example:
      client_configs = {
        platform = {
          allowed_scopes = ["https://gateway.internal/invoke"]
          description    = "Platform engineering team"
        }
      }
  EOT
  type = map(object({
    allowed_scopes = list(string)
    description    = string
  }))
}

variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev or prod)"
  type        = string
}
