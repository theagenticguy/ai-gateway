# ai-gateway · Contract map

This file answers one question: *when module A passes something to module B, what is B really expecting?*

`ai-gateway` is a Python 3.13 LLM-gateway control plane: one shared library (`gwcore`) and 11 AWS Lambda service packages under `src/`. The services almost never import one another (only two direct Python cross-service imports exist), so most inter-module contracts here are **latent** — modules couple through three boundaries rather than through function calls:

1. **gwcore → services** — the shared response, error, auth, and audit shapes every handler builds on (`from gwcore import ...`).
2. **service → AWS** — DynamoDB item shapes (`pk`/`sk` conventions), the agentgateway rendered-config shape, Firehose/Iceberg records.
3. **service ↔ service via shared storage** — one service writes a DynamoDB item shape that another reads (usage records, budget records), with no compiler enforcing the shape match.

For this codebase a **contract** is therefore: a shape — a dataclass, Pydantic model, response envelope, DynamoDB item, or event payload — declared or owned by one module and depended on by at least one other module or external boundary (API Gateway, DynamoDB, Firehose/Iceberg, Cognito, agentgateway, SNS). Where the producer and consumer couple through untyped storage or an external wire, the Shape is quoted from the owning model and the assumptions are cited at the consumer's read/parse site. Contracts are ordered by consumer count.

## HTTP response envelope (`ok` / `page` / `error_response`)

**Producer:** `src/gwcore/responses.py:39` (`ok`), `src/gwcore/responses.py:133` (`page`), `src/gwcore/responses.py:74` (`error_response`).

**Consumer(s):**
- `src/admin_token/handler.py:149` — `responses.ok(body.model_dump())`.
- `src/budget_admin/routes.py:92` (`page`), `src/budget_admin/routes.py:138` (`ok`).
- `src/usage_api/handler.py:199` — `ok(response.model_dump(mode="json"))`.
- `src/team_registration/routes.py:174` — `ok({...}, status=201)`.
- `src/pricing_admin/handler.py:147` — `ok({"prices": summaries, ...})`.
- `src/routing_config/handler.py:165` — `ok({"configs": summaries, "total": ...})`.

**Shape:**
```python
# ok() — src/gwcore/responses.py:71
return {"statusCode": status, "headers": headers, "body": _dumps(body)}
# 304 short-circuit when etag matches — src/gwcore/responses.py:69
return {"statusCode": 304, "headers": headers, "body": ""}
# page() body — src/gwcore/responses.py:140
body = {
    "items": items,
    "count": len(items),
    "next_cursor": encode_cursor(last_key),
}
```

**Assumptions consumers make:**
- The body is serialized with sorted keys and compact separators (`_dumps`, `src/gwcore/responses.py:28`); handlers pass `model.model_dump(mode="json")` and rely on `default=str` to serialize `Decimal`/`datetime` rather than pre-stringifying (`src/usage_api/handler.py:199`, `src/team_registration/routes.py:217`).
- API Gateway / Function URL proxy integration consumes exactly `statusCode` / `headers` / `body`; the envelope is a drop-in for both event sources (`src/gwcore/responses.py:11`).
- `page()` always emits `items` / `count` / `next_cursor`; `budget_admin` passes the DynamoDB `LastEvaluatedKey` straight through and never computes an offset (`src/budget_admin/routes.py:92`).

**Drift risk:** Adding a top-level envelope key (e.g. a `meta` block) is additive and safe, but changing `next_cursor` to a non-opaque value would break cursor round-tripping; keep the cursor opaque (base64 of `LastEvaluatedKey`) and version the envelope with a header if the top-level shape must change.

## Error hierarchy + error envelope (`ControlPlaneError`)

**Producer:** `src/gwcore/errors.py:13` (`ControlPlaneError`, `.to_body()` at `src/gwcore/errors.py:36`); subclasses at `src/gwcore/errors.py:44-84`.

**Consumer(s):**
- `src/budget_admin/handler.py:181` — `except errors.ControlPlaneError` → `responses.error_response(exc)` (`src/budget_admin/handler.py:201`).
- `src/usage_api/handler.py:250` — same catch/map; raises `UpstreamError` at `src/usage_api/handler.py:157`.
- `src/team_registration/handler.py:83` — catch/map; routes raise `ConflictError` / `NotFoundError` (`src/team_registration/routes.py:118`, `:228`).
- `src/routing_config/handler.py:295` — catch/map.
- `src/pricing_admin/handler.py:269` — catch/map.
- `src/admin_token/handler.py:150` — catch/map; `budget_admin/audit_query.py:99` raises `ValidationFailedError`.

**Shape:**
```python
# ControlPlaneError.to_body() — src/gwcore/errors.py:36
def to_body(self) -> dict[str, Any]:
    """Render the error envelope body."""
    body: dict[str, Any] = {"error": {"code": self.code, "message": self.message}}
    if self.details:
        body["error"]["details"] = self.details
    return body
```
Each subclass fixes `status` + `code`: `ValidationFailedError` 400/`validation_failed`, `UnauthorizedError` 401, `ForbiddenError` 403, `NotFoundError` 404, `ConflictError` 409, `UpstreamError` 502 (`src/gwcore/errors.py:44-84`).

**Assumptions consumers make:**
- Every handler assumes a raised `ControlPlaneError` carries a usable HTTP `status` and machine `code`; `error_response` maps `exc.status` directly to `statusCode` (`src/gwcore/responses.py:74`).
- Handlers branch on `exc.status in {401, 403}` to emit an `AuthzDenied` metric + deny audit before mapping (`src/budget_admin/handler.py:182`, `src/usage_api/handler.py:251`, `src/routing_config/handler.py:296`) — they assume authz failures always surface as 401/403 and nothing else does.
- The `code` string is treated as stable/machine-readable and is logged and audited as `detail` (`src/team_registration/handler.py:98`), so it is an API surface, not just prose.

**Drift risk:** Changing a subclass's `code` string silently breaks clients and dashboards that match on it; treat `code` values as a versioned enum and add new subclasses rather than renaming existing codes.

## Principal (validated identity handed to handlers)

**Producer:** `src/gwcore/auth.py:46` (`Principal`), built by `build_principal` (`src/gwcore/auth.py:131`) and `verify_token` (`src/gwcore/auth.py:164`).

**Consumer(s):**
- `src/usage_api/handler.py:223` — `auth.build_principal(event)`; reads `principal.is_admin` + `principal.team` for tenant isolation (`src/usage_api/handler.py:236`).
- `src/budget_admin/handler.py:177` — `build_principal` + `require(scopes=[ADMIN_SCOPE])`; `_get_audit` reads `principal.is_admin` / `principal.team` (`src/budget_admin/handler.py:144`).
- `src/team_registration/handler.py:79` — `build_principal` + `require(...)`; `_audit` reads `principal.sub` / `principal.team` (`src/team_registration/routes.py:77`).
- `src/pricing_admin/handler.py:256`, `src/routing_config/handler.py:280` — `build_principal` + `require(scopes=[ADMIN_SCOPE])`.
- `src/admin_token/handler.py:87` — `build_principal`; `_mint` reads `principal.sub` / `team` / `cost_center` / `tenant_tier` (`src/admin_token/handler.py:96-102`).

**Shape:**
```python
# src/gwcore/auth.py:46
@dataclass(frozen=True)
class Principal:
    """The authenticated caller, normalized across both verification modes."""

    sub: str
    scopes: frozenset[str] = field(default_factory=frozenset)
    team: str = ""
    cost_center: str = ""
    tenant_tier: str = "standard"
    client_id: str = ""
    token_use: str = ""
    raw_claims: dict[str, Any] = field(default_factory=dict)

    @property
    def is_admin(self) -> bool:
        """True if the principal carries any accepted admin scope."""
        return bool(self.scopes & _ADMIN_SCOPE_ALIASES)
```

**Assumptions consumers make:**
- `principal.team` may be empty; `usage_api` explicitly denies a non-admin whose `team` is empty or mismatched so an empty claim cannot bypass tenant isolation via the `?team=` param (`src/usage_api/handler.py:236`); `budget_admin` repeats the guard (`src/budget_admin/handler.py:144`).
- `is_admin` is satisfied by either the canonical `https://gateway.internal/admin` scope or the legacy `"admin"` string — both aliases are accepted during migration (`src/gwcore/auth.py:39`, `src/gwcore/auth.py:62`); `require(scopes=[ADMIN_SCOPE])` relies on this alias set (`src/gwcore/auth.py:218`).
- `admin_token` assumes `team` / `cost_center` / `tenant_tier` are populated well enough to re-emit into a minted token and 403s when `team` is empty (`src/admin_token/handler.py:124`).

**Drift risk:** `build_principal` trusts authorizer/claims without re-verifying signature (`src/gwcore/auth.py:89`), so a claim-name change in the token producer (see the Cognito custom-claim contract below) silently empties `team`/`tenant_tier`; keep claim keys in lockstep with `_principal_from_claims` (`src/gwcore/auth.py:75`).

## Cognito JWT custom claims (`custom:team` / `custom:cost_center` / `custom:tenant_tier`)

**Producer:** `src/pre_token/handler.py:59` (`_build_claim_overrides` injects `custom:team` / `custom:org_unit` / `custom:cost_center` / `custom:tenant_tier` into both tokens); `src/admin_token/handler.py:94` (`_mint` re-emits `custom:team` / `custom:cost_center` / `custom:tenant_tier` + `scope`).

**Consumer(s):** three independent decoders re-read the same claim keys without sharing code:
- `src/gwcore/auth.py:75` — `_principal_from_claims` reads `custom:team`, `custom:cost_center`, `custom:tenant_tier`, `scope`, `sub`, `client_id`/`aud`, `token_use`.
- `src/budget_enforcement/jwt_utils.py:49` — `extract_team` / `extract_user` / `extract_cost_center` / `extract_tenant_tier` read `custom:team`|`team`, `sub`|`username`, `custom:cost_center`|`cost_center`, `custom:tenant_tier`|`tenant_tier`.
- `src/cost_attribution/handler.py:115` — `_extract_identity` reads `custom:team`|`team` and `sub`|`username`.

**Shape:**
```python
# claim override list produced for both ID + access tokens — src/pre_token/handler.py:59
return [
    {"claimKey": "custom:team", "claimValue": claims.team},
    {"claimKey": "custom:org_unit", "claimValue": claims.org_unit},
    {"claimKey": "custom:cost_center", "claimValue": claims.cost_center},
    {"claimKey": "custom:tenant_tier", "claimValue": claims.tenant_tier},
]
```

**Assumptions consumers make:**
- Each decoder assumes the `custom:` prefix but tolerates a bare-key fallback (`custom:team` → `team`) and defaults missing values to `"unknown"` / `"standard"` (`src/budget_enforcement/jwt_utils.py:52`, `:73`; `src/cost_attribution/handler.py:115`).
- `tenant_tier` is assumed lowercasable and matched against tier-default keys downstream (`src/budget_enforcement/jwt_utils.py:73` → `src/budget_enforcement/handler.py:234`).
- `cost_attribution` assumes the identity is untrustworthy unless the ALB enforced JWT auth, prefixing `unverified-` when `JWT_AUTH_ENFORCED` is off (`src/cost_attribution/handler.py:122`) — a claim contract that is identity-tagging, not just extraction.

**Drift risk:** Three separate decoders means a claim rename (or a Cognito switch to un-prefixed keys) must be applied in all three plus `gwcore.auth`, or `team` silently becomes `"unknown"` and cost attribution/budget enforcement mis-bucket; consolidate on `gwcore.auth._principal_from_claims` or a shared constant for the claim keys.

## DynamoDB usage record (`USAGE#…` / `PERIOD#…`)

**Producer:** `src/cost_attribution/handler.py:344` (`_update` writes atomic `ADD` increments); keys `USAGE#TEAM#<team>`, `USAGE#USER#<user>`, `USAGE#TEAM#<team>#MODEL#<model>` all with `sk=PERIOD#<YYYY-MM>` (`src/cost_attribution/handler.py:369`, `:375`, `:382`). Canonical model at `src/cost_attribution/models.py:217`.

**Consumer(s):**
- `src/budget_enforcement/handler.py:125` — reads `total_cost_usd` at `USAGE#TEAM#{team}` / `PERIOD#{period}`; model-level at `src/budget_enforcement/handler.py:139`.
- `src/usage_api/handler.py:42` — reads the team row; `src/usage_api/handler.py:101` maps `total_tokens`/`input_tokens`/`output_tokens`/`cached_tokens`/`total_cost_usd`/`request_count`; scans `#MODEL#` rows at `src/usage_api/handler.py:62`.
- `src/chargeback_report/handler.py:56` — scans `pk begins_with USAGE#TEAM#`, `sk = PERIOD#<month>`; builds summaries at `src/chargeback_report/handler.py:134`.
- `src/team_registration/routes.py:262` — reads `total_cost_usd` for the usage summary.

**Shape:**
```python
# src/cost_attribution/models.py:217
class UsageRecord(BaseModel):
    """Accumulated usage for a given entity+period stored in DynamoDB.

    PK: ``USAGE#<entity_type>#<entity_id>``  SK: ``PERIOD#<YYYY-MM>``
    """

    pk: str = Field(description="Partition key, e.g. USAGE#TEAM#my-team")
    sk: str = Field(description="Sort key, e.g. PERIOD#2026-03")
    total_tokens: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    total_cost_usd: Decimal = Field(default=Decimal("0.00"), ge=0)
    request_count: int = Field(default=0, ge=0)
```

**Assumptions consumers make:**
- Readers assume `total_cost_usd` may be a string or absent and coerce via `Decimal(str(item.get("total_cost_usd", "0")))`, defaulting to `0.00` on failure (`src/budget_enforcement/handler.py:130`, `src/usage_api/handler.py:138`, `src/team_registration/routes.py:266`).
- Readers assume the exact `pk`/`sk` string format and rebuild the key literally; `usage_api` even re-parses the model name back out of the PK by splitting on `#MODEL#` (`src/usage_api/handler.py:119`).
- Consumers assume the current period is `datetime.now(UTC).strftime("%Y-%m")` (`src/budget_enforcement/handler.py:124`, `src/usage_api/handler.py:81`) — the same format the writer uses (`src/cost_attribution/handler.py:302`).

**Drift risk:** The `pk`/`sk` prefix strings are duplicated as f-strings across five modules with no shared constant, so a prefix change in the writer silently yields empty reads (budget checks pass, usage shows zero); centralize the key builders in one module and have every reader/writer import them.

## DynamoDB budget record (`BUDGET#<team>` / `CONFIG`)

**Producer:** `src/team_registration/routes.py:149` seeds the record on team registration; `src/cost_attribution/handler.py:481` updates `alerts_sent`. Canonical model at `src/cost_attribution/models.py:178`.

**Consumer(s):**
- `src/budget_enforcement/handler.py:117` — reads `monthly_budget_usd`, `warn_threshold_pct`, `hard_limit_pct`, `model_limits`, `rpm`, `tokens_per_day` (`src/budget_enforcement/handler.py:242-252`).
- `src/cost_attribution/handler.py:393` — reads `monthly_budget_usd`, `alert_thresholds`, `alerts_sent` (`src/cost_attribution/handler.py:426-432`).
- `src/usage_api/handler.py:49` — reads `monthly_budget_usd` (`src/usage_api/handler.py:165`).
- `src/team_registration/routes.py:252` — reads `monthly_budget_usd` for the usage summary.
- `src/chargeback_report/handler.py:83` — reads budget limits, accepting `monthly_budget_usd` or `monthly_usd`, keyed by `scope_id` or `team` (`src/chargeback_report/handler.py:87-88`).

**Shape:**
```python
# src/cost_attribution/models.py:178
class BudgetRecord(BaseModel):
    """A budget configuration stored in DynamoDB.

    PK: ``BUDGET#<team>``  SK: ``CONFIG``
    """

    pk: str = Field(description="Partition key, e.g. BUDGET#my-team")
    sk: str = Field(default="CONFIG", description="Sort key")
    team: str
    cost_center: str = Field(default="")
    tenant_tier: TenantTier = Field(default=TenantTier.STANDARD)
    monthly_budget_usd: Decimal = Field(default=Decimal("1000.00"), ge=0)
    warn_threshold_pct: Decimal = Field(default=Decimal("80.0"), ge=0, le=100, ...)
    hard_limit_pct: Decimal = Field(default=Decimal("100.0"), ge=0, ...)
    model_limits: dict[str, ModelLimit] = Field(default_factory=dict, ...)
    alert_thresholds: list[int] = Field(default_factory=lambda: [50, 80, 100], ...)
    alerts_sent: list[int] = Field(default_factory=list, ...)
```

**Assumptions consumers make:**
- The seed writer stores `warn_threshold_pct` / `hard_limit_pct` as plain ints (`src/team_registration/routes.py:157`) while the model + `budget_enforcement` read them as `float(...)` (`src/budget_enforcement/handler.py:245`), so consumers assume numeric-coercible, not typed, values.
- `budget_enforcement` treats `model_limits` as a `dict[model -> {monthly_usd, daily_tokens}]` and skips malformed entries (`src/budget_enforcement/handler.py:161`), but the seed writer never writes `model_limits`, so consumers assume its absence means "no per-model caps".
- `cost_attribution` assumes `alerts_sent` is a mutable list it may extend and write back to dedupe SNS alerts (`src/cost_attribution/handler.py:477`).

**Drift risk:** `budget_admin` writes a **structurally different** budget item — `Key={"budget_id":…, "scope":"CONFIG"}` with `scope_type`/`scope_id`/`budget_usd` (`src/budget_admin/routes.py:152-163`) — not the `BUDGET#<team>`/`CONFIG` + `monthly_budget_usd` shape the enforcement path reads; a budget created via `budget_admin` is invisible to `budget_enforcement`, and only `chargeback_report` bridges the two by reading both key/field spellings (`src/chargeback_report/handler.py:87`). Converge the two writers on one item shape (or document `budget_admin` as a separate table) so admin-created budgets actually enforce.

## AuditEvent (Firehose → Iceberg audit trail)

**Producer:** `src/gwcore/audit.py:42` (`AuditEvent`), `emit` at `src/gwcore/audit.py:64`, `event_from_request` at `src/gwcore/audit.py:88`.

**Consumer(s):** every mutating / authz-deciding handler emits one:
- `src/admin_token/handler.py:130` (token exchange + deny at `:163`).
- `src/budget_admin/routes.py:73` (mutations) and `src/budget_admin/handler.py:190` (deny).
- `src/budget_enforcement/handler.py:366` (hard-deny audit).
- `src/pricing_admin/handler.py:110`, `src/routing_config/handler.py:112`, `src/team_registration/routes.py:77`, `src/usage_api/handler.py:257`.
- Reader boundary: `src/budget_admin/audit_query.py:51` projects the Iceberg columns back into records, surfaced as `AuditRecord` (`src/budget_admin/models.py:110`).

**Shape:**
```python
# src/gwcore/audit.py:42
@dataclass
class AuditEvent:
    """A single audit record. Field order matches the Iceberg table schema."""

    action: str
    actor: str
    resource: str
    decision: str = "allow"  # allow | deny
    status: int = 200
    team: str = ""
    source_ip: str = ""
    correlation_id: str = ""
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    detail: str = ""
    ts: str = field(default_factory=_now_iso)
```

**Assumptions consumers make:**
- Field order is assumed to match the Iceberg table schema (`src/gwcore/audit.py:44`); the Athena read projects a fixed subset (`action, actor, resource, decision, status, team, source_ip, correlation_id, detail, ts`) and deliberately omits `before`/`after` as heavy nested JSON (`src/budget_admin/audit_query.py:51`, `src/budget_admin/models.py:113`).
- The reader assumes `status` is the only integer column and coerces it, defaulting others to `""` (`src/budget_admin/audit_query.py:204`).
- Emission is assumed best-effort: producers never handle a failure (`src/gwcore/audit.py:64` swallows and returns `False`), so a Firehose outage must not fail a mutation.

**Drift risk:** Adding a field to `AuditEvent` requires an Iceberg schema evolution AND (if it should be queryable) a new column in `_COLUMNS`, or the field lands in Firehose but is invisible to `GET /audit`; keep `AuditEvent`, the Iceberg DDL, and `audit_query._COLUMNS` changed together.

## agentgateway guardrail-webhook action envelope

**Producer:** `src/gwcore/agentgateway.py:79` (`_envelope`), `pass_action` / `reject_action` / `mask_action` (`src/gwcore/agentgateway.py:95-107`), request helpers `extract_messages` (`:29`) and `header_lookup` (`:64`).

**Consumer(s):**
- `src/budget_enforcement/handler.py:422` — `_request_from_agentgateway` uses `extract_messages` + `header_lookup`; `_build_agentgateway_response` (`src/budget_enforcement/handler.py:432`) returns `pass_action()` / `reject_action(...)`.

**Shape:**
```python
# src/gwcore/agentgateway.py:79
return {
    "statusCode": 200,
    "headers": {"Content-Type": "application/json"},
    "body": json.dumps({"action": action}),
}
# actions — src/gwcore/agentgateway.py:95
{"pass": {}}
{"reject": {"status_code": status_code, "body": body, "reason": reason}}
{"mask": {"body": {"messages": messages}, "reason": reason}}
```

**Assumptions consumers make:**
- The Lambda always returns HTTP 200 and rides the allow/deny decision inside `action`, because agentgateway reads a 4xx as a hook *failure*, not a deny (`src/gwcore/agentgateway.py:83`, `src/budget_enforcement/handler.py:6`).
- The forwarded identity arrives as the `x-amzn-oidc-data` request header and the model as `x-model`, not in the body (`src/budget_enforcement/handler.py:423-424`); the prompt lives under `body.messages` (`src/gwcore/agentgateway.py:35`).
- The token estimate is local (`4 chars/token`, `src/gwcore/agentgateway.py:26`) since agentgateway forwards no token count.

**Drift risk:** The `pass`/`reject`/`mask` action key names and the header names are set by the external agentgateway webhook schema (ADR-017, referenced at `src/gwcore/agentgateway.py:14`); an agentgateway upgrade that renames an action or header would silently turn every deny into a pass — pin the agentgateway config-schema version and add a contract test against a captured webhook payload.

## `check_rate_limit` + `RateLimitResult`

**Producer:** `src/rate_limiter/handler.py:135` (`check_rate_limit`), result model at `src/rate_limiter/models.py:8`.

**Consumer(s):**
- `src/budget_enforcement/handler.py:261` — calls `check_rate_limit(team=…, rpm_limit=…, tokens_per_day_limit=…, estimated_tokens=…)` and reads `.allowed` / `.reason` / `.retry_after_seconds` (`src/budget_enforcement/handler.py:267-272`). This is one of only two direct Python cross-service imports (`src/budget_enforcement/handler.py:51`).

**Shape:**
```python
# src/rate_limiter/models.py:8
class RateLimitResult(BaseModel):
    """Result of a rate limit check against RPM and daily token limits."""

    allowed: bool = Field(description="Whether the request is allowed")
    reason: str = Field(default="", description="Explanation when request is denied")
    retry_after_seconds: int | None = Field(default=None, ...)
    current_rpm: int = Field(default=0, ...)
    current_daily_tokens: int = Field(default=0, ...)
```

**Assumptions consumers make:**
- `budget_enforcement` assumes a rate-store outage returns `allowed=True` with `reason="rate-limit-degraded"` (graceful degradation), never an exception (`src/rate_limiter/handler.py:171`, consumed as a plain allow at `src/budget_enforcement/handler.py:267`).
- The caller assumes `rate_limiter` emits its own denial metric but **no** audit event, so `budget_enforcement` owns the deny-audit to avoid double-counting the same denial (`src/rate_limiter/handler.py:6`, `src/budget_enforcement/handler.py:366`).
- `tokens_per_day_limit == -1` means unlimited and skips the daily check (`src/rate_limiter/handler.py:190`), matching the tier-config sentinel (`src/budget_enforcement/models.py:22`).

**Drift risk:** `rate_limiter` shares the usage table but partitions it under `RATE#RPM#` / `RATE#TOKENS#` prefixes with TTLs (`src/rate_limiter/handler.py:47`, `:105`); a change to those prefixes or the `if_not_exists` counter expression would silently reset windows — keep the counter key schema and the deny-audit ownership documented alongside the `check_rate_limit` signature.

## `TokenPrice` + `PRICING_TABLE`

**Producer:** `src/cost_attribution/pricing.py:25` (`TokenPrice`), the static `PRICING_TABLE` at `src/cost_attribution/pricing.py:71`, keyed by `(provider.lower(), model)` (`src/cost_attribution/pricing.py:239`).

**Consumer(s):**
- `src/cost_attribution/handler.py:178` — `get_cost(...)` per request; `is_known_model` gates the `UnknownModelPrice` metric (`src/cost_attribution/handler.py:183`).
- `src/pricing_admin/handler.py:25` — imports `PRICING_TABLE` (the second and last cross-service Python import); iterates it and reads `.input_per_1k` / `.output_per_1k` / `.cache_read_per_1k` / `.cache_write_per_1k` (`src/pricing_admin/handler.py:136`, `:171-179`).

**Shape:**
```python
# src/cost_attribution/pricing.py:25
class TokenPrice(BaseModel):
    input_per_1k: float = Field(ge=0.0, ...)
    output_per_1k: float = Field(ge=0.0, ...)
    cache_read_per_1k: float | None = Field(default=None, ge=0.0, ...)
    cache_write_per_1k: float | None = Field(default=None, ge=0.0, ...)
    cache_supported: bool = Field(default=True, ...)

    model_config = {"frozen": True}
```

**Assumptions consumers make:**
- `pricing_admin` reads the raw `cache_read_per_1k` / `cache_write_per_1k` (which may be `None`) rather than the `effective_*` properties (`src/pricing_admin/handler.py:178`), so it assumes `None` means "unset" — while `cost_attribution` treats `None` + `cache_supported=True` as "default to 10%/125% of input" (`src/cost_attribution/pricing.py:57-68`, `:292`). The `None` sentinel means different things to the two consumers.
- Both consumers assume the DynamoDB override item shape mirrors `TokenPrice`: `PK=PRICE#<provider>#<model>`, `SK=CONFIG`, plus `input_per_1k` / `output_per_1k` / optional cache fields (`src/pricing_admin/handler.py:59-74`, parsed identically at `src/cost_attribution/pricing.py:186-203`).
- `is_known_model` lower-cases only the provider, not the model (`src/cost_attribution/pricing.py:239`), so consumers assume model IDs are case-exact.

**Drift risk:** The DDB pricing item is written by `pricing_admin._put_price_item` and read by `cost_attribution._load_dynamic_pricing` with no shared parser; adding a field to `TokenPrice` (e.g. a new cache lane) needs both sides updated or overrides silently drop it — extract one shared DDB-item ↔ `TokenPrice` codec.

## agentgateway access-log record (`LogRecord`)

**Producer:** the agentgateway data plane (external) emits flat access-log JSON (ADR-017); modeled and normalized by `src/cost_attribution/models.py:70` (`LogRecord`, with flat→nested synthesis at `src/cost_attribution/models.py:88`).

**Consumer(s):**
- `src/cost_attribution/handler.py:145` — `LogRecord.model_validate(raw)`, then reads `record.usage`, `record.model`, `record.resolved_provider`, `record.oidc_data` (via `_extract_identity`).

**Shape:**
```python
# src/cost_attribution/models.py:70
class LogRecord(BaseModel):
    usage: UsageMetrics | None = None
    model: str = Field(default="unknown")
    provider: str = Field(default="")
    req: RequestInfo = Field(default_factory=RequestInfo)
    oidc_data: str = Field(default="", description="ALB JWT when the access log emits it flat")
# flat token keys accepted — src/cost_attribution/models.py:97
("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens",
 "output_tokens", "cached_input_tokens", "cache_creation_input_tokens")
```

**Assumptions consumers make:**
- The consumer assumes agentgateway emits a flat top-level shape and synthesizes the nested `usage` block from flat keys, accepting either `prompt_tokens`/`input_tokens` and `completion_tokens`/`output_tokens` spellings (`src/cost_attribution/models.py:106-116`).
- A record with no tokens is assumed skippable (`record.usage is None or not record.usage.has_tokens` → dropped, `src/cost_attribution/handler.py:148`).
- The JWT is assumed to arrive either nested under `req.headers["x-amzn-oidc-data"]` or as a flat `oidc_data` field (`src/cost_attribution/handler.py:106-111`).

**Drift risk:** The access-log field names are defined by the agentgateway access-log `add` block (external config); a renamed field makes `usage` synthesize to zero and the record is silently skipped, under-counting spend — pin the access-log field mapping in the agentgateway config and add a fixture test of a real log line.

## RoutingConfig → agentgateway backend render

**Producer:** `src/routing_config/models.py:119` (`RoutingConfig.to_agentgateway_backend`), plus the lossiness report `migration_warnings` (`src/routing_config/models.py:169`).

**Consumer(s):**
- `src/routing_config/handler.py:74` — `_put_custom_config` stores `json.dumps(config.to_agentgateway_backend())` into the `config_json` DynamoDB attribute; `_create_config` / `_update_config` return the rendered block and attach `migration_warnings` to the API response (`src/routing_config/handler.py:207`, `:242`).
- The agentgateway data plane (external) consumes the rendered `ai.groups` shape.

**Shape:**
```python
# returned block — src/routing_config/models.py:161
if self.strategy.mode == StrategyMode.LOADBALANCE:
    groups = [{"providers": [provider_block(t) for t in self.targets]}]
else:  # fallback + conditional collapse to ordered priority groups
    groups = [{"providers": [provider_block(t)]} for t in self.targets]
return {"groups": groups}
# provider_block — src/routing_config/models.py:149
entry = {"name": target.name, "provider": {key: spec}}
if key == "bedrock":
    entry["policies"] = {"backendAuth": {"aws": {}}}
```

**Assumptions consumers make:**
- The render assumes agentgateway's provider keys are `bedrock` / `anthropic` / `openAI` / `azure` / `gemini` and remaps the API's provider names accordingly (`src/routing_config/models.py:140`).
- It assumes Bedrock resolves credentials from ambient task-role SigV4 via `policies.backendAuth.aws` rather than a per-target key (`src/routing_config/models.py:157`).
- The handler assumes agentgateway cannot express conditional predicates, `on_status_codes`, 0-1 weight ratios, per-target retry, or `virtual_key`, and surfaces each dropped semantic as a logged + metered migration warning (`src/routing_config/models.py:169`, `src/routing_config/handler.py:115`).

**Drift risk:** The `groups` / `providers` / `provider` / `name` / `policies.backendAuth` key names are the external agentgateway config schema (ADR-017); an agentgateway schema change would render a structurally valid but semantically wrong backend with no error — version the render against a known agentgateway schema and validate rendered configs against it in CI.

## Other contracts

- **Opaque cursor token** — `encode_cursor` / `parse_cursor` base64 of the DynamoDB `LastEvaluatedKey` (`src/gwcore/responses.py:86-114`); produced by `budget_admin.list_budgets` (`src/budget_admin/routes.py:92`) and round-tripped back via the `?cursor=` param (`src/budget_admin/routes.py:82`).
- **Minted gateway JWT** — `admin_token._mint` HS256 token carrying `scope=invoke` + `custom:team`/`cost_center`/`tenant_tier` (`src/admin_token/handler.py:90`); consumed by the inference-plane edge (external), self-contained so no callback to this service.
- **SNS `budget_alert` message** — `cost_attribution._publish_alert` JSON payload (`src/cost_attribution/handler.py:532`); consumed by external SNS subscribers.
- **Chargeback S3 HTML report** — `chargeback_report` `ReportResponse` + the S3 object at `reports/<month>/chargeback-report-<month>.html` (`src/chargeback_report/handler.py:192`, model `src/chargeback_report/models.py:70`); consumed by Step Functions and S3 report readers.
- **`gwcore.cache.TTLCache`** — generic in-process TTL cache (`src/gwcore/cache.py:32`); only consumer is the JWKS cache inside `gwcore.auth` (`src/gwcore/auth.py:43`) — internal to gwcore, no service consumer.
- **ETag / `cache_seconds` response options** — the capability exists (`src/gwcore/responses.py:39-71`) but no handler currently passes `etag=True` or `cache_seconds`; a latent contract with zero live consumers.
- **`MetricResult`** — intra-module cost_attribution shape (`src/cost_attribution/models.py:125`) produced by `_extract_metrics` and consumed by `_publish_metrics` / `_accumulate_usage` / `_publish_audit_records`; single-module, listed for completeness.
- **CloudWatch EMF metric doc + OTEL GenAI attrs** — `gwcore.telemetry.emit_metric` / `genai_attributes` (`src/gwcore/telemetry.py:19`, `:92`); emitted by every handler to the CloudWatch log-ingest boundary.

## See also

- [architecture/module-map](../architecture/module-map.md) — 12 shared source citations
- [insights/impact-analysis](impact-analysis.md) — 12 shared source citations
- [behavior/processes](../behavior/processes.md) — 11 shared source citations
- [reference/public-api](../reference/public-api.md) — 8 shared source citations
- [insights/business-logic](business-logic.md) — 7 shared source citations
