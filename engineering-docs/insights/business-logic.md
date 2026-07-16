# ai-gateway · Business logic

This file indexes the domain rules baked into the ai-gateway codebase: field/request **validations**, **invariants** (uniqueness, existence, immutability, atomicity), **calculations** (cost, utilization, retry windows), and **policy/gates** (authorization, tenant isolation, budget/rate enforcement).

**Scope.** Application-layer rules only, as expressed in `src/` (Python 3.13, Pydantic v2 models plus Lambda handler guard clauses). Rules are grouped by domain, using the `src/` module names as the bounded-context names. DynamoDB `ConditionExpression` checks (uniqueness / existence) are surfaced under Invariants because they shape application behavior (they map to 409/404 responses), even though the constraint executes DB-side. Out of scope: Terraform/infra config, CloudWatch alarm thresholds, IAM policy, HTML/CSS presentation, and the ALB/API-Gateway JWT signature verification that happens upstream of these handlers.

## Validations

| Rule | Domain | Citation | Failure mode |
| --- | --- | --- | --- |
| `team_name`: 2–64 chars, pattern `^[a-zA-Z0-9_-]+$` | team_registration | `src/team_registration/models.py:42` | Reject 400 (`ValidationFailedError`) via `src/team_registration/routes.py:113-115` |
| `contact_email` must be a valid email (`EmailStr`) | team_registration | `src/team_registration/models.py:43` | Reject 400 |
| `tier` must be one of free/standard/premium/enterprise (defaults to STANDARD) | team_registration | `src/team_registration/models.py:10-16,44` | Reject 400 |
| `description` ≤ 256 chars | team_registration | `src/team_registration/models.py:45` | Reject 400 |
| `budget_usd` > 0 and ≤ 10,000,000 | budget_admin | `src/budget_admin/models.py:49` | Reject 400 (`ValidationFailedError`) via `src/budget_admin/routes.py:145-147` |
| `scope_id`: 1–256 chars | budget_admin | `src/budget_admin/models.py:48` | Reject 400 |
| `token_limit` ≥ 0 (optional) | budget_admin | `src/budget_admin/models.py:50,63` | Reject 400 |
| `scope`/`period`/`tier` constrained to enum members | budget_admin | `src/budget_admin/models.py:12-34,47-52` | Reject 400 |
| `ModelLimit.max_cost_usd` ≥ 0 | budget_admin | `src/budget_admin/models.py:41` | Reject 400 |
| Update request with no non-null fields is rejected | budget_admin | `src/budget_admin/routes.py:195-197` | Reject 400 |
| `month` must match `^\d{4}-(0[1-9]|1[0-2])$` | chargeback_report | `src/chargeback_report/models.py:14-17` | Reject 400 (error count only, input never logged) via `src/chargeback_report/handler.py:224-232` |
| `input_per_1k`/`output_per_1k`/`cache_*_per_1k` ≥ 0 | pricing_admin | `src/pricing_admin/models.py:13-16` | Reject 400 (`ValidationFailedError`) via `src/pricing_admin/handler.py:200-203` |
| `PUT /pricing` body must be a JSON object; provider/model taken from path, not body | pricing_admin | `src/pricing_admin/handler.py:190-199` | Reject 400 |
| `TokenPrice` prices ≥ 0 (`input`/`output`/`cache_read`/`cache_write` per 1K) | cost_attribution | `src/cost_attribution/pricing.py:32-43` | Reject at model construction |
| `ttl_seconds`: 300–43200 requested lifetime | admin_token | `src/admin_token/models.py:26-28` | Reject 400 (`ValidationFailedError`) via `src/admin_token/handler.py:118-121` |
| `audience` constrained to claude/codex/generic | admin_token | `src/admin_token/models.py:10-15,25` | Reject 400 |
| `RoutingTarget.name`: 1–128 chars; `provider` ≥ 1 char | routing_config | `src/routing_config/models.py:26-27` | Reject 400 (`ValidationFailedError`) via `src/routing_config/handler.py:192-194` |
| `RoutingTarget.weight` in 0.0–1.0 | routing_config | `src/routing_config/models.py:32-37` | Reject 400 |
| `RoutingConfig.targets` requires ≥ 1 target | routing_config | `src/routing_config/models.py:81` | Reject 400 |
| `ConfigMetadata.description` ≤ 500 chars; `version` ≥ 1 | routing_config | `src/routing_config/models.py:70,74` | Reject 400 |
| `TierConfig.rpm` ≥ 0; `monthly_usd` ≥ 0 | budget_enforcement | `src/budget_enforcement/models.py:13,15` | Reject at model construction; invalid `TIER_DEFAULTS` env entries fall back to built-ins (`src/budget_enforcement/handler.py:76-79`) |
| `BudgetCheckRequest.estimated_tokens` ≥ 0 | budget_enforcement | `src/budget_enforcement/models.py:31` | Reject at model construction |
| `BudgetRecord.warn_threshold_pct` in 0–100; `hard_limit_pct` ≥ 0; `monthly_budget_usd` ≥ 0 | cost_attribution | `src/cost_attribution/models.py:189-200` | Reject at model construction |
| Usage token fields (`prompt`/`completion`/`total`/`cache_*`) ≥ 0 | cost_attribution | `src/cost_attribution/models.py:15-19` | Reject at model construction |
| Non-int / null usage token values coerced to `0` (before-validator) | cost_attribution | `src/cost_attribution/models.py:21-42` | Coerce (silent) |
| Malformed access-log record (bad JSON, non-dict, schema mismatch, or no token usage) is skipped | cost_attribution | `src/cost_attribution/handler.py:134-149` | Silent drop (record not billed) |
| Audit-query `start`/`end` must be valid ISO-8601 (trailing `Z` accepted) | budget_admin | `src/budget_admin/audit_query.py:86-100` | Reject 400 (`ValidationFailedError`) |

## Invariants

| Invariant | Where enforced | Citation |
| --- | --- | --- |
| Team names are unique — registration checks the `team-name-index` GSI before creating | Application code | `src/team_registration/routes.py:80-89,117-118` (→ `ConflictError` 409) |
| A budget row cannot be created twice for the same `budget_id` | DB constraint (`attribute_not_exists(budget_id)`) + app | `src/budget_admin/routes.py:170-174` (→ `ConflictError` 409) |
| Budget update/delete require the row to already exist | DB constraint (`attribute_exists(budget_id)`) + app | `src/budget_admin/routes.py:216-221,233-238` (→ `NotFoundError` 404) |
| A pricing override cannot be deleted unless it exists | DB constraint (`attribute_exists(PK)`) + app | `src/pricing_admin/handler.py:82-89` |
| A routing config cannot be deleted unless it exists; create rejects an existing name, update requires an existing name | DB constraint (`attribute_exists(config_name)`) + app | `src/routing_config/handler.py:86-91,186-189,227-229` |
| Target names must be unique within a routing config | Application code (Pydantic `model_validator`) | `src/routing_config/models.py:84-91` |
| Loadbalance target weights must sum to ~1.0 (tolerance 0.99–1.01) when all targets are weighted | Application code (`model_validator`) | `src/routing_config/models.py:93-103` |
| Conditional-routing `then`/`default` must reference targets that exist in the config | Application code (`model_validator`) | `src/routing_config/models.py:105-117` |
| `UsageMetrics.total_tokens` is derived from `prompt + completion` when it arrives as 0 | Application code (after-validator) | `src/cost_attribution/models.py:44-49` |
| Usage counters are updated with atomic DynamoDB `ADD` so concurrent Lambda invocations never lose counts | Application code + DB atomic op | `src/cost_attribution/handler.py:288-297,344-365` |
| RPM and daily-token counters use atomic `if_not_exists(...) + :inc` increments | Application code + DB atomic op | `src/rate_limiter/handler.py:44-79,82-118` |
| `alerts_sent` is kept as a sorted deduped set, so each budget threshold alerts at most once | Application code | `src/cost_attribution/handler.py:477` (union in `_process_team_alerts`); read at `src/cost_attribution/handler.py:505-517` |
| Config/pricing/usage records are immutable once built (`model_config = {"frozen": True}`) | Application code (Pydantic frozen models) | `src/cost_attribution/pricing.py:54`; `src/cost_attribution/models.py:175,214,232` |
| Credentials cannot be rotated for, and an already-inactive team cannot be re-deactivated | Application code | `src/team_registration/routes.py:288-289,344-345` (→ `ValidationFailedError` 400) |
| Audit trail is append-only and best-effort — a Firehose failure never fails the request | Application code | `src/gwcore/audit.py:64-85` |

## Calculations

| Calculation | Inputs | Output | Citation |
| --- | --- | --- | --- |
| Request cost | prompt tokens, completion tokens, per-1K prices | USD cost | `src/cost_attribution/pricing.py:265-268` |
| Cache savings | cache-read tokens, cache-creation tokens, prices | USD saved (≥ 0) | `src/cost_attribution/pricing.py:271-301` |
| Effective cache-read / cache-write price | `input_per_1k`, optional explicit cache rates | per-1K USD | `src/cost_attribution/pricing.py:56-68` |
| Budget utilization | current spend, monthly budget | percentage | `src/budget_enforcement/handler.py:282`; `src/usage_api/handler.py:167`; `src/team_registration/routes.py:270`; `src/chargeback_report/handler.py:143-145` |
| Month-over-month change | this-month total, previous-month total | percentage (or None) | `src/chargeback_report/models.py:60-67` |
| Request-time token estimate | flattened message text | tokens | `src/gwcore/agentgateway.py:26,58-61` |
| Seconds until budget-period reset | now (UTC) | retry-after seconds | `src/budget_enforcement/handler.py:149-155` |
| Seconds until next-minute / end-of-day reset | now (UTC) | retry-after seconds | `src/rate_limiter/handler.py:121-124,127-132` |
| RPM / daily-token counter TTLs | minute bucket / end-of-day | expiry epoch | `src/rate_limiter/handler.py:59,98` |
| Newly-crossed alert thresholds | utilization %, configured thresholds, already-sent | list of thresholds | `src/cost_attribution/handler.py:505-517` |
| Top model by cost for a team | batch of per-request metrics | model name | `src/cost_attribution/handler.py:397-405` |

**Request cost formula.** `cost = (prompt_tokens / 1000) × input_per_1k + (completion_tokens / 1000) × output_per_1k` (`src/cost_attribution/pricing.py:265-268`). The `(provider, model)` price is resolved from a static `PRICING_TABLE` merged with a DynamoDB overlay (DynamoDB wins), cached for 300s; an unknown pair falls back to `_DEFAULT_PRICE = 0.01/0.03` and raises the `UnknownModelPrice` signal rather than silently mis-billing (`src/cost_attribution/pricing.py:71-262`).

**Cache savings formula.** `savings = read_savings − write_overhead`, clamped to ≥ 0, where `read_savings = (cache_read_tokens / 1000) × (input_per_1k − effective_cache_read_per_1k)` and `write_overhead = (cache_creation_tokens / 1000) × (effective_cache_write_per_1k − input_per_1k)` (`src/cost_attribution/pricing.py:295-301`). When `cache_supported` is False (e.g. `gpt-oss` on Bedrock), savings are forced to 0 rather than defaulting cache-read to 10% of input (`src/cost_attribution/pricing.py:44-52,290-293`). Absent explicit cache rates, cache-read defaults to 10% of input and cache-write to 125% of input (`src/cost_attribution/pricing.py:56-68`).

## Policy and gates

- **Admin scope required (control-plane mutations & reads):** `budget_admin`, `pricing_admin`, `routing_config`, and `team_registration` handlers all require the admin scope in-handler before dispatch. `src/budget_admin/handler.py:177-178`, `src/pricing_admin/handler.py:256-257`, `src/routing_config/handler.py:280-281`, `src/team_registration/handler.py:79-80`.
- **Admin-scope aliasing:** both the canonical `https://gateway.internal/admin` and the legacy `admin` scope satisfy an admin requirement. `src/gwcore/auth.py:37-39,203-224`.
- **Invoke scope required (usage self-service):** the usage API requires the `https://gateway.internal/invoke` scope. `src/usage_api/handler.py:224`.
- **Tenant isolation (usage):** a non-admin caller may read only their own team's usage — `principal.team` must equal the requested `team`, and an empty team claim does not bypass the check. `src/usage_api/handler.py:236-240`.
- **Tenant isolation (audit trail):** the same own-team-only guard is applied to `GET /audit`, retained even though the entry point already requires admin. `src/budget_admin/handler.py:144-146`.
- **Budget hard-limit block:** a request is blocked with HTTP 429 when utilization ≥ `hard_limit_pct` (default 100%). `src/budget_enforcement/handler.py:297-311`.
- **Budget warn-threshold allow:** at utilization ≥ `warn_threshold_pct` (default 80%) the request is allowed but flagged with a warning reason. `src/budget_enforcement/handler.py:334-346`.
- **Per-model budget cap:** a request is blocked when the team's current spend for that specific model is ≥ its configured `monthly_usd` cap; model `"unknown"` disables only the per-model cap, not the team-level hard stop. `src/budget_enforcement/handler.py:176-201,313-331`.
- **RPM rate limit:** blocked with 429 when the current-minute request count exceeds `rpm_limit` (skipped when `rpm_limit ≤ 0`). `src/rate_limiter/handler.py:161-187`.
- **Daily-token rate limit:** blocked with 429 when the day's accumulated tokens exceed `tokens_per_day_limit`; `-1` means unlimited. `src/rate_limiter/handler.py:189-220`.
- **Graceful degradation:** if DynamoDB is unreachable during a budget or rate-limit lookup, the request is allowed (reason `budget-check-degraded` / `rate-limit-degraded`) — a store outage must never block traffic. `src/budget_enforcement/handler.py:224-229,275-280`; `src/rate_limiter/handler.py:164-171,192-204`.
- **Guardrail-webhook contract:** the budget-enforcement Lambda always returns HTTP 200 and carries the allow/deny decision inside an agentgateway `action` envelope (`pass`/`reject`), because a 4xx would read as a hook failure rather than a deny. `src/budget_enforcement/handler.py:1-18,432-448`; `src/gwcore/agentgateway.py:79-103`.
- **Tier budget defaults:** on registration a team's monthly budget is seeded from `TIER_BUDGET_DEFAULTS` (FREE=$10, STANDARD=$1000, PREMIUM=$10000, ENTERPRISE=$100000; unknown tier → $1000), with `warn_threshold_pct=80` / `hard_limit_pct=100`. `src/team_registration/models.py:28-33`; seeded at `src/team_registration/routes.py:148-161`.
- **Budget-enforcement tier defaults (independent table):** the enforcement Lambda's own built-in defaults are sandbox/standard/premium/unlimited (monthly $25/$100/$1000/$10000), overridable via the `TIER_DEFAULTS` env var or per-team DynamoDB budget records. `src/budget_enforcement/handler.py:62-106,240-259`.
- **Token-exchange team gate:** a caller with no `team` claim cannot be minted a team-scoped token (403). `src/admin_token/handler.py:124-126`.
- **Token TTL clamp:** the minted gateway JWT lifetime is clamped to `max(300, min(requested_ttl, 43200))` seconds (12h ceiling). `src/admin_token/handler.py:41,90-92`.
- **Group→claim mapping (first-match priority):** the pre-token Cognito trigger maps the first IdP group that matches `GROUP_MAPPING` to `custom:team`/`org_unit`/`cost_center`/`tenant_tier`; unmatched users pass through unchanged. `src/pre_token/handler.py:48-56,104-134`.
- **Unverified-identity tagging:** when the ALB is not enforcing JWT auth (`JWT_AUTH_ENFORCED` ≠ `true`), the `x-amzn-oidc-data` header is attacker-spoofable, so cost-attribution prefixes derived team/user with `unverified-`. `src/cost_attribution/handler.py:82-124`.
- **Unknown-model price signal:** when a `(provider, model)` has no pricing row, cost is a default-price estimate and a `UnknownModelPrice` metric is emitted so unpriced models are alarmable rather than silently mis-billed. `src/cost_attribution/pricing.py:232-262`; `src/cost_attribution/handler.py:262-264`.
- **Budget-alert thresholds:** SNS budget alerts fire once per crossed threshold (default `[50, 80, 100]`%), tracked via `alerts_sent`; requires a configured SNS topic and a positive monthly budget. `src/cost_attribution/handler.py:420-502`.
- **Deactivation revokes tokens:** deactivating a team deletes its Cognito app client, which immediately revokes all issued tokens, and marks the team INACTIVE. `src/team_registration/routes.py:338-370`.
- **Pricing overlay precedence:** DynamoDB pricing entries override the static `PRICING_TABLE`; the merged table is cached for 300s. `src/cost_attribution/pricing.py:158-229`.
- **Lossy routing-migration warnings:** rendering a routing config to agentgateway's `ai.groups` shape drops conditional predicates, `on_status_codes`, weight ratios, per-target retry, and virtual keys; each loss is surfaced as an API warning, a log line, and a `RoutingConfigMigrationWarning` metric. `src/routing_config/models.py:169-219`; `src/routing_config/handler.py:115-133`.
- **Audit-query SQL safety:** `GET /audit` binds `team` and the ISO-8601 time bounds as Athena `ExecutionParameters` (never string-interpolated) and clamps the row limit to `[1, 1000]` (default 100). `src/budget_admin/audit_query.py:103-136,219-240`.
- **Audit on every mutation and every authz denial:** each control-plane mutation and each 401/403 rejection emits a `gwcore.audit` event (allow and deny both recorded). `src/gwcore/audit.py:88-119`; e.g. `src/budget_admin/handler.py:181-201`, `src/pricing_admin/handler.py:269-287`.

## See also

- [behavior/processes](../behavior/processes.md) — 7 shared source citations
- [insights/contract-map](contract-map.md) — 7 shared source citations
- [architecture/module-map](../architecture/module-map.md) — 6 shared source citations
- [insights/impact-analysis](impact-analysis.md) — 6 shared source citations
- [architecture/data-flow](../architecture/data-flow.md) — 4 shared source citations
