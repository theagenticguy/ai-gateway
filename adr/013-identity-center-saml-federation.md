# ADR-013: Identity Center SAML/OIDC Federation for User SSO

**Status**: Proposed
**Date**: 2026-03-21
**Deciders**: AI Engineering NAMER

## Context

The AI Gateway currently supports only machine-to-machine (M2M) authentication via Cognito `client_credentials` grant (ADR-005, ADR-008). As the gateway expands beyond automated pipelines to interactive use cases (developer portals, prompt playgrounds, admin dashboards), we need user-level authentication that federates with the organization's existing identity provider.

AWS Identity Center (formerly AWS SSO) is the organization's central identity provider, using SAML 2.0. Some teams also use Okta or Entra ID via OIDC. The gateway needs to support both protocols while mapping IdP group memberships to gateway-specific claims for authorization and cost attribution.

## Decision

Add SAML 2.0 and OIDC federation to the existing Cognito User Pool, with a Pre-Token-Generation V2 Lambda that maps IdP groups to custom gateway claims.

### Architecture

1. **Identity Providers**: Configure `aws_cognito_identity_provider` resources (SAML and/or OIDC) from a `var.identity_providers` map. Supports any number of IdPs.

2. **User App Client**: A new public Cognito app client (`user_sso`) for the `authorization_code` flow with PKCE. No client secret required. Supports Cognito Hosted UI for the login flow.

3. **Pre-Token-Generation V2 Lambda**: Triggered by Cognito before token issuance. Reads the user's IdP group memberships and maps them to custom claims:
   - `custom:team` -- team identifier for routing and attribution
   - `custom:org_unit` -- organizational unit
   - `custom:cost_center` -- cost center for billing attribution
   - `custom:tenant_tier` -- authorization tier (admin, standard, etc.)

4. **Group Mapping**: A configurable JSON map (`var.group_mapping`) defines how IdP groups translate to gateway claims. First matching group wins.

### Coexistence with M2M Auth

The existing M2M authentication (ADR-005, ADR-008) is unchanged. The same Cognito User Pool serves both flows:
- M2M clients use `client_credentials` grant with client secrets
- Users use `authorization_code` grant via Hosted UI with PKCE
- ALB JWT validation accepts tokens from both flows

### Configuration Example

```hcl
enable_user_auth = true

identity_providers = {
  IdentityCenter = {
    provider_type     = "SAML"
    metadata_url      = "https://portal.sso.us-east-1.amazonaws.com/saml/metadata/..."
    provider_details  = {}
    attribute_mapping = {}
  }
}

group_mapping = {
  "aws-ai-gateway-admins" = {
    team        = "platform"
    org_unit    = "ai-engineering"
    cost_center = "CC-1234"
    tenant_tier = "admin"
  }
  "aws-ml-engineers" = {
    team        = "ml-eng"
    org_unit    = "ai-engineering"
    cost_center = "CC-5678"
    tenant_tier = "standard"
  }
}
```

## Consequences

**Positive**:
- Users authenticate with their existing corporate credentials (Identity Center, Okta, Entra ID) via standard SAML 2.0 or OIDC.
- IdP group memberships are automatically translated to gateway-specific claims, enabling per-team authorization and cost attribution without manual user provisioning.
- All new resources are count-gated on `enable_user_auth`, so existing M2M-only deployments are unaffected.
- The Pre-Token Lambda adds minimal latency (~5ms cold start, <1ms warm) and runs only during user login, not on every API call.

**Negative**:
- The Pre-Token Lambda introduces a runtime dependency during token issuance. If the Lambda fails, user logins fail (M2M tokens are unaffected).
- Group mapping must be maintained as Terraform configuration. Changes require a `terraform apply` to update the Lambda environment variable.
- Cognito Hosted UI customization is limited compared to a custom login page.

## Alternatives Considered

| Approach | Verdict |
|---|---|
| Custom authorizer Lambda on every request | Rejected: adds latency to every API call vs. once at login |
| Direct SAML integration at ALB (ALB OIDC action) | Rejected: ALB OIDC action uses cookies/redirects, not suitable for API tokens |
| Separate Cognito User Pool for users | Rejected: complicates ALB JWT validation (multiple JWKS endpoints) |
| Skip group mapping, use raw IdP groups | Rejected: IdP group names are opaque, gateway needs structured claims |

## Sources

- ADR-005: ALB JWT Validation Over API Gateway
- ADR-008: Multi-Tenant Client Isolation
- [Cognito Pre-Token-Generation V2](https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-lambda-pre-token-generation.html)
- [Cognito SAML Federation](https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-user-pools-saml-idp.html)
- [AWS Identity Center SAML](https://docs.aws.amazon.com/singlesignon/latest/userguide/saml-concept.html)
