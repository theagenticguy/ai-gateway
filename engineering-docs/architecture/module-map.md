# ai-gateway · Module map

## gwcore

`gwcore` is the shared control-plane foundation every Lambda service imports: one auth/authorization path, a consistent response + pagination envelope, in-process caching, an append-only audit trail, structured logging, and CloudWatch EMF + OTEL telemetry (`src/gwcore/__init__.py:1`). Its public surface is re-exported from the package root — `Principal`, `authorize`, `build_principal`, `require`, the typed error hierarchy, and `error_response` / `ok` / `page` / `parse_cursor` / `request_body` (`src/gwcore/__init__.py:13`). Authentication offers two verification modes behind one `Principal`: a trusted-edge decode for the Cognito-authorizer path and full RS256 JWKS verification for the token-exchange path (`src/gwcore/auth.py:1`). All 11 services depend on it, and it performs no network or filesystem I/O at import time so adoption does not change Lambda cold-start behavior (`src/gwcore/__init__.py:7`).

- `src/gwcore/auth.py` (241 LOC)
- `src/gwcore/responses.py` (145 LOC)
- `src/gwcore/audit.py` (119 LOC)
- `src/gwcore/telemetry.py` (114 LOC)
- `src/gwcore/cache.py` (91 LOC)
- `src/gwcore/errors.py` (83 LOC)
- `src/gwcore/logging.py` (59 LOC)
- `src/gwcore/__init__.py` (40 LOC)

## cost_attribution

`cost_attribution` is a CloudWatch Logs subscription Lambda that parses gateway access logs, derives per-request cost and token metrics, publishes billing CloudWatch metrics with Provider/Model/Team dimensions, accumulates usage in DynamoDB, fires SNS budget alerts, and writes usage records to the audit Firehose (`src/cost_attribution/handler.py:1`). It is event-driven with no HTTP surface and no authorization (`src/cost_attribution/handler.py:6`). Cost is computed from a token pricing table for LLM providers and models, defined in `pricing.py` and cached for warm-Lambda reuse (`src/cost_attribution/pricing.py:1`). It is the largest module in the tree at 1198 LOC across 4 files (`src/cost_attribution/handler.py:665`).

- `src/cost_attribution/handler.py` (665 LOC)
- `src/cost_attribution/pricing.py` (301 LOC)
- `src/cost_attribution/models.py` (232 LOC)
- `src/cost_attribution/__init__.py` (0 LOC)

## budget_admin

`budget_admin` is the Budget Admin REST API Lambda, migrated onto gwcore, exposing list / get / create / update over `/budgets` against DynamoDB budget and usage tables (`src/budget_admin/handler.py:1`). Authorization is now enforced in-handler: every request builds a `Principal` and requires the admin scope, accepting both the canonical `https://gateway.internal/admin` and legacy `"admin"` strings (`src/budget_admin/handler.py:3`). Route implementations live in `routes.py`, which uses the gwcore envelope, raises typed `gwcore.errors`, paginates lists via cursors, and emits a `gwcore.audit` event on every mutation (`src/budget_admin/routes.py:1`). It also backs `GET /audit` through `audit_query.py`, which runs a parameterized Athena query against the S3 Tables Iceberg `control_plane.audit_events` table (`src/budget_admin/audit_query.py:1`).

- `src/budget_admin/routes.py` (311 LOC)
- `src/budget_admin/audit_query.py` (240 LOC)
- `src/budget_admin/handler.py` (205 LOC)
- `src/budget_admin/models.py` (127 LOC)
- `src/budget_admin/__init__.py` (1 LOC)

## chargeback_report

`chargeback_report` generates the monthly chargeback report, triggered by Step Functions on the 1st of each month rather than by an HTTP request (`src/chargeback_report/handler.py:1`). The handler queries DynamoDB usage and budget tables, renders an HTML report, and uploads it to S3, performing no request authorization since it is invoked by the state machine (`src/chargeback_report/handler.py:4`). HTML rendering is isolated in `report_template.py`, which produces printable output with inline CSS for email compatibility (`src/chargeback_report/report_template.py:1`). It was migrated onto gwcore for the lightest touch — structured JSON logging plus operational EMF metrics for the report-generation outcome (`src/chargeback_report/handler.py:6`).

- `src/chargeback_report/handler.py` (277 LOC)
- `src/chargeback_report/report_template.py` (261 LOC)
- `src/chargeback_report/models.py` (77 LOC)
- `src/chargeback_report/__init__.py` (0 LOC)

## budget_enforcement

`budget_enforcement` is a pre-request budget-enforcement Lambda exposed as a Function URL and called by agentgateway as a guardrail webhook before a request is forwarded to the upstream LLM (`src/budget_enforcement/handler.py:1`). It receives `{"body": {"messages": [...]}}`, always returns HTTP 200, and carries the allow/deny decision inside the `action` envelope because a 4xx would be treated as a hook failure rather than a deny (`src/budget_enforcement/handler.py:4`). It degrades gracefully — if DynamoDB is unreachable the request is allowed and a warning is logged, so a budget-check outage never blocks traffic (`src/budget_enforcement/handler.py:11`). JWT claims are read via `jwt_utils.py`, which base64-decodes the payload only because ALB already verified the signature (`src/budget_enforcement/jwt_utils.py:1`).

- `src/budget_enforcement/handler.py` (448 LOC)
- `src/budget_enforcement/jwt_utils.py` (73 LOC)
- `src/budget_enforcement/models.py` (68 LOC)
- `src/budget_enforcement/__init__.py` (0 LOC)

## team_registration

`team_registration` is the self-service team-onboarding API Lambda on gwcore, covering register / list / get / rotate-credentials / deactivate over `/teams` (`src/team_registration/handler.py:1`). Authorization is now enforced in-handler via gwcore: every request requires the admin scope, replacing the previously dead `team_registration/auth.py` and reliance on the API Gateway authorizer alone (`src/team_registration/handler.py:3`). Route implementations in `routes.py` encapsulate the Cognito and DynamoDB interaction for each route and emit a `gwcore.audit` event on the mutating register / rotate / deactivate paths since each creates or destroys a Cognito app client (`src/team_registration/routes.py:1`). The routes file is the largest at 370 LOC (`src/team_registration/routes.py:1`).

- `src/team_registration/routes.py` (370 LOC)
- `src/team_registration/handler.py` (105 LOC)
- `src/team_registration/models.py` (98 LOC)
- `src/team_registration/__init__.py` (0 LOC)

## routing_config

`routing_config` is the provider-routing-strategy admin Lambda, migrated onto gwcore, exposing list / get / create / update / delete over `/routing/configs` (`src/routing_config/handler.py:1`). Custom routing configs are stored in DynamoDB persisted as the rendered agentgateway AI-backend shape from ADR-017 (`src/routing_config/handler.py:3`). Authorization is enforced in-handler — every request requires the admin scope and the create / update / delete mutations emit audit events (`src/routing_config/handler.py:4`). Request and response shapes for the config resource live in `models.py` at 229 LOC (`src/routing_config/models.py:1`).

- `src/routing_config/handler.py` (317 LOC)
- `src/routing_config/models.py` (229 LOC)
- `src/routing_config/__init__.py` (0 LOC)

## pricing_admin

`pricing_admin` is the dynamic-pricing-override admin Lambda, migrated onto gwcore, where DynamoDB overrides take priority over the static `cost_attribution.pricing` table (`src/pricing_admin/handler.py:1`). It exposes list / get / upsert / delete over `/pricing` and `/pricing/{provider}/{model}` (`src/pricing_admin/handler.py:7`). Authorization is now enforced in-handler — every request requires the admin scope and the upsert / delete mutations emit audit events (`src/pricing_admin/handler.py:4`). The handler carries essentially all of the module's logic at 291 of its 321 LOC (`src/pricing_admin/handler.py:1`).

- `src/pricing_admin/handler.py` (291 LOC)
- `src/pricing_admin/models.py` (27 LOC)
- `src/pricing_admin/__init__.py` (3 LOC)

## usage_api

`usage_api` is the real-time usage self-service API Lambda on gwcore, giving read-only access to team usage in DynamoDB — current period, trailing history, per-model breakdown, and budget utilization (`src/usage_api/handler.py:1`). Tenant isolation is now enforced: a caller may read only their own team's usage — `principal.team` from the token must match the requested `team` — unless they hold the admin scope (`src/usage_api/handler.py:6`). This closes a prior gap where any authenticated caller could read any team's usage via the `team` query parameter (`src/usage_api/handler.py:9`). Response shapes for the usage views live in `models.py` (`src/usage_api/models.py:1`).

- `src/usage_api/handler.py` (272 LOC)
- `src/usage_api/models.py` (41 LOC)
- `src/usage_api/__init__.py` (3 LOC)

## rate_limiter

`rate_limiter` is a pure library module — no Lambda handler, no request event, no authorization — providing RPM and daily-token-limit checks via the `check_rate_limit` function backed by DynamoDB atomic counters (`src/rate_limiter/handler.py:1`). It is imported and called on the hot path by `budget_enforcement`, which owns the deny-audit, so this module emits only metrics and structured logs and never an audit event to avoid double-counting a denial (`src/rate_limiter/handler.py:4`). It uses the same usage table as budget enforcement with different PK prefixes (`src/rate_limiter/handler.py:11`). Like the enforcement webhook it degrades gracefully — a DynamoDB outage allows the request rather than blocking traffic (`src/rate_limiter/handler.py:13`).

- `src/rate_limiter/handler.py` (226 LOC)
- `src/rate_limiter/models.py` (18 LOC)
- `src/rate_limiter/__init__.py` (2 LOC)

## pre_token

`pre_token` is the Cognito Pre-Token-Generation V2 trigger Lambda, migrated onto gwcore for the lightest touch (`src/pre_token/handler.py:1`). It extracts IdP group memberships from the trigger event and maps them to custom gateway claims — team, org_unit, cost_center, tenant_tier — using a configurable `GROUP_MAPPING` environment variable (`src/pre_token/handler.py:4`). It is a Cognito trigger, not an HTTP guardrail webhook: it always returns the possibly-augmented Cognito event, performs no authorization, and touches no DynamoDB (`src/pre_token/handler.py:9`). It emits no audit events because it makes no allow/deny decision and a token refresh happens on every login (`src/pre_token/handler.py:9`).

- `src/pre_token/handler.py` (134 LOC)
- `src/pre_token/models.py` (101 LOC)
- `src/pre_token/__init__.py` (0 LOC)

## admin_token

`admin_token` backs `POST /auth/token`, exchanging a verified SSO session for a gateway token (`src/admin_token/handler.py:1`). The caller presents a Cognito access token already verified by the API Gateway authorizer; the handler re-reads the claims via gwcore and additionally verifies against the JWKS when configured, as defense in depth for the non-authorizer path (`src/admin_token/handler.py:4`). It then mints a short-lived, audience-bound gateway JWT (HS256 over a Secrets Manager signing secret) carrying the caller's team / cost_center / tier claims and an `invoke` scope, and emits an audit event on every exchange (`src/admin_token/handler.py:8`). The minted token is self-contained so the inference path can verify it locally (`src/admin_token/handler.py:14`).

- `src/admin_token/handler.py` (181 LOC)
- `src/admin_token/models.py` (39 LOC)
- `src/admin_token/__init__.py` (8 LOC)

## See also

- [insights/impact-analysis](../insights/impact-analysis.md) — 13 shared source citations
- [insights/contract-map](../insights/contract-map.md) — 12 shared source citations
- [behavior/processes](../behavior/processes.md) — 10 shared source citations
- [insights/debugging-guide](../insights/debugging-guide.md) — 8 shared source citations
- [reference/public-api](../reference/public-api.md) — 8 shared source citations
