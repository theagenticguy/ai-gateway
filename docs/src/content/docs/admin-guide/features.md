---
title: Feature Toggles
description: "B-series features: multi-client, fallback routing, cost attribution, guardrails, and cache."
sidebar:
  order: 6
---
The AI Gateway includes a set of **B-series features** that extend the base platform with additional capabilities. All B-series features are **disabled by default** and can be enabled independently through toggle variables in your Terraform configuration.

## Overview

| Feature | Module | Toggle Variable | Status |
|---|---|---|---|
| B.1 Multi-Client Onboarding | `modules/clients/` | `enable_multi_client` | Opt-in |
| B.2 Provider Fallback Routing | Portkey config JSONs | `enable_fallback_routing` | Opt-in |
| B.3 Cost Attribution Pipeline | Lambda + DynamoDB | `enable_cost_attribution` | Opt-in |
| B.4 Bedrock Guardrails | `modules/guardrails/` | `enable_guardrails` | Opt-in |
| B.5 ElastiCache Response Cache | `modules/cache/` | `enable_response_cache` | Opt-in |

:::note
B-series features are designed as additive modules. Enabling a feature creates new resources alongside the base infrastructure. Disabling a feature destroys only the resources it created.
:::


---

## B.1 Multi-Client Onboarding

### What It Adds

Per-team Cognito credentials that allow you to issue separate client IDs and secrets to each consuming team or service. Each client can be assigned a subset of OAuth scopes, enabling fine-grained access control.

### Resources Created

- Additional Cognito User Pool clients (one per team)
- Per-client scope assignments (e.g., team A gets `invoke` only, team B gets `invoke` + `admin`)
- Optional per-client rate limiting via WAF rules

### How to Enable

```hcl
enable_multi_client = true

client_configurations = {
  team-alpha = {
    scopes = ["https://gateway.internal/invoke"]
  }
  team-beta = {
    scopes = ["https://gateway.internal/invoke", "https://gateway.internal/admin"]
  }
}
```

### How It Works

Each team receives its own `client_id` and `client_secret` from the Cognito User Pool. They use the standard `client_credentials` grant to obtain tokens scoped to their permissions. The ALB JWT listener validates the `scope` claim, ensuring teams can only access endpoints their scopes allow.

:::tip
Use the `admin` scope sparingly. Most consuming services only need `invoke` to call LLM endpoints through the gateway.
:::


---

## B.2 Provider Fallback Routing

### What It Adds

Portkey-native fallback and load-balancing configurations that route requests across multiple LLM providers. If the primary provider fails or is throttled, requests automatically fall back to a secondary provider.

### Routing Strategies

| Strategy | Description | Use Case |
|---|---|---|
| **Fallback** | Try providers in order; move to next on failure | High availability: OpenAI primary, Bedrock fallback |
| **Load Balance** | Distribute requests across providers by weight | Cost optimization: 70% Bedrock, 30% OpenAI |
| **Retry** | Retry failed requests on the same or different provider | Transient error recovery |

### How to Enable

```hcl
enable_fallback_routing = true
```

This deploys Portkey routing configuration files that define fallback chains and load-balancing weights. The configurations are passed to the gateway as environment variables or mounted config files.

### Example Fallback Configuration

```json
{
  "strategy": {
    "mode": "fallback"
  },
  "targets": [
    {
      "provider": "openai",
      "override_params": { "model": "gpt-4" }
    },
    {
      "provider": "bedrock",
      "override_params": { "model": "anthropic.claude-3-5-sonnet-20241022-v2:0" }
    }
  ]
}
```

### Example Load-Balancing Configuration

```json
{
  "strategy": {
    "mode": "loadbalance"
  },
  "targets": [
    {
      "provider": "bedrock",
      "weight": 0.7,
      "override_params": { "model": "anthropic.claude-3-5-sonnet-20241022-v2:0" }
    },
    {
      "provider": "openai",
      "weight": 0.3,
      "override_params": { "model": "gpt-4" }
    }
  ]
}
```

---

## B.3 Cost Attribution Pipeline

### What It Adds

A serverless pipeline that counts tokens, maps them to provider pricing, and publishes cost metrics to CloudWatch. This enables per-team and per-model cost visibility.

### Resources Created

| Resource | Purpose |
|---|---|
| Lambda function | Parses gateway logs, counts prompt/completion tokens, calculates cost |
| DynamoDB pricing table | Stores per-model pricing rates (cost per 1K tokens) |
| CloudWatch Logs subscription | Streams gateway logs to the Lambda function |
| CloudWatch custom metrics | `AIGateway/TokensUsed` and `AIGateway/EstimatedCostUsd` |
| Dashboard widgets | Token usage and cost-by-provider widgets added to the main dashboard |

### How to Enable

```hcl
enable_cost_attribution = true
```

### How It Works

1. The gateway emits structured JSON logs for every request, including provider, model, and response metadata.
2. A CloudWatch Logs subscription filter streams these logs to the Lambda function.
3. The Lambda function extracts token counts from the response, looks up the per-model price in the DynamoDB pricing table, and calculates the estimated cost.
4. Token counts and cost estimates are published as CloudWatch custom metrics under the `AIGateway` namespace.
5. The dashboard displays token usage and cost breakdowns by provider and model.

:::note
The pricing table must be populated with current provider rates. Rates change frequently -- consider automating the update process or reviewing monthly.
:::


---

## B.4 Bedrock Guardrails

### What It Adds

Content safety controls powered by Amazon Bedrock Guardrails. These are applied to requests and responses passing through the gateway, blocking harmful content before it reaches end users.

### Resources Created

| Resource | Purpose |
|---|---|
| Bedrock Guardrail | Content filtering, PII detection, topic policies, word policies |
| Guardrail version | Immutable published version for production use |
| IAM policy | Grants the ECS task role permission to invoke Bedrock Guardrails |

### Guardrail Policies

| Policy Type | Description | Default Behavior |
|---|---|---|
| **Content Filtering** | Blocks harmful content categories (hate, violence, sexual, misconduct) | Block at HIGH strength for all categories |
| **PII Blocking** | Detects and blocks personally identifiable information | Blocks SSN, credit card, email, phone in responses |
| **Topic Policies** | Blocks requests about restricted topics | Configurable deny-list |
| **Word Policies** | Blocks specific words or patterns | Configurable word list |

### How to Enable

```hcl
enable_guardrails = true

guardrail_config = {
  content_filter_strength = "HIGH"
  pii_action             = "BLOCK"
  blocked_topics         = ["financial-advice", "medical-diagnosis"]
  blocked_words          = []
}
```

### How It Works

When guardrails are enabled, the gateway invokes Bedrock's `ApplyGuardrail` API on both the input (prompt) and output (completion). If either triggers a policy violation, the request is blocked with an explanatory error message. The guardrail evaluation adds latency to each request proportional to the content length.

:::caution
Bedrock Guardrails are a regional service. Ensure the guardrail is created in the same region as the ECS cluster. Additional Bedrock quotas may need to be requested for high-throughput use.
:::


---

## B.5 ElastiCache Response Cache

### What It Adds

A Redis-based response cache that stores LLM completions keyed by the request hash. Identical requests return cached responses, reducing latency and provider API costs.

### Resources Created

| Resource | Purpose |
|---|---|
| ElastiCache Serverless (Redis 7.1) | Cache store with TLS encryption in transit |
| Security group | Allows port 6379 from ECS tasks only |
| Subnet group | Places Redis in private subnets |

### Configuration

| Setting | Value |
|---|---|
| Engine | Redis 7.1 |
| Encryption in transit | TLS enabled |
| Eviction policy | `allkeys-lru` (Least Recently Used) |
| Deployment | ElastiCache Serverless (auto-scaling) |

### How to Enable

```hcl
enable_response_cache = true
```

When enabled, the gateway container receives additional environment variables:

| Variable | Value | Purpose |
|---|---|---|
| `CACHE_STORE` | `redis` | Tells Portkey to use Redis for caching |
| `REDIS_URL` | `rediss://{endpoint}:6379` | TLS-encrypted Redis endpoint |
| `CACHE_TTL` | `3600` | Default cache TTL in seconds (1 hour) |

### How It Works

Portkey's built-in caching layer hashes the request body (model, messages, parameters) to generate a cache key. On cache hit, the cached response is returned immediately without calling the LLM provider. On cache miss, the provider response is stored in Redis for subsequent requests.

:::tip
Caching works best for deterministic requests (temperature=0). For creative/random outputs (temperature>0), caching may return unexpected repeated responses. Consider setting `cache: "none"` in the Portkey request headers for non-deterministic calls.
:::


---

---

## C-Series Features

The **C-series features** add metering, governance, and self-service capabilities on top of the B-series platform. All C-series endpoints run on the Admin API Gateway plane (see [ADR-014](/developer-guide/adr-index)) and are enabled with `enable_admin_api = true`.

| Feature | Module | Toggle Variable | Status |
|---|---|---|---|
| C.1 RPM & Token Rate Limiting | `rate_limiter/` | `enable_admin_api` | Opt-in |
| C.2 Usage Self-Service API | `usage_api/` | `enable_admin_api` | Opt-in |
| C.3 Dynamic Pricing Admin | `pricing_admin/` | `enable_admin_api` | Opt-in |
| C.4 Audit Log Pipeline | `modules/audit_log/` | `enable_audit_log` | Opt-in |
| C.5 Per-Team Cache Metrics | `cost_attribution/` | `enable_cost_attribution` | Opt-in |

---

### C.1 RPM & Token Rate Limiting

#### What It Adds

Per-team rate limiting with two dimensions: requests per minute (RPM) and daily token consumption. Limits are defined per tenant tier in `TierConfig` and enforced via DynamoDB atomic counters.

#### How It Works

| Dimension | DynamoDB Key | Window | TTL |
|---|---|---|---|
| RPM | `RATE#RPM#{team}` / `MINUTE#{bucket}` | 1-minute sliding window | 120 seconds |
| Daily tokens | `RATE#TOKENS#{team}` / `DAY#{YYYY-MM-DD}` | Calendar day (UTC) | End of day + 1 hour |

Each request atomically increments the counter. When a limit is exceeded, the gateway returns a `429`-equivalent response with a `retry_after_seconds` hint.

#### Graceful Degradation

If DynamoDB is unreachable, the request is *allowed* and a warning is logged. This prevents rate limiting infrastructure from becoming a single point of failure on the inference path.

#### Tier Defaults

| Tier | RPM | Daily Tokens |
|---|---|---|
| sandbox | 20 | 100,000 |
| standard | 100 | 1,000,000 |
| premium | 500 | 10,000,000 |
| enterprise | -1 (unlimited) | -1 (unlimited) |

---

### C.2 Usage Self-Service API

#### What It Adds

A read-only API that lets teams query their own usage without waiting for monthly chargeback reports.

#### Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/usage/{team}` | Current period usage, budget utilization, and per-model breakdown |
| `GET` | `/usage/{team}/history` | Historical usage by month |

#### Response Fields

| Field | Description |
|---|---|
| `current_period` | Token counts, cost, and request count for the current billing period |
| `models` | Per-model breakdown (tokens, cost, request count) |
| `budget_utilization_pct` | Percentage of monthly budget consumed |
| `history` | Array of past periods with the same structure |

---

### C.3 Dynamic Pricing Admin

#### What It Adds

Runtime pricing overrides stored in DynamoDB with a static fallback table. Operators can update model pricing without redeploying the Lambda.

#### Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/pricing` | List all pricing entries (DynamoDB overrides merged with static defaults) |
| `GET` | `/pricing/{provider}/{model}` | Get pricing for a specific model |
| `PUT` | `/pricing/{provider}/{model}` | Create or update a pricing override |
| `DELETE` | `/pricing/{provider}/{model}` | Remove a DynamoDB override (reverts to static default) |

#### Resolution Order

1. DynamoDB override (if present)
2. Static `PRICING_TABLE` in `pricing.py`

The `source` field in responses indicates whether a price came from `"dynamodb"` or `"static"`.

---

### C.4 Audit Log Pipeline

#### What It Adds

A structured audit trail for all gateway requests, stored as Parquet files in S3 for Athena queries.

#### Resources Created

| Resource | Purpose |
|---|---|
| Kinesis Firehose | Ingests audit events, buffers, and converts to Parquet |
| S3 bucket | Stores Parquet files with Hive-style partitioning (`year=/month=/day=`) |
| Glue Catalog | Database + table for Athena SQL queries |
| CloudWatch Log Group | Firehose delivery error logs |

#### How to Enable

```hcl
enable_audit_log = true
```

#### Audit Record Schema

| Column | Type | Description |
|---|---|---|
| `team` | string | Requesting team |
| `user_id` | string | User identity from JWT |
| `model` | string | Target model |
| `provider` | string | Target provider |
| `prompt_tokens` | int | Input token count |
| `completion_tokens` | int | Output token count |
| `total_tokens` | int | Total tokens |
| `cost_usd` | double | Estimated cost |
| `cache_read_tokens` | int | Tokens served from cache |
| `cache_savings_usd` | double | Cost saved by cache hits |
| `latency_ms` | int | End-to-end latency |
| `status` | string | Request outcome |
| `correlation_id` | string | Request correlation ID |
| `request_timestamp` | string | ISO 8601 timestamp |

#### Lifecycle

- 0–90 days: S3 Standard
- 90–365 days: S3 Standard-IA
- 365+ days: Expired

---

### C.5 Per-Team Cache Metrics

#### What It Adds

Extends the cost attribution pipeline (B.3) to publish cache hit/miss metrics with a `Team` dimension, in addition to the existing `Provider` and `Model` dimensions.

#### New CloudWatch Metrics

| Metric | Dimensions | Description |
|---|---|---|
| `AIGateway/CacheHitRate` | Team, Provider, Model | Percentage of requests served from cache |
| `AIGateway/CacheSavingsUsd` | Team | Estimated cost savings from cache hits |

Use these metrics to identify teams with low cache hit rates and tune their request patterns (e.g., lowering temperature for deterministic calls).

---

## Feature Compatibility Matrix

All B-series features can be enabled independently. The following matrix shows which features complement each other and any dependencies:

| | B.1 Multi-Client | B.2 Fallback Routing | B.3 Cost Attribution | B.4 Guardrails | B.5 Cache |
|---|---|---|---|---|---|
| **B.1 Multi-Client** | -- | Compatible | Compatible (per-client cost) | Compatible | Compatible |
| **B.2 Fallback Routing** | Compatible | -- | Compatible (multi-provider cost) | Compatible | Compatible |
| **B.3 Cost Attribution** | Compatible (per-client cost) | Compatible (multi-provider cost) | -- | Compatible | Compatible (tracks cache savings) |
| **B.4 Guardrails** | Compatible | Compatible | Compatible | -- | Order-dependent (see note) |
| **B.5 Cache** | Compatible | Compatible | Compatible (tracks cache savings) | Order-dependent (see note) | -- |

:::note[B.4 + B.5 Interaction]
When both guardrails and caching are enabled, cached responses bypass guardrail evaluation on cache hits. This means a response that was approved by guardrails at write time is served directly on subsequent reads. If guardrail policies change after a response is cached, the old (potentially non-compliant) response may still be served until the cache entry expires or is evicted.
:::


### Recommended Combinations

| Use Case | Features | Rationale |
|---|---|---|
| **Multi-team platform** | B.1 + B.3 | Per-team credentials with per-team cost visibility |
| **High-availability gateway** | B.2 + B.5 | Fallback routing for resilience, caching for latency |
| **Regulated workloads** | B.1 + B.4 + B.3 | Access control, content safety, and cost tracking |
| **Cost-optimized platform** | B.2 + B.3 + B.5 | Load-balance across providers, track costs, cache responses |
| **Full platform** | B.1 + B.2 + B.3 + B.4 + B.5 | All features enabled for a complete enterprise deployment |