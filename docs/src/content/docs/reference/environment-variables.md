---
title: Environment Variables
description: Container environment variables for the gateway, OTel collector, and optional features.
sidebar:
  order: 3
---

The AI Gateway ECS task runs two containers: the **gateway** (Portkey) and the **OTel collector** (AWS Distro for OpenTelemetry). Each receives environment variables through the Terraform task definition. Provider API keys are injected securely from AWS Secrets Manager.

---

## Gateway Container

### Core Variables

These variables are always set on the gateway container:

| Variable | Value | Description |
|---|---|---|
| `NODE_ENV` | `production` | Node.js runtime environment |
| `PORT` | `8787` | Port the gateway listens on (must match ALB target group health check) |

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

#### How secrets are injected

1. Terraform creates four `aws_secretsmanager_secret` resources under the `ai-gateway/` prefix, encrypted with a dedicated KMS key (`alias/ai-gateway-secrets`).
2. The ECS task definition references each secret by ARN in the `secrets` block (not `environment`).
3. At task launch, the ECS agent calls `secretsmanager:GetSecretValue` using the task execution role and injects the plaintext value as the named environment variable.
4. The gateway process reads the variable at startup -- the secret value never appears in the task definition or CloudWatch logs.

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

### Cache Variables (Conditional)

These variables are injected only when `enable_cache = true` in the Terraform configuration:

| Variable | Example Value | Description |
|---|---|---|
| `CACHE_STORE` | `redis` | Tells Portkey to use Redis for response caching |
| `REDIS_URL` | `rediss://{endpoint}:6379` | TLS-encrypted Redis endpoint (ElastiCache Serverless) |

:::note
The `CACHE_TTL` is not set as an environment variable by the Terraform module. Portkey uses its built-in default TTL for cache entries. If you need to customize TTL, set `CACHE_TTL` (value in seconds) by adding it to the container environment in the compute module.
:::

---

### Routing Variables (Conditional)

When `enable_provider_fallback = true`, each entry in the `routing_configs` Terraform variable is injected as a separate environment variable:

| Variable Pattern | Description |
|---|---|
| `PORTKEY_DEFAULT_CONFIG_{NAME}` | Portkey-compatible routing JSON for the named config |

The `{NAME}` suffix is the uppercased key from the `routing_configs` map. For example, if you define:

```hcl
routing_configs = {
  anthropic = "{\"strategy\":{\"mode\":\"fallback\"},\"targets\":[...]}"
  openai    = "{\"strategy\":{\"mode\":\"loadbalance\"},\"targets\":[...]}"
}
```

The gateway container receives:

- `PORTKEY_DEFAULT_CONFIG_ANTHROPIC`
- `PORTKEY_DEFAULT_CONFIG_OPENAI`

---

### Portkey Config Variable (Conditional)

When guardrail webhook hooks are configured (budget enforcement and/or content scanner), the gateway receives:

| Variable | Description |
|---|---|
| `PORTKEY_CONFIG` | Base64-encoded JSON containing `before_request_hooks` for budget enforcement and content scanning |

This variable is built automatically by Terraform from the `budget_enforcement_webhook_url` and `content_scanner_webhook_url` module inputs. Each configured webhook is registered as a Portkey guardrail hook that runs before every request.

The decoded JSON has the following structure:

```json
{
  "before_request_hooks": [
    {
      "type": "guardrail",
      "id": "budget-enforcement",
      "deny": true,
      "checks": [{
        "id": "budget_check",
        "default.webhook": {
          "webhookURL": "https://..."
        }
      }]
    },
    {
      "type": "guardrail",
      "id": "content-scanner",
      "deny": true,
      "checks": [{
        "id": "content_scan",
        "default.webhook": {
          "webhookURL": "https://..."
        }
      }]
    }
  ]
}
```

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
