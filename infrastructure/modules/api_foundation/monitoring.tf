# =============================================================================
# Monitoring — alarms + dashboard for the control plane (ADR-016)
# =============================================================================
# Pairs with the gwcore.telemetry EMF metrics (AIGateway/ControlPlane) and the
# API Gateway stage metrics. Alarms publish to an SNS topic the platform team
# owns; the dashboard is the operator's at-a-glance view.

# 5xx surge on the control plane.
resource "aws_cloudwatch_metric_alarm" "control_5xx" {
  count               = var.enable_api_foundation ? 1 : 0
  alarm_name          = "${local.name}-5xx"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "5XXError"
  namespace           = "AWS/ApiGateway"
  period              = 300
  statistic           = "Sum"
  threshold           = var.alarm_5xx_threshold
  alarm_description   = "Control-plane API 5xx errors over threshold"
  treat_missing_data  = "notBreaching"
  dimensions = {
    ApiName = "${var.project_name}-${var.environment}-admin-api"
    Stage   = var.stage_name
  }
  alarm_actions = var.alarm_sns_topic_arn == "" ? [] : [var.alarm_sns_topic_arn]
}

# p99 latency.
resource "aws_cloudwatch_metric_alarm" "control_latency" {
  count               = var.enable_api_foundation ? 1 : 0
  alarm_name          = "${local.name}-latency-p99"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "Latency"
  namespace           = "AWS/ApiGateway"
  period              = 300
  extended_statistic  = "p99"
  threshold           = var.alarm_latency_p99_ms
  alarm_description   = "Control-plane API p99 latency over threshold"
  treat_missing_data  = "notBreaching"
  dimensions = {
    ApiName = "${var.project_name}-${var.environment}-admin-api"
    Stage   = var.stage_name
  }
  alarm_actions = var.alarm_sns_topic_arn == "" ? [] : [var.alarm_sns_topic_arn]
}

# Authorization-denial surge — a spike of 401/403 can signal a misconfigured
# client or an attack. Scoped to the AuthzDenied metric (emitted only on
# 401/403), NOT all TokenExchangeError codes, so validation/upstream errors
# don't trip it.
resource "aws_cloudwatch_metric_alarm" "authz_denials" {
  count               = var.enable_api_foundation ? 1 : 0
  alarm_name          = "${local.name}-authz-denials"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "AuthzDenied"
  namespace           = "AIGateway/ControlPlane"
  period              = 300
  statistic           = "Sum"
  threshold           = var.alarm_authz_denial_threshold
  alarm_description   = "Control-plane authorization denials (401/403) over threshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_sns_topic_arn == "" ? [] : [var.alarm_sns_topic_arn]
}

resource "aws_cloudwatch_dashboard" "control" {
  count          = var.enable_api_foundation ? 1 : 0
  dashboard_name = "${local.name}-control-plane"
  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "Requests & errors"
          region = var.aws_region
          metrics = [
            ["AWS/ApiGateway", "Count", "ApiName", "${var.project_name}-${var.environment}-admin-api", "Stage", var.stage_name],
            [".", "4XXError", ".", ".", ".", "."],
            [".", "5XXError", ".", ".", ".", "."],
          ]
          stat   = "Sum"
          period = 300
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "Latency (p50 / p90 / p99)"
          region = var.aws_region
          metrics = [
            ["AWS/ApiGateway", "Latency", "ApiName", "${var.project_name}-${var.environment}-admin-api", "Stage", var.stage_name, { stat = "p50" }],
            ["...", { stat = "p90" }],
            ["...", { stat = "p99" }],
          ]
          period = 300
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "Token exchange & cache"
          region = var.aws_region
          metrics = [
            ["AIGateway/ControlPlane", "TokenExchange"],
            [".", "TokenExchangeError"],
            ["AWS/ApiGateway", "CacheHitCount", "ApiName", "${var.project_name}-${var.environment}-admin-api", "Stage", var.stage_name],
            [".", "CacheMissCount", ".", ".", ".", "."],
          ]
          stat   = "Sum"
          period = 300
        }
      },
    ]
  })
}
