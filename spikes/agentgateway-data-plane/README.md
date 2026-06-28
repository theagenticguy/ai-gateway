# Spike: agentgateway as the ai-gateway data plane

Phase 0 PoC for [ADR-017](../../adr/017-agentgateway-data-plane-spike.md). Proves locally that agentgateway can fill Portkey's slot behind the existing gwcore control plane, and exercises the one seam that does not line up: the guardrail webhook contract.

## What this proves

1. **agentgateway runs as a single container** and serves both `/v1/chat/completions` and `/v1/messages` on one port (the dual surface ADR-006 requires).
2. **The guardrail webhook fires** before the LLM call, the same hook point Portkey used for budget_enforcement and content_scanner.
3. **The adapter translates the contract both ways.** agentgateway sends `{body:{messages}}` and expects `{action: pass|mask|reject}`; the existing Lambdas speak `{verdict}`. The mock adapter shows the translation, including mapping a budget `verdict:false` to a 429 `reject`.
4. **Priority-group failover** mirrors Portkey's ordered fallback (Bedrock primary, Anthropic-direct fallback).

## What this does NOT prove

- Real LLM upstreams (dummy keys; the mock short-circuits at the webhook).
- The cost_attribution access-log schema end to end (the config shows the CEL re-keying needed; validating it against `cost_attribution/models.py` is a Phase 0 follow-up).
- The Redis response-cache regression (agentgateway has none; quantify the hit rate on Portkey before cutover).

## Files

| File | Purpose |
|---|---|
| `config.yaml` | agentgateway config, annotated with Portkey equivalents |
| `webhook-adapter.md` | the contract delta + adapter shim, side-by-side JSON |
| `mock_adapter.py` | runnable mock of the adapter (FastAPI, PEP 723) |
| `docker-compose.yaml` | agentgateway + mock adapter |
| `Dockerfile.mock-adapter` | builds the mock via uv |

## Run

```bash
cd spikes/agentgateway-data-plane

# Option A: full compose (needs the agentgateway image; see [VERIFY] notes)
docker compose up

# Option B: just the adapter contract test (no agentgateway image needed)
uv run mock_adapter.py &
# allow path:
curl -s localhost:8088/budget/request -H 'x-amzn-oidc-data: eyJhbGciOi.dummy.sig' \
  -d '{"body":{"messages":[{"role":"user","content":"hi"}]}}' | jq
# -> {"action":{"pass":{}}}

# deny path:
MOCK_BUDGET_VERDICT=false uv run mock_adapter.py &
curl -s localhost:8088/budget/request -H 'x-amzn-oidc-data: eyJ.dummy.sig' \
  -d '{"body":{"messages":[{"role":"user","content":"hi"}]}}' | jq
# -> {"action":{"reject":{"status_code":429,"body":"...retry_after_seconds...","reason":"Monthly budget exceeded"}}}
```

With the full stack up, point a client at `http://localhost:3000/v1/chat/completions`; agentgateway calls the adapter first, then (on pass) the upstream.

## [VERIFY] before trusting the config

The fields tagged `[VERIFY]` in `config.yaml` and `docker-compose.yaml` are inferred from agentgateway source + controller testdata. Check them against the pinned version's JSON schema (`agentgateway/schema/config.json`) and `agentgateway --help`:

- listener port override (8787 for the ECS drop-in)
- the webhook config field names (`host`/`port`/`path` vs `backendRef`)
- the env-var auth reference syntax for provider keys
- the access-log CEL field block for cost_attribution
- the published container image ref + tag, and the config flag (`-f`/`--file`)

## Next steps (from ADR-017)

- Validate the access-log schema against `cost_attribution/models.py`.
- Build the Rust scan pipeline (cargo audit/deny + Trivy on the image) to match today's CVE gate.
- Measure the Portkey cache hit rate so the response-cache regression is quantified.
