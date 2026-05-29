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
# Team Registration — Self-service API for team onboarding
#
# Creates a DynamoDB table for team metadata, a Lambda function with a
# Function URL endpoint, and an IAM role with Cognito admin + DynamoDB
# read/write permissions.
# =============================================================================

data "aws_caller_identity" "current" {}

# -----------------------------------------------------------------------------
# DynamoDB — gateway-teams table
#
# PK: team_id (S) — UUID for the team
# GSI: team-name-index — lookup by team_name for duplicate checks
# -----------------------------------------------------------------------------

resource "aws_dynamodb_table" "teams" {
  count = var.enable_team_registration ? 1 : 0

  name         = "${var.project_name}-${var.environment}-teams"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "team_id"

  attribute {
    name = "team_id"
    type = "S"
  }

  attribute {
    name = "team_name"
    type = "S"
  }

  global_secondary_index {
    name = "team-name-index"
    key_schema {
      attribute_name = "team_name"
      key_type       = "HASH"
    }
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.teams[0].arn
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-teams"
  })
}

# -----------------------------------------------------------------------------
# KMS — Encryption key for teams DynamoDB table
# -----------------------------------------------------------------------------

resource "aws_kms_key" "teams" {
  count = var.enable_team_registration ? 1 : 0

  description             = "KMS key for ${var.project_name} teams DynamoDB table"
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
      }
    ]
  })

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-teams"
  })
}

resource "aws_kms_alias" "teams" {
  count = var.enable_team_registration ? 1 : 0

  name          = "alias/${var.project_name}-${var.environment}-teams"
  target_key_id = aws_kms_key.teams[0].key_id
}

# -----------------------------------------------------------------------------
# KMS — Lambda environment variable encryption
# -----------------------------------------------------------------------------

resource "aws_kms_key" "lambda_env" {
  count                   = var.enable_team_registration ? 1 : 0
  description             = "KMS key for team registration Lambda env vars"
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
        Sid       = "AllowLambdaService"
        Effect    = "Allow"
        Principal = { Service = "lambda.amazonaws.com" }
        Action    = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey*"]
        Resource  = "*"
      }
    ]
  })

  tags = merge(var.tags, {
    Name = "${var.project_name}-team-registration-lambda"
  })
}

resource "aws_kms_alias" "lambda_env" {
  count         = var.enable_team_registration ? 1 : 0
  name          = "alias/${var.project_name}-team-registration-lambda"
  target_key_id = aws_kms_key.lambda_env[0].key_id
}

# -----------------------------------------------------------------------------
# Lambda Package
# -----------------------------------------------------------------------------

data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../../../src/team_registration"
  output_path = "${path.module}/builds/team_registration.zip"
}

# -----------------------------------------------------------------------------
# CloudWatch Log Group
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "lambda" {
  #checkov:skip=CKV_AWS_158:KMS encryption planned for prod
  #checkov:skip=CKV_AWS_338:365-day retention planned for prod
  count             = var.enable_team_registration ? 1 : 0
  name              = "/aws/lambda/${var.project_name}-${var.environment}-team-registration"
  retention_in_days = 90

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-team-registration"
  })
}

# -----------------------------------------------------------------------------
# IAM Role + Policy
# -----------------------------------------------------------------------------

resource "aws_iam_role" "lambda" {
  count = var.enable_team_registration ? 1 : 0
  name  = "${var.project_name}-${var.environment}-team-registration"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-team-registration"
  })
}

resource "aws_iam_role_policy" "lambda" {
  count = var.enable_team_registration ? 1 : 0
  name  = "team-registration-policy"
  role  = aws_iam_role.lambda[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "CognitoAdmin"
        Effect = "Allow"
        Action = [
          "cognito-idp:CreateUserPoolClient",
          "cognito-idp:DeleteUserPoolClient",
          "cognito-idp:DescribeUserPoolClient",
          "cognito-idp:ListUserPoolClients"
        ]
        Resource = var.cognito_user_pool_arn
      },
      {
        Sid    = "DynamoDBTeams"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:Query",
          "dynamodb:Scan"
        ]
        Resource = [
          aws_dynamodb_table.teams[0].arn,
          "${aws_dynamodb_table.teams[0].arn}/index/*",
        ]
      },
      {
        Sid    = "DynamoDBBudgets"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:Query"
        ]
        Resource = compact([
          var.budgets_table_arn,
          var.usage_table_arn,
        ])
      },
      {
        Sid      = "CloudWatchLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.project_name}-${var.environment}-team-registration:*"
      },
      {
        Sid      = "KMSDecrypt"
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = aws_kms_key.lambda_env[0].arn
      },
      {
        Sid    = "KMSDynamoDB"
        Effect = "Allow"
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:GenerateDataKey*",
          "kms:DescribeKey"
        ]
        Resource = [aws_kms_key.teams[0].arn]
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# Lambda Function
# -----------------------------------------------------------------------------

resource "aws_lambda_function" "team_registration" {
  #checkov:skip=CKV_AWS_115:Concurrency limits set at deployment
  #checkov:skip=CKV_AWS_116:DLQ handled by CloudWatch alarms on errors
  #checkov:skip=CKV_AWS_117:Lambda needs internet access for external APIs
  #checkov:skip=CKV_AWS_272:Code-signing not required for internal dev
  count            = var.enable_team_registration ? 1 : 0
  function_name    = "${var.project_name}-${var.environment}-team-registration"
  description      = "Self-service team registration API — CRUD for team onboarding"
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
      USER_POOL_ID               = var.cognito_user_pool_id
      TEAMS_TABLE                = aws_dynamodb_table.teams[0].name
      BUDGETS_TABLE              = var.budgets_table_name
      USAGE_TABLE                = var.usage_table_name
      PROJECT_NAME               = var.project_name
      ENVIRONMENT                = var.environment
      TOKEN_ENDPOINT             = var.cognito_token_endpoint
      RESOURCE_SERVER_IDENTIFIER = var.resource_server_identifier
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
    Name = "${var.project_name}-${var.environment}-team-registration"
  })
}

# -----------------------------------------------------------------------------
# Lambda Function URL (admin endpoint)
# -----------------------------------------------------------------------------

resource "aws_lambda_function_url" "team_registration" {
  count              = var.enable_team_registration ? 1 : 0
  function_name      = aws_lambda_function.team_registration[0].function_name
  authorization_type = "AWS_IAM"
}
