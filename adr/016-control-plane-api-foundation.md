# ADR-016: Control-Plane API Foundation (`gwcore`)

**Status**: Accepted
**Date**: 2026-06-24
**Deciders**: AI Engineering NAMER
**Builds on**: ADR-005 (ALB JWT), ADR-008 (per-team clients), ADR-013 (SSO federation), ADR-014 (two-plane split)

## Context

The admin/control plane is eleven Lambda services (ADR-014). They share no code: each
re-implements JWT handling, response building, error mapping, and logging. Two concrete
problems result.

1. **Divergent, duplicated auth.** `budget_admin/auth.py` checks the `scope` claim for
   `"admin"`; `team_registration/auth.py` checks for `"https://gateway.internal/admin"`.
   Same intent, two different strings — a latent authorization inconsistency. Both decode
   the JWT with base64 only (no signature verification), which is correct *only* because
   API Gateway's Cognito authorizer already verified it upstream.
2. **No shared envelope, pagination, audit, or telemetry.** Every handler hand-rolls
   `_build_response`, returns ad-hoc error shapes, and emits unstructured logs. There is
   no audit trail for control-plane mutations and no consistent metrics.

As the portal (ADR-pending) turns these APIs into a human-facing surface, the plane needs a
foundation: one authentication/authorization path, a consistent response + pagination
contract, caching, an audit trail, and uniform observability.

## Decision

Introduce a shared package **`src/gwcore/`** (not `platform/` — that name shadows the stdlib
`platform` module under `pythonpath=["src"]` and breaks boto3). Every control-plane handler
imports `gwcore` instead of re-implementing primitives. `gwcore` ships seven modules:

| Module | Responsibility |
|---|---|
| `gwcore.auth` | Principal extraction; **two verification modes** (see below); unified scope+claim RBAC |
| `gwcore.responses` | Response envelope, typed errors → HTTP, cursor pagination |
| `gwcore.cache` | In-process TTL cache (warm-Lambda reuse) + read-through helper + ETag |
| `gwcore.audit` | Append-only audit events → Firehose (→ Iceberg, ADR-pending) |
| `gwcore.logging` | Structured JSON logs with correlation id (request id) |
| `gwcore.telemetry` | CloudWatch EMF metrics + OTEL GenAI-convention span attributes |
| `gwcore.errors` | Typed exception hierarchy mapped to HTTP status by `responses` |

### AuthN — two modes, one principal

The decisive design point: the control plane has **two ingress paths with different trust
properties**, so `gwcore.auth` supports two verification modes that both yield the same
`Principal` object.

- **`trusted_edge` mode** (default for the existing admin handlers): API Gateway's Cognito
  authorizer has already verified the JWT signature, audience, and expiry before invoking
  Lambda. The handler only needs to *read* claims. `gwcore` decodes the payload (base64, no
  re-verify) — preserving today's behavior but through one code path, not eleven.
- **`verify` mode** (for the token-exchange endpoint and any handler reachable without the
  authorizer): full RS256 signature verification against the Cognito JWKS, with `iss`/`exp`/
  `aud` checks. JWKS is fetched once and cached in-process (warm-Lambda reuse) with a TTL and
  a forced refresh on unknown-`kid`, so steady-state verification is zero-network.

A `Principal` carries `sub`, `team`, `cost_center`, `tenant_tier`, `scopes`, `client_id`,
and `token_use`. The IdP-group→claim mapping that populates `team`/`cost_center`/`tier`
already exists in the `pre_token` Lambda (ADR-013).

### AuthZ — unified, declarative

One `require(principal, scopes=..., tiers=...)` gate replaces the divergent string checks.
Scopes are matched against a canonical set; the historical `"admin"` and
`"https://gateway.internal/admin"` are both accepted during migration via an alias table, so
neither existing handler breaks. Authorization decisions emit an audit event regardless of
outcome (allow *and* deny), so denials are observable.

### Caching & performance

- **JWKS cache** (above) removes per-request network I/O from `verify` mode.
- **Read-through TTL cache** for hot, slowly-changing config (pricing table, routing configs,
  tier defaults): `gwcore.cache.read_through(key, loader, ttl)`. In-process only — survives
  across invocations on a warm Lambda, evaporates on cold start, no external dependency. This
  is deliberately *not* the response cache; LLM response caching stays at Redis (ADR-012).
- **HTTP caching**: GET responses carry an `ETag` (content hash) and honor `If-None-Match`
  → `304`, so the portal and CLIs avoid re-transferring unchanged config.
- **API Gateway stage cache** is enabled for idempotent GET routes (pricing, catalog) with a
  short TTL, taking read load off Lambda entirely for the hottest reads.
- **Pagination**: list endpoints return an opaque cursor (base64 of the DynamoDB
  `LastEvaluatedKey`), never offset — O(1) regardless of table size.
- Cold-start: `gwcore` has one third-party import (`pyjwt`); boto3 clients are created lazily
  and module-scoped for warm reuse.

### Audit, logging, monitoring

- **Audit** (`gwcore.audit`): every mutating control-plane call and every authz decision
  emits a structured `AuditEvent` (actor, action, resource, before/after where applicable,
  status, source IP, request id) to a Kinesis Firehose stream. Firehose lands it in Apache
  Iceberg on S3 Tables (ADR-pending) for ACID, compaction, and Athena queryability. Emission
  is best-effort and never fails the request.
- **Logging** (`gwcore.logging`): JSON logs with a per-request correlation id taken from the
  API Gateway request id, so a single request is greppable across handler + audit + metrics.
- **Monitoring** (`gwcore.telemetry`): CloudWatch EMF metric blocks (no `PutMetricData` call
  on the hot path) for request count, latency, authz-deny count, and per-route error rate;
  plus OTEL GenAI semantic-convention attributes so control-plane spans join the same trace
  namespace as the inference plane.

## Alternatives considered

| Option | Verdict |
|---|---|
| **Keep per-service auth, just fix the string mismatch** | Rejected. Fixes one bug, leaves the duplication and the missing audit/telemetry. The portal needs a consistent contract across all routes. |
| **A Lambda layer instead of a `src/` package** | Rejected for now. A layer decouples deploy cadence but complicates local `pytest` (`pythonpath=["src"]` already makes a `src/` package importable in tests with zero packaging). Revisit if cold-start size becomes an issue. |
| **Powertools for AWS Lambda (Python)** | Strong option — it ships EMF metrics, structured logging, and a JWT/authz helper. Rejected as a hard dependency to keep the supply-chain surface minimal (ADR-001/004 ethos) and avoid a large import on cold start, but `gwcore`'s telemetry/logging deliberately mirror Powertools' EMF + structured-log shapes so a later swap is mechanical. |
| **API Gateway Lambda authorizer (custom) instead of Cognito authorizer** | Rejected. The Cognito `COGNITO_USER_POOLS` authorizer already validates signature + scopes natively (ADR-014); a custom authorizer would re-implement that and add latency. `gwcore`'s `verify` mode covers only the non-authorizer paths. |
| **DAX / ElastiCache for the read-through cache** | Rejected. The hot config is tiny and slowly-changing; an in-process TTL cache on warm Lambdas plus the API Gateway stage cache covers it without new infrastructure or per-request network cost. |

## Consequences

**Positive**: one authentication/authorization path (closes the scope-mismatch bug); a
consistent response + error + pagination contract the portal can target; an audit trail and
uniform metrics the plane never had; near-zero added latency (in-process caches, EMF, lazy
clients). Existing handlers migrate incrementally — `gwcore` accepts both legacy scope
strings during the transition.

**Negative**: one new shared package to own and version; handlers must be refactored to adopt
it (incremental, not big-bang); `pyjwt[crypto]` is a new runtime dependency (small, widely
used, pulls in `cryptography`).

**Neutral**: `gwcore` is import-only with no I/O at import time, so it does not change cold-start
behavior beyond the `pyjwt` import.

## Sources

- ADR-013 — Identity Center SAML/OIDC federation + `pre_token` claim mapping
- ADR-014 — Two-plane split (API Gateway + Cognito authorizer for the admin plane)
- AWS — InvokeGuardrailChecks, CloudWatch EMF, API Gateway stage caching, S3 Tables (Iceberg)
- The existing divergent `budget_admin/auth.py` and `team_registration/auth.py`
