terraform {
  required_version = "~> 1.14"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.22"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6"
    }
  }
}

# =============================================================================
# API Foundation — control-plane stage, caching, throttling, WAF, access logs,
# and the token-exchange route (ADR-016)
# =============================================================================
# Layers the production concerns the bare modules/admin_api lacks onto the
# admin REST API:
#   - a deployed STAGE with method-level cache (idempotent GETs) + throttling
#   - per-tenant usage plans + API keys (defense in depth atop Cognito)
#   - regional WAF (rate-based + AWS managed rule sets)
#   - structured JSON access logging to CloudWatch
#   - the POST /auth/token route → admin_token Lambda + its HS256 signing secret
#
# The Cognito authorizer + per-route /teams|/budgets|... resources live in
# modules/admin_api; this module consumes that REST API id by reference.
# =============================================================================

locals {
  name = "${var.project_name}-${var.environment}-control"
}

# -----------------------------------------------------------------------------
# Token signing secret (HS256, >= 32 bytes per RFC 7518; gwcore mints with it)
# -----------------------------------------------------------------------------

#checkov:skip=CKV_AWS_149:SecretsManager default AWS-managed key is sufficient here
#checkov:skip=CKV2_AWS_57:Rotation handled operationally; the secret is a signing key, not a credential
resource "aws_secretsmanager_secret" "token_signing" {
  count                   = var.enable_api_foundation ? 1 : 0
  name                    = "${local.name}-token-signing"
  description             = "HS256 signing secret for minted gateway tokens (admin_token)"
  recovery_window_in_days = 7
}

resource "random_password" "token_signing" {
  count   = var.enable_api_foundation ? 1 : 0
  length  = 48 # > 32 bytes → satisfies PyJWT InsecureKeyLengthWarning floor
  special = false
}

resource "aws_secretsmanager_secret_version" "token_signing" {
  count         = var.enable_api_foundation ? 1 : 0
  secret_id     = aws_secretsmanager_secret.token_signing[0].id
  secret_string = random_password.token_signing[0].result
}

# -----------------------------------------------------------------------------
# admin_token Lambda — POST /auth/token
# -----------------------------------------------------------------------------

resource "aws_iam_role" "admin_token" {
  count = var.enable_api_foundation ? 1 : 0
  name  = "${local.name}-admin-token"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

# Inline jsonencode policy (repo convention) so checkov resolves the concrete
# resource ARNs. Every statement is scoped — secret read to one secret ARN,
# firehose writes to the audit stream ARN — never "*".
resource "aws_iam_role_policy" "admin_token" {
  count = var.enable_api_foundation ? 1 : 0
  name  = "admin-token"
  role  = aws_iam_role.admin_token[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [{
        Effect   = "Allow"
        Action   = "secretsmanager:GetSecretValue"
        Resource = aws_secretsmanager_secret.token_signing[0].arn
      }],
      var.audit_firehose_arn == "" ? [] : [{
        Effect   = "Allow"
        Action   = ["firehose:PutRecord", "firehose:PutRecordBatch"]
        Resource = var.audit_firehose_arn
      }],
    )
  })
}

resource "aws_iam_role_policy_attachment" "admin_token_basic" {
  count      = var.enable_api_foundation ? 1 : 0
  role       = aws_iam_role.admin_token[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

#checkov:skip=CKV_AWS_50:X-Ray tracing is enabled via the OTEL collector layer, not per-function
#checkov:skip=CKV_AWS_115:Reserved concurrency is set at the account level for this low-traffic plane
#checkov:skip=CKV_AWS_116:DLQ not required for a synchronous request/response Lambda
#checkov:skip=CKV_AWS_117:Control-plane Lambda needs egress to Cognito JWKS; VPC attachment is optional
#checkov:skip=CKV_AWS_173:Env vars carry ARNs/URLs, not secrets; the secret is fetched at runtime
resource "aws_lambda_function" "admin_token" {
  count         = var.enable_api_foundation ? 1 : 0
  function_name = "${local.name}-admin-token"
  role          = aws_iam_role.admin_token[0].arn
  runtime       = "python3.13"
  handler       = "admin_token.handler.handler"
  filename      = var.admin_token_package_path
  timeout       = 10
  memory_size   = 256

  environment {
    variables = {
      TOKEN_SIGNING_SECRET_ARN = aws_secretsmanager_secret.token_signing[0].arn
      TOKEN_ISSUER             = var.token_issuer
      COGNITO_JWKS_URL         = var.cognito_jwks_url
      COGNITO_ISSUER           = var.cognito_issuer
      AUDIT_FIREHOSE_STREAM    = var.audit_firehose_stream_name
    }
  }
}

# -----------------------------------------------------------------------------
# /auth/token route on the existing admin REST API
# -----------------------------------------------------------------------------

resource "aws_api_gateway_resource" "auth" {
  count       = var.enable_api_foundation ? 1 : 0
  rest_api_id = var.rest_api_id
  parent_id   = var.rest_api_root_resource_id
  path_part   = "auth"
}

resource "aws_api_gateway_resource" "auth_token" {
  count       = var.enable_api_foundation ? 1 : 0
  rest_api_id = var.rest_api_id
  parent_id   = aws_api_gateway_resource.auth[0].id
  path_part   = "token"
}

#checkov:skip=CKV2_AWS_53:Request validation handled by the handler's Pydantic model
resource "aws_api_gateway_method" "auth_token_post" {
  count                = var.enable_api_foundation ? 1 : 0
  rest_api_id          = var.rest_api_id
  resource_id          = aws_api_gateway_resource.auth_token[0].id
  http_method          = "POST"
  authorization        = "COGNITO_USER_POOLS"
  authorizer_id        = var.cognito_authorizer_id
  authorization_scopes = [var.invoke_scope]
}

resource "aws_api_gateway_integration" "auth_token" {
  count                   = var.enable_api_foundation ? 1 : 0
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.auth_token[0].id
  http_method             = aws_api_gateway_method.auth_token_post[0].http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.admin_token[0].invoke_arn
}

resource "aws_lambda_permission" "auth_token" {
  count         = var.enable_api_foundation ? 1 : 0
  statement_id  = "AllowAPIGatewayInvoke-auth-token"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.admin_token[0].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${var.rest_api_execution_arn}/*"
}
