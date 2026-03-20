# ADR-010: Cost Attribution Pipeline via Lambda + CloudWatch Metrics

**Status**: Accepted
**Date**: 2026-03-20
**Deciders**: AI Engineering NAMER

## Context

The AI Gateway routes requests to multiple LLM providers (Bedrock, OpenAI, Anthropic, Google) but provides no visibility into per-provider token usage or estimated costs. Gateway logs contain token usage data but this data is only queryable via ad-hoc CloudWatch Logs Insights queries.

## Decision

Deploy a Lambda function that subscribes to the gateway CloudWatch log group via a subscription filter, extracts token usage, computes estimated cost using a pricing table, and publishes custom CloudWatch metrics (AIGateway/TokensUsed, AIGateway/EstimatedCostUsd, AIGateway/RequestCount). The subscription filter uses `{ $.usage.total_tokens > 0 }` to minimize unnecessary invocations.

## Alternatives Considered

- **OTEL Custom Metrics**: Couples cost logic to collector lifecycle, adds sidecar complexity.
- **Athena on S3 Logs**: Rich queries but batch latency, higher infrastructure complexity.

## Consequences

**Positive**: Near-real-time cost visibility, minimal infrastructure, feature-flagged rollout, easy pricing table updates.
**Negative**: Manual pricing table updates required, CloudWatch per-dimension metric costs, estimates not actual invoiced amounts.
