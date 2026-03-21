# =============================================================================
# KMS — Encryption keys for routing config resources
# =============================================================================

resource "aws_kms_key" "routing" {
  count = var.enable_routing_api ? 1 : 0

  description             = "KMS key for ${var.project_name} routing config resources"
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
      },
      {
        Sid       = "AllowLambdaService"
        Effect    = "Allow"
        Principal = { Service = "lambda.amazonaws.com" }
        Action    = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey*"]
        Resource  = "*"
      }
    ]
  })

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-routing"
  })
}

resource "aws_kms_alias" "routing" {
  count = var.enable_routing_api ? 1 : 0

  name          = "alias/${var.project_name}-${var.environment}-routing"
  target_key_id = aws_kms_key.routing[0].key_id
}
