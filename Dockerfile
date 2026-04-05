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

# ── Stage 1: Fetch + verify source ──────────────────────────
FROM node:${NODE_VERSION}-alpine AS source
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
FROM node:${NODE_VERSION}-alpine AS build
WORKDIR /app
COPY --from=source /src/package*.json ./
COPY --from=source /src/patches ./patches/
RUN npm ci
COPY --from=source /src .
RUN npm run build \
    && rm -rf node_modules \
    && npm ci --omit=dev

# ── Stage 3: Runtime ─────────────────────────────────────────
FROM node:${NODE_VERSION}-alpine

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
