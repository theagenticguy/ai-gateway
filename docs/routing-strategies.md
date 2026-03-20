# Provider Routing Strategies

The AI Gateway uses [Portkey's routing engine](https://portkey.ai/docs/product/ai-gateway/routing) to control how requests are distributed across LLM providers. This document covers the available strategies, pre-built config templates, and how to use them.

## Available Strategies

### Single (default)

Sends every request to exactly one provider. This is the default behavior when no routing config is supplied. The provider is determined by the `x-portkey-provider` header on each request.

### Fallback

Tries providers in order. If the primary provider returns a qualifying error (e.g. 429, 500, 502, 503, 504), the gateway automatically retries the request against the next provider in the chain. No client-side retry logic needed.

### Load Balance

Distributes requests across providers based on configured weights. Useful for spreading traffic between Bedrock and direct API access, or across regions.

## Passing Configs via Header

Any Portkey routing config can be sent per-request using the `x-portkey-config` header. The value is a base64-encoded JSON string:

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

## Creating Custom Routing Configs

Key fields:

| Field | Description |
|---|---|
| `strategy.mode` | `"fallback"` or `"loadbalance"` |
| `strategy.on_status_codes` | HTTP status codes that trigger failover (fallback mode only) |
| `targets[].provider` | Provider name: `bedrock`, `anthropic`, `openai`, `azure-openai`, `google` |
| `targets[].override_params.model` | Model ID to use for this target |
| `targets[].retry.attempts` | Number of retries before moving to next target |
| `targets[].retry.on_status_codes` | Status codes that trigger a retry within this target |
| `targets[].weight` | Traffic weight (loadbalance mode only, 0.0-1.0) |

## Server-Side Default Configs

When `enable_provider_fallback = true` in Terraform, the pre-built configs are injected into the gateway container as base64-encoded environment variables:

- `PORTKEY_DEFAULT_CONFIG_ANTHROPIC` -- Anthropic fallback config
- `PORTKEY_DEFAULT_CONFIG_OPENAI` -- OpenAI fallback config
