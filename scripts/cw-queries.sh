#!/usr/bin/env bash
set -euo pipefail

# CloudWatch Logs Insights queries for AI Gateway operational visibility
# Targets: /ecs/ai-gateway/gateway log group (Portkey container logs)
# Log format: pino JSON (Fastify)
#
# Usage:
#   ./cw-queries.sh              # Run all queries (default last 1 hour)
#   ./cw-queries.sh requests     # Run a specific query
#   ./cw-queries.sh errors
#   ./cw-queries.sh latency
#   ./cw-queries.sh endpoints
#   ./cw-queries.sh tokens
#   ./cw-queries.sh cost
#   ./cw-queries.sh cache
#   ./cw-queries.sh budget
#   ./cw-queries.sh ttft
#   ./cw-queries.sh expensive
#
# Environment variables:
#   LOG_GROUP    — Override the target log group (default: /ecs/ai-gateway/gateway)
#   START_TIME   — Epoch seconds for query start (default: 1 hour ago)
#   END_TIME     — Epoch seconds for query end (default: now)

LOG_GROUP="${LOG_GROUP:-/ecs/ai-gateway/gateway}"
END_TIME="${END_TIME:-$(date +%s)}"
START_TIME="${START_TIME:-$(( END_TIME - 3600 ))}"

run_query() {
  local name="$1" query="$2"
  echo "=== ${name} ==="
  local qid
  qid=$(aws logs start-query \
    --log-group-name "${LOG_GROUP}" \
    --start-time "${START_TIME}" \
    --end-time "${END_TIME}" \
    --query-string "${query}" \
    --output text --query 'queryId')
  echo "Query ID: ${qid}"
  sleep 3
  aws logs get-query-results --query-id "${qid}"
}

# ---------------------------------------------------------------------------
# Set 1: Operational Visibility Queries
# ---------------------------------------------------------------------------

query_requests() {
  run_query "Requests per hour by provider" \
    "fields @timestamp, @message
| filter ispresent(responseTime)
| stats count(*) as requests by bin(1h), \`req.headers.x-portkey-provider\` as provider
| sort bin(1h) desc"
}

query_errors() {
  run_query "Error rate by provider" \
    "fields @timestamp, @message
| filter ispresent(res.statusCode)
| stats count(*) as total,
        sum(res.statusCode >= 400) as errors,
        (sum(res.statusCode >= 400) / count(*)) * 100 as error_pct
  by \`req.headers.x-portkey-provider\` as provider
| sort error_pct desc"
}

query_latency() {
  run_query "Latency percentiles by provider" \
    "fields @timestamp, responseTime
| filter ispresent(responseTime)
| stats pct(responseTime, 50) as p50,
        pct(responseTime, 95) as p95,
        pct(responseTime, 99) as p99,
        avg(responseTime) as avg_ms
  by \`req.headers.x-portkey-provider\` as provider
| sort p99 desc"
}

query_endpoints() {
  run_query "Requests by endpoint" \
    "fields @timestamp, req.url
| filter ispresent(req.url)
| stats count(*) as requests by \`req.url\` as endpoint
| sort requests desc
| limit 20"
}

# ---------------------------------------------------------------------------
# Set 2: Cost & Token Visibility
# ---------------------------------------------------------------------------

query_tokens() {
  run_query "Token usage by provider and model" \
    "fields @timestamp, provider, model, usage.prompt_tokens, usage.completion_tokens
| filter ispresent(usage.prompt_tokens)
| stats sum(usage.prompt_tokens) as total_prompt,
        sum(usage.completion_tokens) as total_completion,
        sum(usage.prompt_tokens + usage.completion_tokens) as total_tokens
  by provider, model
| sort total_tokens desc"
}

query_cost() {
  run_query "Estimated cost by provider and model" \
    "fields @timestamp, provider, model, estimatedCostUsd
| filter ispresent(estimatedCostUsd)
| stats sum(estimatedCostUsd) as total_cost,
        avg(estimatedCostUsd) as avg_cost,
        count(*) as requests
  by provider, model
| sort total_cost desc"
}

# ---------------------------------------------------------------------------
# Set 3: Cache Performance
# ---------------------------------------------------------------------------

query_cache() {
  run_query "Cache hit rate by hour" \
    "fields @timestamp, cacheHit, cacheTokensSaved
| filter ispresent(cacheHit)
| stats sum(cacheHit) as hits,
        sum(not cacheHit) as misses,
        sum(cacheHit) / count(*) * 100 as hit_rate_pct,
        sum(cacheTokensSaved) as tokens_saved
  by bin(1h)
| sort bin(1h) desc"
}

# ---------------------------------------------------------------------------
# Set 4: Budget Utilization
# ---------------------------------------------------------------------------

query_budget() {
  run_query "Budget utilization (daily spend)" \
    "fields @timestamp, estimatedCostUsd
| filter ispresent(estimatedCostUsd)
| stats sum(estimatedCostUsd) as daily_spend
  by bin(1d)
| sort bin(1d) desc
| limit 7"
}

# ---------------------------------------------------------------------------
# Set 5: Time to First Token
# ---------------------------------------------------------------------------

query_ttft() {
  run_query "TTFT percentiles by provider and model" \
    "fields @timestamp, timeToFirstToken, \`req.headers.x-portkey-provider\` as provider, model
| filter ispresent(timeToFirstToken)
| stats pct(timeToFirstToken, 50) as p50_ms,
        pct(timeToFirstToken, 95) as p95_ms,
        pct(timeToFirstToken, 99) as p99_ms,
        avg(timeToFirstToken) as avg_ms
  by provider, model
| sort p99_ms desc"
}

# ---------------------------------------------------------------------------
# Set 6: Top Expensive Requests
# ---------------------------------------------------------------------------

query_expensive() {
  run_query "Top 50 most expensive requests" \
    "fields @timestamp, \`req.headers.x-team-id\` as team, provider, model,
       usage.prompt_tokens, usage.completion_tokens, estimatedCostUsd
| filter ispresent(estimatedCostUsd)
| sort estimatedCostUsd desc
| limit 50"
}

# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

main() {
  case "${1:-all}" in
    requests)
      query_requests
      ;;
    errors)
      query_errors
      ;;
    latency)
      query_latency
      ;;
    endpoints)
      query_endpoints
      ;;
    tokens)
      query_tokens
      ;;
    cost)
      query_cost
      ;;
    cache)
      query_cache
      ;;
    budget)
      query_budget
      ;;
    ttft)
      query_ttft
      ;;
    expensive)
      query_expensive
      ;;
    all)
      query_requests
      echo ""
      query_errors
      echo ""
      query_latency
      echo ""
      query_endpoints
      echo ""
      query_tokens
      echo ""
      query_cost
      echo ""
      query_cache
      echo ""
      query_budget
      echo ""
      query_ttft
      echo ""
      query_expensive
      ;;
    *)
      echo "Usage: $0 {requests|errors|latency|endpoints|tokens|cost|cache|budget|ttft|expensive|all}" >&2
      exit 1
      ;;
  esac
}

main "$@"
