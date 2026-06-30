---
title: Environment Variables
description: Container environment variables for the gateway, OTel collector, and optional features.
sidebar:
  order: 3
---

The AI Gateway ECS task runs two containers: the **gateway** ([agentgateway](https://github.com/agentgateway/agentgateway), a Rust proxy on a distroless base) and the **OTel collector** (AWS Distro for OpenTelemetry). Provider API keys are injected securely from AWS Secrets Manager. Everything else about routing, providers, guardrails, and access-log shaping lives in the YAML config that Terraform renders and passes to agentgateway inline via `-c` -- not through environment variables.

---

## Gateway Container

### Runtime Configuration

agentgateway takes its entire configuration **inline** from the `-c` argument (the rendered `agentgateway-config.yaml.tftpl`), so the container sets **no runtime environment variables** (`environment = []` in the task definition). The container listens on port `8787`, which matches the ALB target group health check; that port is fixed in the config, not set via an env var.

:::note[No `NODE_ENV` / `PORT`]
Earlier (Portkey OSS) releases ran a Node.js container that read `NODE_ENV` and `PORT` from the environment. agentgateway is a Rust binary configured entirely by its inline YAML, so those variables no longer exist.
:::

### Provider API Keys (Secrets)

Provider API keys are injected from AWS Secrets Manager using the ECS secrets integration. The ECS agent fetches the secret value at task launch and exposes it as an environment variable inside the container.

| Variable | Secrets Manager Path | Description |
|---|---|---|
| `OPENAI_API_KEY` | `ai-gateway/openai-api-key` | OpenAI API key |
| `ANTHROPIC_API_KEY` | `ai-gateway/anthropic-api-key` | Anthropic API key |
| `GOOGLE_API_KEY` | `ai-gateway/google-api-key` | Google API key |
| `AZURE_API_KEY` | `ai-gateway/azure-api-key` | Azure OpenAI API key |

:::caution
Secret values are initialized to `REPLACE_ME` at deployment time. You must update each secret in the AWS Secrets Manager console or via the CLI before the gateway can route to that provider. Requests to a provider with a placeholder key will return `502 Bad Gateway`.
:::

:::note[Bedrock needs no key]
Bedrock is reached with the gateway's ECS task-role credentials (SigV4), not a static API key. The four secrets above cover the direct provider APIs (OpenAI, Anthropic, Google, Azure OpenAI). The agentgateway config references them via shell expansion, e.g. `key: ${ANTHROPIC_API_KEY}`.
:::

#### How secrets are injected

1. Terraform creates four `aws_secretsmanager_secret` resources under the `ai-gateway/` prefix, encrypted with a dedicated KMS key (`alias/ai-gateway-secrets`).
2. The ECS task definition references each secret by ARN in the `secrets` block (not `environment`).
3. At task launch, the ECS agent calls `secretsmanager:GetSecretValue` using the task execution role and injects the plaintext value as the named environment variable.
4. The agentgateway config references the variable via shell expansion (e.g. `${ANTHROPIC_API_KEY}`); the secret value never appears in the task definition or CloudWatch logs.

```bash
# Update a secret via CLI
aws secretsmanager put-secret-value \
  --secret-id ai-gateway/openai-api-key \
  --secret-string "sk-..."
```

:::tip
After updating a secret, force a new ECS deployment to pick up the change. Running tasks do not reload secrets automatically.
```bash
aws ecs update-service --cluster ai-gateway-prod \
  --service ai-gateway-gateway --force-new-deployment
```
:::

---

### Routing, Guardrails, and Budget Enforcement (Inline Config, No Env Vars)

None of routing, guardrails, prompt caching, or budget enforcement is configured through environment variables. They are all rendered into the inline YAML config (`agentgateway-config.yaml.tftpl`):

| Concern | Where it lives | Notes |
|---|---|---|
| Provider routing / failover | `ai.groups` priority groups | Bedrock primary, Anthropic-direct fallback by default. See [Routing Strategies](/ai-gateway/user-guide/routing-strategies/). |
| Model aliases | `policies.ai.modelAliases` | Maps requested model IDs onto backend models. |
| Prompt caching | `policies.ai.promptCaching` | Opt-in, Bedrock-only (injects `cachePoint` markers). Not a response cache. |
| Budget enforcement | `promptGuard.request` webhook | Points at the `budget_enforcement` Lambda Function URL (`budget_enforcement_webhook_url`). Renders only when set. |
| Content safety | `promptGuard` `bedrockGuardrails` | Inline Bedrock Guardrails (ApplyGuardrail), keyed by `bedrock_guardrail_id`. Renders only when set. |

:::note[No response cache, no content-scanner env var]
There is no `CACHE_STORE` / `REDIS_URL` -- the ElastiCache Redis response cache was removed; agentgateway uses provider-native prompt caching instead. There is no `PORTKEY_DEFAULT_CONFIG_*`, no `PORTKEY_CONFIG`, and no `content_scanner_*` variable -- the standalone content-scanner Lambda was removed in favor of inline Bedrock Guardrails.
:::

---

## OTel Collector Container

The OTel collector sidecar runs the AWS Distro for OpenTelemetry (`public.ecr.aws/aws-observability/aws-otel-collector:v0.47.0`).

### Variables

| Variable | Description |
|---|---|
| `AOT_CONFIG_CONTENT` | Full content of the OTel Collector configuration YAML (injected inline) |

The `AOT_CONFIG_CONTENT` variable contains the entire OTel configuration, which the collector reads at startup. The configuration is defined in `infrastructure/otel-config.yaml` and passed through Terraform.

### OTel Configuration Summary

The collector configuration uses the `${env:AWS_REGION}` placeholder, which is resolved from the ECS task metadata (set automatically by Fargate).

#### Receivers

| Receiver | Endpoint | Description |
|---|---|---|
| OTLP gRPC | `localhost:4317` | Accepts traces, metrics, and logs over gRPC |
| OTLP HTTP | `localhost:4318` | Accepts traces, metrics, and logs over HTTP |

#### Processors

| Processor | Description |
|---|---|
| `batch` | Batches telemetry data (timeout: 5s, batch size: 512) |
| `memory_limiter` | Caps collector memory usage at 100 MiB |
| `resource` | Sets `service.name` attribute to `ai-gateway` |
| `attributes/genai` | Maps provider-specific attributes to OpenTelemetry GenAI semantic conventions |

The `attributes/genai` processor enriches spans with the following mappings:

| Source Attribute | GenAI Semantic Convention |
|---|---|
| `provider` | `gen_ai.system` |
| `model` | `gen_ai.request.model` |
| `usage.prompt_tokens` | `gen_ai.usage.input_tokens` |
| `usage.completion_tokens` | `gen_ai.usage.output_tokens` |
| `usage.cached_tokens` | `gen_ai.usage.cached_tokens` |
| `finish_reason` | `gen_ai.response.finish_reason` |

#### Exporters

| Exporter | Destination | Description |
|---|---|---|
| `awsxray` | AWS X-Ray | Distributed traces |
| `awsemf` | CloudWatch Metrics (Embedded Metric Format) | Custom metrics under the `AIGateway` namespace |
| `awscloudwatchlogs` | CloudWatch Logs (`/ecs/ai-gateway/otel-logs`) | Collector logs |

#### CloudWatch Metrics Published

The EMF exporter publishes the following metrics to the `AIGateway` namespace:

| Metric | Dimensions | Description |
|---|---|---|
| `PromptTokens` | Provider, Model | Input tokens per request |
| `CompletionTokens` | Provider, Model | Output tokens per request |
| `CachedTokens` | Provider, Model | Cached tokens per request |
| `TokensUsed` | Provider, Model | Total tokens per request |
| `EstimatedCostUsd` | Provider, Model, Team | Estimated cost in USD |
| `RequestCount` | Provider, Model, StatusClass | Request count by status class |
| `ResponseTime` | Provider, Model | End-to-end response latency |
| `TimeToFirstToken` | Provider, Model | Time to first token (streaming) |
| `CacheHits` | Provider | Cache hit count |
| `CacheMisses` | Provider | Cache miss count |
| `CacheCostSavingsUsd` | Provider | Cost savings from cache hits |

:::note[Cache metrics now mean prompt caching]
These cache metrics are still declared in `otel-config.yaml`, but they now track provider-native **prompt caching** (agentgateway's `promptCaching` to Bedrock `cachePoint`), which cuts input-token cost on prefix reuse. They are not a response cache -- the ElastiCache Redis response cache was removed in the agentgateway migration. agentgateway emits `cached_input_tokens` and `cache_creation_input_tokens` on the access log; the OTel pipeline maps these into the cache metrics and `gen_ai.usage.cached_tokens`.
:::

#### Pipelines

| Pipeline | Receivers | Processors | Exporters |
|---|---|---|---|
| `traces` | OTLP | memory_limiter, resource, attributes/genai, batch | AWS X-Ray |
| `metrics` | OTLP | memory_limiter, resource, attributes/genai, batch | AWS EMF |
| `logs` | OTLP | memory_limiter, resource, batch | CloudWatch Logs |

---

## Resource Allocation

The ECS task divides CPU and memory between the two containers:

| Container | CPU | Memory |
|---|---|---|
| Gateway | `gateway_cpu - 256` | `gateway_memory - 256` |
| OTel Collector | 256 units | 256 MiB |

With the default task size of 1024 CPU / 2048 MiB, the gateway gets 768 CPU units and 1792 MiB of memory.

---

## Logging

Both containers use the `awslogs` log driver:

| Container | Log Group | Stream Prefix |
|---|---|---|
| Gateway | `/ecs/ai-gateway/gateway` (from observability module) | `gateway` |
| OTel Collector | `/ecs/ai-gateway/otel` (from observability module) | `otel` |
