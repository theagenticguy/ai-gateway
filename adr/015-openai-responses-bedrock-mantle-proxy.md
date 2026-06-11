# ADR-015: OpenAI Responses → Bedrock via the openai provider + custom_host (proxy, not translation)

**Status**: Accepted
**Date**: 2026-06-11
**Deciders**: AI Engineering NAMER

## Context

The OpenAI Codex client is **Responses-API-only** (`wire_api = "chat"` was removed upstream and is rejected at config parse). The flagship GPT-5.5 / GPT-5.4 models on Amazon Bedrock are **also Responses-only**, served at the OpenAI-compatible mantle endpoint:

```
https://bedrock-mantle.<region>.api.aws/openai/v1/responses
```

Portkey OSS's `bedrock` provider maps OpenAI Chat Completions → Bedrock **Converse** and has **no** `createModelResponse` (Responses) implementation. An early design read this as "the gateway cannot serve Codex/GPT-5.5 to Bedrock," and proposed either (a) forking Portkey to build a Responses→Converse translator or (b) routing Codex *around* the gateway directly to mantle. Both were wrong.

## Decision

Serve the OpenAI Responses lane **through the gateway, with stock Portkey and no fork**, by treating mantle as what it is — a native OpenAI-compatible upstream. Use Portkey's `openai` provider (which **does** implement `createModelResponse`) with a `custom_host` pointed at the mantle base:

```json
{ "provider": "openai",
  "custom_host": "https://bedrock-mantle.<region>.api.aws/openai/v1",
  "api_key": "<BEDROCK_API_KEY>" }
```

The gateway rewrites only the upstream host, preserves the `/responses` path verbatim, and re-issues `Authorization: Bearer <Bedrock API key>` (which mantle's OpenAI-compatible path accepts). `before_request_hooks`, logging, and cost attribution all run — so the flagship lane stays governed and inside the customer AWS boundary.

**Path is per model family** (verified live): GPT-5.5/5.4 use the mantle `/openai/v1` base; gpt-oss-120b/-20b use the `/v1` base (Chat Completions, served by the existing `bedrock` provider → Converse). The gateway sets `custom_host` per family.

## Verification

Confirmed live (Portkey OSS main @ `669825c`, local spike, 2026-06-11):
- A Codex-shaped Responses request through the gateway returns a valid completion from mantle for both `openai.gpt-oss-20b` (`/v1`) and `openai.gpt-5.5` (`/openai/v1`).
- SSE streaming relays faithfully; the terminal `response.completed` event carries `usage`.
- The `custom_host` is honored and the `/responses` path preserved (`src/handlers/services/providerContext.ts` `getFullURL`; `src/providers/openai/api.ts` `getEndpoint`).

## Consequences

**Positive**: No fork, no Converse-translation maintenance burden; the flagship lane keeps gateway hooks, logging, and isolation. Retires the bypass and fork options.

**Negative / caveats**:
- The gateway holds a Bedrock API key as a static bearer (Portkey OSS has no refreshing-bearer loop for the `openai` provider) — use a long-lived key or an external rotator.
- OSS does not parse `usage` on **streamed** responses; per-stream cost attribution needs a small `afterRequestHook` / stream tee (additive, not a fork).
- Because the lane uses the `openai` provider, the `custom_host` **must be pinned** and caller-supplied `custom_host` overrides rejected, or the gateway can route prompts to `api.openai.com` (the egress hole was demonstrated live). Egress to OpenAI SaaS is denied at the VPC as defense-in-depth.

## Supersedes / amends

Amends **ADR-006**: `/v1/responses` is served by the gateway for the `openai` provider, and the OpenAI-on-Bedrock lane uses that provider with a mantle `custom_host` — Responses traffic flows **through** the gateway, not around it. The earlier "bypass the gateway" and "fork a `bedrock-responses` provider" framings are retracted.

## Sources

- AWS — Get started with OpenAI GPT-5.5/GPT-5.4 and Codex on Amazon Bedrock; Bedrock mantle inference docs.
- Portkey OSS `src/handlers/services/providerContext.ts`, `src/providers/openai/api.ts`, `src/handlers/modelResponsesHandler.ts` (verified at commit `669825c`).
- Local spike `~/workplace/portkey-mantle-spike` (2026-06-11).
