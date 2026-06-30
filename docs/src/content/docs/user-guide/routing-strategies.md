---
title: Provider Routing Strategies
description: "Provider routing with agentgateway priority groups: fallback and load balancing, plus the routing-config API."
sidebar:
  order: 4
---
The AI Gateway is the [agentgateway](https://github.com/agentgateway/agentgateway) proxy. Routing is expressed as agentgateway `ai.groups` **priority-group failover**: the gateway tries the providers in group 0, then group 1, and so on. The active chain is rendered into the gateway's YAML config by Terraform and delivered inline at container start.

This page covers how the strategies map onto agentgateway, how to manage custom configs through the routing-config API, and the known limitations carried over from the previous (Portkey) routing engine.

:::note[Server-side, not per-request]
agentgateway selects the provider chain from its rendered config. There is **no** per-request routing override -- the `x-portkey-config` and `x-routing-config` headers from earlier releases no longer exist. To change routing you update the rendered config (for the default chain) or the routing-config API (for named custom configs).
:::

## How Routing Works

The default rendered config ships a two-tier chain:

```yaml
backends:
- ai:
    groups:
    - providers:
      - name: bedrock-primary       # group 0: tried first
        provider:
          bedrock:
            model: anthropic.claude-sonnet-4-20250514-v1:0
    - providers:
      - name: anthropic-fallback    # group 1: tried if group 0 is evicted
        provider:
          anthropic:
            model: claude-sonnet-4-20250514
```

A route also carries an `ai` policy with `modelAliases`, which maps a requested model ID onto a backend model (for example, `gpt-4*` to a Bedrock Claude model). The `model` field in the request body is resolved against these aliases and the active chain.

agentgateway fails over between groups on **connection failure and health eviction**. It does not fail over on a specific upstream HTTP status code -- there is no per-edge `on_status_codes` trigger like the Portkey engine had. This is the most important behavioral difference to keep in mind when migrating a routing config.

## Strategies and How They Map

The routing-config API accepts three strategy modes. Each renders to agentgateway via `RoutingConfig.to_agentgateway_backend()`:

| Strategy | agentgateway mapping | Notes |
|---|---|---|
| **fallback** | Each target becomes its own priority group, in order. | Reproduces an ordered fallback chain. Failover is on connection/health eviction, not on a status code. |
| **loadbalance** | All targets in **one** group. | agentgateway load-balances within a group using power-of-two-choices. Balancing is **capacity-based**, not a 0--1 weight ratio. A per-target `weight` is carried but does not set an exact traffic split. |
| **conditional** | Request-field predicates are **dropped**; targets collapse to an ordered fallback chain. | agentgateway has no request-field predicate routing (e.g. on `max_tokens`). See the limitation below. |

### Fallback

Tries providers in priority order. If the primary group is connection-failed or evicted by health checks, agentgateway moves to the next group. Use this for "Bedrock primary, Anthropic-direct backup" style resilience.

### Load Balance

Spreads requests across the targets in a single group. agentgateway uses power-of-two-choices load balancing weighted by backend capacity. This is good for spreading traffic across providers or regions, but it does **not** implement an exact percentage split -- so it is not a precise A/B-test traffic splitter.

:::caution[Weights are not exact ratios]
The routing-config API still accepts a `weight` (0.0--1.0) per target for `loadbalance` mode and validates that weights sum to ~1.0, but agentgateway's load balancing is capacity-based. Treat weights as a hint, not a guaranteed split.
:::

### Conditional (limitation)

The Portkey engine could inspect a request field such as `max_tokens` and route to a different model tier (a "cost-optimized" pattern). agentgateway has no equivalent request-field predicate routing.

:::caution[Conditional routing is not supported]
A `conditional` config is still accepted by the routing-config API for backward compatibility, but the conditions are **dropped** on render: the targets collapse to a single priority-ordered fallback chain. If you depend on predicate-based model selection (e.g. short prompts to a cheaper model), implement it client-side by sending a different `model`. Flag any conditional configs during migration.
:::

## Managing Custom Configs via the API

The `routing_config` Lambda exposes a CRUD API (available when the Admin API is enabled). Custom configs are stored in DynamoDB as the rendered agentgateway backend JSON. Mutations require the **admin** scope and emit audit events.

:::note[Routing changes are not live-reloaded today]
Routing lives in the **static rendered agentgateway config** baked into the container at deploy time (`infrastructure/modules/compute/agentgateway-config.yaml.tftpl`, delivered inline at container start). A change made through the routing-config API is **persisted to DynamoDB**, but it does **not** take effect instantly and is **not** applied per team at request time. It takes effect on the **next config render + ECS task reload** — that is, when Terraform re-renders the gateway config and the ECS service rolls new tasks.

This is a current-state limitation. A render-and-reload path (and ultimately xDS-style dynamic config delivery) is the documented follow-up — see [ADR-017](/ai-gateway/adrs/017-agentgateway-data-plane-spike/). Until then, treat the routing-config API as a way to author and version configs that become live on the next deploy, not as a live routing control plane.
:::

| Method | Path | Description |
|---|---|---|
| `GET` | `/routing/configs` | List custom config summaries |
| `GET` | `/routing/configs/{name}` | Get a specific custom config |
| `POST` | `/routing/configs` | Create a custom config |
| `PUT` | `/routing/configs/{name}` | Update a custom config |
| `DELETE` | `/routing/configs/{name}` | Delete a custom config |

:::note[No built-in presets]
Earlier releases shipped eight preset config files under `infrastructure/portkey-configs/`. Those files were removed in the agentgateway migration. The API now serves only custom configs that you create; every config returned is `builtin: false`.
:::

### List all configs

```bash
curl -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  https://<admin-api-url>/routing/configs
```

### Get a specific config

```bash
curl -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  https://<admin-api-url>/routing/configs/my-fallback
```

### Create a fallback config

Each target becomes its own priority group, tried in order:

```bash
curl -X POST https://<admin-api-url>/routing/configs \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "bedrock-then-anthropic",
    "strategy": {"mode": "fallback"},
    "targets": [
      {"name": "primary", "provider": "bedrock", "override_params": {"model": "anthropic.claude-sonnet-4-20250514-v1:0"}},
      {"name": "backup", "provider": "anthropic", "override_params": {"model": "claude-sonnet-4-20250514"}}
    ],
    "metadata": {"description": "Bedrock primary, Anthropic-direct fallback"}
  }'
```

### Create a load-balance config

All targets share one group; agentgateway balances by capacity:

```bash
curl -X POST https://<admin-api-url>/routing/configs \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "spread-bedrock-anthropic",
    "strategy": {"mode": "loadbalance"},
    "targets": [
      {"name": "bedrock", "provider": "bedrock", "weight": 0.6, "override_params": {"model": "anthropic.claude-sonnet-4-20250514-v1:0"}},
      {"name": "anthropic", "provider": "anthropic", "weight": 0.4, "override_params": {"model": "claude-sonnet-4-20250514"}}
    ],
    "metadata": {"description": "Spread Anthropic-model traffic across Bedrock and direct"}
  }'
```

### Delete a custom config

```bash
curl -X DELETE https://<admin-api-url>/routing/configs/spread-bedrock-anthropic \
  -H "Authorization: Bearer ${ADMIN_TOKEN}"
```

## Config Field Reference

The routing-config API accepts the following fields. Note which ones still affect agentgateway behavior and which are carried for compatibility only.

| Field | Description | agentgateway effect |
|---|---|---|
| `strategy.mode` | `"fallback"`, `"loadbalance"`, or `"conditional"` | Determines how targets map to priority groups (see table above) |
| `strategy.on_status_codes` | HTTP status codes that triggered failover under Portkey | **No effect** -- agentgateway fails over on connection/health eviction |
| `strategy.conditions` | Condition objects (conditional mode only) | **Dropped on render** -- no predicate routing |
| `targets[].name` | Unique target name within the config | Carried as the provider `name` |
| `targets[].provider` | `bedrock`, `anthropic`, `openai`, `azure-openai`, `google` | Mapped to the agentgateway provider key (`openai` to `openAI`, `azure-openai` to `azure`, `google` to `gemini`) |
| `targets[].override_params.model` | Model ID for this target | Set as the provider `model` |
| `targets[].weight` | Traffic weight, 0.0--1.0 (loadbalance only) | Carried, but balancing is capacity-based -- not an exact ratio |
| `targets[].retry` | Per-target retry config | **No effect** -- no per-edge retry equivalent |
| `targets[].virtual_key` | Provider virtual-key reference | **No effect** -- credentials come from Secrets Manager / the ECS task role |
| `metadata.description` | Human-readable description | Stored with the config |

:::note[Bedrock authentication]
Bedrock targets are rendered with `policies.backendAuth.aws: {}`, which uses the gateway's ECS task-role credentials with SigV4 -- no static key. Other providers read their API key from Secrets Manager (injected as `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc.).
:::

## Editing the Default Chain

The default provider chain (served to requests with no custom config) lives in the rendered gateway config, `infrastructure/modules/compute/agentgateway-config.yaml.tftpl`. To change which providers are reachable or their failover order, edit that template and re-apply Terraform. The `enable_provider_fallback` and `routing_configs` Terraform variables control whether named configs are wired in. See the [Admin Guide](/ai-gateway/admin-guide/) for the deployment workflow.
