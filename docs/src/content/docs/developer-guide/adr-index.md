---
title: Architecture Decision Records
description: Architecture Decision Records — accepted and proposed decisions.
sidebar:
  order: 4
---
## What Are ADRs

Architecture Decision Records (ADRs) capture significant technical decisions along with their context, alternatives considered, and consequences. They serve as a historical record so that future contributors understand _why_ a particular approach was chosen, not just _what_ was built.

ADRs are stored in the `adr/` directory at the repository root.

## Decision Log

| ADR | Title | Status | Summary |
|-----|-------|--------|---------|
| [001](../../adr/001-portkey-oss-over-litellm.md) | Portkey OSS as LLM Gateway Proxy | Accepted | Selected Portkey OSS over LiteLLM due to LiteLLM's 14 CVEs (including critical RCE and active SSRF exploitation), systemic memory leaks, and 800 MB+ image size. Portkey has zero known CVEs and a ~62 MB image. |
| [002](../../adr/002-python-slim-over-chainguard.md) | python:3.13-slim Over Chainguard | Accepted | Chose `python:3.13-slim` with multi-stage hardening over Chainguard because the free Chainguard tier locks to `latest` (Python 3.14), and the 3.13 tag requires a paid subscription. |
| [003](../../adr/003-single-nat-gw-with-vpc-endpoints.md) | Single NAT Gateway + VPC Endpoints | Accepted | Deployed one NAT Gateway instead of two, combined with VPC endpoints for ECR, CloudWatch, Secrets Manager, and S3. Saves approximately $32/month with acceptable HA trade-off for outbound internet traffic. |
| [004](../../adr/004-security-pipeline-composition.md) | 3-Phase Container Security Pipeline | Accepted | Structured the security pipeline into three phases: pre-build (hadolint + checkov), post-build (trivy + syft), and post-scan (cosign). Skipped grype (trivy covers it) and osv-scanner (`uv audit` provides native OSV scanning). |
| [005](../../adr/005-alb-jwt-validation-over-api-gateway.md) | ALB JWT Validation Over API Gateway | Accepted | Uses ALB-native `validate_token` action (launched Nov 2025) instead of API Gateway HTTP API for JWT authentication. Saves $260-2,400/month depending on request volume with zero additional latency. |
| [006](../../adr/006-portkey-dual-format-api.md) | Portkey Dual-Format API | Accepted | Verified that Portkey OSS natively serves both OpenAI Chat Completions (`/v1/chat/completions`) and Anthropic Messages (`/v1/messages`) on a single port. No custom middleware or translation layer needed. |
| [007](../../adr/007-terraform-provider-upgrade-for-jwt.md) | AWS Provider Upgrade to >= 6.22 | Accepted | Upgraded the Terraform AWS provider from `~> 5.0` to `~> 6.22` to enable the ALB JWT validation resource (`jwt_validation` block in `aws_lb_listener`). Zero-risk upgrade since infrastructure was deployed fresh on v6. |
| [008](../../adr/008-multi-tenant-client-isolation.md) | Multi-Tenant Client Isolation | Accepted | Per-team Cognito app clients via a `clients` Terraform module. Each team gets isolated credentials, scopes, and usage tracking. |
| [009](../../adr/009-provider-routing-strategy.md) | Provider Routing Strategy | Accepted | Portkey's native routing engine for provider-level fallback and load-balance strategies via `x-portkey-config` header or default environment variables. |
| [010](../../adr/010-cost-attribution-pipeline.md) | Cost Attribution Pipeline | Accepted | Lambda subscribes to gateway CloudWatch logs, extracts token usage, computes estimated cost, and publishes custom CloudWatch metrics per team. |
| [011](../../adr/011-bedrock-guardrails-integration.md) | Bedrock Guardrails Integration | Accepted | Terraform module for Amazon Bedrock Guardrails with configurable content filtering, PII blocking, topic denial, and word filtering policies. |
| [012](../../adr/012-response-cache-strategy.md) | Response Cache Strategy | Accepted | ElastiCache Redis cluster in VPC private subnets for exact-match response caching via Portkey Gateway. |
| [013](../../adr/013-identity-center-saml-federation.md) | Identity Center SAML/OIDC Federation | Proposed | SAML 2.0 and OIDC federation with the Cognito User Pool, plus a Pre-Token-Generation V2 Lambda for IdP group-to-claim mapping. |

## Creating a New ADR

### Naming Convention

ADR files follow the pattern:

```
adr/NNN-short-descriptive-title.md
```

Where `NNN` is a zero-padded sequential number (e.g., `008`).

### Template

Use this template for new ADRs:

```markdown
# ADR-NNN: Title

**Status**: Proposed | Accepted | Deprecated | Superseded by ADR-XXX
**Date**: YYYY-MM-DD
**Deciders**: AI Engineering NAMER

## Context

What is the issue that we're seeing that is motivating this decision or change?

## Decision

What is the change that we're proposing and/or doing?

## Alternatives Considered

| Criteria | Option A | Option B | Option C |
|----------|----------|----------|----------|
| ... | ... | ... | ... |

## Rationale

Why was this option chosen over the alternatives?

## Consequences

**Positive**: What becomes easier or possible as a result?

**Negative**: What becomes harder or is introduced as a trade-off?
```

### Process

1. Copy the template above into a new file: `adr/NNN-your-title.md`.
2. Set the status to `Proposed`.
3. Fill in context, decision, alternatives, rationale, and consequences.
4. Open a PR. Discussion happens in the PR review.
5. Once approved and merged, update the status to `Accepted`.

:::tip[When to write an ADR]
Write an ADR when a decision is significant enough that a future contributor would ask "why did we do it this way?" Common triggers: choosing between competing tools, making a cost/performance/security trade-off, adopting a new architectural pattern, or deviating from a common convention.
:::