# [Feature] Bedrock Guardrails: detect-only (observe) mode + assessment in access log

**Repo:** github.com/agentgateway/agentgateway
**File at:** https://github.com/agentgateway/agentgateway/issues/new (no issue template exists; this is free-form)
**Labels to request:** enhancement, area/llm

---

## Summary

Add a `detectOnly` (observe) mode to the `bedrockGuardrails` prompt-guard policy: invoke the AWS `ApplyGuardrail` API inline and record what it found, but never block or mask the request/response. Today the Bedrock guardrail is strictly enforcing — a `BLOCKED` assessment becomes a rejection and an `ANONYMIZED` assessment mutates the content (`crates/agentgateway/src/llm/policy/mod.rs` `apply_bedrock_guardrails_request`/`_response`); there is no way to evaluate a guardrail against live traffic without affecting it.

## Motivation

Operators rolling out a guardrail need to measure its false-positive/false-negative behavior on real traffic before turning on enforcement. AWS supports this at the guardrail-resource level ("detect mode": filter `action: NONE` returns the assessment with `detected: true` and takes no action — see the AWS docs on harmful-content handling options). But agentgateway has no gateway-side equivalent, and more importantly it **discards the assessment on any non-blocking result** — only a `guardrail_checks` counter (phase + allow/mask/reject) survives, with no category/confidence detail and nothing in the access log. So even when the AWS resource is in detect mode, the rich assessment never reaches an operator's logs.

This matters for any deployment that wants an audit trail of guardrail decisions (e.g. shipping them to a data lake) rather than only a Prometheus counter.

## Proposal

Two parts:

1. **`detectOnly: bool` on `BedrockGuardrails`** (default `false`, so existing configs are unchanged). When `true`, the guardrail is invoked, the assessment is recorded, and the outcome is always pass-through (`GuardrailOutcome::None`) regardless of `is_blocked()`/`is_anonymized()`. This guarantees non-enforcement gateway-side even if the AWS resource is configured to `BLOCK`/`ANONYMIZE`.

2. **Expose the assessment.** As a first step, emit a structured `tracing` event (`target: "agentgateway::guardrail::detect"`) carrying the raw per-filter `assessments` and the action the guardrail *would* have taken — no prompt/completion text. As a follow-up, expose the assessment as a first-class CEL field (mirroring the existing `mcpGuardrails` dynamic-metadata field at `crates/agentgateway/src/cel/types.rs:73-74`) so it can be added to the access log via `frontendPolicies.accessLog.add`.

## Example

```yaml
policies:
  ai:
    promptGuard:
      request:
      - bedrockGuardrails:
          guardrailIdentifier: gr-abc123
          guardrailVersion: "1"
          region: us-west-2
          detectOnly: true   # evaluate + log, never block/mask
```

## Prior art / related

- #1607 confirms the Bedrock guardrail is enforce-only today (block/pass/mask).
- #1630 added mask support to the same code path (the closest analog).
- #570 (closed) added guardrail-trip metrics — establishes that maintainers want guardrail observability; this extends it from a counter to the actual assessment.
- #2141 (open) asks for governance/decision metadata in traces — the access-log half of this proposal advances that; cross-linking.
- #1609 (open, "AI Guardrail Backend") refactors guardrail config; a `detectOnly` flag should slot cleanly into whatever shape lands there.

## Scope

Small: one optional config field + a pass-through branch + a structured log, with tests. The CEL field is a follow-up. Happy to open the PR (have a working branch). Would a maintainer prefer this as one PR or split (behavior first, CEL field second)?
