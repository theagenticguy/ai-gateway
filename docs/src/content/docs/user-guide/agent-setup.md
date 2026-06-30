---
title: Agent Setup
description: Step-by-step configuration for each supported AI coding agent.
sidebar:
  order: 2
---
Configure any of the supported AI coding agents to route requests through the AI Gateway.

---

## Overview

The gateway is the [agentgateway](https://github.com/agentgateway/agentgateway) proxy, which serves two API formats natively on a single port:

| Format | Endpoint | Used By |
|---|---|---|
| **Anthropic Messages** | `/v1/messages` | Claude Code |
| **OpenAI Chat Completions** | `/v1/chat/completions` | OpenCode, Goose, Continue.dev, LangChain, Codex CLI |

Provider and model selection is **server-side**. agentgateway reads its routing from a YAML config rendered by Terraform: a priority-group failover chain (Bedrock primary, Anthropic-direct fallback) plus `modelAliases` that map requested model IDs onto backend models. Clients do **not** send a provider routing header -- there is no `x-portkey-provider` and no per-request routing override. You point your agent at the gateway URL with a valid JWT, and the gateway decides where the request goes.

:::note[No provider header]
Earlier (Portkey-based) releases required an `x-portkey-provider` header on every request. agentgateway removed it: the active provider chain lives in the rendered gateway config, and the gateway selects the backend by model alias and failover priority. Remove any `x-portkey-*` headers from your agent configuration.
:::

---

## Prerequisites

Before configuring any agent, ensure you have:

1. The gateway URL (`GATEWAY_URL`) -- ALB DNS name from `terraform output alb_dns_name`
2. Cognito credentials -- `GATEWAY_CLIENT_ID`, `GATEWAY_CLIENT_SECRET`, `GATEWAY_TOKEN_ENDPOINT`
3. The token script -- `scripts/get-gateway-token.sh` must be executable (`chmod +x`)

See [Authentication](../getting-started/authentication.md) for details on obtaining tokens.

---

## Agent Configurations

### 1. Claude Code

Claude Code talks the **native Anthropic Messages API** (`/v1/messages`). It uses `apiKeyHelper` to auto-fetch and re-fetch tokens.

#### Step 1 -- Set the API key helper

```bash
claude config set --global apiKeyHelper ~/workplace/ai-gateway/scripts/get-gateway-token.sh
```

#### Step 2 -- Set environment variables

Add to your shell profile (`~/.zshrc` or `~/.bashrc`):

```bash
# Gateway base URL -- no /v1 suffix (Claude Code appends it)
export ANTHROPIC_BASE_URL="${GATEWAY_URL}"

# Token TTL -- must be a real env var, NOT in settings.json env block.
# Claude Code bug #7660: TTL in the settings.json env block is ignored.
export CLAUDE_CODE_API_KEY_HELPER_TTL_MS=3000000
```

:::tip[No custom headers needed]
agentgateway selects the provider server-side, so you do not need `ANTHROPIC_CUSTOM_HEADERS`. If you set it for a previous release, remove it.
:::

:::caution[Known issue #26999]
`ANTHROPIC_BASE_URL` must be exported in your shell profile. Setting it in the Claude Code settings JSON alone is unreliable -- some internal codepaths read only from the process environment.
:::


#### Step 3 (optional) -- Re-enable MCP tool search

When Claude Code connects to a non-first-party host, it disables MCP tool search by default. To re-enable:

```bash
export ENABLE_TOOL_SEARCH=true
```

#### Full shell profile block

```bash
# --- AI Gateway (Claude Code) ---
export ANTHROPIC_BASE_URL="${GATEWAY_URL}"
export CLAUDE_CODE_API_KEY_HELPER_TTL_MS=3000000
export ENABLE_TOOL_SEARCH=true
export GATEWAY_CLIENT_ID="<your-client-id>"
export GATEWAY_CLIENT_SECRET="<your-client-secret>"
export GATEWAY_TOKEN_ENDPOINT="<cognito-token-endpoint>"
```

---

### 2. OpenCode

[OpenCode](https://github.com/opencode-ai/opencode) uses `@ai-sdk/openai-compatible` for custom providers.

#### Configuration file

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
        "baseURL": "${GATEWAY_URL}/v1"
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

#### API key

Set the API key in your environment:

```bash
export OPENAI_API_KEY=$(~/workplace/ai-gateway/scripts/get-gateway-token.sh)
```

:::note
OpenCode reads the API key from `OPENAI_API_KEY` even when using a custom provider. Use the [shell wrapper pattern](#token-caching) to keep the token fresh across sessions.
:::


---

### 3. Goose

[Goose](https://github.com/block/goose) reads provider configuration from environment variables.

#### Environment variables

```bash
export GOOSE_PROVIDER=openai
# Host only -- Goose appends /v1 internally. Do NOT add /v1 here.
export OPENAI_HOST="${GATEWAY_URL}"
export OPENAI_API_KEY=$(~/workplace/ai-gateway/scripts/get-gateway-token.sh)
```

#### Wrapper script for fresh tokens

Goose reads `OPENAI_API_KEY` once at startup. Create a wrapper at `~/bin/goose-gateway.sh` to refresh the token on every launch:

```bash
#!/usr/bin/env bash
# Refresh token and launch Goose
export GOOSE_PROVIDER=openai
export OPENAI_HOST="${GATEWAY_URL}"
export OPENAI_API_KEY=$(~/workplace/ai-gateway/scripts/get-gateway-token.sh)

exec goose "$@"
```

```bash
chmod +x ~/bin/goose-gateway.sh
```

:::note
No provider header is required. agentgateway selects the upstream provider from its rendered config, so Goose needs only the gateway URL and a valid token.
:::


---

### 4. Continue.dev

[Continue](https://continue.dev) supports `config.yaml` (the `config.json` format is deprecated).

#### Configuration file

Edit `~/.continue/config.yaml`:

```yaml
models:
  - name: GPT-4.1 (Gateway)
    provider: openai
    model: gpt-4.1
    apiBase: "${GATEWAY_URL}/v1"
    apiKey: "${TOKEN}"

  - name: Claude Sonnet (Gateway)
    provider: openai
    model: claude-sonnet-4-20250514
    apiBase: "${GATEWAY_URL}/v1"
    apiKey: "${TOKEN}"
```

Replace `${TOKEN}` with the output of `scripts/get-gateway-token.sh`, or use the [shell wrapper pattern](#token-caching) to keep it fresh. No `requestOptions.headers` are needed -- the gateway maps the requested model onto a backend via `modelAliases` and routes through its priority-group failover chain.

:::note
Continue reads `config.yaml` at startup. If your token expires mid-session, restart Continue or use the command palette to reload the configuration.
:::


---

### 5. LangChain

Use `ChatOpenAI` from `langchain-openai` to route through the gateway:

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
)

response = llm.invoke("Hello from LangChain via the AI Gateway")
print(response.content)
```

To target an Anthropic model, just change the `model` argument -- the gateway resolves it against `modelAliases` and its provider failover chain. No custom headers are needed:

```python
llm = ChatOpenAI(
    base_url="${GATEWAY_URL}/v1",
    api_key=token,
    model="claude-sonnet-4-20250514",
)
```

---

### 6. Codex CLI

[Codex CLI](https://github.com/openai/codex) cannot override its built-in `openai` provider. Define a new provider name in the config.

#### Configuration file

Edit `~/.codex/config.toml`:

```toml
[model_providers.gateway]
name = "AI Gateway"
base_url = "${GATEWAY_URL}/v1"
env_key = "GATEWAY_API_KEY"
```

#### API key and launch

```bash
export GATEWAY_API_KEY=$(~/workplace/ai-gateway/scripts/get-gateway-token.sh)

codex --provider gateway --model gpt-4.1
```

:::caution[Do not use the built-in `openai` provider name]
Codex CLI does not allow overriding the built-in `openai` provider. If requests go to `api.openai.com` instead of the gateway, ensure you are using a custom provider name like `gateway`.
:::


---

## Token Caching

Cognito JWTs have a default TTL of **3600 seconds** (1 hour). To avoid unnecessary token requests, cache the token and refresh before expiry.

### Claude Code

Claude Code handles caching automatically:

1. `apiKeyHelper` is called once at startup to fetch the token.
2. `CLAUDE_CODE_API_KEY_HELPER_TTL_MS=3000000` (50 minutes) tells Claude Code to proactively re-invoke the helper before the token expires.
3. On a `401` response, Claude Code immediately re-invokes the helper regardless of TTL.

No additional caching is needed.

### Other Agents -- Shell Wrapper Pattern

For agents that read API keys from environment variables (Goose, OpenCode, Codex CLI), use a caching wrapper to avoid calling Cognito on every command:

```bash
#!/usr/bin/env bash
# ~/.local/bin/gateway-token-cached.sh
#
# Caches the gateway token in a file, refreshing when older than 50 minutes.

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