# =============================================================================
# Deployment + Stage — caching, throttling, access logging (ADR-016)
# =============================================================================

# Redeploy when any wired method/integration changes. The /auth/token route
# ids are folded into the trigger so adding it forces a fresh deployment.
resource "aws_api_gateway_deployment" "control" {
  count       = var.enable_api_foundation ? 1 : 0
  rest_api_id = var.rest_api_id

  triggers = {
    redeploy = sha1(jsonencode([
      aws_api_gateway_resource.auth_token[0].id,
      aws_api_gateway_method.auth_token_post[0].id,
      aws_api_gateway_integration.auth_token[0].id,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }
}

# -----------------------------------------------------------------------------
# Access log group (structured JSON access logs)
# -----------------------------------------------------------------------------

#checkov:skip=CKV_AWS_158:CloudWatch log KMS encryption is out of scope for this self-contained module
resource "aws_cloudwatch_log_group" "access" {
  count             = var.enable_api_foundation ? 1 : 0
  name              = "/aws/apigateway/${local.name}/access"
  retention_in_days = var.log_retention_days
}

# -----------------------------------------------------------------------------
# Stage — method cache + access logging + X-Ray
# -----------------------------------------------------------------------------

#checkov:skip=CKV2_AWS_29:WAF is associated below via aws_wafv2_web_acl_association
#checkov:skip=CKV2_AWS_4:Logging level is set on the method settings below
#checkov:skip=CKV_AWS_120:Caching is enabled on idempotent GET routes via method settings, not stage-wide
resource "aws_api_gateway_stage" "control" {
  count                 = var.enable_api_foundation ? 1 : 0
  rest_api_id           = var.rest_api_id
  deployment_id         = aws_api_gateway_deployment.control[0].id
  stage_name            = var.stage_name
  xray_tracing_enabled  = true
  cache_cluster_enabled = var.cache_enabled
  cache_cluster_size    = var.cache_cluster_size

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.access[0].arn
    format = jsonencode({
      requestId          = "$context.requestId"
      ip                 = "$context.identity.sourceIp"
      caller             = "$context.identity.caller"
      user               = "$context.authorizer.claims.sub"
      team               = "$context.authorizer.claims.custom:team"
      httpMethod         = "$context.httpMethod"
      resourcePath       = "$context.resourcePath"
      status             = "$context.status"
      responseLength     = "$context.responseLength"
      latency            = "$context.responseLatency"
      integrationLatency = "$context.integrationLatency"
      userAgent          = "$context.identity.userAgent"
    })
  }
}

# -----------------------------------------------------------------------------
# Method settings — default throttle + logging/metrics; cache on GET routes
# -----------------------------------------------------------------------------

resource "aws_api_gateway_method_settings" "default" {
  count       = var.enable_api_foundation ? 1 : 0
  rest_api_id = var.rest_api_id
  stage_name  = aws_api_gateway_stage.control[0].stage_name
  method_path = "*/*"

  settings {
    throttling_rate_limit  = var.throttle_rate_limit
    throttling_burst_limit = var.throttle_burst_limit
    metrics_enabled        = true
    logging_level          = "INFO"
    data_trace_enabled     = false # never log request/response bodies (may carry PII)
    caching_enabled        = false # default off; turned on per-GET-route below
  }
}

# Idempotent, slowly-changing GET reads (pricing, catalog) — cache at the edge
# so steady-state reads never reach Lambda.
resource "aws_api_gateway_method_settings" "cached_gets" {
  for_each    = var.enable_api_foundation && var.cache_enabled ? toset(var.cached_get_paths) : toset([])
  rest_api_id = var.rest_api_id
  stage_name  = aws_api_gateway_stage.control[0].stage_name
  method_path = each.value # e.g. "pricing/GET"

  settings {
    caching_enabled      = true
    cache_ttl_in_seconds = var.cache_ttl_seconds
    cache_data_encrypted = true
    metrics_enabled      = true
    logging_level        = "INFO"
  }
}

# -----------------------------------------------------------------------------
# Usage plan + per-tenant API keys (defense in depth atop Cognito)
# -----------------------------------------------------------------------------

resource "aws_api_gateway_usage_plan" "control" {
  count = var.enable_api_foundation ? 1 : 0
  name  = "${local.name}-usage-plan"

  api_stages {
    api_id = var.rest_api_id
    stage  = aws_api_gateway_stage.control[0].stage_name
  }

  throttle_settings {
    rate_limit  = var.throttle_rate_limit
    burst_limit = var.throttle_burst_limit
  }

  quota_settings {
    limit  = var.quota_limit
    period = "DAY"
  }
}
