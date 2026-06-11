# ADR-006: Portkey OSS Natively Serves Both OpenAI and Anthropic API Formats

**Status**: Accepted (amended 2026-06-11 — see ADR-015)
**Date**: 2026-03-18
**Deciders**: AI Engineering NAMER

> **Amendment (2026-06-11):** `/v1/responses` is served by the gateway for the
> `openai` provider (not the `bedrock` provider). The OpenAI-on-Bedrock lane —
> Codex + GPT-5.5/5.4, which are Responses-API-only — therefore routes through
> the `openai` provider with a `custom_host` pointed at the Bedrock mantle
> endpoint, **proxied through the gateway, no fork**. See **ADR-015**. The
> "Codex CLI → `/v1/chat/completions`" row below is stale: Codex is now
> Responses-only and uses `/v1/responses`.

## Context

We need our gateway to serve two API formats simultaneously to cover all major AI coding agents:
- `/v1/chat/completions` (OpenAI format) — for OpenCode, Goose, Continue, LangChain, Codex CLI
- `/v1/messages` (Anthropic Messages format) — for Claude Code, Anthropic SDK

## Decision

Use Portkey OSS as-is — it serves both formats natively on a single instance, single port (8787).

## Verification

Confirmed from Portkey OSS source (`src/index.ts`):
- `POST /v1/messages` → `messagesHandler` (Anthropic Messages API)
- `POST /v1/chat/completions` → `chatCompletionsHandler` (OpenAI format)
- Plus: `/v1/embeddings`, `/v1/images/generations`, `/v1/audio/*`, `/v1/responses` (OpenAI Responses API)

Anthropic header forwarding confirmed:
- `anthropic-beta` and `anthropic-version` are extracted from incoming requests and forwarded to upstream Anthropic/Bedrock
- Both direct headers and `x-portkey-` prefixed variants are supported
- `X-Api-Key` is extracted and forwarded for Anthropic auth

## Consequences

**Positive**: No custom middleware needed. Single container, single port. Every major coding agent works out of the box. Claude Code uses `/v1/messages`, everything else uses `/v1/chat/completions`.

**Negative**: None. This is a pure validation ADR — Portkey does what we need.

## Agent Connection Matrix

| Agent | Endpoint | Config |
|---|---|---|
| Claude Code | `/v1/messages` | `ANTHROPIC_BASE_URL=<gateway>` |
| Claude Code (Bedrock) | `/v1/messages` | `ANTHROPIC_BEDROCK_BASE_URL=<gateway>` + `CLAUDE_CODE_SKIP_BEDROCK_AUTH=1` |
| OpenCode | `/v1/chat/completions` | `LOCAL_ENDPOINT=<gateway>/v1` |
| Goose | `/v1/chat/completions` | `OPENAI_HOST=<gateway>/v1` |
| Continue.dev | `/v1/chat/completions` | `apiBase: <gateway>/v1` in config.yaml |
| LangChain | `/v1/chat/completions` | `OPENAI_BASE_URL=<gateway>/v1` |
| Codex CLI | `/v1/responses` (Responses-only) | `base_url=<gateway>/v1`, `wire_api="responses"` — see ADR-015 |

## Sources

- Portkey OSS `src/index.ts` — route registrations (verified via repomix + DeepWiki)
- Portkey OSS `src/providers/anthropic/api.ts` — header forwarding
- Portkey OSS `src/handlers/handlerUtils.ts` — config extraction from headers
