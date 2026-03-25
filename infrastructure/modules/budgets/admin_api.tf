# =============================================================================
# Budget Admin API Lambda — REST API for budget management via Function URL
# =============================================================================

# ── Lambda package ───────────────────────────────────────────────────────────

data "archive_file" "budget_admin" {
  type        = "zip"
  source_dir  = "${path.module}/../../../src/budget_admin"
  output_path = "${path.module}/builds/budget_admin.zip"
}

# ── KMS key for Lambda environment variables ─────────────────────────────────

resource "aws_kms_key" "admin_api_lambda_env" {
  count                   = var.enable_budget_admin ? 1 : 0
  description             = "KMS key for budget admin API Lambda env vars"
  deletion_window_in_days = 7
  enable_key_rotation     = true
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableRootAccount"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${var.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
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
  tags = { Name = "${var.project_name}-budget-admin-api-lambda" }
}

resource "aws_kms_alias" "admin_api_lambda_env" {
  count         = var.enable_budget_admin ? 1 : 0
  name          = "alias/${var.project_name}-budget-admin-api-lambda"
  target_key_id = aws_kms_key.admin_api_lambda_env[0].key_id
}

# ── IAM role ─────────────────────────────────────────────────────────────────

resource "aws_iam_role" "admin_api_lambda" {
  count = var.enable_budget_admin ? 1 : 0
  name  = "${var.project_name}-${var.environment}-budget-admin-api"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "admin_api_lambda" {
  count = var.enable_budget_admin ? 1 : 0
  name  = "budget-admin-api-policy"
  role  = aws_iam_role.admin_api_lambda[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBFullAccess"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:Query",
          "dynamodb:Scan",
          "dynamodb:BatchGetItem",
          "dynamodb:BatchWriteItem",
        ]
        Resource = [
          "arn:aws:dynamodb:${var.aws_region}:${var.account_id}:table/${var.budgets_table}",
          "arn:aws:dynamodb:${var.aws_region}:${var.account_id}:table/${var.budgets_table}/index/*",
          "arn:aws:dynamodb:${var.aws_region}:${var.account_id}:table/${var.usage_table}",
          "arn:aws:dynamodb:${var.aws_region}:${var.account_id}:table/${var.usage_table}/index/*",
        ]
      },
      {
        Sid      = "CloudWatchLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/aws/lambda/${var.project_name}-${var.environment}-budget-admin-api:*"
      },
      {
        Sid      = "KMSDecrypt"
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = aws_kms_key.admin_api_lambda_env[0].arn
      }
    ]
  })
}

# ── CloudWatch log group ─────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "admin_api_lambda" {
  count             = var.enable_budget_admin ? 1 : 0
  name              = "/aws/lambda/${var.project_name}-${var.environment}-budget-admin-api"
  retention_in_days = 90
}

# ── Lambda function ──────────────────────────────────────────────────────────

resource "aws_lambda_function" "budget_admin_api" {
  count            = var.enable_budget_admin ? 1 : 0
  function_name    = "${var.project_name}-${var.environment}-budget-admin-api"
  description      = "Budget Admin REST API — CRUD budgets, query usage"
  runtime          = "python3.13"
  handler          = "handler.handler"
  role             = aws_iam_role.admin_api_lambda[0].arn
  filename         = data.archive_file.budget_admin.output_path
  source_code_hash = data.archive_file.budget_admin.output_base64sha256
  timeout          = 30
  memory_size      = 256
  kms_key_arn      = aws_kms_key.admin_api_lambda_env[0].arn

  environment {
    variables = {
      BUDGETS_TABLE = var.budgets_table
      USAGE_TABLE   = var.usage_table
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.admin_api_lambda[0].name
  }

  depends_on = [
    aws_cloudwatch_log_group.admin_api_lambda,
    aws_iam_role_policy.admin_api_lambda,
  ]
}

# ── Function URL (AWS_IAM auth) ─────────────────────────────────────────────

resource "aws_lambda_function_url" "budget_admin_api" {
  count              = var.enable_budget_admin ? 1 : 0
  function_name      = aws_lambda_function.budget_admin_api[0].function_name
  authorization_type = "AWS_IAM"
}

# ── Variables (admin-api-specific) ───────────────────────────────────────────

variable "enable_budget_admin" {
  description = "Feature flag: whether to create the budget admin API Lambda + Function URL"
  type        = bool
  default     = false
}

# ── Outputs ──────────────────────────────────────────────────────────────────

output "admin_api_lambda_function_arn" {
  description = "ARN of the budget admin API Lambda function"
  value       = var.enable_budget_admin ? aws_lambda_function.budget_admin_api[0].arn : null
}

output "admin_api_lambda_function_name" {
  description = "Name of the budget admin API Lambda function"
  value       = var.enable_budget_admin ? aws_lambda_function.budget_admin_api[0].function_name : null
}

output "admin_api_function_url" {
  description = "Function URL for the budget admin API"
  value       = var.enable_budget_admin ? aws_lambda_function_url.budget_admin_api[0].function_url : null
}
