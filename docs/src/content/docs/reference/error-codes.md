---
title: Error Codes
description: HTTP status codes, gateway errors, WAF responses, and rate limit responses with troubleshooting guidance.
sidebar:
  order: 4
---

Complete reference for error responses returned by the AI Gateway, organized by source: ALB/JWT validation, WAF, gateway application logic, rate limiting, and upstream providers.

---

## HTTP Status Code Summary

| Code | Meaning | Source | Common Cause |
|---|---|---|---|
| `200` | OK | Gateway | Request succeeded |
| `401` | Unauthorized | ALB | Invalid, expired, or missing JWT |
| `403` | Forbidden | WAF or ALB | WAF block, wrong OAuth scope, or IP rate limit |
| `429` | Too Many Requests | Gateway or provider | Budget exhausted, RPM/token limit, or provider rate limit |
| `502` | Bad Gateway | Gateway | Upstream provider unreachable or returned an error |
| `503` | Service Unavailable | ALB | No healthy ECS tasks or gateway overloaded |

---

## 200 OK

The request was processed successfully. The response body matches the format of the upstream provider (OpenAI Chat Completions or Anthropic Messages).

:::tip
A `200` response always includes a `usage` object with token counts. Use these values for cost tracking and to verify that responses are within expected bounds.
:::

---

## 401 Unauthorized

The ALB rejected the request because the JWT is invalid or missing. The ALB performs JWT validation before the request reaches the gateway container.

### Triggers

| Trigger | Details |
|---|---|
| Missing `Authorization` header | No `Bearer <jwt>` token in the request |
| Expired token | Cognito JWTs have a 1-hour TTL |
| Invalid signature | Token not signed by the expected Cognito User Pool |
| Malformed token | Token is not a valid JWT (wrong format, corrupt base64) |

### Response

The ALB returns an HTML error page (not JSON) with HTTP 401. There is no response body when JWT validation fails at the ALB layer.

### Troubleshooting

1. **Refresh your token** -- Cognito JWTs expire after 1 hour:
   ```bash
   TOKEN=$(./scripts/get-gateway-token.sh)
   ```

2. **Verify the token is well-formed**:
   ```bash
   echo "$TOKEN" | cut -d. -f2 | base64 -d 2>/dev/null | python3 -m json.tool
   ```

3. **Check expiry**:
   ```bash
   echo "$TOKEN" | cut -d. -f2 | base64 -d 2>/dev/null | python3 -c "
   import json, sys, datetime
   data = json.load(sys.stdin)
   exp = data.get('exp', 0)
   remaining = exp - int(datetime.datetime.now().timestamp())
   print(f'Expires in {remaining // 60} minutes')
   "
   ```

4. **Claude Code users** -- Ensure `apiKeyHelper` is configured and executable:
   ```bash
   claude config set --global apiKeyHelper ~/workplace/ai-gateway/scripts/get-gateway-token.sh
   chmod +x ~/workplace/ai-gateway/scripts/get-gateway-token.sh
   ```

:::caution
For Claude Code, `CLAUDE_CODE_API_KEY_HELPER_TTL_MS` must be set as a real shell environment variable, not in the settings.json `env` block. This is a known issue (bug #7660).
:::

---

## 403 Forbidden

The request was authenticated (valid JWT) but rejected by WAF rules or authorization checks.

### Triggers

| Trigger | Details |
|---|---|
| WAF block | AWS Managed Rules (common exploits, IP reputation) matched the request |
| IP rate limit | WAF per-IP rate limit exceeded (2,000 requests per 5-minute window) |
| Wrong OAuth scope | JWT does not contain the required `https://gateway.internal/invoke` scope |

### WAF Block Response

When WAF blocks a request, the response includes the `x-amzn-waf-action` header:

```
HTTP/1.1 403 Forbidden
x-amzn-waf-action: BLOCK
```

The response body is an AWS WAF default block page (HTML), not a JSON payload.

### WAF Rules in Effect

| Rule Group | Description |
|---|---|
| AWS Common Rule Set | Blocks common web exploits (SQL injection, XSS, etc.) |
| IP Reputation List | Blocks requests from known-bad IP addresses |
| Per-IP Rate Limit | 2,000 requests per 5-minute window per source IP |

### Troubleshooting

1. **Check for the WAF header** in the response:
   ```bash
   curl -v -H "Authorization: Bearer $TOKEN" \
        -H "x-portkey-provider: openai" \
        ${GATEWAY_URL}/v1/chat/completions 2>&1 | grep -i waf
   ```

2. **If IP rate limited** -- Wait for the 5-minute window to expire, or reduce request volume.

3. **If scope is wrong** -- Decode the JWT and verify the `scope` claim:
   ```bash
   echo "$TOKEN" | cut -d. -f2 | base64 -d 2>/dev/null | python3 -m json.tool
   ```
   Contact the gateway admin to update the Cognito app client scopes.

---

## 429 Too Many Requests

The gateway or upstream provider is rate-limiting your requests.

### Gateway Rate Limit (Team Layer)

Per-team rate limiting is enforced via DynamoDB atomic counters when the Admin API (C.1) is enabled. Two dimensions are tracked:

| Dimension | Window | Description |
|---|---|---|
| RPM (requests per minute) | 1-minute sliding window | Atomic counter per team per minute bucket |
| Daily tokens | Calendar day (UTC) | Cumulative token count per team per day |

#### Rate Limit Response

```json
{
  "allowed": false,
  "reason": "RPM limit exceeded (101/100 requests per minute)",
  "retry_after_seconds": 42
}
```

| Field | Description |
|---|---|
| `allowed` | Always `false` when rate limited |
| `reason` | Human-readable explanation of which limit was hit |
| `retry_after_seconds` | Number of seconds to wait before retrying |

#### Tier Defaults

| Tier | RPM | Daily Tokens | Monthly Budget (USD) |
|---|---|---|---|
| sandbox | 20 | 100,000 | $25 |
| standard | 100 | 500,000 | $100 |
| premium | 500 | 5,000,000 | $1,000 |
| unlimited | 2,000 | unlimited | $10,000 |

:::note[Graceful degradation]
If DynamoDB is unreachable, the rate limiter allows the request through and logs a warning. Rate limiting infrastructure never becomes a single point of failure on the inference path.
:::

### Budget Enforcement

When `enable_budgets = true`, the budget enforcement webhook runs as a Portkey `before_request_hook`. If a team's monthly budget is exhausted (utilization reaches the hard limit percentage, default 100%), the request is denied before reaching the provider. The enforcement Lambda also supports model-level budget caps -- if a specific model's spend exceeds its configured limit, only that model is blocked.

Budget enforcement includes a warning threshold (default 80%). Requests are allowed when the warning is reached, but a warning is logged.

### Provider Rate Limit

Upstream providers (OpenAI, Anthropic, Google, Azure) return their own 429 responses. These are passed through to the caller. Provider rate limits are independent of gateway rate limits.

**Retry strategy:** Implement exponential backoff. Most 429 responses include a `Retry-After` header.

---

## 502 Bad Gateway

The gateway could not reach the upstream LLM provider or received an error from it.

### Triggers

| Trigger | Details |
|---|---|
| Provider outage | Upstream provider is down or returning errors |
| Invalid API key | The stored provider key is expired, revoked, or still set to `REPLACE_ME` |
| Network issue | ECS task lacks outbound internet access (NAT Gateway misconfiguration) |
| Invalid model | The specified model does not exist at the provider |

### Response

The gateway passes through the upstream provider's error response when available. When the provider is completely unreachable, the ALB returns a generic 502 error page.

### Troubleshooting

1. **Test a different provider** to isolate the issue:
   ```bash
   curl -H "Authorization: Bearer $TOKEN" \
        -H "x-portkey-provider: anthropic" \
        -H "Content-Type: application/json" \
        -d '{"model":"claude-sonnet-4-20250514","max_tokens":1,"messages":[{"role":"user","content":"hi"}]}' \
        ${GATEWAY_URL}/v1/chat/completions
   ```

2. **Run the health check with provider testing**:
   ```bash
   ./scripts/check-health.sh --url "$GATEWAY_URL" --token "$TOKEN" --providers
   ```

3. **Verify the API key is set** in Secrets Manager:
   ```bash
   aws secretsmanager get-secret-value \
     --secret-id ai-gateway/openai-api-key \
     --query SecretString --output text
   ```
   If the value is `REPLACE_ME`, the key has not been configured.

:::caution
Never log or share the secret value from the command above. Use it only to verify that the key is not the placeholder.
:::

---

## 503 Service Unavailable

The gateway itself is overloaded or all ECS tasks are unhealthy.

### Triggers

| Trigger | Details |
|---|---|
| No healthy targets | All ECS tasks failed health checks; ALB has no targets to route to |
| Gateway overloaded | Concurrent request count exceeds capacity; autoscaling has not yet caught up |
| Deployment in progress | A rolling deployment temporarily reduces available capacity |

### Response

The ALB returns its default 503 page (HTML). There is no JSON body.

### Troubleshooting

1. **Wait 30--60 seconds** -- Autoscaling should add capacity.

2. **Check ECS service health**:
   ```bash
   aws ecs describe-services \
     --cluster ai-gateway-prod \
     --services ai-gateway-gateway \
     --query 'services[0].{desired:desiredCount,running:runningCount,events:events[:3]}'
   ```

3. **Check the ALB target group**:
   ```bash
   aws elbv2 describe-target-health \
     --target-group-arn "$TARGET_GROUP_ARN"
   ```

4. **Run the basic health check**:
   ```bash
   curl -s -o /dev/null -w "%{http_code}" ${GATEWAY_URL}/
   # Expected: 200
   ```

---

## Gateway Application Errors

These errors come from the Portkey gateway application itself (HTTP 200 status code but with an error in the JSON body, or a non-standard HTTP status).

### Missing Provider Header

```json
{"error": "provider is not set"}
```

Every request must include the `x-portkey-provider` header. See the [API Reference](/ai-gateway/user-guide/api-reference/) for valid provider values and the [Troubleshooting guide](/ai-gateway/user-guide/troubleshooting/#missing-x-portkey-provider-header) for per-agent configuration.

### Invalid Model

The provider rejected the model name. This typically means:
- The model does not exist at the specified provider
- The model name is misspelled
- The provider account does not have access to the model

The error response is passed through from the upstream provider.

---

## Cognito Token Endpoint Errors

These errors occur when obtaining a JWT from the Cognito token endpoint, before any gateway request is made.

### `invalid_grant`

```json
{"error": "invalid_grant"}
```

| Cause | Fix |
|---|---|
| Wrong token endpoint | Verify `GATEWAY_TOKEN_ENDPOINT` is `https://<domain>.auth.<region>.amazoncognito.com/oauth2/token` |
| Wrong credentials | Confirm `GATEWAY_CLIENT_ID` and `GATEWAY_CLIENT_SECRET` are correct |
| Wrong grant type | The Cognito app client must be configured for `client_credentials` grant |

### `invalid_client`

```json
{"error": "invalid_client"}
```

The client ID or secret is incorrect. This is a standard OAuth2 error returned by the Cognito token endpoint. Double-check the values or contact the gateway admin.

### `invalid_scope`

```json
{"error": "invalid_scope"}
```

The requested scope is not configured on the Cognito app client. This is a standard OAuth2 error returned by the Cognito token endpoint. Valid scopes are `https://gateway.internal/invoke` and `https://gateway.internal/admin`.

---

## Content Scanner Errors

When `enable_content_scanner = true`, the content scanner Lambda runs as a Portkey `before_request_hook` and can block requests. The scanner supports four modes per team: `off`, `detect`, `redact`, and `block`.

### PII Detected (block mode)

When the team's PII mode is set to `block` and PII entities are found, the request is denied. The scanner returns a Portkey `verdict: false` response, and the gateway blocks the request before it reaches the upstream provider.

### Injection Detected (block mode)

When the team's injection mode is set to `block` and a prompt injection pattern is matched, the request is denied. Critical-severity injections in `redact` mode are also escalated to a block.

### Scanner Behavior by Mode

| Mode | PII Behavior | Injection Behavior |
|---|---|---|
| `off` | No scan | No scan |
| `detect` | Allow, log detections | Allow, log detections |
| `redact` | Strip PII, forward sanitized request | Allow (block critical severity only) |
| `block` | Deny request | Deny request |

:::note
The scanner fails open. If the Lambda encounters an internal error, the request is allowed through and a warning is logged. A broken scanner never blocks legitimate traffic.
:::

---

## Guardrail Errors

When `enable_guardrails = true`, Bedrock Guardrails evaluate both the input and output of each request.

### Request Blocked by Guardrail

The response includes a Bedrock Guardrails action indicator. The exact format depends on the guardrail policy that triggered:

| Policy | Example Reason |
|---|---|
| Content filter | Harmful content detected (hate, violence, sexual, misconduct) |
| PII blocking | PII detected in response (SSN, credit card, email, phone) |
| Topic policy | Request matches a blocked topic (e.g., competitor products, internal financials) |
| Word policy | Request or response contains a blocked word or phrase |

---

## Error Response Quick Reference

| Symptom | Likely Code | First Check |
|---|---|---|
| HTML error page, no JSON | `401` or `503` | ALB-level rejection; check JWT or ECS health |
| `x-amzn-waf-action: BLOCK` header | `403` | WAF rule triggered; check IP and request patterns |
| `"provider is not set"` | Varies | Missing `x-portkey-provider` header |
| `"allowed": false` with `retry_after_seconds` | `429` | Team rate limit or budget exceeded |
| Request blocked by content scanner | Varies | Content scanner in `block` mode denied the request |
| Provider error passthrough | `502` | Check API key in Secrets Manager; test alternate provider |
| No response / timeout | -- | Check gateway URL, VPN, and ECS service status |
