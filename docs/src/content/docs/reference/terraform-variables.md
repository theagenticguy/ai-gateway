---
title: Terraform Variables
description: Complete reference for all Terraform input variables across core infrastructure and feature modules.
sidebar:
  order: 2
---

All Terraform input variables for the AI Gateway infrastructure, organized by category. Every optional feature is disabled by default and enabled via its toggle variable.

---

## Core Infrastructure

| Variable | Type | Default | Description |
|---|---|---|---|
| `aws_region` | `string` | `"us-east-1"` | AWS region to deploy into |
| `environment` | `string` | -- (required) | Deployment environment (`dev` or `prod`) |
| `project_name` | `string` | `"ai-gateway"` | Project name used for resource naming |
| `vpc_cidr` | `string` | `"10.0.0.0/16"` | CIDR block for the VPC |
| `gateway_image` | `string` | `"ghcr.io/agentgateway/agentgateway:latest"` | Docker image URI for the AI Gateway data plane (agentgateway, ADR-017). Overridden at apply time with the ECR URI pinned + mirrored by the release workflow; the upstream GHCR default keeps `plan`/`validate` resolvable. |
| `gateway_desired_count` | `number` | `2` | Desired number of gateway ECS tasks |
| `gateway_cpu` | `number` | `1024` | Total CPU units for the gateway ECS task |
| `gateway_memory` | `number` | `2048` | Total memory (MiB) for the gateway ECS task |
| `autoscaling_min_capacity` | `number` | `2` | Minimum number of ECS tasks for autoscaling |
| `autoscaling_max_capacity` | `number` | `6` | Maximum number of ECS tasks for autoscaling |
| `certificate_arn` | `string` | `""` | ACM certificate ARN for HTTPS listener |
| `enable_waf` | `bool` | `true` | Whether to enable WAF on the ALB |

:::note
The `environment` variable is validated to accept only `dev` or `prod`. Passing any other value will fail at plan time.
:::

---

## Authentication

| Variable | Type | Default | Description |
|---|---|---|---|
| `cognito_user_pool_id` | `string` | `""` | Cognito User Pool ID for JWT validation. Leave empty to disable JWT auth. |
| `cognito_domain_prefix` | `string` | `""` | Cognito User Pool domain prefix for the token endpoint. Leave empty to skip domain creation. |
| `enable_jwt_auth` | `bool` | `false` | Whether to enable ALB JWT validation. Requires `certificate_arn` and `cognito_user_pool_id`. |
| `identity_providers` | `map(object)` | `{}` | Map of external identity providers (SAML/OIDC) to federate with Cognito |
| `enable_user_auth` | `bool` | `false` | Whether to enable user-facing SSO authentication (authorization_code flow) |
| `callback_urls` | `list(string)` | `["http://localhost:3000/callback"]` | List of allowed callback URLs for the user SSO client |
| `logout_urls` | `list(string)` | `["http://localhost:3000/logout"]` | List of allowed logout URLs for the user SSO client |
| `group_mapping` | `map(object)` | `{}` | Mapping from IdP group names to gateway claims (team, org_unit, cost_center, tenant_tier) |

### `identity_providers` object schema

Each entry in the `identity_providers` map has the following shape:

```hcl
identity_providers = {
  my_idp = {
    provider_type     = "SAML"          # "SAML" or "OIDC"
    metadata_url      = "https://..."   # IdP metadata URL
    provider_details  = { ... }         # Provider-specific details
    attribute_mapping = { ... }         # Attribute mapping to Cognito
  }
}
```

### `group_mapping` object schema

Each entry maps an IdP group name to gateway claims:

```hcl
group_mapping = {
  "Engineering" = {
    team        = "platform"
    org_unit    = "engineering"
    cost_center = "CC-1234"
    tenant_tier = "premium"
  }
}
```

---

## Clients

| Variable | Type | Default | Description |
|---|---|---|---|
| `client_configs` | `map(object)` | `{}` | Map of team configurations for per-team Cognito app clients |

### `client_configs` object schema

Each key is a team identifier. The value specifies allowed OAuth scopes and a description:

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

---

## Routing

| Variable | Type | Default | Description |
|---|---|---|---|
| `enable_provider_fallback` | `bool` | `false` | Whether to enable provider fallback routing. When true, routing configs are wired into the gateway. |
| `routing_configs` | `map(string)` | `{}` | Map of named routing configurations as JSON strings. Keys are config names (e.g. `anthropic`, `openai`), values are agentgateway routing JSON (`ai.groups` priority tiers). |

:::note
Routing lives in the agentgateway YAML config, not in environment variables. The default provider chain (Bedrock primary, Anthropic-direct fallback) is rendered into the inline config; named custom configs are managed through the routing-config API. See [Routing Strategies](/ai-gateway/user-guide/routing-strategies/).
:::

---

## Guardrails

Content safety is **inline Bedrock Guardrails** (the `ApplyGuardrail` API, called in-path by agentgateway's `promptGuard` policy on both request and response). There is no separate content-scanner Lambda.

| Variable | Type | Default | Description |
|---|---|---|---|
| `enable_guardrails` | `bool` | `true` | Whether to create the Bedrock Guardrail and wire it into the agentgateway data plane (ADR-017). When true, the guardrail runs inline in detect/log-only mode by default. |
| `enforce_guardrails` | `bool` | `false` | `false` = detect/LOG-ONLY (filters evaluate and emit assessments but never block or anonymize; topic filters off). `true` = BLOCK on trip and attach topic filters. Set per environment (e.g. dev=false, prod selectively true). |
| `guardrails_blocked_topics` | `list(object)` | See below | List of topics to block, each with a name, definition, and optional examples |
| `guardrails_blocked_words` | `list(string)` | `[]` | List of words or phrases to block in inputs and outputs |
| `guardrails_content_filter_strength` | `string` | `"HIGH"` | Strength of content filters (`LOW`, `MEDIUM`, or `HIGH`) |

### `guardrails_blocked_topics` default

```hcl
[
  {
    name       = "competitor_products"
    definition = "Discussions or recommendations about competitor products and services."
    examples   = ["Tell me about competing AI platforms"]
  },
  {
    name       = "internal_financials"
    definition = "Internal financial data, revenue figures, or unreleased business metrics."
    examples   = ["What is the company revenue this quarter"]
  }
]
```

:::caution
The `guardrails_content_filter_strength` variable is validated at plan time. Only `LOW`, `MEDIUM`, and `HIGH` are accepted.
:::

:::note[Guardrail ID/version are computed, not set]
The `bedrock_guardrail_id` and `bedrock_guardrail_version` consumed by the compute module are **outputs of the guardrails module**, wired in `main.tf` (they are not root input variables). Setting `enable_guardrails = true` creates the guardrail and passes its ID/version into the rendered agentgateway config. Setting it to `false` leaves them empty, which omits the guardrail block from the config.
:::

---

## Cost Attribution & Observability

| Variable | Type | Default | Description |
|---|---|---|---|
| `enable_cost_attribution` | `bool` | `false` | Whether to deploy the cost attribution Lambda pipeline |
| `alarm_sns_topic_arns` | `list(string)` | `[]` | List of SNS topic ARNs for CloudWatch alarm notifications. If empty, a default topic is created. |
| `budget_limit_daily_usd` | `number` | `1000` | Daily budget limit in USD for dashboard gauge and budget alarm |
| `budget_alarm_threshold_pct` | `number` | `80` | Percentage of daily budget that triggers the budget utilization alarm |
| `error_rate_threshold_pct` | `number` | `5` | Error rate percentage threshold that triggers the high error rate alarm |
| `error_rate_evaluation_minutes` | `number` | `5` | Number of 1-minute evaluation periods for the error rate alarm |
| `p99_latency_threshold_ms` | `number` | `30000` | P99 latency threshold in milliseconds that triggers the high latency alarm |
| `latency_evaluation_minutes` | `number` | `5` | Number of 1-minute evaluation periods for the latency alarm |
| `provider_down_minutes` | `number` | `10` | Number of consecutive 1-minute periods with zero requests before declaring a provider down |

---

## Budgets

| Variable | Type | Default | Description |
|---|---|---|---|
| `enable_budgets` | `bool` | `false` | Whether to deploy the budget and usage tracking DynamoDB tables |

---

## Chargeback

| Variable | Type | Default | Description |
|---|---|---|---|
| `enable_chargeback` | `bool` | `false` | Whether to deploy the monthly chargeback report pipeline (requires `enable_budgets`) |

:::note
The chargeback module depends on the budgets module. Setting `enable_chargeback = true` without `enable_budgets = true` will result in missing resources.
:::

---

## Audit Log

| Variable | Type | Default | Description |
|---|---|---|---|
| `enable_audit_log` | `bool` | `false` | Enable audit logging via Firehose to S3 |

---

## Admin API

| Variable | Type | Default | Description |
|---|---|---|---|
| `enable_admin_api` | `bool` | `false` | Enable the API Gateway admin plane (also enables team_registration and routing modules) |

:::tip
Enabling the Admin API unlocks the metering and governance features: rate limiting, usage self-service, dynamic pricing admin, team management, budget management, and routing config management.
:::

---

## Inspector

| Variable | Type | Default | Description |
|---|---|---|---|
| `enable_inspector` | `bool` | `false` | Whether to enable Amazon Inspector enhanced scanning for ECR repositories |

---

## AppConfig

| Variable | Type | Default | Description |
|---|---|---|---|
| `enable_appconfig` | `bool` | `false` | Enable AWS AppConfig for feature flag and dynamic configuration management |

---

## Quick Reference: All Feature Toggles

A summary of every feature toggle and its default state:

| Toggle | Default | Feature |
|---|---|---|
| `enable_waf` | `true` | AWS WAF on the ALB |
| `enable_guardrails` | `true` | Inline Bedrock Guardrails content safety (detect/log-only unless `enforce_guardrails`) |
| `enforce_guardrails` | `false` | Flip guardrails from detect/log-only to BLOCK |
| `enable_jwt_auth` | `false` | ALB JWT validation via Cognito |
| `enable_user_auth` | `false` | User-facing SSO (authorization_code flow) |
| `enable_provider_fallback` | `false` | Provider fallback routing |
| `enable_cost_attribution` | `false` | Cost attribution Lambda pipeline |
| `enable_budgets` | `false` | Budget and usage tracking |
| `enable_chargeback` | `false` | Monthly chargeback report pipeline |
| `enable_audit_log` | `false` | Firehose-to-S3 audit logging |
| `enable_admin_api` | `false` | API Gateway admin plane (metering and governance features) |
| `enable_inspector` | `false` | Amazon Inspector ECR scanning |
| `enable_appconfig` | `false` | AWS AppConfig feature flags |
