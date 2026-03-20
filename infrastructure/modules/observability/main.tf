resource "aws_kms_key" "logs" {
  description             = "KMS key for AI Gateway CloudWatch Logs encryption"
  deletion_window_in_days = 7
  enable_key_rotation     = true
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Sid = "EnableRootAccount", Effect = "Allow", Principal = { AWS = "arn:aws:iam::${var.account_id}:root" }, Action = "kms:*", Resource = "*" },
      { Sid    = "AllowCloudWatchLogs", Effect = "Allow", Principal = { Service = "logs.${var.aws_region}.amazonaws.com" },
        Action = ["kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"], Resource = "*",
      Condition = { ArnLike = { "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:*" } } }
    ]
  })
  tags = { Name = "ai-gateway-logs" }
}

resource "aws_kms_alias" "logs" {
  name          = "alias/ai-gateway-logs"
  target_key_id = aws_kms_key.logs.key_id
}

resource "aws_cloudwatch_log_group" "gateway" {
  name              = "/ecs/${var.project_name}/gateway"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.logs.arn
}

resource "aws_cloudwatch_log_group" "otel" {
  name              = "/ecs/${var.project_name}/otel"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.logs.arn
}

resource "aws_cloudwatch_query_definition" "requests_per_hour" {
  name            = "${var.project_name}/requests-per-hour-by-provider"
  log_group_names = [aws_cloudwatch_log_group.gateway.name]
  query_string    = "fields @timestamp, @message | filter ispresent(responseTime) | stats count(*) as requests by bin(1h), `req.headers.x-portkey-provider` as provider | sort bin(1h) desc"
}

resource "aws_cloudwatch_query_definition" "error_rate" {
  name            = "${var.project_name}/error-rate-by-provider"
  log_group_names = [aws_cloudwatch_log_group.gateway.name]
  query_string    = "fields @timestamp, @message | filter ispresent(res.statusCode) | stats count(*) as total, sum(res.statusCode >= 400) as errors, (sum(res.statusCode >= 400) / count(*)) * 100 as error_pct by `req.headers.x-portkey-provider` as provider | sort error_pct desc"
}

resource "aws_cloudwatch_query_definition" "latency_percentiles" {
  name            = "${var.project_name}/latency-percentiles-by-provider"
  log_group_names = [aws_cloudwatch_log_group.gateway.name]
  query_string    = "fields @timestamp, responseTime | filter ispresent(responseTime) | stats pct(responseTime, 50) as p50, pct(responseTime, 95) as p95, pct(responseTime, 99) as p99, avg(responseTime) as avg_ms by `req.headers.x-portkey-provider` as provider | sort p99 desc"
}

resource "aws_cloudwatch_query_definition" "requests_by_endpoint" {
  name            = "${var.project_name}/requests-by-endpoint"
  log_group_names = [aws_cloudwatch_log_group.gateway.name]
  query_string    = "fields @timestamp, req.url | filter ispresent(req.url) | stats count(*) as requests by `req.url` as endpoint | sort requests desc | limit 20"
}

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "${var.project_name}-${var.environment}"
  dashboard_body = jsonencode({
    widgets = concat([
      { type = "log", x = 0, y = 0, width = 12, height = 6, properties = {
        query = "SOURCE '${aws_cloudwatch_log_group.gateway.name}' | fields @timestamp, @message | filter ispresent(responseTime) | stats count(*) as requests by bin(1h), `req.headers.x-portkey-provider` as provider | sort bin(1h) desc",
      region = var.aws_region, stacked = false, view = "timeSeries", title = "Requests per Hour by Provider" } },
      { type = "log", x = 12, y = 0, width = 12, height = 6, properties = {
        query = "SOURCE '${aws_cloudwatch_log_group.gateway.name}' | fields @timestamp, @message | filter ispresent(res.statusCode) | stats count(*) as total, sum(res.statusCode >= 400) as errors, (sum(res.statusCode >= 400) / count(*)) * 100 as error_pct by `req.headers.x-portkey-provider` as provider | sort error_pct desc",
      region = var.aws_region, stacked = false, view = "table", title = "Error Rate by Provider" } },
      { type = "log", x = 0, y = 6, width = 12, height = 6, properties = {
        query = "SOURCE '${aws_cloudwatch_log_group.gateway.name}' | fields @timestamp, responseTime | filter ispresent(responseTime) | stats pct(responseTime, 50) as p50, pct(responseTime, 95) as p95, pct(responseTime, 99) as p99, avg(responseTime) as avg_ms by `req.headers.x-portkey-provider` as provider | sort p99 desc",
      region = var.aws_region, stacked = false, view = "table", title = "Latency Percentiles by Provider (ms)" } },
      { type = "log", x = 12, y = 6, width = 12, height = 6, properties = {
        query = "SOURCE '${aws_cloudwatch_log_group.gateway.name}' | fields @timestamp, req.url | filter ispresent(req.url) | stats count(*) as requests by `req.url` as endpoint | sort requests desc | limit 20",
      region = var.aws_region, stacked = false, view = "table", title = "Top Endpoints by Request Count" } },
      ], var.enable_cost_widgets ? [
      { type = "metric", x = 0, y = 12, width = 12, height = 6, properties = {
        metrics = [["AIGateway", "TokensUsed", "Provider", "bedrock"], ["AIGateway", "TokensUsed", "Provider", "openai"], ["AIGateway", "TokensUsed", "Provider", "anthropic"], ["AIGateway", "TokensUsed", "Provider", "google"]],
      period = 300, stat = "Sum", region = var.aws_region, title = "Token Usage by Provider" } },
      { type = "metric", x = 12, y = 12, width = 12, height = 6, properties = {
        metrics = [["AIGateway", "EstimatedCostUsd", "Provider", "bedrock"], ["AIGateway", "EstimatedCostUsd", "Provider", "openai"], ["AIGateway", "EstimatedCostUsd", "Provider", "anthropic"], ["AIGateway", "EstimatedCostUsd", "Provider", "google"]],
      period = 300, stat = "Sum", region = var.aws_region, title = "Estimated Cost by Provider (USD)" } },
    ] : [])
  })
}
