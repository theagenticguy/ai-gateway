# AI Gateway — Agent Setup Guide

This guide covers how to configure six AI coding agents to route requests through
the AI Gateway (Portkey OSS on ECS Fargate behind an ALB).

---

## Prerequisites

| Item | Value |
|------|-------|
| Gateway URL | `${GATEWAY_URL}` (ALB DNS — get via `terraform output alb_dns_name`) |
| Cognito token endpoint | `${TOKEN_ENDPOINT}` (not yet provisioned) |
| Client ID | Issued per team / service account |
| Client secret | Issued per team / service account |

**Auth is not yet active.** The Cognito user pool and ALB OIDC listener rule
have not been provisioned. Until then, the gateway accepts unauthenticated
requests. You can skip token generation steps and use any placeholder value for
API key fields. When auth is turned on, existing configs will work as-is once
you supply real credentials.

Required environment variables (set these in your shell profile once auth is
active):

```bash
export GATEWAY_CLIENT_ID="<your-client-id>"
export GATEWAY_CLIENT_SECRET="<your-client-secret>"
export GATEWAY_TOKEN_ENDPOINT="<cognito-token-endpoint>"
```

---

## How the Gateway Works

The gateway is [Portkey AI Gateway](https://github.com/Portkey-ai/gateway)
(open-source) deployed on ECS Fargate behind an Application Load Balancer.

**Dual-format support.** Portkey natively speaks both:

- **Anthropic Messages API** (`/v1/messages`) — used by Claude Code
- **OpenAI Chat Completions API** (`/v1/chat/completions`) — used by most other agents

**Provider routing.** The `x-portkey-provider` header tells the gateway which
upstream LLM provider to forward to. The gateway holds API keys for each
provider as ECS secrets — callers never need provider API keys.

| Header value | Upstream provider |
|---|---|
| `anthropic` | Anthropic (Claude) |
| `openai` | OpenAI (GPT) |
| `google` | Google (Gemini) |
| `azure-openai` | Azure OpenAI |

If you omit the header, the gateway returns a provider-selection error.

---

## Token Generation

The script `scripts/get-gateway-token.sh` handles the Cognito
`client_credentials` OAuth2 flow and prints a raw JWT to stdout.

### Required Environment Variables

| Variable | Description |
|---|---|
| `GATEWAY_CLIENT_ID` | Cognito app-client ID |
| `GATEWAY_CLIENT_SECRET` | Cognito app-client secret |
| `GATEWAY_TOKEN_ENDPOINT` | Cognito `/oauth2/token` URL |

### Usage

```bash
# One-shot
TOKEN=$(./scripts/get-gateway-token.sh)

# Verify
echo "$TOKEN" | cut -d. -f2 | base64 -d 2>/dev/null | python3 -m json.tool
```

The script exits non-zero on any failure and writes diagnostics to stderr.

---

## Agent Configurations

### 1. Claude Code (Anthropic Messages API)

Claude Code talks the native Anthropic Messages API. It uses `apiKeyHelper` to
auto-fetch (and re-fetch on 401) a fresh token.

#### Step 1 — Set the API key helper

```bash
claude config set --global apiKeyHelper ~/workplace/ai-gateway/scripts/get-gateway-token.sh
```

#### Step 2 — Set environment variables

Add these to your shell profile (`~/.zshrc`, `~/.bashrc`, etc.):

```bash
# Gateway base URL — no /v1 suffix (Claude Code appends it)
export ANTHROPIC_BASE_URL="${GATEWAY_URL}"

# Provider routing header (newline-separated key: value format)
export ANTHROPIC_CUSTOM_HEADERS="x-portkey-provider: anthropic"

# Token TTL — set as a real env var, NOT in settings.json env block.
# Claude Code bug #7660: TTL in the settings.json env block is ignored.
export CLAUDE_CODE_API_KEY_HELPER_TTL_MS=3000000
```

> **Known issue #26999:** `ANTHROPIC_BASE_URL` must be exported in your shell
> profile. Setting it in the Claude Code settings JSON alone is unreliable —
> some internal codepaths read only from the process environment.

#### Step 3 (optional) — Re-enable MCP tool search

When Claude Code connects to a non-first-party host, it disables MCP tool
search by default. If you need tool search:

```bash
export ENABLE_TOOL_SEARCH=true
```

#### Full shell profile block

```bash
# --- AI Gateway (Claude Code) ---
export ANTHROPIC_BASE_URL="${GATEWAY_URL}"
export ANTHROPIC_CUSTOM_HEADERS="x-portkey-provider: anthropic"
export CLAUDE_CODE_API_KEY_HELPER_TTL_MS=3000000
export ENABLE_TOOL_SEARCH=true
export GATEWAY_CLIENT_ID="<your-client-id>"
export GATEWAY_CLIENT_SECRET="<your-client-secret>"
export GATEWAY_TOKEN_ENDPOINT="<cognito-token-endpoint>"
```

---

### 2. OpenCode (OpenAI-compatible)

[OpenCode](https://github.com/opencode-ai/opencode) uses `@ai-sdk/openai-compatible`
for custom providers.

Create or edit `opencode.json` in your project root:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "gateway": {
      "id": "gateway",
      "name": "AI Gateway",
      "type": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "${GATEWAY_URL}/v1",
        "headers": {
          "x-portkey-provider": "openai"
        }
      },
      "models": {
        "gpt-4.1": {
          "id": "gpt-4.1",
          "name": "GPT-4.1 (via Gateway)",
          "type": "chat",
          "attachment": true
        }
      }
    }
  },
  "model": {
    "chat": "gateway/gpt-4.1"
  }
}
```

Set the API key in your environment:

```bash
export OPENAI_API_KEY=$(~/workplace/ai-gateway/scripts/get-gateway-token.sh)
```

---

### 3. Goose by Block (OpenAI-compatible)

[Goose](https://github.com/block/goose) reads provider config from environment
variables.

```bash
export GOOSE_PROVIDER=openai
# Host only — Goose appends /v1 internally. Do NOT add /v1 here.
export OPENAI_HOST="${GATEWAY_URL}"
export OPENAI_API_KEY=$(~/workplace/ai-gateway/scripts/get-gateway-token.sh)
```

Goose does not support custom headers via environment variables. To inject the
`x-portkey-provider` header, create a wrapper script at
`~/bin/goose-gateway.sh`:

```bash
#!/usr/bin/env bash
# Refresh token and launch Goose with provider header
export GOOSE_PROVIDER=openai
export OPENAI_HOST="${GATEWAY_URL}"
export OPENAI_API_KEY=$(~/workplace/ai-gateway/scripts/get-gateway-token.sh)

# Goose reads OPENAI_EXTRA_HEADERS if available (check your version)
export OPENAI_EXTRA_HEADERS="x-portkey-provider: openai"

exec goose "$@"
```

If your version of Goose does not support `OPENAI_EXTRA_HEADERS`, configure a
default provider route on the gateway side (contact the gateway admin).

---

### 4. Continue.dev (OpenAI-compatible)

[Continue](https://continue.dev) supports `config.yaml` (the `config.json`
format is deprecated).

Edit `~/.continue/config.yaml`:

```yaml
models:
  - name: GPT-4.1 (Gateway)
    provider: openai
    model: gpt-4.1
    apiBase: "${GATEWAY_URL}/v1"
    apiKey: "${TOKEN}"
    requestOptions:
      headers:
        x-portkey-provider: openai

  - name: Claude Sonnet (Gateway)
    provider: openai
    model: claude-sonnet-4-20250514
    apiBase: "${GATEWAY_URL}/v1"
    apiKey: "${TOKEN}"
    requestOptions:
      headers:
        x-portkey-provider: anthropic
```

Replace `${TOKEN}` with the output of `scripts/get-gateway-token.sh`, or use
the shell-wrapper approach from the [Token Caching](#token-caching-guidance)
section to keep it fresh.

> **Note:** Continue reads `config.yaml` at startup. If your token expires
> mid-session, restart Continue or use the command palette to reload config.

---

### 5. LangChain Python (OpenAI-compatible)

Use `ChatOpenAI` from `langchain-openai`:

```python
from langchain_openai import ChatOpenAI
import subprocess

# Fetch a fresh token
token = subprocess.run(
    ["./scripts/get-gateway-token.sh"],
    capture_output=True, text=True, check=True,
).stdout

llm = ChatOpenAI(
    base_url="${GATEWAY_URL}/v1",
    api_key=token,
    model="gpt-4.1",
    default_headers={"x-portkey-provider": "openai"},
)

response = llm.invoke("Hello from LangChain via the AI Gateway")
print(response.content)
```

For Anthropic models through the gateway (still using the OpenAI wire format):

```python
llm = ChatOpenAI(
    base_url="${GATEWAY_URL}/v1",
    api_key=token,
    model="claude-sonnet-4-20250514",
    default_headers={"x-portkey-provider": "anthropic"},
)
```

---

### 6. Codex CLI by OpenAI (OpenAI-compatible)

[Codex CLI](https://github.com/openai/codex) cannot override its built-in
`openai` provider. Define a new provider name in the config.

Edit `~/.codex/config.toml`:

```toml
[model_providers.gateway]
name = "AI Gateway"
base_url = "${GATEWAY_URL}/v1"
env_key = "GATEWAY_API_KEY"

[model_providers.gateway.headers]
x-portkey-provider = "openai"
```

Set the environment variable:

```bash
export GATEWAY_API_KEY=$(~/workplace/ai-gateway/scripts/get-gateway-token.sh)
```

Launch Codex CLI with the gateway provider:

```bash
codex --provider gateway --model gpt-4.1
```

---

## Token Caching Guidance

Cognito JWTs have a default TTL of **3600 seconds** (1 hour). To avoid
unnecessary token requests, cache the token and refresh before expiry.

### Claude Code

Claude Code handles caching automatically:

1. `apiKeyHelper` is called once at startup to fetch the token.
2. `CLAUDE_CODE_API_KEY_HELPER_TTL_MS=3000000` (50 minutes) tells Claude Code
   to proactively re-invoke the helper before the token expires.
3. On a 401 response, Claude Code immediately re-invokes the helper regardless
   of TTL.

No additional caching is needed.

### Other Agents — Shell Wrapper Pattern

For agents that read API keys from environment variables (Goose, OpenCode,
Codex CLI), use a caching wrapper to avoid calling Cognito on every command:

```bash
# ~/.local/bin/gateway-token-cached.sh
#!/usr/bin/env bash
#
# Caches the gateway token in a file, refreshing when it is older than 50 min.

set -euo pipefail

CACHE_FILE="${HOME}/.cache/ai-gateway/token"
MAX_AGE=3000  # seconds (50 minutes)

mkdir -p "$(dirname "$CACHE_FILE")"

# Refresh if cache is missing or stale
if [[ ! -f "$CACHE_FILE" ]] || \
   [[ $(( $(date +%s) - $(stat -c %Y "$CACHE_FILE" 2>/dev/null || echo 0) )) -gt $MAX_AGE ]]; then
  ~/workplace/ai-gateway/scripts/get-gateway-token.sh > "$CACHE_FILE"
  chmod 600 "$CACHE_FILE"
fi

cat "$CACHE_FILE"
```

Then in your shell profile:

```bash
export OPENAI_API_KEY=$(~/.local/bin/gateway-token-cached.sh)
export GATEWAY_API_KEY=$(~/.local/bin/gateway-token-cached.sh)
```

---

## Troubleshooting

### 401 Unauthorized

- Token has expired. Re-run `scripts/get-gateway-token.sh` and update your
  environment variable.
- For Claude Code: check that `apiKeyHelper` is set and the script is
  executable (`chmod +x`).
- Verify the token is a valid JWT: `echo "$TOKEN" | cut -d. -f2 | base64 -d 2>/dev/null | python3 -m json.tool`

### 403 Forbidden

- The Cognito app client may not have the required scope. Contact the gateway
  admin to verify client configuration.
- WAF may be blocking the request. Check the `x-amzn-waf-action` response
  header.

### Connection Refused / Timeout

- Verify the gateway URL is reachable: `curl -s -o /dev/null -w "%{http_code}" ${GATEWAY_URL}/`
  (expect `200`).
- If behind VPN, confirm your network can reach the ALB.
- The ALB health check path is `/` on port 8787. If the gateway is unhealthy,
  check ECS service events in the AWS console.

### Missing `x-portkey-provider` Header

```
{"error": "provider is not set"}
```

Every request must include the `x-portkey-provider` header. Double-check that:

- Claude Code: `ANTHROPIC_CUSTOM_HEADERS` is set (newline-separated format).
- OpenCode: `options.headers` is present in `opencode.json`.
- LangChain: `default_headers` dict is passed to `ChatOpenAI`.
- Codex CLI: `[model_providers.gateway.headers]` section exists in `config.toml`.

### Invalid Grant / Token Endpoint Error

```
{"error": "invalid_grant"}
```

- Verify `GATEWAY_TOKEN_ENDPOINT` is the full URL (e.g.,
  `https://<domain>.auth.<region>.amazoncognito.com/oauth2/token`).
- Confirm `GATEWAY_CLIENT_ID` and `GATEWAY_CLIENT_SECRET` are correct.
- Check that the Cognito app client is configured for `client_credentials`
  grant type.

### MCP Tool Search Disabled (Claude Code)

When connected to a non-first-party host, Claude Code disables MCP tool
search. Symptoms: tool search returns no results, or tools from MCP servers
are not discovered.

Fix:

```bash
export ENABLE_TOOL_SEARCH=true
```

### Provider Override Not Working

Some agents (notably Codex CLI) do not allow overriding the built-in `openai`
provider name. If requests are going to `api.openai.com` instead of the
gateway:

- Codex CLI: use a custom provider name (`gateway`, not `openai`) as shown
  above.
- Goose: use `OPENAI_HOST` (not `OPENAI_BASE_URL`), and omit the `/v1` suffix.

### Config Precedence

When environment variables and config files conflict:

| Agent | Precedence (highest first) |
|---|---|
| Claude Code | Env vars > `claude config` settings > defaults |
| OpenCode | `opencode.json` in project root > global config |
| Goose | Env vars > `~/.config/goose/config.yaml` |
| Continue | `~/.continue/config.yaml` (only source) |
| LangChain | Constructor args > env vars |
| Codex CLI | CLI flags > `config.toml` > env vars |
