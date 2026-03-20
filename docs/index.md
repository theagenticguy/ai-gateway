# AI Gateway

**Lightweight LLM inference gateway on AWS -- route any AI agent to any model provider through a single endpoint.**

---

## Overview

AI Gateway deploys [Portkey AI Gateway OSS](https://github.com/Portkey-ai/gateway) (v1.15.2) on ECS Fargate behind an Application Load Balancer, giving your AI coding agents a unified entry point to multiple model providers. It speaks both the **OpenAI Chat Completions** and **Anthropic Messages** API formats natively, so every major agent works without translation layers or custom adapters.

Authentication is handled by **Cognito M2M** (`client_credentials` grant) with **ALB-native JWT validation** -- no API Gateway required, no per-request cost added.

---

## Key Features

| Feature | Description |
|---|---|
| **Dual API format** | Serves `/v1/chat/completions` (OpenAI) and `/v1/messages` (Anthropic) natively |
| **Multi-provider routing** | Routes to Bedrock, OpenAI, Anthropic, Google, and Azure OpenAI via a single header |
| **Cognito M2M auth** | Machine-to-machine JWT authentication with ALB-native validation |
| **Zero per-request cost** | ALB JWT validation eliminates the need for API Gateway |
| **Auto-scaling** | ECS Fargate scales on CPU utilization and ALB request count |
| **Observability** | OpenTelemetry sidecar with CloudWatch logs, X-Ray traces, and operational dashboards |

---

## Compatible Agents

| Agent | API Format | Endpoint |
|---|---|---|
| Claude Code | Anthropic Messages | `/v1/messages` |
| OpenCode | OpenAI Chat Completions | `/v1/chat/completions` |
| Goose | OpenAI Chat Completions | `/v1/chat/completions` |
| Continue.dev | OpenAI Chat Completions | `/v1/chat/completions` |
| LangChain | OpenAI Chat Completions | `/v1/chat/completions` |
| Codex CLI | OpenAI Chat Completions | `/v1/chat/completions` |

---

## Quick Links

<div class="grid cards" markdown>

-   **Getting Started**

    ---

    Clone, install, deploy, and make your first request in under 5 minutes.

    [:octicons-arrow-right-24: Quick start](getting-started/index.md)

-   **User Guide**

    ---

    Configure your AI agent, learn the API, and troubleshoot common issues.

    [:octicons-arrow-right-24: User guide](user-guide/index.md)

-   **Admin Guide**

    ---

    Deploy, manage environments, configure security, and monitor the gateway.

    [:octicons-arrow-right-24: Admin guide](admin-guide/index.md)

-   **Developer Guide**

    ---

    Contribute to the project, understand the architecture, and run CI locally.

    [:octicons-arrow-right-24: Developer guide](developer-guide/index.md)

</div>
