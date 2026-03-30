terraform {
  required_version = "~> 1.14"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.22"
    }
  }
}

# =============================================================================
# KMS — encryption for CloudWatch Logs
# =============================================================================

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

# =============================================================================
# Log Groups
# =============================================================================

resource "aws_cloudwatch_log_group" "gateway" {
  #checkov:skip=CKV_AWS_158:KMS encryption planned for prod
  #checkov:skip=CKV_AWS_338:365-day retention planned for prod
  name              = "/ecs/${var.project_name}/gateway"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.logs.arn
}

resource "aws_cloudwatch_log_group" "otel" {
  #checkov:skip=CKV_AWS_158:KMS encryption planned for prod
  #checkov:skip=CKV_AWS_338:365-day retention planned for prod
  name              = "/ecs/${var.project_name}/otel"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.logs.arn
}

# =============================================================================
# Saved Query Definitions (legacy — kept for backward compat)
# =============================================================================

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

# =============================================================================
# SNS Topic for Alarms
# =============================================================================

resource "aws_sns_topic" "alarms" {
  #checkov:skip=CKV_AWS_26:SNS encryption planned for prod
  count = length(var.alarm_sns_topic_arns) == 0 ? 1 : 0
  name  = "${var.project_name}-${var.environment}-alarms"
  tags  = local.common_tags
}

# =============================================================================
# Dashboard — Comprehensive per-tenant observability
# =============================================================================

locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }

  ns     = "AIGateway"
  region = var.aws_region
  gw_log = aws_cloudwatch_log_group.gateway.name
  period = 300
  h6     = 6
  w12    = 12
  w8     = 8

  alarm_topic_arns = length(var.alarm_sns_topic_arns) > 0 ? var.alarm_sns_topic_arns : (length(aws_sns_topic.alarms) > 0 ? [aws_sns_topic.alarms[0].arn] : [])

  providers_list = ["bedrock", "openai", "anthropic", "google"]
  teams_list     = var.teams_list

  # ---------------------------------------------------------------------------
  # Row 1 — Overview (y=0): total requests, total cost, active teams, error rate
  # ---------------------------------------------------------------------------

  row1_total_requests = {
    type   = "metric"
    x      = 0
    y      = 0
    width  = 6
    height = local.h6
    properties = {
      metrics = [
        [local.ns, "RequestCount", { stat = "Sum", label = "Total Requests" }]
      ]
      period = local.period
      region = local.region
      title  = "Total Requests"
      view   = "singleValue"
      stat   = "Sum"
    }
  }

  row1_total_cost = {
    type   = "metric"
    x      = 6
    y      = 0
    width  = 6
    height = local.h6
    properties = {
      metrics = [
        [local.ns, "EstimatedCostUsd", { stat = "Sum", label = "Total Cost (USD)" }]
      ]
      period = local.period
      region = local.region
      title  = "Total Cost (USD)"
      view   = "singleValue"
      stat   = "Sum"
    }
  }

  row1_active_teams = {
    type   = "log"
    x      = 12
    y      = 0
    width  = 6
    height = local.h6
    properties = {
      query  = "SOURCE '${local.gw_log}' | fields @timestamp | filter ispresent(`req.headers.x-portkey-provider`) | stats count_distinct(`req.headers.x-team-id`) as active_teams by bin(1h) | sort bin(1h) desc | limit 1"
      region = local.region
      title  = "Active Teams"
      view   = "singleValue"
    }
  }

  row1_error_rate = {
    type   = "log"
    x      = 18
    y      = 0
    width  = 6
    height = local.h6
    properties = {
      query  = "SOURCE '${local.gw_log}' | fields @timestamp | filter ispresent(res.statusCode) | stats (sum(res.statusCode >= 400) / count(*)) * 100 as error_rate_pct"
      region = local.region
      title  = "Error Rate (%)"
      view   = "singleValue"
    }
  }

  # ---------------------------------------------------------------------------
  # Row 2 — Cost by Team (y=6): stacked bar by team, table of top 10
  # ---------------------------------------------------------------------------

  row2_cost_by_team_bar = {
    type   = "metric"
    x      = 0
    y      = 6
    width  = local.w12
    height = local.h6
    properties = {
      metrics = [
        for p in local.providers_list : [local.ns, "EstimatedCostUsd", "Provider", p]
      ]
      period  = 3600
      stat    = "Sum"
      region  = local.region
      title   = "Cost by Provider (Hourly)"
      view    = "bar"
      stacked = true
    }
  }

  row2_cost_by_team_table = {
    type   = "log"
    x      = 12
    y      = 6
    width  = local.w12
    height = local.h6
    properties = {
      query  = "SOURCE '${local.gw_log}' | fields @timestamp, provider, model, estimatedCostUsd | filter ispresent(estimatedCostUsd) | stats sum(estimatedCostUsd) as total_cost, count(*) as requests by `req.headers.x-team-id` as team | sort total_cost desc | limit 10"
      region = local.region
      title  = "Top 10 Teams by Cost"
      view   = "table"
    }
  }

  # ---------------------------------------------------------------------------
  # Row 3 — Token Breakdown (y=12): line chart: input/output/cached by provider; pie by model
  # ---------------------------------------------------------------------------

  row3_token_line = {
    type   = "metric"
    x      = 0
    y      = 12
    width  = local.w12
    height = local.h6
    properties = {
      metrics = flatten([
        for p in local.providers_list : [
          [local.ns, "PromptTokens", "Provider", p],
          [local.ns, "CompletionTokens", "Provider", p],
          [local.ns, "CachedTokens", "Provider", p],
        ]
      ])
      period = local.period
      stat   = "Sum"
      region = local.region
      title  = "Token Usage: Input / Output / Cached by Provider"
      view   = "timeSeries"
    }
  }

  row3_token_pie = {
    type   = "log"
    x      = 12
    y      = 12
    width  = local.w12
    height = local.h6
    properties = {
      query  = "SOURCE '${local.gw_log}' | fields @timestamp, model, usage.prompt_tokens, usage.completion_tokens | filter ispresent(usage.prompt_tokens) | stats sum(usage.prompt_tokens + usage.completion_tokens) as total_tokens by model | sort total_tokens desc | limit 10"
      region = local.region
      title  = "Token Distribution by Model"
      view   = "pie"
    }
  }

  # ---------------------------------------------------------------------------
  # Row 4 — Performance (y=18): P50/P95/P99 latency, TTFT, error rate by provider
  # ---------------------------------------------------------------------------

  row4_latency = {
    type   = "log"
    x      = 0
    y      = 18
    width  = local.w8
    height = local.h6
    properties = {
      query  = "SOURCE '${local.gw_log}' | fields @timestamp, responseTime | filter ispresent(responseTime) | stats pct(responseTime, 50) as p50_ms, pct(responseTime, 95) as p95_ms, pct(responseTime, 99) as p99_ms by `req.headers.x-portkey-provider` as provider | sort p99_ms desc"
      region = local.region
      title  = "Latency P50 / P95 / P99 by Provider (ms)"
      view   = "table"
    }
  }

  row4_ttft = {
    type   = "metric"
    x      = 8
    y      = 18
    width  = local.w8
    height = local.h6
    properties = {
      metrics = [
        for p in local.providers_list : [local.ns, "TimeToFirstToken", "Provider", p]
      ]
      period = local.period
      stat   = "p99"
      region = local.region
      title  = "Time to First Token P99 by Provider (ms)"
      view   = "timeSeries"
    }
  }

  row4_errors_by_provider = {
    type   = "log"
    x      = 16
    y      = 18
    width  = local.w8
    height = local.h6
    properties = {
      query  = "SOURCE '${local.gw_log}' | fields @timestamp | filter ispresent(res.statusCode) | stats count(*) as total, sum(res.statusCode >= 400) as errors, (sum(res.statusCode >= 400) / count(*)) * 100 as error_pct by `req.headers.x-portkey-provider` as provider | sort error_pct desc"
      region = local.region
      title  = "Error Rate by Provider (%)"
      view   = "bar"
    }
  }

  # ---------------------------------------------------------------------------
  # Row 5 — Budget Health (y=24): gauge for utilization, alarm status
  # ---------------------------------------------------------------------------

  row5_budget_gauge = {
    type   = "metric"
    x      = 0
    y      = 24
    width  = local.w12
    height = local.h6
    properties = {
      metrics = [
        [local.ns, "EstimatedCostUsd", { stat = "Sum", label = "Current Spend" }]
      ]
      period = 86400
      region = local.region
      title  = "Daily Budget Utilization"
      view   = "gauge"
      stat   = "Sum"
      yAxis = {
        left = {
          min = 0
          max = var.budget_limit_daily_usd
        }
      }
    }
  }

  row5_alarm_status = {
    type   = "alarm"
    x      = 12
    y      = 24
    width  = local.w12
    height = local.h6
    properties = {
      alarms = [
        aws_cloudwatch_metric_alarm.budget_utilization.arn,
        aws_cloudwatch_metric_alarm.high_error_rate.arn,
        aws_cloudwatch_metric_alarm.high_p99_latency.arn,
      ]
      title  = "Alarm Status"
      states = [{ value = "ALARM", label = "In Alarm" }, { value = "OK", label = "OK" }]
    }
  }

  # ---------------------------------------------------------------------------
  # Row 6 — Cache (y=30): hit rate, tokens saved, dollar savings
  # ---------------------------------------------------------------------------

  row6_cache_hit_rate = {
    type   = "metric"
    x      = 0
    y      = 30
    width  = local.w8
    height = local.h6
    properties = {
      metrics = [
        [local.ns, "CacheHits", { stat = "Sum", id = "hits", visible = false }],
        [local.ns, "CacheMisses", { stat = "Sum", id = "misses", visible = false }],
        [{ expression = "100 * hits / (hits + misses)", label = "Cache Hit Rate (%)", id = "rate" }],
      ]
      period = local.period
      region = local.region
      title  = "Cache Hit Rate (%)"
      view   = "timeSeries"
    }
  }

  row6_cache_tokens_saved = {
    type   = "metric"
    x      = 8
    y      = 30
    width  = local.w8
    height = local.h6
    properties = {
      metrics = [
        [local.ns, "CachedTokens", { stat = "Sum", label = "Tokens Saved by Cache" }]
      ]
      period = local.period
      region = local.region
      title  = "Tokens Saved by Cache"
      view   = "timeSeries"
      stat   = "Sum"
    }
  }

  row6_cache_cost_savings = {
    type   = "metric"
    x      = 16
    y      = 30
    width  = local.w8
    height = local.h6
    properties = {
      metrics = [
        [local.ns, "CacheCostSavingsUsd", { stat = "Sum", label = "Cache Savings (USD)" }]
      ]
      period = local.period
      region = local.region
      title  = "Cache Cost Savings (USD)"
      view   = "singleValue"
      stat   = "Sum"
    }
  }

  # ---------------------------------------------------------------------------
  # Row 7 — Cache by Team (y=36): hits/misses by team, savings by team
  # ---------------------------------------------------------------------------

  row7_cache_by_team = {
    type   = "metric"
    x      = 0
    y      = 36
    width  = local.w12
    height = local.h6
    properties = {
      metrics = flatten([
        for t in local.teams_list : [
          [local.ns, "CacheHitsByTeam", "Team", t],
          [local.ns, "CacheMissesByTeam", "Team", t],
        ]
      ])
      period = local.period
      stat   = "Sum"
      region = local.region
      title  = "Cache Hits / Misses by Team"
      view   = "timeSeries"
    }
  }

  row7_cache_savings_by_team = {
    type   = "metric"
    x      = 12
    y      = 36
    width  = local.w12
    height = local.h6
    properties = {
      metrics = [
        for t in local.teams_list : [local.ns, "CacheSavingsByTeam", "Team", t]
      ]
      period = 3600
      stat   = "Sum"
      region = local.region
      title  = "Cache Savings by Team (USD)"
      view   = "bar"
    }
  }

  # ---------------------------------------------------------------------------
  # Assemble all widgets
  # ---------------------------------------------------------------------------

  overview_widgets = [
    local.row1_total_requests,
    local.row1_total_cost,
    local.row1_active_teams,
    local.row1_error_rate,
  ]

  performance_widgets = [
    local.row4_latency,
    local.row4_ttft,
    local.row4_errors_by_provider,
  ]
}

locals {
  # Conditional widget lists — use for-expression to avoid tuple length mismatch
  cost_widget_candidates = [
    local.row2_cost_by_team_bar,
    local.row2_cost_by_team_table,
    local.row3_token_line,
    local.row3_token_pie,
    local.row5_budget_gauge,
    local.row5_alarm_status,
  ]
  cost_widgets = [for w in local.cost_widget_candidates : w if var.enable_cost_widgets]

  cache_widget_candidates = [
    local.row6_cache_hit_rate,
    local.row6_cache_tokens_saved,
    local.row6_cache_cost_savings,
    local.row7_cache_by_team,
    local.row7_cache_savings_by_team,
  ]
  cache_widgets = [for w in local.cache_widget_candidates : w if var.enable_cache_widgets]
}

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "${var.project_name}-${var.environment}"
  dashboard_body = jsonencode({
    widgets = concat(
      local.overview_widgets,
      local.cost_widgets,
      local.performance_widgets,
      local.cache_widgets,
    )
  })
}
