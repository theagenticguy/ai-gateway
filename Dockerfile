# ──────────────────────────────────────────────────────────────
# AI Gateway data-plane image (ADR-017: agentgateway, replacing Portkey OSS)
# ──────────────────────────────────────────────────────────────
# agentgateway publishes an official hardened image (distroless
# cgr.dev/chainguard/glibc-dynamic base, ENTRYPOINT /app/agentgateway). Rather
# than rebuild the Rust binary from source, we pin the upstream image BY DIGEST
# and re-tag it into our ECR. The digest is the immutable supply-chain contract;
# the tag (AGENTGATEWAY_REF) is informational.
#
# This is a deliberate posture change from the Portkey build: the old Dockerfile
# compiled Node + patched npm CVEs at build time. agentgateway is a single
# static-ish Rust binary on a distroless base, so the npm copy-then-patch
# apparatus is gone. CVE scanning moves to image-level scanning of the pinned
# digest (Trivy/Grype/Inspector on the ECR image), which the CI already runs.
#
# These ARG defaults are a FALLBACK for bare `docker build .`; the canonical pin
# lives in versions.env and CI passes it via --build-arg.
ARG AGENTGATEWAY_REF=v1.3.1
ARG AGENTGATEWAY_VERSION=1.3.1
# Pin by digest. Keep in sync with versions.env. CI overrides this with the
# resolved digest from versions.env; the default is digest-pinned too so a bare
# `docker build .` is reproducible (the tag is informational, the digest binds).
ARG AGENTGATEWAY_IMAGE=ghcr.io/agentgateway/agentgateway@sha256:c3ce7b75da90fef70239befcc1c3adc05152d7b9dd21fcb8351178026a2c4381

# Single stage: re-tag the pinned upstream image. We intentionally do NOT add
# layers (no shell, no package manager) so the distroless attack surface is
# preserved. agentgateway reads its config from the `-c <inline>` arg passed by
# the ECS task definition (see infrastructure/modules/compute), and serves on
# port 8787 with readiness on 15021.
FROM ${AGENTGATEWAY_IMAGE}

ARG AGENTGATEWAY_VERSION
LABEL org.opencontainers.image.title="AI Gateway" \
      org.opencontainers.image.description="AI Gateway data plane (agentgateway, ADR-017)" \
      org.opencontainers.image.source="https://github.com/theagenticguy/ai-gateway" \
      org.opencontainers.image.version="${AGENTGATEWAY_VERSION}" \
      org.opencontainers.image.base.name="ghcr.io/agentgateway/agentgateway"

EXPOSE 8787

# Entrypoint is inherited from the upstream image (/app/agentgateway). The ECS
# task definition supplies `command: ["-c", "<rendered config>"]`. The distroless
# base has no shell, so the HEALTHCHECK is defined at the orchestrator level
# (ECS container healthCheck hits the readiness port) rather than here.
