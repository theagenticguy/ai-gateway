# Webhook adapter: Portkey hook contract to agentgateway guardrail contract

This is the make-or-break seam. The existing `budget_enforcement` and
`content_scanner` Lambdas speak Portkey's `before_request_hooks` contract.
agentgateway speaks a different guardrail-webhook contract. They do not line up.
An adapter translates both directions so the proven Lambda logic stays untouched.

## The contract delta

| Aspect | Portkey (what the Lambdas expect) | agentgateway (what it sends/wants) |
|---|---|---|
| Path | One Function URL per hook | POST to `/request` (and `/response`) on the webhook backend (`webhook.rs:10-11,138`) |
| Request body | Rich: request payload + metadata; JWT in the body | `{"body": {"messages": [{"role","content"}]}}` (`GuardrailsPromptRequest`, `webhook.rs:13-18,52-57`) |
| Identity | budget_enforcement reads `jwt_token` from the body; cost_attribution reads `x-amzn-oidc-data` header | Forwards inbound request headers to the webhook (`webhook.rs:160-167`), so `x-amzn-oidc-data` arrives as a header |
| Response | `{"verdict": bool, "data": {...}, "error": "..."}` at HTTP 200 (a 4xx = hook failure, not deny) | `{"action": {"pass"\|"mask"\|"reject"}}` (`GuardrailsPromptResponse`, `webhook.rs:20-29,106-112`) |
| Deny semantics | `verdict:false` + optional `retry_after_seconds` in `data` | `{"action":{"reject":{"body":"...","status_code":429}}}` (`RejectAction`, `webhook.rs:93-103`) |
| Allow | `verdict:true` | `{"action":{"pass":{}}}` |
| Mask/redact | `data.transformedData.request.json.messages[].content` | `{"action":{"mask":{"body":{"messages":[...]}}}}` (`MaskAction`, `webhook.rs:81-91`) |

Two structural mismatches, not just renames:

1. **Allow/deny encoding.** Portkey uses a boolean `verdict`; agentgateway uses a tagged `action` object. The adapter maps `verdict:false` to `{action:{reject:{...}}}` and `verdict:true` to `{action:{pass:{}}}`.
2. **Identity location.** budget_enforcement reads the JWT from the request *body* (`jwt_token`). agentgateway gives the webhook the JWT as a forwarded *header* (`x-amzn-oidc-data`). The adapter lifts the header into the body field the Lambda expects.

## Adapter shim (recommended)

A thin translator (Lambda Function URL or a small Fastify/FastAPI service) sits between agentgateway and each existing Lambda. It does not touch the enforcement logic. Pseudocode:

```python
# POST /budget/request  (agentgateway -> adapter -> budget_enforcement)
def budget_request(agw_req, headers):
    jwt = headers["x-amzn-oidc-data"]            # agentgateway forwarded it
    messages = agw_req["body"]["messages"]
    model = infer_model(headers, messages)       # from x-model header or alias
    est_tokens = rough_token_estimate(messages)

    # Call the UNCHANGED budget_enforcement Lambda in its native shape
    lambda_resp = invoke_budget_enforcement({
        "jwt_token": jwt,
        "model": model,
        "estimated_tokens": est_tokens,
    })
    body = json.loads(lambda_resp["body"])        # {verdict, data, error}

    if body["verdict"]:
        return {"action": {"pass": {}}}
    retry = body.get("data", {}).get("retry_after_seconds")
    return {"action": {"reject": {
        "status_code": 429,
        "body": json.dumps({"error": body.get("error", "budget exceeded"),
                            "retry_after_seconds": retry}),
        "reason": body.get("error", "budget exceeded"),
    }}}
```

```python
# POST /scan/request  (agentgateway -> adapter -> content_scanner)
def scan_request(agw_req, headers):
    messages = agw_req["body"]["messages"]
    content = "\n".join(m["content"] for m in messages if isinstance(m["content"], str))
    team = team_from_jwt(headers["x-amzn-oidc-data"])

    lambda_resp = invoke_content_scanner({
        "content": content,
        "team_id": team,
        "model": headers.get("x-model", "unknown"),
        "request_id": headers.get("x-request-id", "adapter"),
    })
    body = json.loads(lambda_resp["body"])         # {verdict, data, error}

    if body["verdict"]:                            # allow (incl. detect-only)
        return {"action": {"pass": {}}}
    # content_scanner blocked. If it redacted, return mask; else reject.
    transformed = body.get("data", {}).get("transformedData")
    if transformed:
        masked = transformed["request"]["json"]["messages"]
        return {"action": {"mask": {"body": {"messages": masked}}}}
    return {"action": {"reject": {
        "status_code": 400,
        "body": json.dumps({"error": "content policy violation"}),
        "reason": "content_scanner blocked",
    }}}
```

## Side-by-side examples

### Budget deny

agentgateway POSTs to the adapter `/budget/request`:

```json
{ "body": { "messages": [ {"role": "user", "content": "summarize this"} ] } }
```
(plus forwarded header `x-amzn-oidc-data: eyJ...`)

Adapter calls `budget_enforcement` in its native shape, which returns (HTTP 200):

```json
{ "verdict": false,
  "data": { "retry_after_seconds": 172800,
            "budget_status": { "utilization_pct": 103.2 } },
  "error": "Monthly budget exceeded (103.2% of $1000)" }
```

Adapter returns to agentgateway:

```json
{ "action": { "reject": {
    "status_code": 429,
    "body": "{\"error\":\"Monthly budget exceeded (103.2% of $1000)\",\"retry_after_seconds\":172800}",
    "reason": "Monthly budget exceeded (103.2% of $1000)" } } }
```

agentgateway rejects the request with 429 before calling the LLM. Same user-visible outcome as Portkey today.

### Budget allow

`budget_enforcement` returns `{"verdict": true, "data": {...}}`; adapter returns `{"action": {"pass": {}}}`; request proceeds.

## Alternative: native handler mode

Add an agentgateway-native code path inside each Lambda that returns `{action}` directly, selected by a header or path. Cleaner long-term (no extra hop), but it edits the load-bearing enforcement code and doubles its contract surface. Prefer the adapter for the spike; revisit native mode only at cutover.

## Open verifications

- Confirm agentgateway's exact webhook config field names (`host`/`port`/`path` vs a `backendRef`) against the pinned schema.
- Confirm header forwarding includes `x-amzn-oidc-data` by default or needs `forward_header_matches` (`webhook.rs:160-167` has a TODO that header forwarding is becoming configurable).
- Confirm the `mask` body shape agentgateway expects matches `MaskActionBody::PromptMessages` exactly.
