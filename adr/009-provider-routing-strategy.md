# ADR-009: Provider Routing Strategy

## Status

Accepted

## Date

2026-03-20

## Context

The AI Gateway currently sends each request to a single LLM provider with no automatic failover. If the primary provider experiences an outage, rate-limits the request (HTTP 429), or returns a server error (5xx), the client receives the error directly and must handle retries itself.

This creates fragility in production workloads:

- **Bedrock throttling**: During high-demand periods, Bedrock returns 429s that propagate to callers.
- **Provider outages**: A single-provider architecture means any provider downtime is a full outage for models served by that provider.
- **No client-side retry standardization**: Each client team implements their own retry/fallback logic, leading to inconsistent behavior and duplicated effort.

## Decision

Use Portkey's native routing engine to implement provider-level fallback and load-balance strategies. Routing configs are JSON objects passed via the `x-portkey-config` header (base64-encoded) or injected as default environment variables in the gateway container.

### Routing modes

1. **Single** (default, current behavior): One provider per request, determined by `x-portkey-provider` header.
2. **Fallback**: Ordered list of providers. On qualifying errors (429, 5xx), the gateway tries the next provider automatically. Each target can have per-provider retry settings.
3. **Load balance**: Weighted distribution across providers. Useful for quota spreading or cost optimization.

### Pre-built configs

| Config | Primary | Fallback | Use case |
|---|---|---|---|
| `fallback-anthropic.json` | Bedrock | Anthropic direct API | Anthropic models with Bedrock-first routing |
| `fallback-openai.json` | OpenAI | Azure OpenAI | OpenAI models with Azure fallback |
| `loadbalance-multi.json` | Bedrock (60%) | Anthropic (40%) | Distribute Anthropic traffic across providers |

### Infrastructure integration

When `enable_provider_fallback` is set to `true`, Terraform injects the fallback configs as base64-encoded environment variables in the ECS task definition. Clients can also override per-request via the `x-portkey-config` header.

## Options Considered

### Option 1: Custom reverse proxy (rejected)

Build a custom routing layer (e.g., Envoy, nginx, or a Python service) in front of the Portkey gateway.

- **Pro**: Full control over routing logic.
- **Con**: Significant development and operational overhead. Another service to deploy, monitor, and maintain. Duplicates functionality that Portkey already provides natively.

### Option 2: API Gateway routing rules (rejected)

Use AWS API Gateway with Lambda authorizers to implement provider fallback at the edge.

- **Pro**: AWS-native, integrates with existing infrastructure.
- **Con**: Adds latency (Lambda cold starts). Limited retry logic. Cannot inspect LLM-specific response codes easily. Does not support weighted load balancing across providers.

### Option 3: Portkey native routing (accepted)

Use Portkey's built-in `strategy` configuration with `fallback` and `loadbalance` modes.

- **Pro**: Zero additional infrastructure. Zero code changes. Battle-tested routing logic. Per-target retry configuration. Transparent to clients (they can still use the standard OpenAI-compatible API).
- **Con**: Vendor-specific config format (JSON schema tied to Portkey). If we migrate away from Portkey, these configs would need rewriting.

## Consequences

### Positive

- **Improved resilience**: Automatic failover from Bedrock to direct API (or vice versa) on provider errors.
- **Zero code changes**: All routing is config-driven. No application code modifications needed.
- **Client simplicity**: Clients no longer need their own retry/fallback logic for provider-level failures.
- **Incremental rollout**: `enable_provider_fallback` defaults to `false`, so existing deployments are unaffected until opted in.

### Negative

- **Portkey lock-in**: Routing configs use Portkey's proprietary JSON format. Migration to another gateway would require rewriting these configs.
- **Config complexity**: Teams need to understand the routing config schema to create custom strategies.
- **Cost implications**: Fallback to direct API providers (Anthropic, OpenAI) may incur different pricing than Bedrock. Teams should be aware of cost differences between providers.

### Neutral

- **Observability**: Portkey logs which provider served each request, so fallback events are visible in existing OTel traces.
- **API key management**: All provider API keys are already provisioned in Secrets Manager. No new secrets are needed.
