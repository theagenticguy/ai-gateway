# ai-gateway · Impact analysis

This file answers one question: *if I touch X, what else do I have to think about?*

**"High-impact surface"** here means a symbol or module whose change ripples outward across the codebase, ranked by **inbound direct-import count from the shared `gwcore` package plus preamble-flagged data-plane contracts**. `ai-gateway` is a set of 12 single-purpose Lambda services (`src/*/handler.py`) sitting on one shared foundation package, `src/gwcore/` (`src/gwcore/__init__.py:1`). Every service handler imports from `gwcore`, so a signature change to a `gwcore` export is the widest blast radius in the repo. The counts below come from `codegraph impact <symbol>` cross-checked against `grep -rn "from gwcore" src` — the number of *service files* that directly import a submodule is the selection metric, because that is the count of Lambdas that must be re-tested and re-deployed on a breaking change.

The eight surfaces are the six `gwcore` submodules with the highest service-file fan-in (`logging` 12, `telemetry` 11, `audit` 11, `auth` 9, `errors` 9, `responses` 8), plus two preamble-flagged contract surfaces: the agentgateway config renderer (`routing_config.models.RoutingConfig.to_agentgateway_backend`) and the agentgateway guardrail-webhook helpers (`gwcore.agentgateway`). `gwcore.cache.TTLCache` has a high raw `codegraph impact` number (147) but that is transitive module-reachability noise: its only in-`src` caller is `gwcore.auth`, so it is routed to `## Other notable surfaces`.

In the tables below, a "service" is one Lambda under `src/<name>/`. Where a `gwcore` change would force an edit to every service that imports it, individual rows are collapsed into one summary row citing the shared import line, per the packet's too-many-consumers fallback.

## gwcore.auth — Principal, build_principal, require, verify_token

Defined at: `src/gwcore/auth.py:46` (`Principal`), `src/gwcore/auth.py:131` (`build_principal`), `src/gwcore/auth.py:227` (`require`), `src/gwcore/auth.py:164` (`verify_token`)

The single authentication/authorization path for the control plane (ADR-016). `build_principal(event)` normalizes an authorizer-verified request into a frozen `Principal`; `require(...)` is the one authorization gate; `verify_token(...)` does full RS256 JWKS verification for the token-exchange path.

| Downstream | Type | Touch on change | Citation |
| --- | --- | --- | --- |
| `admin_token/handler.py` (`_principal:72`, `handler:109`, `verify_token` at `:85`) | direct import | yes | `src/admin_token/handler.py:30` |
| `budget_admin/handler.py` (`handler:164`) | direct import | yes | `src/budget_admin/handler.py:38` |
| `budget_admin/routes.py` | direct import | yes | `src/budget_admin/routes.py:27` |
| `pricing_admin/handler.py` (`handler:245`) | direct import | yes | `src/pricing_admin/handler.py:26` |
| `routing_config/handler.py` (`handler:269`, gate at `:280`-`281`) | direct import | yes | `src/routing_config/handler.py:27` |
| `team_registration/handler.py` (`handler:67`) | direct import | yes | `src/team_registration/handler.py:23` |
| `team_registration/routes.py` | direct import | yes | `src/team_registration/routes.py:23` |
| `usage_api/handler.py` (`handler:205`) | direct import | yes | `src/usage_api/handler.py:23` |
| `gwcore/__init__.py` re-exports `Principal`, `authorize`, `build_principal`, `require` | direct import | yes | `src/gwcore/__init__.py:13` |
| `gwcore/cache.py` `TTLCache` backs the module-scoped JWKS cache | indirect | likely | `src/gwcore/auth.py:43` |
| `tests/test_gwcore.py` (build_principal, authorize, require, verify_token cases) | test | yes | `tests/test_gwcore.py:194` |
| `tests/test_admin_token.py` (imports `Principal` directly) | test | yes | `tests/test_admin_token.py:94` |
| `tests/test_budget_admin.py` (cross-team isolation guard cases) | test | likely | `tests/test_budget_admin.py:595` |

Blast-radius notes:

- `Principal` is a **frozen dataclass** (`src/gwcore/auth.py:46`); consumers read `principal.sub`, `.team`, `.scopes`, `.tenant_tier` as immutable attributes. Adding a required field with no default breaks `_principal_from_claims` (`src/gwcore/auth.py:75`) and every construction site; adding a field with a default is safe.
- `is_admin` treats both the canonical `https://gateway.internal/admin` scope and the legacy bare `"admin"` string as admin, via `_ADMIN_SCOPE_ALIASES` (`src/gwcore/auth.py:39`, `:62`). Removing the legacy alias silently de-authorizes any caller still holding the old scope — a behavioral break with no signature change.
- `build_principal` **prefers `requestContext.authorizer.claims` and only falls back to decoding the bearer payload** (`src/gwcore/auth.py:138`-`152`); it does *not* verify signatures (that is the authorizer's job). `verify_token` is the only path that verifies. Conflating the two, or making `build_principal` raise where it currently returns claims, changes the auth contract for all 8 admin/API handlers.

## gwcore.responses — ok, error_response, page, parse_cursor, request_body

Defined at: `src/gwcore/responses.py:39` (`ok`), `src/gwcore/responses.py:74` (`error_response`), `src/gwcore/responses.py:133` (`page`), `src/gwcore/responses.py:94` (`parse_cursor`), `src/gwcore/responses.py:117` (`request_body`)

The one wire-shape contract for every handler: success envelope, error mapping, and opaque-cursor pagination. `codegraph impact ok` reports 49 affected symbols and `request_body` 23 — the widest response-layer fan-out.

| Downstream | Type | Touch on change | Citation |
| --- | --- | --- | --- |
| `admin_token/handler.py` (`responses`, `error_response`) | direct import | yes | `src/admin_token/handler.py:30` |
| `budget_admin/handler.py` (`ok`, `page`, `responses`) | direct import | yes | `src/budget_admin/handler.py:38` |
| `budget_admin/routes.py` (`ok`, `page`, `parse_cursor`, `request_body`) | direct import | yes | `src/budget_admin/routes.py:27` |
| `pricing_admin/handler.py` (`ok`, `responses`, `request_body`) | direct import | yes | `src/pricing_admin/handler.py:26` |
| `routing_config/handler.py` (`ok`, `responses`, `request_body`) | direct import | yes | `src/routing_config/handler.py:27` |
| `team_registration/handler.py` (`ok`, `responses`) | direct import | yes | `src/team_registration/handler.py:23` |
| `team_registration/routes.py` (`ok`, `request_body`) | direct import | yes | `src/team_registration/routes.py:23` |
| `usage_api/handler.py` (`ok`, `responses`) | direct import | yes | `src/usage_api/handler.py:23` |
| `gwcore/__init__.py` re-exports `ok`, `error_response`, `page`, `parse_cursor`, `request_body` | direct import | yes | `src/gwcore/__init__.py:22` |
| `clients/admin_cli/admin_cli/client.py` parses the response envelope over HTTP | runtime dispatch | likely | `clients/admin_cli/admin_cli/client.py:5` |
| `tests/test_gwcore.py`, `tests/test_budget_admin.py` (imports `encode_cursor`), `tests/test_pricing_admin.py`, `tests/test_routing_config.py`, `tests/test_usage_api.py` | test | yes | `tests/test_budget_admin.py:32` |

Blast-radius notes:

- `ok`/`error_response`/`page` return the API Gateway proxy shape `{statusCode, headers, body}` where `body` is a JSON **string** (`src/gwcore/responses.py:71`, `:80`, `:145`); handlers return these dicts verbatim to Lambda. Changing the envelope keys or making `body` a dict breaks every handler's return contract *and* the out-of-repo `admin_cli`, which decodes the envelope over the wire (`clients/admin_cli/admin_cli/client.py:5`).
- Cursor pagination is symmetric and opaque: `parse_cursor` decodes what `encode_cursor` produced (base64 of a DynamoDB `LastEvaluatedKey`) and **tolerates unpadded input** (`src/gwcore/responses.py:104`-`105`). A malformed cursor raises `ValidationFailedError`, not a 500 (`src/gwcore/responses.py:109`). Changing the encoding invalidates cursors already handed to clients.
- `ok(..., etag=True)` returns a bodiless `304` when `if_none_match` matches, and ETags are the sha256 of canonical sorted-key JSON (`src/gwcore/responses.py:29`, `:65`-`69`). Any change to `_dumps` serialization silently changes every ETag and breaks conditional-GET caching.

## gwcore.errors — ControlPlaneError hierarchy

Defined at: `src/gwcore/errors.py:13`

The typed exception hierarchy handlers raise; `error_response` maps each to its HTTP status + stable `code`. `codegraph impact ControlPlaneError` reports 54 affected symbols — the largest single count in the repo, because every handler both raises subclasses and catches the base in its outer `try`.

| Downstream | Type | Touch on change | Citation |
| --- | --- | --- | --- |
| `gwcore/responses.py` `error_response` reads `exc.status` / `exc.to_body()` | direct import | yes | `src/gwcore/responses.py:23` |
| `admin_token/handler.py` | direct import | yes | `src/admin_token/handler.py:30` |
| `budget_admin/handler.py`, `budget_admin/routes.py`, `budget_admin/audit_query.py` | direct import | yes | `src/budget_admin/handler.py:38` |
| `pricing_admin/handler.py` | direct import | yes | `src/pricing_admin/handler.py:26` |
| `routing_config/handler.py` (raises + catches base at `:295`) | direct import | yes | `src/routing_config/handler.py:27` |
| `team_registration/handler.py`, `team_registration/routes.py` | direct import | yes | `src/team_registration/handler.py:23` |
| `usage_api/handler.py` | direct import | yes | `src/usage_api/handler.py:23` |
| `gwcore/__init__.py` re-exports 6 error classes | direct import | yes | `src/gwcore/__init__.py:14` |
| `clients/admin_cli/admin_cli/client.py` maps the `error.code`/`error.message` envelope to `GatewayError` | runtime dispatch | likely | `clients/admin_cli/admin_cli/client.py:270` |
| `tests/test_gwcore.py`, `tests/test_budget_admin.py` (imports `errors` in multiple cases) | test | yes | `tests/test_gwcore.py:19` |

Blast-radius notes:

- Each subclass owns a class-level `status` and `code` (e.g. `ValidationFailedError` → 400/`validation_failed`, `UnauthorizedError` → 401/`unauthorized`; `src/gwcore/errors.py:44`-`83`). The `code` string is a **stable machine-readable API contract** — the `admin_cli` and any client branch on it (`clients/admin_cli/admin_cli/client.py:270`). Renaming a `code` is a client-visible breaking change even though no Python signature changes.
- `to_body()` fixes the error envelope shape to `{"error": {"code", "message", "details?}}` (`src/gwcore/errors.py:36`-`41`). `error_response` depends on this exact method name and shape (`src/gwcore/responses.py:79`); changing it breaks response mapping for all handlers at once.
- `UpstreamError` (502) is defined but **not exported from `__init__`** (`src/gwcore/errors.py:79` vs `src/gwcore/__init__.py:14`); handlers reach it via `errors.UpstreamError` (e.g. `src/routing_config/handler.py:173`). Adding it to `__all__` is safe; removing the class breaks the `from gwcore import errors` callers directly.

## gwcore.audit — AuditEvent, emit, event_from_request

Defined at: `src/gwcore/audit.py:42` (`AuditEvent`), `src/gwcore/audit.py:64` (`emit`), `src/gwcore/audit.py:88` (`event_from_request`)

Append-only audit trail: every mutating call and every allow/deny authz decision emits a structured `AuditEvent` to Kinesis Firehose → Iceberg on S3 Tables (ADR-016). `codegraph impact event_from_request` reports 25 affected symbols; 11 service files import `audit`.

| Downstream | Type | Touch on change | Citation |
| --- | --- | --- | --- |
| `admin_token/handler.py` | direct import | yes | `src/admin_token/handler.py:30` |
| `budget_admin/handler.py`, `budget_admin/routes.py`, `budget_admin/audit_query.py` | direct import | yes | `src/budget_admin/handler.py:38` |
| `budget_enforcement/handler.py` | direct import | yes | `src/budget_enforcement/handler.py:48` |
| `pricing_admin/handler.py` | direct import | yes | `src/pricing_admin/handler.py:26` |
| `routing_config/handler.py` (`_audit` at `:111`-`112`, deny path `:302`) | direct import | yes | `src/routing_config/handler.py:27` |
| `team_registration/handler.py`, `team_registration/routes.py` | direct import | yes | `src/team_registration/handler.py:23` |
| `usage_api/handler.py` | direct import | yes | `src/usage_api/handler.py:23` |
| `budget_admin/models.py` schema mirrors `AuditEvent` field order | indirect | likely | `src/budget_admin/models.py:113` |
| `tests/test_gwcore.py` (audit emit/event cases) | test | yes | `tests/test_gwcore.py:19` |

Blast-radius notes:

- `emit` is **best-effort and never raises**: a Firehose failure is logged and swallowed, and an unset `AUDIT_FIREHOSE_STREAM` logs-and-drops with a `False` return (`src/gwcore/audit.py:64`-`85`). Handlers rely on this — they call `audit.emit(...)` without a `try`. Making `emit` raise would fail live requests on an audit outage.
- `AuditEvent` field order is declared to **match the Iceberg table schema** (`src/gwcore/audit.py:44`), and `budget_admin/audit_query.py` reads that table back. `budget_admin/models.py:113` documents columns that mirror `AuditEvent`. Reordering or renaming fields desyncs the write path from the Athena/Iceberg read path — a cross-system break the type checker won't catch.
- `event_from_request` pulls `source_ip` and `correlation_id` from `requestContext` with cross-event-source fallbacks (`identity.sourceIp` vs `http.sourceIp`, `requestId` vs `request_id`; `src/gwcore/audit.py:102`-`106`). Handlers pass the raw event through and trust this extraction; changing the keys silently drops attribution.

## gwcore.telemetry — emit_metric, Timer

Defined at: `src/gwcore/telemetry.py:19` (`emit_metric`), `src/gwcore/telemetry.py:54` (`Timer`)

CloudWatch EMF metrics + OTEL GenAI span attributes. Metrics are emitted as structured stdout log lines (no `PutMetricData` on the hot path, ADR-016). `codegraph impact emit_metric` reports 63 affected symbols; 11 service files import `telemetry` — the single most-imported behavioral helper after logging.

| Downstream | Type | Touch on change | Citation |
| --- | --- | --- | --- |
| `admin_token/handler.py` (`Timer`, `emit_metric`) | direct import | yes | `src/admin_token/handler.py:32` |
| `budget_admin/handler.py` | direct import | yes | `src/budget_admin/handler.py:40` |
| `budget_enforcement/handler.py` | direct import | yes | `src/budget_enforcement/handler.py:50` |
| `chargeback_report/handler.py` | direct import | yes | `src/chargeback_report/handler.py:26` |
| `cost_attribution/handler.py` | direct import | yes | `src/cost_attribution/handler.py:35` |
| `pre_token/handler.py` (`emit_metric` only) | direct import | yes | `src/pre_token/handler.py:24` |
| `pricing_admin/handler.py` | direct import | yes | `src/pricing_admin/handler.py:29` |
| `rate_limiter/handler.py` (`emit_metric` only) | direct import | yes | `src/rate_limiter/handler.py:29` |
| `routing_config/handler.py` (`_surface_migration_warnings:126`, deny path `:297`) | direct import | yes | `src/routing_config/handler.py:30` |
| `team_registration/handler.py`, `usage_api/handler.py` | direct import | yes | `src/team_registration/handler.py:25` |
| `tests/test_gwcore.py` + 8 per-service test files assert emitted metrics | test | yes | `tests/test_gwcore.py:19` |

Blast-radius notes:

- `emit_metric` **prints the EMF JSON to stdout and returns the dict** (`src/gwcore/telemetry.py:50`-`51`). CloudWatch materializes the metric only if the stdout shape is exactly the EMF `_aws.CloudWatchMetrics` schema; the return value exists purely so tests can assert without parsing stdout. Any change to the printed structure silently stops metrics from being ingested — no error, no test failure unless tests read stdout.
- `Timer` is a context manager that emits a `Milliseconds` latency metric **on `__exit__`** (`src/gwcore/telemetry.py:81`-`89`); handlers wrap their whole request body in `with Timer("RequestLatency", route=...)` (e.g. `src/routing_config/handler.py:279`). Changing `Timer` to require positional args or to not emit on exit breaks latency dashboards for every service.
- `genai_attributes` (`src/gwcore/telemetry.py:92`) has only 2 affected symbols (`codegraph impact`) — self + one test — so it is low-impact; the surface's blast radius is carried by `emit_metric` and `Timer`.

## gwcore.logging — get_logger, correlation_id, bind

Defined at: `src/gwcore/logging.py:39` (`get_logger`), `src/gwcore/logging.py:51` (`correlation_id`), `src/gwcore/logging.py:57` (`bind`)

Structured JSON logging with a per-request correlation id. This is the single most-imported submodule: **all 12 service files** import at least `get_logger` (`grep -rn "from gwcore.logging"`). `codegraph impact get_logger` reports 22 affected symbols.

| Downstream | Type | Touch on change | Citation |
| --- | --- | --- | --- |
| `admin_token/handler.py` (`bind`, `correlation_id`, `get_logger`) | direct import | yes | `src/admin_token/handler.py:31` |
| `budget_admin/handler.py`, `budget_admin/audit_query.py` | direct import | yes | `src/budget_admin/handler.py:39` |
| `budget_enforcement/handler.py` | direct import | yes | `src/budget_enforcement/handler.py:49` |
| `chargeback_report/handler.py`, `cost_attribution/handler.py` (`get_logger` only) | direct import | yes | `src/chargeback_report/handler.py:25` |
| `pre_token/handler.py`, `rate_limiter/handler.py` (`get_logger` only) | direct import | yes | `src/pre_token/handler.py:23` |
| `pricing_admin/handler.py`, `routing_config/handler.py`, `team_registration/handler.py`, `usage_api/handler.py` (`bind`, `correlation_id`, `get_logger`) | direct import | yes | `src/routing_config/handler.py:28` |
| `gwcore/audit.py` uses `get_logger` for its own logger | indirect | yes | `src/gwcore/audit.py:22` |
| `tests/test_gwcore.py` (imports `logging as gwlog`) + `tests/test_budget_admin.py`, `test_budget_enforcement.py`, `test_pricing_admin.py`, `test_routing_config.py`, `test_team_registration.py`, `test_usage_api.py` (assert `correlation_id`/`bind`) | test | yes | `tests/test_gwcore.py:20` |

Blast-radius notes:

- `get_logger` is **idempotent on warm Lambda**: it only adds a `StreamHandler` if none exists and sets `propagate = False` (`src/gwcore/logging.py:42`-`47`). Handlers call it at module scope. Removing the idempotency guard would duplicate log lines on every warm invocation.
- Structured fields ride in via `extra={"fields": {...}}` and are merged into the JSON payload by `JsonFormatter.format` (`src/gwcore/logging.py:31`-`33`); `gwcore.audit` uses this exact convention to log the audit record on Firehose miss (`src/gwcore/audit.py:74`). Changing the `"fields"` key name breaks structured logging across all callers.
- `correlation_id` reads `requestContext.requestId` with a `request_id` fallback (`src/gwcore/logging.py:53`-`54`) — the same id `audit.event_from_request` uses (`src/gwcore/audit.py:106`). Changing this de-correlates logs from audit events for a single request.

## routing_config.models.RoutingConfig.to_agentgateway_backend — agentgateway config rendering

Defined at: `src/routing_config/models.py:119`

Renders a stored routing config into agentgateway's `ai.groups` backend shape (ADR-017) — the boundary between the control plane and the data plane. Its lossy render is paired with `migration_warnings()` (`src/routing_config/models.py:169`), which the handler surfaces to callers, logs, and meters.

| Downstream | Type | Touch on change | Citation |
| --- | --- | --- | --- |
| `routing_config/handler.py` `_put_custom_config` persists the rendered shape to DynamoDB | direct import | yes | `src/routing_config/handler.py:64`, `:70` |
| `routing_config/handler.py` `_create_config` returns the render in the API body | direct import | yes | `src/routing_config/handler.py:210` |
| `routing_config/handler.py` `_update_config` returns the render in the API body | direct import | yes | `src/routing_config/handler.py:245` |
| `routing_config/handler.py` `_surface_migration_warnings` calls `migration_warnings()` + emits `RoutingConfigMigrationWarning` metric | direct import | yes | `src/routing_config/handler.py:124`, `:126` |
| agentgateway runtime (out-of-repo) consumes the persisted `ai.groups` YAML/JSON | runtime dispatch | likely | `src/routing_config/models.py:119`-`138` |
| `tests/test_routing_config.py` (`test_to_agentgateway_*`, migration-warning cases) | test | yes | `tests/test_routing_config.py:196` |

Blast-radius notes:

- The render is **deliberately lossy**: conditional predicates, `on_status_codes`, 0-1 weight ratios, per-target `retry`, and `virtual_key` have no agentgateway equivalent and are dropped (`src/routing_config/models.py:119`-`167`). `migration_warnings()` is the contract that makes each loss visible (`src/routing_config/models.py:169`-`219`). Any new field on `RoutingTarget`/`RoutingStrategy` that agentgateway can't express must gain a matching warning here, or the loss goes silent.
- The `provider_key` map (`src/routing_config/models.py:140`-`147`) hard-codes the provider-name translation (`openai` → `openAI`, `azure-openai`/`azure` → `azure`, `google` → `gemini`). Bedrock targets additionally get `policies.backendAuth.aws` injected (`src/routing_config/models.py:156`-`158`). Changing these keys changes the config agentgateway actually loads — a data-plane routing change with no control-plane API change.
- The rendered dict is persisted as `config_json` in DynamoDB (`src/routing_config/handler.py:70`), so a render-shape change is **not retroactive**: already-stored configs keep the old shape until re-`PUT`. Migrations must re-render existing rows.

## gwcore.agentgateway — guardrail-webhook contract helpers

Defined at: `src/gwcore/agentgateway.py:29` (`extract_messages`), `:58` (`estimate_tokens`), `:64` (`header_lookup`), `:95` (`pass_action`), `:100` (`reject_action`), `:105` (`mask_action`)

The Lambda-side helpers that speak agentgateway's guardrail-webhook contract (ADR-017): pull messages/tokens/identity out of the request, and shape the `pass`/`mask`/`reject` action envelope agentgateway expects. Only one service consumes them today, but that service is the **request-time enforcement gate** on the data plane.

| Downstream | Type | Touch on change | Citation |
| --- | --- | --- | --- |
| `budget_enforcement/handler.py` (`extract_messages:422`, `header_lookup:423`-`424`, `estimate_tokens:428`, `pass_action:440`, `reject_action:444`) | direct import | yes | `src/budget_enforcement/handler.py:48` |
| agentgateway runtime (out-of-repo) POSTs the request and reads the action envelope | runtime dispatch | likely | `src/gwcore/agentgateway.py:79`-`92` |
| `tests/test_agentgateway_contract.py` (imports `agentgateway`, exercises all helpers) | test | yes | `tests/test_agentgateway_contract.py:13` |

Blast-radius notes:

- The response builders always return **HTTP 200** with the decision carried inside `{"action": {...}}` in the body (`src/gwcore/agentgateway.py:79`-`92`). A 4xx would read to agentgateway as a *hook failure* rather than a deny (`src/gwcore/agentgateway.py:84`-`85`). Changing `_envelope` to return a non-200 status would silently disable enforcement (fail-open).
- `extract_messages`, `messages_to_text`, and `header_lookup` are **tolerant of malformed input** — they return `[]`/`""` rather than raising (`src/gwcore/agentgateway.py:35`-`37`, `:64`-`73`) — so `budget_enforcement` degrades gracefully instead of 500-ing the guardrail call. Making them strict would turn a malformed body into a hook failure.
- `estimate_tokens` uses a fixed 4-chars-per-token heuristic (`src/gwcore/agentgateway.py:26`, `:58`-`61`) and is explicitly the *pre-request* estimate; the authoritative count comes post-hoc from `cost_attribution`. Callers must not treat this as exact — a budget gate tuned against it will drift if the constant changes.

## Other notable surfaces

- `gwcore.cache.TTLCache` — defined at `src/gwcore/cache.py:32`. `codegraph impact` reports 147 (transitive noise); `codegraph callers TTLCache` and `grep -rn TTLCache src` confirm the only in-`src` caller is the JWKS cache in `gwcore.auth` (`src/gwcore/auth.py:43`). Internal-only; a change ripples to `auth` (and its downstream) but no service imports it directly.
- `budget_enforcement.jwt_utils` — defined at `src/budget_enforcement/jwt_utils.py:17`. A local decode-only JWT helper used solely by `budget_enforcement/handler.py:33`; not shared, parallel to `gwcore.auth.decode_claims`.
- Per-service Pydantic `models.py` — each service owns its own request/response models (e.g. `pricing_admin/models.py`, `team_registration/models.py`); changing one is scoped to that service. `RoutingConfig` is the exception (a shared data-plane-contract model), covered as its own surface above.
- `gwcore.auth.authorize` — the pure-predicate form of `require` (`src/gwcore/auth.py:203`); `require` is the raising gate handlers actually call, so `authorize` is covered indirectly by the auth surface.

## See also

- [architecture/module-map](../architecture/module-map.md) — 13 shared source citations
- [insights/contract-map](contract-map.md) — 12 shared source citations
- [behavior/processes](../behavior/processes.md) — 10 shared source citations
- [reference/public-api](../reference/public-api.md) — 9 shared source citations
- [insights/debugging-guide](debugging-guide.md) — 8 shared source citations
