# =============================================================================
# KMS — Encryption key for budget DynamoDB tables
# =============================================================================

resource "aws_kms_key" "budgets" {
  count = var.enable_budgets ? 1 : 0

  description             = "KMS key for ${var.project_name} budget DynamoDB tables"
  deletion_window_in_days = 7
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableRootAccount"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "AllowDynamoDBService"
        Effect    = "Allow"
        Principal = { Service = "dynamodb.amazonaws.com" }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:DescribeKey",
          "kms:CreateGrant"
        ]
        Resource = "*"
      }
    ]
  })

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-budgets"
  })
}

resource "aws_kms_alias" "budgets" {
  count = var.enable_budgets ? 1 : 0

  name          = "alias/${var.project_name}-${var.environment}-budgets"
  target_key_id = aws_kms_key.budgets[0].key_id
}
