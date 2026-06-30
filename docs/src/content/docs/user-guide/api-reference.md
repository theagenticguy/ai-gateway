---
title: API Reference
description: Endpoints, headers, request/response formats, and rate limits.
sidebar:
  order: 3
---
The AI Gateway is the [agentgateway](https://github.com/agentgateway/agentgateway) proxy. It exposes two endpoints that mirror the native APIs of OpenAI and Anthropic on a single port. All requests require a valid JWT. Provider and model selection is handled server-side by the rendered gateway config -- there is no provider routing header.

---

## Endpoints

| Endpoint | Format | Description |
|---|---|---|
| `POST /v1/chat/completions` | OpenAI Chat Completions | Standard OpenAI-compatible chat completions |
| `POST /v1/messages` | Anthropic Messages | Standard Anthropic-compatible messages |

Both endpoints are served on the same port (`8787` behind the ALB); agentgateway selects the route type from the path suffix.

---

## Required Headers

Every request must include:

| Header | Value | Description |
|---|---|---|
| `Authorization` | `Bearer <jwt>` | Cognito M2M JWT access token |

:::note[No provider header]
Earlier (Portkey-based) releases required an `x-portkey-provider` header. agentgateway removed it: the active provider and its failover chain live in the gateway's rendered config, and the gateway maps the requested model onto a backend via `modelAliases`. Do not send `x-portkey-*` headers.
:::


---

## Provider and Model Selection

The gateway routes to providers using a server-side priority-group failover chain defined in its config (the default ships Bedrock as primary with Anthropic-direct as fallback). agentgateway types eight providers; this deployment provisions five:

| Provider | Typical Models |
|---|---|
| Bedrock | `anthropic.claude-sonnet-4-20250514-v1:0` |
| Anthropic | `claude-sonnet-4-20250514`, `claude-opus-4-20250514` |
| OpenAI | `gpt-4.1`, `gpt-4.1-mini`, `o3` |
| Google | `gemini-2.5-pro`, `gemini-2.5-flash` |
| Azure OpenAI | Deployment-specific model names |

The `model` field in your request body is matched against the gateway's `modelAliases` (for example, `gpt-4*` can be aliased to a Bedrock model) and the active provider chain. To change which providers are reachable or their failover order, update the rendered config (see [Routing Strategies](/ai-gateway/user-guide/routing-strategies/)).

---

## Request Examples

### OpenAI Chat Completions Format

```bash
curl -X POST "${GATEWAY_URL}/v1/chat/completions" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4.1",
    "messages": [
      {"role": "system", "content": "You are a helpful assistant."},
      {"role": "user", "content": "Hello, world!"}
    ],
    "max_tokens": 256
  }'
```

**Response:**

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1711234567,
  "model": "gpt-4.1",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Hello! How can I help you today?"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 19,
    "completion_tokens": 9,
    "total_tokens": 28
  }
}
```

### Anthropic Messages Format

```bash
curl -X POST "${GATEWAY_URL}/v1/messages" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -d '{
    "model": "claude-sonnet-4-20250514",
    "max_tokens": 256,
    "messages": [
      {"role": "user", "content": "Hello, world!"}
    ]
  }'
```

**Response:**

```json
{
  "id": "msg_abc123",
  "type": "message",
  "role": "assistant",
  "content": [
    {
      "type": "text",
      "text": "Hello! How can I help you today?"
    }
  ],
  "model": "claude-sonnet-4-20250514",
  "stop_reason": "end_turn",
  "usage": {
    "input_tokens": 10,
    "output_tokens": 9
  }
}
```

### Reaching Anthropic Models via OpenAI Format

You can request Anthropic models through the OpenAI Chat Completions endpoint by setting the `model` field. agentgateway translates the request format on the fly and routes to the provider chain that serves that model:

```bash
curl -X POST "${GATEWAY_URL}/v1/chat/completions" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-20250514",
    "messages": [
      {"role": "user", "content": "Hello from the OpenAI format!"}
    ],
    "max_tokens": 256
  }'
```

This is how agents like Continue.dev and LangChain reach Anthropic models through the OpenAI-compatible endpoint.

---

## Rate Limiting

The gateway enforces rate limiting at two layers:

### WAF Layer (IP-based)

| Rule | Limit |
|---|---|
| **Per-IP rate limit** | 2,000 requests per 5-minute window per IP address |
| **AWS Managed Rules** | AWS Common Rule Set, IP reputation list |

When WAF rate-limits a request, the gateway returns HTTP 403 with an `x-amzn-waf-action` response header.

### Team Layer (RPM + Daily Tokens)

Per-team rate limits are enforced via DynamoDB atomic counters (C.1, available when the Admin API is enabled). Each team's tier defines two limits:

| Tier | RPM | Daily Tokens |
|---|---|---|
| sandbox | 20 | 100,000 |
| standard | 100 | 1,000,000 |
| premium | 500 | 10,000,000 |
| enterprise | unlimited | unlimited |

When a team exceeds its limit, the rate limiter returns:

```json
{
  "allowed": false,
  "reason": "RPM limit exceeded (101/100 requests per minute)",
  "retry_after_seconds": 42
}
```

### Budget Enforcement Layer

When budgets are enabled, the `budget_enforcement` Lambda runs in-path as an agentgateway `promptGuard` request webhook. When a team's budget is exhausted, the Lambda returns agentgateway's `{"action": "reject"}` contract, which agentgateway maps to an **HTTP 429** for the client. See [Error Codes](/ai-gateway/reference/error-codes/#429-too-many-requests).

:::tip[Graceful degradation]
The budget Lambda fails **open**: on a DynamoDB outage it allows the request through rather than blocking. Enforcement never becomes a single point of failure on the inference path.
:::


---

:::note[Admin API]
For admin endpoints (teams, budgets, pricing, routing, usage), see the [Admin API](/ai-gateway/admin-guide/admin-api/) page. The admin API runs on a separate API Gateway with Cognito authorization.
:::


## Health Check

The ALB health check endpoint is:

| Path | Port | Expected Response |
|---|---|---|
| `/` | 8787 | HTTP 200 |

You can verify the gateway is reachable:

```bash
curl -s -o /dev/null -w "%{http_code}" "${GATEWAY_URL}/"
# Expected: 200
```