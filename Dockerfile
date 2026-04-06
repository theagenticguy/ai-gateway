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
ARG PORTKEY_VERSION=1.15.2
ARG PORTKEY_TARBALL_SHA256
ARG NODE_VERSION=24
ARG NODE_ALPINE_DIGEST=sha256:01743339035a5c3c11a373cd7c83aeab6ed1457b55da6a69e014a95ac4e4700b

# ── Stage 1: Fetch + verify source ──────────────────────────
FROM node:${NODE_VERSION}-alpine@${NODE_ALPINE_DIGEST} AS source
ARG PORTKEY_VERSION
ARG PORTKEY_TARBALL_SHA256
RUN set -eu \
    && wget -qO /tmp/portkey.tar.gz \
       "https://github.com/Portkey-AI/gateway/archive/refs/tags/v${PORTKEY_VERSION}.tar.gz" \
    && if [ -n "${PORTKEY_TARBALL_SHA256:-}" ]; then \
         echo "${PORTKEY_TARBALL_SHA256}  /tmp/portkey.tar.gz" | sha256sum -c -; \
       fi \
    && mkdir -p /src \
    && tar -xzf /tmp/portkey.tar.gz --strip-components=1 -C /src \
    && rm -f /tmp/portkey.tar.gz

# ── Stage 2: Build ───────────────────────────────────────────
FROM node:${NODE_VERSION}-alpine@${NODE_ALPINE_DIGEST} AS build
WORKDIR /app
COPY --from=source /src/package*.json ./
COPY --from=source /src/patches ./patches/
# Patch vulnerable deps at build time
# Direct deps  → update version in dependencies (overrides can't touch direct deps)
#   hono               4.12.10 ← CVE-2025-62610, CVE-2026-22817, CVE-2026-22818, CVE-2026-29045
#   @hono/node-server  1.19.12 ← CVE-2026-29087 (HIGH)
# Transitive deps → npm overrides
#   picomatch          2.3.2   ← CVE-2026-33671 (HIGH) + CVE-2026-33672 (MEDIUM)
#   yaml               2.8.3   ← CVE-2026-33532 (MEDIUM)
#   minimatch          9.0.9   ← CVE-2026-27904, CVE-2026-27903 (HIGH)
RUN node -e " \
  const fs = require('fs'); \
  const pkg = JSON.parse(fs.readFileSync('package.json','utf8')); \
  pkg.dependencies.hono = '4.12.10'; \
  pkg.dependencies['@hono/node-server'] = '1.19.12'; \
  pkg.overrides = { ...pkg.overrides, \
    picomatch: '2.3.2', \
    yaml: '2.8.3', \
    minimatch: '9.0.9' \
  }; \
  fs.writeFileSync('package.json', JSON.stringify(pkg, null, 2));" \
    && npm install --package-lock-only --ignore-scripts \
    && npm ci
COPY --from=source /src .
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
    && apk add --no-cache tini wget

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
