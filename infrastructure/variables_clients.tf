variable "client_configs" {
  description = <<-EOT
    Map of team configurations for per-team Cognito app clients.
    Each key is the team identifier; value specifies allowed OAuth scopes
    and a human-readable description.

    Example:
      client_configs = {
        platform = {
          allowed_scopes = ["https://gateway.internal/invoke"]
          description    = "Platform engineering team"
        }
        ml-ops = {
          allowed_scopes = ["https://gateway.internal/invoke", "https://gateway.internal/admin"]
          description    = "ML Operations team"
        }
      }
  EOT
  type = map(object({
    allowed_scopes = list(string)
    description    = string
  }))
  default = {}
}
