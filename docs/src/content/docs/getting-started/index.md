---
title: Getting Started
description: Clone, install, deploy, and make your first request in under 5 minutes.
sidebar:
  order: 1
---
Get the AI Gateway running locally and deploy it to AWS in under 5 minutes.

---

## What You Will Build

A fully operational LLM inference gateway on AWS, built on the [agentgateway](https://github.com/agentgateway/agentgateway) Rust proxy, that:

- Accepts requests in both OpenAI and Anthropic API formats on a single port
- Routes to multiple model providers (Bedrock, OpenAI, Anthropic, Google, Azure OpenAI) using priority-group failover defined in the gateway config
- Authenticates callers via Cognito M2M JWT tokens
- Auto-scales on ECS Fargate behind an Application Load Balancer
- Collects traces, metrics, and logs via OpenTelemetry

---

## 5-Minute Quickstart

### 1. Clone and install tools

```bash
git clone git@github.com:theagenticguy/ai-gateway.git
cd ai-gateway

# Install all tool versions defined in mise.toml
# (Python 3.13, Terraform 1.10.5, lefthook, checkov, trivy, hadolint, gitleaks)
mise install
```

### 2. Install dependencies

```bash
# Install Python dependencies
uv sync

# Install git hooks (pre-commit, pre-push, commit-msg)
lefthook install
```

### 3. Initialize Terraform

```bash
cd infrastructure
terraform init -backend-config=environments/dev.tfvars
```

### 4. Preview infrastructure

```bash
terraform plan -var-file=environments/dev.tfvars
```

### 5. Deploy

```bash
terraform apply -var-file=environments/dev.tfvars
```

:::tip[All-in-one with mise]
You can also run `mise run install` to handle steps 2 in a single command, or `mise run tf:plan` for steps 3--4.
:::


---

## What Happens Next

After deployment, Terraform outputs the ALB DNS name and Cognito token endpoint. You will need these to:

1. **Get a token** -- Use `scripts/get-gateway-token.sh` to obtain a JWT from Cognito
2. **Configure your agent** -- Point your AI coding agent at the gateway URL
3. **Start routing requests** -- Send LLM requests through the gateway; the rendered gateway config selects the provider and applies priority-group failover

---

## Next Steps

- [Prerequisites](prerequisites.md) -- Detailed tool and AWS account requirements
- [Authentication](authentication.md) -- How Cognito M2M auth works and how to get tokens
- [Agent Setup](../user-guide/agent-setup.md) -- Configure Claude Code, OpenCode, Goose, and more