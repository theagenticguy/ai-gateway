---
title: Provider Routing Strategies
description: "Provider routing strategies: fallback, load-balance, cost-optimized, and more."
sidebar:
  order: 4
---
The AI Gateway uses [Portkey's routing engine](https://portkey.ai/docs/product/ai-gateway) to control how requests are distributed across LLM providers. This document covers the available strategies, pre-built config templates, and how to use them.

## Available Strategies

### Single (default)

Sends every request to exactly one provider. This is the default behavior when no routing config is supplied. The provider is determined by the `x-portkey-provider` header on each request.

### Fallback

Tries providers in order. If the primary provider returns a qualifying error (e.g. 429, 500, 502, 503, 504), the gateway automatically retries the request against the next provider in the chain. No client-side retry logic needed.

### Load Balance

Distributes requests across providers based on configured weights. Useful for spreading traffic between Bedrock and direct API access, or across regions.

### Cost-Optimized (conditional)

Routes requests to the cheapest appropriate model based on prompt complexity. Uses Portkey's conditional routing mode to inspect the `max_tokens` parameter and select a model tier:

- Requests with `max_tokens <= 100` go to Haiku (cheapest)
- Requests with `max_tokens <= 1000` go to Sonnet
- All other requests default to Sonnet

This strategy reduces costs by steering simple, short-output tasks to smaller models while preserving quality for complex tasks.

### A/B Testing (weighted load balance)

Routes a configurable percentage of traffic to a variant model for side-by-side comparison. Built on the loadbalance mode with asymmetric weights:

- Control group (90% default): Receives the established production model
- Variant group (10% default): Receives the candidate model being evaluated

Adjust the weights in the config to control the traffic split. Combine with observability to compare latency, cost, and quality metrics across groups.

### Latency-Optimized (multi-provider load balance)

Distributes traffic across multiple providers to minimize overall latency. Uses weighted load balancing with error-triggered redistribution:

- Bedrock (50%): Primary provider with the most capacity
- Anthropic direct (30%): Secondary provider
- OpenAI GPT-4o (20%): Tertiary provider for diversification

Providers that return 429/500/502/503 have their traffic automatically redistributed to healthy providers.

## Selecting a Strategy Per Request

There are three ways to select a routing strategy:

### 1. Per-request header

Pass a base64-encoded Portkey config in the `x-portkey-config` header:

```bash
CONFIG=$(echo -n '{"strategy":{"mode":"fallback"},"targets":[{"provider":"bedrock"},{"provider":"anthropic"}]}' | base64 -w0)

curl -X POST https://gateway.example.com/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "x-portkey-config: $CONFIG" \
  -d '{
    "messages": [{"role": "user", "content": "Hello"}],
    "model": "claude-sonnet-4-20250514"
  }'
```

### 2. Named config header

Reference a config by name via the `x-routing-config` header. The gateway resolves the name against built-in and custom configs:

```bash
curl -X POST https://gateway.example.com/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "x-routing-config: cost-optimized" \
  -d '{
    "messages": [{"role": "user", "content": "Summarize this in one sentence"}],
    "model": "claude-sonnet-4-20250514",
    "max_tokens": 50
  }'
```

### 3. Server-side defaults

When `enable_provider_fallback = true` in Terraform, the pre-built fallback configs are injected as defaults. No header needed.

## Pre-Built Config Templates

Config templates live in `infrastructure/portkey-configs/`.

### fallback-anthropic.json

**Use when:** You want Bedrock as the primary Anthropic provider with automatic fallback to the direct Anthropic API if Bedrock returns errors.

- Primary: Bedrock (`anthropic.claude-sonnet-4-20250514-v1:0`) with 2 retries on 429/500/502/503
- Fallback: Anthropic direct (`claude-sonnet-4-20250514`)
- Triggers on: 429, 500, 502, 503, 504

### fallback-openai.json

**Use when:** You want OpenAI as the primary provider with automatic fallback to Azure OpenAI if OpenAI returns errors.

- Primary: OpenAI (`gpt-4.1`) with 2 retries on 429/500/502/503
- Fallback: Azure OpenAI (`gpt-4.1`)
- Triggers on: 429, 500, 502, 503, 504

### loadbalance-multi.json

**Use when:** You want to spread Anthropic model traffic across Bedrock (60%) and the direct Anthropic API (40%) for cost optimization or quota management.

### cost-optimized.json

**Use when:** You want to minimize cost by routing simple requests to Haiku and complex requests to Sonnet. Ideal for workloads with a mix of simple classification/extraction tasks and longer generative tasks.

- Condition 1: `max_tokens <= 100` routes to Haiku (`anthropic.claude-haiku-4-5-20251001-v1:0`)
- Condition 2: `max_tokens <= 1000` routes to Sonnet (`anthropic.claude-sonnet-4-20250514-v1:0`)
- Default: Sonnet

### ab-test-template.json

**Use when:** You want to compare two models in production. The template ships with a 90/10 split between Sonnet 4 (control) and Sonnet 4.5 (variant).

- Control (90%): Bedrock Sonnet 4 (`anthropic.claude-sonnet-4-20250514-v1:0`)
- Variant (10%): Bedrock Sonnet 4.5 (`anthropic.claude-sonnet-4-5-20250514-v1:0`)
- Error failover on: 429, 500, 502, 503

### lowest-latency.json

**Use when:** You want to minimize latency by spreading traffic across multiple providers, with automatic failover away from slow or erroring providers.

- Bedrock Claude Sonnet (50%): Primary capacity
- Anthropic direct (30%): Lower-latency for smaller payloads
- OpenAI GPT-4o (20%): Cross-provider diversification
- Error failover on: 429, 500, 502, 503

## Creating Custom Configs via the API

The routing config API allows you to create, update, and delete custom routing configurations stored in DynamoDB. Built-in configs (from `infrastructure/portkey-configs/`) are always available and read-only.

### List all configs

```bash
curl https://<routing-api-url>/routing/configs
```

### Get a specific config

```bash
curl https://<routing-api-url>/routing/configs/cost-optimized
```

### Create a custom config

```bash
curl -X POST https://<routing-api-url>/routing/configs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-ab-test",
    "strategy": {
      "mode": "loadbalance",
      "on_status_codes": [429, 500]
    },
    "targets": [
      {"name": "control", "provider": "bedrock", "weight": 0.8, "override_params": {"model": "anthropic.claude-sonnet-4-20250514-v1:0"}},
      {"name": "variant", "provider": "bedrock", "weight": 0.2, "override_params": {"model": "anthropic.claude-sonnet-4-5-20250514-v1:0"}}
    ],
    "metadata": {"description": "80/20 A/B test for Sonnet 4 vs 4.5"}
  }'
```

### Update a custom config

```bash
curl -X PUT https://<routing-api-url>/routing/configs/my-ab-test \
  -H "Content-Type: application/json" \
  -d '{
    "strategy": {"mode": "loadbalance"},
    "targets": [
      {"name": "control", "provider": "bedrock", "weight": 0.5, "override_params": {"model": "anthropic.claude-sonnet-4-20250514-v1:0"}},
      {"name": "variant", "provider": "bedrock", "weight": 0.5, "override_params": {"model": "anthropic.claude-sonnet-4-5-20250514-v1:0"}}
    ]
  }'
```

### Delete a custom config

```bash
curl -X DELETE https://<routing-api-url>/routing/configs/my-ab-test
```

## Config Field Reference

| Field | Description |
|---|---|
| `strategy.mode` | `"fallback"`, `"loadbalance"`, or `"conditional"` |
| `strategy.on_status_codes` | HTTP status codes that trigger failover/rebalance |
| `strategy.conditions` | Array of condition objects (conditional mode only) |
| `targets[].name` | Unique target name within the config |
| `targets[].provider` | Provider name: `bedrock`, `anthropic`, `openai`, `azure-openai`, `google` |
| `targets[].override_params.model` | Model ID to use for this target |
| `targets[].retry.attempts` | Number of retries before moving to next target |
| `targets[].retry.on_status_codes` | Status codes that trigger a retry within this target |
| `targets[].weight` | Traffic weight (loadbalance mode only, 0.0-1.0, must sum to 1.0) |
| `targets[].virtual_key` | Portkey virtual key for this target |
| `metadata.description` | Human-readable description of the config |

## Server-Side Default Configs

When `enable_provider_fallback = true` in Terraform, the pre-built configs are injected into the gateway container as base64-encoded environment variables:

- `PORTKEY_DEFAULT_CONFIG_ANTHROPIC` -- Anthropic fallback config
- `PORTKEY_DEFAULT_CONFIG_OPENAI` -- OpenAI fallback config