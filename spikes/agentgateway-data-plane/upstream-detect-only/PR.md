# PR: feat(llm): add detect-only mode to Bedrock Guardrails

**Open against:** github.com/agentgateway/agentgateway, base `main`
**Branch:** `feature/bedrock-guardrails-detect-only` (push our fork)
**No PR template exists in the repo** — this body follows the maintainer-preferred structure from PR #1946 (Description / Fix / How / Compatibility / Example).

---

## Description

Adds a `detectOnly` (observe) mode to the `bedrockGuardrails` prompt-guard policy. When enabled, the guardrail is invoked via `ApplyGuardrail` and its assessment is recorded, but the request and response are always passed through — never blocked or masked. This lets operators evaluate a guardrail against live traffic before enforcing, and surfaces the assessment (which the gateway currently discards on non-blocking results) for audit/logging.

## Fix

Fixes #<ISSUE> <!-- replace with the issue number filed from ISSUE.md -->

## How

- `BedrockGuardrails` gains `detect_only: bool` (`#[serde(default, rename = "detectOnly")]`, defaults false).
- `apply_bedrock_guardrails_request` / `apply_bedrock_guardrails_response` short-circuit to `GuardrailOutcome::None` when `detect_only` is set, after recording the assessment — so a `BLOCKED`/`ANONYMIZED` result no longer rejects or mutates content.
- `ApplyGuardrailResponse::would_action()` returns `BLOCKED` | `ANONYMIZED` | `NONE` (the action that *would* have been enforced).
- `ApplyGuardrailResponse::log_detect_only()` emits a structured `tracing::info!` on target `agentgateway::guardrail::detect` carrying the raw per-filter `assessments` and `would_action` — no prompt/completion text.
- Tests in `llm/policy/tests.rs` cover `would_action` across blocked / anonymized / none (incl. the AWS detect-mode shape: top-level `action: NONE` with per-filter `detected: true`).

3 files, +106 lines.

## Compatibility

Fully backward compatible. `detect_only` defaults to `false`, so every existing config keeps enforcing exactly as before. No change to the wire format of `ApplyGuardrail` requests, the metric labels, or any other guard kind.

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
          detectOnly: true
```

With `detectOnly: true`, a request that the guardrail would block instead passes through, and you get a log line like:

```
INFO agentgateway::guardrail::detect: bedrock guardrail detect-only evaluation
  guardrail_id=gr-abc123 guardrail_version=1 source=Input would_action=BLOCKED
  assessments=[{"contentPolicy":{"filters":[{"action":"NONE","confidence":"HIGH","detected":true,"type":"HATE"}]}}]
```

## Follow-up (noted, not in this PR)

Expose the assessment as a first-class CEL field (mirroring `mcpGuardrails` at `cel/types.rs:73-74`) so it can be added to the access log via `frontendPolicies.accessLog.add`, rather than only the `tracing` event. Happy to do that here or in a second PR — maintainer's preference.

## Checklist before pushing (must pass on a dev box — see VERIFICATION-NOTE.md)

- [ ] `make lint` (cargo fmt --check + clippy -D warnings) — rustfmt already verified clean
- [ ] `make generate-schema check-clean-repo` — **required**: the new `detectOnly` field changes `schema/config.json`; regenerate and commit it or CI fails
- [ ] `make test` (cargo test, insta snapshots)
- [ ] every commit `git commit -s` (DCO) — done on the branch
