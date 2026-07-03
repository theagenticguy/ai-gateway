# =============================================================================
# CloudWatch Logs Insights — Saved Queries
# =============================================================================

resource "aws_cloudwatch_query_definition" "requests_by_model" {
  name            = "${var.project_name}/requests-by-model"
  log_group_names = [aws_cloudwatch_log_group.gateway.name]
  query_string    = <<-EOQ
    fields @timestamp, model, provider
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
    fields @timestamp, res.statusCode, provider, @message
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
    fields @timestamp, responseTime, provider, model
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

# Cost and team are NOT in the access log: cost exists only as the
# EstimatedCostUsd metric, and team lives inside the ALB JWT (`oidc_data`), which
# Logs Insights cannot decode. So this cannot be a cost-by-team log query — for
# per-team cost use the dashboard's Team-dimensioned EstimatedCostUsd widgets.
# What the FLAT log CAN answer is token volume by provider/model, so this query
# now reports that (flat `prompt_tokens`/`completion_tokens`).
resource "aws_cloudwatch_query_definition" "tokens_by_provider_model" {
  name            = "${var.project_name}/tokens-by-provider-model"
  log_group_names = [aws_cloudwatch_log_group.gateway.name]
  query_string    = <<-EOQ
    fields @timestamp, provider, model, prompt_tokens, completion_tokens, total_tokens
    | filter ispresent(total_tokens)
    | stats sum(prompt_tokens) as total_prompt,
            sum(completion_tokens) as total_completion,
            sum(total_tokens) as total_tokens,
            count(*) as requests
      by provider, model
    | sort total_tokens desc
    | limit 20
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

# NOTE: the ttft-percentiles saved query was removed. `timeToFirstToken` is not
# a field in the flat agentgateway access log, so the query matched nothing.
# Re-add it only once the gateway actually emits a TTFT field.

# The flat log has neither per-request cost (`estimatedCostUsd`) nor team
# (`x-team-id`), so "most expensive" cannot be ranked by cost from logs. Rank by
# total_tokens instead (the flat proxy for request size) over the real flat
# fields. For true per-request cost, join the audit Firehose/Iceberg table.
resource "aws_cloudwatch_query_definition" "largest_requests_by_tokens" {
  name            = "${var.project_name}/largest-requests-by-tokens"
  log_group_names = [aws_cloudwatch_log_group.gateway.name]
  query_string    = <<-EOQ
    fields @timestamp, provider, model, prompt_tokens, completion_tokens, total_tokens
    | filter ispresent(total_tokens)
    | sort total_tokens desc
    | limit 50
  EOQ
}
