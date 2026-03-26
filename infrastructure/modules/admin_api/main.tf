terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.22"
    }
  }
}

# =============================================================================
# Admin API — API Gateway REST API with Cognito Authorizer
# =============================================================================
# This module provisions a REST API for admin/secondary endpoints.
# The ALB handles the inference path (/v1/chat/completions, /v1/messages).
# Each admin handler gets a dedicated path prefix with AWS_PROXY Lambda
# integration: /teams, /budgets, /routing, /scanner, /pricing.
# The root resource (/) retains a MOCK integration for health checks.
# =============================================================================

# -----------------------------------------------------------------------------
# REST API
# -----------------------------------------------------------------------------

#checkov:skip=CKV_AWS_120:API caching not needed for admin APIs
#checkov:skip=CKV2_AWS_29:WAF on ALB covers inference path; admin API is low-traffic
resource "aws_api_gateway_rest_api" "admin" {
  count = var.enable_admin_api ? 1 : 0

  name        = "${var.project_name}-${var.environment}-admin-api"
  description = "Admin plane REST API for ${var.project_name} (${var.environment})"

  endpoint_configuration {
    types = ["REGIONAL"]
  }
}

# -----------------------------------------------------------------------------
# Cognito Authorizer
# -----------------------------------------------------------------------------

resource "aws_api_gateway_authorizer" "cognito" {
  count = var.enable_admin_api ? 1 : 0

  name          = "cognito"
  rest_api_id   = aws_api_gateway_rest_api.admin[0].id
  type          = "COGNITO_USER_POOLS"
  provider_arns = [var.cognito_user_pool_arn]
}

# =============================================================================
# Lambda path-prefix integrations — one per admin handler
# =============================================================================
# Each handler gets a top-level resource (e.g. /teams), a {proxy+} child for
# sub-paths, ANY methods on both with Cognito auth, and AWS_PROXY integrations
# pointing at the corresponding Lambda function.
# =============================================================================

# ---------------------------------------------------------------------------
# /teams → team_registration Lambda
# ---------------------------------------------------------------------------

resource "aws_api_gateway_resource" "teams" {
  count       = var.enable_admin_api ? 1 : 0
  rest_api_id = aws_api_gateway_rest_api.admin[0].id
  parent_id   = aws_api_gateway_rest_api.admin[0].root_resource_id
  path_part   = "teams"
}

resource "aws_api_gateway_resource" "teams_proxy" {
  count       = var.enable_admin_api ? 1 : 0
  rest_api_id = aws_api_gateway_rest_api.admin[0].id
  parent_id   = aws_api_gateway_resource.teams[0].id
  path_part   = "{proxy+}"
}

#checkov:skip=CKV2_AWS_53:Request validation handled by Lambda handler Pydantic models
resource "aws_api_gateway_method" "teams_any" {
  count                = var.enable_admin_api ? 1 : 0
  rest_api_id          = aws_api_gateway_rest_api.admin[0].id
  resource_id          = aws_api_gateway_resource.teams[0].id
  http_method          = "ANY"
  authorization        = "COGNITO_USER_POOLS"
  authorizer_id        = aws_api_gateway_authorizer.cognito[0].id
  authorization_scopes = [var.required_scope]
}

#checkov:skip=CKV2_AWS_53:Request validation handled by Lambda handler Pydantic models
resource "aws_api_gateway_method" "teams_proxy_any" {
  count                = var.enable_admin_api ? 1 : 0
  rest_api_id          = aws_api_gateway_rest_api.admin[0].id
  resource_id          = aws_api_gateway_resource.teams_proxy[0].id
  http_method          = "ANY"
  authorization        = "COGNITO_USER_POOLS"
  authorizer_id        = aws_api_gateway_authorizer.cognito[0].id
  authorization_scopes = [var.required_scope]
}

resource "aws_api_gateway_integration" "teams" {
  count                   = var.enable_admin_api ? 1 : 0
  rest_api_id             = aws_api_gateway_rest_api.admin[0].id
  resource_id             = aws_api_gateway_resource.teams[0].id
  http_method             = aws_api_gateway_method.teams_any[0].http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = var.team_registration_invoke_arn
}

resource "aws_api_gateway_integration" "teams_proxy" {
  count                   = var.enable_admin_api ? 1 : 0
  rest_api_id             = aws_api_gateway_rest_api.admin[0].id
  resource_id             = aws_api_gateway_resource.teams_proxy[0].id
  http_method             = aws_api_gateway_method.teams_proxy_any[0].http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = var.team_registration_invoke_arn
}

resource "aws_lambda_permission" "teams" {
  count         = var.enable_admin_api ? 1 : 0
  statement_id  = "AllowAPIGatewayInvoke-teams"
  action        = "lambda:InvokeFunction"
  function_name = var.team_registration_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.admin[0].execution_arn}/*"
}

# ---------------------------------------------------------------------------
# /budgets → budget_admin Lambda
# ---------------------------------------------------------------------------

resource "aws_api_gateway_resource" "budgets" {
  count       = var.enable_admin_api ? 1 : 0
  rest_api_id = aws_api_gateway_rest_api.admin[0].id
  parent_id   = aws_api_gateway_rest_api.admin[0].root_resource_id
  path_part   = "budgets"
}

resource "aws_api_gateway_resource" "budgets_proxy" {
  count       = var.enable_admin_api ? 1 : 0
  rest_api_id = aws_api_gateway_rest_api.admin[0].id
  parent_id   = aws_api_gateway_resource.budgets[0].id
  path_part   = "{proxy+}"
}

#checkov:skip=CKV2_AWS_53:Request validation handled by Lambda handler Pydantic models
resource "aws_api_gateway_method" "budgets_any" {
  count                = var.enable_admin_api ? 1 : 0
  rest_api_id          = aws_api_gateway_rest_api.admin[0].id
  resource_id          = aws_api_gateway_resource.budgets[0].id
  http_method          = "ANY"
  authorization        = "COGNITO_USER_POOLS"
  authorizer_id        = aws_api_gateway_authorizer.cognito[0].id
  authorization_scopes = [var.required_scope]
}

#checkov:skip=CKV2_AWS_53:Request validation handled by Lambda handler Pydantic models
resource "aws_api_gateway_method" "budgets_proxy_any" {
  count                = var.enable_admin_api ? 1 : 0
  rest_api_id          = aws_api_gateway_rest_api.admin[0].id
  resource_id          = aws_api_gateway_resource.budgets_proxy[0].id
  http_method          = "ANY"
  authorization        = "COGNITO_USER_POOLS"
  authorizer_id        = aws_api_gateway_authorizer.cognito[0].id
  authorization_scopes = [var.required_scope]
}

resource "aws_api_gateway_integration" "budgets" {
  count                   = var.enable_admin_api ? 1 : 0
  rest_api_id             = aws_api_gateway_rest_api.admin[0].id
  resource_id             = aws_api_gateway_resource.budgets[0].id
  http_method             = aws_api_gateway_method.budgets_any[0].http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = var.budget_admin_invoke_arn
}

resource "aws_api_gateway_integration" "budgets_proxy" {
  count                   = var.enable_admin_api ? 1 : 0
  rest_api_id             = aws_api_gateway_rest_api.admin[0].id
  resource_id             = aws_api_gateway_resource.budgets_proxy[0].id
  http_method             = aws_api_gateway_method.budgets_proxy_any[0].http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = var.budget_admin_invoke_arn
}

resource "aws_lambda_permission" "budgets" {
  count         = var.enable_admin_api ? 1 : 0
  statement_id  = "AllowAPIGatewayInvoke-budgets"
  action        = "lambda:InvokeFunction"
  function_name = var.budget_admin_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.admin[0].execution_arn}/*"
}

# ---------------------------------------------------------------------------
# /routing → routing_config Lambda
# ---------------------------------------------------------------------------

resource "aws_api_gateway_resource" "routing" {
  count       = var.enable_admin_api ? 1 : 0
  rest_api_id = aws_api_gateway_rest_api.admin[0].id
  parent_id   = aws_api_gateway_rest_api.admin[0].root_resource_id
  path_part   = "routing"
}

resource "aws_api_gateway_resource" "routing_proxy" {
  count       = var.enable_admin_api ? 1 : 0
  rest_api_id = aws_api_gateway_rest_api.admin[0].id
  parent_id   = aws_api_gateway_resource.routing[0].id
  path_part   = "{proxy+}"
}

#checkov:skip=CKV2_AWS_53:Request validation handled by Lambda handler Pydantic models
resource "aws_api_gateway_method" "routing_any" {
  count                = var.enable_admin_api ? 1 : 0
  rest_api_id          = aws_api_gateway_rest_api.admin[0].id
  resource_id          = aws_api_gateway_resource.routing[0].id
  http_method          = "ANY"
  authorization        = "COGNITO_USER_POOLS"
  authorizer_id        = aws_api_gateway_authorizer.cognito[0].id
  authorization_scopes = [var.required_scope]
}

#checkov:skip=CKV2_AWS_53:Request validation handled by Lambda handler Pydantic models
resource "aws_api_gateway_method" "routing_proxy_any" {
  count                = var.enable_admin_api ? 1 : 0
  rest_api_id          = aws_api_gateway_rest_api.admin[0].id
  resource_id          = aws_api_gateway_resource.routing_proxy[0].id
  http_method          = "ANY"
  authorization        = "COGNITO_USER_POOLS"
  authorizer_id        = aws_api_gateway_authorizer.cognito[0].id
  authorization_scopes = [var.required_scope]
}

resource "aws_api_gateway_integration" "routing" {
  count                   = var.enable_admin_api ? 1 : 0
  rest_api_id             = aws_api_gateway_rest_api.admin[0].id
  resource_id             = aws_api_gateway_resource.routing[0].id
  http_method             = aws_api_gateway_method.routing_any[0].http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = var.routing_config_invoke_arn
}

resource "aws_api_gateway_integration" "routing_proxy" {
  count                   = var.enable_admin_api ? 1 : 0
  rest_api_id             = aws_api_gateway_rest_api.admin[0].id
  resource_id             = aws_api_gateway_resource.routing_proxy[0].id
  http_method             = aws_api_gateway_method.routing_proxy_any[0].http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = var.routing_config_invoke_arn
}

resource "aws_lambda_permission" "routing" {
  count         = var.enable_admin_api ? 1 : 0
  statement_id  = "AllowAPIGatewayInvoke-routing"
  action        = "lambda:InvokeFunction"
  function_name = var.routing_config_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.admin[0].execution_arn}/*"
}

# ---------------------------------------------------------------------------
# /scanner → content_scanner Lambda
# ---------------------------------------------------------------------------

resource "aws_api_gateway_resource" "scanner" {
  count       = var.enable_admin_api ? 1 : 0
  rest_api_id = aws_api_gateway_rest_api.admin[0].id
  parent_id   = aws_api_gateway_rest_api.admin[0].root_resource_id
  path_part   = "scanner"
}

resource "aws_api_gateway_resource" "scanner_proxy" {
  count       = var.enable_admin_api ? 1 : 0
  rest_api_id = aws_api_gateway_rest_api.admin[0].id
  parent_id   = aws_api_gateway_resource.scanner[0].id
  path_part   = "{proxy+}"
}

#checkov:skip=CKV2_AWS_53:Request validation handled by Lambda handler Pydantic models
resource "aws_api_gateway_method" "scanner_any" {
  count                = var.enable_admin_api ? 1 : 0
  rest_api_id          = aws_api_gateway_rest_api.admin[0].id
  resource_id          = aws_api_gateway_resource.scanner[0].id
  http_method          = "ANY"
  authorization        = "COGNITO_USER_POOLS"
  authorizer_id        = aws_api_gateway_authorizer.cognito[0].id
  authorization_scopes = [var.required_scope]
}

#checkov:skip=CKV2_AWS_53:Request validation handled by Lambda handler Pydantic models
resource "aws_api_gateway_method" "scanner_proxy_any" {
  count                = var.enable_admin_api ? 1 : 0
  rest_api_id          = aws_api_gateway_rest_api.admin[0].id
  resource_id          = aws_api_gateway_resource.scanner_proxy[0].id
  http_method          = "ANY"
  authorization        = "COGNITO_USER_POOLS"
  authorizer_id        = aws_api_gateway_authorizer.cognito[0].id
  authorization_scopes = [var.required_scope]
}

resource "aws_api_gateway_integration" "scanner" {
  count                   = var.enable_admin_api ? 1 : 0
  rest_api_id             = aws_api_gateway_rest_api.admin[0].id
  resource_id             = aws_api_gateway_resource.scanner[0].id
  http_method             = aws_api_gateway_method.scanner_any[0].http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = var.content_scanner_invoke_arn
}

resource "aws_api_gateway_integration" "scanner_proxy" {
  count                   = var.enable_admin_api ? 1 : 0
  rest_api_id             = aws_api_gateway_rest_api.admin[0].id
  resource_id             = aws_api_gateway_resource.scanner_proxy[0].id
  http_method             = aws_api_gateway_method.scanner_proxy_any[0].http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = var.content_scanner_invoke_arn
}

resource "aws_lambda_permission" "scanner" {
  count         = var.enable_admin_api ? 1 : 0
  statement_id  = "AllowAPIGatewayInvoke-scanner"
  action        = "lambda:InvokeFunction"
  function_name = var.content_scanner_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.admin[0].execution_arn}/*"
}

# ---------------------------------------------------------------------------
# /pricing → pricing_admin Lambda
# ---------------------------------------------------------------------------

resource "aws_api_gateway_resource" "pricing" {
  count       = var.enable_admin_api ? 1 : 0
  rest_api_id = aws_api_gateway_rest_api.admin[0].id
  parent_id   = aws_api_gateway_rest_api.admin[0].root_resource_id
  path_part   = "pricing"
}

resource "aws_api_gateway_resource" "pricing_proxy" {
  count       = var.enable_admin_api ? 1 : 0
  rest_api_id = aws_api_gateway_rest_api.admin[0].id
  parent_id   = aws_api_gateway_resource.pricing[0].id
  path_part   = "{proxy+}"
}

#checkov:skip=CKV2_AWS_53:Request validation handled by Lambda handler Pydantic models
resource "aws_api_gateway_method" "pricing_any" {
  count                = var.enable_admin_api ? 1 : 0
  rest_api_id          = aws_api_gateway_rest_api.admin[0].id
  resource_id          = aws_api_gateway_resource.pricing[0].id
  http_method          = "ANY"
  authorization        = "COGNITO_USER_POOLS"
  authorizer_id        = aws_api_gateway_authorizer.cognito[0].id
  authorization_scopes = [var.required_scope]
}

#checkov:skip=CKV2_AWS_53:Request validation handled by Lambda handler Pydantic models
resource "aws_api_gateway_method" "pricing_proxy_any" {
  count                = var.enable_admin_api ? 1 : 0
  rest_api_id          = aws_api_gateway_rest_api.admin[0].id
  resource_id          = aws_api_gateway_resource.pricing_proxy[0].id
  http_method          = "ANY"
  authorization        = "COGNITO_USER_POOLS"
  authorizer_id        = aws_api_gateway_authorizer.cognito[0].id
  authorization_scopes = [var.required_scope]
}

resource "aws_api_gateway_integration" "pricing" {
  count                   = var.enable_admin_api ? 1 : 0
  rest_api_id             = aws_api_gateway_rest_api.admin[0].id
  resource_id             = aws_api_gateway_resource.pricing[0].id
  http_method             = aws_api_gateway_method.pricing_any[0].http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = var.pricing_admin_invoke_arn
}

resource "aws_api_gateway_integration" "pricing_proxy" {
  count                   = var.enable_admin_api ? 1 : 0
  rest_api_id             = aws_api_gateway_rest_api.admin[0].id
  resource_id             = aws_api_gateway_resource.pricing_proxy[0].id
  http_method             = aws_api_gateway_method.pricing_proxy_any[0].http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = var.pricing_admin_invoke_arn
}

resource "aws_lambda_permission" "pricing" {
  count         = var.enable_admin_api ? 1 : 0
  statement_id  = "AllowAPIGatewayInvoke-pricing"
  action        = "lambda:InvokeFunction"
  function_name = var.pricing_admin_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.admin[0].execution_arn}/*"
}

# -----------------------------------------------------------------------------
# Root resource method (for paths like "/")
# -----------------------------------------------------------------------------

resource "aws_api_gateway_method" "root_any" {
  count = var.enable_admin_api ? 1 : 0

  rest_api_id          = aws_api_gateway_rest_api.admin[0].id
  resource_id          = aws_api_gateway_rest_api.admin[0].root_resource_id
  http_method          = "ANY"
  authorization        = "COGNITO_USER_POOLS"
  authorizer_id        = aws_api_gateway_authorizer.cognito[0].id
  authorization_scopes = [var.required_scope]
}

# MOCK integration for root resource
resource "aws_api_gateway_integration" "root_mock" {
  count = var.enable_admin_api ? 1 : 0

  rest_api_id = aws_api_gateway_rest_api.admin[0].id
  resource_id = aws_api_gateway_rest_api.admin[0].root_resource_id
  http_method = aws_api_gateway_method.root_any[0].http_method
  type        = "MOCK"

  request_templates = {
    "application/json" = jsonencode({ statusCode = 200 })
  }
}

resource "aws_api_gateway_method_response" "root_200" {
  count = var.enable_admin_api ? 1 : 0

  rest_api_id = aws_api_gateway_rest_api.admin[0].id
  resource_id = aws_api_gateway_rest_api.admin[0].root_resource_id
  http_method = aws_api_gateway_method.root_any[0].http_method
  status_code = "200"
}

resource "aws_api_gateway_integration_response" "root_200" {
  count = var.enable_admin_api ? 1 : 0

  rest_api_id = aws_api_gateway_rest_api.admin[0].id
  resource_id = aws_api_gateway_rest_api.admin[0].root_resource_id
  http_method = aws_api_gateway_method.root_any[0].http_method
  status_code = aws_api_gateway_method_response.root_200[0].status_code

  response_templates = {
    "application/json" = jsonencode({ message = "Admin API — use /teams, /budgets, /routing, /scanner, or /pricing" })
  }
}

# -----------------------------------------------------------------------------
# Deployment + Stage
# -----------------------------------------------------------------------------

resource "aws_api_gateway_deployment" "admin" {
  count = var.enable_admin_api ? 1 : 0

  rest_api_id = aws_api_gateway_rest_api.admin[0].id

  triggers = {
    redeployment = sha1(jsonencode([
      # Root MOCK
      aws_api_gateway_method.root_any[0].id,
      aws_api_gateway_integration.root_mock[0].id,
      # /teams
      aws_api_gateway_resource.teams[0].id,
      aws_api_gateway_resource.teams_proxy[0].id,
      aws_api_gateway_method.teams_any[0].id,
      aws_api_gateway_method.teams_proxy_any[0].id,
      aws_api_gateway_integration.teams[0].id,
      aws_api_gateway_integration.teams_proxy[0].id,
      # /budgets
      aws_api_gateway_resource.budgets[0].id,
      aws_api_gateway_resource.budgets_proxy[0].id,
      aws_api_gateway_method.budgets_any[0].id,
      aws_api_gateway_method.budgets_proxy_any[0].id,
      aws_api_gateway_integration.budgets[0].id,
      aws_api_gateway_integration.budgets_proxy[0].id,
      # /routing
      aws_api_gateway_resource.routing[0].id,
      aws_api_gateway_resource.routing_proxy[0].id,
      aws_api_gateway_method.routing_any[0].id,
      aws_api_gateway_method.routing_proxy_any[0].id,
      aws_api_gateway_integration.routing[0].id,
      aws_api_gateway_integration.routing_proxy[0].id,
      # /scanner
      aws_api_gateway_resource.scanner[0].id,
      aws_api_gateway_resource.scanner_proxy[0].id,
      aws_api_gateway_method.scanner_any[0].id,
      aws_api_gateway_method.scanner_proxy_any[0].id,
      aws_api_gateway_integration.scanner[0].id,
      aws_api_gateway_integration.scanner_proxy[0].id,
      # /pricing
      aws_api_gateway_resource.pricing[0].id,
      aws_api_gateway_resource.pricing_proxy[0].id,
      aws_api_gateway_method.pricing_any[0].id,
      aws_api_gateway_method.pricing_proxy_any[0].id,
      aws_api_gateway_integration.pricing[0].id,
      aws_api_gateway_integration.pricing_proxy[0].id,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [
    aws_api_gateway_method.root_any,
    aws_api_gateway_integration.root_mock,
    aws_api_gateway_integration.teams,
    aws_api_gateway_integration.teams_proxy,
    aws_api_gateway_integration.budgets,
    aws_api_gateway_integration.budgets_proxy,
    aws_api_gateway_integration.routing,
    aws_api_gateway_integration.routing_proxy,
    aws_api_gateway_integration.scanner,
    aws_api_gateway_integration.scanner_proxy,
    aws_api_gateway_integration.pricing,
    aws_api_gateway_integration.pricing_proxy,
  ]
}

resource "aws_api_gateway_stage" "admin" {
  #checkov:skip=CKV_AWS_120:API caching not needed for admin APIs
  #checkov:skip=CKV2_AWS_29:WAF on ALB covers inference path; admin API is low-traffic
  count = var.enable_admin_api ? 1 : 0

  deployment_id = aws_api_gateway_deployment.admin[0].id
  rest_api_id   = aws_api_gateway_rest_api.admin[0].id
  stage_name    = var.environment

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.admin[0].arn
    format = jsonencode({
      requestId      = "$context.requestId"
      ip             = "$context.identity.sourceIp"
      caller         = "$context.identity.caller"
      user           = "$context.identity.user"
      requestTime    = "$context.requestTime"
      httpMethod     = "$context.httpMethod"
      resourcePath   = "$context.resourcePath"
      status         = "$context.status"
      protocol       = "$context.protocol"
      responseLength = "$context.responseLength"
    })
  }
}

# -----------------------------------------------------------------------------
# CloudWatch Logging
# -----------------------------------------------------------------------------

resource "aws_api_gateway_method_settings" "admin" {
  count = var.enable_admin_api ? 1 : 0

  rest_api_id = aws_api_gateway_rest_api.admin[0].id
  stage_name  = aws_api_gateway_stage.admin[0].stage_name
  method_path = "*/*"

  settings {
    logging_level   = "INFO"
    metrics_enabled = true
  }
}

resource "aws_cloudwatch_log_group" "admin" {
  #checkov:skip=CKV_AWS_158:KMS encryption not required for low-sensitivity admin API logs
  #checkov:skip=CKV_AWS_338:90-day retention sufficient for admin API logs
  count = var.enable_admin_api ? 1 : 0

  name              = "/aws/apigateway/${var.project_name}-${var.environment}-admin"
  retention_in_days = 90
}
