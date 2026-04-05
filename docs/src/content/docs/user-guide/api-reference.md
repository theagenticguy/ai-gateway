---
title: API Reference
description: Endpoints, headers, request/response formats, and rate limits.
sidebar:
  order: 3
---
The AI Gateway exposes two endpoints that mirror the native APIs of OpenAI and Anthropic. All requests require a valid JWT and a provider routing header.

---

## Endpoints

| Endpoint | Format | Description |
|---|---|---|
| `POST /v1/chat/completions` | OpenAI Chat Completions | Standard OpenAI-compatible chat completions |
| `POST /v1/messages` | Anthropic Messages | Standard Anthropic-compatible messages |

---

## Required Headers

Every request must include:

| Header | Value | Description |
|---|---|---|
| `Authorization` | `Bearer <jwt>` | Cognito M2M JWT access token |
| `x-portkey-provider` | `anthropic`, `openai`, `google`, or `azure-openai` | Tells the gateway which upstream provider to route to |

:::caution[Missing provider header]
If `x-portkey-provider` is omitted, the gateway returns:
```json
{"error": "provider is not set"}
```
:::


---

## Provider Values

| Value | Upstream Provider | Typical Models |
|---|---|---|
| `anthropic` | Anthropic | `claude-sonnet-4-20250514`, `claude-opus-4-20250514` |
| `openai` | OpenAI | `gpt-4.1`, `gpt-4.1-mini`, `o3` |
| `google` | Google | `gemini-2.5-pro`, `gemini-2.5-flash` |
| `azure-openai` | Azure OpenAI | Deployment-specific model names |

---

## Request Examples

### OpenAI Chat Completions Format

```bash
curl -X POST "${GATEWAY_URL}/v1/chat/completions" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -H "x-portkey-provider: openai" \
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
  -H "x-portkey-provider: anthropic" \
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

### Routing Anthropic Models via OpenAI Format

You can route requests to Anthropic models using the OpenAI Chat Completions format. The gateway translates the request on the fly:

```bash
curl -X POST "${GATEWAY_URL}/v1/chat/completions" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -H "x-portkey-provider: anthropic" \
  -d '{
    "model": "claude-sonnet-4-20250514",
    "messages": [
      {"role": "user", "content": "Hello from the OpenAI format!"}
    ],
    "max_tokens": 256
  }'
```

This is how agents like Continue.dev and LangChain access Anthropic models through the OpenAI-compatible endpoint.

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

Per-team rate limits are enforced via DynamoDB atomic counters (C.1). Each team's tier defines two limits:

| Tier | RPM | Daily Tokens |
|---|---|---|
| sandbox | 20 | 100,000 |
| standard | 100 | 1,000,000 |
| premium | 500 | 10,000,000 |
| enterprise | unlimited | unlimited |

When a team exceeds its limit, the response includes:

```json
{
  "allowed": false,
  "reason": "RPM limit exceeded (101/100 requests per minute)",
  "retry_after_seconds": 42
}
```

:::tip[Graceful degradation]
If DynamoDB is unreachable, requests are allowed through. Rate limiting never blocks requests due to infrastructure failures.
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