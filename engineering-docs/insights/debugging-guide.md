# ai-gateway · Debugging guide

Something is broken. Where do you look first? This guide captures the operational knowledge for the ai-gateway control plane (Python 3.13 Lambdas behind API Gateway) and its data-plane edge (WAFv2 → ALB → agentgateway on ECS Fargate). It maps observable symptoms to the code path that produces them, names the single cheapest confirming check, catalogs where logs and errors surface, and orders the checks a debugger should run.

The plane is split two ways (`docs/src/content/docs/admin-guide/incident-response.md:9`): most inference incidents live in the data plane; auth, budget, attribution, and admin incidents live in the control plane and the cost-attribution Lambda. Errors are typed and mapped to HTTP through one contract — handlers raise a `ControlPlaneError` subclass (`src/gwcore/errors.py:13`) and `error_response` renders it (`src/gwcore/responses.py:74`).

## Failure-mode index

| Symptom | Likely surface | First check | Citation |
|---|---|---|---|
| HTML error page, HTTP 401, no JSON body | ALB rejected the Cognito JWT before it reached the gateway (missing/expired/bad-signature token); control-plane routes without the authorizer raise `UnauthorizedError` | Decode the JWT payload and check `exp` and that a `Bearer` token is present | `src/gwcore/auth.py:147` |
| HTTP 401 JSON `{"error":{"code":"unauthorized"}}` with `details.reason` | Full RS256 verification failed in `verify_token` (bad signature, wrong issuer, expired, unknown `kid`) | Read `details.reason` (the `PyJWTError` type) in the response envelope | `src/gwcore/auth.py:191` |
| HTTP 403 JSON `{"error":{"code":"forbidden"}}` | Scope/tier gate `require` rejected the principal (missing `invoke`/`admin` scope, wrong tenant tier) | Decode the JWT `scope` claim; compare to `details.required_scopes` | `src/gwcore/auth.py:241` |
| HTTP 403 HTML with `x-amzn-waf-action: BLOCK` | WAF managed rule or per-IP rate limit (2,000 req / 5-min window), upstream of the gateway | Look for the `x-amzn-waf-action` response header; check source IP volume | `docs/src/content/docs/reference/error-codes.md:99` |
| HTTP 429 with no provider response, request never reached upstream | `budget_enforcement` hard-limit block (utilization ≥ hard-limit pct) returned an agentgateway `reject` | `GET /usage?team=<team>` for `budget_utilization_pct`; look for the `BudgetDenied` EMF metric | `src/budget_enforcement/handler.py:297` |
| HTTP 429 for one model only, team still under budget | Model-level budget cap tripped in `_check_model_budget` | Compare per-model spend to the team's `model_limits` config | `src/budget_enforcement/handler.py:314` |
| HTTP 429 `{"allowed":false,"reason":"RPM limit exceeded ..."}` | `rate_limiter` RPM or daily-token counter exceeded the tier limit | Look for the `RateLimitDenied` metric (dimension `Check=rpm` or `daily_tokens`) | `src/rate_limiter/handler.py:173` |
| Budget/rate limits silently allow everything; no denials logged | Fail-open graceful degradation — DynamoDB unreachable, so the check returns `allowed=True` | Grep logs for `budget-check-degraded` / `rate-limit-degraded`; check `RateLimitDegraded` metric | `src/budget_enforcement/handler.py:227` |
| HTTP 502 on an inference request | Upstream provider unreachable/erroring, or the stored provider key is still `REPLACE_ME` / expired | Verify the provider key in Secrets Manager (`ai-gateway/<provider>-api-key`) is not the placeholder | `docs/src/content/docs/reference/error-codes.md:211` |
| HTTP 502 JSON `{"error":{"code":"upstream_error"}}` from a control-plane route | A control-plane dependency failed — DynamoDB, Cognito, Secrets Manager, or Athena raised, mapped to `UpstreamError` | Read `details` on the envelope; e.g. Cognito failure in team registration carries the client-error message | `src/gwcore/errors.py:79` |
| HTTP 503 HTML, no JSON | No healthy ECS tasks or gateway overloaded; ALB has nothing to route to | `aws ecs describe-services` running vs desired count; check target-group health | `docs/src/content/docs/reference/error-codes.md:248` |
| Per-team metrics/usage/audit show `unverified-<team>` buckets | `enable_jwt_auth = false` → `JWT_AUTH_ENFORCED != true`, so cost-attribution refuses to trust the spoofable header and prefixes the identity | Check `enable_jwt_auth` in the env tfvars; a wave of `unverified-*` teams is the tell | `src/cost_attribution/handler.py:122` |
| `GET /audit` returns 502 `upstream_error` | Athena audit query failed, timed out (~30s), or `AUDIT_ATHENA_WORKGROUP` is unset | Confirm the workgroup env var is set; read `details.state`/`reason` on the envelope | `src/budget_admin/audit_query.py:143` |
| Cost-attribution Lambda returns 400/500; no per-team metrics land | CloudWatch Logs subscription payload failed to decode (400), or `put_metric_data` failed (500) | Check the `CostAttributionError` metric by `Code` dimension (`decode_error` / `publish_error`) | `src/cost_attribution/handler.py:606` |
| `UnknownModelPrice` metric non-zero; cost looks wrong | A model has no pricing entry, so `cost_usd` was a default-price estimate, not a real rate | `GET /pricing/<provider>/<model>`; add an override via `pricing_admin` | `src/cost_attribution/handler.py:263` |
| HTTP 400 `{"error":{"code":"validation_failed","details":{"cursor":"invalid"}}}` on a list route | Malformed opaque pagination cursor passed to `parse_cursor` | Re-issue the request without the `cursor` param to get a fresh first page | `src/gwcore/responses.py:108` |
| Audit trail has gaps; mutations not recorded | Audit emission is best-effort — a Firehose failure is logged and swallowed, or no stream is configured so events are logged and dropped | Grep for `Failed to emit audit event`; confirm `AUDIT_FIREHOSE_STREAM` is set | `src/gwcore/audit.py:81` |

## Log and error surfaces

| Surface | Where it emits | What to grep for | Citation |
|---|---|---|---|
| Structured control-plane app logs | stdout via a `StreamHandler` with `JsonFormatter` (single-line JSON), captured to the Lambda's CloudWatch log stream | `correlation_id`, `level`, `logger`, and any `exc_info` stack | `src/gwcore/logging.py:18` |
| Correlation id (request join key) | Set from the API Gateway / Function URL `requestId`; bound onto every log line via `bind` | `correlation_id` field value pulled from the failing response | `src/gwcore/logging.py:51` |
| Error envelope (HTTP response body) | Response body built by `error_response`; shape `{"error":{"code","message","details"}}` | `error.code` (`unauthorized`, `forbidden`, `validation_failed`, `upstream_error`, …) and `error.details` | `src/gwcore/responses.py:74` |
| CloudWatch EMF metrics (control plane) | Printed to stdout as an EMF JSON line; CloudWatch materializes them under namespace `AIGateway/ControlPlane` | `BudgetDenied`, `RateLimitDenied`, `RateLimitDegraded`, `AuthzDenied`, `*Error` metric names | `src/gwcore/telemetry.py:50` |
| Billing metrics (cost attribution) | `cloudwatch.put_metric_data` under namespace `AIGateway` with Provider/Model/Team dimensions | `EstimatedCostUsd`, `TokensUsed`, `RequestCount`, `UnknownModelPrice` | `src/cost_attribution/handler.py:280` |
| Audit trail | Kinesis Firehose → Iceberg on S3 Tables; read path is Athena via `GET /audit` | `action`, `actor`, `decision` (`allow`/`deny`), `status`, `correlation_id` | `src/gwcore/audit.py:64` |
| Data-plane gateway access log | agentgateway container → CloudWatch `/ecs/ai-gateway/gateway` (structured JSON, KMS-encrypted) | `provider`, `model`, `res.statusCode`, `responseTime` | `docs/src/content/docs/admin-guide/monitoring.md:42` |
| Saved Logs Insights queries | Deployed as CloudWatch saved queries against the gateway log group; also run via `./scripts/cw-queries.sh` | `ai-gateway/error-rate-by-provider`, `latency-percentiles-by-provider` | `docs/src/content/docs/admin-guide/monitoring.md:97` |
| CloudWatch alarms → SNS | Defined in `infrastructure/modules/observability/alarms.tf`; publish to `alarm_topic_arns` | `high-error-rate`, `high-p99-latency`, `provider-down-*`, `budget-utilization` | `docs/src/content/docs/admin-guide/incident-response.md:49` |

## First-checks ladder

1. Read the HTTP status and body shape first. An HTML page with no JSON is an ALB/WAF-layer rejection (401/403/503); a JSON `{"error":{"code":...}}` body is a typed control-plane error whose `code` names the surface. `src/gwcore/responses.py:74`
2. Pull the `correlation_id` from the failing response or log line and grep it across the handler's CloudWatch log stream to reconstruct the single request. `src/gwcore/logging.py:51`
3. For any 401/403, decode the JWT payload and inspect `exp`, `scope`, and `custom:team`; an expired token (Cognito TTL is 1 hour) or a missing `invoke`/`admin` scope explains most auth denials. `src/gwcore/auth.py:89`
4. Check the EMF deny/error metrics before reading raw logs — `BudgetDenied`, `RateLimitDenied`, `AuthzDenied`, and the `*Error` counters (by `Code`/`Check` dimension) localize the failure to one gate cheaply. `src/gwcore/telemetry.py:50`
5. If limits appear to be silently not enforced, grep for the fail-open markers `budget-check-degraded` / `rate-limit-degraded` and the `RateLimitDegraded` metric — a DynamoDB outage makes both checks allow all traffic by design. `src/budget_enforcement/handler.py:227`
6. If per-team cost/usage/audit rows show `unverified-*` buckets, this is the JWT-not-enforced degradation path, not a data bug — confirm `enable_jwt_auth` in the environment tfvars rather than editing data. `src/cost_attribution/handler.py:122`
7. For a 502 on inference, verify the provider key in Secrets Manager (`ai-gateway/<provider>-api-key`) is populated and not still the `REPLACE_ME` placeholder. `docs/src/content/docs/reference/error-codes.md:211`
8. For a 503, check ECS service running-vs-desired count and ALB target-group health before assuming a code fault — it is usually capacity or a rolling deploy. `docs/src/content/docs/reference/error-codes.md:248`
9. To break a broad error spike down by provider and status code, run the saved query `ai-gateway/error-rate-by-provider` (or `./scripts/cw-queries.sh errors`). `docs/src/content/docs/admin-guide/monitoring.md:97`
10. If a CloudWatch alarm fired, map it to its first-response playbook in the incident-response runbook before making manual changes (note that `high-error-rate` also drives AppConfig auto-rollback). `docs/src/content/docs/admin-guide/incident-response.md:49`

## Known incident patterns

Source carries no `INCIDENT` / `POSTMORTEM` / `FLAKY` / `KNOWN BUG` code comments and no `INCIDENTS.md` pointer file. The patterns below are drawn from the deliberate safety behaviors in source and the known-issue references in the ops docs.

- **JWT auth not enforced (`unverified-*` attribution):** when `enable_jwt_auth = false`, the ALB does not validate the JWT and the forwarded `x-amzn-oidc-data` header is spoofable, so cost-attribution prefixes the resolved team/user with `unverified-`. Signal: `unverified-<team>` buckets in per-team metrics, usage rows, and audit records, which can make the `budget-utilization` alarm look wrong. Mitigation: enable ALB JWT validation (`enable_jwt_auth = true`); do not edit the Lambda or DynamoDB to suppress the prefix. `src/cost_attribution/handler.py:122`
- **Budget/rate-limit fail-open on a DynamoDB outage:** both in-path gates return `allowed=True` when DynamoDB is unreachable, so a data-store failure never blocks the inference path. Signal: `budget-check-degraded` / `rate-limit-degraded` log lines and the `RateLimitDegraded` metric, with spend/usage able to overrun limits during the window. Mitigation: restore DynamoDB reachability; treat overspend during the outage as a bounded, dated gap. `src/budget_enforcement/handler.py:227`
- **Audit records silently dropped:** audit emission is best-effort — a Firehose `put_record` failure is logged and swallowed, and if `AUDIT_FIREHOSE_STREAM` is unset the event is logged at INFO and dropped. Signal: gaps in the `GET /audit` trail and `Failed to emit audit event` log lines. Mitigation: confirm the Firehose stream is configured and its IAM/delivery health; the request itself is never failed by an audit error. `src/gwcore/audit.py:73`
- **`UnknownModelPrice` mis-billing:** a model with no pricing entry is costed with a default-price estimate rather than a real rate, and the `UnknownModelPrice` metric is emitted so it is not silent. Signal: non-zero `UnknownModelPrice` and cost figures that look off for a newly added model. Mitigation: add a pricing override via `pricing_admin` (`PUT /pricing/<provider>/<model>`). `src/cost_attribution/handler.py:263`
- **Provider key still `REPLACE_ME`:** secrets are created with a placeholder value, and requests to a provider whose key is still the placeholder return 502. Signal: 502 responses isolated to one provider immediately after a deploy or key rotation. Mitigation: populate the real key in Secrets Manager (`ai-gateway/<provider>-api-key`). `docs/src/content/docs/reference/error-codes.md:211`
- **Claude Code TTL bug #7660 / base-URL issue #26999:** `CLAUDE_CODE_API_KEY_HELPER_TTL_MS` must be a real shell environment variable (the settings.json `env` block is ignored), and `ANTHROPIC_BASE_URL` must be exported in the shell profile. Signal: recurring 401s from Claude Code clients despite a valid token helper. Mitigation: export both as real environment variables in the shell profile. `docs/src/content/docs/reference/error-codes.md:82`

## See also

- [architecture/module-map](../architecture/module-map.md) — 8 shared source citations
- [insights/impact-analysis](impact-analysis.md) — 8 shared source citations
- [insights/contract-map](contract-map.md) — 7 shared source citations
- [reference/public-api](../reference/public-api.md) — 7 shared source citations
- [behavior/processes](../behavior/processes.md) — 6 shared source citations
