---
title: "ADR-017: agentgateway as the data plane"
description: Replaced Portkey OSS with agentgateway as the data plane; control plane and identity layer unchanged, response cache removed in favor of provider-native prompt caching.
sidebar:
  order: 17
---

**Status**: Accepted
**Date**: 2026-06-27
**Deciders**: AI Engineering NAMER
**Supersedes**: [ADR-012](/ai-gateway/adrs/012-response-cache-strategy/) (response cache)
**Relates to**: [ADR-001](/ai-gateway/adrs/001-portkey-oss-over-litellm/), [ADR-006](/ai-gateway/adrs/006-portkey-dual-format-api/), [ADR-009](/ai-gateway/adrs/009-provider-routing-strategy/), [ADR-011](/ai-gateway/adrs/011-bedrock-guardrails-integration/), [ADR-014](/ai-gateway/adrs/014-two-plane-architecture-split/), [ADR-015](/ai-gateway/adrs/015-openai-responses-bedrock-mantle-proxy/), [ADR-016](/ai-gateway/adrs/016-control-plane-api-foundation/)

## Decision

Replace Portkey OSS with [agentgateway](https://github.com/agentgateway/agentgateway) as the data plane. The control plane (the gwcore Lambdas, DynamoDB, Cognito, Firehose to Iceberg) and the identity layer (Cognito M2M, ALB JWT, per-team clients, SSO) stay unchanged. The swap is contained to the ECS container, the rendered agentgateway config, and a small set of integration seams. The LLM response cache is removed; response and semantic caching are out of scope.

agentgateway is a Rust LLM/MCP/A2A proxy (Linux Foundation). Its `llm/` crate is a stronger data-plane engine than Portkey OSS on translation fidelity, inline guardrail breadth, reasoning-token handling, and rerank/realtime surfaces, and it is natively an MCP and A2A gateway. The engine choice in [ADR-001](/ai-gateway/adrs/001-portkey-oss-over-litellm/) aged; the control-plane bet did not.

## What changed

- **Data-plane container.** The ECS task runs the agentgateway image (Rust, distroless base, port 8787), pinned by digest in `versions.env` (`AGENTGATEWAY_REF` / `AGENTGATEWAY_IMAGE_DIGEST`) and re-tagged into ECR. The Node/npm Portkey build and its CVE-patch apparatus are gone.
- **Config.** agentgateway reads a YAML config delivered inline via `-c`, rendered from `infrastructure/modules/compute/agentgateway-config.yaml.tftpl`. Routing is an `ai.groups` priority-group failover (Bedrock primary, Anthropic fallback). The `x-portkey-*` header mechanism is gone.
- **Guardrails.** `budget_enforcement` is the one in-path Lambda, speaking agentgateway's `{action: pass | reject}` webhook contract. Content safety runs **inline** via agentgateway calling Bedrock Guardrails (ApplyGuardrail API), detect/log-only by default, configured by the `guardrails` module. The standalone `content_scanner` Lambda was removed.
- **Cost attribution.** `cost_attribution` parses agentgateway's flat access-log shape (synthesizing the nested `usage` block from flat token fields, reading the flat `oidc_data` field for identity).
- **Response cache removed.** The ElastiCache Redis exact-match response cache is decommissioned (supersedes [ADR-012](/ai-gateway/adrs/012-response-cache-strategy/)). The replacement is provider-native **prompt caching**: agentgateway's `promptCaching` policy injects Bedrock `cachePoint` markers on the Bedrock path. Prompt caching is opt-in, Bedrock-path only, and cuts input-token cost on prefix reuse — it is not a response cache (it still round-trips to the model and bills output tokens).

## Prompt caching is not a response cache

The removed Redis layer returned a cached completion for an identical request, saving latency and the full call. Prompt caching reuses prompt prefixes to discount input tokens on a cache hit, but still calls the model and still bills output. The latency/throughput win of the response cache is not relocated — it is dropped, by decision. Cache token accounting survives: agentgateway emits `cachedInputTokens` and `cacheCreationInputTokens`, which `cost_attribution` reads.

## What improves

- Typed, compile-checked, snapshot-tested cross-API translation, including streaming SSE to AWS event-stream.
- Inline request- and response-side guardrails (Bedrock Guardrails, plus regex/PII, OpenAI Moderation, Google Model Armor, Azure Content Safety).
- First-class reasoning-token accounting and `reasoning_content` signature replay.
- Native MCP and A2A governance — the strategic gap the parity study flagged.
- `ai.groups` priority-group provider failover.

## Consequences

- **v0.1 is agentgateway-only.** The dual-contract scaffolding kept during the migration spike was removed in the v0.1 cleanup: the Portkey `{verdict}` path, `to_portkey_config()`, the `content_scanner` Lambda, the Redis cache module, the Portkey routing-config presets, and the Portkey release scanner are all deleted. Rollback is no longer a routing flip.
- **Conditional routing is a known gap.** agentgateway has no request-field predicate routing, so Portkey `conditional` strategies collapse to ordered fallback. See [Routing Strategies](/ai-gateway/user-guide/routing-strategies/).
- **Provider breadth narrows on paper.** agentgateway types 8 providers (5 provisioned) versus Portkey's advertised 200+; practical exposure is unchanged.

This page summarizes the decision. The full ADR — including the original spike's seam-by-seam migration table, risk register, and phased path — lives at `adr/017-agentgateway-data-plane-spike.md` in the repository root.
