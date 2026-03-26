terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.22"
    }
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.0"
    }
  }
}

# =============================================================================
# IAM — Lambda execution role for the content scanner
# =============================================================================

resource "aws_iam_role" "lambda" {
  count = var.enable_content_scanner ? 1 : 0
  name  = "${var.project_name}-${var.environment}-content-scanner"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-content-scanner"
  })
}

resource "aws_iam_role_policy" "lambda" {
  count = var.enable_content_scanner ? 1 : 0
  name  = "content-scanner-policy"
  role  = aws_iam_role.lambda[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ComprehendPii"
        Effect   = "Allow"
        Action   = ["comprehend:DetectPiiEntities"]
        Resource = "*"
      },
      {
        Sid    = "DynamoDBRead"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Query",
        ]
        Resource = var.enable_content_scanner ? aws_dynamodb_table.config[0].arn : "*"
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/aws/lambda/${var.project_name}-${var.environment}-content-scanner:*"
      },
      {
        Sid      = "KMSDecrypt"
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = var.enable_content_scanner ? aws_kms_key.lambda_env[0].arn : "*"
      },
      {
        Sid    = "AppConfigRead"
        Effect = "Allow"
        Action = [
          "appconfig:StartConfigurationSession",
          "appconfig:GetLatestConfiguration",
        ]
        Resource = "*"
      },
    ]
  })
}
