# ADR-017: agentgateway as the data plane (spike)

- **Status:** Proposed (spike)
- **Date:** 2026-06-27
- **Supersedes:** none
- **Relates to:** ADR-001 (Portkey over LiteLLM), ADR-006 (dual API surface), ADR-009 (provider routing), ADR-010 (cost attribution), ADR-012 (response cache), ADR-014 (two-plane split), ADR-015 (mantle proxy), ADR-016 (control-plane foundation)

## Decision (lead)

Keep Portkey OSS as the data plane today. Treat agentgateway as a tracked alternative, not a migration in flight. Adopt it only when one of three triggers fires (see Recommendation). The control plane (the gwcore Lambdas, DynamoDB, Cognito, Firehose to Iceberg) stays unchanged under either engine; the swap is contained to the ECS container and four integration seams.

This is a paper + local-PoC spike. Nothing here changes production.

## Context and decision drivers

A prior parity study (recorded in the thread that spawned this ADR) found that agentgateway, a Rust data-plane proxy, is now a stronger LLM *engine* than Portkey OSS on translation fidelity, inline guardrail breadth, reasoning-token handling, and rerank/realtime surfaces, and that it is natively an MCP and A2A gateway. The engine choice in ADR-001 aged. The control-plane bet did not: everything agentgateway lacks (dollar budgets with a ledger, per-team/user/model cost attribution, chargeback, Cognito multi-tenancy, the Iceberg audit trail) is exactly what this repo built.

That raises a fair question: can agentgateway drop into the data-plane slot behind the existing control plane? This ADR answers seam by seam. The answer is yes mechanically, with one make-or-break integration cost (the webhook contract differs) and two real regressions (response cache, supply-chain apparatus).

Decision drivers, in priority order:

1. Keep the control plane intact. The Lambdas are the moat; a data-plane swap must not force a control-plane rewrite.
2. No silent regressions. The Redis response cache and the npm-based CVE apparatus are load-bearing today.
3. Net capability gain must be real, not theoretical. MCP/A2A governance and better translation are the prize; weigh them against migration cost.

## The replacement design

agentgateway replaces the Portkey container in the ECS task definition. It runs as a single Linux container (`cgr.dev/chainguard/glibc-dynamic` base, `ENTRYPOINT ["/app/agentgateway"]`, confirmed in `agentgateway/Dockerfile:95,106`) and reads a YAML config file watched for changes (`crates/agentgateway/src/state_manager.rs:114,123`).

**Stays identical:**

- ALB-native JWT validation (Cognito JWKS) in front of the data plane. agentgateway sits behind the ALB exactly as Portkey does.
- Secrets Manager to ECS env injection for OpenAI/Anthropic/Google/Azure keys.
- Bedrock via the ECS task IAM role (agentgateway does SigV4 with ambient credentials, `crates/agentgateway/src/http/auth/aws.rs`; `BackendAuth::Aws` default at `llm/mod.rs:357-365`).
- The entire control plane: budget_enforcement, content_scanner, cost_attribution, routing_config, team_registration, the rest of the gwcore Lambdas, DynamoDB, Cognito, Firehose to Iceberg.
- The OTel collector sidecar pattern (agentgateway emits OTel `gen_ai` metrics natively, so the sidecar still has something to scrape).

**Changes:**

- The data-plane container image and its build/scan pipeline (npm tree to Rust binary).
- The webhook contract the guardrail Lambdas speak (the make-or-break seam, detailed below).
- The access-log schema cost_attribution parses (field names and the identity source differ).
- Routing config delivery (base64 env JSON to a YAML config file or xDS), which weakens dynamic per-team routing.
- The Redis response cache is lost (agentgateway has no response cache).

## Seam-by-seam migration

| Seam | Portkey today | agentgateway equivalent | Effort | Risk |
|---|---|---|---|---|
| Guardrail webhooks (budget_enforcement + content_scanner) | `before_request_hooks`, Lambda returns `{verdict, data, error}` at HTTP 200; JWT carried in request body | `promptGuard.request[].webhook` posts `{body:{messages:[...]}}` to `/request`, expects `{action: pass\|mask\|reject}` (`llm/policy/webhook.rs:13-47`). Contracts differ. Needs an adapter Lambda or a handler rewrite. | High | High |
| cost_attribution access log | CloudWatch subscription parses Portkey JSON: `usage.{prompt_tokens,completion_tokens,total_tokens,cache_*}`, `req.headers["x-amzn-oidc-data"]` for identity (`cost_attribution/models.py:11-85`) | agentgateway CEL access log exposes `input_tokens/output_tokens/total_tokens/cached_input_tokens/cache_creation_input_tokens/provider/request_model` (`cel/types.rs:1203-1262`). Field names differ; identity must be added to the log via CEL. | Medium | High |
| routing_config delivery | base64 `PORTKEY_CONFIG`/`PORTKEY_DEFAULT_CONFIG_*` env, or per-request `x-portkey-config` header, managed by routing_config Lambda over DynamoDB | YAML config file (hot-reloaded) or xDS. Per-team dynamic routing without redeploy needs an xDS control source or a config-file rewrite + container reload. | Medium-High | Medium |
| Redis response cache | `CACHE_STORE=redis` + ElastiCache, exact-match (ADR-012) | None. agentgateway has provider cache-MARKER injection only (`llm/policy/mod.rs` PromptCachingConfig), not a response cache. | n/a (removal) | Medium |
| Provider secrets / Bedrock SigV4 | Secrets to env; Bedrock via task role | Same env model; Bedrock SigV4 via `BackendAuth::Aws` ambient creds. Carries over cleanly. | Low | Low |
| OTel | ADOT sidecar, X-Ray + EMF, gen_ai semconv | agentgateway emits OTel `gen_ai` metrics natively; sidecar pattern unchanged. | Low | Low |
| ALB JWT | ALB validates Cognito JWT, forwards `x-amzn-oidc-data` | Unchanged. agentgateway sits behind the ALB. | Low | Low |
| mantle lane (ADR-015) | Portkey `custom_host` to `bedrock-mantle.<region>.api.aws/openai/v1`, OpenAI Responses API; gpt-oss via Bedrock Converse | Custom provider with `hostOverride`/`pathOverride` + `ProviderFormat::Responses` (`llm/custom.rs:59-69`); gpt-oss via Bedrock provider. Feasible; static bearer needs the `Key` auth mode. | Medium | Medium |
| API surface | OpenAI chat completions, Anthropic messages, embeddings/images/audio passthrough | Chat completions, Anthropic messages, Responses, embeddings, rerank, count_tokens are first-class. Images/audio passthrough is NOT a typed route; needs verification or a passthrough route. | Medium | Medium |

## What improves

- **Translation fidelity.** Typed, compile-checked, snapshot-tested cross-API translation including streaming SSE to AWS-event-stream.
- **Inline guardrails.** Five backends (regex/PII local, OpenAI moderation, Bedrock Guardrails, Google Model Armor, Azure Content Safety), request and response side, in-proxy. ADR-011's "Bedrock guardrail does not auto-intercept" caveat goes away; it becomes an inline `promptGuard` policy (`examples/ai-prompt-guard/bedrock-config.yaml`).
- **Reasoning tokens.** First-class reasoning-token accounting and `reasoning_content` signature replay across turns. ai-gateway folds these into completion tokens today.
- **MCP and A2A governance.** Native `mcp/` and `a2a/` modules. This is the strategic gap the parity study flagged; agentgateway closes it without a from-scratch build.
- **Priority-group failover.** `ai.groups` with explicit priority levels gives ordered provider failover (`controller/.../llm-multi-priority.yaml`), which is closer to Portkey's ordered fallback than power-of-two load balancing alone.

## What regresses

- **Response cache.** agentgateway has no exact-match response cache. Repeated-prompt latency and the cost savings tracked by `get_cache_savings` (cost_attribution) are lost unless an external cache is added in front. This is a real cost regression, not a footnote. Quantify hit rate from current cache-savings metrics before deciding.
- **Supply-chain apparatus.** The mature, documented CVE pipeline (Trivy/Grype/OSV against Portkey's npm tree, the copy-then-patch Dockerfile, the SHA pin) is rebuilt for a Rust binary. The Rust attack surface is smaller, but the existing tooling, runbooks, and the OpenSSF posture do not transfer for free.
- **Provider breadth.** Portkey advertises 200+ providers; agentgateway types 8. Only five are provisioned today, so the practical exposure is low, but any future "just add provider X" assumption narrows.
- **Config dynamism.** Per-team routing changes that are a DynamoDB write + env refresh today become a config-file rewrite + reload, or require standing up an xDS source. This is an operational regression unless xDS is adopted.

## What breaks and needs a shim

The guardrail webhook contract. Portkey posts a rich body (the request payload plus metadata, with the JWT in the body) and reads `{verdict: bool, data, error}` at HTTP 200. agentgateway posts `{body:{messages:[{role,content}]}}` to a `/request` path and reads `{action: {pass|mask|reject}}` (`llm/policy/webhook.rs`). The existing Lambdas speak the Portkey shape. Two options, detailed in `spikes/agentgateway-data-plane/webhook-adapter.md`:

1. **Adapter Lambda (recommended for the PoC).** A thin translator in front of budget_enforcement and content_scanner that maps agentgateway's request to the Lambda's expected body and maps the Lambda's `{verdict}` back to `{action: pass}` or `{action: reject}`. Smallest change, keeps the proven Lambda logic untouched. Cost: the JWT must reach the adapter; agentgateway forwards request headers to the webhook (`webhook.rs:160-167`), so `x-amzn-oidc-data` rides along in a header, and the adapter moves it into the body the Lambda expects.
2. **Native handler mode.** Add an agentgateway-native response path to each Lambda (return `{action}` directly). Cleaner long-term, but edits the load-bearing enforcement code and doubles its contract surface.

The identity plumbing is the subtle part. Today budget_enforcement reads the JWT from the request *body* and cost_attribution reads it from `req.headers["x-amzn-oidc-data"]`. Under agentgateway, the guardrail webhook receives forwarded headers, so the JWT is header-borne at the hook; the adapter rehydrates the body field. For cost_attribution, the JWT must be written into agentgateway's access log via a CEL field, because the access log is now agentgateway's, not Portkey's.

## Phased migration path

- **Phase 0: local PoC (this spike).** Stand up agentgateway + a mock webhook via docker-compose, prove the adapter translates the contract both ways, prove chat-completions and messages both route, prove the access log carries the fields cost_attribution needs. No AWS, no risk. Artifacts in `spikes/agentgateway-data-plane/`.
- **Phase 1: shadow.** Deploy agentgateway as a second ECS service behind the same ALB on a shadow path. Mirror a copy of real traffic (ALB does not mirror natively; use a duplicating client or a test harness). Compare access logs, token counts, and guardrail verdicts against Portkey. No user traffic on agentgateway.
- **Phase 2: one non-prod team.** Cut a single sandbox-tier team to agentgateway via routing. Watch budget enforcement, cost attribution, and guardrails end to end against the live control plane.
- **Phase 3: cutover.** Shift the listener target from Portkey to agentgateway team by team, highest-tolerance first. Keep Portkey warm.
- **Rollback:** the ALB target group flips back to the Portkey service. Because the control plane is unchanged, rollback is a routing change, not a data migration. Keep the Portkey task definition and image registered through Phase 3.

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Webhook adapter mis-maps a deny, fails open silently | Medium | High | Contract tests both directions; assert `reject` maps from `verdict:false`; alarm on adapter error rate; keep content_scanner fail-open behavior explicit |
| cost_attribution silently stops attributing (field/identity mismatch) | Medium-High | High | Validate the agentgateway access-log schema against `cost_attribution/models.py` in Phase 0; add a canary that asserts non-zero attributed cost per team in Phase 1 |
| Response-cache loss raises latency and cost | Medium | Medium | Measure current cache hit rate and savings first; if material, add an external cache shim before cutover or stay on Portkey |
| Per-team dynamic routing needs redeploy | Medium | Medium | Adopt xDS as the config source, or accept config-file reload latency; decide in Phase 1 |
| CVE pipeline gap during transition | Medium | Medium | Build the Rust scan pipeline (cargo audit/deny, Trivy on the image) in Phase 0; do not cut over until it matches today's gate |
| mantle Responses lane regresses | Low-Medium | Medium | Prove the custom-provider Responses route in Phase 0 against a mantle stub |
| Images/audio passthrough unsupported | Low | Medium | Confirm whether any consumer uses these today; add a passthrough route if so |

## PoC scope and how to run it

Scope: prove the four hard seams locally, no AWS. Artifacts:

- `spikes/agentgateway-data-plane/config.yaml`: agentgateway config serving chat-completions + messages, Bedrock/OpenAI/Anthropic providers, the webhook guardrail wired to a mock, model aliases, priority-group failover, gen_ai telemetry.
- `spikes/agentgateway-data-plane/webhook-adapter.md`: the contract delta with side-by-side JSON and the adapter shim.
- `spikes/agentgateway-data-plane/docker-compose.yaml`: agentgateway + mock webhook.
- `spikes/agentgateway-data-plane/README.md`: run steps and what each proves.

Run: `cd spikes/agentgateway-data-plane && docker compose up`, then send a chat-completions request and observe the webhook is called and the access log carries token + identity fields. See the README.

## Recommendation

**Do not replace now. Replace conditionally, when a trigger fires.** Confidence: medium.

The mechanical swap is feasible and the control plane is safe, but the migration buys little today against three live triggers that would change the call:

1. **MCP/A2A governance becomes a committed roadmap item.** This is agentgateway's decisive edge and a from-scratch build on Portkey is expensive. If the org commits to agent-traffic governance, adopt agentgateway as the data plane in the same stroke.
2. **Reasoning-token accounting or response-side inline guardrails become a hard requirement** that Portkey cannot meet. Both are native in agentgateway.
3. **Portkey's hosted-control-plane gravity becomes a procurement problem** (post Palo Alto Networks acquisition). The OSS runtime is insulated today, but if that changes, agentgateway is the off-ramp.

Absent a trigger, the proven Portkey runtime plus the Redis cache plus the mature CVE pipeline outweigh a better engine. Run the Phase 0 PoC now so the option is shovel-ready, measure the cache hit rate so the regression is quantified, and revisit at the next roadmap checkpoint.
