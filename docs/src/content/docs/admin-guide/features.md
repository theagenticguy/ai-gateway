---
title: Feature Toggles
description: "Optional platform features: multi-client isolation, fallback routing, cost attribution, guardrails, caching, rate limiting, audit logging, and SSO."
sidebar:
  order: 6
---
The AI Gateway includes optional features that extend the base platform. All features are **disabled by default** and can be enabled independently through toggle variables in your Terraform configuration.

:::note
Features are designed as additive modules. Enabling a feature creates new resources alongside the base infrastructure. Disabling a feature destroys only the resources it created.
:::

## Feature Overview

| Feature | Toggle Variable | Category |
|---|---|---|
| [Multi-Client Onboarding](#multi-client-onboarding) | `enable_multi_client` | Access Control |
| [Provider Fallback Routing](#provider-fallback-routing) | `enable_fallback_routing` | Routing |
| [Cost Attribution Pipeline](#cost-attribution-pipeline) | `enable_cost_attribution` | Cost Management |
| [Bedrock Guardrails](#bedrock-guardrails) | `enable_guardrails` | Content Safety |
| [ElastiCache Response Cache](#elasticache-response-cache) | `enable_response_cache` | Performance |
| [RPM & Token Rate Limiting](#rpm--token-rate-limiting) | `enable_admin_api` | Metering |
| [Usage Self-Service API](#usage-self-service-api) | `enable_admin_api` | Metering |
| [Dynamic Pricing Admin](#dynamic-pricing-admin) | `enable_admin_api` | Metering |
| [Audit Log Pipeline](#audit-log-pipeline) | `enable_audit_log` | Compliance |
| [Per-Team Cache Metrics](#per-team-cache-metrics) | `enable_cost_attribution` | Cost Management |
| [Identity Provider Federation](#identity-provider-federation) | `enable_user_auth` | Identity & SSO |
| [Pre-Token Group Mapping](#pre-token-group-mapping) | `enable_user_auth` | Identity & SSO |

---

## Access Control

### Multi-Client Onboarding

Per-team Cognito credentials that allow you to issue separate client IDs and secrets to each consuming team or service. Each client can be assigned a subset of OAuth scopes, enabling fine-grained access control.

**Resources created:**

- Additional Cognito User Pool clients (one per team)
- Per-client scope assignments (e.g., team A gets `invoke` only, team B gets `invoke` + `admin`)
- Optional per-client rate limiting via WAF rules

**How to enable:**

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

Each team receives its own `client_id` and `client_secret` from the Cognito User Pool. They use the standard `client_credentials` grant to obtain tokens scoped to their permissions. The ALB JWT listener validates the `scope` claim, ensuring teams can only access endpoints their scopes allow.

:::tip
Use the `admin` scope sparingly. Most consuming services only need `invoke` to call LLM endpoints through the gateway.
:::


---

## Routing

### Provider Fallback Routing

Portkey-native fallback and load-balancing configurations that route requests across multiple LLM providers. If the primary provider fails or is throttled, requests automatically fall back to a secondary provider.

**Routing strategies:**

| Strategy | Description | Use Case |
|---|---|---|
| **Fallback** | Try providers in order; move to next on failure | High availability: OpenAI primary, Bedrock fallback |
| **Load Balance** | Distribute requests across providers by weight | Cost optimization: 70% Bedrock, 30% OpenAI |
| **Retry** | Retry failed requests on the same or different provider | Transient error recovery |

**How to enable:**

```hcl
enable_fallback_routing = true
```

This deploys Portkey routing configuration files that define fallback chains and load-balancing weights. The configurations are passed to the gateway as environment variables or mounted config files.

**Example fallback configuration:**

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

**Example load-balancing configuration:**

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

## Cost Management

### Cost Attribution Pipeline

A serverless pipeline that counts tokens, maps them to provider pricing, and publishes cost metrics to CloudWatch. This enables per-team and per-model cost visibility.

**Resources created:**

| Resource | Purpose |
|---|---|
| Lambda function | Parses gateway logs, counts prompt/completion tokens, calculates cost |
| DynamoDB pricing table | Stores per-model pricing rates (cost per 1K tokens) |
| CloudWatch Logs subscription | Streams gateway logs to the Lambda function |
| CloudWatch custom metrics | `AIGateway/TokensUsed` and `AIGateway/EstimatedCostUsd` |
| Dashboard widgets | Token usage and cost-by-provider widgets added to the main dashboard |

**How to enable:**

```hcl
enable_cost_attribution = true
```

The gateway emits structured JSON logs for every request. A CloudWatch Logs subscription filter streams these logs to a Lambda function that extracts token counts, looks up per-model pricing in a DynamoDB table, calculates estimated cost, and publishes custom CloudWatch metrics under the `AIGateway` namespace.

:::note
The pricing table must be populated with current provider rates. Rates change frequently -- consider automating the update process or reviewing monthly.
:::


### Per-Team Cache Metrics

Extends the cost attribution pipeline to publish cache hit/miss metrics with a `Team` dimension, in addition to the existing `Provider` and `Model` dimensions.

| Metric | Dimensions | Description |
|---|---|---|
| `AIGateway/CacheHitRate` | Team, Provider, Model | Percentage of requests served from cache |
| `AIGateway/CacheSavingsUsd` | Team | Estimated cost savings from cache hits |

Use these metrics to identify teams with low cache hit rates and tune their request patterns (e.g., lowering temperature for deterministic calls).

---

## Content Safety

### Bedrock Guardrails

Content safety controls powered by Amazon Bedrock Guardrails. These are applied to requests and responses passing through the gateway, blocking harmful content before it reaches end users.

**Resources created:**

| Resource | Purpose |
|---|---|
| Bedrock Guardrail | Content filtering, PII detection, topic policies, word policies |
| Guardrail version | Immutable published version for production use |
| IAM policy | Grants the ECS task role permission to invoke Bedrock Guardrails |

**Guardrail policies:**

| Policy Type | Description | Default Behavior |
|---|---|---|
| **Content Filtering** | Blocks harmful content categories (hate, violence, sexual, misconduct) | Block at HIGH strength for all categories |
| **PII Blocking** | Detects and blocks personally identifiable information | Blocks SSN, credit card, email, phone in responses |
| **Topic Policies** | Blocks requests about restricted topics | Configurable deny-list |
| **Word Policies** | Blocks specific words or patterns | Configurable word list |

**How to enable:**

```hcl
enable_guardrails = true

guardrail_config = {
  content_filter_strength = "HIGH"
  pii_action             = "BLOCK"
  blocked_topics         = ["financial-advice", "medical-diagnosis"]
  blocked_words          = []
}
```

When guardrails are enabled, the gateway invokes Bedrock's `ApplyGuardrail` API on both the input (prompt) and output (completion). If either triggers a policy violation, the request is blocked with an explanatory error message.

:::caution
Bedrock Guardrails are a regional service. Ensure the guardrail is created in the same region as the ECS cluster. Additional Bedrock quotas may need to be requested for high-throughput use.
:::


---

## Performance

### ElastiCache Response Cache

A Redis-based response cache that stores LLM completions keyed by the request hash. Identical requests return cached responses, reducing latency and provider API costs.

**Resources created:**

| Resource | Purpose |
|---|---|
| ElastiCache Serverless (Redis 7.1) | Cache store with TLS encryption in transit |
| Security group | Allows port 6379 from ECS tasks only |
| Subnet group | Places Redis in private subnets |

**How to enable:**

```hcl
enable_response_cache = true
```

When enabled, the gateway container receives additional environment variables:

| Variable | Value | Purpose |
|---|---|---|
| `CACHE_STORE` | `redis` | Tells Portkey to use Redis for caching |
| `REDIS_URL` | `rediss://{endpoint}:6379` | TLS-encrypted Redis endpoint |
| `CACHE_TTL` | `3600` | Default cache TTL in seconds (1 hour) |

Portkey's built-in caching layer hashes the request body (model, messages, parameters) to generate a cache key. On cache hit, the cached response is returned immediately without calling the LLM provider. On cache miss, the provider response is stored in Redis for subsequent requests.

:::tip
Caching works best for deterministic requests (temperature=0). For creative/random outputs (temperature>0), caching may return unexpected repeated responses. Consider setting `cache: "none"` in the Portkey request headers for non-deterministic calls.
:::


---

## Metering & Governance

These features run on the [Admin API](/ai-gateway/admin-guide/admin-api/) plane (see [ADR-014](/ai-gateway/adrs/014-two-plane-architecture-split/)) and are enabled with `enable_admin_api = true`.

### RPM & Token Rate Limiting

Per-team rate limiting with two dimensions: requests per minute (RPM) and daily token consumption. Limits are defined per tenant tier and enforced via DynamoDB atomic counters.

| Dimension | DynamoDB Key | Window | TTL |
|---|---|---|---|
| RPM | `RATE#RPM#{team}` / `MINUTE#{bucket}` | 1-minute sliding window | 120 seconds |
| Daily tokens | `RATE#TOKENS#{team}` / `DAY#{YYYY-MM-DD}` | Calendar day (UTC) | End of day + 1 hour |

Each request atomically increments the counter. When a limit is exceeded, the gateway returns a `429`-equivalent response with a `retry_after_seconds` hint.

**Tier defaults:**

| Tier | RPM | Daily Tokens |
|---|---|---|
| sandbox | 20 | 100,000 |
| standard | 100 | 1,000,000 |
| premium | 500 | 10,000,000 |
| enterprise | -1 (unlimited) | -1 (unlimited) |

If DynamoDB is unreachable, the request is *allowed* and a warning is logged. Rate limiting never blocks requests due to infrastructure failures.

### Usage Self-Service API

A read-only API that lets teams query their own usage without waiting for monthly chargeback reports.

| Method | Path | Description |
|---|---|---|
| `GET` | `/usage/{team}` | Current period usage, budget utilization, and per-model breakdown |
| `GET` | `/usage/{team}/history` | Historical usage by month |

### Dynamic Pricing Admin

Runtime pricing overrides stored in DynamoDB with a static fallback table. Operators can update model pricing without redeploying the Lambda.

| Method | Path | Description |
|---|---|---|
| `GET` | `/pricing` | List all pricing entries (DynamoDB overrides merged with static defaults) |
| `GET` | `/pricing/{provider}/{model}` | Get pricing for a specific model |
| `PUT` | `/pricing/{provider}/{model}` | Create or update a pricing override |
| `DELETE` | `/pricing/{provider}/{model}` | Remove a DynamoDB override (reverts to static default) |

The `source` field in responses indicates whether a price came from `"dynamodb"` or `"static"`.

---

## Compliance

### Audit Log Pipeline

A structured audit trail for all gateway requests, stored as Parquet files in S3 for Athena queries.

**Resources created:**

| Resource | Purpose |
|---|---|
| Kinesis Firehose | Ingests audit events, buffers, and converts to Parquet |
| S3 bucket | Stores Parquet files with Hive-style partitioning (`year=/month=/day=`) |
| Glue Catalog | Database + table for Athena SQL queries |
| CloudWatch Log Group | Firehose delivery error logs |

**How to enable:**

```hcl
enable_audit_log = true
```

**Audit record schema:**

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

**Lifecycle:** 0-90 days S3 Standard, 90-365 days S3 Standard-IA, 365+ days expired.

---

## Identity & SSO

### Identity Provider Federation

Federation with external identity providers (AWS Identity Center, Okta, Entra ID, or any SAML 2.0 / OIDC-compliant IdP) through the existing Cognito User Pool. Users authenticate with their corporate credentials via the Cognito Hosted UI and receive JWT tokens for gateway access.

**Resources created:**

| Resource | Purpose |
|---|---|
| `aws_cognito_identity_provider` | One per entry in `identity_providers` (SAML or OIDC) |
| Cognito app client (`user_sso`) | Public client for `authorization_code` flow with PKCE |
| Cognito Hosted UI domain | Login page served by Cognito |
| Pre-Token-Generation V2 Lambda | Maps IdP groups to custom gateway claims |

**How to enable:**

```hcl
enable_user_auth = true

identity_providers = {
  IdentityCenter = {
    provider_type     = "SAML"
    metadata_url      = "https://portal.sso.us-east-1.amazonaws.com/saml/metadata/..."
    provider_details  = {}
    attribute_mapping = {}
  }
}

callback_urls = ["https://gateway.example.com/callback"]
logout_urls   = ["https://gateway.example.com/logout"]
```

The user authenticates via the Cognito Hosted UI, which redirects to the configured IdP. After authentication, Cognito issues an `authorization_code` that the application exchanges for JWT tokens using PKCE. The ALB validates these tokens the same way it validates M2M tokens.

:::tip
The `user_sso` app client uses PKCE and does not require a client secret. This makes it safe for single-page applications and CLI tools.
:::


You can federate with multiple IdPs simultaneously by adding entries to the `identity_providers` map.

### Pre-Token Group Mapping

A Pre-Token-Generation V2 Lambda that runs during Cognito token issuance and maps IdP group memberships to structured gateway claims. This enables per-team authorization, cost attribution, and tier-based rate limiting without manual user provisioning.

**Custom claims injected:**

| Claim | Purpose |
|---|---|
| `custom:team` | Team identifier for routing and cost attribution |
| `custom:org_unit` | Organizational unit |
| `custom:cost_center` | Cost center for billing attribution |
| `custom:tenant_tier` | Authorization tier (e.g., `admin`, `standard`, `sandbox`) |

**How to enable:**

```hcl
group_mapping = {
  "aws-ai-gateway-admins" = {
    team        = "platform"
    org_unit    = "ai-engineering"
    cost_center = "CC-1234"
    tenant_tier = "admin"
  }
  "aws-ml-engineers" = {
    team        = "ml-eng"
    org_unit    = "ai-engineering"
    cost_center = "CC-5678"
    tenant_tier = "standard"
  }
}
```

After a user authenticates via their IdP, Cognito triggers the Pre-Token Lambda before issuing the JWT. The Lambda reads the user's IdP groups, looks up the first matching entry in `group_mapping`, and injects the corresponding claims into the token.

:::caution
The group mapping is stored as a Lambda environment variable and updated via `terraform apply`. If you add or rename IdP groups, you must update the mapping and redeploy.
:::


### Coexistence with M2M Authentication

User SSO and M2M authentication share the same Cognito User Pool and ALB JWT validation. Both flows produce JWTs that the ALB validates against the same JWKS endpoint.

| Aspect | M2M | User SSO |
|---|---|---|
| **OAuth grant** | `client_credentials` | `authorization_code` with PKCE |
| **Credentials** | Client ID + secret | Corporate IdP credentials |
| **Token contains** | Scopes (`invoke`, `admin`) | Scopes + custom claims (`team`, `tier`) |
| **Use case** | Service-to-service automation | Developer portals, dashboards, CLI tools |

:::note
You do not need to choose between M2M and user auth. Both can be active simultaneously. The ALB accepts tokens from either flow.
:::


---

## Feature Compatibility Matrix

All features can be enabled independently. The following matrix shows interactions for the platform features:

| | Multi-Client | Fallback Routing | Cost Attribution | Guardrails | Cache |
|---|---|---|---|---|---|
| **Multi-Client** | -- | Compatible | Compatible (per-client cost) | Compatible | Compatible |
| **Fallback Routing** | Compatible | -- | Compatible (multi-provider cost) | Compatible | Compatible |
| **Cost Attribution** | Compatible (per-client cost) | Compatible (multi-provider cost) | -- | Compatible | Compatible (tracks cache savings) |
| **Guardrails** | Compatible | Compatible | Compatible | -- | Order-dependent (see note) |
| **Cache** | Compatible | Compatible | Compatible (tracks cache savings) | Order-dependent (see note) | -- |

:::note[Guardrails + Cache Interaction]
When both guardrails and caching are enabled, cached responses bypass guardrail evaluation on cache hits. If guardrail policies change after a response is cached, the old response may still be served until the cache entry expires.
:::


### Recommended Combinations

| Use Case | Features | Rationale |
|---|---|---|
| **Multi-team platform** | Multi-Client + Cost Attribution | Per-team credentials with per-team cost visibility |
| **High-availability gateway** | Fallback Routing + Cache | Fallback routing for resilience, caching for latency |
| **Regulated workloads** | Multi-Client + Guardrails + Cost Attribution | Access control, content safety, and cost tracking |
| **Cost-optimized platform** | Fallback Routing + Cost Attribution + Cache | Load-balance across providers, track costs, cache responses |
| **Full platform** | All features enabled | Complete enterprise deployment |
