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
      { Sid = "KMSDecrypt", Effect = "Allow", Action = ["kms:Decrypt"], Resource = aws_kms_key.lambda_env[0].arn }
    ]
  })
}

resource "aws_cloudwatch_log_group" "lambda" {
  count             = var.enable_cost_attribution ? 1 : 0
  name              = "/aws/lambda/${var.project_name}-${var.environment}-cost-attribution"
  retention_in_days = 90
}

resource "aws_lambda_function" "cost_attribution" {
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
  environment { variables = { METRIC_NAMESPACE = "AIGateway" } }
  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.lambda[0].name
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
