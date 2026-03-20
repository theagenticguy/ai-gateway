# AI Gateway

[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/theagenticguy/ai-gateway/badge)](https://scorecard.dev/viewer/?uri=github.com/theagenticguy/ai-gateway)
[![CI/CD Pipeline](https://github.com/theagenticguy/ai-gateway/actions/workflows/ci.yml/badge.svg)](https://github.com/theagenticguy/ai-gateway/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/theagenticguy/ai-gateway/graph/badge.svg)](https://codecov.io/gh/theagenticguy/ai-gateway)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

## Overview

AI Gateway is a lightweight LLM inference gateway on AWS that routes AI agent requests through [Portkey AI Gateway OSS](https://github.com/Portkey-ai/gateway) to multiple model providers -- Bedrock, OpenAI, Anthropic, Google, and Azure OpenAI -- via a unified API. It serves both the OpenAI Chat Completions format (`/v1/chat/completions`) and the Anthropic Messages format (`/v1/messages`) natively, so every major coding agent works out of the box.

Authentication uses Cognito M2M (`client_credentials`) with ALB-native JWT validation, eliminating the need for API Gateway and its per-request costs.

## Architecture

The infrastructure follows a single-region, two-AZ deployment on AWS:

- **VPC** -- Two public subnets (ALB) and two private subnets (ECS tasks), with a single NAT Gateway for outbound internet and VPC endpoints for ECR, CloudWatch Logs, Secrets Manager, and S3.
- **ALB** -- Application Load Balancer in public subnets with TLS 1.3, WAF v2 (AWS Managed Rules + IP rate limiting), and native JWT validation.
- **Cognito** -- User Pool with M2M `client_credentials` grant, custom OAuth scopes, and JWKS endpoint for ALB signature verification.
- **ECS Fargate** -- Portkey gateway container (port 8787) with an AWS OpenTelemetry Collector sidecar. Autoscales on CPU utilization and ALB request count.
- **CloudWatch** -- Log groups for gateway and OTel collector, saved Logs Insights queries, and an operational dashboard (requests, errors, latency, top endpoints by provider).
- **Secrets Manager** -- Stores provider API keys (OpenAI, Anthropic, Google, Azure) injected into ECS tasks at runtime.

A detailed Mermaid architecture diagram is available in the `docs/` directory.

## Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| [mise](https://mise.jdx.dev/) | latest | Tool version manager (installs all other tools) |
| [uv](https://docs.astral.sh/uv/) | latest | Python package manager |
| [Terraform](https://www.terraform.io/) | >= 1.9 | Infrastructure as code (installed via mise) |
| [AWS CLI](https://aws.amazon.com/cli/) | v2 | AWS operations |
| [Docker](https://www.docker.com/) | latest | Container builds and local testing |

mise will install pinned versions of Python 3.13, Terraform 1.10.5, lefthook, checkov, trivy, hadolint, and gitleaks automatically from `mise.toml`.

## Quick Start

```bash
# Clone the repository
git clone git@github.com:theagenticguy/ai-gateway.git
cd ai-gateway

# Install all tool versions defined in mise.toml
mise install

# Install Python dependencies
uv sync

# Install git hooks
lefthook install

# Initialize Terraform
cd infrastructure
terraform init -backend-config=environments/dev.tfvars

# Preview infrastructure changes
terraform plan -var-file=environments/dev.tfvars
```

## Development

All project tasks are defined in `mise.toml` and run with `mise run <task>`:

| Task | Command | Description |
|------|---------|-------------|
| `install` | `mise run install` | Install Python dependencies and git hooks |
| `dev` | `mise run dev` | Run the API gateway locally with hot reload (port 8000) |
| `test` | `mise run test` | Run the test suite with pytest |
| `lint` | `mise run lint` | Run ruff linter and format check |
| `format` | `mise run format` | Auto-format Python (ruff) and Terraform (fmt) |
| `typecheck` | `mise run typecheck` | Run pyright type checker |
| `security` | `mise run security` | Run all security scans (SAST, secrets, IaC, Dockerfile) |
| `security:sast` | `mise run security:sast` | SAST scan with semgrep |
| `security:secrets` | `mise run security:secrets` | Secret detection with gitleaks |
| `security:iac` | `mise run security:iac` | IaC security scan with checkov |
| `security:dockerfile` | `mise run security:dockerfile` | Lint Dockerfiles with hadolint |
| `security:image` | `mise run security:image` | Scan container image with trivy |
| `security:fs` | `mise run security:fs` | Filesystem vulnerability scan with trivy |
| `tf:init` | `mise run tf:init` | Initialize Terraform |
| `tf:plan` | `mise run tf:plan` | Terraform plan (dry-run) |
| `tf:fmt` | `mise run tf:fmt` | Format Terraform files |
| `tf:validate` | `mise run tf:validate` | Validate Terraform configuration |
| `ci` | `mise run ci` | Full CI pipeline (lint, typecheck, test, security) |
| `ci:lint` | `mise run ci:lint` | Validate GitHub Actions workflows with actionlint |
| `ci:validate` | `mise run ci:validate` | Validate all CI + quality gates in one shot |

### Git Hooks

[Lefthook](https://github.com/evilmartians/lefthook) manages git hooks. All hooks run in parallel for speed.

**pre-commit** (runs on every commit):

| Check | Scope |
|-------|-------|
| ruff lint + auto-fix | `*.py` staged files |
| ruff format | `*.py` staged files |
| pyright | `src/` |
| gitleaks | staged changes |
| hadolint | `Dockerfile*` staged files |
| terraform fmt | `infrastructure/**/*.tf` |
| terraform validate | `infrastructure/**/*.tf` |

**pre-push** (runs before push):

| Check | Scope |
|-------|-------|
| pytest | `tests/` (fail-fast) |
| semgrep | Full repository |
| checkov | `infrastructure/**/*.tf` |
| trivy fs | Full repository |

**commit-msg** (validates commit message format):

Enforces [Conventional Commits](https://www.conventionalcommits.org/) format: `<type>(<scope>): <description>`. Supported types: feat, fix, docs, style, refactor, perf, test, build, ci, chore, revert.

## Security

The project implements a multi-layered security scanning pipeline across development, CI, and deployment.

| Layer | Tool | What It Covers |
|-------|------|----------------|
| SAST | [semgrep](https://semgrep.dev/) | Python code analysis (OWASP Top 10, security audit rules) |
| Secrets | [gitleaks](https://gitleaks.io/) | Prevents secrets from entering the repository |
| IaC | [checkov](https://www.checkov.io/) | Terraform security and compliance (2,500+ policies) |
| Dockerfile | [hadolint](https://github.com/hadolint/hadolint) | Dockerfile best practices with ShellCheck integration |
| Container | [trivy](https://trivy.dev/) | Vulnerability scanning of container images (HIGH + CRITICAL) |
| SBOM | [syft](https://github.com/anchore/syft) | CycloneDX software bill of materials generation |
| Signing | [cosign](https://github.com/sigstore/cosign) | Keyless image signing via Sigstore OIDC |
| Code analysis | [CodeQL](https://codeql.github.com/) | GitHub-native semantic code analysis (via SARIF upload) |
| Scorecard | [OpenSSF Scorecard](https://scorecard.dev/) | Supply chain security posture assessment |
| Dependency Review | [dependency-review-action](https://github.com/actions/dependency-review-action) | PR-time vulnerability and license check (denies GPL-3.0, AGPL-3.0) |
| Dependabot | [GitHub Dependabot](https://docs.github.com/en/code-security/dependabot) | Automated dependency updates for Python, Terraform, and GitHub Actions |
| TFLint | [tflint](https://github.com/terraform-linters/tflint) | Terraform linting with AWS ruleset |

In CI, scanning follows a 3-phase pipeline: **pre-build** (hadolint + checkov), **post-build** (trivy + syft), and **post-scan** (cosign signing). See [ADR-004](adr/004-security-pipeline-composition.md) for the full rationale.

## Infrastructure

All Terraform configuration lives in the `infrastructure/` directory.

| File | Purpose |
|------|---------|
| `providers.tf` | AWS provider configuration with default resource tags |
| `versions.tf` | Terraform and provider version constraints (AWS ~> 6.22) |
| `variables.tf` | Core input variables (region, environment, VPC CIDR, ECS sizing) |
| `variables_auth.tf` | Authentication variables (Cognito pool ID, JWT auth toggle) |
| `vpc.tf` | VPC with 2 AZs, public/private subnets, single NAT Gateway, VPC endpoints |
| `alb.tf` | Application Load Balancer, HTTPS/HTTP listeners, target group for gateway |
| `alb_auth.tf` | ALB JWT validation listener (Cognito-backed, conditionally enabled) |
| `cognito.tf` | Cognito User Pool, resource server (OAuth scopes), M2M client, domain |
| `ecs.tf` | ECS Fargate cluster, service, Portkey gateway + OTel sidecar containers, autoscaling |
| `ecr.tf` | ECR repository with immutable tags, scan-on-push, lifecycle policy |
| `iam.tf` | Task execution role (ECR + Secrets Manager) and task role (Bedrock + observability) |
| `secrets.tf` | Secrets Manager entries for provider API keys (OpenAI, Anthropic, Google, Azure) |
| `waf.tf` | WAFv2 Web ACL with AWS Managed Rules, IP reputation list, and per-IP rate limiting |
| `cloudwatch.tf` | Log groups for gateway and OTel collector |
| `dashboard.tf` | CloudWatch saved queries and operational dashboard |
| `outputs.tf` | ALB DNS, ECS cluster/service names, ECR URL, Cognito endpoints |
| `otel-config.yaml` | OpenTelemetry Collector configuration (traces to X-Ray, metrics to EMF, logs to CloudWatch) |

Environment-specific variable files are in `infrastructure/environments/` (`dev.tfvars`, `prod.tfvars`).

## Authentication Flow

The gateway uses Cognito machine-to-machine (M2M) authentication with ALB-native JWT validation:

1. **Token request** -- The client calls the Cognito `/oauth2/token` endpoint with `client_credentials` grant type, providing a client ID and secret.
2. **Token issuance** -- Cognito returns a signed JWT access token (1-hour TTL) with the `https://gateway.internal/invoke` scope.
3. **Request** -- The client sends requests to the ALB with `Authorization: Bearer <jwt>`.
4. **ALB validation** -- The ALB validates the JWT signature against Cognito's JWKS endpoint, checks `iss`, `exp`, `nbf`, `iat`, and the required `scope` claim. Invalid tokens receive a 401 response directly from the ALB.
5. **Forwarding** -- Valid requests are forwarded to the ECS Fargate target group running the Portkey gateway.

This approach adds zero additional cost and zero additional latency compared to API Gateway-based JWT validation. See [ADR-005](adr/005-alb-jwt-validation-over-api-gateway.md) and [ADR-007](adr/007-terraform-provider-upgrade-for-jwt.md) for details.

## ADRs

Architectural Decision Records are in the `adr/` directory.

| ADR | Title | Status |
|-----|-------|--------|
| [001](adr/001-portkey-oss-over-litellm.md) | Portkey OSS as LLM Gateway Proxy | Accepted |
| [002](adr/002-python-slim-over-chainguard.md) | python:3.13-slim Over Chainguard for Container Base Image | Accepted |
| [003](adr/003-single-nat-gw-with-vpc-endpoints.md) | Single NAT Gateway + VPC Endpoints for Cost Optimization | Accepted |
| [004](adr/004-security-pipeline-composition.md) | 3-Phase Container Security Pipeline | Accepted |
| [005](adr/005-alb-jwt-validation-over-api-gateway.md) | ALB JWT Validation Over API Gateway for Auth | Accepted |
| [006](adr/006-portkey-dual-format-api.md) | Portkey OSS Natively Serves Both OpenAI and Anthropic API Formats | Accepted |
| [007](adr/007-terraform-provider-upgrade-for-jwt.md) | Upgrade AWS Terraform Provider to >= 6.22 for ALB JWT Validation | Accepted |

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/get-gateway-token.sh` | Obtains a Cognito M2M access token via `client_credentials` grant. Requires `GATEWAY_CLIENT_ID`, `GATEWAY_CLIENT_SECRET`, and `GATEWAY_TOKEN_ENDPOINT` environment variables. Outputs the raw JWT to stdout for use as a Bearer token. |
| `scripts/cw-queries.sh` | Runs CloudWatch Logs Insights queries against the gateway log group. Supports individual queries (`requests`, `errors`, `latency`, `endpoints`) or `all`. Configurable via `LOG_GROUP`, `START_TIME`, and `END_TIME` environment variables. |

## Agent Compatibility

The gateway supports six AI coding agents across two API formats. See [docs/agent-setup.md](docs/agent-setup.md) for detailed configuration instructions.

| Agent | API Format | Endpoint |
|-------|-----------|----------|
| Claude Code | Anthropic Messages | `/v1/messages` |
| OpenCode | OpenAI Chat Completions | `/v1/chat/completions` |
| Goose | OpenAI Chat Completions | `/v1/chat/completions` |
| Continue.dev | OpenAI Chat Completions | `/v1/chat/completions` |
| LangChain | OpenAI Chat Completions | `/v1/chat/completions` |
| Codex CLI | OpenAI Chat Completions | `/v1/chat/completions` |

## Contributing

1. Fork the repository.
2. Create a feature branch from `main`.
3. Make your changes and ensure all quality gates pass (`mise run ci`).
4. Open a pull request against `main`.
5. All CI checks must pass before merge. The pipeline runs lint, IaC security scanning, container image scanning, and deploys to production on merge to `main`.

## License

This project is licensed under the [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0).
