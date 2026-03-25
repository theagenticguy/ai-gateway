# =============================================================================
# CloudWatch Alarms — configurable thresholds, SNS notifications
# =============================================================================

# -----------------------------------------------------------------------------
# Budget Utilization — fires when daily spend exceeds threshold
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "budget_utilization" {
  alarm_name          = "${var.project_name}-${var.environment}-budget-utilization"
  alarm_description   = "Daily estimated cost exceeds ${var.budget_alarm_threshold_pct}% of $${var.budget_limit_daily_usd} budget"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = var.budget_limit_daily_usd * (var.budget_alarm_threshold_pct / 100)

  metric_name = "EstimatedCostUsd"
  namespace   = "AIGateway"
  period      = 86400
  statistic   = "Sum"

  alarm_actions             = local.alarm_topic_arns
  ok_actions                = local.alarm_topic_arns
  insufficient_data_actions = []
  treat_missing_data        = "notBreaching"

  tags = local.common_tags
}

# -----------------------------------------------------------------------------
# High Error Rate — fires when >5% of requests return 4xx/5xx for 5 min
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "high_error_rate" {
  alarm_name          = "${var.project_name}-${var.environment}-high-error-rate"
  alarm_description   = "Error rate exceeds ${var.error_rate_threshold_pct}% for ${var.error_rate_evaluation_minutes} minutes"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = var.error_rate_evaluation_minutes
  threshold           = var.error_rate_threshold_pct

  metric_query {
    id          = "errors"
    return_data = false
    metric {
      metric_name = "RequestCount"
      namespace   = "AIGateway"
      period      = 60
      stat        = "Sum"
      dimensions = {
        StatusClass = "error"
      }
    }
  }

  metric_query {
    id          = "total"
    return_data = false
    metric {
      metric_name = "RequestCount"
      namespace   = "AIGateway"
      period      = 60
      stat        = "Sum"
    }
  }

  metric_query {
    id          = "error_rate"
    expression  = "IF(total > 0, (errors / total) * 100, 0)"
    label       = "Error Rate %"
    return_data = true
  }

  alarm_actions             = local.alarm_topic_arns
  ok_actions                = local.alarm_topic_arns
  insufficient_data_actions = []
  treat_missing_data        = "notBreaching"

  tags = local.common_tags
}

# -----------------------------------------------------------------------------
# High P99 Latency — fires when P99 exceeds threshold for 5 min
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "high_p99_latency" {
  alarm_name          = "${var.project_name}-${var.environment}-high-p99-latency"
  alarm_description   = "P99 latency exceeds ${var.p99_latency_threshold_ms}ms for ${var.latency_evaluation_minutes} minutes"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = var.latency_evaluation_minutes
  threshold           = var.p99_latency_threshold_ms

  metric_name        = "ResponseTime"
  namespace          = "AIGateway"
  period             = 60
  extended_statistic = "p99"

  alarm_actions             = local.alarm_topic_arns
  ok_actions                = local.alarm_topic_arns
  insufficient_data_actions = []
  treat_missing_data        = "notBreaching"

  tags = local.common_tags
}

# -----------------------------------------------------------------------------
# Provider Down — fires when a provider has zero requests for 10 min
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "provider_down" {
  for_each = toset(local.providers_list)

  alarm_name          = "${var.project_name}-${var.environment}-provider-down-${each.key}"
  alarm_description   = "Provider ${each.key} has had zero requests for ${var.provider_down_minutes} minutes"
  comparison_operator = "LessThanOrEqualToThreshold"
  evaluation_periods  = var.provider_down_minutes
  threshold           = 0

  metric_name = "RequestCount"
  namespace   = "AIGateway"
  period      = 60
  statistic   = "Sum"
  dimensions = {
    Provider = each.key
  }

  alarm_actions             = local.alarm_topic_arns
  ok_actions                = local.alarm_topic_arns
  insufficient_data_actions = []
  treat_missing_data        = "breaching"

  tags = local.common_tags
}
