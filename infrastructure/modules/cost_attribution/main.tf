terraform {
  required_version = "~> 1.14"

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

data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../../../src/cost_attribution"
  output_path = "${path.module}/builds/cost_attribution.zip"
}

resource "aws_kms_key" "lambda_env" {
  count                   = var.enable_cost_attribution ? 1 : 0
  description             = "KMS key for cost attribution Lambda env vars"
  deletion_window_in_days = 7
  enable_key_rotation     = true
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Sid = "EnableRootAccount", Effect = "Allow", Principal = { AWS = "arn:aws:iam::${var.account_id}:root" }, Action = "kms:*", Resource = "*" },
      { Sid = "AllowLambdaService", Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey*"], Resource = "*" }
    ]
  })
  tags = { Name = "${var.project_name}-cost-attribution-lambda" }
}

resource "aws_kms_alias" "lambda_env" {
  count         = var.enable_cost_attribution ? 1 : 0
  name          = "alias/${var.project_name}-cost-attribution-lambda"
  target_key_id = aws_kms_key.lambda_env[0].key_id
}

resource "aws_iam_role" "lambda" {
  count = var.enable_cost_attribution ? 1 : 0
  name  = "${var.project_name}-${var.environment}-cost-attribution"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "lambda" {
  count = var.enable_cost_attribution ? 1 : 0
  name  = "cost-attribution-policy"
  role  = aws_iam_role.lambda[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Sid = "CloudWatchMetrics", Effect = "Allow", Action = ["cloudwatch:PutMetricData"], Resource = "*", Condition = { StringEquals = { "cloudwatch:namespace" = "AIGateway" } } },
      { Sid = "CloudWatchLogs", Effect = "Allow", Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"], Resource = "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/aws/lambda/${var.project_name}-${var.environment}-cost-attribution:*" },
      { Sid = "KMSDecrypt", Effect = "Allow", Action = ["kms:Decrypt"], Resource = aws_kms_key.lambda_env[0].arn },
      {
        Sid    = "DynamoDBReadWrite"
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query"]
        Resource = [
          "arn:aws:dynamodb:${var.aws_region}:${var.account_id}:table/${var.usage_table}",
          "arn:aws:dynamodb:${var.aws_region}:${var.account_id}:table/${var.budgets_table}",
        ]
      },
      # Pricing overlay (runtime-configurable rates). _load_dynamic_pricing uses
      # Scan, so this grants Scan/GetItem on the pricing table only when one is
      # configured. Empty pricing_table_name => no statement (static table only).
      {
        Sid      = "DynamoDBPricingOverlayRead"
        Effect   = "Allow"
        Action   = ["dynamodb:Scan", "dynamodb:GetItem"]
        Resource = var.pricing_table_name != "" ? ["arn:aws:dynamodb:${var.aws_region}:${var.account_id}:table/${var.pricing_table_name}"] : ["arn:aws:dynamodb:${var.aws_region}:${var.account_id}:table/__none__"]
      },
      {
        Sid      = "SNSPublishAlerts"
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = var.budget_alerts_sns_topic_arn != "" ? [var.budget_alerts_sns_topic_arn] : []
      }
    ]
  })
}

resource "aws_cloudwatch_log_group" "lambda" {
  #checkov:skip=CKV_AWS_158:KMS encryption planned for prod
  #checkov:skip=CKV_AWS_338:365-day retention planned for prod
  count             = var.enable_cost_attribution ? 1 : 0
  name              = "/aws/lambda/${var.project_name}-${var.environment}-cost-attribution"
  retention_in_days = 90
}

resource "aws_lambda_function" "cost_attribution" {
  #checkov:skip=CKV_AWS_115:Concurrency limits set at deployment
  #checkov:skip=CKV_AWS_116:DLQ handled by CloudWatch alarms on errors
  #checkov:skip=CKV_AWS_117:Lambda needs internet access for external APIs
  #checkov:skip=CKV_AWS_272:Code-signing not required for internal dev
  count            = var.enable_cost_attribution ? 1 : 0
  function_name    = "${var.project_name}-${var.environment}-cost-attribution"
  description      = "Processes gateway logs and publishes token usage / cost metrics"
  runtime          = "python3.13"
  handler          = "handler.handler"
  role             = aws_iam_role.lambda[0].arn
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256
  timeout          = 60
  memory_size      = 128
  kms_key_arn      = aws_kms_key.lambda_env[0].arn
  environment {
    variables = {
      METRIC_NAMESPACE            = "AIGateway"
      USAGE_TABLE                 = var.usage_table
      BUDGETS_TABLE               = var.budgets_table
      BUDGET_ALERTS_SNS_TOPIC_ARN = var.budget_alerts_sns_topic_arn
      # F.2: wire the audit Firehose so _publish_audit_records is no longer dead
      # code (the handler no-ops cleanly when this is empty).
      AUDIT_FIREHOSE_STREAM = var.audit_firehose_stream
      # F.5: dynamic pricing overlay table (empty = static PRICING_TABLE only).
      PRICING_TABLE_NAME = var.pricing_table_name
      # F.6 (made safe by F.4): the handler trusts the x-amzn-oidc-data header
      # only when the ALB enforces JWT; otherwise it tags identity unverified-*.
      JWT_AUTH_ENFORCED = tostring(var.jwt_auth_enforced)
    }
  }
  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.lambda[0].name
  }
  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.lambda, aws_iam_role_policy.lambda]
}

resource "aws_lambda_permission" "cloudwatch_logs" {
  count         = var.enable_cost_attribution ? 1 : 0
  statement_id  = "AllowCloudWatchLogsInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.cost_attribution[0].function_name
  principal     = "logs.${var.aws_region}.amazonaws.com"
  source_arn    = "${var.gateway_log_group_arn}:*"
}

resource "aws_cloudwatch_log_subscription_filter" "gateway" {
  count           = var.enable_cost_attribution ? 1 : 0
  name            = "${var.project_name}-cost-attribution"
  log_group_name  = var.gateway_log_group_name
  filter_pattern  = "{ $.usage.total_tokens > 0 }"
  destination_arn = aws_lambda_function.cost_attribution[0].arn
  depends_on      = [aws_lambda_permission.cloudwatch_logs]
}
