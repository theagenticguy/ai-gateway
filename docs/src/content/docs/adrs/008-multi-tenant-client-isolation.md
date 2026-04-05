---
title: "ADR-008: Multi-Tenant Client Isolation"
description: Per-team Cognito app clients for credential isolation, scoped access, and independent rotation.
sidebar:
  order: 8
---

**Status**: Accepted
**Date**: 2026-03-20
**Deciders**: AI Engineering NAMER

## Context

The AI Gateway's authentication layer (ADR-005, ADR-007) uses a single Cognito User Pool with ALB JWT validation. Currently, there is exactly one hardcoded M2M app client (`gateway_m2m`) that all consumers share.

This single-client model creates several problems as adoption grows:

- **No credential isolation**: If one team's credentials are compromised, all teams are affected and the shared credential must be rotated.
- **No per-team audit trail**: CloudTrail and Cognito logs show the same `client_id` for every request, making it impossible to attribute usage to specific teams.
- **No per-team scope control**: Every consumer gets the same OAuth scopes. There is no way to grant `admin` scope to the platform team while limiting other teams to `invoke` only.
- **No independent rotation**: Rotating credentials requires coordinating with every consumer simultaneously.

## Decision

Introduce a `clients` Terraform module (`infrastructure/modules/clients/`) that creates per-team Cognito app clients from a configurable map (`client_configs`). Each team gets:

- Its own `aws_cognito_user_pool_client` with `client_credentials` grant
- Team-specific OAuth scopes (subset of the resource server's available scopes)
- Independent client ID and secret for credential rotation

The module is opt-in: it only creates resources when `client_configs` is non-empty. The existing `gateway_m2m` client in the auth module is preserved for backward compatibility.

### Configuration Example

```hcl
client_configs = {
  platform = {
    allowed_scopes = ["https://gateway.internal/invoke", "https://gateway.internal/admin"]
    description    = "Platform engineering team"
  }
  ml-training = {
    allowed_scopes = ["https://gateway.internal/invoke"]
    description    = "ML training pipeline service account"
  }
}
```

## Consequences

**Positive**:
- Credential compromise is isolated to one team; other teams are unaffected.
- Per-team `client_id` in JWT claims enables attribution in CloudTrail, ALB access logs, and application-layer metrics.
- Teams can be granted different scope sets (e.g., only platform gets `admin`).
- Credential rotation is per-team with no cross-team coordination.
- Adding or removing a team is a single Terraform variable change.

**Negative**:
- Client lifecycle management is now required: teams must be onboarded/offboarded via Terraform.
- The number of Cognito app clients grows linearly with teams (Cognito supports up to 1,000 per user pool, which is sufficient).
- Client secrets are stored in Terraform state; state encryption and access controls must be enforced.

## Alternatives Considered

| Approach | Verdict |
|---|---|
| API keys at application layer | Rejected: duplicates Cognito's capability, no JWT validation at ALB |
| Single client + custom claims Lambda | Rejected: still shares credentials, adds Lambda cold-start latency |
| Separate Cognito User Pool per team | Rejected: over-isolated, complicates ALB listener config (one JWKS per pool) |

## Sources

- ADR-005: ALB JWT Validation Over API Gateway
- ADR-007: Terraform Provider Upgrade for JWT
- [Cognito quotas: App clients per user pool](https://docs.aws.amazon.com/cognito/latest/developerguide/limits.html) (1,000 default)
