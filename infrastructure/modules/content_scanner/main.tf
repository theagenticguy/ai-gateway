# =============================================================================
# Content Scanner — Lambda + Function URL for PII redaction & injection detection
# =============================================================================

data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../../../src/content_scanner"
  output_path = "${path.module}/builds/content_scanner.zip"
}

# -----------------------------------------------------------------------------
# KMS — encrypt Lambda environment variables
# -----------------------------------------------------------------------------

resource "aws_kms_key" "lambda_env" {
  count                   = var.enable_content_scanner ? 1 : 0
  description             = "KMS key for content scanner Lambda env vars"
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
      },
    ]
  })

  tags = merge(var.tags, {
    Name = "${var.project_name}-content-scanner-lambda"
  })
}

resource "aws_kms_alias" "lambda_env" {
  count         = var.enable_content_scanner ? 1 : 0
  name          = "alias/${var.project_name}-content-scanner-lambda"
  target_key_id = aws_kms_key.lambda_env[0].key_id
}

# -----------------------------------------------------------------------------
# CloudWatch Log Group
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "lambda" {
  #checkov:skip=CKV_AWS_158:KMS encryption planned for prod
  #checkov:skip=CKV_AWS_338:365-day retention planned for prod
  count             = var.enable_content_scanner ? 1 : 0
  name              = "/aws/lambda/${var.project_name}-${var.environment}-content-scanner"
  retention_in_days = 90

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-content-scanner"
  })
}

# -----------------------------------------------------------------------------
# DynamoDB — per-team scan configuration
# -----------------------------------------------------------------------------

resource "aws_dynamodb_table" "config" {
  count        = var.enable_content_scanner ? 1 : 0
  name         = "${var.project_name}-${var.environment}-content-scanner-config"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "team_id"

  attribute {
    name = "team_id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.lambda_env[0].arn
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-content-scanner-config"
  })
}

# -----------------------------------------------------------------------------
# Lambda Function
# -----------------------------------------------------------------------------

resource "aws_lambda_function" "content_scanner" {
  #checkov:skip=CKV_AWS_115:Concurrency limits set at deployment
  #checkov:skip=CKV_AWS_116:DLQ handled by CloudWatch alarms on errors
  #checkov:skip=CKV_AWS_117:Lambda needs internet access for external APIs
  #checkov:skip=CKV_AWS_272:Code-signing not required for internal dev
  count            = var.enable_content_scanner ? 1 : 0
  function_name    = "${var.project_name}-${var.environment}-content-scanner"
  description      = "Scans content for PII (via Comprehend) and prompt injection patterns"
  runtime          = "python3.13"
  handler          = "handler.handler"
  role             = aws_iam_role.lambda[0].arn
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256
  timeout          = 30
  memory_size      = 256
  kms_key_arn      = aws_kms_key.lambda_env[0].arn

  environment {
    variables = {
      CONFIG_TABLE_NAME      = aws_dynamodb_table.config[0].name
      DEFAULT_PII_MODE       = var.default_pii_mode
      DEFAULT_INJECTION_MODE = var.default_injection_mode
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.lambda[0].name
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [
    aws_cloudwatch_log_group.lambda,
    aws_iam_role_policy.lambda,
  ]

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-content-scanner"
  })
}

# -----------------------------------------------------------------------------
# Lambda Function URL (public endpoint for gateway integration)
# -----------------------------------------------------------------------------

resource "aws_lambda_function_url" "content_scanner" {
  count              = var.enable_content_scanner ? 1 : 0
  function_name      = aws_lambda_function.content_scanner[0].function_name
  authorization_type = "AWS_IAM"
}
