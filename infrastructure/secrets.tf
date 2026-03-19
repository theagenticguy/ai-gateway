locals {
  secrets = {
    openai    = "ai-gateway/openai-api-key"
    anthropic = "ai-gateway/anthropic-api-key"
    google    = "ai-gateway/google-api-key"
    azure     = "ai-gateway/azure-api-key"
  }
}

resource "aws_secretsmanager_secret" "secrets" {
  for_each = local.secrets

  name = each.value
}

resource "aws_secretsmanager_secret_version" "secrets" {
  for_each = local.secrets

  secret_id     = aws_secretsmanager_secret.secrets[each.key].id
  secret_string = "REPLACE_ME"
}
