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

# =============================================================================
# Chargeback Report Pipeline
#
# Step Functions workflow triggered monthly by EventBridge:
#   1. GenerateReport (Lambda) — queries DynamoDB, renders HTML, uploads to S3
#   2. SendNotification (SNS)  — publishes summary to notification topic
# =============================================================================

locals {
  resource_prefix = "${var.project_name}-${var.environment}-chargeback"
}

data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../../../src/chargeback_report"
  output_path = "${path.module}/builds/chargeback_report.zip"
}

# -----------------------------------------------------------------------------
# S3 Bucket — Report Storage (versioned, encrypted, 13-month retention)
# -----------------------------------------------------------------------------

resource "aws_s3_bucket" "reports" {
  #checkov:skip=CKV_AWS_18:Access logging planned for prod
  #checkov:skip=CKV_AWS_144:Cross-region replication planned for prod
  #checkov:skip=CKV2_AWS_62:Event notifications planned for prod
  count  = var.enable_chargeback ? 1 : 0
  bucket = "${local.resource_prefix}-reports-${var.account_id}"
  tags   = merge(var.tags, { Name = "${local.resource_prefix}-reports" })
}

resource "aws_s3_bucket_versioning" "reports" {
  count  = var.enable_chargeback ? 1 : 0
  bucket = aws_s3_bucket.reports[0].id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "reports" {
  count  = var.enable_chargeback ? 1 : 0
  bucket = aws_s3_bucket.reports[0].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.chargeback[0].arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "reports" {
  count  = var.enable_chargeback ? 1 : 0
  bucket = aws_s3_bucket.reports[0].id
  rule {
    id     = "expire-old-reports"
    status = "Enabled"
    expiration {
      days = 395 # ~13 months
    }
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

resource "aws_s3_bucket_public_access_block" "reports" {
  count                   = var.enable_chargeback ? 1 : 0
  bucket                  = aws_s3_bucket.reports[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# -----------------------------------------------------------------------------
# KMS Key — Encryption for Lambda env vars, S3, and logs
# -----------------------------------------------------------------------------

resource "aws_kms_key" "chargeback" {
  count                   = var.enable_chargeback ? 1 : 0
  description             = "KMS key for chargeback report resources"
  deletion_window_in_days = 7
  enable_key_rotation     = true
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Sid = "EnableRootAccount", Effect = "Allow", Principal = { AWS = "arn:aws:iam::${var.account_id}:root" }, Action = "kms:*", Resource = "*" },
      { Sid = "AllowLambdaService", Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey*"], Resource = "*" },
      { Sid = "AllowS3Service", Effect = "Allow", Principal = { Service = "s3.amazonaws.com" }, Action = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey*"], Resource = "*" }
    ]
  })
  tags = merge(var.tags, { Name = "${local.resource_prefix}-kms" })
}

resource "aws_kms_alias" "chargeback" {
  count         = var.enable_chargeback ? 1 : 0
  name          = "alias/${local.resource_prefix}"
  target_key_id = aws_kms_key.chargeback[0].key_id
}

# -----------------------------------------------------------------------------
# IAM — Lambda Execution Role
# -----------------------------------------------------------------------------

resource "aws_iam_role" "lambda" {
  count = var.enable_chargeback ? 1 : 0
  name  = "${local.resource_prefix}-lambda"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
  tags = merge(var.tags, { Name = "${local.resource_prefix}-lambda" })
}

resource "aws_iam_role_policy" "lambda" {
  count = var.enable_chargeback ? 1 : 0
  name  = "chargeback-lambda-policy"
  role  = aws_iam_role.lambda[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "DynamoDBRead"
        Effect   = "Allow"
        Action   = ["dynamodb:Scan", "dynamodb:Query", "dynamodb:GetItem", "dynamodb:BatchGetItem"]
        Resource = [var.usage_table_arn, "${var.usage_table_arn}/index/*", var.budgets_table_arn, "${var.budgets_table_arn}/index/*"]
      },
      {
        Sid      = "S3Write"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject"]
        Resource = "${aws_s3_bucket.reports[0].arn}/*"
      },
      {
        Sid      = "CloudWatchLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/aws/lambda/${local.resource_prefix}:*"
      },
      {
        Sid      = "KMSDecrypt"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey*"]
        Resource = aws_kms_key.chargeback[0].arn
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# CloudWatch Log Group — Lambda
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "lambda" {
  #checkov:skip=CKV_AWS_158:KMS encryption planned for prod
  #checkov:skip=CKV_AWS_338:365-day retention planned for prod
  count             = var.enable_chargeback ? 1 : 0
  name              = "/aws/lambda/${local.resource_prefix}"
  retention_in_days = 90
  tags              = merge(var.tags, { Name = "${local.resource_prefix}-logs" })
}

# -----------------------------------------------------------------------------
# Lambda Function — Report Generator
# -----------------------------------------------------------------------------

resource "aws_lambda_function" "chargeback" {
  #checkov:skip=CKV_AWS_115:Concurrency limits set at deployment
  #checkov:skip=CKV_AWS_116:DLQ handled by CloudWatch alarms on errors
  #checkov:skip=CKV_AWS_117:Lambda needs internet access for external APIs
  #checkov:skip=CKV_AWS_272:Code-signing not required for internal dev
  count            = var.enable_chargeback ? 1 : 0
  function_name    = local.resource_prefix
  description      = "Generates monthly chargeback reports from DynamoDB usage data"
  runtime          = "python3.13"
  handler          = "handler.handler"
  role             = aws_iam_role.lambda[0].arn
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256
  timeout          = 120
  memory_size      = 256
  kms_key_arn      = aws_kms_key.chargeback[0].arn

  environment {
    variables = {
      USAGE_TABLE   = var.usage_table_name
      BUDGETS_TABLE = var.budgets_table_name
      REPORT_BUCKET = aws_s3_bucket.reports[0].id
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
  tags       = merge(var.tags, { Name = local.resource_prefix })
}

# -----------------------------------------------------------------------------
# IAM — Step Functions Execution Role
# -----------------------------------------------------------------------------

resource "aws_iam_role" "sfn" {
  count = var.enable_chargeback ? 1 : 0
  name  = "${local.resource_prefix}-sfn"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "states.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
  tags = merge(var.tags, { Name = "${local.resource_prefix}-sfn" })
}

resource "aws_iam_role_policy" "sfn" {
  count = var.enable_chargeback ? 1 : 0
  name  = "chargeback-sfn-policy"
  role  = aws_iam_role.sfn[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "InvokeLambda"
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = aws_lambda_function.chargeback[0].arn
      },
      {
        Sid      = "PublishSNS"
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = var.sns_topic_arn
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# Step Functions State Machine
# -----------------------------------------------------------------------------

resource "aws_sfn_state_machine" "chargeback" {
  count    = var.enable_chargeback ? 1 : 0
  name     = local.resource_prefix
  role_arn = aws_iam_role.sfn[0].arn

  definition = jsonencode({
    Comment = "Monthly chargeback report generation pipeline"
    StartAt = "GenerateReport"
    States = {
      GenerateReport = {
        Type     = "Task"
        Resource = aws_lambda_function.chargeback[0].arn
        Next     = "SendNotification"
        Retry = [
          {
            ErrorEquals     = ["States.TaskFailed"]
            IntervalSeconds = 30
            MaxAttempts     = 2
            BackoffRate     = 2.0
          }
        ]
        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            Next        = "ReportFailed"
          }
        ]
      }
      SendNotification = {
        Type     = "Task"
        Resource = "arn:aws:states:::sns:publish"
        Parameters = {
          "TopicArn"  = var.sns_topic_arn
          "Subject"   = "AI Gateway Chargeback Report"
          "Message.$" = "$.summary"
        }
        End = true
      }
      ReportFailed = {
        Type     = "Task"
        Resource = "arn:aws:states:::sns:publish"
        Parameters = {
          TopicArn = var.sns_topic_arn
          Subject  = "AI Gateway Chargeback Report FAILED"
          Message  = "The monthly chargeback report generation failed. Check CloudWatch logs for details."
        }
        End = true
      }
    }
  })

  tags = merge(var.tags, { Name = local.resource_prefix })
}

# -----------------------------------------------------------------------------
# EventBridge Rule — Monthly Trigger (1st of month at 06:00 UTC)
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "monthly" {
  count               = var.enable_chargeback ? 1 : 0
  name                = "${local.resource_prefix}-monthly"
  description         = "Triggers chargeback report on the 1st of each month"
  schedule_expression = "cron(0 6 1 * ? *)"
  tags                = merge(var.tags, { Name = "${local.resource_prefix}-monthly" })
}

resource "aws_cloudwatch_event_target" "sfn" {
  count    = var.enable_chargeback ? 1 : 0
  rule     = aws_cloudwatch_event_rule.monthly[0].name
  arn      = aws_sfn_state_machine.chargeback[0].arn
  role_arn = aws_iam_role.eventbridge[0].arn

  input = jsonencode({
    month         = "auto"
    output_format = "html"
  })
}

# IAM role for EventBridge to invoke Step Functions
resource "aws_iam_role" "eventbridge" {
  count = var.enable_chargeback ? 1 : 0
  name  = "${local.resource_prefix}-eventbridge"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "events.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
  tags = merge(var.tags, { Name = "${local.resource_prefix}-eventbridge" })
}

resource "aws_iam_role_policy" "eventbridge" {
  count = var.enable_chargeback ? 1 : 0
  name  = "chargeback-eventbridge-policy"
  role  = aws_iam_role.eventbridge[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "StartExecution"
        Effect   = "Allow"
        Action   = ["states:StartExecution"]
        Resource = aws_sfn_state_machine.chargeback[0].arn
      }
    ]
  })
}
