---
title: Agent Setup
description: Step-by-step configuration for each supported AI coding agent.
sidebar:
  order: 2
---
Configure any of the 6 supported AI coding agents to route requests through the AI Gateway.

---

## Overview

The gateway serves two API formats natively:

| Format | Endpoint | Used By |
|---|---|---|
| **Anthropic Messages** | `/v1/messages` | Claude Code |
| **OpenAI Chat Completions** | `/v1/chat/completions` | OpenCode, Goose, Continue.dev, LangChain, Codex CLI |

Every request must include the `x-portkey-provider` header to tell the gateway which upstream provider to route to:

| Header Value | Provider |
|---|---|
| `anthropic` | Anthropic (Claude) |
| `openai` | OpenAI (GPT) |
| `google` | Google (Gemini) |
| `azure-openai` | Azure OpenAI |

If the header is omitted, the gateway returns `{"error": "provider is not set"}`.

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

# Provider routing header (newline-separated key: value format)
export ANTHROPIC_CUSTOM_HEADERS="x-portkey-provider: anthropic"

# Token TTL -- must be a real env var, NOT in settings.json env block.
# Claude Code bug #7660: TTL in the settings.json env block is ignored.
export CLAUDE_CODE_API_KEY_HELPER_TTL_MS=3000000
```

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
export ANTHROPIC_CUSTOM_HEADERS="x-portkey-provider: anthropic"
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

#### Wrapper script for custom headers

Goose does not support custom headers via environment variables. Create a wrapper script at `~/bin/goose-gateway.sh`:

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

```bash
chmod +x ~/bin/goose-gateway.sh
```

:::note
If your version of Goose does not support `OPENAI_EXTRA_HEADERS`, contact the gateway admin to configure a default provider route on the gateway side.
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

Replace `${TOKEN}` with the output of `scripts/get-gateway-token.sh`, or use the [shell wrapper pattern](#token-caching) to keep it fresh.

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
    default_headers={"x-portkey-provider": "openai"},
)

response = llm.invoke("Hello from LangChain via the AI Gateway")
print(response.content)
```

To route Anthropic models through the gateway (still using the OpenAI wire format):

```python
llm = ChatOpenAI(
    base_url="${GATEWAY_URL}/v1",
    api_key=token,
    model="claude-sonnet-4-20250514",
    default_headers={"x-portkey-provider": "anthropic"},
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

[model_providers.gateway.headers]
x-portkey-provider = "openai"
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