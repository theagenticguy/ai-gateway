# ADR-004: 3-Phase Container Security Pipeline

**Status**: Accepted
**Date**: 2026-03-18
**Deciders**: AI Engineering NAMER

## Context

We need a comprehensive security pipeline covering IaC validation, Dockerfile linting, container image scanning, SBOM generation, and image signing. Multiple tools exist with overlapping capabilities.

## Decision

Implement a **3-phase pipeline** with 6 tools, each in its optimal position:

```
Phase 1 (PRE-BUILD):  hadolint + checkov
Phase 2 (POST-BUILD): trivy + syft
Phase 3 (POST-SCAN):  cosign
```

Skip grype (trivy covers container scanning) and osv-scanner (`uv audit` provides native dependency scanning).

## Tool Selection Rationale

| Tool | Role | Why This Tool |
|---|---|---|
| hadolint | Dockerfile linting | Only dedicated Dockerfile linter; ShellCheck integration |
| checkov | IaC scanning | 2,500+ Terraform policies; graph-based cross-resource analysis |
| trivy | Container scanning | Broadest single scanner: vulns + misconfig + secrets + licenses |
| syft | SBOM generation | CycloneDX + SPDX output; pairs with Anchore ecosystem |
| cosign | Image signing | Keyless Sigstore OIDC; GitHub Actions native |

### Why NOT grype alongside trivy?

Grype's EPSS/KEV risk scoring is valuable for large security teams doing triage at scale. For a small team (2-5), trivy's all-in-one approach covers container images, filesystem scanning, IaC (absorbed tfsec), secrets, and licenses. Adding grype creates tool sprawl without proportional benefit at this scale. Add it later if trivy proves insufficient.

### Why NOT osv-scanner?

`uv audit` (preview, added in uv 0.10.10) provides native OSV-based dependency scanning with zero additional tooling. `trivy fs .` also scans `uv.lock`. No need for a third dependency scanner.

## CAUTION Items

- **trivy**: CVE-2026-28353 supply chain incident (Feb-Mar 2026). CLI binary unaffected. Mitigation: pin versions, verify checksums.
- **hadolint**: Single maintainer, resumed after 3-year gap. No alternative exists. Stable tool.

## Consequences

**Positive**: Full supply chain coverage (source -> build -> registry), keyless signing for provenance, SBOM for compliance, minimal tool count for small team.

**Negative**: 6 tools to maintain versions for (managed by mise). Trivy CAUTION status requires version pinning discipline.
