---
title: Feature Toggles
description: "Optional platform features: multi-client isolation, fallback routing, cost attribution, guardrails, prompt caching, rate limiting, audit logging, and SSO."
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
| [Multi-Client Onboarding](#multi-client-onboarding) | `client_configs` (non-empty map) | Access Control |
| [Provider Fallback Routing](#provider-fallback-routing) | (gateway config) + `enable_routing_api` | Routing |
| [Cost Attribution Pipeline](#cost-attribution-pipeline) | `enable_cost_attribution` | Cost Management |
| [Bedrock Guardrails](#bedrock-guardrails) | `enable_guardrails` | Content Safety |
| [Provider-Native Prompt Caching](#provider-native-prompt-caching) | (gateway config) | Performance |
| [RPM & Token Rate Limiting](#rpm--token-rate-limiting) | `enable_admin_api` | Metering |
| [Usage Self-Service API](#usage-self-service-api) | `enable_admin_api` | Metering |
| [Dynamic Pricing Admin](#dynamic-pricing-admin) | `enable_admin_api` | Metering |
| [Audit Log Pipeline](#audit-log-pipeline) | `enable_audit_log` | Compliance |
| [Identity Provider Federation](#identity-provider-federation) | `enable_user_auth` | Identity & SSO |
| [Pre-Token Group Mapping](#pre-token-group-mapping) | `enable_user_auth` | Identity & SSO |

---

## Access Control

### Multi-Client Onboarding

Per-team Cognito credentials that allow you to issue separate client IDs and secrets to each consuming team or service. Each client can be assigned a subset of OAuth scopes, enabling fine-grained access control.

There is **no boolean toggle** for this feature. The `clients` Terraform module is driven entirely by the `client_configs` map variable: one entry per team, each provisioning a dedicated Cognito app client. The module is created only when `client_configs` is non-empty (`length(var.client_configs) > 0`); leaving it at the default empty map (`{}`) means no per-team clients are created.

**Resources created (one set per `client_configs` entry):**

- A dedicated Cognito User Pool app client (`aws_cognito_user_pool_client`) named `<project_name>-<team>-<environment>`, with a generated client secret
- Per-client scope assignments drawn from each entry's `allowed_scopes` (e.g., team A gets `invoke` only, team B gets `invoke` + `admin`)
- A 1-hour `client_credentials` access-token validity

**How to enable:**

Set the `client_configs` map. Each key is a team identifier; each value is an object with `allowed_scopes` (a list of OAuth scope identifiers) and a human-readable `description`:

```hcl
client_configs = {
  platform = {
    allowed_scopes = ["https://gateway.internal/invoke"]
    description    = "Platform engineering team"
  }
  ml-ops = {
    allowed_scopes = ["https://gateway.internal/invoke", "https://gateway.internal/admin"]
    description    = "ML Operations team"
  }
}
```

Each team receives its own `client_id` and `client_secret` from the Cognito User Pool (exposed via the `client_ids` and `client_secrets` Terraform outputs, keyed by team). They use the standard `client_credentials` grant to obtain tokens scoped to their permissions. The ALB JWT listener validates the `scope` claim, ensuring teams can only access endpoints their scopes allow.

:::tip
Use the `admin` scope sparingly. Most consuming services only need `invoke` to call LLM endpoints through the gateway.
:::


---

## Routing

### Provider Fallback Routing

agentgateway routes across providers using **priority-group failover**, declared in the rendered config (`compute/agentgateway-config.yaml.tftpl`) under `ai.groups`. This is **always on** — there is no enable/disable toggle for failover itself. Each group is a list of providers; the gateway tries the first group, then falls through to the next on failure. The default config makes **Bedrock the primary** and **Anthropic-direct the fallback**. Bedrock uses ambient ECS task-role credentials (SigV4, no static key); the Anthropic fallback uses an API key from Secrets Manager.

The optional **dynamic routing API** (Lambda + DynamoDB, gated by `enable_routing_api`) lets operators author and version routing rules; the `routing_config` Lambda renders them into the agentgateway backend config.

:::note[Routing changes are not live-reloaded today]
Routing lives in the static rendered agentgateway config baked into the container at deploy time. Changes made through the routing-config admin API are persisted to DynamoDB but take effect on the **next config render + ECS task reload**, not instantly and not per team at request time. The render-and-reload path (and xDS-style dynamic delivery) is a documented follow-up — see [ADR-017](/ai-gateway/adrs/017-agentgateway-data-plane-spike/) and [Routing Strategies](/ai-gateway/user-guide/routing-strategies/).
:::

**How it works:**

| Mechanism | Where | Behavior |
|---|---|---|
| **Priority-group failover** | `ai.groups` in the rendered config | Try each group in order; fall through to the next group when a provider errors |
| **Model aliases** | `policies.ai.modelAliases` | Rewrite a requested model id to a provider-specific id (e.g. map `gpt-4*` → a Bedrock Claude model) |

**Example (excerpt of the rendered config):**

```yaml
ai:
  groups:
    - providers:
        - name: bedrock-primary
          provider:
            bedrock:
              model: anthropic.claude-sonnet-4-20250514-v1:0
              region: us-east-1
          policies:
            backendAuth:
              aws: {}            # ambient ECS task-role SigV4
    - providers:
        - name: anthropic-fallback
          provider:
            anthropic:
              model: claude-sonnet-4-20250514
          policies:
            backendAuth:
              key: ${ANTHROPIC_API_KEY}
```

:::note
There is no `x-portkey-config` or `x-portkey-provider` header. Provider selection and failover are defined in the rendered config, not per-request headers. The `routing` admin API (`/routing`) and the `routing_config` Lambda manage the dynamic backend that renders into this config.
:::

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

The cost attribution Lambda reads agentgateway's flat access log, including the `cached_input_tokens` and `cache_creation_input_tokens` fields emitted when [provider-native prompt caching](#provider-native-prompt-caching) is active, so prompt-cache savings show up in per-team cost metrics.

---

## Content Safety

### Bedrock Guardrails

Content safety powered by Amazon Bedrock Guardrails. agentgateway calls the Bedrock `ApplyGuardrail` API **inline** — in path, on both the input (prompt) and the output (completion) — signed with the ECS task role. There is no scanner Lambda and no separate scanner route; the `guardrails` Terraform module provisions the guardrail resource the gateway invokes.

**Resources created:**

| Resource | Purpose |
|---|---|
| Bedrock Guardrail | Content filters, sensitive-information (PII) policy, topic policies, word policies |
| Guardrail version | Immutable published version for production use |
| IAM policy | Grants the ECS task role permission to call `ApplyGuardrail` |

**Detect-only by default.** When `enforce_guardrails = false` (the default), every filter action is set to `NONE`: `ApplyGuardrail` still evaluates each request and returns assessments, but the gateway passes the request through untouched (log-only). Flip `enforce_guardrails = true` per environment to make filters `BLOCK`/`ANONYMIZE` and attach topic filters.

**Guardrail policies:**

| Policy Type | Description | Default Behavior (`enforce_guardrails = false`) |
|---|---|---|
| **Content Filtering** | Hate, violence, sexual, misconduct, prompt-attack categories | Evaluated at the configured strength, action `NONE` (detect/log only) |
| **Sensitive Information (PII)** | Detects PII entities (SSN, credit card, phone, email by default) | Detected, action `NONE` — set to `BLOCK`/`ANONYMIZE` when enforcing |
| **Topic Policies** | Restricted-topic deny-list | Attached only when enforcing |
| **Word Policies** | Specific words or phrases | Evaluated, action `NONE` until enforcing |

**How to enable:**

```hcl
enable_guardrails       = true
enforce_guardrails      = false   # detect/log-only; set true to BLOCK
content_filter_strength = "HIGH"
blocked_pii_types       = ["SSN", "CREDIT_DEBIT_CARD_NUMBER", "PHONE", "EMAIL"]
blocked_topics          = []
blocked_words           = []
```

When the gateway's `bedrockGuardrails` policy is wired (a non-empty `bedrock_guardrail_id` is rendered into the config), every request and response runs through `ApplyGuardrail`. With enforcement off, the call returns `action=NONE` and nothing is blocked; with enforcement on, a policy violation blocks the request with the configured message.

:::caution
Bedrock Guardrails are a regional service. Ensure the guardrail is created in the same region as the ECS cluster. Additional Bedrock quotas may need to be requested for high-throughput use.
:::


---

## Performance

### Provider-Native Prompt Caching

agentgateway has **no response cache** — there is no ElastiCache/Redis tier. Instead it relies on **provider-native prompt caching**, configured by the opt-in `promptCaching` policy in the rendered config. The policy injects Bedrock `cachePoint` markers into the system prompt, message history, and tool definitions, gated at a minimum token threshold.

This is **not** a response cache: every request still round-trips to the model and bills output tokens. What it saves is **input-token cost on prefix reuse** — a long shared system prompt or conversation prefix is billed at the cached (cheaper) rate on subsequent calls.

**Scope and configuration (in the rendered config, opt-in):**

```yaml
policies:
  ai:
    promptCaching:
      cacheSystem: true
      cacheMessages: true
      cacheTools: true
      minTokens: 1024
```

| Aspect | Behavior |
|---|---|
| **Opt-in** | No `cachePoint` markers are added unless the `promptCaching` block is present |
| **Bedrock path only** | Markers are injected on the `bedrock-primary` provider; the Anthropic-fallback provider ignores this policy |
| **Anthropic fallback** | Caching there depends on the client sending `cache_control`, which agentgateway passes through |
| **`minTokens`** | Prefixes below the threshold are not marked, avoiding overhead on short prompts |

Prompt-cache token counts surface in the access log as `cached_input_tokens` / `cache_creation_input_tokens` and flow through to cost attribution. See [ADR-017](/ai-gateway/adrs/017-agentgateway-data-plane-spike/) (which supersedes the response-cache decision in [ADR-012](/ai-gateway/adrs/012-response-cache-strategy/)).


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

| | Multi-Client | Fallback Routing | Cost Attribution | Guardrails | Prompt Caching |
|---|---|---|---|---|---|
| **Multi-Client** | -- | Compatible | Compatible (per-client cost) | Compatible | Compatible |
| **Fallback Routing** | Compatible | -- | Compatible (multi-provider cost) | Compatible | Compatible |
| **Cost Attribution** | Compatible (per-client cost) | Compatible (multi-provider cost) | -- | Compatible | Compatible (tracks cache-token savings) |
| **Guardrails** | Compatible | Compatible | Compatible | -- | Compatible |
| **Prompt Caching** | Compatible | Compatible (Bedrock path only) | Compatible (tracks cache-token savings) | Compatible | -- |

:::note[Guardrails + Prompt Caching]
Prompt caching is not a response cache — every request still reaches the model, so inline Bedrock Guardrails evaluate every request and response regardless of cache state. There is no stale-response concern.
:::


### Recommended Combinations

| Use Case | Features | Rationale |
|---|---|---|
| **Multi-team platform** | Multi-Client + Cost Attribution | Per-team credentials with per-team cost visibility |
| **High-availability gateway** | Fallback Routing + Prompt Caching | Priority-group failover for resilience, prompt caching to cut input-token cost on prefix reuse |
| **Regulated workloads** | Multi-Client + Guardrails + Cost Attribution | Access control, content safety, and cost tracking |
| **Cost-optimized platform** | Fallback Routing + Cost Attribution + Prompt Caching | Fail over across providers, track costs, cut input-token cost on the Bedrock path |
| **Full platform** | All features enabled | Complete enterprise deployment |
