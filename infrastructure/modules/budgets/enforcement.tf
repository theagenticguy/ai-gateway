# =============================================================================
# Budget Enforcement Lambda — pre-request budget check via Function URL
# =============================================================================

# ── Lambda package ───────────────────────────────────────────────────────────

data "archive_file" "enforcement" {
  type        = "zip"
  source_dir  = "${path.module}/../../../src/budget_enforcement"
  output_path = "${path.module}/builds/budget_enforcement.zip"
}

# ── KMS key for Lambda environment variables ─────────────────────────────────

resource "aws_kms_key" "enforcement_lambda_env" {
  count                   = var.enable_budget_enforcement ? 1 : 0
  description             = "KMS key for budget enforcement Lambda env vars"
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
  tags = { Name = "${var.project_name}-budget-enforcement-lambda" }
}

resource "aws_kms_alias" "enforcement_lambda_env" {
  count         = var.enable_budget_enforcement ? 1 : 0
  name          = "alias/${var.project_name}-budget-enforcement-lambda"
  target_key_id = aws_kms_key.enforcement_lambda_env[0].key_id
}

# ── IAM role ─────────────────────────────────────────────────────────────────

resource "aws_iam_role" "enforcement_lambda" {
  count = var.enable_budget_enforcement ? 1 : 0
  name  = "${var.project_name}-${var.environment}-budget-enforcement"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "enforcement_lambda" {
  count = var.enable_budget_enforcement ? 1 : 0
  name  = "budget-enforcement-policy"
  role  = aws_iam_role.enforcement_lambda[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBRead"
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:Query"]
        Resource = [
          "arn:aws:dynamodb:${var.aws_region}:${var.account_id}:table/${var.budgets_table}",
          "arn:aws:dynamodb:${var.aws_region}:${var.account_id}:table/${var.usage_table}",
        ]
      },
      {
        Sid      = "CloudWatchLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/aws/lambda/${var.project_name}-${var.environment}-budget-enforcement:*"
      },
      {
        Sid      = "KMSDecrypt"
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = aws_kms_key.enforcement_lambda_env[0].arn
      }
    ]
  })
}

# ── CloudWatch log group ─────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "enforcement_lambda" {
  count             = var.enable_budget_enforcement ? 1 : 0
  name              = "/aws/lambda/${var.project_name}-${var.environment}-budget-enforcement"
  retention_in_days = 90
}

# ── Lambda function ──────────────────────────────────────────────────────────

resource "aws_lambda_function" "budget_enforcement" {
  count            = var.enable_budget_enforcement ? 1 : 0
  function_name    = "${var.project_name}-${var.environment}-budget-enforcement"
  description      = "Pre-request budget check — returns allow/deny based on team spend"
  runtime          = "python3.13"
  handler          = "handler.handler"
  role             = aws_iam_role.enforcement_lambda[0].arn
  filename         = data.archive_file.enforcement.output_path
  source_code_hash = data.archive_file.enforcement.output_base64sha256
  timeout          = 10
  memory_size      = 128
  kms_key_arn      = aws_kms_key.enforcement_lambda_env[0].arn

  environment {
    variables = {
      BUDGETS_TABLE           = var.budgets_table
      USAGE_TABLE             = var.usage_table
      TIER_DEFAULT_FREE       = var.tier_default_free
      TIER_DEFAULT_STANDARD   = var.tier_default_standard
      TIER_DEFAULT_PREMIUM    = var.tier_default_premium
      TIER_DEFAULT_ENTERPRISE = var.tier_default_enterprise
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.enforcement_lambda[0].name
  }

  depends_on = [
    aws_cloudwatch_log_group.enforcement_lambda,
    aws_iam_role_policy.enforcement_lambda,
  ]
}

# ── Function URL (public endpoint for gateway to call) ───────────────────────

resource "aws_lambda_function_url" "budget_enforcement" {
  count              = var.enable_budget_enforcement ? 1 : 0
  function_name      = aws_lambda_function.budget_enforcement[0].function_name
  authorization_type = "AWS_IAM"
}

# ── Outputs ──────────────────────────────────────────────────────────────────

output "lambda_function_arn" {
  value = var.enable_budget_enforcement ? aws_lambda_function.budget_enforcement[0].arn : null
}

output "lambda_function_name" {
  value = var.enable_budget_enforcement ? aws_lambda_function.budget_enforcement[0].function_name : null
}

output "function_url" {
  value = var.enable_budget_enforcement ? aws_lambda_function_url.budget_enforcement[0].function_url : null
}
