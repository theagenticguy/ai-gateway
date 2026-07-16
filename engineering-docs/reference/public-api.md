# ai-gateway · Public API

The importable public surface of `ai-gateway` is the shared `gwcore` package — the authentication, error, response, audit, cache, telemetry, logging, and agentgateway-contract helpers that every control-plane and data-plane Lambda imports. The 30 symbols below are the `gwcore` exports ranked by inbound-reference count from the eleven service packages under `src/` (tie-broken alphabetically). The repository ships no CLI (no `argparse`, `console_scripts`, or `__main__` entry point), so the HTTP surface of the Lambda services is enumerated in the final `## HTTP` section.

### emit_metric

```py
def emit_metric(  # noqa: PLR0913 — keyword-only EMF fields; all optional with defaults
    name: str,
    value: float,
    *,
    unit: str = "Count",
    namespace: str = _DEFAULT_NAMESPACE,
    dimensions: dict[str, str] | None = None,
    properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
```

Emit a single CloudWatch EMF metric line to stdout and return the structure.
`src/gwcore/telemetry.py:19`

### ok

```py
def ok(  # noqa: PLR0913 — keyword-only response options; all optional with defaults
    body: Any,
    *,
    status: int = 200,
    cache_seconds: int | None = None,
    etag: bool = False,
    if_none_match: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
```

Build a success response with optional ETag, conditional 304, and cache headers.
`src/gwcore/responses.py:39`

### UpstreamError

```py
class UpstreamError(ControlPlaneError):
    """A downstream dependency (DynamoDB, Cognito, etc.) failed."""

    status = 502
    code = "upstream_error"
```

A `ControlPlaneError` subclass mapping a failed downstream dependency (DynamoDB, Cognito, etc.) to HTTP 502.
`src/gwcore/errors.py:79`

### get_logger

```py
def get_logger(name: str) -> logging.Logger:
```

Return a JSON logger, idempotently configured so it is safe to call on a warm Lambda.
`src/gwcore/logging.py:39`

### Principal

```py
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
```

The authenticated caller, normalized across both the trusted-edge and full-verification token paths.
`src/gwcore/auth.py:46`

### ValidationFailedError

```py
class ValidationFailedError(ControlPlaneError):
    """Request failed validation."""

    status = 400
    code = "validation_failed"
```

A `ControlPlaneError` subclass raised when a request fails validation, mapped to HTTP 400.
`src/gwcore/errors.py:44`

### Timer

```py
class Timer:
    """Context manager that emits a latency metric on exit.
    ...
    def __init__(
        self,
        metric: str,
        *,
        namespace: str = _DEFAULT_NAMESPACE,
        clock: Any = time.monotonic,
        **dimensions: str,
    ) -> None:
```

A context manager that emits a latency metric (in milliseconds) via `emit_metric` when its block exits.
`src/gwcore/telemetry.py:54`

### bind

```py
def bind(logger: logging.Logger, cid: str) -> logging.LoggerAdapter[logging.Logger]:
```

Bind a correlation id so every line emitted through the returned adapter carries it.
`src/gwcore/logging.py:57`

### ControlPlaneError

```py
class ControlPlaneError(Exception):
    ...
    status: int = 500
    code: str = "internal_error"

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        code: str | None = None,
    ) -> None:
```

The base control-plane exception, carrying an HTTP `status`, a stable machine-readable `code`, a human `message`, and optional structured `details`.
`src/gwcore/errors.py:13`

### correlation_id

```py
def correlation_id(event: dict[str, Any]) -> str:
```

Extract the API Gateway / Function URL request id from an event for log correlation.
`src/gwcore/logging.py:51`

### emit

```py
def emit(event: AuditEvent, *, stream_name: str | None = None) -> bool:
```

Write an `AuditEvent` to Kinesis Firehose on a best-effort basis (never raises), returning True on a successful put.
`src/gwcore/audit.py:64`

### NotFoundError

```py
class NotFoundError(ControlPlaneError):
    """Resource does not exist."""

    status = 404
    code = "not_found"
```

A `ControlPlaneError` subclass raised when a resource does not exist, mapped to HTTP 404.
`src/gwcore/errors.py:65`

### build_principal

```py
def build_principal(event: dict[str, Any]) -> Principal:
```

Build a `Principal` from an authorizer-verified request (the trusted-edge path), raising `UnauthorizedError` when no usable claims are found.
`src/gwcore/auth.py:131`

### error_response

```py
def error_response(exc: ControlPlaneError) -> dict[str, Any]:
```

Map a `ControlPlaneError` to an HTTP response carrying its status and the consistent error envelope.
`src/gwcore/responses.py:74`

### event_from_request

```py
def event_from_request(  # noqa: PLR0913 — keyword-only audit fields; all but action/actor/resource optional
    req_event: dict[str, Any],
    *,
    action: str,
    actor: str,
    resource: str,
    decision: str = "allow",
    status: int = 200,
    team: str = "",
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    detail: str = "",
) -> AuditEvent:
```

Build an `AuditEvent`, pulling source IP and correlation id out of the request event.
`src/gwcore/audit.py:88`

### request_body

```py
def request_body(event: dict[str, Any]) -> str:
```

Return the request body, decoding base64 when the event's `isBase64Encoded` flag is set.
`src/gwcore/responses.py:117`

### AuditEvent

```py
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

A single audit record whose field order matches the Iceberg table schema.
`src/gwcore/audit.py:42`

### page

```py
def page(
    items: list[Any],
    last_key: dict[str, Any] | None = None,
    *,
    cache_seconds: int | None = None,
) -> dict[str, Any]:
```

Build a paginated list response of the shape `{items, next_cursor, count}` over a DynamoDB `LastEvaluatedKey`.
`src/gwcore/responses.py:133`

### require

```py
def require(
    principal: Principal,
    *,
    scopes: list[str] | None = None,
    tiers: list[str] | None = None,
    require_all_scopes: bool = False,
) -> None:
```

Enforce scope/tier requirements as the single authorization gate for the plane, raising `ForbiddenError` on failure.
`src/gwcore/auth.py:227`

### ADMIN_SCOPE

```py
ADMIN_SCOPE = "https://gateway.internal/admin"
```

The canonical admin OAuth scope string required by every control-plane admin route.
`src/gwcore/auth.py:37`

### authorize

```py
def authorize(
    principal: Principal,
    *,
    scopes: list[str] | None = None,
    tiers: list[str] | None = None,
    require_all_scopes: bool = False,
) -> bool:
```

Return True if the principal satisfies the scope/tier requirements as a pure predicate that never raises.
`src/gwcore/auth.py:203`

### TTLCache

```py
class TTLCache[T]:
    ...
    def __init__(self, *, default_ttl: float = 300.0, clock: _Clock = time.monotonic) -> None:
```

A thread-safe, in-process cache with per-key TTL used for warm-Lambda reuse of hot config and the JWKS key set.
`src/gwcore/cache.py:32`

### ConflictError

```py
class ConflictError(ControlPlaneError):
    """Resource state conflicts with the request (e.g. duplicate, version mismatch)."""

    status = 409
    code = "conflict"
```

A `ControlPlaneError` subclass raised when resource state conflicts with the request, mapped to HTTP 409.
`src/gwcore/errors.py:72`

### ForbiddenError

```py
class ForbiddenError(ControlPlaneError):
    """Authenticated but not permitted."""

    status = 403
    code = "forbidden"
```

A `ControlPlaneError` subclass raised when a caller is authenticated but not permitted, mapped to HTTP 403.
`src/gwcore/errors.py:58`

### INVOKE_SCOPE

```py
INVOKE_SCOPE = "https://gateway.internal/invoke"
```

The OAuth scope string carried by minted gateway tokens and required by the inference-adjacent usage API.
`src/gwcore/auth.py:38`

### messages_to_text

```py
def messages_to_text(messages: list[dict[str, Any]]) -> str:
```

Flatten message contents (string or OpenAI content-part list) into a single string for content scanning.
`src/gwcore/agentgateway.py:40`

### decode_claims

```py
def decode_claims(token: str) -> dict[str, Any]:
```

Decode a JWT payload without signature verification, safe only behind the API Gateway Cognito authorizer.
`src/gwcore/auth.py:89`

### encode_cursor

```py
def encode_cursor(last_key: dict[str, Any] | None) -> str | None:
```

Encode a DynamoDB `LastEvaluatedKey` into an opaque base64 pagination cursor.
`src/gwcore/responses.py:86`

### etag_for

```py
def etag_for(body: Any) -> str:
```

Compute a strong ETag (sha256 of the canonical JSON) for a response body.
`src/gwcore/responses.py:33`

### header_lookup

```py
def header_lookup(event: dict[str, Any], name: str) -> str:
```

Case-insensitive header lookup from a Lambda Function URL event, returning an empty string when absent.
`src/gwcore/agentgateway.py:64`

## HTTP

The data plane (`POST /v1/chat/completions`, `POST /v1/messages`) is served by agentgateway behind the ALB and is not implemented in this repository's Python; it is documented for completeness and cited to the admin guide. Every control-plane Lambda additionally serves `GET /health` (listed once). Routes are sorted by path ascending, then method.

### POST /

The budget-enforcement agentgateway guardrail webhook (Lambda Function URL): reads the posted messages, checks team/model budget, and returns a `pass`/`reject` action envelope.
`src/budget_enforcement/handler.py:380`

### POST /auth/token

Exchange a verified SSO session for a short-lived, audience-bound gateway JWT carrying the caller's team/cost_center/tier claims.
`src/admin_token/handler.py:109`

### GET /audit

Governed read of the control-plane audit trail for a team over a period, with ADR-008 tenant isolation for non-admins.
`src/budget_admin/handler.py:87`

### GET /budgets

List all budgets with gwcore cursor pagination.
`src/budget_admin/handler.py:90`

### POST /budgets

Create a new budget, failing with a conflict if one with the same ID already exists.
`src/budget_admin/handler.py:93`

### DELETE /budgets/{id}

Delete a budget by ID.
`src/budget_admin/handler.py:116`

### GET /budgets/{id}

Get a single budget by ID, including current-period usage.
`src/budget_admin/handler.py:112`

### PUT /budgets/{id}

Update a budget with partial fields via a DynamoDB UpdateItem expression.
`src/budget_admin/handler.py:114`

### GET /health

Unauthenticated liveness probe returning `{"status": "healthy"}`; served by every control-plane Lambda.
`src/budget_admin/handler.py:171`

### GET /pricing

List all prices with DynamoDB overrides merged over the static pricing table.
`src/pricing_admin/handler.py:260`

### DELETE /pricing/{provider}/{model}

Delete a pricing override, reporting whether a static fallback price remains.
`src/pricing_admin/handler.py:266`

### GET /pricing/{provider}/{model}

Get the effective price for a specific provider/model.
`src/pricing_admin/handler.py:262`

### PUT /pricing/{provider}/{model}

Upsert a pricing entry for a provider/model.
`src/pricing_admin/handler.py:264`

### GET /routing/configs

List all custom routing configs.
`src/routing_config/handler.py:284`

### POST /routing/configs

Create a custom routing config, persisted as the rendered agentgateway AI-backend shape.
`src/routing_config/handler.py:288`

### DELETE /routing/configs/{name}

Delete a custom routing config by name.
`src/routing_config/handler.py:292`

### GET /routing/configs/{name}

Get a specific custom routing config by name.
`src/routing_config/handler.py:286`

### PUT /routing/configs/{name}

Update a custom routing config by name.
`src/routing_config/handler.py:290`

### GET /teams

Return all active teams.
`src/team_registration/handler.py:56`

### POST /teams

Register a new team: create a Cognito client, store metadata, and seed a budget.
`src/team_registration/handler.py:54`

### DELETE /teams/{id}

Deactivate a team, deleting its Cognito client (revoking all tokens) and marking it inactive.
`src/team_registration/handler.py:62`

### GET /teams/{id}

Get team details, current usage, and budget.
`src/team_registration/handler.py:58`

### POST /teams/{id}/rotate

Rotate a team's Cognito client credentials (delete the old client, create a new one).
`src/team_registration/handler.py:60`

### GET /usage

Read a team's usage for the current period, trailing history, or per-model breakdown, with tenant isolation for non-admins.
`src/usage_api/handler.py:205`

### GET /usage/{scope}/{id}

Get usage for the current period for a given scope/entity.
`src/budget_admin/handler.py:102`

### GET /usage/{scope}/{id}/history

Get the daily usage breakdown for a scope/entity over a date range.
`src/budget_admin/handler.py:99`

### POST /v1/chat/completions

The OpenAI Chat Completions inference endpoint, served by agentgateway behind the ALB (not implemented in this repository).
`docs/src/content/docs/admin-guide/admin-api.md:24`

### POST /v1/messages

The Anthropic Messages inference endpoint, served by agentgateway behind the ALB (not implemented in this repository).
`docs/src/content/docs/admin-guide/admin-api.md:24`

## See also

- [insights/impact-analysis](../insights/impact-analysis.md) — 9 shared source citations
- [architecture/module-map](../architecture/module-map.md) — 8 shared source citations
- [insights/contract-map](../insights/contract-map.md) — 8 shared source citations
- [insights/debugging-guide](../insights/debugging-guide.md) — 7 shared source citations
- [behavior/processes](../behavior/processes.md) — 6 shared source citations
