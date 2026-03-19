# ADR-002: python:3.13-slim Over Chainguard for Container Base Image

**Status**: Accepted
**Date**: 2026-03-18
**Deciders**: AI Engineering NAMER

## Context

We need a secure, minimal Docker base image for our Python 3.13 tooling containers. The gateway container (Portkey) uses its own Node.js-based image, but our supporting scripts, health checkers, and future application code need a Python base.

## Decision

Use **`python:3.13-slim`** with multi-stage hardening (non-root user, tini, minimal packages) as the base image. Maintain a clear upgrade path to Chainguard paid images.

## Alternatives Considered

| Criteria (weight) | Chainguard `python:latest` | Google distroless | `python:3.13-slim` + hardening |
|---|---|---|---|
| Security posture (0.25) | 10/10 (zero CVEs) | 8/10 (low CVEs) | 6/10 (some CVEs, mitigated) |
| Python 3.13 support (0.20) | 7/10 (paid for 3.13 tag) | 3/10 (no 3.13) | 10/10 (native) |
| uv compatibility (0.15) | 8/10 | 5/10 | 10/10 |
| Image size (0.10) | 10/10 (~23 MB) | 8/10 (~50 MB) | 7/10 (~150 MB) |
| Free / no vendor lock (0.10) | 5/10 (free=latest only) | 10/10 | 10/10 |
| **Weighted Score** | **7.05** | **5.25** | **8.30** |

## Rationale

Chainguard offers the best security posture (zero CVEs), but the free tier pins to `latest` (currently Python 3.14). The Python 3.13 tag requires a paid subscription. Google distroless does not support Python 3.13. For a small team, `python:3.13-slim` with proper hardening provides the best balance of security, compatibility, and developer experience. The security gap is closed by multi-stage builds, non-root execution, and trivy scanning in CI.

## Consequences

**Positive**: Native Python 3.13, zero friction with uv, debuggable (has shell), free, team familiarity.

**Negative**: Higher CVE count than Chainguard (~150 MB vs ~23 MB image size). Requires disciplined CI scanning. Upgrade path to Chainguard paid is straightforward (change `FROM` line).
