# ──────────────────────────────────────────────────────────────
# Hardened Portkey AI Gateway container
# ──────────────────────────────────────────────────────────────
# Builds from Portkey OSS source with security hardening:
# - Node.js 24 LTS (active support through Oct 2026)
# - Non-root user (node, UID 1000)
# - tini for proper PID 1 signal handling
# - No npm in runtime (direct node entrypoint)
# - HEALTHCHECK for orchestrator liveness probes
# ──────────────────────────────────────────────────────────────
# PORTKEY_REF is the git ref to build from: a release tag (e.g. v1.15.2) or a
# full commit SHA. GitHub serves archive/<ref>.tar.gz for both forms.
# PORTKEY_VERSION is a human-readable label only (image tags, logs).
ARG PORTKEY_REF=v1.15.2
ARG PORTKEY_VERSION=1.15.2
ARG PORTKEY_TARBALL_SHA256
ARG NODE_VERSION=24
# node:24-alpine digest. Bumped to the Alpine 3.23 rebuild that ships
# openssl 3.5.6-r0 and musl 1.2.5-r23, which fix the HIGH/CRITICAL OS CVEs
# flagged by the nightly rescan (CVE-2026-28387/28388/28389/28390/31789/31790
# in openssl, CVE-2026-40200 in musl). The previous digest
# (01743339…) bundled openssl 3.5.5-r0 / musl 1.2.5-r21; although the runtime
# stage runs `apk upgrade`, the rescan SBOM was captured before the fixed
# packages landed, so the scan gated. Pinning the patched base makes the build
# deterministically clean.
ARG NODE_ALPINE_DIGEST=sha256:2bdb65ed1dab192432bc31c95f94155ca5ad7fc1392fb7eb7526ab682fa5bf14

# ── Stage 1: Fetch + verify source ──────────────────────────
FROM node:${NODE_VERSION}-alpine@${NODE_ALPINE_DIGEST} AS source
ARG PORTKEY_REF
ARG PORTKEY_TARBALL_SHA256
RUN set -eu \
    && wget -qO /tmp/portkey.tar.gz \
       "https://github.com/Portkey-AI/gateway/archive/${PORTKEY_REF}.tar.gz" \
    && if [ -n "${PORTKEY_TARBALL_SHA256:-}" ]; then \
         echo "${PORTKEY_TARBALL_SHA256}  /tmp/portkey.tar.gz" | sha256sum -c -; \
       fi \
    && mkdir -p /src \
    && tar -xzf /tmp/portkey.tar.gz --strip-components=1 -C /src \
    && rm -f /tmp/portkey.tar.gz

# ── Stage 2: Build ───────────────────────────────────────────
FROM node:${NODE_VERSION}-alpine@${NODE_ALPINE_DIGEST} AS build
WORKDIR /app
# Copy the FULL upstream source first, THEN patch. Upstream ships its own
# package.json + package-lock.json, so the patch and the regenerated lock must
# be applied on top of the copied tree. The previous order patched first and
# then ran `COPY /src .`, which silently clobbered both manifests with the
# unpatched upstream ones before the final `npm ci` — so the CVE patches below
# never actually shipped. Copy-then-patch fixes that.
COPY --from=source /src .
# Patch vulnerable deps at build time
# Direct deps  → update version in dependencies (overrides can't touch direct deps)
#   hono               4.12.23 ← CVE-2025-62610, CVE-2026-22817/22818/29045 + later 4.12.x GHSAs
#   @hono/node-server  1.19.14 ← CVE-2026-29087 (HIGH) + GHSA-92pp-h63x-v22m (latest stable 1.x)
#   ws                 8.20.1  ← CVE-2026-45736 (HIGH) uninitialized memory disclosure on close()
# Transitive deps → npm overrides
#   picomatch          2.3.2   ← CVE-2026-33671 (HIGH) + CVE-2026-33672 (MEDIUM)
#   yaml               2.8.3   ← CVE-2026-33532 (MEDIUM)
#   minimatch          9.0.9   ← CVE-2026-27904, CVE-2026-27903 (HIGH)
#   brace-expansion    2.0.3   ← GHSA-f886-m6hf-6m8v (MEDIUM)
#   tmp                0.2.6   ← CVE-2026-44705 (HIGH) path traversal
RUN node -e " \
  const fs = require('fs'); \
  const pkg = JSON.parse(fs.readFileSync('package.json','utf8')); \
  pkg.dependencies.hono = '4.12.23'; \
  pkg.dependencies['@hono/node-server'] = '1.19.14'; \
  pkg.dependencies.ws = '8.20.1'; \
  pkg.overrides = { ...pkg.overrides, \
    picomatch: '2.3.2', \
    yaml: '2.8.3', \
    minimatch: '9.0.9', \
    'brace-expansion': '2.0.3', \
    tmp: '0.2.6' \
  }; \
  fs.writeFileSync('package.json', JSON.stringify(pkg, null, 2));" \
    && npm install --package-lock-only --ignore-scripts \
    && npm ci
RUN npm run build \
    && rm -rf node_modules \
    && npm ci --omit=dev

# ── Stage 3: Runtime ─────────────────────────────────────────
FROM node:${NODE_VERSION}-alpine@${NODE_ALPINE_DIGEST}

LABEL org.opencontainers.image.title="AI Gateway" \
      org.opencontainers.image.description="Hardened Portkey AI Gateway" \
      org.opencontainers.image.source="https://github.com/theagenticguy/ai-gateway" \
      org.opencontainers.image.base.name="node:${NODE_VERSION}-alpine"

RUN apk upgrade --no-cache \
    && apk add --no-cache tini wget \
    && rm -rf /usr/local/lib/node_modules/npm /usr/local/bin/npm /usr/local/bin/npx

WORKDIR /app
COPY --from=build /app/build ./build/
COPY --from=build /app/node_modules ./node_modules/
COPY --from=build /app/package.json ./

RUN chown -R node:node /app
USER node

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:8787/ || exit 1

ENTRYPOINT ["/sbin/tini", "--"]
CMD ["node", "build/start-server.js"]
