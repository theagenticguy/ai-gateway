# =============================================================================
# Routing Config Lambda — CRUD API for routing configurations via Function URL
# =============================================================================

# -- Lambda package ------------------------------------------------------------

data "archive_file" "routing_config" {
  type        = "zip"
  source_dir  = "${path.module}/../../../src/routing_config"
  output_path = "${path.module}/builds/routing_config.zip"
}

# -- IAM role ------------------------------------------------------------------

resource "aws_iam_role" "routing_lambda" {
  count = var.enable_routing_api ? 1 : 0
  name  = "${var.project_name}-${var.environment}-routing-config"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "routing_lambda" {
  count = var.enable_routing_api ? 1 : 0
  name  = "routing-config-policy"
  role  = aws_iam_role.routing_lambda[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBAccess"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:DeleteItem",
          "dynamodb:Scan",
        ]
        Resource = [
          aws_dynamodb_table.routing_configs[0].arn,
        ]
      },
      {
        Sid      = "CloudWatchLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.project_name}-${var.environment}-routing-config:*"
      },
      {
        Sid      = "KMSAccess"
        Effect   = "Allow"
        Action   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey*", "kms:DescribeKey"]
        Resource = aws_kms_key.routing[0].arn
      }
    ]
  })
}

# -- CloudWatch log group ------------------------------------------------------

resource "aws_cloudwatch_log_group" "routing_lambda" {
  #checkov:skip=CKV_AWS_158:KMS encryption planned for prod
  #checkov:skip=CKV_AWS_338:365-day retention planned for prod
  count             = var.enable_routing_api ? 1 : 0
  name              = "/aws/lambda/${var.project_name}-${var.environment}-routing-config"
  retention_in_days = 90
}

# -- Lambda function -----------------------------------------------------------

resource "aws_lambda_function" "routing_config" {
  #checkov:skip=CKV_AWS_115:Concurrency limits set at deployment
  #checkov:skip=CKV_AWS_116:DLQ handled by CloudWatch alarms on errors
  #checkov:skip=CKV_AWS_117:Lambda needs internet access for external APIs
  #checkov:skip=CKV_AWS_272:Code-signing not required for internal dev
  count            = var.enable_routing_api ? 1 : 0
  function_name    = "${var.project_name}-${var.environment}-routing-config"
  description      = "Routing config CRUD API — serves built-in and custom Portkey routing configs"
  runtime          = "python3.13"
  handler          = "handler.handler"
  role             = aws_iam_role.routing_lambda[0].arn
  filename         = data.archive_file.routing_config.output_path
  source_code_hash = data.archive_file.routing_config.output_base64sha256
  timeout          = 10
  memory_size      = 128
  kms_key_arn      = aws_kms_key.routing[0].arn

  environment {
    variables = {
      ROUTING_CONFIGS_TABLE = aws_dynamodb_table.routing_configs[0].name
      PORTKEY_CONFIGS_DIR   = "/var/task/portkey-configs"
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.routing_lambda[0].name
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [
    aws_cloudwatch_log_group.routing_lambda,
    aws_iam_role_policy.routing_lambda,
  ]
}

# -- Function URL (IAM-authed endpoint) ----------------------------------------

resource "aws_lambda_function_url" "routing_config" {
  count              = var.enable_routing_api ? 1 : 0
  function_name      = aws_lambda_function.routing_config[0].function_name
  authorization_type = "AWS_IAM"
}
