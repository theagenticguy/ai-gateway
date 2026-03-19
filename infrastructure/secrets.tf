# ------------------------------------------------------------------
# KMS key for Secrets Manager encryption
# ------------------------------------------------------------------

resource "aws_kms_key" "secrets" {
  description             = "KMS key for AI Gateway secrets encryption"
  deletion_window_in_days = 7
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EnableRootAccount"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      }
    ]
  })

  tags = {
    Name = "ai-gateway-secrets"
  }
}

resource "aws_kms_alias" "secrets" {
  name          = "alias/ai-gateway-secrets"
  target_key_id = aws_kms_key.secrets.key_id
}

locals {
  secrets = {
    openai    = "ai-gateway/openai-api-key"
    anthropic = "ai-gateway/anthropic-api-key"
    google    = "ai-gateway/google-api-key"
    azure     = "ai-gateway/azure-api-key"
  }
}

resource "aws_secretsmanager_secret" "secrets" {
  #checkov:skip=CKV2_AWS_57:External provider API keys cannot be auto-rotated by Secrets Manager
  for_each = local.secrets

  name       = each.value
  kms_key_id = aws_kms_key.secrets.arn
}

resource "aws_secretsmanager_secret_version" "secrets" {
  for_each = local.secrets

  secret_id     = aws_secretsmanager_secret.secrets[each.key].id
  secret_string = "REPLACE_ME"
}
