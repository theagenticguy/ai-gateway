---
title: "ADR-005: ALB JWT Validation Over API Gateway"
description: Uses ALB-native JWT validation instead of API Gateway, saving $260-2,400/month with zero additional latency.
sidebar:
  order: 5
---

**Status**: Proposed
**Date**: 2026-03-18
**Deciders**: AI Engineering NAMER

## Context

We need JWT-based authentication for the LLM gateway. The initial spec proposed API Gateway HTTP API ($260+/mo at 100 req/s) as the JWT validation layer because ALB's native auth was cookie-based (browser only).

However, AWS launched **ALB JWT Verification** in November 2025 (GA in all regions). This is a new `validate_token` listener action that validates Bearer JWTs directly at the ALB -- no cookies, no redirects, no API Gateway needed.

## Decision

Use **ALB native JWT validation** (`validate_token` action) instead of API Gateway HTTP API for JWT authentication.

## How ALB JWT Validation Works

1. Client sends `Authorization: Bearer <jwt>` to ALB
2. ALB validates the JWT signature against the IdP's JWKS endpoint
3. ALB checks mandatory claims: `iss` (issuer) and `exp` (expiration)
4. ALB also validates `nbf` (not before) and `iat` (issued at) if present
5. If valid -> forward to target; if invalid -> return 401
6. No cookies, no redirects, no browser interaction required

This is purpose-built for M2M and S2S communications.

## Cost Comparison

| Approach | Monthly Cost at 100 req/s | Monthly Cost at 1000 req/s |
|---|---|---|
| ALB only (current) | ~$250 | ~$250 |
| ALB + API Gateway HTTP API | ~$510 (+$260) | ~$2,650 (+$2,400) |
| ALB with JWT validation | ~$250 (+$0) | ~$250 (+$0) |

**Savings: $260-$2,400/month** -- JWT validation is included in the ALB at no additional cost.

## Terraform Configuration

```hcl
resource "aws_lb_listener" "https" {
  load_balancer_arn = module.alb.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  default_action {
    type = "validate_token"

    validate_token {
      token_type = "JWT"
      issuer     = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.main.id}"

      jwks_endpoint = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.main.id}/.well-known/jwks.json"

      on_token_valid {
        type             = "forward"
        target_group_arn = module.alb.target_groups["gateway"].arn
      }

      on_token_invalid {
        type = "deny"
      }
    }
  }
}
```

## What We Still Need Cognito For

ALB validates JWTs but does not issue them. Cognito is still needed as the identity provider:
- Issue tokens via `client_credentials` (M2M) and `authorization_code` (user SSO)
- Federate with Entra ID (SAML), Okta (OIDC)
- Pre-Token Lambda to inject custom claims (org_unit, cost_center, team)
- JWKS endpoint for ALB to verify signatures

## Alternatives Considered

| Criteria | ALB JWT Validation | API Gateway HTTP API | Portkey JWT Plugin |
|---|---|---|---|
| Cost | $0 additional | $260-2400/mo | $0 additional |
| Latency | ~0ms (ALB-native) | ~10-15ms | ~1-2ms (in-process) |
| Managed | Yes (AWS) | Yes (AWS) | No (OSS plugin) |
| Claims forwarding | Yes (in headers) | Yes (in context) | Yes (in hook context) |
| Per-route auth | Yes (listener rules) | Yes (route-level) | No (global only) |
| WAF integration | Yes (same ALB) | Needs CloudFront | N/A |

## Consequences

**Positive**: Zero additional cost, zero additional latency, zero additional infrastructure. JWT validation at the ALB is the simplest possible architecture. WAF stays on the same ALB.

**Negative**: Newer feature (Nov 2025) -- less community experience than API Gateway. Cannot do per-client rate limiting at the auth layer (WAF rate rules by IP/header are still available). No usage plans or throttling (those would need application-layer implementation).

## Sources

- [AWS: ALB JWT Verification](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/listener-verify-jwt.html)
- [AWS Announcement (Nov 2025)](https://aws.amazon.com/about-aws/whats-new/2025/11/application-load-balancer-jwt-verification/)
