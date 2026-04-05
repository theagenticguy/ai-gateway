---
title: "ADR-001: Portkey OSS as LLM Gateway Proxy"
description: Selected Portkey OSS over LiteLLM due to zero CVEs, lightweight image, and MIT license.
sidebar:
  order: 1
---

**Status**: Accepted
**Date**: 2026-03-18
**Deciders**: AI Engineering NAMER

## Context

We need a multi-provider LLM API gateway that routes requests to OpenAI, Anthropic, AWS Bedrock, Google Vertex AI, and Azure OpenAI through a unified API. The gateway must be open source, lightweight, and deployable on ECS Fargate.

## Decision

Use **Portkey AI Gateway OSS** (v1.15.2) as the LLM proxy layer.

## Alternatives Considered

| Criteria | Portkey OSS | LiteLLM | Bifrost | Direct Bedrock |
|---|---|---|---|---|
| Stars | 10.9K | 39K | 2.8K | N/A |
| License | MIT | MIT + Enterprise | MIT | N/A |
| CVEs | 0 known | 14 (incl. RCE, SSRF) | 0 known | N/A |
| Memory leaks | None reported | Systemic, multiple issues | None reported | N/A |
| Multi-provider | 200+ models | 100+ models | 15+ providers | AWS only |
| Image size | ~62 MB | ~800 MB+ | ~20 MB | N/A |
| Language | TypeScript/Node.js | Python | Go | N/A |

## Rationale

LiteLLM was eliminated due to 14 CVEs (including critical RCE and active SSRF exploitation), systemic memory leaks requiring periodic restarts, brittle Prisma database migrations, and enterprise feature gating creep. Bifrost has impressive benchmarks but weak organic adoption. Direct Bedrock limits us to a single provider. Portkey has the strongest community (10.9K stars), proven production usage (claims 10B+ tokens/day), MIT license, Series A backing, and zero known CVEs.

## Consequences

**Positive**: Clean MIT license, zero CVEs, lightweight container (~62 MB), OpenAI-compatible unified API, active development, Gateway 2.0 merging enterprise features into OSS.

**Negative**: No `/v1/health` endpoint in OSS (use `GET /`), per-request config via headers (no server-side config file), TypeScript/Node.js runtime (team's primary language is Python), vendor relationship with Portkey AI for enterprise features.
