# ------------------------------------------------------------------
# KMS key for CloudWatch Logs encryption
# ------------------------------------------------------------------

resource "aws_kms_key" "logs" {
  description             = "KMS key for AI Gateway CloudWatch Logs encryption"
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
      },
      {
        Sid    = "AllowCloudWatchLogs"
        Effect = "Allow"
        Principal = {
          Service = "logs.${var.aws_region}.amazonaws.com"
        }
        Action = [
          "kms:Encrypt*",
          "kms:Decrypt*",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:Describe*"
        ]
        Resource = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:*"
          }
        }
      }
    ]
  })

  tags = {
    Name = "ai-gateway-logs"
  }
}

resource "aws_kms_alias" "logs" {
  name          = "alias/ai-gateway-logs"
  target_key_id = aws_kms_key.logs.key_id
}

# ------------------------------------------------------------------
# Log Groups
# ------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "gateway" {
  name              = "/ecs/${var.project_name}/gateway"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.logs.arn
}

resource "aws_cloudwatch_log_group" "otel" {
  name              = "/ecs/${var.project_name}/otel"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.logs.arn
}
