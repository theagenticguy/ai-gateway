# ADR-007: Upgrade AWS Terraform Provider to >= 6.22 for ALB JWT Validation

**Status**: Accepted
**Date**: 2026-03-18
**Deciders**: AI Engineering NAMER

## Context

ADR-005 decided to use ALB native JWT validation (`jwt-validation` action type) instead of API Gateway. This feature requires AWS Terraform provider >= v6.22.0 (released November 21, 2025).

Our current `versions.tf` specifies `~> 5.0`. This must be upgraded.

## Decision

Upgrade the AWS provider constraint from `~> 5.0` to `~> 6.22` in `infrastructure/versions.tf`.

## Breaking Changes in the 5.x → 6.x Jump

The AWS provider v6.0.0 was a major release. Key breaking changes that may affect our config:
- Resource and data source renames/removals
- Default tag propagation behavior changes
- Some attribute type changes

Since our Terraform was just written (no state yet), the upgrade is zero-risk — we're starting fresh on v6.

## JWT Validation Configuration

```hcl
default_action {
  type = "jwt-validation"
  jwt_validation {
    issuer        = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.gateway.id}"
    jwks_endpoint = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.gateway.id}/.well-known/jwks.json"
    additional_claim {
      format = "string-array"
      name   = "scope"
      values = ["https://gateway.internal/invoke"]
    }
  }
}
```

Key constraints:
- HTTPS listener only
- RS256 algorithm only
- JWKS endpoint must be publicly accessible (Cognito's is)
- Max 10 additional claims, max 10 values per claim

## Consequences

**Positive**: Unlocks free JWT validation at ALB. No API Gateway needed.
**Negative**: Major provider version jump. Must validate all existing resources still plan clean on v6.

## Sources

- [Terraform AWS Provider #45067](https://github.com/hashicorp/terraform-provider-aws/issues/45067) — jwt-validation feature, closed in v6.22.0
- [Terraform Registry: aws_lb_listener](https://registry.terraform.io/providers/hashicorp/aws/6.35.0/docs/resources/lb_listener) — jwt_validation block spec
