# ai-gateway · Processes

This is the inventory of "what runs when" in the ai-gateway control plane. Each service under `src/<name>/` is an AWS Lambda whose `handler(event, _context)` is the process initiator; `gwcore/` is the shared library the handlers call into. The inference data plane itself is agentgateway (external Rust), referenced at `src/gwcore/agentgateway.py:14`; this repo's participation on the inference hot path is the `budget_enforcement` guardrail webhook, documented first below. `rate_limiter` is a pure library with no handler, so it appears as a step inside budget enforcement and again under `## Minor flows`.

## budget_enforcement (pre-request guardrail webhook)

Entry point: `src/budget_enforcement/handler.py:380`

1. Compute a correlation id and open a latency Timer for the request `src/budget_enforcement/handler.py:388`.
2. Parse the agentgateway request body (base64-decode when flagged) `src/budget_enforcement/handler.py:393`.
3. Build a `BudgetCheckRequest` from the agentgateway call — extract messages, read the `x-amzn-oidc-data` JWT and `x-model` header, estimate tokens `src/budget_enforcement/handler.py:413`.
4. Decode the JWT payload and extract team / user / cost-center / tier claims `src/budget_enforcement/handler.py:218`.
5. Fetch the team's DynamoDB budget record, falling back to a graceful allow on any store failure `src/budget_enforcement/handler.py:226`.
6. Resolve tier config (rate limits + budget defaults), overriding with budget-item values when present `src/budget_enforcement/handler.py:234`.
7. Run the RPM + daily-token rate-limit check; deny with 429 if exceeded `src/budget_enforcement/handler.py:261`.
8. Fetch current spend, compute utilization, then apply hard-limit, per-model, and warning-threshold checks and map the decision onto the agentgateway pass/reject envelope `src/budget_enforcement/handler.py:277`.

### Related
- `src/budget_enforcement/handler.py:207`
- `src/budget_enforcement/handler.py:432`
- `src/budget_enforcement/jwt_utils.py:17`
- `src/gwcore/agentgateway.py:29`
- `src/gwcore/agentgateway.py:95`
- `src/rate_limiter/handler.py:135`

## cost_attribution (usage aggregation pipeline)

Entry point: `src/cost_attribution/handler.py:598`

1. Gzip-decode the CloudWatch Logs subscription payload `src/cost_attribution/handler.py:605`.
2. For each log event, validate the record and derive a `MetricResult` — provider, tokens, cost, cache savings, and JWT-derived team/user `src/cost_attribution/handler.py:616`.
3. Publish per-`[Provider,Model]` and per-`Team` billing metrics to CloudWatch in ≤1000-datum batches `src/cost_attribution/handler.py:631`.
4. Accumulate team-, user-, and model-level usage in DynamoDB via atomic ADD updates `src/cost_attribution/handler.py:641`.
5. Check each team's budget thresholds and publish newly crossed thresholds as SNS budget alerts, then persist `alerts_sent` `src/cost_attribution/handler.py:647`.
6. Publish per-request audit records to the Kinesis Firehose usage stream in ≤500-record batches `src/cost_attribution/handler.py:655`.
7. Return a `HandlerResponse` counting total / processed / skipped / errored events `src/cost_attribution/handler.py:659`.

### Related
- `src/cost_attribution/handler.py:134`
- `src/cost_attribution/handler.py:288`
- `src/cost_attribution/handler.py:491`
- `src/cost_attribution/pricing.py:265`
- `src/cost_attribution/pricing.py:271`
- `src/gwcore/telemetry.py:19`

## pre_token (Cognito claim mapping)

Entry point: `src/pre_token/handler.py:69`

1. Validate the incoming Cognito Pre-Token-Generation V2 trigger event `src/pre_token/handler.py:77`.
2. Extract the user's group memberships from `groupConfiguration.groupsToOverride`, falling back to SAML-mapped `cognito:groups` `src/pre_token/handler.py:87`.
3. Load and validate the `GROUP_MAPPING` env var; return the event unchanged if no mapping is configured `src/pre_token/handler.py:103`.
4. Resolve the first matching group's claims; return unchanged if no group matches `src/pre_token/handler.py:109`.
5. Build the `claimsToAddOrOverride` list (team / org_unit / cost_center / tenant_tier) `src/pre_token/handler.py:123`.
6. Inject the overrides into both the ID token and the access token generation, then return the augmented event `src/pre_token/handler.py:126`.

### Related
- `src/pre_token/handler.py:30`
- `src/pre_token/handler.py:48`
- `src/pre_token/handler.py:59`
- `src/pre_token/models.py:1`
- `src/gwcore/telemetry.py:19`

## admin_token (gateway token exchange)

Entry point: `src/admin_token/handler.py:109`

1. Build the caller principal — fully verify the Cognito access token against JWKS when configured, else read authorizer-verified claims `src/admin_token/handler.py:115`.
2. Validate the `TokenExchangeRequest` body (audience + requested TTL) `src/admin_token/handler.py:119`.
3. Guard that the principal carries a team claim; a teamless caller cannot be issued a team-scoped token `src/admin_token/handler.py:124`.
4. Mint a short-lived HS256 gateway JWT with the caller's team/cost-center/tier claims and the invoke scope, clamping the TTL to the 12h ceiling `src/admin_token/handler.py:128`.
5. Emit a `token.exchange` audit event and a `TokenExchange` metric `src/admin_token/handler.py:130`.
6. Return the minted token in a `TokenExchangeResponse` envelope `src/admin_token/handler.py:142`.

### Related
- `src/admin_token/handler.py:72`
- `src/admin_token/handler.py:90`
- `src/admin_token/handler.py:54`
- `src/gwcore/auth.py:164`
- `src/gwcore/audit.py:88`

## team_registration (self-service onboarding)

Entry point: `src/team_registration/handler.py:67`

1. Extract HTTP method and path; short-circuit `GET /health` `src/team_registration/handler.py:70`.
2. Build the principal and require the admin scope (in-handler authorization) `src/team_registration/handler.py:79`.
3. Dispatch method + path to the matching route function `src/team_registration/handler.py:52`.
4. On `POST /teams`: validate the request, reject a duplicate team name, then create a Cognito app client with the client-credentials/invoke grant `src/team_registration/routes.py:110`.
5. Persist team metadata to the teams table and seed a tier-default budget record in the budgets table `src/team_registration/routes.py:133`.
6. Emit a `team.create` audit event and return the credentials + setup instructions `src/team_registration/routes.py:164`.

### Related
- `src/team_registration/routes.py:196`
- `src/team_registration/routes.py:223`
- `src/team_registration/routes.py:282`
- `src/team_registration/routes.py:338`
- `src/gwcore/auth.py:227`
- `src/gwcore/audit.py:64`

## budget_admin (budget CRUD + audit read)

Entry point: `src/budget_admin/handler.py:164`

1. Initialize the DynamoDB resource + table names at cold start `src/budget_admin/handler.py:45`.
2. Extract method and path; short-circuit `GET /health` `src/budget_admin/handler.py:168`.
3. Build the principal and require the admin scope `src/budget_admin/handler.py:177`.
4. Match method + path against the budget / usage / audit route patterns and dispatch `src/budget_admin/handler.py:85`.
5. For budget mutations, validate the body and write to the budgets table with a conditional expression, emitting a `budget.*` audit event `src/budget_admin/routes.py:141`.
6. For `GET /audit`, enforce tenant isolation, then run the parameterized Athena query over the Iceberg audit table and return a paginated result `src/budget_admin/handler.py:124`.

### Related
- `src/budget_admin/routes.py:79`
- `src/budget_admin/routes.py:187`
- `src/budget_admin/routes.py:247`
- `src/budget_admin/audit_query.py:219`
- `src/gwcore/responses.py:133`

## usage_api (usage self-service read)

Entry point: `src/usage_api/handler.py:205`

1. Extract method and path; short-circuit `GET /health` `src/usage_api/handler.py:216`.
2. Build the principal and require the invoke scope `src/usage_api/handler.py:223`.
3. Read the required `team` query parameter, rejecting a missing value `src/usage_api/handler.py:226`.
4. Enforce tenant isolation — a non-admin may read only their own team's usage `src/usage_api/handler.py:236`.
5. Parse the optional `history` and `models` parameters `src/usage_api/handler.py:242`.
6. Fetch current-period usage, budget utilization, trailing history, and per-model breakdown, then return the `UsageResponse` `src/usage_api/handler.py:149`.

### Related
- `src/usage_api/handler.py:39`
- `src/usage_api/handler.py:53`
- `src/usage_api/handler.py:114`
- `src/gwcore/auth.py:131`
- `src/gwcore/responses.py:39`

## routing_config (provider routing config CRUD)

Entry point: `src/routing_config/handler.py:269`

1. Extract path and method; short-circuit `GET /health` `src/routing_config/handler.py:273`.
2. Build the principal and require the admin scope `src/routing_config/handler.py:280`.
3. Parse the optional config name from the path and dispatch to the matching CRUD route `src/routing_config/handler.py:282`.
4. On create/update: validate the body into a `RoutingConfig`, check for conflict/existence, and persist it as the rendered agentgateway backend JSON in DynamoDB `src/routing_config/handler.py:179`.
5. Surface any lossy agentgateway-render migration warnings via log + metric `src/routing_config/handler.py:115`.
6. Emit a `routing.*` audit event and return the rendered config with its warnings `src/routing_config/handler.py:206`.

### Related
- `src/routing_config/handler.py:149`
- `src/routing_config/handler.py:168`
- `src/routing_config/handler.py:252`
- `src/routing_config/handler.py:64`
- `src/gwcore/auth.py:227`

## Minor flows

- pricing_admin — entry at `src/pricing_admin/handler.py:245`. Admin-scoped CRUD over dynamic pricing overrides that take priority over the static `cost_attribution.pricing` table; upsert/delete emit `pricing.*` audit events.
- chargeback_report — entry at `src/chargeback_report/handler.py:212`. Step-Functions-invoked monthly job that queries the usage + budget tables, renders an HTML report, and uploads it to S3.
- rate_limiter — entry at `src/rate_limiter/handler.py:135`. Pure library (`check_rate_limit`) enforcing RPM + daily-token DynamoDB atomic counters; invoked as a step inside budget_enforcement, with graceful-degradation allow on store failure.

## See also

- [insights/contract-map](../insights/contract-map.md) — 11 shared source citations
- [architecture/module-map](../architecture/module-map.md) — 10 shared source citations
- [insights/impact-analysis](../insights/impact-analysis.md) — 10 shared source citations
- [insights/business-logic](../insights/business-logic.md) — 7 shared source citations
- [insights/debugging-guide](../insights/debugging-guide.md) — 6 shared source citations
