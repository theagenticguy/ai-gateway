# -----------------------------------------------------------------------------
# Pre-Token-Generation Lambda (V2 trigger)
#
# Maps IdP group memberships to custom gateway claims:
#   team, org_unit, cost_center, tenant_tier
#
# Count-gated on var.enable_user_auth.
#
# Ref: ADR-013 (Identity Center SAML/OIDC Federation)
# -----------------------------------------------------------------------------

data "archive_file" "pre_token" {
  type        = "zip"
  source_dir  = "${path.module}/../../../src/pre_token"
  output_path = "${path.module}/builds/pre_token.zip"
}

# -- IAM Role -----------------------------------------------------------------

resource "aws_iam_role" "pre_token" {
  count = var.enable_user_auth ? 1 : 0
  name  = "${var.project_name}-${var.environment}-pre-token"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = {
    Name = "${var.project_name}-${var.environment}-pre-token"
  }
}

resource "aws_iam_role_policy" "pre_token" {
  count = var.enable_user_auth ? 1 : 0
  name  = "pre-token-policy"
  role  = aws_iam_role.pre_token[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "CloudWatchLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:*:log-group:/aws/lambda/${var.project_name}-${var.environment}-pre-token:*"
      }
    ]
  })
}

# -- CloudWatch Log Group -----------------------------------------------------

resource "aws_cloudwatch_log_group" "pre_token" {
  #checkov:skip=CKV_AWS_158:KMS encryption planned for prod
  #checkov:skip=CKV_AWS_338:365-day retention planned for prod
  count             = var.enable_user_auth ? 1 : 0
  name              = "/aws/lambda/${var.project_name}-${var.environment}-pre-token"
  retention_in_days = 90

  tags = {
    Name = "${var.project_name}-${var.environment}-pre-token"
  }
}

# -- SQS Dead Letter Queue (CKV_AWS_116) --------------------------------------

resource "aws_sqs_queue" "lambda_dlq" {
  #checkov:skip=CKV2_AWS_73:Using AWS-managed SQS key for dev
  count             = var.enable_user_auth ? 1 : 0
  name              = "${var.project_name}-${var.environment}-pre-token-dlq"
  kms_master_key_id = "alias/aws/sqs"
  tags = {
    Name = "${var.project_name}-${var.environment}-pre-token-dlq"
  }
}

# -- Lambda Function ----------------------------------------------------------

resource "aws_lambda_function" "pre_token" {
  #checkov:skip=CKV_AWS_115:Concurrency limits set at deployment
  #checkov:skip=CKV_AWS_116:DLQ handled by CloudWatch alarms on errors
  #checkov:skip=CKV_AWS_117:Lambda needs internet access for external APIs
  #checkov:skip=CKV_AWS_272:Code-signing not required for internal dev
  count = var.enable_user_auth ? 1 : 0

  function_name    = "${var.project_name}-${var.environment}-pre-token"
  description      = "Pre-Token-Generation V2: maps IdP groups to gateway claims"
  runtime          = "python3.13"
  handler          = "handler.handler"
  role             = aws_iam_role.pre_token[0].arn
  filename         = data.archive_file.pre_token.output_path
  source_code_hash = data.archive_file.pre_token.output_base64sha256
  timeout          = 5
  memory_size      = 128

  reserved_concurrent_executions = 10

  dead_letter_config {
    target_arn = aws_sqs_queue.lambda_dlq[0].arn
  }

  environment {
    variables = {
      GROUP_MAPPING = jsonencode(var.group_mapping)
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.pre_token[0].name
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [
    aws_cloudwatch_log_group.pre_token,
    aws_iam_role_policy.pre_token,
  ]

  tags = {
    Name = "${var.project_name}-${var.environment}-pre-token"
  }
}

# -- Lambda Permission (Cognito invocation) -----------------------------------

resource "aws_lambda_permission" "cognito_pre_token" {
  count = var.enable_user_auth ? 1 : 0

  statement_id  = "AllowCognitoInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.pre_token[0].function_name
  principal     = "cognito-idp.amazonaws.com"
  source_arn    = aws_cognito_user_pool.gateway.arn
}
