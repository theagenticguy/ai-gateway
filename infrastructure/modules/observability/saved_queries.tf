# =============================================================================
# CloudWatch Logs Insights — Saved Queries
# =============================================================================

resource "aws_cloudwatch_query_definition" "requests_by_model" {
  name            = "${var.project_name}/requests-by-model"
  log_group_names = [aws_cloudwatch_log_group.gateway.name]
  query_string    = <<-EOQ
    fields @timestamp, model, `req.headers.x-portkey-provider` as provider
    | filter ispresent(responseTime)
    | stats count(*) as requests by model, provider
    | sort requests desc
    | limit 20
  EOQ
}

resource "aws_cloudwatch_query_definition" "errors_by_provider" {
  name            = "${var.project_name}/errors-by-provider-detail"
  log_group_names = [aws_cloudwatch_log_group.gateway.name]
  query_string    = <<-EOQ
    fields @timestamp, res.statusCode, `req.headers.x-portkey-provider` as provider, @message
    | filter res.statusCode >= 400
    | stats count(*) as errors by provider, res.statusCode
    | sort errors desc
    | limit 20
  EOQ
}

resource "aws_cloudwatch_query_definition" "latency_percentiles_detail" {
  name            = "${var.project_name}/latency-percentiles-detail"
  log_group_names = [aws_cloudwatch_log_group.gateway.name]
  query_string    = <<-EOQ
    fields @timestamp, responseTime, `req.headers.x-portkey-provider` as provider, model
    | filter ispresent(responseTime)
    | stats pct(responseTime, 50) as p50,
            pct(responseTime, 90) as p90,
            pct(responseTime, 95) as p95,
            pct(responseTime, 99) as p99,
            max(responseTime) as max_ms,
            avg(responseTime) as avg_ms,
            count(*) as requests
      by provider, model
    | sort p99 desc
  EOQ
}

resource "aws_cloudwatch_query_definition" "cost_by_team" {
  name            = "${var.project_name}/cost-by-team"
  log_group_names = [aws_cloudwatch_log_group.gateway.name]
  query_string    = <<-EOQ
    fields @timestamp, `req.headers.x-team-id` as team, provider, model,
           usage.prompt_tokens, usage.completion_tokens, estimatedCostUsd
    | filter ispresent(estimatedCostUsd)
    | stats sum(estimatedCostUsd) as total_cost,
            sum(usage.prompt_tokens) as total_prompt,
            sum(usage.completion_tokens) as total_completion,
            count(*) as requests
      by team
    | sort total_cost desc
    | limit 20
  EOQ
}

resource "aws_cloudwatch_query_definition" "cache_stats" {
  name            = "${var.project_name}/cache-stats"
  log_group_names = [aws_cloudwatch_log_group.gateway.name]
  query_string    = <<-EOQ
    fields @timestamp, cacheHit, cacheTokensSaved
    | filter ispresent(cacheHit)
    | stats sum(cacheHit) as hits,
            sum(not cacheHit) as misses,
            sum(cacheHit) / count(*) * 100 as hit_rate_pct,
            sum(cacheTokensSaved) as tokens_saved
      by bin(1h)
    | sort bin(1h) desc
  EOQ
}

resource "aws_cloudwatch_query_definition" "top_endpoints" {
  name            = "${var.project_name}/top-endpoints-detail"
  log_group_names = [aws_cloudwatch_log_group.gateway.name]
  query_string    = <<-EOQ
    fields @timestamp, req.url, responseTime, res.statusCode
    | filter ispresent(req.url)
    | stats count(*) as requests,
            avg(responseTime) as avg_latency_ms,
            pct(responseTime, 99) as p99_ms,
            sum(res.statusCode >= 400) as errors
      by `req.url` as endpoint
    | sort requests desc
    | limit 20
  EOQ
}

resource "aws_cloudwatch_query_definition" "ttft_percentiles" {
  name            = "${var.project_name}/ttft-percentiles"
  log_group_names = [aws_cloudwatch_log_group.gateway.name]
  query_string    = <<-EOQ
    fields @timestamp, timeToFirstToken, `req.headers.x-portkey-provider` as provider, model
    | filter ispresent(timeToFirstToken)
    | stats pct(timeToFirstToken, 50) as p50_ms,
            pct(timeToFirstToken, 95) as p95_ms,
            pct(timeToFirstToken, 99) as p99_ms,
            avg(timeToFirstToken) as avg_ms
      by provider, model
    | sort p99_ms desc
  EOQ
}

resource "aws_cloudwatch_query_definition" "top_expensive_requests" {
  name            = "${var.project_name}/top-expensive-requests"
  log_group_names = [aws_cloudwatch_log_group.gateway.name]
  query_string    = <<-EOQ
    fields @timestamp, `req.headers.x-team-id` as team, provider, model,
           usage.prompt_tokens, usage.completion_tokens, estimatedCostUsd
    | filter ispresent(estimatedCostUsd)
    | sort estimatedCostUsd desc
    | limit 50
  EOQ
}
