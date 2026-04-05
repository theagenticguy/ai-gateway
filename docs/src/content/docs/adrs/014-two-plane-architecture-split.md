---
title: "ADR-014: Two-Plane Architecture Split"
description: ALB for inference path, API Gateway for admin APIs — eliminates duplicated JWT validation across Lambda handlers.
sidebar:
  order: 14
---

**Status**: Accepted
**Date**: 2026-03-26
**Deciders**: AI Engineering NAMER
**Supersedes**: Partially refines ADR-005 (ALB JWT for inference path remains unchanged)

## Context

ADR-005 chose ALB JWT validation over API Gateway to eliminate per-request costs on the inference path. The C-Series refinements (C.1-C.5) introduce admin and secondary APIs:

- **C.2 Usage API** -- teams query their own usage (read-only)
- **C.3 Pricing Admin** -- operators manage dynamic pricing overrides (CRUD)
- **Team Registration** -- self-service team onboarding
- **Budget Admin** -- budget CRUD
- **Routing Config** -- routing rule management
- **Content Scanner** -- guardrails configuration

These admin endpoints were initially deployed as Lambda Function URLs with hand-rolled JWT validation (`validate_admin_scope()` in each handler). This created two problems:

1. **Auth duplication**: Every admin handler re-implemented JWT extraction, scope validation, and error formatting. A bug in one handler's auth check could silently bypass authorization.
2. **Inconsistent enforcement**: Lambda Function URLs have no built-in auth layer -- authorization depends entirely on application code running correctly.

## Decision

Split the architecture into two planes:

| Plane | Transport | Auth | Traffic Pattern |
|---|---|---|---|
| **Inference** | ALB with `validate_token` | ALB-native JWT validation | High-volume, latency-sensitive |
| **Admin** | API Gateway REST API | Cognito Authorizer (`COGNITO_USER_POOLS`) | Low-volume, correctness-sensitive |

All admin endpoints move behind a single API Gateway REST API with a Cognito authorizer. The ALB continues handling the inference path (`/v1/chat/completions`, `/v1/messages`).

### Admin API Route Map

| Path | Lambda | Purpose |
|---|---|---|
| `/teams` | team_registration | Self-service onboarding |
| `/budgets` | budget_admin | Budget CRUD |
| `/routing` | routing_config | Routing rule management |
| `/scanner` | content_scanner | Guardrails configuration |
| `/pricing` | pricing_admin | Dynamic pricing overrides |
| `/usage` | usage_api | Real-time usage self-service |

Each path prefix gets a `{proxy+}` child resource for sub-paths, with `ANY` methods and `AWS_PROXY` Lambda integrations.

## Consequences

**Positive**:
- Auth is enforced once at the gateway layer -- individual Lambda handlers drop their auth code. API Gateway rejects unauthorized requests before they reach Lambda.
- Single Cognito authorizer with `authorization_scopes` covers all admin endpoints uniformly.
- Admin APIs gain API Gateway features for free: access logging, CloudWatch metrics, request throttling, WAF attachment if needed later.
- Feature-flagged via `enable_admin_api` variable -- can be enabled per environment.

**Negative**:
- API Gateway adds ~10-15ms latency to admin calls (acceptable for admin traffic).
- API Gateway REST API cost: $3.50/million requests. At admin-level traffic (<10K req/day), cost is negligible (~$1/month).
- One more infrastructure module to maintain (`modules/admin_api`).

**Neutral**:
- Lambda handlers retain their business logic unchanged -- only the auth check is removed.
- The inference path (ALB) is unaffected.

## Alternatives Considered

1. **Keep Lambda Function URLs with per-handler auth** -- rejected because auth duplication is a security liability and maintenance burden.
2. **API Gateway HTTP API** -- considered, but REST API provides a native `COGNITO_USER_POOLS` authorizer with built-in `authorization_scopes` enforcement. HTTP API's JWT authorizer requires a custom Lambda authorizer to enforce Cognito scopes.
3. **Single API Gateway for everything (inference + admin)** -- rejected per ADR-005 reasoning: API Gateway on the inference path adds $260-2,400/month and 10-15ms latency for zero benefit since ALB JWT validation handles it natively.
