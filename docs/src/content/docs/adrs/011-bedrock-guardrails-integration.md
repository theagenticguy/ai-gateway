---
title: "ADR-011: Bedrock Guardrails Integration"
description: Amazon Bedrock Guardrails for content filtering, PII blocking, topic denial, and word filtering.
sidebar:
  order: 11
---

**Status**: Accepted
**Date**: 2026-03-20
**Deciders**: AI Engineering NAMER

## Context

The AI Gateway currently has no content safety layer. Enterprise teams routing LLM traffic through the gateway need configurable content filtering to:

- Block harmful content (hate speech, violence, sexual content, insults, misconduct)
- Prevent PII leakage (SSNs, credit card numbers, phone numbers, email addresses)
- Enforce topic restrictions (e.g., block discussions of competitor products or internal financials)
- Filter specific words or phrases per organizational policy

Without a guardrail layer, individual teams must implement their own content moderation, leading to inconsistent enforcement and duplicated effort.

## Decision

Integrate Amazon Bedrock Guardrails as a Terraform module (`infrastructure/modules/guardrails/`) that creates and versions a `aws_bedrock_guardrail` resource with configurable policies for content filtering, PII blocking, topic denial, and word filtering.

The module is gated by `enable_guardrails` (default `false`) so existing deployments are unaffected. ECS task IAM roles are extended with `bedrock:ApplyGuardrail` and `bedrock:GetGuardrail` permissions.

## Alternatives Considered

### Custom Lambda content filter
A Lambda function invoked before/after model calls to scan for PII and harmful content. Rejected because it requires building and maintaining custom ML models or regex-based detection, with no parity to Bedrock's built-in classifiers.

### Portkey built-in guardrails
Portkey Cloud offers guardrail hooks, but these require Portkey Cloud (SaaS) rather than the self-hosted OSS gateway we deploy. Not available in our architecture.

### Third-party content moderation APIs
Services like Perspective API or OpenAI Moderation endpoint. Rejected because they add external dependencies, egress costs, and latency to a non-AWS service, conflicting with our AWS-native design principle.

## Consequences

**Positive**:
- AWS-native solution requiring no additional infrastructure beyond the guardrail resource itself
- Configurable per deployment environment (dev may use lower thresholds than prod)
- Immutable versioning via `aws_bedrock_guardrail_version` for audit and rollback
- PII detection uses Bedrock's built-in classifiers (no custom model training)
- Prompt attack detection included as a content filter category

**Negative**:
- Adds latency per request when guardrails are applied (Bedrock API call for each evaluation)
- Bedrock Guardrails pricing applies per text unit processed
- Content filter categories and PII types are limited to what Bedrock supports
- Guardrail must be explicitly applied by the calling application; it does not auto-intercept gateway traffic

## Sources

- [Amazon Bedrock Guardrails documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails.html)
- [Terraform aws_bedrock_guardrail resource](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/bedrock_guardrail)
